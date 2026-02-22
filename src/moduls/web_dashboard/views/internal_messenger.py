from __future__ import annotations

import datetime as dt
from typing import Iterable, List

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_appbuilder.security.sqla.models import Role, User
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy import func, or_

from ..db import db
from ..models.audit import AuditLog
from ..models.internal_messenger import (
    InternalChatMessage,
    InternalChatState,
    InternalChatThread,
    ensure_internal_messenger_schema,
)
from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api

_CSRF_FAILURE_MESSAGE = (
    "Сессия проверки безопасности устарела. Обновите страницу и повторите действие."
)
_MAX_MESSAGE_LEN = 4000
_MAX_RENDER_MESSAGES = 500


def _ensure_messenger_schema() -> None:
    ensure_internal_messenger_schema()


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


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_thread_ids(form_data) -> list[int]:
    raw_values = form_data.getlist("thread_id")
    if not raw_values:
        csv_values = (form_data.get("thread_ids") or "").strip()
        if csv_values:
            raw_values = [item.strip() for item in csv_values.split(",")]

    thread_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        thread_id = _parse_int(raw, 0)
        if thread_id > 0 and thread_id not in seen:
            seen.add(thread_id)
            thread_ids.append(thread_id)
    return thread_ids


def _compact_text(text: str, limit: int = 180) -> str:
    cleaned = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def _pair_ids(first_id: int, second_id: int) -> tuple[int, int]:
    if first_id <= second_id:
        return first_id, second_id
    return second_id, first_id


def _thread_other_user_id(thread: InternalChatThread, user_id: int) -> int | None:
    if thread.user_low_id == user_id:
        return thread.user_high_id
    if thread.user_high_id == user_id:
        return thread.user_low_id
    return None


def _user_display(user: User | None) -> str:
    if not user:
        return "Неизвестный пользователь"
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    base = " ".join([part for part in (first, last) if part]).strip()
    if user.username:
        if base:
            return f"{base} ({user.username})"
        return user.username
    return base or f"ID {user.id}"


def _roles_display(user: User | None) -> str:
    if not user or not getattr(user, "roles", None):
        return ""
    names = sorted({role.name for role in user.roles if role and role.name})
    return ", ".join(names)


def _load_user_map(ids: Iterable[int]) -> dict[int, User]:
    unique_ids = {item for item in ids if item}
    if not unique_ids:
        return {}
    users = db.session.query(User).filter(User.id.in_(unique_ids)).all()
    return {user.id: user for user in users}


def _ensure_chat_state(thread_id: int, user_id: int) -> InternalChatState:
    _ensure_messenger_schema()
    state = (
        db.session.query(InternalChatState)
        .filter(
            InternalChatState.thread_id == thread_id,
            InternalChatState.user_id == user_id,
        )
        .first()
    )
    if state:
        return state
    state = InternalChatState(thread_id=thread_id, user_id=user_id, is_hidden=False)
    db.session.add(state)
    db.session.flush()
    return state


def _get_or_create_thread(current_user_id: int, other_user_id: int) -> tuple[InternalChatThread, InternalChatState, InternalChatState]:
    _ensure_messenger_schema()
    low_id, high_id = _pair_ids(current_user_id, other_user_id)
    thread = (
        db.session.query(InternalChatThread)
        .filter(
            InternalChatThread.user_low_id == low_id,
            InternalChatThread.user_high_id == high_id,
        )
        .first()
    )
    if not thread:
        thread = InternalChatThread(
            user_low_id=low_id,
            user_high_id=high_id,
            created_at=dt.datetime.utcnow(),
            updated_at=dt.datetime.utcnow(),
            last_message_at=None,
        )
        db.session.add(thread)
        db.session.flush()

    current_state = _ensure_chat_state(thread.id, current_user_id)
    other_state = _ensure_chat_state(thread.id, other_user_id)
    return thread, current_state, other_state


def _load_thread_for_user(thread_id: int, user_id: int) -> tuple[InternalChatThread, InternalChatState, int] | None:
    _ensure_messenger_schema()
    thread = db.session.query(InternalChatThread).filter(InternalChatThread.id == thread_id).first()
    if not thread:
        return None
    other_user_id = _thread_other_user_id(thread, user_id)
    if not other_user_id:
        return None
    state = _ensure_chat_state(thread.id, user_id)
    return thread, state, other_user_id


def _readable_message_count_for_thread(thread_id: int, user_id: int, last_read_message_id: int | None) -> int:
    query = db.session.query(func.count(InternalChatMessage.id)).filter(
        InternalChatMessage.thread_id == thread_id,
        InternalChatMessage.sender_id != user_id,
    )
    if last_read_message_id:
        query = query.filter(InternalChatMessage.id > last_read_message_id)
    return int(query.scalar() or 0)


def _collect_chat_list(current_user_id: int) -> list[dict]:
    _ensure_messenger_schema()
    states = (
        db.session.query(InternalChatState)
        .filter(
            InternalChatState.user_id == current_user_id,
            InternalChatState.is_hidden.is_(False),
        )
        .all()
    )
    if not states:
        return []

    state_map = {state.thread_id: state for state in states}
    thread_ids = list(state_map.keys())
    threads = (
        db.session.query(InternalChatThread)
        .filter(InternalChatThread.id.in_(thread_ids))
        .all()
    )
    thread_map = {thread.id: thread for thread in threads}

    last_message_rows = (
        db.session.query(InternalChatMessage.thread_id, func.max(InternalChatMessage.id).label("last_id"))
        .filter(InternalChatMessage.thread_id.in_(thread_ids))
        .group_by(InternalChatMessage.thread_id)
        .all()
    )
    last_id_map = {int(row.thread_id): int(row.last_id) for row in last_message_rows if row.last_id}
    last_messages = {}
    if last_id_map:
        messages = db.session.query(InternalChatMessage).filter(InternalChatMessage.id.in_(list(last_id_map.values()))).all()
        last_messages = {msg.thread_id: msg for msg in messages}

    user_ids: set[int] = set()
    for thread in threads:
        other_id = _thread_other_user_id(thread, current_user_id)
        if other_id:
            user_ids.add(other_id)
    user_map = _load_user_map(user_ids)

    items: list[dict] = []
    for thread_id, state in state_map.items():
        thread = thread_map.get(thread_id)
        if not thread:
            continue
        other_user_id = _thread_other_user_id(thread, current_user_id)
        if not other_user_id:
            continue
        other_user = user_map.get(other_user_id)
        last_message = last_messages.get(thread_id)
        preview = _compact_text(last_message.body if last_message else "")
        if not preview:
            preview = "Сообщений пока нет"
        last_at_value = (
            (last_message.created_at if last_message else None)
            or thread.last_message_at
            or thread.updated_at
            or thread.created_at
        )
        unread_count = _readable_message_count_for_thread(
            thread_id=thread_id,
            user_id=current_user_id,
            last_read_message_id=state.last_read_message_id,
        )
        items.append(
            {
                "id": thread_id,
                "other_user_id": other_user_id,
                "other_user_label": _user_display(other_user),
                "other_user_roles": _roles_display(other_user),
                "last_preview": preview,
                "last_at": _format_datetime(last_at_value),
                "last_at_value": last_at_value,
                "unread_count": unread_count,
            }
        )

    items.sort(
        key=lambda item: item.get("last_at_value") or dt.datetime.min,
        reverse=True,
    )
    return items


def _sum_unread(chat_items: list[dict]) -> int:
    return sum(int(item.get("unread_count") or 0) for item in chat_items)


def _serialize_chat_items(chat_items: list[dict]) -> list[dict]:
    return [
        {
            "id": int(item.get("id") or 0),
            "other_user_id": int(item.get("other_user_id") or 0),
            "other_user_label": item.get("other_user_label") or "",
            "other_user_roles": item.get("other_user_roles") or "",
            "last_preview": item.get("last_preview") or "",
            "last_at": item.get("last_at") or "",
            "unread_count": int(item.get("unread_count") or 0),
        }
        for item in chat_items
    ]


def _serialize_single_message(
    msg: InternalChatMessage,
    *,
    current_user_id: int,
    other_user_id: int,
) -> dict:
    user_map = _load_user_map({current_user_id, other_user_id})
    sender = user_map.get(msg.sender_id)
    is_mine = msg.sender_id == current_user_id
    return {
        "id": int(msg.id),
        "is_mine": is_mine,
        "sender_label": "Вы" if is_mine else _user_display(sender),
        "body": msg.body or "",
        "created_at": _format_datetime(msg.created_at),
    }


def _load_thread_messages(
    thread_id: int,
    current_user_id: int,
    other_user_id: int,
    *,
    since_id: int = 0,
    limit: int = _MAX_RENDER_MESSAGES,
) -> tuple[list[dict], int]:
    base_query = db.session.query(InternalChatMessage).filter(
        InternalChatMessage.thread_id == thread_id
    )
    if since_id > 0:
        query = (
            base_query.filter(InternalChatMessage.id > since_id)
            .order_by(InternalChatMessage.id.asc())
        )
        messages = query.limit(limit).all()
    else:
        # For first load we show the latest records, not the oldest ones.
        messages_desc = (
            base_query.order_by(InternalChatMessage.id.desc()).limit(limit).all()
        )
        messages = list(reversed(messages_desc))
    user_map = _load_user_map({current_user_id, other_user_id})
    payload: list[dict] = []
    last_message_id = since_id
    for msg in messages:
        sender = user_map.get(msg.sender_id)
        is_mine = msg.sender_id == current_user_id
        payload.append(
            {
                "id": int(msg.id),
                "is_mine": is_mine,
                "sender_label": "Вы" if is_mine else _user_display(sender),
                "body": msg.body or "",
                "created_at": _format_datetime(msg.created_at),
            }
        )
        if msg.id > last_message_id:
            last_message_id = int(msg.id)
    if since_id <= 0:
        latest_id = (
            db.session.query(func.max(InternalChatMessage.id))
            .filter(InternalChatMessage.thread_id == thread_id)
            .scalar()
        )
        if latest_id:
            last_message_id = int(latest_id)
    return payload, int(last_message_id or 0)


def _append_audit_log(action: str, target: str, result: str, details: str = "") -> None:
    try:
        log = AuditLog(
            user=current_user.username,
            action=action,
            target=target,
            result=result,
            source="web",
            ip=request.remote_addr or "",
            details=details,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


class InternalMessengerView(BaseView):
    route_base = "/messenger"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        selected_thread_id = _parse_int(request.args.get("thread_id"), 0)
        chat_items = _collect_chat_list(current_user.id)
        unread_total = _sum_unread(chat_items)

        selected_chat = None
        if selected_thread_id:
            selected_chat = next((item for item in chat_items if item["id"] == selected_thread_id), None)
        elif chat_items:
            selected_chat = chat_items[0]
            selected_thread_id = int(selected_chat["id"])

        active_messages: List[dict] = []
        active_user_label = ""
        active_user_roles = ""
        active_thread_id = selected_thread_id if selected_chat else 0
        active_last_message_id = 0
        active_truncated = False

        if selected_chat:
            loaded = _load_thread_for_user(selected_thread_id, current_user.id)
            if not loaded:
                flash("Чат не найден или недоступен.", "warning")
                return redirect(url_for(f"{self.__class__.__name__}.list"))
            _thread, state, other_user_id = loaded

            state_was_hidden = bool(state.is_hidden)
            state.is_hidden = False
            messages_desc = (
                db.session.query(InternalChatMessage)
                .filter(InternalChatMessage.thread_id == selected_thread_id)
                .order_by(InternalChatMessage.id.desc())
                .limit(_MAX_RENDER_MESSAGES)
                .all()
            )
            messages = list(reversed(messages_desc))
            active_truncated = len(messages_desc) >= _MAX_RENDER_MESSAGES
            if messages:
                active_last_message_id = int(messages[-1].id)
            user_map = _load_user_map({current_user.id, other_user_id})
            other_user = user_map.get(other_user_id)
            active_user_label = _user_display(other_user)
            active_user_roles = _roles_display(other_user)

            for msg in messages:
                sender = user_map.get(msg.sender_id)
                is_mine = msg.sender_id == current_user.id
                active_messages.append(
                    {
                        "id": msg.id,
                        "is_mine": is_mine,
                        "sender_label": "Вы" if is_mine else _user_display(sender),
                        "body": msg.body or "",
                        "created_at": _format_datetime(msg.created_at),
                    }
                )

            should_commit = state_was_hidden
            if messages:
                last_message_id = int(messages[-1].id)
                if not state.last_read_message_id or state.last_read_message_id < last_message_id:
                    state.last_read_message_id = last_message_id
                    state.last_read_at = dt.datetime.utcnow()
                    should_commit = True
            if should_commit:
                db.session.commit()

        can_write = self.appbuilder.sm.has_access("can_action", self.class_permission_name)
        return self.render_template(
            "internal_messenger.html",
            chats=chat_items,
            unread_total=unread_total,
            selected_thread_id=active_thread_id,
            active_messages=active_messages,
            active_user_label=active_user_label,
            active_user_roles=active_user_roles,
            active_last_message_id=active_last_message_id,
            active_truncated=active_truncated,
            can_write=can_write,
        )

    @expose("/start", methods=["POST"])
    @has_access
    @permission_name("action")
    def start(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        recipient_id = _parse_int(request.form.get("recipient_id"), 0)
        if not recipient_id:
            flash("Выберите пользователя для начала чата.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if recipient_id == current_user.id:
            flash("Нельзя создать чат с самим собой.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        recipient = (
            db.session.query(User)
            .filter(User.id == recipient_id, User.active.is_(True))
            .first()
        )
        if not recipient:
            flash("Пользователь не найден или отключен.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        thread, current_state, other_state = _get_or_create_thread(current_user.id, recipient.id)
        current_state.is_hidden = False
        other_state.is_hidden = False
        thread.updated_at = dt.datetime.utcnow()
        db.session.commit()

        _append_audit_log(
            action="messenger_start_chat",
            target=recipient.username or str(recipient.id),
            result="ok",
            details=f"thread={thread.id}",
        )
        return redirect(url_for(f"{self.__class__.__name__}.list", thread_id=thread.id))

    @expose("/start-live", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def start_live(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            return jsonify({"ok": False, "message": _CSRF_FAILURE_MESSAGE}), 400

        recipient_id = _parse_int(request.form.get("recipient_id"), 0)
        if not recipient_id:
            return jsonify({"ok": False, "message": "Выберите пользователя для начала чата."}), 400
        if recipient_id == current_user.id:
            return jsonify({"ok": False, "message": "Нельзя создать чат с самим собой."}), 400

        recipient = (
            db.session.query(User)
            .filter(User.id == recipient_id, User.active.is_(True))
            .first()
        )
        if not recipient:
            return jsonify({"ok": False, "message": "Пользователь не найден или отключен."}), 404

        thread, current_state, other_state = _get_or_create_thread(current_user.id, recipient.id)
        current_state.is_hidden = False
        other_state.is_hidden = False
        thread.updated_at = dt.datetime.utcnow()
        db.session.commit()

        messages, last_message_id = _load_thread_messages(
            thread_id=thread.id,
            current_user_id=current_user.id,
            other_user_id=recipient.id,
            since_id=0,
            limit=_MAX_RENDER_MESSAGES,
        )
        if last_message_id and (
            not current_state.last_read_message_id or current_state.last_read_message_id < last_message_id
        ):
            current_state.last_read_message_id = int(last_message_id)
            current_state.last_read_at = dt.datetime.utcnow()
            db.session.commit()

        _append_audit_log(
            action="messenger_start_chat",
            target=recipient.username or str(recipient.id),
            result="ok",
            details=f"thread={thread.id};mode=live",
        )

        chat_items = _collect_chat_list(current_user.id)
        return jsonify(
            {
                "ok": True,
                "thread_id": int(thread.id),
                "messages": messages,
                "last_message_id": int(last_message_id or 0),
                "active_user_label": _user_display(recipient),
                "active_user_roles": _roles_display(recipient),
                "unread_total": _sum_unread(chat_items),
                "chats": _serialize_chat_items(chat_items),
            }
        )

    @expose("/send", methods=["POST"])
    @has_access
    @permission_name("action")
    def send(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        thread_id = _parse_int(request.form.get("thread_id"), 0)
        body = (request.form.get("body") or "").strip()
        if not thread_id:
            flash("Не выбран чат для отправки сообщения.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if not body:
            flash("Введите текст сообщения.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list", thread_id=thread_id))
        if len(body) > _MAX_MESSAGE_LEN:
            flash(f"Сообщение слишком длинное (максимум {_MAX_MESSAGE_LEN} символов).", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list", thread_id=thread_id))

        loaded = _load_thread_for_user(thread_id, current_user.id)
        if not loaded:
            abort(404)
        thread, current_state, other_user_id = loaded
        other_state = _ensure_chat_state(thread.id, other_user_id)

        now = dt.datetime.utcnow()
        message = InternalChatMessage(
            thread_id=thread.id,
            sender_id=current_user.id,
            body=body,
            created_at=now,
        )
        db.session.add(message)
        db.session.flush()

        thread.last_message_at = now
        thread.updated_at = now
        current_state.last_read_message_id = message.id
        current_state.last_read_at = now
        current_state.is_hidden = False
        other_state.is_hidden = False
        db.session.commit()

        _append_audit_log(
            action="messenger_send",
            target=str(other_user_id),
            result="ok",
            details=f"thread={thread.id};message={message.id}",
        )
        return redirect(url_for(f"{self.__class__.__name__}.list", thread_id=thread.id))

    @expose("/send-live", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def send_live(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            return jsonify({"ok": False, "message": _CSRF_FAILURE_MESSAGE}), 400

        thread_id = _parse_int(request.form.get("thread_id"), 0)
        body = (request.form.get("body") or "").strip()
        if not thread_id:
            return jsonify({"ok": False, "message": "Не выбран чат для отправки сообщения."}), 400
        if not body:
            return jsonify({"ok": False, "message": "Введите текст сообщения."}), 400
        if len(body) > _MAX_MESSAGE_LEN:
            return (
                jsonify(
                    {
                        "ok": False,
                        "message": f"Сообщение слишком длинное (максимум {_MAX_MESSAGE_LEN} символов).",
                    }
                ),
                400,
            )

        loaded = _load_thread_for_user(thread_id, current_user.id)
        if not loaded:
            return jsonify({"ok": False, "message": "Чат не найден."}), 404
        thread, current_state, other_user_id = loaded
        other_state = _ensure_chat_state(thread.id, other_user_id)

        now = dt.datetime.utcnow()
        message = InternalChatMessage(
            thread_id=thread.id,
            sender_id=current_user.id,
            body=body,
            created_at=now,
        )
        db.session.add(message)
        db.session.flush()

        thread.last_message_at = now
        thread.updated_at = now
        current_state.last_read_message_id = message.id
        current_state.last_read_at = now
        current_state.is_hidden = False
        other_state.is_hidden = False
        db.session.commit()

        _append_audit_log(
            action="messenger_send",
            target=str(other_user_id),
            result="ok",
            details=f"thread={thread.id};message={message.id};mode=live",
        )

        chat_items = _collect_chat_list(current_user.id)
        return jsonify(
            {
                "ok": True,
                "thread_id": int(thread.id),
                "message": _serialize_single_message(
                    message,
                    current_user_id=current_user.id,
                    other_user_id=other_user_id,
                ),
                "last_message_id": int(message.id),
                "unread_total": _sum_unread(chat_items),
                "chats": _serialize_chat_items(chat_items),
            }
        )

    @expose("/delete", methods=["POST"])
    @has_access
    @permission_name("action")
    def delete(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        thread_ids = _parse_thread_ids(request.form)

        if not thread_ids:
            flash("Выберите хотя бы один чат для удаления.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        deleted_count = 0
        for thread_id in thread_ids:
            loaded = _load_thread_for_user(thread_id, current_user.id)
            if not loaded:
                continue
            _thread, state, _other_user_id = loaded
            if not state.is_hidden:
                state.is_hidden = True
                deleted_count += 1

        db.session.commit()
        if deleted_count:
            flash(f"Удалено чатов: {deleted_count}.", "success")
            _append_audit_log(
                action="messenger_delete_chats",
                target=current_user.username or str(current_user.id),
                result="ok",
                details=f"count={deleted_count}",
            )
        else:
            flash("Выбранные чаты не найдены или уже удалены.", "warning")

        return redirect(url_for(f"{self.__class__.__name__}.list"))

    @expose("/delete-live", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def delete_live(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            return jsonify({"ok": False, "message": _CSRF_FAILURE_MESSAGE}), 400

        thread_ids = _parse_thread_ids(request.form)
        if not thread_ids:
            return jsonify({"ok": False, "message": "Выберите хотя бы один чат для удаления."}), 400

        deleted_count = 0
        for thread_id in thread_ids:
            loaded = _load_thread_for_user(thread_id, current_user.id)
            if not loaded:
                continue
            _thread, state, _other_user_id = loaded
            if not state.is_hidden:
                state.is_hidden = True
                deleted_count += 1

        db.session.commit()

        if deleted_count:
            _append_audit_log(
                action="messenger_delete_chats",
                target=current_user.username or str(current_user.id),
                result="ok",
                details=f"count={deleted_count};mode=live",
            )
            message_text = f"Удалено чатов: {deleted_count}."
            ok = True
        else:
            message_text = "Выбранные чаты не найдены или уже удалены."
            ok = False

        chat_items = _collect_chat_list(current_user.id)
        return jsonify(
            {
                "ok": ok,
                "message": message_text,
                "deleted_count": int(deleted_count),
                "unread_total": _sum_unread(chat_items),
                "chats": _serialize_chat_items(chat_items),
            }
        )

    @expose("/users")
    @has_access_api
    @permission_name("list")
    def users(self):
        term = (request.args.get("q") or "").strip()
        show_all = (request.args.get("all") or "").strip().lower() in ("1", "true", "yes", "on")
        limit = _parse_int(request.args.get("limit"), 40)
        if limit <= 0:
            limit = 0
        else:
            limit = max(10, min(limit, 500))

        if not term and not show_all:
            return jsonify({"items": []})

        query = db.session.query(User).filter(
            User.active.is_(True),
            User.id != current_user.id,
        )
        if term:
            like = f"%{term.casefold()}%"
            query = query.outerjoin(User.roles).filter(
                or_(
                    func.lower(User.username).like(like),
                    func.lower(User.first_name).like(like),
                    func.lower(User.last_name).like(like),
                    func.lower(User.email).like(like),
                    func.lower(Role.name).like(like),
                )
            )

        query = query.distinct().order_by(
            func.lower(User.last_name),
            func.lower(User.first_name),
            func.lower(User.username),
        )
        if limit:
            users = query.limit(limit).all()
        else:
            users = query.all()

        items: list[dict] = []
        for user in users:
            label = _user_display(user)
            roles_text = _roles_display(user)
            if roles_text:
                label = f"{label} - {roles_text}"
            items.append(
                {
                    "id": user.id,
                    "label": label,
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "roles": [role.name for role in user.roles] if user.roles else [],
                }
            )

        total = len(items)
        truncated = bool(limit and total >= limit)
        return jsonify({"items": items, "total": total, "truncated": truncated})

    @expose("/notifications")
    @has_access_api
    @permission_name("list")
    def notifications(self):
        chat_items = _collect_chat_list(current_user.id)
        return jsonify({"unread_total": _sum_unread(chat_items)})

    @expose("/live")
    @has_access_api
    @permission_name("list")
    def live(self):
        thread_id = _parse_int(request.args.get("thread_id"), 0)
        since_id = max(0, _parse_int(request.args.get("since_id"), 0))

        chat_items = _collect_chat_list(current_user.id)
        payload = {
            "ok": True,
            "thread_id": 0,
            "last_message_id": since_id,
            "messages": [],
            "unread_total": _sum_unread(chat_items),
            "chats": _serialize_chat_items(chat_items),
        }

        if thread_id <= 0:
            return jsonify(payload)

        loaded = _load_thread_for_user(thread_id, current_user.id)
        if not loaded:
            return jsonify(payload)

        _thread, state, other_user_id = loaded
        commit_needed = False
        if state.is_hidden:
            state.is_hidden = False
            commit_needed = True

        messages, last_message_id = _load_thread_messages(
            thread_id=thread_id,
            current_user_id=current_user.id,
            other_user_id=other_user_id,
            since_id=since_id,
            limit=200,
        )
        payload["thread_id"] = int(thread_id)
        payload["last_message_id"] = int(last_message_id or since_id)
        payload["messages"] = messages

        if last_message_id and (
            not state.last_read_message_id or state.last_read_message_id < last_message_id
        ):
            state.last_read_message_id = int(last_message_id)
            state.last_read_at = dt.datetime.utcnow()
            commit_needed = True

        if commit_needed:
            db.session.commit()
            chat_items = _collect_chat_list(current_user.id)
            payload["unread_total"] = _sum_unread(chat_items)
            payload["chats"] = _serialize_chat_items(chat_items)

        return jsonify(payload)

    @expose("/thread/<int:thread_id>/messages")
    @has_access_api
    @permission_name("list")
    def thread_messages(self, thread_id: int):
        loaded = _load_thread_for_user(thread_id, current_user.id)
        if not loaded:
            return jsonify({"message": "Чат не найден."}), 404
        _thread, state, other_user_id = loaded

        since_id = max(0, _parse_int(request.args.get("since_id"), 0))
        payload, last_message_id = _load_thread_messages(
            thread_id=thread_id,
            current_user_id=current_user.id,
            other_user_id=other_user_id,
            since_id=since_id,
            limit=_MAX_RENDER_MESSAGES,
        )

        if last_message_id and (
            not state.last_read_message_id or state.last_read_message_id < last_message_id
        ):
            state.last_read_message_id = int(last_message_id)
            state.last_read_at = dt.datetime.utcnow()
            db.session.commit()

        return jsonify({"items": payload, "thread_id": thread_id, "last_message_id": last_message_id})
