# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from flask import current_app
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access

from ..plugins import build_plugin_diagnostics, discover_plugins


class ExtensionsView(BaseView):
    route_base = "/extensions"
    base_permissions = ["can_list"]

    @expose("/")
    @has_access
    @permission_name("list")
    def list(self):
        plugins = current_app.config.get("WEB_PLUGINS")
        diagnostics = current_app.config.get("WEB_PLUGINS_DIAGNOSTICS")
        if plugins is None:
            base_dir = current_app.config.get("BASE_DIR", "")
            resource_root = current_app.config.get("RESOURCE_ROOT")
            root_path = Path(resource_root) if resource_root else None
            plugins = discover_plugins(base_dir, root_path)
        if not isinstance(diagnostics, dict):
            base_dir = current_app.config.get("BASE_DIR", "")
            resource_root = current_app.config.get("RESOURCE_ROOT")
            root_path = Path(resource_root) if resource_root else None
            diagnostics = build_plugin_diagnostics(base_dir, root_path, plugins=plugins or [])
        return self.render_template(
            "extensions.html",
            plugins=diagnostics.get("items") or [],
            summary=diagnostics.get("summary") or {},
            roots=diagnostics.get("roots") or [],
            runtime=diagnostics.get("runtime") or {},
        )
