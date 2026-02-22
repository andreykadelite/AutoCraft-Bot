from __future__ import annotations

import datetime as dt

from flask import flash, jsonify, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_appbuilder.security.sqla.models import User
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy import select

from ..db import db
from ..models.admin_broadcast import (
    AdminBroadcast,
    AdminBroadcastDelivery,
    AdminLoginBanner,
)
from ..models.audit import AuditLog
from ..models.messages import UserMessage
from ..security import panel_has_access as has_access

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или устарело. Обновите страницу и повторите действие."
)
_MAX_SUBJECT_LEN = 200
_MAX_BODY_LEN = 4000
_DEFAULT_SUBJECT = "Сообщение администратора"
_DEFAULT_HISTORY_LIMIT = 30
_DEFAULT_PULL_LIMIT = 5


def _is_csrf_valid() -> bool:
    token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.args.get("csrf_token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().casefold() in {"1", "true", "on", "yes", "да"}


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def _compact_text(text: str, limit: int = 200) -> str:
    cleaned = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _latest_login_banner() -> AdminLoginBanner | None:
    return (
        db.session.query(AdminLoginBanner)
        .order_by(AdminLoginBanner.id.desc())
        .first()
    )


def _broadcast_channels(item: AdminBroadcast) -> list[str]:
    channels = ["Внутренние сообщения всем пользователям"]
    if item.notify_authenticated:
        channels.append("Уведомление авторизованным пользователям")
    return channels


def _load_history(limit: int = _DEFAULT_HISTORY_LIMIT) -> list[dict]:
    rows = (
        db.session.query(AdminBroadcast)
        .order_by(AdminBroadcast.id.desc())
        .limit(max(1, limit))
        .all()
    )
    items: list[dict] = []
    for row in rows:
        items.append(
            {
                "id": int(row.id),
                "created_at": _format_datetime(row.created_at),
                "created_by_username": row.created_by_username or "",
                "subject": (row.subject or "").strip() or _DEFAULT_SUBJECT,
                "body_preview": _compact_text(row.body or ""),
                "recipients_count": int(row.recipients_count or 0),
                "channels": _broadcast_channels(row),
                "notify_authenticated": bool(row.notify_authenticated),
            }
        )
    return items


def _load_login_banner_payload() -> dict:
    banner = _latest_login_banner()
    if not banner:
        return {
            "enabled": False,
            "subject": "",
            "body": "",
            "updated_at": "",
            "updated_by_username": "",
        }
    return {
        "enabled": bool(banner.enabled),
        "subject": (banner.subject or "").strip(),
        "body": banner.body or "",
        "updated_at": _format_datetime(banner.updated_at),
        "updated_by_username": banner.updated_by_username or "",
    }


def _append_audit_log(action: str, result: str, details: str = "") -> None:
    actor = getattr(current_user, "username", "") or "web"
    try:
        log = AuditLog(
            user=actor,
            action=action,
            target="admin_broadcast",
            result=result,
            source="web",
            ip=request.remote_addr or "",
            details=details,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


class AdminBroadcastView(BaseView):
    route_base = "/admin-broadcast"
    base_permissions = ["can_list", "can_action"]
    default_view = "list"

    @expose("/")
    @has_access
    def list(self):
        login_banner = _load_login_banner_payload()
        can_action = self.appbuilder.sm.has_access("can_action", self.class_permission_name)
        return self.render_template(
            "admin_broadcast.html",
            history=_load_history(),
            can_action=can_action,
            login_banner=login_banner,
        )

    @expose("/send", methods=["POST"])
    @has_access
    @permission_name("action")
    def send(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        subject_raw = (request.form.get("subject") or "").strip()
        body = (request.form.get("body") or "").strip()
        notify_authenticated = True

        if not body:
            flash("Введите текст рассылки.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if len(subject_raw) > _MAX_SUBJECT_LEN:
            flash(
                f"Тема слишком длинная (максимум {_MAX_SUBJECT_LEN} символов).",
                "warning",
            )
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if len(body) > _MAX_BODY_LEN:
            flash(
                f"Сообщение слишком длинное (максимум {_MAX_BODY_LEN} символов).",
                "warning",
            )
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        recipients = (
            db.session.query(User)
            .filter(
                User.active.is_(True),
            )
            .all()
        )
        subject = subject_raw or None
        messages = [
            UserMessage(
                sender_id=current_user.id,
                recipient_id=user.id,
                subject=subject,
                body=body,
            )
            for user in recipients
        ]
        broadcast = AdminBroadcast(
            created_at=dt.datetime.utcnow(),
            created_by_id=int(current_user.id),
            created_by_username=(current_user.username or "admin"),
            subject=subject,
            body=body,
            recipients_count=len(messages),
            show_on_login=False,
            notify_authenticated=notify_authenticated,
        )

        try:
            db.session.add(broadcast)
            if messages:
                db.session.add_all(messages)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            flash(f"Не удалось сохранить рассылку: {exc}", "danger")
            _append_audit_log("admin_broadcast_send", "fail", details="db_error")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        channels = ["всем пользователям"]
        channels.append("уведомления авторизованным")
        channels_text = ", ".join(channels)
        flash(
            (
                f"Рассылка отправлена. Доставлено внутреннее сообщение пользователям: {len(messages)}. "
                f"Каналы: {channels_text}."
            ),
            "success",
        )
        _append_audit_log(
            "admin_broadcast_send",
            "ok",
            details=(
                f"broadcast_id={broadcast.id};recipients={len(messages)};"
                f"notify={int(notify_authenticated)}"
            ),
        )
        return redirect(url_for(f"{self.__class__.__name__}.list"))

    @expose("/login-banner/save", methods=["POST"])
    @has_access
    @permission_name("action")
    def save_login_banner(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        enabled = _parse_bool(request.form.get("banner_enabled"))
        subject_raw = (request.form.get("banner_subject") or "").strip()
        body = (request.form.get("banner_body") or "").strip()

        if len(subject_raw) > _MAX_SUBJECT_LEN:
            flash(
                f"Тема баннера слишком длинная (максимум {_MAX_SUBJECT_LEN} символов).",
                "warning",
            )
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if len(body) > _MAX_BODY_LEN:
            flash(
                f"Текст баннера слишком длинный (максимум {_MAX_BODY_LEN} символов).",
                "warning",
            )
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if enabled and not body:
            flash("Для включения баннера заполните текст сообщения.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        current = _latest_login_banner()
        if not current:
            current = AdminLoginBanner(
                enabled=False,
                subject="",
                body="",
                updated_at=dt.datetime.utcnow(),
                updated_by_id=int(current_user.id),
                updated_by_username=(current_user.username or "admin"),
            )
            db.session.add(current)

        current.enabled = enabled
        current.subject = subject_raw or None
        current.body = body
        current.updated_at = dt.datetime.utcnow()
        current.updated_by_id = int(current_user.id)
        current.updated_by_username = (current_user.username or "admin")

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            flash(f"Не удалось сохранить баннер: {exc}", "danger")
            _append_audit_log("admin_login_banner_update", "fail", details="db_error")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        if enabled:
            flash("Баннер на странице авторизации включен и обновлен.", "success")
        else:
            flash("Баннер на странице авторизации отключен.", "success")

        _append_audit_log(
            "admin_login_banner_update",
            "ok",
            details=(
                f"enabled={int(enabled)};subject_len={len(subject_raw)};body_len={len(body)}"
            ),
        )
        return redirect(url_for(f"{self.__class__.__name__}.list"))

    @expose("/notifications/poll", methods=["POST"])
    def poll_notifications(self):
        if not current_user.is_authenticated:
            return jsonify({"error": "unauthorized"}), 401
        if not _is_csrf_valid():
            return jsonify({"error": "csrf"}), 403

        limit = _parse_int(request.args.get("limit"), _DEFAULT_PULL_LIMIT)
        limit = max(1, min(limit, 20))

        delivered_select = select(AdminBroadcastDelivery.broadcast_id).where(
            AdminBroadcastDelivery.user_id == current_user.id
        )
        rows = (
            db.session.query(AdminBroadcast)
            .filter(
                AdminBroadcast.notify_authenticated.is_(True),
                ~AdminBroadcast.id.in_(delivered_select),
            )
            .order_by(AdminBroadcast.id.desc())
            .limit(limit)
            .all()
        )
        if not rows:
            return jsonify({"items": []})

        rows = list(reversed(rows))
        now = dt.datetime.utcnow()
        try:
            for row in rows:
                db.session.add(
                    AdminBroadcastDelivery(
                        broadcast_id=int(row.id),
                        user_id=int(current_user.id),
                        delivered_at=now,
                    )
                )
            db.session.commit()
        except Exception:
            db.session.rollback()
            return jsonify({"items": []})

        items: list[dict] = []
        for row in rows:
            items.append(
                {
                    "id": int(row.id),
                    "subject": (row.subject or "").strip() or _DEFAULT_SUBJECT,
                    "body": row.body or "",
                    "created_at": _format_datetime(row.created_at),
                    "author": row.created_by_username or "",
                }
            )
        return jsonify({"items": items})
