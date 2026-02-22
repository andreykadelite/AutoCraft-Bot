from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, event

from ..db import db

_MAX_AUDIT_FIELD_LEN = 220


def _clip_audit_field(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > _MAX_AUDIT_FIELD_LEN:
        return text[:_MAX_AUDIT_FIELD_LEN] + "...(truncated)"
    return text


def _emit_bot_log(message: str) -> None:
    try:
        import __main__  # type: ignore

        logger = getattr(__main__, "write_bot_log", None)
        if callable(logger):
            logger(message)
    except Exception:
        pass


class AuditLog(db.Model):
    __tablename__ = "panel_audit"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    user = Column(String(128), default="system")
    action = Column(String(128), nullable=False)
    target = Column(String(256), default="")
    result = Column(String(16), default="ok")
    source = Column(String(64), default="web")
    ip = Column(String(64), default="")
    details = Column(Text, default="")

    def __repr__(self) -> str:
        return f"{self.action} ({self.user})"


@event.listens_for(AuditLog, "after_insert")
def _audit_after_insert(_mapper: Any, _connection: Any, target: AuditLog) -> None:
    """Mirror panel audit entries into the existing AutoCraft bot log."""
    try:
        ts = ""
        created_at = getattr(target, "created_at", None)
        if created_at is not None:
            try:
                ts = created_at.isoformat(sep=" ", timespec="seconds")
            except Exception:
                ts = str(created_at)
        parts = [
            f"[WEB][AUDIT] ts={_clip_audit_field(ts)}",
            f"user={_clip_audit_field(getattr(target, 'user', ''))}",
            f"action={_clip_audit_field(getattr(target, 'action', ''))}",
            f"result={_clip_audit_field(getattr(target, 'result', ''))}",
            f"source={_clip_audit_field(getattr(target, 'source', ''))}",
        ]
        target_text = _clip_audit_field(getattr(target, "target", ""))
        if target_text:
            parts.append(f"target={target_text}")
        ip_text = _clip_audit_field(getattr(target, "ip", ""))
        if ip_text:
            parts.append(f"ip={ip_text}")
        details_text = _clip_audit_field(getattr(target, "details", ""))
        if details_text:
            parts.append(f"details={details_text}")
        _emit_bot_log(" ".join(parts))
    except Exception:
        pass
