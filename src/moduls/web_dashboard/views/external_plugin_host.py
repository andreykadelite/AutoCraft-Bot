# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import abort, current_app, jsonify, request, send_from_directory
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from werkzeug.exceptions import HTTPException

from ..external_host import HostedPluginContext
from ..security import panel_has_access as has_access


class ExternalPluginHostView(BaseView):
    route_base = "/plugins"
    base_permissions = ["can_list"]

    def _find_plugin(self, plugin_id: str) -> Any | None:
        for plugin in current_app.config.get("WEB_PLUGINS") or []:
            if getattr(plugin, "plugin_id", "") != plugin_id:
                continue
            if getattr(plugin, "source_type", "") != "external":
                continue
            return plugin
        return None

    def _find_runtime(self, plugin_id: str) -> Any | None:
        registry = current_app.config.get("WEB_EXTERNAL_PLUGIN_RUNTIMES") or {}
        return registry.get(plugin_id)

    def _plugin_permission_name(self, plugin: Any) -> str:
        name = str(getattr(plugin, "permission_view_name", "") or "").strip()
        if name:
            return name
        return str(getattr(plugin, "plugin_id", "") or "ExternalPlugin")

    def _normalize_permission(self, permission_name: str) -> str:
        text = str(permission_name or "").strip() or "can_list"
        if not text.startswith("can_"):
            text = f"can_{text}"
        return text

    def _render_plugin_denied(self, plugin: Any, permission_name: str):
        normalized = self._normalize_permission(permission_name)
        return (
            self.render_template(
                "access_denied.html",
                message=f'Доступ к расширению "{plugin.menu_label}" запрещен.',
                view_label=plugin.menu_label,
                view_name=self._plugin_permission_name(plugin),
                view_display=plugin.menu_label,
                permission_label=normalized,
                permission_name=normalized,
                permission_display=normalized,
                request_path=request.path,
            ),
            403,
        )

    def _ensure_plugin_permission(self, plugin: Any, permission_name: str):
        normalized = self._normalize_permission(permission_name)
        if self.appbuilder.sm.has_access(normalized, self._plugin_permission_name(plugin)):
            return None
        return self._render_plugin_denied(plugin, normalized)

    def _build_context(self, plugin: Any) -> HostedPluginContext:
        return HostedPluginContext(
            app=current_app._get_current_object(),
            appbuilder=self.appbuilder,
            plugin=plugin,
            request=request,
        )

    def _dispatch_runtime(self, plugin_id: str, subpath: str = ""):
        plugin = self._find_plugin(plugin_id)
        if plugin is None:
            return abort(404)

        runtime = self._find_runtime(plugin_id)
        if runtime is None:
            return abort(404)

        try:
            permission_name = runtime.permission_for(str(subpath or ""), request.method)
        except Exception:
            permission_name = "can_list"

        denied = self._ensure_plugin_permission(plugin, permission_name)
        if denied is not None:
            return denied

        context = self._build_context(plugin)
        try:
            return runtime.dispatch(context, str(subpath or ""))
        except HTTPException:
            raise
        except Exception as exc:
            try:
                current_app.logger.exception(
                    "Ошибка внешнего расширения %s: %s",
                    plugin_id,
                    exc,
                )
            except Exception:
                pass
            message = f'Ошибка расширения "{plugin.menu_label}": {exc}'
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "error": message}), 500
            return (
                self.render_template(
                    "access_denied.html",
                    message=message,
                    view_label=plugin.menu_label,
                    view_name=self._plugin_permission_name(plugin),
                    view_display=plugin.menu_label,
                    permission_label="Ошибка",
                    permission_name="error",
                    permission_display="Ошибка",
                    request_path=request.path,
                ),
                500,
            )

    @expose("/<plugin_id>/")
    @has_access
    @permission_name("list")
    def list(self, plugin_id: str):
        return self._dispatch_runtime(plugin_id, "")

    @expose("/<plugin_id>/static/<path:filename>", methods=["GET"])
    @has_access
    @permission_name("list")
    def static(self, plugin_id: str, filename: str):
        plugin = self._find_plugin(plugin_id)
        if plugin is None:
            return abort(404)

        denied = self._ensure_plugin_permission(plugin, "can_list")
        if denied is not None:
            return denied

        static_dir = Path(getattr(plugin, "root", "")) / "static"
        if not static_dir.is_dir():
            return abort(404)
        return send_from_directory(str(static_dir), filename, max_age=0)

    @expose("/<plugin_id>/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @has_access
    @permission_name("list")
    def dispatch(self, plugin_id: str, subpath: str):
        return self._dispatch_runtime(plugin_id, subpath)
