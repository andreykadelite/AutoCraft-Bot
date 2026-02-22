from __future__ import annotations

import datetime as dt

from sqlalchemy import Index, UniqueConstraint, inspect, text

from ..db import db

_INTERNAL_MESSENGER_SCHEMA_READY = False


class InternalChatThread(db.Model):
    __tablename__ = "internal_chat_threads"

    id = db.Column(db.Integer, primary_key=True)
    user_low_id = db.Column(db.Integer, nullable=False, index=True)
    user_high_id = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=dt.datetime.utcnow,
        onupdate=dt.datetime.utcnow,
        nullable=False,
    )
    last_message_at = db.Column(db.DateTime, nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_internal_chat_thread_users"),
        Index("ix_internal_chat_thread_pair", "user_low_id", "user_high_id"),
    )


class InternalChatState(db.Model):
    __tablename__ = "internal_chat_states"

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=True, index=True)
    last_read_at = db.Column(db.DateTime, nullable=True)
    is_hidden = db.Column(db.Boolean, default=False, nullable=False, index=True)
    updated_at = db.Column(
        db.DateTime,
        default=dt.datetime.utcnow,
        onupdate=dt.datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_internal_chat_state_user"),
        Index("ix_internal_chat_state_user_hidden", "user_id", "is_hidden"),
    )


class InternalChatMessage(db.Model):
    __tablename__ = "internal_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, nullable=False, index=True)
    sender_id = db.Column(db.Integer, nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_internal_chat_message_thread_created", "thread_id", "created_at"),
    )


def ensure_internal_messenger_schema() -> None:
    global _INTERNAL_MESSENGER_SCHEMA_READY
    if _INTERNAL_MESSENGER_SCHEMA_READY:
        return

    try:
        engine = db.engine
        InternalChatThread.__table__.create(bind=engine, checkfirst=True)
        InternalChatState.__table__.create(bind=engine, checkfirst=True)
        InternalChatMessage.__table__.create(bind=engine, checkfirst=True)

        inspector = inspect(engine)
        table_columns = {
            InternalChatThread.__tablename__: {col["name"] for col in inspector.get_columns(InternalChatThread.__tablename__)},
            InternalChatState.__tablename__: {col["name"] for col in inspector.get_columns(InternalChatState.__tablename__)},
            InternalChatMessage.__tablename__: {col["name"] for col in inspector.get_columns(InternalChatMessage.__tablename__)},
        }
        required_columns = {
            InternalChatThread.__tablename__: {
                "last_message_at": "DATETIME",
            },
            InternalChatState.__tablename__: {
                "last_read_message_id": "INTEGER",
                "last_read_at": "DATETIME",
                "is_hidden": "BOOLEAN NOT NULL DEFAULT 0",
                "updated_at": "DATETIME",
            },
            InternalChatMessage.__tablename__: {
                "created_at": "DATETIME",
            },
        }

        with engine.begin() as conn:
            for table_name, columns in required_columns.items():
                existing = table_columns.get(table_name, set())
                for column_name, column_type in columns.items():
                    if column_name in existing:
                        continue
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))

            conn.execute(
                text(
                    f"""
                    UPDATE {InternalChatState.__tablename__}
                    SET is_hidden = 0
                    WHERE is_hidden IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {InternalChatState.__tablename__}
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE updated_at IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {InternalChatMessage.__tablename__}
                    SET created_at = CURRENT_TIMESTAMP
                    WHERE created_at IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {InternalChatThread.__tablename__}
                    SET last_message_at = (
                        SELECT MAX(m.created_at)
                        FROM {InternalChatMessage.__tablename__} m
                        WHERE m.thread_id = {InternalChatThread.__tablename__}.id
                    )
                    WHERE last_message_at IS NULL
                    """
                )
            )

        _INTERNAL_MESSENGER_SCHEMA_READY = True
    except Exception:
        _INTERNAL_MESSENGER_SCHEMA_READY = False
