from __future__ import annotations

import datetime as dt
import json
from typing import Dict, List

from ..db import db
from ..models.audit import AuditLog
from ..models.jobs import Job
from ..models.power import PowerAction
from ..ops.operations.power import (
    execute_power_action,
    normalize_power_action,
    power_action_label,
)

_ACTIVE_STATUSES = ("pending", "running")
_RUNNING_STALE_SECONDS = 5 * 60

_STATUS_LABELS = {
    "pending": "Ожидание",
    "running": "Выполняется",
    "dispatched": "Отправлено в ОС",
    "cancelled": "Отменено",
    "failed": "Ошибка",
}

_STATUS_CHIPS = {
    "pending": "warn",
    "running": "warn",
    "dispatched": "ok",
    "cancelled": "warn",
    "failed": "bad",
}

_VERIFICATION_LABELS = {
    "pending": "Проверка ожидается",
    "accepted": "Подтверждено",
    "cancelled": "Отменено",
    "failed": "Ошибка проверки",
}


def _clip_details(text: str, max_len: int = 400) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(value) > max_len:
        return value[:max_len] + "...(truncated)"
    return value


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M:%S")


def _remaining_seconds(item: PowerAction, now: dt.datetime | None = None) -> int:
    if not item or not item.scheduled_for:
        return 0
    if item.status != "pending":
        return 0
    now = now or dt.datetime.now()
    delta = int((item.scheduled_for - now).total_seconds())
    return max(0, delta)


def serialize_power_action(item: PowerAction | None, now: dt.datetime | None = None) -> dict:
    if not item:
        return {}
    now = now or dt.datetime.now()
    remaining = _remaining_seconds(item, now=now)
    return {
        "id": item.id,
        "action": item.action,
        "action_label": power_action_label(item.action),
        "status": item.status,
        "status_label": _STATUS_LABELS.get(item.status, item.status),
        "status_chip": _STATUS_CHIPS.get(item.status, "warn"),
        "verification": item.verification,
        "verification_label": _VERIFICATION_LABELS.get(item.verification, item.verification),
        "verification_details": item.verification_details or "",
        "delay_code": item.delay_code or "",
        "delay_label": item.delay_label or "",
        "requested_by": item.requested_by or "",
        "scheduled_for_iso": item.scheduled_for.isoformat() if item.scheduled_for else "",
        "scheduled_for_display": _format_datetime(item.scheduled_for),
        "created_at_display": _format_datetime(item.created_at),
        "started_at_display": _format_datetime(item.started_at),
        "finished_at_display": _format_datetime(item.finished_at),
        "cancelled_at_display": _format_datetime(item.cancelled_at),
        "cancelled_by": item.cancelled_by or "",
        "remaining_seconds": remaining,
        "countdown_target_ts": int(item.scheduled_for.timestamp()) if item.scheduled_for else 0,
    }


def get_active_power_action() -> PowerAction | None:
    return (
        db.session.query(PowerAction)
        .filter(PowerAction.status.in_(_ACTIVE_STATUSES))
        .order_by(PowerAction.scheduled_for.asc(), PowerAction.id.asc())
        .first()
    )


def list_recent_power_actions(limit: int = 12) -> List[dict]:
    rows = (
        db.session.query(PowerAction)
        .order_by(PowerAction.created_at.desc(), PowerAction.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [serialize_power_action(item) for item in rows]


def _audit(
    *,
    user: str,
    action: str,
    target: str,
    result: str,
    source: str,
    ip: str,
    details: str = "",
) -> None:
    try:
        log = AuditLog(
            user=user,
            action=action,
            target=target,
            result=result,
            source=source,
            ip=ip,
            details=_clip_details(details, max_len=600),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def schedule_power_action(
    *,
    action: str,
    scheduled_for: dt.datetime,
    actor: str,
    source: str = "web",
    ip: str = "",
    delay_code: str = "",
    delay_label: str = "",
) -> Dict[str, object]:
    normalized = normalize_power_action(action)
    if not normalized:
        return {"ok": False, "error": "Неизвестное действие питания."}
    if not isinstance(scheduled_for, dt.datetime):
        return {"ok": False, "error": "Не удалось разобрать дату и время запуска."}

    now = dt.datetime.now()
    if scheduled_for <= now:
        return {"ok": False, "error": "Время запуска должно быть в будущем."}

    active = get_active_power_action()
    if active:
        active_payload = serialize_power_action(active, now=now)
        return {
            "ok": False,
            "error": "Уже есть активное запланированное действие. Сначала отмените его.",
            "active": active_payload,
        }

    row = PowerAction(
        scheduled_for=scheduled_for,
        action=normalized,
        status="pending",
        requested_by=actor or "web",
        request_source=source or "web",
        request_ip=ip or "",
        delay_code=(delay_code or "").strip(),
        delay_label=(delay_label or "").strip(),
        verification="pending",
        verification_details="Ожидает запуска по расписанию.",
        created_at=now,
        updated_at=now,
    )
    try:
        db.session.add(row)
        db.session.commit()
        stored = db.session.get(PowerAction, row.id)
        if not stored or stored.status != "pending":
            raise RuntimeError("Проверка сохранения расписания не пройдена.")
    except Exception as exc:
        db.session.rollback()
        _audit(
            user=actor,
            action="power.schedule.create",
            target=normalized,
            result="fail",
            source=source,
            ip=ip,
            details=f"Не удалось сохранить задачу: {exc}",
        )
        return {"ok": False, "error": f"Не удалось сохранить расписание: {exc}"}

    _audit(
        user=actor,
        action="power.schedule.create",
        target=normalized,
        result="ok",
        source=source,
        ip=ip,
        details=f"id={row.id}; scheduled_for={scheduled_for.isoformat()}; delay={delay_label or delay_code}",
    )
    return {"ok": True, "item": serialize_power_action(row, now=now)}


def cancel_active_power_action(*, actor: str, source: str = "web", ip: str = "") -> Dict[str, object]:
    now = dt.datetime.now()
    active = get_active_power_action()
    if not active:
        return {"ok": False, "error": "Активное запланированное действие не найдено."}
    if active.status != "pending":
        return {"ok": False, "error": "Действие уже выполняется и не может быть отменено."}

    action_name = active.action
    action_id = active.id
    try:
        active.status = "cancelled"
        active.cancelled_at = now
        active.cancelled_by = actor or "web"
        active.finished_at = now
        active.updated_at = now
        active.verification = "cancelled"
        active.verification_details = "Пользователь отменил запланированное действие."
        db.session.commit()
        stored = db.session.get(PowerAction, action_id)
        if not stored or stored.status != "cancelled":
            raise RuntimeError("Проверка отмены не пройдена.")
    except Exception as exc:
        db.session.rollback()
        _audit(
            user=actor,
            action="power.schedule.cancel",
            target=action_name,
            result="fail",
            source=source,
            ip=ip,
            details=f"id={action_id}; error={exc}",
        )
        return {"ok": False, "error": f"Не удалось отменить действие: {exc}"}

    _audit(
        user=actor,
        action="power.schedule.cancel",
        target=action_name,
        result="ok",
        source=source,
        ip=ip,
        details=f"id={action_id}",
    )
    return {"ok": True, "item": serialize_power_action(stored, now=now)}


def _log_job_for_execution(item: PowerAction, result: dict, started_at: dt.datetime, finished_at: dt.datetime) -> None:
    try:
        job = Job(
            user=item.requested_by or "scheduler",
            operation=f"power.{item.action}",
            status="success" if result.get("ok") else "failed",
            params=json.dumps(
                {
                    "schedule_id": item.id,
                    "action": item.action,
                    "scheduled_for": item.scheduled_for.isoformat() if item.scheduled_for else "",
                },
                ensure_ascii=False,
            ),
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            source="scheduler",
            started_at=started_at,
            finished_at=finished_at,
        )
        db.session.add(job)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _mark_stale_running_actions(now: dt.datetime) -> None:
    stale_before = now - dt.timedelta(seconds=_RUNNING_STALE_SECONDS)
    stale_rows = (
        db.session.query(PowerAction)
        .filter(
            PowerAction.status == "running",
            PowerAction.started_at.isnot(None),
            PowerAction.started_at < stale_before,
        )
        .all()
    )
    if not stale_rows:
        return
    for row in stale_rows:
        row.status = "failed"
        row.finished_at = now
        row.updated_at = now
        row.verification = "failed"
        row.verification_details = (
            "Действие осталось в состоянии выполнения после перезапуска панели и помечено как ошибка."
        )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def process_due_power_actions() -> Dict[str, object]:
    now = dt.datetime.now()
    _mark_stale_running_actions(now)
    due = (
        db.session.query(PowerAction)
        .filter(
            PowerAction.status == "pending",
            PowerAction.scheduled_for <= now,
        )
        .order_by(PowerAction.scheduled_for.asc(), PowerAction.id.asc())
        .first()
    )
    if not due:
        return {"ok": True, "processed": 0}

    try:
        due.status = "running"
        due.started_at = now
        due.updated_at = now
        due.verification = "pending"
        due.verification_details = "Действие передано на исполнение."
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return {"ok": False, "processed": 0, "error": f"Не удалось перевести задачу в running: {exc}"}

    result = execute_power_action(due.action)
    finished_at = dt.datetime.now()

    try:
        if result.get("ok"):
            due.status = "dispatched"
            due.verification = "accepted"
            due.verification_details = _clip_details(
                str(result.get("stdout") or "Команда отправлена системе."),
                max_len=1200,
            )
        else:
            due.status = "failed"
            due.verification = "failed"
            due.verification_details = _clip_details(
                str(result.get("stderr") or "Команда завершилась с ошибкой."),
                max_len=1200,
            )
        due.finished_at = finished_at
        due.updated_at = finished_at
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return {"ok": False, "processed": 0, "error": f"Не удалось обновить статус исполнения: {exc}"}

    _log_job_for_execution(due, result, started_at=now, finished_at=finished_at)
    _audit(
        user=due.requested_by or "scheduler",
        action="power.schedule.execute",
        target=due.action,
        result="ok" if result.get("ok") else "fail",
        source="scheduler",
        ip=due.request_ip or "",
        details=f"id={due.id}; status={due.status}; {due.verification_details}",
    )
    return {
        "ok": bool(result.get("ok", False)),
        "processed": 1,
        "item": serialize_power_action(due, now=finished_at),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
    }
