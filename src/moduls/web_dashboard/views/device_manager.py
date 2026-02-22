import datetime as dt

from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..security import panel_has_access as has_access
from ..ops.base import run_operation
from ..ops.operations.device_manager import list_devices

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)


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


def _to_int(value):
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _format_datetime(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(value)
    except Exception:
        return value
    return parsed.strftime("%d.%m.%Y %H:%M")


def _normalize_device(raw: dict) -> dict:
    instance_id = (raw.get("instance_id") or "").strip()
    name = (raw.get("name") or "").strip() or instance_id
    class_name = (raw.get("class_name") or "").strip()
    status_raw = (raw.get("status") or "").strip()
    status_lower = status_raw.lower()
    manufacturer = (raw.get("manufacturer") or "").strip()
    present = raw.get("present")
    problem_code = _to_int(raw.get("problem_code"))
    if problem_code is None:
        problem_code = _to_int(raw.get("config_error"))
    disabled = False
    if status_raw.lower().startswith("disabled") or problem_code == 22:
        disabled = True
    has_problem = False
    if problem_code is not None and problem_code != 0:
        has_problem = True
    status_key = "ok"
    if disabled:
        status_key = "disabled"
    elif has_problem:
        status_key = "problem"
    elif not status_raw or "unknown" in status_lower:
        status_key = "unknown"
    elif "error" in status_lower or "degraded" in status_lower:
        status_key = "problem"

    return {
        "instance_id": instance_id,
        "name": name,
        "class_name": class_name,
        "status_raw": status_raw,
        "status_key": status_key,
        "manufacturer": manufacturer,
        "present": present,
        "problem_code": problem_code,
        "config_error": _to_int(raw.get("config_error")),
        "hardware_id": raw.get("hardware_id") or [],
        "location": (raw.get("location") or "").strip(),
        "driver_version": (raw.get("driver_version") or "").strip(),
        "driver_date": _format_datetime(raw.get("driver_date") or ""),
        "driver_provider": (raw.get("driver_provider") or "").strip(),
        "driver_inf": (raw.get("driver_inf") or "").strip(),
        "service": (raw.get("service") or "").strip(),
    }


def _summary(devices: list[dict]) -> dict:
    total = len(devices)
    ok_count = 0
    problem_count = 0
    disabled_count = 0
    unknown_count = 0
    not_present = 0
    for item in devices:
        status_key = item.get("status_key")
        if status_key == "disabled":
            disabled_count += 1
        elif status_key == "problem":
            problem_count += 1
        elif status_key == "unknown":
            unknown_count += 1
        else:
            ok_count += 1
        if item.get("present") is False:
            not_present += 1
    return {
        "total": total,
        "ok": ok_count,
        "problem": problem_count,
        "disabled": disabled_count,
        "unknown": unknown_count,
        "not_present": not_present,
    }


class DeviceManagerView(BaseView):
    route_base = "/devices"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        result = list_devices()
        devices_raw = result.get("data", []) if isinstance(result, dict) else result
        error = result.get("stderr") if isinstance(result, dict) and not result.get("ok", True) else ""

        devices = [_normalize_device(item) for item in devices_raw if isinstance(item, dict)]
        devices.sort(key=lambda item: (item.get("name") or item.get("instance_id") or "").lower())

        status_filter = (request.args.get("status") or "all").strip().lower()
        class_filter = (request.args.get("class") or "").strip()
        present_filter = (request.args.get("present") or "all").strip().lower()
        search = (request.args.get("q") or "").strip().lower()

        classes = sorted({item.get("class_name") for item in devices if item.get("class_name")})

        filtered = []
        for item in devices:
            if status_filter != "all" and item.get("status_key") != status_filter:
                continue
            if class_filter and item.get("class_name") != class_filter:
                continue
            if present_filter != "all":
                present = item.get("present")
                if present_filter == "1" and present is False:
                    continue
                if present_filter == "0" and present is not False:
                    continue
            if search:
                haystack = " ".join(
                    [
                        item.get("name", ""),
                        item.get("instance_id", ""),
                        item.get("manufacturer", ""),
                        item.get("class_name", ""),
                        " ".join(item.get("hardware_id") or []),
                    ]
                ).lower()
                if search not in haystack:
                    continue
            filtered.append(item)

        summary_all = _summary(devices)
        summary_filtered = _summary(filtered)
        filter_active = any(
            [
                status_filter != "all",
                class_filter,
                present_filter != "all",
                search,
            ]
        )

        return self.render_template(
            "device_manager.html",
            devices=filtered,
            classes=classes,
            status_filter=status_filter,
            class_filter=class_filter,
            present_filter=present_filter,
            search=search,
            summary_all=summary_all,
            summary=summary_filtered,
            filter_active=filter_active,
            error=error,
        )

    @expose("/action/<path:instance_id>/<action>", methods=["POST"])
    @has_access
    @permission_name("action")
    def action(self, instance_id: str, action: str):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("DeviceManagerView.list"))

        action_map = {
            "enable": "device.enable",
            "disable": "device.disable",
            "restart": "device.restart",
        }
        operation = action_map.get(action)
        if not operation:
            flash("Неизвестное действие.", "warning")
            return redirect(url_for("DeviceManagerView.list"))

        run_operation(
            operation=operation,
            params={"instance_id": instance_id},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return redirect(url_for("DeviceManagerView.list"))

    @expose("/rescan", methods=["POST"])
    @has_access
    @permission_name("action")
    def rescan(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("DeviceManagerView.list"))

        run_operation(
            operation="device.rescan",
            params={},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return redirect(url_for("DeviceManagerView.list"))
