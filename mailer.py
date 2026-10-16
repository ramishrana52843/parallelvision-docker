"""
mailer.py — sends the 6-digit verification code over plain SMTP (stdlib only,
no extra packages, no third-party SDK).

Only used when SMTP_HOST is set. Any SMTP server works:
  * Gmail   : smtp.gmail.com : 587, with an "App password" (needs 2-step verification)
  * AWS SES : email-smtp.<region>.amazonaws.com : 587, with SES SMTP credentials
Port 587 (STARTTLS) is used on purpose: EC2 blocks outbound port 25 by default.
"""

import smtplib
from email.message import EmailMessage


def send_verification_code(app, to_addr: str, code: str) -> None:
    cfg = app.config
    msg = EmailMessage()
    msg["Subject"] = "Your ParallelVision verification code"
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to_addr
    msg.set_content(
        f"Your verification code is: {code}\n\n"
        f"It expires in {cfg['VERIFY_CODE_MINUTES']} minutes. "
        "If you didn't sign up, ignore this email."
    )

    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as server:
        server.starttls()
        if cfg["SMTP_USER"]:
            server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        server.send_message(msg)
