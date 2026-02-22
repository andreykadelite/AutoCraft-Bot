from __future__ import annotations

import datetime as dt
import json
from typing import Dict, Iterable, List

from ..db import db
from ..models.audit import AuditLog
from ..models.jobs import Job
from ..models.power import PowerAction, PowerRecurringSchedule
from ..ops.operations.power import execute_power_action, normalize_power_action, power_action_label

_WEEKDAY_OPTIONS = [
    {"value": 0, "short": "Пн", "label": "Понедельник"},
    {"value": 1, "short": "Вт", "label": "Вторник"},
    {"value": 2, "short": "Ср", "label": "Среда"},
    {"value": 3, "short": "Чт", "label": "Четверг"},
    {"value": 4, "short": "Пт", "label": "Пятница"},
    {"value": 5, "short": "Сб", "label": "Суббота"},
    {"value": 6, "short": "Вс", "label": "Воскресенье"},
]

_WEEKDAY_SHORT = {int(item["value"]): str(item["short"]) for item in _WEEKDAY_OPTIONS}
_WEEKDAY_LABELS = {int(item["value"]): str(item["label"]) for item in _WEEKDAY_OPTIONS}
_RUNNING_STATUS = "running"


def _clip_details(text: str, max_len: int = 400) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(value) > max_len:
        return value[:max_len] + "...(truncated)"
    return value


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y %H:%M:%S")


def _parse_time_of_day(value: str) -> tuple[int, int] | None:
    raw = (value or "").strip()
    if len(raw) != 5 or ":" not in raw:
        return None
    hour_raw, minute_raw = raw.split(":", 1)
    if not hour_raw.isdigit() or not minute_raw.isdigit():
        return None
    hour = int(hour_raw)
    minute = int(minute_raw)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour, minute


def _format_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _normalize_weekdays(values: Iterable[int | str]) -> list[int]:
    unique: list[int] = []
    for raw in values:
        try:
            day = int(str(raw).strip())
        except Exception:
            continue
        if day < 0 or day > 6:
            continue
        if day not in unique:
            unique.append(day)
    unique.sort()
    return unique


def _weekdays_mask(days: list[int]) -> int:
    mask = 0
    for day in days:
        if 0 <= day <= 6:
            mask |= 1 << day
    return mask


def _weekdays_from_mask(mask: int) -> list[int]:
    days: list[int] = []
    for day in range(7):
        if mask & (1 << day):
            days.append(day)
    return days


def _weekdays_short_label(days: list[int]) -> str:
    if not days:
        return ""
    return ", ".join(_WEEKDAY_SHORT.get(day, str(day)) for day in days)


def _weekdays_long_label(days: list[int]) -> str:
    if not days:
        return ""
    return ", ".join(_WEEKDAY_LABELS.get(day, str(day)) for day in days)


def _calc_next_run(mask: int, time_of_day: str, now: dt.datetime | None = None) -> dt.datetime | None:
    parsed_time = _parse_time_of_day(time_of_day)
    if not parsed_time:
        return None
    if mask <= 0:
        return None

    now = now or dt.datetime.now()
    hour, minute = parsed_time
    start_date = now.date()

    for offset in range(0, 8):
        target_date = start_date + dt.timedelta(days=offset)
        weekday = target_date.weekday()
        if not (mask & (1 << weekday)):
            continue
        candidate = dt.datetime.combine(target_date, dt.time(hour=hour, minute=minute))
        if candidate > now:
            return candidate
    return None


def list_power_weekday_options() -> list[dict]:
    return [dict(item) for item in _WEEKDAY_OPTIONS]


def serialize_power_recurring_schedule(
    item: PowerRecurringSchedule | None,
    *,
    now: dt.datetime | None = None,
) -> dict:
    if not item:
        return {}
    now = now or dt.datetime.now()
    days = _weekdays_from_mask(int(item.weekdays_mask or 0))
    enabled = bool(item.enabled)
    next_run = item.next_run_at if enabled else None
    return {
        "id": item.id,
        "action": item.action,
        "action_label": power_action_label(item.action),
        "days": days,
        "days_short_label": _weekdays_short_label(days),
        "days_long_label": _weekdays_long_label(days),
        "time_of_day": item.time_of_day or "",
        "enabled": enabled,
        "enabled_label": "Включено" if enabled else "Отключено",
        "status_chip": "ok" if enabled else "warn",
        "next_run_iso": next_run.isoformat() if next_run else "",
        "next_run_display": _format_datetime(next_run),
        "last_run_display": _format_datetime(item.last_run_at),
        "created_at_display": _format_datetime(item.created_at),
        "updated_at_display": _format_datetime(item.updated_at),
        "requested_by": item.requested_by or "",
        "is_overdue": bool(next_run and next_run <= now),
    }


def list_power_recurring_schedules(limit: int = 50) -> List[dict]:
    rows = (
        db.session.query(PowerRecurringSchedule)
        .order_by(
            PowerRecurringSchedule.enabled.desc(),
            PowerRecurringSchedule.next_run_at.asc(),
            PowerRecurringSchedule.id.asc(),
        )
        .limit(max(1, int(limit)))
        .all()
    )
    return [serialize_power_recurring_schedule(item) for item in rows]


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
            details=_clip_details(details, max_len=900),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _log_job_for_execution(
    *,
    schedule: PowerRecurringSchedule,
    action_event: PowerAction,
    result: dict,
    started_at: dt.datetime,
    finished_at: dt.datetime,
) -> None:
    try:
        params = {
            "schedule_type": "recurring",
            "schedule_id": schedule.id,
            "power_action_id": action_event.id,
            "action": action_event.action,
            "scheduled_for": action_event.scheduled_for.isoformat() if action_event.scheduled_for else "",
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else "",
        }
        job = Job(
            user=schedule.requested_by or "scheduler",
            operation=f"power.{action_event.action}",
            status="success" if result.get("ok") else "failed",
            params=json.dumps(params, ensure_ascii=False),
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


def create_power_recurring_schedule(
    *,
    action: str,
    weekdays: Iterable[int | str],
    time_of_day: str,
    actor: str,
    source: str = "web",
    ip: str = "",
) -> Dict[str, object]:
    normalized = normalize_power_action(action)
    if not normalized:
        return {"ok": False, "error": "Неизвестное действие питания."}

    parsed_time = _parse_time_of_day(time_of_day)
    if not parsed_time:
        return {"ok": False, "error": "Укажите корректное время в формате ЧЧ:ММ."}
    hhmm = _format_time(parsed_time[0], parsed_time[1])

    days = _normalize_weekdays(weekdays)
    if not days:
        return {"ok": False, "error": "Выберите хотя бы один день недели."}
    mask = _weekdays_mask(days)

    now = dt.datetime.now()
    next_run = _calc_next_run(mask, hhmm, now=now)
    if not next_run:
        return {"ok": False, "error": "Не удалось вычислить следующий запуск расписания."}

    duplicate = (
        db.session.query(PowerRecurringSchedule)
        .filter(
            PowerRecurringSchedule.action == normalized,
            PowerRecurringSchedule.weekdays_mask == mask,
            PowerRecurringSchedule.time_of_day == hhmm,
            PowerRecurringSchedule.enabled.is_(True),
        )
        .first()
    )
    if duplicate:
        payload = serialize_power_recurring_schedule(duplicate, now=now)
        return {
            "ok": False,
            "error": "Такое активное расписание уже существует.",
            "item": payload,
        }

    row = PowerRecurringSchedule(
        action=normalized,
        weekdays_mask=mask,
        time_of_day=hhmm,
        enabled=True,
        next_run_at=next_run,
        last_run_at=None,
        requested_by=actor or "web",
        request_source=source or "web",
        request_ip=ip or "",
        created_at=now,
        updated_at=now,
    )
    try:
        db.session.add(row)
        db.session.commit()
        stored = db.session.get(PowerRecurringSchedule, row.id)
        if not stored:
            raise RuntimeError("Не удалось проверить созданное расписание.")
    except Exception as exc:
        db.session.rollback()
        _audit(
            user=actor or "web",
            action="power.recurring.create",
            target=normalized,
            result="fail",
            source=source,
            ip=ip,
            details=f"Не удалось сохранить расписание: {exc}",
        )
        return {"ok": False, "error": f"Не удалось сохранить расписание: {exc}"}

    _audit(
        user=actor or "web",
        action="power.recurring.create",
        target=normalized,
        result="ok",
        source=source,
        ip=ip,
        details=(
            f"id={row.id}; weekdays={_weekdays_short_label(days)}; time={hhmm}; "
            f"next_run={next_run.isoformat()}"
        ),
    )
    return {"ok": True, "item": serialize_power_recurring_schedule(row, now=now)}


def set_power_recurring_schedule_enabled(
    *,
    schedule_id: int,
    enabled: bool,
    actor: str,
    source: str = "web",
    ip: str = "",
) -> Dict[str, object]:
    row = db.session.get(PowerRecurringSchedule, int(schedule_id or 0))
    if not row:
        return {"ok": False, "error": "Расписание не найдено."}

    now = dt.datetime.now()
    next_run = row.next_run_at
    if enabled:
        next_run = _calc_next_run(
            int(row.weekdays_mask or 0),
            row.time_of_day or "",
            now=now,
        )
        if not next_run:
            return {"ok": False, "error": "Не удалось вычислить следующий запуск для расписания."}

    try:
        row.enabled = bool(enabled)
        row.next_run_at = next_run if enabled else None
        row.updated_at = now
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _audit(
            user=actor or "web",
            action="power.recurring.toggle",
            target=row.action,
            result="fail",
            source=source,
            ip=ip,
            details=f"id={row.id}; enabled={int(bool(enabled))}; error={exc}",
        )
        return {"ok": False, "error": f"Не удалось обновить расписание: {exc}"}

    _audit(
        user=actor or "web",
        action="power.recurring.toggle",
        target=row.action,
        result="ok",
        source=source,
        ip=ip,
        details=(
            f"id={row.id}; enabled={int(bool(enabled))}; "
            f"next_run={row.next_run_at.isoformat() if row.next_run_at else 'none'}"
        ),
    )
    return {"ok": True, "item": serialize_power_recurring_schedule(row, now=now)}


def delete_power_recurring_schedule(
    *,
    schedule_id: int,
    actor: str,
    source: str = "web",
    ip: str = "",
) -> Dict[str, object]:
    row = db.session.get(PowerRecurringSchedule, int(schedule_id or 0))
    if not row:
        return {"ok": False, "error": "Расписание не найдено."}

    action_name = row.action
    row_id = row.id
    try:
        db.session.delete(row)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _audit(
            user=actor or "web",
            action="power.recurring.delete",
            target=action_name,
            result="fail",
            source=source,
            ip=ip,
            details=f"id={row_id}; error={exc}",
        )
        return {"ok": False, "error": f"Не удалось удалить расписание: {exc}"}

    _audit(
        user=actor or "web",
        action="power.recurring.delete",
        target=action_name,
        result="ok",
        source=source,
        ip=ip,
        details=f"id={row_id}",
    )
    return {"ok": True, "id": row_id}


def process_due_power_recurring_schedules() -> Dict[str, object]:
    now = dt.datetime.now()
    due = (
        db.session.query(PowerRecurringSchedule)
        .filter(
            PowerRecurringSchedule.enabled.is_(True),
            PowerRecurringSchedule.next_run_at.isnot(None),
            PowerRecurringSchedule.next_run_at <= now,
        )
        .order_by(PowerRecurringSchedule.next_run_at.asc(), PowerRecurringSchedule.id.asc())
        .first()
    )
    if not due:
        return {"ok": True, "processed": 0}

    schedule_time = due.next_run_at or now
    next_run = _calc_next_run(
        int(due.weekdays_mask or 0),
        due.time_of_day or "",
        now=now + dt.timedelta(seconds=1),
    )
    if not next_run:
        try:
            due.enabled = False
            due.next_run_at = None
            due.updated_at = now
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {
            "ok": False,
            "processed": 0,
            "error": "Не удалось вычислить следующее время для повторяющегося расписания.",
        }

    days = _weekdays_from_mask(int(due.weekdays_mask or 0))
    delay_label = f"Повтор: {_weekdays_short_label(days)} {due.time_of_day}"
    event = PowerAction(
        scheduled_for=schedule_time,
        action=due.action,
        status=_RUNNING_STATUS,
        requested_by=due.requested_by or "scheduler",
        request_source="recurring",
        request_ip=due.request_ip or "",
        delay_code=f"recurring:{due.id}",
        delay_label=delay_label,
        started_at=now,
        finished_at=None,
        cancelled_at=None,
        cancelled_by="",
        verification="pending",
        verification_details="Действие запущено по повторяющемуся расписанию.",
        created_at=now,
        updated_at=now,
    )

    try:
        due.last_run_at = now
        due.next_run_at = next_run
        due.updated_at = now
        db.session.add(event)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return {"ok": False, "processed": 0, "error": f"Не удалось сохранить запуск расписания: {exc}"}

    started_at = now
    result = execute_power_action(due.action)
    finished_at = dt.datetime.now()

    try:
        stored_event = db.session.get(PowerAction, event.id)
        if not stored_event:
            raise RuntimeError("Событие выполнения не найдено после запуска.")
        if result.get("ok"):
            stored_event.status = "dispatched"
            stored_event.verification = "accepted"
            stored_event.verification_details = _clip_details(
                str(result.get("stdout") or "Команда отправлена системе."),
                max_len=1200,
            )
        else:
            stored_event.status = "failed"
            stored_event.verification = "failed"
            stored_event.verification_details = _clip_details(
                str(result.get("stderr") or "Команда завершилась с ошибкой."),
                max_len=1200,
            )
        stored_event.finished_at = finished_at
        stored_event.updated_at = finished_at
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return {"ok": False, "processed": 0, "error": f"Не удалось обновить статус выполнения: {exc}"}

    _log_job_for_execution(
        schedule=due,
        action_event=stored_event,
        result=result,
        started_at=started_at,
        finished_at=finished_at,
    )
    _audit(
        user=due.requested_by or "scheduler",
        action="power.recurring.execute",
        target=due.action,
        result="ok" if result.get("ok") else "fail",
        source="scheduler",
        ip=due.request_ip or "",
        details=(
            f"schedule_id={due.id}; event_id={stored_event.id}; status={stored_event.status}; "
            f"{stored_event.verification_details}"
        ),
    )
    return {
        "ok": bool(result.get("ok", False)),
        "processed": 1,
        "schedule_id": due.id,
        "item": serialize_power_recurring_schedule(due, now=finished_at),
        "event": {
            "id": stored_event.id,
            "status": stored_event.status,
            "verification": stored_event.verification,
        },
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
    }
