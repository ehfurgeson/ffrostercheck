import smtplib
from email.message import EmailMessage

import pytest

from app.config import ConfigError, EmailConfig, EnvironmentConfig
from app.notification import (
    EmailDeliveryError,
    HtmlEmail,
    SMTPEmailNotifier,
    TextEmail,
)


class FakeSMTP:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float,
        refused: dict[str, tuple[int, bytes]] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.connection = (host, port, timeout)
        self.refused = refused or {}
        self.failure = failure
        self.calls: list[str] = []
        self.login_credentials: tuple[str, str] | None = None
        self.message: EmailMessage | None = None

    def __enter__(self) -> "FakeSMTP":
        self.calls.append("enter")
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("exit")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, *, context: object) -> None:
        assert context is not None
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")
        self.login_credentials = (username, password)
        if self.failure is not None:
            raise self.failure

    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]:
        self.calls.append("send_message")
        self.message = message
        return self.refused


def _config() -> EmailConfig:
    return EmailConfig("smtp.gmail.com", 587, "recipient@example.com")


def _environment() -> EnvironmentConfig:
    return EnvironmentConfig(
        smtp_user="sender@example.com",
        smtp_app_password="super-secret-password",
    )


def test_sends_multipart_alert_over_authenticated_starttls() -> None:
    smtp = FakeSMTP("unused", 0, timeout=0)
    notifier = SMTPEmailNotifier(
        _config(),
        _environment(),
        timeout_seconds=12.5,
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            smtp, host, port, timeout
        ),
    )

    notifier.send_game_alert(
        TextEmail("Fantasy Check", "Plain alert"),
        HtmlEmail("Fantasy Check", "<strong>HTML alert</strong>"),
    )

    assert smtp.connection == ("smtp.gmail.com", 587, 12.5)
    assert smtp.calls == ["enter", "ehlo", "starttls", "ehlo", "login", "send_message", "exit"]
    assert smtp.login_credentials == ("sender@example.com", "super-secret-password")
    assert smtp.message is not None
    assert smtp.message["Subject"] == "Fantasy Check"
    assert smtp.message["From"] == "sender@example.com"
    assert smtp.message["To"] == "recipient@example.com"
    assert smtp.message.get_body(preferencelist=("plain",)).get_content() == "Plain alert\n"
    assert (
        smtp.message.get_body(preferencelist=("html",)).get_content()
        == "<strong>HTML alert</strong>\n"
    )


def test_sends_plain_text_alert_without_empty_html_part() -> None:
    smtp = FakeSMTP("unused", 0, timeout=0)
    notifier = SMTPEmailNotifier(
        _config(),
        _environment(),
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            smtp, host, port, timeout
        ),
    )

    notifier.send_game_alert(TextEmail("Fantasy Check", "Plain alert"))

    assert smtp.message is not None
    assert not smtp.message.is_multipart()
    assert smtp.message.get_content() == "Plain alert\n"


def test_requires_both_smtp_credentials_before_connecting() -> None:
    with pytest.raises(ConfigError, match="SMTP_APP_PASSWORD"):
        SMTPEmailNotifier(
            _config(),
            EnvironmentConfig(smtp_user="sender@example.com"),
        )


def test_rejects_mismatched_text_and_html_subjects_before_connecting() -> None:
    connections = 0

    def connect(*args: object, **kwargs: object) -> FakeSMTP:
        nonlocal connections
        connections += 1
        return FakeSMTP("unused", 0, timeout=0)

    notifier = SMTPEmailNotifier(_config(), _environment(), smtp_factory=connect)

    with pytest.raises(EmailDeliveryError, match="subjects must match"):
        notifier.send_game_alert(
            TextEmail("Text subject", "Plain"),
            HtmlEmail("HTML subject", "<p>HTML</p>"),
        )

    assert connections == 0


def test_wraps_smtp_failure_without_exposing_credentials_or_server_detail() -> None:
    smtp = FakeSMTP(
        "unused",
        0,
        timeout=0,
        failure=smtplib.SMTPAuthenticationError(535, b"super-secret-password rejected"),
    )
    notifier = SMTPEmailNotifier(
        _config(),
        _environment(),
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            smtp, host, port, timeout
        ),
    )

    with pytest.raises(EmailDeliveryError) as caught:
        notifier.send_game_alert(TextEmail("Fantasy Check", "Plain"))

    assert "SMTPAuthenticationError" in str(caught.value)
    assert "super-secret-password" not in str(caught.value)


def test_treats_any_refused_recipient_as_delivery_failure() -> None:
    smtp = FakeSMTP(
        "unused",
        0,
        timeout=0,
        refused={"recipient@example.com": (550, b"mailbox unavailable")},
    )
    notifier = SMTPEmailNotifier(
        _config(),
        _environment(),
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            smtp, host, port, timeout
        ),
    )

    with pytest.raises(EmailDeliveryError, match="refused 1 recipient"):
        notifier.send_game_alert(TextEmail("Fantasy Check", "Plain"))


def test_rejects_non_positive_connection_timeout() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        SMTPEmailNotifier(_config(), _environment(), timeout_seconds=0)


def _configure_connection(
    smtp: FakeSMTP,
    host: str,
    port: int,
    timeout: float,
) -> FakeSMTP:
    smtp.connection = (host, port, timeout)
    return smtp
