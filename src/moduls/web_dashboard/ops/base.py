from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from flask import request
from flask import g
from flask_login import current_user

from ..db import db
from ..models.audit import AuditLog
from ..models.jobs import Job
from .allowlist import ALLOWLIST

_OPS_LOGGER = logging.getLogger("panel.ops")
_SENSITIVE_FIELDS = ("password", "passwd", "secret", "token", "api", "csrf", "authorization")
_MAX_LOG_VALUE_LEN = 200


def _clip_log_value(value: Any) -> str:
    text = str(value)
    if len(text) > _MAX_LOG_VALUE_LEN:
        return text[:_MAX_LOG_VALUE_LEN] + "...(truncated)"
    return text


def _mask_sensitive(key: str, value: Any) -> Any:
    low = key.lower()
    if any(item in low for item in _SENSITIVE_FIELDS):
        return "***"
    if isinstance(value, dict):
        return {str(k): _mask_sensitive(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip_log_value(v) for v in value]
    return _clip_log_value(value)


def _safe_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not params:
        return {}
    safe: Dict[str, Any] = {}
    for key, value in params.items():
        safe[str(key)] = _mask_sensitive(str(key), value)
    return safe

def _user_roles() -> list[str]:
    try:
        proxy_roles = getattr(g, "autocraft_proxy_roles", None)
        if proxy_roles:
            return [str(role) for role in proxy_roles if str(role).strip()]
    except Exception:
        pass
    roles = []
    try:
        roles = [r.name for r in current_user.roles]  # type: ignore[attr-defined]
    except Exception:
        roles = []
    return roles


def _is_allowed(operation: str, roles: list[str]) -> bool:
    entry = ALLOWLIST.get(operation)
    if not entry:
        return False
    allowed = entry.get("roles", [])
    if not roles:
        return False
    return any(role in allowed for role in roles)


def run_operation(
    operation: str,
    params: Optional[Dict[str, Any]] = None,
    actor: str = "system",
    source: str = "system",
) -> Dict[str, Any]:
    params = params or {}
    roles = _user_roles()
    safe_params = _safe_params(params)

    if not _is_allowed(operation, roles):
        try:
            _OPS_LOGGER.warning(
                "operation_denied op=%s actor=%s roles=%s source=%s params=%s",
                operation,
                actor,
                roles,
                source,
                safe_params,
            )
        except Exception:
            pass
        _audit(actor, operation, False, source, details="permission denied")
        return {"ok": False, "stdout": "", "stderr": "Недостаточно прав."}

    entry = ALLOWLIST.get(operation)
    if not entry:
        try:
            _OPS_LOGGER.warning(
                "operation_blocked op=%s actor=%s roles=%s source=%s",
                operation,
                actor,
                roles,
                source,
            )
        except Exception:
            pass
        _audit(actor, operation, False, source, details="operation not allowed")
        return {"ok": False, "stdout": "", "stderr": "Операция запрещена."}

    job = Job(
        user=actor,
        operation=operation,
        status="running",
        params=json.dumps(params, ensure_ascii=False),
        source=source,
        started_at=datetime.now(),
    )
    db.session.add(job)
    db.session.commit()
    job_id = getattr(job, "id", None)
    started_at = time.monotonic()
    try:
        _OPS_LOGGER.info(
            "operation_start op=%s job_id=%s actor=%s source=%s params=%s",
            operation,
            job_id,
            actor,
            source,
            safe_params,
        )
    except Exception:
        pass

    result: Dict[str, Any] = {"ok": True, "stdout": "", "stderr": ""}

    try:
        result = entry["func"](**params)  # type: ignore[misc]
        result = result or {"ok": True, "stdout": "", "stderr": ""}
        job.status = "success" if result.get("ok", True) else "failed"
        job.stdout = result.get("stdout", "")
        job.stderr = result.get("stderr", "")
    except Exception as e:
        job.status = "failed"
        job.stderr = str(e)
        result = {"ok": False, "stdout": "", "stderr": str(e)}
        try:
            _OPS_LOGGER.exception(
                "operation_error op=%s job_id=%s actor=%s source=%s",
                operation,
                job_id,
                actor,
                source,
            )
        except Exception:
            pass

    job.finished_at = datetime.now()
    db.session.commit()
    duration_ms = int((time.monotonic() - started_at) * 1000)
    ok = bool(result.get("ok", False))
    error_text = _clip_log_value(result.get("stderr", ""))
    try:
        if ok:
            _OPS_LOGGER.info(
                "operation_end op=%s job_id=%s ok=%s duration_ms=%s",
                operation,
                job_id,
                ok,
                duration_ms,
            )
        else:
            _OPS_LOGGER.warning(
                "operation_end op=%s job_id=%s ok=%s duration_ms=%s error=%s",
                operation,
                job_id,
                ok,
                duration_ms,
                error_text,
            )
    except Exception:
        pass
    _audit(actor, operation, result.get("ok", False), source, details=result.get("stderr", ""))
    return result


def _audit(actor: str, action: str, result: bool, source: str, details: str = "") -> None:
    try:
        ip = request.remote_addr or ""
    except Exception:
        ip = ""

    log = AuditLog(
        user=actor,
        action=action,
        target="",
        result="ok" if result else "fail",
        source=source,
        ip=ip,
        details=details,
    )
    db.session.add(log)
    db.session.commit()
