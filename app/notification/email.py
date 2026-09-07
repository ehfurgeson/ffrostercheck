"""Authenticated SMTP delivery for rendered Fantasy Watchdog alerts."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Callable, Protocol

from app.config import EmailConfig, EnvironmentConfig
from app.notification.templates import HtmlEmail, TextEmail


class EmailDeliveryError(RuntimeError):
    """Raised when an alert cannot be safely constructed or delivered."""


class EmailNotifier(Protocol):
    """Transport interface for a rendered kickoff-window alert."""

    def send_game_alert(
        self,
        text_email: TextEmail,
        html_email: HtmlEmail | None = None,
    ) -> None:
        """Send one rendered alert."""


SMTPFactory = Callable[..., smtplib.SMTP]


class SMTPEmailNotifier:
    """Send multipart alerts through authenticated SMTP with STARTTLS."""

    def __init__(
        self,
        config: EmailConfig,
        environment: EnvironmentConfig,
        *,
        timeout_seconds: float = 30.0,
        smtp_factory: SMTPFactory = smtplib.SMTP,
    ) -> None:
        environment.require_email()
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")

        self._host = config.smtp_host
        self._port = config.smtp_port
        self._recipient = config.recipient
        self._username = environment.smtp_user or ""
        self._password = environment.smtp_app_password or ""
        self._timeout_seconds = timeout_seconds
        self._smtp_factory = smtp_factory

    def verify_connection(self) -> None:
        """Authenticate over STARTTLS without sending mail."""

        try:
            with self._smtp_factory(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self._username, self._password)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(
                f"SMTP verification failed via {self._host}:{self._port}: "
                f"{type(exc).__name__}"
            ) from exc

    def send_game_alert(
        self,
        text_email: TextEmail,
        html_email: HtmlEmail | None = None,
    ) -> None:
        """Send one plain-text or multipart alert, raising on any refusal."""

        message = self._build_message(text_email, html_email)
        try:
            with self._smtp_factory(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self._username, self._password)
                refused = smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(
                f"SMTP delivery failed via {self._host}:{self._port}: "
                f"{type(exc).__name__}"
            ) from exc

        if refused:
            raise EmailDeliveryError(
                f"SMTP server refused {len(refused)} recipient(s) via "
                f"{self._host}:{self._port}"
            )

    def _build_message(
        self,
        text_email: TextEmail,
        html_email: HtmlEmail | None,
    ) -> EmailMessage:
        if html_email is not None and html_email.subject != text_email.subject:
            raise EmailDeliveryError("Text and HTML email subjects must match")

        message = EmailMessage()
        message["Subject"] = text_email.subject
        message["From"] = self._username
        message["To"] = self._recipient
        message.set_content(text_email.body)
        if html_email is not None:
            message.add_alternative(html_email.body, subtype="html")
        return message
