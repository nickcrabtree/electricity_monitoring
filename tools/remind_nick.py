#!/usr/bin/env python3
"""Email Nick the open items in docs/REMINDERS.md. Run weekly from quartz's crontab; nothing is sent if none is open.

An open item is a `- [ ] ...` line plus any indented continuation lines; `- [x]` items are done and skipped. The
weekly cron schedule is what keeps reminders to at most one a week, so no state is kept. Mail goes to quartz's postfix
as for every other LAN host (see ~/scripts/cross_repo_notes/topics/mail_notifications.md). Stdlib only.

  remind_nick.py               send now if anything is open
  remind_nick.py --dry-run     print the email instead
"""

from __future__ import annotations

import argparse
import smtplib
import socket
import sys
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path

REMINDERS = Path(__file__).resolve().parent.parent / 'docs' / 'REMINDERS.md'
SMTP_HOST = 'quartz.local'
SMTP_PORT = 25
RCPT = 'nickc@quartz.local'
SENDER = f'reminders@{socket.gethostname()}'


def open_items(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith('- [ ] '):
            current = [line[6:].strip()]
            items.append('')
        elif line.startswith('- '):
            current = None
        elif current is not None and line.startswith('  ') and line.strip():
            current.append(line.strip())
        else:
            current = None
        if current is not None:
            items[-1] = '\n'.join(current)
    return items


def message(items: list[str]) -> tuple[str, str]:
    subject = f'[reminder] {len(items)} open item{"s" if len(items) != 1 else ""} (electricity_monitoring)'
    body = '\n\n'.join(f'- {item}' for item in items)
    body += (
        '\n\nThese come from docs/REMINDERS.md in the electricity_monitoring repo (weekly, from quartz cron).'
        '\nTick an item ([x]) there, or ask an agent to, to stop it.\n'
    )
    return subject, body


def send_email(subject: str, body: str) -> bool:
    try:
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = SENDER, RCPT, subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print(f'could not send reminder email: {exc}', file=sys.stderr)
        return False


def run(text: str, send: Callable[[str, str], bool] = send_email) -> int:
    items = open_items(text)
    if not items:
        return 0
    return 0 if send(*message(items)) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    text = REMINDERS.read_text()
    if args.dry_run:
        items = open_items(text)
        print('\n\n'.join(message(items)) if items else 'nothing open')
        return 0
    return run(text)


if __name__ == '__main__':
    raise SystemExit(main())
