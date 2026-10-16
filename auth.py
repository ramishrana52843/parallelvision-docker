"""
auth.py — simple session-based authentication (no third-party auth service).

  /register  create account (password hashed with werkzeug's salted hash)
  /login     verify password, store user id in a signed session cookie
  /logout    clear session
  /verify    only when SMTP is configured: enter the emailed 6-digit code
"""

from __future__ import annotations

import re
from functools import wraps

from flask import (Blueprint, current_app, flash, g, redirect,
                   render_template, request, session, url_for)

from models import db, User
from mailer import send_verification_code

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── helpers ──────────────────────────────────────────────────────────────────

@bp.before_app_request
def load_user():
    uid = session.get("uid")
    g.user = db.session.get(User, uid) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith(("/process", "/status")):
                return {"error": "Please log in first."}, 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _safe_next(target: str | None) -> str:
    # Only allow same-site relative redirects.
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


def _send_code(user: User) -> bool:
    code = user.issue_verify_code(current_app.config["VERIFY_CODE_MINUTES"])
    db.session.commit()
    try:
        send_verification_code(current_app, user.email, code)
        return True
    except Exception as exc:  # SMTP down, bad credentials, ...
        current_app.logger.error("Could not send verification email: %s", exc)
        return False


# ── routes ───────────────────────────────────────────────────────────────────

@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        error = None
        if not EMAIL_RE.match(email) or len(email) > 255:
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif User.query.filter_by(email=email).first():
            error = "That email is already registered."

        if error:
            flash(error, "error")
            return render_template("register.html", email=email), 400

        verify = current_app.config["EMAIL_VERIFICATION"]
        user = User(email=email, is_verified=not verify)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        if not verify:
            session.clear()
            session["uid"] = user.id
            return redirect(url_for("index"))

        session.clear()
        session["pending_uid"] = user.id
        if not _send_code(user):
            flash("Account created, but the email could not be sent. Use 'Resend code'.", "error")
        return redirect(url_for("auth.verify"))

    return render_template("register.html", email="")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("login.html", email=email), 401

        if not user.is_verified:
            session.clear()
            session["pending_uid"] = user.id
            _send_code(user)
            flash("Please verify your email first. We sent you a new code.", "error")
            return redirect(url_for("auth.verify"))

        session.clear()
        session["uid"] = user.id
        return redirect(_safe_next(request.args.get("next")))

    return render_template("login.html", email="")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    user = db.session.get(User, session.get("pending_uid")) if session.get("pending_uid") else None
    if user is None or user.is_verified:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        cfg = current_app.config
        ok = user.check_verify_code(request.form.get("code", ""), cfg["VERIFY_MAX_ATTEMPTS"])
        db.session.commit()
        if ok:
            user.is_verified = True
            user.verify_code_hash = None
            db.session.commit()
            session.clear()
            session["uid"] = user.id
            return redirect(url_for("index"))
        flash("Wrong or expired code. Request a new one if needed.", "error")
        return render_template("verify.html", email=user.email), 400

    return render_template("verify.html", email=user.email)


@bp.route("/verify/resend", methods=["POST"])
def verify_resend():
    user = db.session.get(User, session.get("pending_uid")) if session.get("pending_uid") else None
    if user is None or user.is_verified:
        return redirect(url_for("auth.login"))
    if _send_code(user):
        flash("A new code was sent.", "success")
    else:
        flash("Could not send the email. Check the SMTP settings.", "error")
    return redirect(url_for("auth.verify"))
