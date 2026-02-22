from __future__ import annotations

from sqlalchemy import func, or_

from .db import db
from .models.internal_messenger import InternalChatMessage, InternalChatState
from .models.messages import UserMessage


def count_communications_unread(user_id: int) -> int:
    unread = (
        db.session.query(func.count(UserMessage.id))
        .filter(
            UserMessage.recipient_id == int(user_id),
            UserMessage.read_at.is_(None),
            UserMessage.deleted_by_recipient.is_(False),
        )
        .scalar()
    )
    return int(unread or 0)


def count_messenger_unread(user_id: int) -> int:
    unread = (
        db.session.query(func.count(InternalChatMessage.id))
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
        .scalar()
    )
    return int(unread or 0)
