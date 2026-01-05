import logging
import math
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from flask import abort, jsonify, redirect, render_template, request, session, url_for

from . import admin_bp
from .auth import admin_login_required, authenticate, load_admin_credentials
from .services import (
    AdminDatabase,
    approve_users,
    count_users_filtered,
    compute_dashboard_counts,
    delete_keyword,
    extend_users_bulk,
    list_users_paginated,
    list_keyword_alerts,
    log_admin_action,
    toggle_keyword,
    update_block_state,
)

logger = logging.getLogger("admin_panel")

DATA_DIR = os.environ.get("DATA_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

_admin_db: Optional[AdminDatabase] = None
_dashboard_cache: dict = {"data": None, "ts": None}


def _get_admin_db() -> AdminDatabase:
    global _admin_db
    if _admin_db is None:
        _admin_db = AdminDatabase(DATA_DIR)
    return _admin_db


def _get_dashboard_stats() -> dict:
    now = datetime.utcnow()
    if _dashboard_cache.get("data") and _dashboard_cache.get("ts"):
        if now - _dashboard_cache["ts"] < timedelta(seconds=30):
            return _dashboard_cache["data"]

    stats = compute_dashboard_counts(_get_admin_db())
    _dashboard_cache["data"] = stats
    _dashboard_cache["ts"] = now
    return stats


def _invalidate_dashboard_cache():
    _dashboard_cache["data"] = None
    _dashboard_cache["ts"] = None


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
    stats = _get_dashboard_stats()
    return render_template("dashboard.html", stats=stats)


@admin_bp.route("/users", methods=["GET"])
@admin_login_required
def users():
    status_filter = (request.args.get("status") or "all").lower()
    if status_filter not in {"all", "active", "expired", "demo", "blocked"}:
        status_filter = "all"

    expiry_filter = request.args.get("expiry") or ""
    if expiry_filter not in {"today", "1d", "3d", "7d", "30d"}:
        expiry_filter = ""

    search_query = (request.args.get("search") or "").strip()

    try:
        page_int = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page_int = 1
    try:
        page_size_int = max(min(int(request.args.get("page_size", "50")), 200), 1)
    except ValueError:
        page_size_int = 50

    db = _get_admin_db()
    users_page = list_users_paginated(
        db,
        page_int,
        page_size_int,
        status_filter,
        search_query,
        expiry_filter,
    )
    total_users = count_users_filtered(db, status_filter, search_query, expiry_filter)
    total_pages = max(1, math.ceil(total_users / page_size_int)) if total_users else 1

    return render_template(
        "users.html",
        csrf_token=session.get("csrf_token"),
        users_page=users_page,
        total_pages=total_pages,
        total_users=total_users,
        current_page=page_int,
        page_size=page_size_int,
        status_filter=status_filter,
        search_query=search_query,
        expiry_filter=expiry_filter,
    )


@admin_bp.route("/users/bulk", methods=["POST"])
@admin_login_required
def users_bulk_action():
    require_csrf()
    payload = request.get_json(silent=True) or request.form
    action = (payload.get("action") or "").lower()
    try:
        chat_ids = [int(cid) for cid in payload.get("chat_ids", []) if int(cid) > 0]
    except Exception:
        chat_ids = []

    db = _get_admin_db()
    updated = 0
    message = ""

    if action == "extend":
        try:
            days = int(payload.get("days", 0))
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            updated = extend_users_bulk(db, chat_ids, days)
            message = f"{updated} istifadəçi üçün {days} gün əlavə edildi" if updated else "İstifadəçi tapılmadı"
    elif action == "approve":
        updated = approve_users(db, chat_ids)
        message = f"{updated} istifadəçi təsdiqləndi" if updated else "Təsdiqlənəcək istifadəçi tapılmadı"
    elif action == "block":
        updated = update_block_state(db, chat_ids, True)
        message = f"{updated} istifadəçi bloklandı" if updated else "Bloklanacaq istifadəçi tapılmadı"
    elif action == "unblock":
        updated = update_block_state(db, chat_ids, False)
        message = f"{updated} istifadəçi blokdan çıxarıldı" if updated else "Heç kim blokdan çıxarılmadı"
    else:
        return jsonify({"ok": False, "message": "Naməlum əməliyyat"}), 400

    if updated:
        admin_username = session.get("admin_username") or "unknown"
        log_admin_action(db, admin_username, action, updated)
        _invalidate_dashboard_cache()
        logger.info("Admin bulk action action=%s count=%s ids=%s", action, updated, chat_ids)

    return jsonify({"ok": updated > 0, "updated": updated, "message": message})


@admin_bp.route("/keywords", methods=["GET", "POST"])
@admin_login_required
def keywords():
    message: Optional[str] = None
    error: Optional[str] = None
    user_filter: Optional[int] = None
    db = _get_admin_db()

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
