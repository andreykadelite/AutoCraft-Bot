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
from ..models.messages import UserMessage
from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from ..unread_counters import count_communications_unread

_CSRF_FAILURE_MESSAGE = (
    "Сессия проверки безопасности устарела. Обновите страницу и повторите действие."
)


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


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def _compact_text(text: str, limit: int = 180) -> str:
    cleaned = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


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


def _roles_display(user: User | None) -> str:
    if not user or not getattr(user, "roles", None):
        return ""
    names = sorted({role.name for role in user.roles if role and role.name})
    return ", ".join(names)


def _message_subject(msg: UserMessage) -> str:
    subject = (msg.subject or "").strip()
    return subject if subject else "Без темы"


def _message_preview(msg: UserMessage) -> str:
    return _compact_text(msg.body or "")


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _load_user_map(ids: Iterable[int]) -> dict[int, User]:
    unique_ids = {item for item in ids if item}
    if not unique_ids:
        return {}
    users = db.session.query(User).filter(User.id.in_(unique_ids)).all()
    return {user.id: user for user in users}


class CommunicationCenterView(BaseView):
    route_base = "/communications"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        box = (request.args.get("box") or "inbox").strip().lower()
        if box not in ("inbox", "sent"):
            box = "inbox"

        page = max(0, _parse_int(request.args.get("page"), 0))
        page_size = _parse_int(request.args.get("page_size"), 20)
        page_size = max(10, min(page_size, 100))

        base_query = db.session.query(UserMessage)
        if box == "inbox":
            base_query = base_query.filter(
                UserMessage.recipient_id == current_user.id,
                UserMessage.deleted_by_recipient.is_(False),
            )
        else:
            base_query = base_query.filter(
                UserMessage.sender_id == current_user.id,
                UserMessage.deleted_by_sender.is_(False),
            )

        total = base_query.count()
        if total and page * page_size >= total:
            page = max(0, (total - 1) // page_size)

        messages = (
            base_query.order_by(UserMessage.created_at.desc())
            .offset(page * page_size)
            .limit(page_size)
            .all()
        )

        lookup_ids: set[int] = set()
        if box == "inbox":
            lookup_ids.update(msg.sender_id for msg in messages)
        else:
            lookup_ids.update(msg.recipient_id for msg in messages)
        user_map = _load_user_map(lookup_ids)

        items: List[dict] = []
        for msg in messages:
            other = user_map.get(msg.sender_id if box == "inbox" else msg.recipient_id)
            items.append(
                {
                    "id": msg.id,
                    "subject": _message_subject(msg),
                    "preview": _message_preview(msg),
                    "created_at": _format_datetime(msg.created_at),
                    "is_unread": box == "inbox" and msg.read_at is None,
                    "other_user": _user_display(other),
                    "other_roles": _roles_display(other),
                }
            )

        inbox_count = (
            db.session.query(UserMessage)
            .filter(
                UserMessage.recipient_id == current_user.id,
                UserMessage.deleted_by_recipient.is_(False),
            )
            .count()
        )
        sent_count = (
            db.session.query(UserMessage)
            .filter(
                UserMessage.sender_id == current_user.id,
                UserMessage.deleted_by_sender.is_(False),
            )
            .count()
        )
        unread_count = count_communications_unread(int(current_user.id))

        prefill_recipient_id = _parse_int(request.args.get("recipient_id"), 0)
        prefill_subject = (request.args.get("subject") or "").strip()
        prefill_body = (request.args.get("body") or "").strip()
        recipient = None
        if prefill_recipient_id:
            recipient = (
                db.session.query(User)
                .filter(User.id == prefill_recipient_id, User.active.is_(True))
                .first()
            )
        recipient_label = _user_display(recipient) if recipient else ""
        can_write = self.appbuilder.sm.has_access("can_action", self.class_permission_name)

        return self.render_template(
            "communication_center.html",
            box=box,
            messages=items,
            page=page,
            page_size=page_size,
            total=total,
            inbox_count=inbox_count,
            sent_count=sent_count,
            unread_count=unread_count,
            recipient_id=prefill_recipient_id or "",
            recipient_label=recipient_label,
            subject=prefill_subject,
            body=prefill_body,
            can_write=can_write,
        )

    @expose("/message/<int:message_id>")
    @has_access
    @permission_name("list")
    def view(self, message_id: int):
        msg = db.session.query(UserMessage).filter(UserMessage.id == message_id).first()
        if not msg:
            abort(404)

        is_sender = msg.sender_id == current_user.id
        is_recipient = msg.recipient_id == current_user.id
        if not (is_sender or is_recipient):
            abort(404)
        if is_sender and msg.deleted_by_sender:
            abort(404)
        if is_recipient and msg.deleted_by_recipient:
            abort(404)

        if is_recipient and msg.read_at is None:
            msg.mark_read()
            db.session.commit()

        box = (request.args.get("box") or ("sent" if is_sender else "inbox")).lower()
        if box not in ("inbox", "sent"):
            box = "inbox" if is_recipient else "sent"

        can_action = self.appbuilder.sm.has_access("can_action", self.__class__.__name__)

        users = _load_user_map([msg.sender_id, msg.recipient_id])
        sender = users.get(msg.sender_id)
        recipient = users.get(msg.recipient_id)

        return self.render_template(
            "communication_message.html",
            message=msg,
            subject=_message_subject(msg),
            sender_label=_user_display(sender),
            recipient_label=_user_display(recipient),
            sender_roles=_roles_display(sender),
            recipient_roles=_roles_display(recipient),
            created_at=_format_datetime(msg.created_at),
            read_at=_format_datetime(msg.read_at),
            box=box,
            can_action=can_action,
        )

    @expose("/send", methods=["POST"])
    @has_access
    @permission_name("action")
    def send(self):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        recipient_id = _parse_int(request.form.get("recipient_id"), 0)
        subject = (request.form.get("subject") or "").strip()
        body = (request.form.get("body") or "").strip()

        if not recipient_id:
            flash("Выберите получателя сообщения.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))
        if not body:
            flash("Введите текст сообщения.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list", recipient_id=recipient_id))
        if len(subject) > 200:
            flash("Тема сообщения слишком длинная (максимум 200 символов).", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list", recipient_id=recipient_id))
        if len(body) > 4000:
            flash("Сообщение слишком длинное (максимум 4000 символов).", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list", recipient_id=recipient_id))

        recipient = (
            db.session.query(User)
            .filter(User.id == recipient_id, User.active.is_(True))
            .first()
        )
        if not recipient:
            flash("Получатель не найден или отключен.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        message = UserMessage(
            sender_id=current_user.id,
            recipient_id=recipient.id,
            subject=subject,
            body=body,
        )
        db.session.add(message)
        db.session.commit()

        try:
            log = AuditLog(
                user=current_user.username,
                action="message_send",
                target=recipient.username,
                result="ok",
                source="web",
                ip=request.remote_addr or "",
                details=f"id={message.id}",
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()

        flash("Сообщение отправлено.", "success")
        return redirect(url_for(f"{self.__class__.__name__}.list", box="sent"))

    @expose("/delete/<int:message_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def delete(self, message_id: int):
        token = _get_csrf_token()
        if not _is_csrf_valid(token):
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        msg = db.session.query(UserMessage).filter(UserMessage.id == message_id).first()
        if not msg:
            flash("Сообщение не найдено.", "warning")
            return redirect(url_for(f"{self.__class__.__name__}.list"))

        is_sender = msg.sender_id == current_user.id
        is_recipient = msg.recipient_id == current_user.id
        if not (is_sender or is_recipient):
            abort(403)

        if is_sender:
            msg.deleted_by_sender = True
        if is_recipient:
            msg.deleted_by_recipient = True

        if msg.deleted_by_sender and msg.deleted_by_recipient:
            db.session.delete(msg)
        db.session.commit()

        flash("Сообщение удалено.", "success")
        box = (request.form.get("box") or "inbox").strip().lower()
        return redirect(url_for(f"{self.__class__.__name__}.list", box=box))

    @expose("/users")
    @has_access_api
    @permission_name("list")
    def users(self):
        term = (request.args.get("q") or "").strip()
        show_all = (request.args.get("all") or "").strip() in ("1", "true", "yes", "on")
        limit = _parse_int(request.args.get("limit"), 40)
        if limit <= 0:
            limit = 0
        else:
            limit = max(10, min(limit, 500))

        if not term and not show_all:
            return jsonify({"items": []})

        query = db.session.query(User).filter(User.active.is_(True))

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

        items: List[dict] = []
        for user in users:
            label = _user_display(user)
            roles = _roles_display(user)
            if roles:
                label = f"{label} — {roles}"
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
        unread_count = count_communications_unread(int(current_user.id))
        return jsonify({"unread_total": int(unread_count or 0)})
