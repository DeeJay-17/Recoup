#!/usr/bin/env python3
"""Scripted customer AP persona for closed-loop demos and tests.

Polls Mailpit for outbound emails that have no reply yet and answers according to the invoice's
scenario (from the Mock ERP ground truth): provides the PO, confirms payment after a credit memo,
accepts a payment plan, redirects to the active contact, or disputes. The LLM-driven persona
(phase 7) replaces the canned texts; the loop stays the same.

Usage: uv run python scripts/simulate_customer.py [--once] [--interval 15] [--persona cooperative|evasive]
"""

from __future__ import annotations

import argparse
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage

import httpx

MAILPIT = os.environ.get("MAILPIT_API_URL", "http://localhost:8025")
SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
MAILBOX = os.environ.get("INBOUND_MAILBOX", "ar@acme-demo.com")
ERP = os.environ.get("MOCK_ERP_URL", "http://localhost:8002")
COMM = os.environ.get("COMM_URL", "http://localhost:8005")
INV_RE = re.compile(r"\bINV-\d{5,8}\b")

REPLIES = {
    "MISSING_PO": "Thanks for reaching out. Our purchase order for this invoice is {po}. Please reissue the invoice referencing it and we will process it in this week's run.",
    "WRONG_CONTACT": "Thanks, received. I'm the right contact for AP going forward; the invoice is now in our system and payment will be scheduled on {date}.",
    "DISPUTE_PRICING": "Thank you for the credit memo, that matches our PO pricing. The remaining balance is scheduled for payment on {date}.",
    "DISPUTE_QUANTITY": "Appreciated, the credit for the short shipment resolves it. Payment will be released on {date}.",
    "DUPLICATE_INVOICE": "Confirmed, the original invoice was already paid; thanks for voiding the duplicate. Nothing further owed on our side.",
    "SHORT_PAY": "Understood. We've reviewed the contract and will remit the remaining freight amount; payment is scheduled for {date}.",
    "CASH_FLOW": "Thank you for your flexibility. We accept the payment plan as proposed and will pay the first installment on the first due date.",
    "NONE": "Thanks, we will look into this and revert.",
}
EVASIVE = "Thanks for the note. Can you send a copy of the statement and the original invoice? We'll review once received."
REDIRECT = "I have left the AP team; please contact {email} for anything related to this account."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=15)
    ap.add_argument("--persona", choices=["cooperative", "evasive"], default="cooperative")
    args = ap.parse_args()
    answered: set[str] = set()
    with httpx.Client(timeout=30) as c:
        while True:
            replied = 0
            msgs = c.get(f"{MAILPIT}/api/v1/messages", params={"limit": 200}).json()["messages"]
            answered_subjects = {
                m["Subject"].strip().lower()
                for m in msgs
                if m["From"]["Address"].lower() != MAILBOX
            }
            for m in msgs:
                if m["From"]["Address"].lower() != MAILBOX or m["ID"] in answered:
                    continue
                if f"re: {m['Subject'].strip().lower()}" in answered_subjects:
                    answered.add(m["ID"])
                    continue
                full = c.get(f"{MAILPIT}/api/v1/message/{m['ID']}").json()
                refs = INV_RE.findall(full["Subject"] + " " + (full.get("Text") or ""))
                scenario, po = "NONE", None
                if refs:
                    gt = c.get(f"{ERP}/admin/ground-truth/{refs[0]}")
                    if gt.status_code == 200:
                        scenario = gt.json().get("root_cause", "NONE")
                        inv = c.get(f"{ERP}/invoices/{refs[0]}").json()
                        po = inv.get("po_number")
                        if scenario == "MISSING_PO":
                            po = f"PO-{inv.get('customer_ref', 'CUST').split('-')[-1]}{refs[0].split('-')[1][-4:]}"
                to_addr = m["To"][0]["Address"]
                date = time.strftime("%Y-%m-%d", time.localtime(time.time() + 7 * 86400))
                if args.persona == "evasive":
                    text = EVASIVE
                else:
                    text = REPLIES.get(scenario, REPLIES["NONE"]).format(
                        po=po or "PO-50123", date=date
                    )
                reply = EmailMessage()
                reply["From"] = f"Customer AP <{to_addr}>"
                reply["To"] = MAILBOX
                reply["Subject"] = f"Re: {full['Subject']}"
                mid = (
                    full["MessageID"]
                    if full["MessageID"].startswith("<")
                    else f"<{full['MessageID']}>"
                )
                reply["In-Reply-To"] = mid
                reply["References"] = mid
                reply.set_content(f"{text}\n\n> {(full.get('Text') or '')[:160].strip()}")
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                    s.send_message(reply)
                answered.add(m["ID"])
                replied += 1
                print(f"[{scenario}] replied to '{full['Subject']}' as {to_addr}")
            if replied:
                r = c.post(f"{COMM}/internal/inbound/poll")
                print(f"inbound poll: {r.json() if r.status_code == 200 else r.status_code}")
            if args.once:
                return 0
            time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
