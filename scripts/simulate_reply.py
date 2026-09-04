#!/usr/bin/env python3
"""Play the customer: reply to the newest outbound email in Mailpit so the inbound poller links it.

Usage: uv run python scripts/simulate_reply.py [--text "..."] [--attach-pdf path]
Stands in for the Customer Simulator until phase 7.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.message import EmailMessage

import httpx

MAILPIT = os.environ.get("MAILPIT_API_URL", "http://localhost:8025")
SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
MAILBOX = os.environ.get("INBOUND_MAILBOX", "ar@acme-demo.com")
COMM = os.environ.get("COMM_URL", "http://localhost:8005")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--text",
        default=(
            "Thanks for the note. Our PO for this invoice is PO-50123; "
            "please reissue and we will process it this week."
        ),
    )
    ap.add_argument("--attach-pdf", default=None)
    ap.add_argument(
        "--no-thread",
        action="store_true",
        help="Reply without In-Reply-To (forces invoice-number linking)",
    )
    args = ap.parse_args()

    with httpx.Client(timeout=30) as c:
        msgs = c.get(f"{MAILPIT}/api/v1/messages", params={"limit": 50}).json()["messages"]
        outbound = [m for m in msgs if m["From"]["Address"].lower() == MAILBOX]
        if not outbound:
            print(
                "no outbound email in Mailpit yet; send one first "
                "(tool send_email or POST /emails/send)"
            )
            return 1
        latest = outbound[0]
        full = c.get(f"{MAILPIT}/api/v1/message/{latest['ID']}").json()
        to_addr = latest["To"][0]["Address"]
        reply = EmailMessage()
        reply["From"] = f"Customer AP <{to_addr}>"
        reply["To"] = MAILBOX
        reply["Subject"] = f"Re: {full['Subject']}"
        if not args.no_thread:
            reply["In-Reply-To"] = (
                full["MessageID"] if full["MessageID"].startswith("<") else f"<{full['MessageID']}>"
            )
            reply["References"] = reply["In-Reply-To"]
        reply.set_content(f"{args.text}\n\n> {full['Text'][:200].strip()}")
        if args.attach_pdf:
            with open(args.attach_pdf, "rb") as f:
                reply.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype="pdf",
                    filename=os.path.basename(args.attach_pdf),
                )
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.send_message(reply)
        print(f"replied as {to_addr} to '{full['Subject']}'")
        r = c.post(f"{COMM}/internal/inbound/poll")
        r.raise_for_status()
        print(f"inbound poll: {r.json()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
