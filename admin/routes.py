import logging
import os
import secrets
from typing import Optional

from flask import abort, redirect, render_template, request, session, url_for

from . import admin_bp
from .auth import admin_login_required, authenticate, load_admin_credentials
from .services import (
    AdminDatabase,
    approve_user,
    block_user,
    compute_dashboard_counts,
    compute_user_status,
    delete_keyword,
    extend_user,
    fetch_subscription,
    fetch_user,
    list_keyword_alerts,
    toggle_keyword,
)

logger = logging.getLogger("admin_panel")

DATA_DIR = os.environ.get("DATA_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
db = AdminDatabase(DATA_DIR)


def require_csrf():
    token = session.get("csrf_token")
    form_token = request.form.get("csrf_token") or (request.get_json(silent=True) or {}).get(
        "csrf_token"
    )
    if not token or not form_token or token != form_token:
        abort(400)


@admin_bp.before_app_request
def ensure_credentials_loaded():
    if request.blueprint != admin_bp.name:
        return None
    if not load_admin_credentials():
        return "Admin credentials are not configured", 503
    return None


@admin_bp.route("/")
def admin_root():
    return redirect(url_for("admin.login"))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    error: Optional[str] = None
    if session.get("admin_authenticated"):
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            error = "Username və şifrə tələb olunur"
        elif authenticate(username, password):
            session["admin_authenticated"] = True
            session["admin_username"] = username
            session["csrf_token"] = secrets.token_hex(16)
            logger.info("Admin login successful username=%s", username)
            return redirect(url_for("admin.dashboard"))
        else:
            logger.warning("Admin login failed username=%s", username)
            error = "Düzgün olmayan məlumatlar"
    return render_template("login.html", error=error)


@admin_bp.route("/logout")
def logout():
    username = session.get("admin_username")
    session.clear()
    if username:
        logger.info("Admin logout username=%s", username)
    return redirect(url_for("admin.login"))


@admin_bp.route("/dashboard")
@admin_login_required
def dashboard():
    stats = compute_dashboard_counts(db)
    return render_template("dashboard.html", stats=stats)


@admin_bp.route("/users", methods=["GET", "POST"])
@admin_login_required
def users():
    message: Optional[str] = None
    error: Optional[str] = None
    user_row = None
    sub_row = None
    status = None
    effective = None
    chat_id_param = request.args.get("chat_id") or request.form.get("chat_id")

    if request.method == "POST":
        require_csrf()
        action = request.form.get("action")
        try:
            chat_id = int(request.form.get("chat_id", "0"))
        except ValueError:
            chat_id = None
        if not chat_id:
            error = "chat_id tələb olunur"
        elif action == "extend":
            try:
                days = int(request.form.get("days", "0"))
            except ValueError:
                days = 0
            new_exp = extend_user(db, chat_id, days)
            if new_exp:
                message = (
                    f"İstifadəçinin müddəti {days} gün uzadıldı. Yeni tarix: {new_exp:%Y-%m-%d %H:%M}"
                )
                logger.info("Admin extended user chat_id=%s days=%s", chat_id, days)
            else:
                error = "İstifadəçi tapılmadı və ya müddət uzadıla bilmədi"
        elif action == "block":
            if block_user(db, chat_id, True):
                message = "İstifadəçi bloklandı"
                logger.info("Admin blocked user chat_id=%s", chat_id)
            else:
                error = "Bloklama mümkün olmadı"
        elif action == "unblock":
            if block_user(db, chat_id, False):
                message = "İstifadəçi blokdan çıxarıldı"
                logger.info("Admin unblocked user chat_id=%s", chat_id)
            else:
                error = "Blokdan çıxarma mümkün olmadı"
        elif action == "approve":
            if approve_user(db, chat_id):
                message = "İstifadəçi təsdiqləndi"
                logger.info("Admin approved user chat_id=%s", chat_id)
            else:
                error = "Təsdiqləmə mümkün olmadı"
        else:
            error = "Naməlum əməliyyat"
        chat_id_param = chat_id

    if chat_id_param:
        try:
            chat_id_int = int(chat_id_param)
            user_row = fetch_user(db, chat_id_int)
            sub_row = fetch_subscription(db, chat_id_int)
            if user_row:
                status, effective = compute_user_status(user_row, sub_row)
        except ValueError:
            error = "chat_id düzgün deyil"

    return render_template(
        "users.html",
        user=user_row,
        subscription=sub_row,
        status=status,
        effective=effective,
        message=message,
        error=error,
        csrf_token=session.get("csrf_token"),
    )


@admin_bp.route("/keywords", methods=["GET", "POST"])
@admin_login_required
def keywords():
    message: Optional[str] = None
    error: Optional[str] = None
    user_filter: Optional[int] = None
    if request.method == "POST":
        require_csrf()
        action = request.form.get("action")
        try:
            alert_id = int(request.form.get("alert_id", "0"))
        except ValueError:
            alert_id = None
        try:
            user_filter = int(request.form.get("user_id", "0")) if request.form.get("user_id") else None
        except ValueError:
            user_filter = None

        if action in {"enable", "disable"} and alert_id:
            if toggle_keyword(db, alert_id, action == "enable"):
                message = "Açar söz yeniləndi"
                logger.info("Admin toggled keyword alert_id=%s action=%s", alert_id, action)
            else:
                error = "Yeniləmə mümkün olmadı"
        elif action == "delete" and alert_id:
            if delete_keyword(db, alert_id):
                message = "Açar söz silindi"
                logger.info("Admin deleted keyword alert_id=%s", alert_id)
            else:
                error = "Silinmə mümkün olmadı"
        else:
            error = "Naməlum əməliyyat"
    else:
        try:
            user_filter = int(request.args.get("user_id")) if request.args.get("user_id") else None
        except ValueError:
            user_filter = None

    alerts = list_keyword_alerts(db, user_filter)
    return render_template(
        "keywords.html",
        alerts=alerts,
        message=message,
        error=error,
        csrf_token=session.get("csrf_token"),
        user_filter=user_filter,
    )
