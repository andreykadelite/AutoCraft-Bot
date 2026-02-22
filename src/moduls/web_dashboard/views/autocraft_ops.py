from flask import current_app, flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..ops.base import run_operation
from ..ops.operations.autocraft import collect_autocraft_status, get_autocraft_settings

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


def _form_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _form_checkbox(name: str, default: bool = False) -> bool:
    values = request.form.getlist(name)
    if not values:
        return default
    return _form_bool(values[-1], default=default)


def _normalize_startup_method(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in ("startup_bat", "startup_lnk"):
        return "startup"
    if text in ("auto", "startup", "registry", "schtask"):
        return text
    return "startup"


class AutoCraftOpsView(BaseView):
    route_base = "/autocraft/ops"
    base_permissions = ["can_list", "can_action"]

    def _render_page(
        self,
        base_dir: str,
        bot_cfg_override: dict | None = None,
        local_api_cfg_override: dict | None = None,
        startup_cfg_override: dict | None = None,
    ):
        status = collect_autocraft_status(base_dir)
        actions = self._action_specs(status)
        settings = get_autocraft_settings(base_dir)
        bot_cfg = dict(settings.get("credentials") or {})
        local_api_cfg = dict(settings.get("local_api") or {})
        startup_cfg = dict(status.get("autorun") or settings.get("startup") or {})
        if bot_cfg_override:
            bot_cfg.update(bot_cfg_override)
        if local_api_cfg_override:
            local_api_cfg.update(local_api_cfg_override)
        if startup_cfg_override:
            startup_cfg.update(startup_cfg_override)
        startup_form = {
            "enabled": bool(startup_cfg.get("configured_enabled", startup_cfg.get("enabled", False))),
            "start_in_tray": bool(
                startup_cfg.get("configured_start_in_tray", startup_cfg.get("start_in_tray", False))
            ),
            "method": _normalize_startup_method(
                str(startup_cfg.get("configured_method") or startup_cfg.get("method") or "startup")
            ),
        }
        return self.render_template(
            "autocraft_ops.html",
            status=status,
            actions=actions,
            bot_cfg=bot_cfg,
            local_api_cfg=local_api_cfg,
            startup_cfg=startup_cfg,
            startup_form=startup_form,
        )

    def _action_specs(self, status):
        running = bool(status.get("running"))
        api_running = bool(status.get("api_processes"))
        runtime = status.get("runtime") or {}
        plugins_reload_ready = bool(runtime.get("in_process") and runtime.get("bot_running"))
        startup_status = status.get("autorun") or {}
        autorun_supported = bool(startup_status.get("supported", True))
        autorun_enabled = bool(startup_status.get("enabled"))

        return [
            {
                "key": "start",
                "title": "Запустить AutoCraft",
                "description": "Старт основного процесса AutoCraft (режим вотчдога).",
                "operation": "autocraft.start",
                "danger": False,
                "enabled": not running,
                "group": "core",
                "confirm": "Запустить AutoCraft?",
            },
            {
                "key": "restart_full",
                "title": "Полный перезапуск AutoCraft",
                "description": "Перезапуск с выгрузкой процессов и поднятием нового экземпляра.",
                "operation": "autocraft.restart_full",
                "danger": True,
                "enabled": True,
                "group": "core",
                "confirm": "Запустить полный перезапуск AutoCraft?",
            },
            {
                "key": "stop",
                "title": "Остановить AutoCraft",
                "description": "Корректное завершение процесса AutoCraft.",
                "operation": "autocraft.stop",
                "danger": True,
                "enabled": running,
                "group": "core",
                "confirm": "Остановить AutoCraft?",
            },
            {
                "key": "kill",
                "title": "Завершить AutoCraft принудительно",
                "description": "Мгновенно завершает процесс без сохранения состояния.",
                "operation": "autocraft.kill",
                "danger": True,
                "enabled": running,
                "group": "core",
                "confirm": "Принудительно завершить AutoCraft?",
            },
            {
                "key": "api_start",
                "title": "Запустить локальный Telegram API",
                "description": "Запуск telegram-bot-api на основе настроек gui_settings.",
                "operation": "autocraft.api.start",
                "danger": False,
                "enabled": not api_running,
                "group": "api",
                "confirm": "Запустить локальный Telegram API?",
            },
            {
                "key": "api_restart",
                "title": "Перезапустить локальный Telegram API",
                "description": "Остановить и запустить telegram-bot-api заново.",
                "operation": "autocraft.api.restart",
                "danger": True,
                "enabled": True,
                "group": "api",
                "confirm": "Перезапустить локальный Telegram API?",
            },
            {
                "key": "api_stop",
                "title": "Остановить локальный Telegram API",
                "description": "Завершение процесса telegram-bot-api.",
                "operation": "autocraft.api.stop",
                "danger": True,
                "enabled": api_running,
                "group": "api",
                "confirm": "Остановить локальный Telegram API?",
            },
            {
                "key": "plugins_scan",
                "title": "Пересканировать плагины",
                "description": "Обновить список доступных плагинов в каталоге plugins.",
                "operation": "autocraft.plugins.scan",
                "danger": False,
                "enabled": True,
                "group": "plugins",
                "confirm": "Пересканировать плагины?",
            },
            {
                "key": "plugins_reload",
                "title": "Перезагрузить плагины",
                "description": "Сброс обработчиков и повторная загрузка модулей.",
                "operation": "autocraft.plugins.reload",
                "danger": True,
                "enabled": plugins_reload_ready,
                "group": "plugins",
                "confirm": "Перезагрузить плагины AutoCraft?",
            },
            {
                "key": "autorun_enable",
                "title": "Включить автозапуск AutoCraft",
                "description": "Применить сохраненные настройки автозапуска (метод и запуск в трее).",
                "operation": "autocraft.autorun.enable",
                "danger": False,
                "enabled": autorun_supported and not autorun_enabled,
                "group": "startup",
                "confirm": "Включить автозапуск AutoCraft?",
            },
            {
                "key": "autorun_disable",
                "title": "Выключить автозапуск AutoCraft",
                "description": "Полное удаление автозапуска из системы.",
                "operation": "autocraft.autorun.disable",
                "danger": True,
                "enabled": autorun_supported and autorun_enabled,
                "group": "startup",
                "confirm": "Выключить автозапуск AutoCraft?",
            },
        ]

    @expose("/")
    @has_access
    def list(self):
        base_dir = current_app.config.get("BASE_DIR")
        return self._render_page(base_dir)

    @expose("/action/<action_key>", methods=["POST"])
    @has_access
    @permission_name("action")
    def run_action(self, action_key: str):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoCraftOpsView.list"))

        base_dir = current_app.config.get("BASE_DIR")
        status = collect_autocraft_status(base_dir)
        actions = {item["key"]: item for item in self._action_specs(status)}
        spec = actions.get(action_key)
        if not spec:
            flash("Действие не найдено.", "danger")
            return redirect(url_for("AutoCraftOpsView.list"))

        result = run_operation(
            operation=spec["operation"],
            params={"base_dir": base_dir},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if result.get("ok"):
            flash(result.get("stdout") or "Операция выполнена.", "success")
        else:
            flash(result.get("stderr") or "Операция завершилась с ошибкой.", "danger")
        return redirect(url_for("AutoCraftOpsView.list"))

    @expose("/settings/bot", methods=["POST"])
    @has_access
    @permission_name("action")
    def save_bot_settings(self):
        base_dir = current_app.config.get("BASE_DIR")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoCraftOpsView.list"))

        token = (request.form.get("token") or "").strip()
        pin = (request.form.get("pin") or "").strip()
        allowed_ids = (request.form.get("allowed_ids") or "").strip()
        address = (request.form.get("address") or "").strip()
        port = (request.form.get("port") or "").strip()
        use_standard_api = _form_checkbox("use_standard_api", default=False)
        submit_action = (request.form.get("submit_action") or "save").strip().lower()

        params = {
            "base_dir": base_dir,
            "token": token,
            "pin": pin,
            "allowed_ids": allowed_ids,
            "address": address,
            "port": port,
            "use_standard_api": use_standard_api,
        }
        operation = "autocraft.bot.check" if submit_action == "check" else "autocraft.bot.settings.save"
        result = run_operation(
            operation=operation,
            params=params,
            actor=getattr(current_user, "username", "web"),
            source="web",
        )

        if result.get("ok"):
            flash(result.get("stdout") or "Операция выполнена.", "success")
            if submit_action == "check":
                return self._render_page(
                    base_dir,
                    bot_cfg_override={
                        "token": token,
                        "pin": pin,
                        "allowed_ids": allowed_ids,
                        "address": address,
                        "port": port,
                        "use_standard_api": use_standard_api,
                    },
                )
            return redirect(url_for("AutoCraftOpsView.list"))

        flash(result.get("stderr") or "Операция завершилась с ошибкой.", "danger")
        return self._render_page(
            base_dir,
            bot_cfg_override={
                "token": token,
                "pin": pin,
                "allowed_ids": allowed_ids,
                "address": address,
                "port": port,
                "use_standard_api": use_standard_api,
            },
        )

    @expose("/settings/local-api", methods=["POST"])
    @has_access
    @permission_name("action")
    def save_local_api_settings(self):
        base_dir = current_app.config.get("BASE_DIR")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoCraftOpsView.list"))

        local_api_cfg = {
            "api_id": (request.form.get("api_id") or "").strip(),
            "api_hash": (request.form.get("api_hash") or "").strip(),
            "local_mode": _form_checkbox("local_mode", default=True),
            "http_ip": (request.form.get("http_ip") or "").strip(),
            "http_port": (request.form.get("http_port") or "").strip(),
            "max_webhook_connections": (request.form.get("max_webhook_connections") or "").strip(),
            "verbosity": (request.form.get("verbosity") or "").strip(),
            "data_dir": (request.form.get("data_dir") or "").strip(),
            "temp_dir": (request.form.get("temp_dir") or "").strip(),
            "exe_path": (request.form.get("exe_path") or "").strip(),
            "auto_start": _form_checkbox("auto_start", default=False),
            "log_max_size": (request.form.get("log_max_size") or "").strip(),
            "ui_max_lines": (request.form.get("ui_max_lines") or "").strip(),
            "api_max_lines": (request.form.get("api_max_lines") or "").strip(),
            "log_flush_ms": (request.form.get("log_flush_ms") or "").strip(),
            "api_log_to_file": _form_checkbox("api_log_to_file", default=False),
            "auto_detect_paths": _form_checkbox("auto_detect_paths", default=False),
        }

        result = run_operation(
            operation="autocraft.local_api.settings.save",
            params={"base_dir": base_dir, **local_api_cfg},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if result.get("ok"):
            flash(result.get("stdout") or "Настройки сохранены.", "success")
            return redirect(url_for("AutoCraftOpsView.list"))

        flash(result.get("stderr") or "Не удалось сохранить настройки.", "danger")
        return self._render_page(base_dir, local_api_cfg_override=local_api_cfg)

    @expose("/settings/startup", methods=["POST"])
    @has_access
    @permission_name("action")
    def save_startup_settings(self):
        base_dir = current_app.config.get("BASE_DIR")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("AutoCraftOpsView.list"))

        autorun_enabled = _form_checkbox("autorun_enabled", default=False)
        start_in_tray = _form_checkbox("start_in_tray", default=False)
        method = _normalize_startup_method(request.form.get("method"))
        if not autorun_enabled:
            start_in_tray = False

        params = {
            "base_dir": base_dir,
            "enabled": autorun_enabled,
            "start_in_tray": start_in_tray,
            "method": method,
        }
        result = run_operation(
            operation="autocraft.autorun.configure",
            params=params,
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        if result.get("ok"):
            flash(result.get("stdout") or "Настройки автозапуска применены.", "success")
            return redirect(url_for("AutoCraftOpsView.list"))

        flash(result.get("stderr") or "Не удалось применить настройки автозапуска.", "danger")
        return self._render_page(
            base_dir,
            startup_cfg_override={
                "configured_enabled": autorun_enabled,
                "configured_start_in_tray": start_in_tray,
                "configured_method": method,
            },
        )
