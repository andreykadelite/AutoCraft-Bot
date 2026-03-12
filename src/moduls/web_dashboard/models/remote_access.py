from __future__ import annotations

import datetime as dt

from sqlalchemy import inspect, text

from ..db import db

_REMOTE_ACCESS_SCHEMA_READY = False


def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class RemoteControlPolicy(db.Model):
    __tablename__ = "panel_remote_control_policy"

    id = db.Column(db.Integer, primary_key=True)
    controller_node_id = db.Column(db.String(128), nullable=False, unique=True, index=True)
    controller_name = db.Column(db.String(255), default="", nullable=False)
    controller_ip = db.Column(db.String(64), default="", nullable=False)
    decision = db.Column(db.String(16), default="prompt", nullable=False)  # allow|deny|prompt
    remember = db.Column(db.Boolean, default=False, nullable=False)
    updated_by = db.Column(db.String(128), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class RemoteControlRequest(db.Model):
    __tablename__ = "panel_remote_control_requests"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    controller_node_id = db.Column(db.String(128), nullable=False, index=True)
    controller_name = db.Column(db.String(255), default="", nullable=False)
    controller_ip = db.Column(db.String(64), default="", nullable=False)
    controller_panel_url = db.Column(db.String(512), default="", nullable=False)
    requester_user = db.Column(db.String(128), default="", nullable=False)
    requested_roles = db.Column(db.Text, default="", nullable=False)
    status = db.Column(db.String(16), default="pending", nullable=False, index=True)  # pending|approved|denied|expired|cancelled
    remember_decision = db.Column(db.Boolean, default=False, nullable=False)
    response_note = db.Column(db.String(512), default="", nullable=False)
    grant_token = db.Column(db.String(96), default="", nullable=False, index=True)
    grant_expires_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    responded_at = db.Column(db.DateTime, nullable=True)


def ensure_remote_access_schema() -> None:
    global _REMOTE_ACCESS_SCHEMA_READY
    if _REMOTE_ACCESS_SCHEMA_READY:
        return

    try:
        engine = db.engine
        RemoteControlPolicy.__table__.create(bind=engine, checkfirst=True)
        RemoteControlRequest.__table__.create(bind=engine, checkfirst=True)
        inspector = inspect(engine)

        policy_columns = {col["name"] for col in inspector.get_columns(RemoteControlPolicy.__tablename__)}
        request_columns = {col["name"] for col in inspector.get_columns(RemoteControlRequest.__tablename__)}

        policy_required = {
            "controller_name": "VARCHAR(255) NOT NULL DEFAULT ''",
            "controller_ip": "VARCHAR(64) NOT NULL DEFAULT ''",
            "decision": "VARCHAR(16) NOT NULL DEFAULT 'prompt'",
            "remember": "BOOLEAN NOT NULL DEFAULT 0",
            "updated_by": "VARCHAR(128) NOT NULL DEFAULT ''",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        }
        request_required = {
            "controller_panel_url": "VARCHAR(512) NOT NULL DEFAULT ''",
            "requester_user": "VARCHAR(128) NOT NULL DEFAULT ''",
            "requested_roles": "TEXT NOT NULL DEFAULT ''",
            "remember_decision": "BOOLEAN NOT NULL DEFAULT 0",
            "response_note": "VARCHAR(512) NOT NULL DEFAULT ''",
            "grant_token": "VARCHAR(96) NOT NULL DEFAULT ''",
            "grant_expires_at": "DATETIME",
            "expires_at": "DATETIME",
            "updated_at": "DATETIME",
            "responded_at": "DATETIME",
        }

        with engine.begin() as conn:
            for column_name, column_type in policy_required.items():
                if column_name in policy_columns:
                    continue
                conn.execute(
                    text(
                        f"ALTER TABLE {RemoteControlPolicy.__tablename__} "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

            for column_name, column_type in request_required.items():
                if column_name in request_columns:
                    continue
                conn.execute(
                    text(
                        f"ALTER TABLE {RemoteControlRequest.__tablename__} "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlPolicy.__tablename__}
                    SET decision = 'prompt'
                    WHERE decision IS NULL OR decision = ''
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlPolicy.__tablename__}
                    SET remember = 0
                    WHERE remember IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET status = 'pending'
                    WHERE status IS NULL OR status = ''
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET remember_decision = 0
                    WHERE remember_decision IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET grant_token = ''
                    WHERE grant_token IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET response_note = ''
                    WHERE response_note IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET controller_panel_url = ''
                    WHERE controller_panel_url IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET requester_user = ''
                    WHERE requester_user IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET requested_roles = ''
                    WHERE requested_roles IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlPolicy.__tablename__}
                    SET created_at = CURRENT_TIMESTAMP
                    WHERE created_at IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlPolicy.__tablename__}
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE updated_at IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET created_at = CURRENT_TIMESTAMP
                    WHERE created_at IS NULL
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    UPDATE {RemoteControlRequest.__tablename__}
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE updated_at IS NULL
                    """
                )
            )

        _REMOTE_ACCESS_SCHEMA_READY = True
    except Exception:
        _REMOTE_ACCESS_SCHEMA_READY = False

