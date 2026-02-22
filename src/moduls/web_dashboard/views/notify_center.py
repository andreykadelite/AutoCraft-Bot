from __future__ import annotations

import datetime as dt

from flask import g, jsonify, request, session, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_appbuilder.security.sqla.models import User
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy import func, or_

from ..db import db
from ..models.audit import AuditLog
from ..models.internal_messenger import InternalChatMessage, InternalChatState
from ..models.messages import UserMessage
from ..models.user_notification_state import (
    UserNotificationState,
    ensure_user_notification_state_schema,
)
from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from ..unread_counters import count_communications_unread, count_messenger_unread

_SYSTEM_HISTORY_LIMIT = 100
_POLL_SYSTEM_HISTORY_LIMIT = 10
_POLL_SYSTEM_BANNERS_LIMIT = 6
_POLL_INITIAL_MESSAGE_BANNERS_LIMIT = 6
_POLL_MESSAGE_BANNERS_LIMIT = 12
_EXCLUDED_ACTIONS = {"login"}
_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или устарело. Обновите страницу и повторите действие."
)
_ACTION_TITLES = {
    "panel_settings_update": "Настройки сохранены",
    "panel_settings_update_api": "Настройки сохранены через API",
    "admin_broadcast_send": "Рассылка администратора выполнена",
    "admin_login_banner_update": "Баннер авторизации обновлён",
    "message_send": "Сообщение отправлено",
    "messenger_send": "Сообщение в мессенджере отправлено",
    "messenger_start_chat": "Чат в мессенджере открыт",
    "messenger_delete_chats": "Диалоги мессенджера обновлены",
}


def _is_csrf_valid(token: str) -> bool:
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _get_csrf_token() -> str:
    return (
        request.form.get("csrf_token", "")
        or request.headers.get("X-CSRFToken", "")
        or request.headers.get("X-CSRF-Token", "")
        or request.args.get("csrf_token", "")
    )


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _compact_text(text: str, limit: int = 220) -> str:
    cleaned = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def _user_display(user: User | None) -> str:
    if not user:
        return "Неизвестный пользователь"
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    base = " ".join([part for part in (last, first) if part]).strip()
    if user.username:
        if base:
            return f"{base} ({user.username})"
        return user.username
    return base or f"ID {user.id}"


def _load_user_map(ids: set[int]) -> dict[int, User]:
    clean_ids = {int(item) for item in ids if item}
    if not clean_ids:
        return {}
    users = db.session.query(User).filter(User.id.in_(clean_ids)).all()
    return {int(user.id): user for user in users}


def _system_audit_query(username: str):
    query = db.session.query(AuditLog).filter(
        AuditLog.user == username,
        AuditLog.source.in_(("web", "api")),
    )
    if _EXCLUDED_ACTIONS:
        query = query.filter(~AuditLog.action.in_(_EXCLUDED_ACTIONS))
    return query


def _get_latest_system_audit_id(username: str, *, after_id: int = 0) -> int:
    query = _system_audit_query(username)
    if int(after_id) > 0:
        query = query.filter(AuditLog.id > int(after_id))
    latest = query.with_entities(func.max(AuditLog.id)).scalar()
    return int(latest or 0)


def _get_or_create_notification_state(user_id: int, username: str) -> UserNotificationState:
    ensure_user_notification_state_schema()
    state = (
        db.session.query(UserNotificationState)
        .filter(UserNotificationState.user_id == user_id)
        .first()
    )
    if state:
        return state

    state = UserNotificationState(
        user_id=user_id,
        system_last_read_audit_id=_get_latest_system_audit_id(username),
        updated_at=dt.datetime.utcnow(),
    )
    db.session.add(state)
    db.session.commit()
    return state


def _history_cutoff_audit_id(state: UserNotificationState) -> int:
    return int(state.system_history_cleared_before_audit_id or 0)


def _effective_system_read_audit_id(state: UserNotificationState) -> int:
    return max(
        int(state.system_last_read_audit_id or 0),
        _history_cutoff_audit_id(state),
    )


def _count_system_unread(
    username: str,
    *,
    last_read_audit_id: int,
    cleared_before_audit_id: int = 0,
) -> int:
    query = _system_audit_query(username)
    after_id = max(int(last_read_audit_id or 0), int(cleared_before_audit_id or 0))
    if after_id > 0:
        query = query.filter(AuditLog.id > int(after_id))
    return int(query.count() or 0)


def _level_from_audit(item: AuditLog) -> str:
    if (item.result or "").strip().casefold() == "ok":
        return "success"
    return "danger"


def _title_from_audit(item: AuditLog) -> str:
    action = (item.action or "").strip()
    if action in _ACTION_TITLES:
        return _ACTION_TITLES[action]
    if (item.result or "").strip().casefold() != "ok":
        return "Системное действие завершилось ошибкой"
    return "Системное действие выполнено"


def _body_from_audit(item: AuditLog) -> str:
    details = _compact_text(item.details or "", limit=260)
    if details:
        return details
    action = (item.action or "").strip()
    target = (item.target or "").strip()
    if target:
        return _compact_text(f"{action}: {target}", limit=260)
    return _compact_text(action or "Системное событие", limit=260)


def _serialize_system_item(item: AuditLog) -> dict:
    return {
        "id": int(item.id),
        "kind": "system",
        "title": _title_from_audit(item),
        "body": _body_from_audit(item),
        "created_at": _format_datetime(item.created_at),
        "level": _level_from_audit(item),
        "url": url_for("SystemNotifyCenterView.list"),
        "action_label": "Перейти",
        "source_label": "AutoCraft",
        "action": item.action or "",
        "target": item.target or "",
        "result": item.result or "",
    }


def _load_system_history(
    username: str,
    *,
    limit: int,
    cleared_before_audit_id: int = 0,
) -> list[dict]:
    query = _system_audit_query(username)
    if int(cleared_before_audit_id or 0) > 0:
        query = query.filter(AuditLog.id > int(cleared_before_audit_id))
    rows = (
        query
        .order_by(AuditLog.id.desc())
        .limit(max(1, limit))
        .all()
    )
    return [_serialize_system_item(row) for row in rows]


def _load_system_banners_after(
    username: str,
    *,
    after_id: int,
    limit: int,
    cleared_before_audit_id: int = 0,
) -> tuple[list[dict], int]:
    next_after_id = max(int(after_id or 0), int(cleared_before_audit_id or 0))
    rows = (
        _system_audit_query(username)
        .filter(AuditLog.id > int(next_after_id))
        .order_by(AuditLog.id.asc())
        .limit(max(1, limit))
        .all()
    )
    if not rows:
        return [], int(next_after_id)

    return [_serialize_system_item(row) for row in rows], int(rows[-1].id)


def _messenger_unread_query(user_id: int):
    return (
        db.session.query(InternalChatMessage)
        .join(
            InternalChatState,
            InternalChatState.thread_id == InternalChatMessage.thread_id,
        )
        .filter(
            InternalChatState.user_id == int(user_id),
            InternalChatState.is_hidden.is_(False),
            InternalChatMessage.sender_id != int(user_id),
            or_(
                InternalChatState.last_read_message_id.is_(None),
                InternalChatMessage.id > InternalChatState.last_read_message_id,
            ),
        )
    )


def _communications_unread_query(user_id: int):
    return db.session.query(UserMessage).filter(
        UserMessage.recipient_id == int(user_id),
        UserMessage.read_at.is_(None),
        UserMessage.deleted_by_recipient.is_(False),
    )


def _latest_messenger_received_message_id(user_id: int) -> int:
    latest = (
        db.session.query(func.max(InternalChatMessage.id))
        .join(
            InternalChatState,
            InternalChatState.thread_id == InternalChatMessage.thread_id,
        )
        .filter(
            InternalChatState.user_id == int(user_id),
            InternalChatState.is_hidden.is_(False),
            InternalChatMessage.sender_id != int(user_id),
        )
        .scalar()
    )
    return int(latest or 0)


def _latest_communications_received_message_id(user_id: int) -> int:
    latest = (
        db.session.query(func.max(UserMessage.id))
        .filter(
            UserMessage.recipient_id == int(user_id),
            UserMessage.deleted_by_recipient.is_(False),
        )
        .scalar()
    )
    return int(latest or 0)


def _load_messenger_banners(
    user_id: int,
    *,
    after_id: int,
    limit: int,
    initial: bool,
) -> tuple[list[dict], int]:
    query = _messenger_unread_query(user_id)
    limit = max(1, int(limit))
    rows: list[InternalChatMessage]
    total_unread = 0

    if initial:
        rows_desc = query.order_by(InternalChatMessage.id.desc()).limit(limit).all()
        rows = list(reversed(rows_desc))
        total_unread = int(query.count() or 0)
    else:
        query = (
            db.session.query(InternalChatMessage)
            .join(
                InternalChatState,
                InternalChatState.thread_id == InternalChatMessage.thread_id,
            )
            .filter(
                InternalChatState.user_id == int(user_id),
                InternalChatState.is_hidden.is_(False),
                InternalChatMessage.sender_id != int(user_id),
            )
        )
        rows = (
            query.filter(InternalChatMessage.id > int(after_id))
            .order_by(InternalChatMessage.id.asc())
            .limit(limit)
            .all()
        )

    if not rows:
        return [], int(after_id)

    sender_map = _load_user_map({int(msg.sender_id) for msg in rows})
    items: list[dict] = []
    last_id = int(after_id)

    for msg in rows:
        sender_label = _user_display(sender_map.get(int(msg.sender_id)))
        items.append(
            {
                "id": f"messenger:{int(msg.id)}",
                "kind": "messenger",
                "title": f"Мессенджер: {sender_label}",
                "body": _compact_text(msg.body or "", limit=180),
                "created_at": _format_datetime(msg.created_at),
                "level": "info",
                "url": url_for("InternalMessengerView.list", thread_id=int(msg.thread_id)),
                "action_label": "Перейти",
                "source_label": "Мессенджер",
            }
        )
        last_id = max(last_id, int(msg.id))

    if initial and total_unread > len(rows):
        remaining = int(total_unread - len(rows))
        items.append(
            {
                "id": f"messenger:remaining:{last_id}:{remaining}",
                "kind": "messenger",
                "title": "Мессенджер: есть ещё непрочитанные",
                "body": f"Ещё непрочитанных сообщений: {remaining}.",
                "created_at": _format_datetime(dt.datetime.utcnow()),
                "level": "info",
                "url": url_for("InternalMessengerView.list"),
                "action_label": "Перейти",
                "source_label": "Мессенджер",
            }
        )

    return items, int(last_id)


def _load_communications_banners(
    user_id: int,
    *,
    after_id: int,
    limit: int,
    initial: bool,
) -> tuple[list[dict], int]:
    query = _communications_unread_query(user_id)
    limit = max(1, int(limit))
    rows: list[UserMessage]
    total_unread = 0

    if initial:
        rows_desc = query.order_by(UserMessage.id.desc()).limit(limit).all()
        rows = list(reversed(rows_desc))
        total_unread = int(query.count() or 0)
    else:
        query = db.session.query(UserMessage).filter(
            UserMessage.recipient_id == int(user_id),
            UserMessage.deleted_by_recipient.is_(False),
        )
        rows = (
            query.filter(UserMessage.id > int(after_id))
            .order_by(UserMessage.id.asc())
            .limit(limit)
            .all()
        )

    if not rows:
        return [], int(after_id)

    sender_map = _load_user_map({int(msg.sender_id) for msg in rows})
    items: list[dict] = []
    last_id = int(after_id)

    for msg in rows:
        sender_label = _user_display(sender_map.get(int(msg.sender_id)))
        subject = (msg.subject or "").strip() or "Без темы"
        preview = _compact_text(msg.body or "", limit=170)
        items.append(
            {
                "id": f"communications:{int(msg.id)}",
                "kind": "communications",
                "title": f"Центр коммуникаций: {sender_label}",
                "body": _compact_text(f"{subject}. {preview}", limit=220),
                "created_at": _format_datetime(msg.created_at),
                "level": "info",
                "url": url_for("CommunicationCenterView.view", message_id=int(msg.id), box="inbox"),
                "action_label": "Перейти",
                "source_label": "Центр коммуникаций",
            }
        )
        last_id = max(last_id, int(msg.id))

    if initial and total_unread > len(rows):
        remaining = int(total_unread - len(rows))
        items.append(
            {
                "id": f"communications:remaining:{last_id}:{remaining}",
                "kind": "communications",
                "title": "Центр коммуникаций: есть ещё непрочитанные",
                "body": f"Ещё непрочитанных сообщений: {remaining}.",
                "created_at": _format_datetime(dt.datetime.utcnow()),
                "level": "info",
                "url": url_for("CommunicationCenterView.list", box="inbox"),
                "action_label": "Перейти",
                "source_label": "Центр коммуникаций",
            }
        )

    return items, int(last_id)


class SystemNotifyCenterView(BaseView):
    route_base = "/notify-center"
    base_permissions = ["can_list"]

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        username = (current_user.username or "").strip()
        user_id = int(current_user.id)
        state = _get_or_create_notification_state(user_id, username)
        cleared_before_id = _history_cutoff_audit_id(state)
        history = _load_system_history(
            username,
            limit=_SYSTEM_HISTORY_LIMIT,
            cleared_before_audit_id=cleared_before_id,
        )
        latest_visible_id = int(history[0]["id"]) if history else int(cleared_before_id)
        current_read_id = _effective_system_read_audit_id(state)

        if latest_visible_id > current_read_id:
            state.system_last_read_audit_id = latest_visible_id
            state.updated_at = dt.datetime.utcnow()
            db.session.commit()

        g.system_unread_count = 0
        session["notify_hub_system_seen_id"] = max(latest_visible_id, cleared_before_id)
        session.modified = True

        return self.render_template(
            "notify_center.html",
            unread_count=0,
            history=history,
            clear_url=url_for("SystemNotifyCenterView.clear_system_history"),
        )

    @expose("/poll")
    @has_access_api
    @permission_name("list")
    def poll(self):
        username = (current_user.username or "").strip()
        user_id = int(current_user.id)
        state = _get_or_create_notification_state(user_id, username)
        cleared_before_id = _history_cutoff_audit_id(state)
        effective_read_id = _effective_system_read_audit_id(state)

        system_unread = _count_system_unread(
            username,
            last_read_audit_id=effective_read_id,
            cleared_before_audit_id=cleared_before_id,
        )
        messenger_unread = count_messenger_unread(user_id)
        communications_unread = count_communications_unread(user_id)
        system_recent = _load_system_history(
            username,
            limit=_POLL_SYSTEM_HISTORY_LIMIT,
            cleared_before_audit_id=cleared_before_id,
        )

        first_poll = "notify_hub_initialized" not in session
        seen_system_id_default = (
            int(effective_read_id)
            if first_poll
            else (int(system_recent[0]["id"]) if system_recent else int(cleared_before_id))
        )
        seen_system_id = max(
            _parse_int(session.get("notify_hub_system_seen_id"), seen_system_id_default),
            int(cleared_before_id),
        )
        seen_messenger_message_id = _parse_int(
            session.get("notify_hub_seen_messenger_message_id"),
            0,
        )
        seen_communications_message_id = _parse_int(
            session.get("notify_hub_seen_communications_message_id"),
            0,
        )

        banners: list[dict] = []

        if first_poll:
            messenger_banners, last_messenger_message_id = _load_messenger_banners(
                user_id,
                after_id=0,
                limit=_POLL_INITIAL_MESSAGE_BANNERS_LIMIT,
                initial=True,
            )
            communications_banners, last_communications_message_id = _load_communications_banners(
                user_id,
                after_id=0,
                limit=_POLL_INITIAL_MESSAGE_BANNERS_LIMIT,
                initial=True,
            )
            system_banners, last_system_banner_id = _load_system_banners_after(
                username,
                after_id=seen_system_id,
                limit=_POLL_SYSTEM_BANNERS_LIMIT,
                cleared_before_audit_id=cleared_before_id,
            )
        else:
            messenger_banners, last_messenger_message_id = _load_messenger_banners(
                user_id,
                after_id=seen_messenger_message_id,
                limit=_POLL_MESSAGE_BANNERS_LIMIT,
                initial=False,
            )
            communications_banners, last_communications_message_id = _load_communications_banners(
                user_id,
                after_id=seen_communications_message_id,
                limit=_POLL_MESSAGE_BANNERS_LIMIT,
                initial=False,
            )
            system_banners, last_system_banner_id = _load_system_banners_after(
                username,
                after_id=seen_system_id,
                limit=_POLL_SYSTEM_BANNERS_LIMIT,
                cleared_before_audit_id=cleared_before_id,
            )

        banners.extend(messenger_banners)
        banners.extend(communications_banners)
        banners.extend(system_banners)

        if first_poll:
            next_messenger_seen_message_id = max(
                int(last_messenger_message_id or 0),
                _latest_messenger_received_message_id(user_id),
            )
            next_communications_seen_message_id = max(
                int(last_communications_message_id or 0),
                _latest_communications_received_message_id(user_id),
            )
        else:
            next_messenger_seen_message_id = int(last_messenger_message_id or 0)
            next_communications_seen_message_id = int(last_communications_message_id or 0)

        session["notify_hub_seen_messenger_message_id"] = int(next_messenger_seen_message_id)
        session["notify_hub_seen_communications_message_id"] = int(next_communications_seen_message_id)
        session["notify_hub_system_seen_id"] = int(
            max(
                int(last_system_banner_id or 0),
                int(seen_system_id or 0),
                int(cleared_before_id or 0),
            )
        )

        session["notify_hub_prev_messenger_unread"] = int(messenger_unread)
        session["notify_hub_prev_communications_unread"] = int(communications_unread)
        session["notify_hub_initialized"] = True
        session.modified = True

        return jsonify(
            {
                "ok": True,
                "counters": {
                    "system": int(system_unread),
                    "messenger": int(messenger_unread),
                    "communications": int(communications_unread),
                },
                "system_recent": system_recent,
                "banners": banners,
            }
        )

    @expose("/system/read", methods=["POST"])
    @has_access_api
    @permission_name("list")
    def mark_system_read(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            return jsonify({"ok": False, "message": _CSRF_FAILURE_MESSAGE}), 400

        username = (current_user.username or "").strip()
        user_id = int(current_user.id)
        state = _get_or_create_notification_state(user_id, username)
        cleared_before_id = _history_cutoff_audit_id(state)
        latest_id = _get_latest_system_audit_id(username, after_id=cleared_before_id)
        read_id = max(int(latest_id), int(cleared_before_id))
        state.system_last_read_audit_id = int(read_id)
        state.updated_at = dt.datetime.utcnow()
        db.session.commit()

        session["notify_hub_system_seen_id"] = int(read_id)
        session.modified = True
        return jsonify({"ok": True, "system_unread": 0})

    @expose("/system/clear", methods=["POST"])
    @has_access_api
    @permission_name("list")
    def clear_system_history(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            return jsonify({"ok": False, "message": _CSRF_FAILURE_MESSAGE}), 400

        username = (current_user.username or "").strip()
        user_id = int(current_user.id)
        state = _get_or_create_notification_state(user_id, username)
        latest_id = _get_latest_system_audit_id(username)
        state.system_history_cleared_before_audit_id = int(latest_id)
        state.system_last_read_audit_id = max(
            int(state.system_last_read_audit_id or 0),
            int(latest_id),
        )
        state.updated_at = dt.datetime.utcnow()
        db.session.commit()

        session["notify_hub_system_seen_id"] = int(latest_id)
        session["notify_hub_initialized"] = True
        session.modified = True
        return jsonify({"ok": True, "system_unread": 0, "system_recent": []})
