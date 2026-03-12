from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from werkzeug.security import check_password_hash

from ..db import db
from ..models.audit import AuditLog
from ..ops.base import run_operation
from ..ops.operations.power import available_power_actions, normalize_power_action, power_action_label
from ..security import panel_has_access as has_access
from ..tasks.power_actions import (
    cancel_active_power_action,
    get_active_power_action,
    list_recent_power_actions,
    schedule_power_action,
    serialize_power_action,
)
from ..tasks.power_recurring import (
    create_power_recurring_schedule,
    delete_power_recurring_schedule,
    list_power_recurring_schedules,
    list_power_weekday_options,
    set_power_recurring_schedule_enabled,
)

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)
_UNLOCK_SESSION_KEY = "power_menu_unlock"
_UNLOCK_FAILED_ATTEMPTS_KEY = "power_menu_unlock_fails"
_UNLOCK_BLOCK_UNTIL_KEY = "power_menu_unlock_block_until"
_UNLOCK_TTL_SECONDS = 10 * 60
_UNLOCK_MAX_ATTEMPTS = 5
_UNLOCK_BLOCK_SECONDS = 90
_CUSTOM_DELAY_CODE = "custom"

_DELAY_CHOICES: List[Dict[str, object]] = [
    {"code": "1h", "label": "Через 1 час", "seconds": 1 * 60 * 60},
    {"code": "2h", "label": "Через 2 часа", "seconds": 2 * 60 * 60},
    {"code": "4h", "label": "Через 4 часа", "seconds": 4 * 60 * 60},
    {"code": "8h", "label": "Через 8 часов", "seconds": 8 * 60 * 60},
    {"code": "16h", "label": "Через 16 часов", "seconds": 16 * 60 * 60},
    {"code": "24h", "label": "Через 24 часа", "seconds": 24 * 60 * 60},
    {"code": _CUSTOM_DELAY_CODE, "label": "Свое время", "seconds": None},
]
_DELAY_SECONDS = {
    str(item["code"]): item["seconds"]
    for item in _DELAY_CHOICES
    if item.get("seconds") is not None
}
_RECURRING_WEEKDAY_CHOICES = list_power_weekday_options()
_RECURRING_DEFAULT_DAYS = [int(item["value"]) for item in _RECURRING_WEEKDAY_CHOICES]
_RECURRING_DEFAULT_TIME = "23:00"


def _is_csrf_valid() -> bool:
    token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    return request.headers.get("X-Real-IP", "") or request.remote_addr or ""


def _audit(*, action: str, target: str, result: str, details: str = "") -> None:
    try:
        log = AuditLog(
            user=getattr(current_user, "username", "web"),
            action=action,
            target=target,
            result=result,
            source="web",
            ip=_get_client_ip(),
            details=(details or "")[:900],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _unlock_payload() -> dict:
    value = session.get(_UNLOCK_SESSION_KEY)
    if isinstance(value, dict):
        return value
    return {}


def _clear_unlock() -> None:
    session.pop(_UNLOCK_SESSION_KEY, None)


def _set_unlock() -> int:
    now = int(time.time())
    expires = now + _UNLOCK_TTL_SECONDS
    session[_UNLOCK_SESSION_KEY] = {
        "user_key": _unlock_user_key(),
        "expires_at": expires,
    }
    return expires


def _get_unlock_expires() -> int:
    payload = _unlock_payload()
    expires_at = int(payload.get("expires_at") or 0)
    return expires_at


def _is_unlocked() -> bool:
    payload = _unlock_payload()
    if not payload:
        return False
    if str(payload.get("user_key") or "").strip() != _unlock_user_key():
        _clear_unlock()
        return False
    expires_at = int(payload.get("expires_at") or 0)
    if expires_at <= int(time.time()):
        _clear_unlock()
        return False
    return True


def _is_unlock_blocked() -> int:
    now = int(time.time())
    locked_until = int(session.get(_UNLOCK_BLOCK_UNTIL_KEY) or 0)
    if locked_until > now:
        return locked_until - now
    if locked_until:
        session.pop(_UNLOCK_BLOCK_UNTIL_KEY, None)
    return 0


def _clear_unlock_failures() -> None:
    session.pop(_UNLOCK_FAILED_ATTEMPTS_KEY, None)
    session.pop(_UNLOCK_BLOCK_UNTIL_KEY, None)


def _register_unlock_failure() -> int:
    failures = int(session.get(_UNLOCK_FAILED_ATTEMPTS_KEY) or 0) + 1
    session[_UNLOCK_FAILED_ATTEMPTS_KEY] = failures
    if failures < _UNLOCK_MAX_ATTEMPTS:
        return 0
    lock_until = int(time.time()) + _UNLOCK_BLOCK_SECONDS
    session[_UNLOCK_BLOCK_UNTIL_KEY] = lock_until
    session[_UNLOCK_FAILED_ATTEMPTS_KEY] = 0
    return _UNLOCK_BLOCK_SECONDS


def _effective_actor_username() -> str:
    actor = str(getattr(g, "autocraft_proxy_actor", "") or "").strip()
    if actor:
        return actor
    actor = str(getattr(current_user, "username", "") or "").strip()
    return actor or "web"


def _unlock_user_key() -> str:
    user_id = int(getattr(current_user, "id", 0) or 0)
    if user_id > 0:
        return f"id:{user_id}"
    return f"user:{_effective_actor_username().casefold()}"


def _password_hash_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore").strip()
        except Exception:
            return ""
    return str(value).strip()


def _resolve_password_hash() -> str:
    password_hash = _password_hash_text(getattr(current_user, "password", "") or "")
    if password_hash:
        return password_hash

    sm = getattr(getattr(current_app, "appbuilder", None), "sm", None)
    if sm is None:
        return ""

    lookup_candidates = []
    user_id = int(getattr(current_user, "id", 0) or 0)
    if user_id > 0:
        lookup_candidates.append(("id", user_id))

    actor = _effective_actor_username()
    if actor:
        lookup_candidates.append(("username", actor))
        lowered = actor.lower()
        if lowered != actor:
            lookup_candidates.append(("username", lowered))

    user_obj = None
    for mode, value in lookup_candidates:
        if mode == "id":
            getter = getattr(sm, "get_user_by_id", None)
            if not callable(getter):
                continue
            try:
                user_obj = getter(value)
            except Exception:
                user_obj = None
        else:
            finder = getattr(sm, "find_user", None)
            if not callable(finder):
                continue
            try:
                user_obj = finder(username=value)
            except TypeError:
                try:
                    user_obj = finder(value)
                except Exception:
                    user_obj = None
            except Exception:
                user_obj = None
        if user_obj is not None:
            break

    return _password_hash_text(getattr(user_obj, "password", "") or "")


def _verify_current_password(password: str) -> bool:
    password = (password or "").strip()
    if not password:
        return False
    password_hash = _resolve_password_hash()
    if not password_hash:
        return False
    try:
        if check_password_hash(password_hash, password):
            return True
    except Exception:
        pass
    try:
        sm = getattr(getattr(current_app, "appbuilder", None), "sm", None)
        bcrypt = getattr(sm, "bcrypt", None) if sm else None
        if bcrypt and hasattr(bcrypt, "check_password_hash"):
            return bool(bcrypt.check_password_hash(password_hash, password))
    except Exception:
        pass
    return False


def _parse_custom_datetime(value: str) -> dt.datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _parse_recurring_days(values: list[str] | None) -> list[int]:
    unique: list[int] = []
    for raw in values or []:
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


def _parse_time_of_day(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) != 5 or ":" not in raw:
        return ""
    hour_raw, minute_raw = raw.split(":", 1)
    if not hour_raw.isdigit() or not minute_raw.isdigit():
        return ""
    hour = int(hour_raw)
    minute = int(minute_raw)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


class PowerView(BaseView):
    route_base = "/power"
    base_permissions = ["can_list", "can_action"]

    def _can_action(self) -> bool:
        try:
            return bool(self.appbuilder.sm.has_access("can_action", self.class_permission_name))
        except Exception:
            return False

    def _build_action_choices(self) -> list[dict]:
        return [
            {
                "code": code,
                "label": power_action_label(code),
                "confirm_now": f"Выполнить действие «{power_action_label(code)}» прямо сейчас?",
            }
            for code in available_power_actions()
        ]

    def _render_page(
        self,
        *,
        selected_action: str = "shutdown",
        selected_delay: str = "1h",
        custom_time: str = "",
        selected_recurring_action: str = "shutdown",
        selected_recurring_days: list[int] | None = None,
        selected_recurring_time: str = _RECURRING_DEFAULT_TIME,
    ):
        active = serialize_power_action(get_active_power_action())
        recent = list_recent_power_actions(limit=10)
        recurring = list_power_recurring_schedules(limit=30)
        unlocked = _is_unlocked()
        unlock_expires_at = _get_unlock_expires() if unlocked else 0
        unlock_expires_display = (
            dt.datetime.fromtimestamp(unlock_expires_at).strftime("%d.%m.%Y %H:%M:%S")
            if unlock_expires_at
            else ""
        )
        recurring_days = _parse_recurring_days([str(item) for item in (selected_recurring_days or [])])
        if not recurring_days:
            recurring_days = list(_RECURRING_DEFAULT_DAYS)
        recurring_time = _parse_time_of_day(selected_recurring_time) or _RECURRING_DEFAULT_TIME
        can_action = self._can_action()
        unlock_retry_after = _is_unlock_blocked()
        min_custom_time = (dt.datetime.now() + dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
        unlock_user = _effective_actor_username()
        is_proxy_request = bool(getattr(g, "autocraft_proxy_request", False))
        if is_proxy_request:
            unlock_help_text = (
                "Для входа в раздел питания подтвердите пароль пользователя "
                f"«{unlock_user}», который сейчас управляет удаленным узлом."
            )
            unlock_input_label = f"Текущий пароль пользователя «{unlock_user}»"
        else:
            unlock_help_text = "Для входа в раздел питания подтвердите пароль текущего пользователя панели."
            unlock_input_label = "Текущий пароль пользователя панели"
        return self.render_template(
            "power.html",
            active_action=active,
            recent_actions=recent,
            recurring_schedules=recurring,
            recurring_weekday_choices=_RECURRING_WEEKDAY_CHOICES,
            action_choices=self._build_action_choices(),
            delay_choices=_DELAY_CHOICES,
            selected_action=selected_action,
            selected_delay=selected_delay,
            custom_time=custom_time,
            selected_recurring_action=selected_recurring_action,
            selected_recurring_days=recurring_days,
            selected_recurring_time=recurring_time,
            can_action=can_action,
            unlocked=unlocked,
            unlock_expires_at=unlock_expires_at,
            unlock_expires_display=unlock_expires_display,
            unlock_retry_after=unlock_retry_after,
            min_custom_time=min_custom_time,
            status_url=url_for("PowerView.status"),
            unlock_help_text=unlock_help_text,
            unlock_input_label=unlock_input_label,
            unlock_user=unlock_user,
            is_proxy_request=is_proxy_request,
        )

    @expose("/")
    @has_access
    def list(self):
        return self._render_page()

    @expose("/status")
    @has_access
    @permission_name("list")
    def status(self):
        active = serialize_power_action(get_active_power_action())
        return jsonify(
            {
                "ok": True,
                "server_time": int(time.time()),
                "active_action": active,
            }
        )

    @expose("/unlock", methods=["POST"])
    @has_access
    @permission_name("list")
    def unlock(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))

        retry_after = _is_unlock_blocked()
        if retry_after > 0:
            flash(f"Слишком много неверных попыток. Повторите через {retry_after} сек.", "warning")
            _audit(
                action="power.unlock",
                target="menu",
                result="fail",
                details=f"rate_limited:{retry_after}",
            )
            return redirect(url_for("PowerView.list"))

        password = request.form.get("password") or ""
        if not _verify_current_password(password):
            blocked_for = _register_unlock_failure()
            if blocked_for > 0:
                flash(f"Слишком много неверных попыток. Доступ заблокирован на {blocked_for} сек.", "danger")
            else:
                if getattr(g, "autocraft_proxy_request", False):
                    flash(f"Неверный пароль пользователя «{_effective_actor_username()}».", "danger")
                else:
                    flash("Неверный пароль текущего пользователя панели.", "danger")
            _audit(
                action="power.unlock",
                target="menu",
                result="fail",
                details="invalid_password",
            )
            return redirect(url_for("PowerView.list"))

        _clear_unlock_failures()
        expires_at = _set_unlock()
        expires_text = dt.datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y %H:%M:%S")
        _audit(
            action="power.unlock",
            target="menu",
            result="ok",
            details=f"expires_at={expires_text}",
        )
        flash(f"Доступ к разделу питания подтвержден до {expires_text}.", "success")
        return redirect(url_for("PowerView.list"))

    @expose("/lock", methods=["POST"])
    @has_access
    @permission_name("list")
    def lock(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        _clear_unlock()
        _audit(action="power.lock", target="menu", result="ok", details="manual_lock")
        flash("Управление питанием снова заблокировано.", "info")
        return redirect(url_for("PowerView.list"))

    @expose("/instant/<action>", methods=["POST"])
    @has_access
    @permission_name("action")
    def instant_action(self, action: str):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        if not _is_unlocked():
            flash("Для выполнения действий питания подтвердите пароль на странице раздела.", "warning")
            _audit(action="power.instant", target=action, result="fail", details="locked")
            return redirect(url_for("PowerView.list"))

        normalized = normalize_power_action(action)
        if not normalized:
            flash("Неизвестное действие питания.", "danger")
            return redirect(url_for("PowerView.list"))

        result = run_operation(
            operation=f"power.{normalized}",
            params={},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if result.get("ok"):
            flash(result.get("stdout") or "Команда отправлена системе.", "success")
        else:
            flash(result.get("stderr") or "Команда завершилась с ошибкой.", "danger")
        return redirect(url_for("PowerView.list"))

    @expose("/schedule", methods=["POST"])
    @has_access
    @permission_name("action")
    def schedule(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        if not _is_unlocked():
            flash("Для планирования действий питания подтвердите пароль на странице раздела.", "warning")
            _audit(action="power.schedule", target="menu", result="fail", details="locked")
            return redirect(url_for("PowerView.list"))

        selected_action = normalize_power_action(request.form.get("action"))
        if not selected_action:
            flash("Выберите корректное действие питания.", "danger")
            return self._render_page()

        selected_delays_raw = [item for item in request.form.getlist("delay_option") if item]
        selected_delays: list[str] = []
        for item in selected_delays_raw:
            if item not in _DELAY_SECONDS and item != _CUSTOM_DELAY_CODE:
                continue
            if item not in selected_delays:
                selected_delays.append(item)
        if len(selected_delays) != 1:
            flash("Выберите ровно один интервал для планирования.", "danger")
            return self._render_page(selected_action=selected_action)

        selected_delay = selected_delays[0]
        custom_time = (request.form.get("custom_time") or "").strip()
        now = dt.datetime.now()
        scheduled_for = None
        delay_label = ""

        if selected_delay == _CUSTOM_DELAY_CODE:
            scheduled_for = _parse_custom_datetime(custom_time)
            delay_label = "Свое время"
            if scheduled_for is None:
                flash("Не удалось разобрать пользовательское время запуска.", "danger")
                return self._render_page(
                    selected_action=selected_action,
                    selected_delay=selected_delay,
                    custom_time=custom_time,
                )
        else:
            seconds = int(_DELAY_SECONDS.get(selected_delay) or 0)
            if seconds <= 0:
                flash("Некорректный интервал планирования.", "danger")
                return self._render_page(selected_action=selected_action, selected_delay=selected_delay)
            scheduled_for = now + dt.timedelta(seconds=seconds)
            delay_label = next(
                (str(item.get("label") or "") for item in _DELAY_CHOICES if item.get("code") == selected_delay),
                selected_delay,
            )

        if scheduled_for <= now + dt.timedelta(seconds=50):
            flash("Минимальное время планирования: не меньше 1 минуты от текущего момента.", "danger")
            return self._render_page(
                selected_action=selected_action,
                selected_delay=selected_delay,
                custom_time=custom_time,
            )
        if scheduled_for > now + dt.timedelta(days=30):
            flash("Максимальный горизонт планирования: 30 дней.", "danger")
            return self._render_page(
                selected_action=selected_action,
                selected_delay=selected_delay,
                custom_time=custom_time,
            )

        result = schedule_power_action(
            action=selected_action,
            scheduled_for=scheduled_for,
            actor=getattr(current_user, "username", "web"),
            source="web",
            ip=_get_client_ip(),
            delay_code=selected_delay,
            delay_label=delay_label,
        )
        if not result.get("ok"):
            flash(str(result.get("error") or "Не удалось запланировать действие."), "danger")
            return self._render_page(
                selected_action=selected_action,
                selected_delay=selected_delay,
                custom_time=custom_time,
            )

        item = result.get("item") or {}
        flash(
            "Действие запланировано: "
            f"{item.get('action_label', power_action_label(selected_action))} "
            f"на {item.get('scheduled_for_display', scheduled_for.strftime('%d.%m.%Y %H:%M:%S'))}.",
            "success",
        )
        return redirect(url_for("PowerView.list"))

    @expose("/recurring", methods=["POST"])
    @has_access
    @permission_name("action")
    def schedule_recurring(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        if not _is_unlocked():
            flash("Для планирования повторяющихся действий подтвердите пароль на странице раздела.", "warning")
            _audit(action="power.recurring.create", target="menu", result="fail", details="locked")
            return redirect(url_for("PowerView.list"))

        selected_action = normalize_power_action(request.form.get("action"))
        selected_days = _parse_recurring_days(request.form.getlist("days"))
        time_raw = (request.form.get("time_of_day") or "").strip()
        selected_time = _parse_time_of_day(time_raw)

        if not selected_action:
            flash("Выберите корректное действие питания.", "danger")
            return self._render_page(
                selected_recurring_days=selected_days,
                selected_recurring_time=selected_time or time_raw,
            )
        if not selected_days:
            flash("Выберите хотя бы один день недели.", "danger")
            return self._render_page(
                selected_recurring_action=selected_action,
                selected_recurring_days=selected_days,
                selected_recurring_time=selected_time or time_raw,
            )
        if not selected_time:
            flash("Укажите корректное время в формате ЧЧ:ММ.", "danger")
            return self._render_page(
                selected_recurring_action=selected_action,
                selected_recurring_days=selected_days,
                selected_recurring_time=time_raw,
            )

        result = create_power_recurring_schedule(
            action=selected_action,
            weekdays=selected_days,
            time_of_day=selected_time,
            actor=getattr(current_user, "username", "web"),
            source="web",
            ip=_get_client_ip(),
        )
        if not result.get("ok"):
            flash(str(result.get("error") or "Не удалось сохранить повторяющееся расписание."), "danger")
            return self._render_page(
                selected_recurring_action=selected_action,
                selected_recurring_days=selected_days,
                selected_recurring_time=selected_time,
            )

        item = result.get("item") or {}
        flash(
            "Повторяющееся расписание сохранено: "
            f"{item.get('action_label', power_action_label(selected_action))}, "
            f"{item.get('days_short_label', '')} в {item.get('time_of_day', selected_time)}. "
            f"Ближайший запуск: {item.get('next_run_display', 'не вычислен')}.",
            "success",
        )
        return redirect(url_for("PowerView.list"))

    @expose("/recurring/<int:schedule_id>/toggle", methods=["POST"])
    @has_access
    @permission_name("action")
    def toggle_recurring(self, schedule_id: int):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        if not _is_unlocked():
            flash("Для изменения расписания подтвердите пароль на странице раздела.", "warning")
            _audit(action="power.recurring.toggle", target=str(schedule_id), result="fail", details="locked")
            return redirect(url_for("PowerView.list"))

        enabled_raw = (request.form.get("enabled") or "").strip().lower()
        enabled = enabled_raw in {"1", "true", "on", "yes"}
        result = set_power_recurring_schedule_enabled(
            schedule_id=schedule_id,
            enabled=enabled,
            actor=getattr(current_user, "username", "web"),
            source="web",
            ip=_get_client_ip(),
        )
        if not result.get("ok"):
            flash(str(result.get("error") or "Не удалось обновить расписание."), "danger")
            return redirect(url_for("PowerView.list"))

        item = result.get("item") or {}
        next_run = item.get("next_run_display") or "не запланирован"
        if enabled:
            flash(f"Расписание включено. Ближайший запуск: {next_run}.", "success")
        else:
            flash("Расписание отключено.", "info")
        return redirect(url_for("PowerView.list"))

    @expose("/recurring/<int:schedule_id>/delete", methods=["POST"])
    @has_access
    @permission_name("action")
    def delete_recurring(self, schedule_id: int):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        if not _is_unlocked():
            flash("Для удаления расписания подтвердите пароль на странице раздела.", "warning")
            _audit(action="power.recurring.delete", target=str(schedule_id), result="fail", details="locked")
            return redirect(url_for("PowerView.list"))

        result = delete_power_recurring_schedule(
            schedule_id=schedule_id,
            actor=getattr(current_user, "username", "web"),
            source="web",
            ip=_get_client_ip(),
        )
        if result.get("ok"):
            flash("Повторяющееся расписание удалено.", "success")
        else:
            flash(str(result.get("error") or "Не удалось удалить расписание."), "danger")
        return redirect(url_for("PowerView.list"))

    @expose("/cancel", methods=["POST"])
    @has_access
    @permission_name("action")
    def cancel(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("PowerView.list"))
        if not _is_unlocked():
            flash("Для отмены действия подтвердите пароль на странице раздела.", "warning")
            _audit(action="power.cancel", target="schedule", result="fail", details="locked")
            return redirect(url_for("PowerView.list"))

        result = cancel_active_power_action(
            actor=getattr(current_user, "username", "web"),
            source="web",
            ip=_get_client_ip(),
        )
        if result.get("ok"):
            flash("Запланированное действие отменено и проверка отмены выполнена.", "success")
        else:
            flash(str(result.get("error") or "Не удалось отменить действие."), "danger")
        return redirect(url_for("PowerView.list"))
