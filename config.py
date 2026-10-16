"""
config.py — all settings come from environment variables so the same code
runs locally, on EC2, and on Elastic Beanstalk without edits.

Beanstalk: Configuration > Software > Environment properties
EC2:       export them, or put them in /etc/parallelvision.env (see deploy/)
"""

import os
from sqlalchemy.engine import URL


def _database_url() -> str:
    # 1. Explicit URL wins, e.g. mysql+pymysql://user:pass@host:3306/dbname
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    # 2. Beanstalk sets these automatically when an RDS instance is attached
    #    to the environment through the console.
    if os.environ.get("RDS_HOSTNAME"):
        return URL.create(
            "mysql+pymysql",
            username=os.environ.get("RDS_USERNAME"),
            password=os.environ.get("RDS_PASSWORD"),
            host=os.environ["RDS_HOSTNAME"],
            port=int(os.environ.get("RDS_PORT", 3306)),
            database=os.environ.get("RDS_DB_NAME", "ebdb"),
        ).render_as_string(hide_password=False)

    # 3. Local development fallback (NOT for deployment: disk is ephemeral)
    return "sqlite:///local.db"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB per request

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"

    # S3
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    PRESIGN_SECONDS = 900

    # Limits
    MAX_IMAGES_PER_JOB = 20

    # Optional email verification. Leave SMTP_HOST empty to skip it entirely
    # (accounts are then active immediately after registering).
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", "")
    VERIFY_CODE_MINUTES = 15
    VERIFY_MAX_ATTEMPTS = 5

    @classmethod
    def email_verification_enabled(cls) -> bool:
        return bool(cls.SMTP_HOST and cls.SMTP_FROM)
