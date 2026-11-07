"""Sending email. Two transports behind one small interface: SMTP (Mailpit locally) and
Amazon SES through its API (production, with the instance role's credentials).

Both are blocking client libraries, so each send runs in a worker thread.
"""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Protocol

import boto3

from app.core.config import EmailBackend, Settings


@dataclass(frozen=True)
class OutgoingEmail:
    to_address: str
    to_name: str
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    async def send(self, email: OutgoingEmail) -> None:
        """Deliver one message, or raise if the transport refused it."""
        ...


def build_mime(email: OutgoingEmail, sender: str) -> EmailMessage:
    """A multipart/alternative message with a plain-text and an HTML part."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = formataddr((email.to_name, email.to_address))
    message["Subject"] = email.subject
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    return message


class SmtpSender:
    def __init__(self, host: str, port: int, sender: str, timeout: float) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._timeout = timeout

    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send_blocking, build_mime(email, self._sender))

    def _send_blocking(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.send_message(message)


class SesSender:
    """Sends the same MIME message through the SES v2 API. Credentials come from the
    default chain, which is the instance role in production."""

    def __init__(self, sender: str, region: str | None, client: Any = None) -> None:
        self._sender = sender
        self._client = client or boto3.client("sesv2", region_name=region)

    async def send(self, email: OutgoingEmail) -> None:
        raw = build_mime(email, self._sender).as_bytes()
        await asyncio.to_thread(
            self._client.send_email,
            FromEmailAddress=self._sender,
            Destination={"ToAddresses": [email.to_address]},
            Content={"Raw": {"Data": raw}},
        )


def build_sender(settings: Settings) -> EmailSender:
    if settings.email_backend is EmailBackend.SES:
        return SesSender(settings.email_from, settings.ses_region)
    return SmtpSender(
        settings.smtp_host,
        settings.smtp_port,
        settings.email_from,
        settings.smtp_timeout_seconds,
    )
