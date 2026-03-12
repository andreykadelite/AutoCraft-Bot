# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

from flask import after_this_request, current_app, flash, redirect, request, send_file, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_wtf.csrf import validate_csrf

from ..plugins import (
    build_plugin_diagnostics,
    create_external_plugin_archive,
    discover_plugins,
    install_external_plugin,
    install_external_plugin_from_zip,
    uninstall_external_plugin,
)
from ..security import panel_has_access as has_access

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)

_VALID_TABS = {"integrated", "external"}


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


def _normalize_tab(value: str | None) -> str:
    tab = str(value or "").strip().lower()
    if tab in _VALID_TABS:
        return tab
    return "integrated"


def _is_truthy(value: str | None) -> bool:
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "on", "да"}


def _flash_result(result: dict, *, success_default: str, error_default: str) -> None:
    if result.get("ok"):
        flash(result.get("message") or success_default, "success")
    else:
        flash(result.get("message") or error_default, "danger")

    details = [str(item).strip() for item in (result.get("details") or []) if str(item).strip()]
    if details and not result.get("ok"):
        flash(details[-1], "warning")


class ExtensionsView(BaseView):
    route_base = "/extensions"
    base_permissions = ["can_list", "can_action"]

    def _build_context(self) -> tuple[list, dict, str, Path | None, dict]:
        plugins = current_app.config.get("WEB_PLUGINS")
        diagnostics = current_app.config.get("WEB_PLUGINS_DIAGNOSTICS")
        base_dir = str(current_app.config.get("BASE_DIR", "") or "")
        resource_root = current_app.config.get("RESOURCE_ROOT")
        root_path = Path(resource_root) if resource_root else None
        if plugins is None:
            plugins = discover_plugins(base_dir, root_path)
        if not isinstance(diagnostics, dict):
            diagnostics = build_plugin_diagnostics(base_dir, root_path, plugins=plugins or [])
        return plugins or [], diagnostics, base_dir, root_path, diagnostics.get("groups") or {}

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        _plugins, diagnostics, _base_dir, _root_path, groups = self._build_context()
        active_tab = _normalize_tab(request.args.get("tab"))
        return self.render_template(
            "extensions.html",
            plugins=diagnostics.get("items") or [],
            groups=groups,
            summary=diagnostics.get("summary") or {},
            roots=diagnostics.get("roots") or [],
            roots_by_source=diagnostics.get("roots_by_source") or {},
            runtime=diagnostics.get("runtime") or {},
            active_tab=active_tab,
        )

    @expose("/install/<plugin_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def install(self, plugin_id: str):
        active_tab = _normalize_tab(request.form.get("tab") or "external")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("ExtensionsView.list", tab=active_tab))

        _plugins, _diagnostics, base_dir, root_path, _groups = self._build_context()
        recreate = str(request.form.get("mode") or "").strip().lower() == "reinstall"
        result = install_external_plugin(
            current_app.appbuilder,
            current_app._get_current_object(),
            base_dir,
            root_path,
            plugin_id,
            recreate=recreate,
        )
        _flash_result(
            result,
            success_default="Операция завершена.",
            error_default="Не удалось установить расширение.",
        )
        return redirect(url_for("ExtensionsView.list", tab=active_tab))

    @expose("/upload_zip", methods=["POST"])
    @has_access
    @permission_name("action")
    def upload_zip(self):
        active_tab = _normalize_tab(request.form.get("tab") or "external")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("ExtensionsView.list", tab=active_tab))

        uploaded = request.files.get("plugin_zip")
        _plugins, _diagnostics, base_dir, root_path, _groups = self._build_context()
        result = install_external_plugin_from_zip(
            current_app.appbuilder,
            current_app._get_current_object(),
            base_dir,
            root_path,
            uploaded,
            replace_existing=_is_truthy(request.form.get("replace_existing")),
            ensure_environment=_is_truthy(request.form.get("setup_environment")),
            recreate_environment=_is_truthy(request.form.get("recreate_environment")),
        )
        _flash_result(
            result,
            success_default="Расширение установлено из ZIP.",
            error_default="Не удалось установить расширение из ZIP.",
        )
        return redirect(url_for("ExtensionsView.list", tab=active_tab))

    @expose("/uninstall/<plugin_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def uninstall(self, plugin_id: str):
        active_tab = _normalize_tab(request.form.get("tab") or "external")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("ExtensionsView.list", tab=active_tab))

        _plugins, _diagnostics, base_dir, root_path, _groups = self._build_context()
        result = uninstall_external_plugin(
            current_app.appbuilder,
            current_app._get_current_object(),
            base_dir,
            root_path,
            plugin_id,
        )
        _flash_result(
            result,
            success_default="Расширение удалено.",
            error_default="Не удалось удалить расширение.",
        )
        return redirect(url_for("ExtensionsView.list", tab=active_tab))

    @expose("/download_zip/<plugin_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def download_zip(self, plugin_id: str):
        active_tab = _normalize_tab(request.form.get("tab") or "external")
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            return redirect(url_for("ExtensionsView.list", tab=active_tab))

        include_venv = _is_truthy(request.form.get("include_venv"))
        _plugins, _diagnostics, base_dir, root_path, _groups = self._build_context()
        result = create_external_plugin_archive(
            current_app._get_current_object(),
            base_dir,
            root_path,
            plugin_id,
            include_venv=include_venv,
        )
        if not result.get("ok"):
            _flash_result(
                result,
                success_default="Архив расширения готов.",
                error_default="Не удалось подготовить архив расширения.",
            )
            return redirect(url_for("ExtensionsView.list", tab=active_tab))

        archive_path = Path(str(result.get("archive_path") or ""))
        cleanup_dir = Path(str(result.get("cleanup_dir") or "")) if result.get("cleanup_dir") else None
        download_name = str(result.get("download_name") or archive_path.name)

        @after_this_request
        def _cleanup(response):
            try:
                if archive_path.exists():
                    archive_path.unlink(missing_ok=True)
            except Exception:
                pass
            if cleanup_dir:
                try:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)
                except Exception:
                    pass
            return response

        return send_file(
            archive_path,
            as_attachment=True,
            download_name=download_name,
            mimetype="application/zip",
        )
