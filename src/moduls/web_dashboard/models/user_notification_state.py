from __future__ import annotations

import datetime as dt

from sqlalchemy import inspect, text

from ..db import db

_NOTIFY_STATE_SCHEMA_READY = False


class UserNotificationState(db.Model):
    __tablename__ = "user_notification_states"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    system_last_read_audit_id = db.Column(db.Integer, nullable=False, default=0)
    system_history_cleared_before_audit_id = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )
    updated_at = db.Column(
        db.DateTime,
        default=dt.datetime.utcnow,
        onupdate=dt.datetime.utcnow,
        nullable=False,
    )


def ensure_user_notification_state_schema() -> None:
    global _NOTIFY_STATE_SCHEMA_READY
    if _NOTIFY_STATE_SCHEMA_READY:
        return

    try:
        engine = db.engine
        UserNotificationState.__table__.create(bind=engine, checkfirst=True)
        inspector = inspect(engine)
        columns = {
            col["name"] for col in inspector.get_columns(UserNotificationState.__tablename__)
        }
        required = {
            "system_last_read_audit_id": "INTEGER NOT NULL DEFAULT 0",
            "system_history_cleared_before_audit_id": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "DATETIME",
        }

        with engine.begin() as conn:
            for column_name, column_type in required.items():
                if column_name in columns:
                    continue
                conn.execute(
                    text(
                        f"ALTER TABLE {UserNotificationState.__tablename__} "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
            conn.execute(
                text(
                    f"""
                    UPDATE {UserNotificationState.__tablename__}
                    SET system_last_read_audit_id = 0
                    WHERE system_last_read_audit_id IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {UserNotificationState.__tablename__}
                    SET system_history_cleared_before_audit_id = 0
                    WHERE system_history_cleared_before_audit_id IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {UserNotificationState.__tablename__}
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE updated_at IS NULL
                    """
                )
            )

        _NOTIFY_STATE_SCHEMA_READY = True
    except Exception:
        _NOTIFY_STATE_SCHEMA_READY = False
