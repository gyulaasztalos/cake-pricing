"""SMTP mailer — the price-sync report e-mail.

Mirrors cake-order's mailer (same iCloud account, STARTTLS/TLS/plain switch).
The only user-ish data in the body is component names, Jinja-autoescaped for the
HTML part; nothing user-controlled reaches a header. Failures raise MailerError
so the job fails loudly (→ AlertManager).
"""

from __future__ import annotations

import datetime as dt
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.config import settings
from app.i18n import t
from app.services.price_sync import SyncResult
from app.templating import email_env


class MailerError(Exception):
    """Sending failed."""


def _render(template: str, **ctx: object) -> str:
    return email_env.get_template(f"email/{template}").render(**ctx)


def _send(msg: EmailMessage) -> None:
    try:
        if settings.smtp_security == "tls":
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=ssl.create_default_context()
            ) as smtp:
                _login_and_send(smtp, msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
                if settings.smtp_security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                _login_and_send(smtp, msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailerError(str(exc)) from exc


def _login_and_send(smtp: smtplib.SMTP, msg: EmailMessage) -> None:
    if settings.smtp_user:
        smtp.login(settings.smtp_user, settings.smtp_password)
    smtp.send_message(msg)


def send_price_report(result: SyncResult) -> None:
    """Report the day's price changes and any not-found product ids to the shop
    inbox. Caller only invokes this when there is something to report."""
    ctx = {
        "changes": result.changes,
        "missing": result.missing,
        "checked": result.checked,
        "now": dt.datetime.now(dt.UTC),
    }
    msg = EmailMessage()
    msg["From"] = str(Address("Anita Tortái", addr_spec=settings.mail_from))
    msg["To"] = settings.order_inbox
    msg["Subject"] = t(
        "email.price_report.subject", changed=len(result.changes), missing=len(result.missing)
    )
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(_render("price_report.txt", **ctx))
    msg.add_alternative(_render("price_report.html", **ctx), subtype="html")
    _send(msg)
