# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..security import panel_has_access as has_access
from ..ops.base import run_operation
from ..ops.operations.autostart import (
    get_registry_locations,
    get_startup_locations,
    list_autostart_tasks,
    list_registry_autostart,
    list_startup_folders,
)
from ..utils import parse_bool

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


class AutoStartView(BaseView):
    route_base = "/autostart"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        registry_entries, registry_error = list_registry_autostart()
        folder_entries, folder_error = list_startup_folders()
        task_entries, task_error = list_autostart_tasks()
        registry_locations = get_registry_locations()
        startup_locations = [
            item for item in get_startup_locations() if item.get("path")
        ]
        value_types = [
            {"value": "REG_SZ", "label": "REG_SZ (строка)"},
            {"value": "REG_EXPAND_SZ", "label": "REG_EXPAND_SZ (переменные)"},
        ]
        task_triggers = [
            {"value": "logon", "label": "При входе в систему"},
            {"value": "boot", "label": "При старте Windows (SYSTEM)"},
        ]
        task_run_levels = [
            {"value": "limited", "label": "Обычный запуск"},
            {"value": "highest", "label": "Повышенные права"},
        ]
        return self.render_template(
            "autostart.html",
            registry_entries=registry_entries,
            registry_error=registry_error,
            registry_locations=registry_locations,
            folder_entries=folder_entries,
            folder_error=folder_error,
            startup_locations=startup_locations,
            task_entries=task_entries,
            task_error=task_error,
            value_types=value_types,
            task_triggers=task_triggers,
            task_run_levels=task_run_levels,
        )

    @expose("/folder/add", methods=["POST"])
    @has_access
    @permission_name("action")
    def folder_add(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        location_id = (request.form.get("location_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        command = (request.form.get("command") or "").strip()
        working_dir = (request.form.get("working_dir") or "").strip()
        if not location_id or not name or not command:
            flash("Заполните имя, команду и папку автозагрузки.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.folder.add",
            params={
                "location_id": location_id,
                "name": name,
                "command": command,
                "working_dir": working_dir,
            },
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка добавления.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/folder/remove", methods=["POST"])
    @has_access
    @permission_name("action")
    def folder_remove(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        location_id = (request.form.get("location_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        if not location_id or not name:
            flash("Не указан элемент автозагрузки.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.folder.remove",
            params={"location_id": location_id, "name": name},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка удаления.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/folder/toggle", methods=["POST"])
    @has_access
    @permission_name("action")
    def folder_toggle(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        location_id = (request.form.get("location_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        enabled = parse_bool(request.form.get("enabled"), default=True)
        if not location_id or not name:
            flash("Не указан элемент автозагрузки.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.folder.set_enabled",
            params={"location_id": location_id, "name": name, "enabled": enabled},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка изменения статуса.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/registry/add", methods=["POST"])
    @has_access
    @permission_name("action")
    def registry_add(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        location_id = (request.form.get("location_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        command = (request.form.get("command") or "").strip()
        value_type = (request.form.get("value_type") or "REG_SZ").strip()
        if not location_id or not name or not command:
            flash("Заполните имя, команду и ветку реестра.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.registry.add",
            params={
                "location_id": location_id,
                "name": name,
                "command": command,
                "value_type": value_type,
            },
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка добавления.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/registry/remove", methods=["POST"])
    @has_access
    @permission_name("action")
    def registry_remove(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        location_id = (request.form.get("location_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        if not location_id or not name:
            flash("Не указан элемент автозагрузки.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.registry.remove",
            params={"location_id": location_id, "name": name},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка удаления.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/registry/toggle", methods=["POST"])
    @has_access
    @permission_name("action")
    def registry_toggle(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        location_id = (request.form.get("location_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        enabled = parse_bool(request.form.get("enabled"), default=True)
        if not location_id or not name:
            flash("Не указан элемент автозагрузки.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.registry.set_enabled",
            params={"location_id": location_id, "name": name, "enabled": enabled},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка изменения статуса.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/task/add", methods=["POST"])
    @has_access
    @permission_name("action")
    def task_add(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        name = (request.form.get("name") or "").strip()
        command = (request.form.get("command") or "").strip()
        trigger = (request.form.get("trigger") or "logon").strip()
        run_level = (request.form.get("run_level") or "limited").strip()
        task_path = (request.form.get("task_path") or "\\").strip()
        working_dir = (request.form.get("working_dir") or "").strip()
        if not name or not command:
            flash("Заполните имя задачи и команду.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.task.add",
            params={
                "name": name,
                "command": command,
                "trigger": trigger,
                "run_level": run_level,
                "task_path": task_path,
                "working_dir": working_dir,
            },
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка создания задачи.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/task/remove", methods=["POST"])
    @has_access
    @permission_name("action")
    def task_remove(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        full_name = (request.form.get("full_name") or "").strip()
        if not full_name:
            flash("Не указана задача.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.task.remove",
            params={"full_name": full_name},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка удаления задачи.", "danger")
        return redirect(url_for("AutoStartView.list"))

    @expose("/task/toggle", methods=["POST"])
    @has_access
    @permission_name("action")
    def task_toggle(self):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoStartView.list"))
        full_name = (request.form.get("full_name") or "").strip()
        enabled = parse_bool(request.form.get("enabled"), default=True)
        if not full_name:
            flash("Не указана задача.", "danger")
            return redirect(url_for("AutoStartView.list"))
        result = run_operation(
            operation="autostart.task.set_enabled",
            params={"full_name": full_name, "enabled": enabled},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if not result.get("ok", False):
            flash(result.get("stderr") or "Ошибка изменения статуса задачи.", "danger")
        return redirect(url_for("AutoStartView.list"))
