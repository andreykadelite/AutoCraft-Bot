from __future__ import annotations

import datetime as dt
import importlib
import secrets
import uuid
from typing import Any, Dict, List, Tuple

from sqlalchemy import and_, or_

from .db import db
from .models.remote_access import (
    RemoteControlPolicy,
    RemoteControlRequest,
    ensure_remote_access_schema,
)

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_PROMPT = "prompt"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

_DEFAULT_REQUEST_TTL_SEC = 180
_DEFAULT_GRANT_TTL_SEC = 1800


def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        num = int(value)
    except Exception:
        num = default
    return max(low, min(high, num))


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _safe_text(value: Any, limit: int = 255) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _clean_roles(value: Any) -> str:
    if isinstance(value, list):
        items = [_safe_text(item, 64) for item in value]
    else:
        items = [_safe_text(item, 64) for item in str(value or "").split(",")]
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return ",".join(unique)


def _load_lan_backend():
    last_error: Exception | None = None
    for module_name in (
        "gui_win.gui_win_lan_discovery_window",
        "moduls.gui_win.gui_win_lan_discovery_window",
    ):
        try:
            module = importlib.import_module(module_name)
            getter = getattr(module, "get_lan_discovery_backend", None)
            if callable(getter):
                return getter()
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Служба ЛВС недоступна")


def load_remote_access_settings() -> Dict[str, Any]:
    defaults = {
        "controller_enabled": True,
        "target_enabled": True,
        "require_approval": True,
        "request_ttl_sec": _DEFAULT_REQUEST_TTL_SEC,
        "grant_ttl_sec": _DEFAULT_GRANT_TTL_SEC,
        "auto_request_on_select": True,
        "node_id": "",
        "node_name": "",
        "node_role": "multi",
        "local_ips": [],
    }
    try:
        backend = _load_lan_backend()
        settings = dict(backend.load_settings(force_reload=True))
        status = dict(backend.get_status() or {})
    except Exception:
        return defaults

    defaults["controller_enabled"] = _parse_bool(
        settings.get("remote_control_controller_enabled"),
        True,
    )
    defaults["target_enabled"] = _parse_bool(
        settings.get("remote_control_target_enabled"),
        True,
    )
    defaults["require_approval"] = _parse_bool(
        settings.get("remote_control_require_approval"),
        True,
    )
    defaults["request_ttl_sec"] = _parse_int(
        settings.get("remote_control_request_timeout_sec"),
        _DEFAULT_REQUEST_TTL_SEC,
        30,
        3600,
    )
    defaults["grant_ttl_sec"] = _parse_int(
        settings.get("remote_control_grant_ttl_sec"),
        _DEFAULT_GRANT_TTL_SEC,
        60,
        24 * 3600,
    )
    defaults["auto_request_on_select"] = _parse_bool(
        settings.get("remote_control_auto_request_on_select"),
        True,
    )
    defaults["node_id"] = _safe_text(settings.get("node_id"), 128)
    defaults["node_name"] = _safe_text(settings.get("instance_name"), 255)
    defaults["node_role"] = _safe_text(settings.get("mode"), 32) or "multi"

    ips = status.get("local_ips")
    if isinstance(ips, list):
        defaults["local_ips"] = [str(item).strip() for item in ips if str(item).strip()]
    return defaults


def _new_grant_token() -> str:
    return secrets.token_urlsafe(24)


def cleanup_expired_requests() -> None:
    ensure_remote_access_schema()
    now = _utcnow()
    changed = False
    rows = (
        db.session.query(RemoteControlRequest)
        .filter(
            or_(
                and_(
                    RemoteControlRequest.status == STATUS_PENDING,
                    RemoteControlRequest.expires_at.isnot(None),
                    RemoteControlRequest.expires_at < now,
                ),
                and_(
                    RemoteControlRequest.status == STATUS_APPROVED,
                    RemoteControlRequest.grant_expires_at.isnot(None),
                    RemoteControlRequest.grant_expires_at < now,
                ),
            )
        )
        .all()
    )
    for row in rows:
        row.status = STATUS_EXPIRED
        row.updated_at = now
        if not row.responded_at:
            row.responded_at = now
        changed = True
    if changed:
        db.session.commit()


def list_policies(limit: int = 200) -> List[Dict[str, Any]]:
    ensure_remote_access_schema()
    rows = (
        db.session.query(RemoteControlPolicy)
        .order_by(RemoteControlPolicy.updated_at.desc())
        .limit(max(1, min(int(limit or 200), 1000)))
        .all()
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "controller_node_id": row.controller_node_id,
                "controller_name": row.controller_name,
                "controller_ip": row.controller_ip,
                "decision": row.decision,
                "remember": bool(row.remember),
                "updated_by": row.updated_by,
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }
        )
    return items


def get_policy(controller_node_id: str) -> RemoteControlPolicy | None:
    ensure_remote_access_schema()
    node = _safe_text(controller_node_id, 128)
    if not node:
        return None
    return (
        db.session.query(RemoteControlPolicy)
        .filter(RemoteControlPolicy.controller_node_id == node)
        .first()
    )


def upsert_policy(
    controller_node_id: str,
    controller_name: str,
    controller_ip: str,
    decision: str,
    remember: bool,
    actor: str,
) -> RemoteControlPolicy:
    ensure_remote_access_schema()
    node = _safe_text(controller_node_id, 128)
    if not node:
        raise ValueError("Не указан controller_node_id")
    normalized_decision = str(decision or DECISION_PROMPT).strip().lower()
    if normalized_decision not in {DECISION_ALLOW, DECISION_DENY, DECISION_PROMPT}:
        normalized_decision = DECISION_PROMPT
    row = get_policy(node)
    now = _utcnow()
    if not row:
        row = RemoteControlPolicy(controller_node_id=node)
        db.session.add(row)
        row.created_at = now
    row.controller_name = _safe_text(controller_name, 255)
    row.controller_ip = _safe_text(controller_ip, 64)
    row.decision = normalized_decision
    row.remember = bool(remember)
    row.updated_by = _safe_text(actor, 128)
    row.updated_at = now
    db.session.commit()
    return row


def _serialize_request(row: RemoteControlRequest) -> Dict[str, Any]:
    now = _utcnow()
    expires_in = None
    if row.expires_at:
        expires_in = int((row.expires_at - now).total_seconds())
    grant_expires_in = None
    if row.grant_expires_at:
        grant_expires_in = int((row.grant_expires_at - now).total_seconds())
    return {
        "request_id": row.request_id,
        "controller_node_id": row.controller_node_id,
        "controller_name": row.controller_name,
        "controller_ip": row.controller_ip,
        "controller_panel_url": row.controller_panel_url,
        "requester_user": row.requester_user,
        "requested_roles": row.requested_roles,
        "status": row.status,
        "remember_decision": bool(row.remember_decision),
        "response_note": row.response_note,
        "grant_token": row.grant_token,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "responded_at": row.responded_at.isoformat() if row.responded_at else "",
        "expires_at": row.expires_at.isoformat() if row.expires_at else "",
        "expires_in_sec": expires_in,
        "grant_expires_at": row.grant_expires_at.isoformat() if row.grant_expires_at else "",
        "grant_expires_in_sec": grant_expires_in,
    }


def list_pending_requests(limit: int = 80) -> List[Dict[str, Any]]:
    ensure_remote_access_schema()
    cleanup_expired_requests()
    rows = (
        db.session.query(RemoteControlRequest)
        .filter(RemoteControlRequest.status == STATUS_PENDING)
        .order_by(RemoteControlRequest.created_at.desc())
        .limit(max(1, min(int(limit or 80), 500)))
        .all()
    )
    return [_serialize_request(row) for row in rows]


def _find_recent_pending(controller_node_id: str) -> RemoteControlRequest | None:
    now = _utcnow()
    cutoff = now - dt.timedelta(minutes=5)
    return (
        db.session.query(RemoteControlRequest)
        .filter(
            RemoteControlRequest.controller_node_id == controller_node_id,
            RemoteControlRequest.status == STATUS_PENDING,
            or_(RemoteControlRequest.expires_at.is_(None), RemoteControlRequest.expires_at > now),
            RemoteControlRequest.created_at >= cutoff,
        )
        .order_by(RemoteControlRequest.created_at.desc())
        .first()
    )


def _issue_grant(row: RemoteControlRequest, ttl_sec: int) -> None:
    now = _utcnow()
    row.status = STATUS_APPROVED
    row.grant_token = _new_grant_token()
    row.grant_expires_at = now + dt.timedelta(seconds=max(60, int(ttl_sec or _DEFAULT_GRANT_TTL_SEC)))
    row.responded_at = now
    row.updated_at = now


def create_control_request(
    controller_node_id: str,
    controller_name: str,
    controller_ip: str,
    controller_panel_url: str,
    requester_user: str,
    requested_roles: Any,
) -> Dict[str, Any]:
    ensure_remote_access_schema()
    cleanup_expired_requests()
    settings = load_remote_access_settings()
    if not settings.get("target_enabled", True):
        return {
            "ok": False,
            "status": "disabled",
            "message": "На целевом узле отключен режим управляемого узла.",
        }

    node_id = _safe_text(controller_node_id, 128)
    if not node_id:
        return {"ok": False, "status": "error", "message": "Не указан controller_node_id."}

    pending = _find_recent_pending(node_id)
    if pending:
        data = _serialize_request(pending)
        data.update({"ok": True, "status": STATUS_PENDING, "message": "Запрос уже ожидает подтверждения."})
        return data

    row = RemoteControlRequest(
        request_id=uuid.uuid4().hex,
        controller_node_id=node_id,
        controller_name=_safe_text(controller_name, 255),
        controller_ip=_safe_text(controller_ip, 64),
        controller_panel_url=_safe_text(controller_panel_url, 512),
        requester_user=_safe_text(requester_user, 128),
        requested_roles=_clean_roles(requested_roles),
        status=STATUS_PENDING,
        remember_decision=False,
        response_note="",
    )
    now = _utcnow()
    row.created_at = now
    row.updated_at = now
    row.expires_at = now + dt.timedelta(seconds=max(30, int(settings.get("request_ttl_sec") or _DEFAULT_REQUEST_TTL_SEC)))

    policy = get_policy(node_id)
    require_approval = bool(settings.get("require_approval", True))
    auto_mode = False
    if policy and policy.remember:
        if policy.decision == DECISION_DENY:
            row.status = STATUS_DENIED
            row.remember_decision = True
            row.response_note = "Отклонено сохраненным правилом."
            row.responded_at = now
            auto_mode = True
        elif policy.decision == DECISION_ALLOW:
            _issue_grant(row, int(settings.get("grant_ttl_sec") or _DEFAULT_GRANT_TTL_SEC))
            row.remember_decision = True
            row.response_note = "Одобрено сохраненным правилом."
            auto_mode = True
    elif not require_approval:
        _issue_grant(row, int(settings.get("grant_ttl_sec") or _DEFAULT_GRANT_TTL_SEC))
        row.response_note = "Одобрено автоматически настройками целевого узла."
        auto_mode = True

    db.session.add(row)
    db.session.commit()
    data = _serialize_request(row)
    data.update(
        {
            "ok": True,
            "status": row.status,
            "message": (
                "Доступ разрешен."
                if row.status == STATUS_APPROVED
                else "Доступ отклонен."
                if row.status == STATUS_DENIED
                else "На целевом узле требуется подтверждение."
            ),
            "auto_mode": auto_mode,
        }
    )
    return data


def get_request_status(request_id: str, controller_node_id: str = "") -> Dict[str, Any]:
    ensure_remote_access_schema()
    cleanup_expired_requests()
    rid = _safe_text(request_id, 64)
    if not rid:
        return {"ok": False, "status": "error", "message": "Не указан request_id."}
    query = db.session.query(RemoteControlRequest).filter(RemoteControlRequest.request_id == rid)
    node = _safe_text(controller_node_id, 128)
    if node:
        query = query.filter(RemoteControlRequest.controller_node_id == node)
    row = query.first()
    if not row:
        return {"ok": False, "status": "not_found", "message": "Запрос не найден."}
    data = _serialize_request(row)
    data.update({"ok": True, "status": row.status})
    return data


def respond_to_request(
    request_id: str,
    approve: bool,
    remember: bool,
    actor: str,
    note: str = "",
) -> Dict[str, Any]:
    ensure_remote_access_schema()
    cleanup_expired_requests()
    rid = _safe_text(request_id, 64)
    if not rid:
        return {"ok": False, "status": "error", "message": "Не указан request_id."}
    row = (
        db.session.query(RemoteControlRequest)
        .filter(RemoteControlRequest.request_id == rid)
        .first()
    )
    if not row:
        return {"ok": False, "status": "not_found", "message": "Запрос не найден."}

    now = _utcnow()
    if row.status == STATUS_EXPIRED:
        return {"ok": False, "status": STATUS_EXPIRED, "message": "Срок действия запроса истек."}
    if row.status == STATUS_CANCELLED:
        return {"ok": False, "status": STATUS_CANCELLED, "message": "Запрос был отменен."}

    if approve:
        settings = load_remote_access_settings()
        _issue_grant(row, int(settings.get("grant_ttl_sec") or _DEFAULT_GRANT_TTL_SEC))
        row.response_note = _safe_text(note, 512) or "Одобрено оператором."
    else:
        row.status = STATUS_DENIED
        row.response_note = _safe_text(note, 512) or "Отклонено оператором."
        row.responded_at = now
        row.updated_at = now
        row.grant_token = ""
        row.grant_expires_at = None

    row.remember_decision = bool(remember)
    if remember:
        upsert_policy(
            controller_node_id=row.controller_node_id,
            controller_name=row.controller_name,
            controller_ip=row.controller_ip,
            decision=DECISION_ALLOW if approve else DECISION_DENY,
            remember=True,
            actor=actor,
        )
    else:
        db.session.commit()

    data = _serialize_request(row)
    data.update(
        {
            "ok": True,
            "status": row.status,
            "message": "Запрос одобрен." if approve else "Запрос отклонен.",
        }
    )
    return data


def cancel_request(request_id: str, actor: str = "") -> Dict[str, Any]:
    ensure_remote_access_schema()
    rid = _safe_text(request_id, 64)
    if not rid:
        return {"ok": False, "status": "error", "message": "Не указан request_id."}
    row = (
        db.session.query(RemoteControlRequest)
        .filter(RemoteControlRequest.request_id == rid)
        .first()
    )
    if not row:
        return {"ok": False, "status": "not_found", "message": "Запрос не найден."}
    row.status = STATUS_CANCELLED
    row.response_note = _safe_text(actor, 128) or "отменено"
    row.updated_at = _utcnow()
    row.responded_at = row.updated_at
    row.grant_token = ""
    row.grant_expires_at = None
    db.session.commit()
    data = _serialize_request(row)
    data.update({"ok": True, "status": row.status, "message": "Запрос отменен."})
    return data


def validate_grant(
    controller_node_id: str,
    grant_token: str,
) -> Tuple[bool, str]:
    ensure_remote_access_schema()
    cleanup_expired_requests()
    node = _safe_text(controller_node_id, 128)
    token = _safe_text(grant_token, 96)
    if not node or not token:
        return False, "missing_controller_or_token"
    now = _utcnow()
    row = (
        db.session.query(RemoteControlRequest)
        .filter(
            RemoteControlRequest.controller_node_id == node,
            RemoteControlRequest.status == STATUS_APPROVED,
            RemoteControlRequest.grant_token == token,
            RemoteControlRequest.grant_expires_at.isnot(None),
            RemoteControlRequest.grant_expires_at > now,
        )
        .order_by(RemoteControlRequest.updated_at.desc())
        .first()
    )
    if not row:
        return False, "grant_not_found_or_expired"
    return True, "ok"
