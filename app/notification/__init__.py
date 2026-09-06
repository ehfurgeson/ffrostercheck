"""Notification rendering and delivery interfaces."""

from app.notification.templates import TextEmail, build_text_email

__all__ = ["TextEmail", "build_text_email"]
