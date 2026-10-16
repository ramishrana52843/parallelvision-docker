"""
models.py — two tables in RDS:

  users   : login accounts (password stored as a salted hash, never plain text)
  images  : one row per processed image; the actual bytes live in S3,
            the row only stores the S3 keys + metadata, owned by a user.
"""

from datetime import datetime, timedelta
import hashlib
import hmac
import secrets

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_verified   = db.Column(db.Boolean, nullable=False, default=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Optional email-verification state (only used when SMTP is configured)
    verify_code_hash = db.Column(db.String(64))
    verify_expires   = db.Column(db.DateTime)
    verify_attempts  = db.Column(db.Integer, nullable=False, default=0)

    images = db.relationship("Image", backref="owner", lazy="dynamic",
                             cascade="all, delete-orphan")

    # ── password ──
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # ── verification code ──
    def issue_verify_code(self, minutes: int) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.verify_code_hash = hashlib.sha256(code.encode()).hexdigest()
        self.verify_expires   = datetime.utcnow() + timedelta(minutes=minutes)
        self.verify_attempts  = 0
        return code

    def check_verify_code(self, code: str, max_attempts: int) -> bool:
        if (not self.verify_code_hash or not self.verify_expires
                or datetime.utcnow() > self.verify_expires
                or self.verify_attempts >= max_attempts):
            return False
        self.verify_attempts += 1
        given = hashlib.sha256(code.strip().encode()).hexdigest()
        return hmac.compare_digest(given, self.verify_code_hash)


class Image(db.Model):
    __tablename__ = "images"

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    display_name  = db.Column(db.String(255), nullable=False)
    original_key  = db.Column(db.String(512), nullable=False)   # S3 object key
    result_key    = db.Column(db.String(512), nullable=False)   # S3 object key
    operations    = db.Column(db.String(512), nullable=False, default="")
    width         = db.Column(db.Integer)
    height        = db.Column(db.Integer)
    process_ms    = db.Column(db.Float)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
