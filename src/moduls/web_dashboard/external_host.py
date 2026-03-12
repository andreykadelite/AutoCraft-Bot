# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import current_app, render_template_string
from flask import url_for as flask_url_for
from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, Rule

_ACTION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_MISSING_TEMPLATE = (
    "{% extends \"panel_base.html\" %}"
    "{% block panel_content %}"
    "<div class=\"panel-card\">"
    "<h3>Расширение недоступно</h3>"
    "<p>Шаблон расширения не найден.</p>"
    "</div>"
    "{% endblock %}"
)


@dataclass(slots=True)
class ExternalPluginMenuProxy:
    class_permission_name: str


@dataclass(slots=True)
class HostedPluginContext:
    app: Any
    appbuilder: Any
    plugin: Any
    request: Any

    def plugin_url(self, subpath: str = "", **params: Any) -> str:
        base = str(getattr(self.plugin, "route_base", "") or "").rstrip("/")
        text = str(subpath or "").strip()
        if text and not text.startswith("/"):
            text = "/" + text
        full_path = (base or "") + text
        if not full_path:
            full_path = "/"
        if not text and not full_path.endswith("/"):
            full_path += "/"

        if not params:
            return full_path

        parsed = urlsplit(full_path)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in params.items():
            if value is None:
                continue
            query[str(key)] = str(value)
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )

    def static_url(self, filename: str = "") -> str:
        text = str(filename or "").lstrip("/")
        if not text:
            return self.plugin_url("static")
        return self.plugin_url(f"static/{text}")

    def render_template_source(self, template_source: str, **kwargs: Any) -> str:
        payload = dict(kwargs)
        payload.setdefault("base_template", self.appbuilder.base_template)
        payload.setdefault("appbuilder", self.appbuilder)
        payload.setdefault("current_app", current_app)
        return render_template_string(template_source, **payload)


class ExternalHostedPlugin:
    permissions: tuple[str, ...] = ("can_list", "can_action")

    def permission_names(self) -> list[str]:
        return [str(item) for item in self.permissions if str(item or "").strip()]

    def permission_for(self, subpath: str, method: str) -> str:
        return "can_action" if str(method or "").upper() in _ACTION_METHODS else "can_list"

    def dispatch(self, context: HostedPluginContext, subpath: str) -> Any:
        raise NotFound()


class TemplateHostedRuntime(ExternalHostedPlugin):
    permissions = ("can_list",)

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def _load_template(self) -> str:
        template_path = getattr(self.plugin, "template", None)
        if not template_path:
            return ""
        for encoding in ("utf-8", "utf-8-sig", "cp1251", "utf-16"):
            try:
                return Path(template_path).read_text(encoding=encoding).lstrip("\ufeff")
            except Exception:
                continue
        return ""

    def dispatch(self, context: HostedPluginContext, subpath: str) -> Any:
        normalized = str(subpath or "").strip().strip("/")
        if normalized:
            raise NotFound()

        template_source = self._load_template()
        if not template_source:
            return context.render_template_source(_MISSING_TEMPLATE, plugin=self.plugin)
        return context.render_template_source(
            template_source,
            plugin=self.plugin,
            static_url=context.static_url(),
            run_url="",
            plugin_static=lambda filename: context.static_url(filename),
        )


class LegacyViewHostedRuntime(ExternalHostedPlugin):
    def __init__(self, plugin: Any, module: Any, view_class: type, appbuilder: Any) -> None:
        self.plugin = plugin
        self.module = module
        self.view_class = view_class
        self.appbuilder = appbuilder
        self.permission_view_name = str(getattr(plugin, "permission_view_name", "") or view_class.__name__)
        self._probe_view = self._new_view()
        self.permissions = tuple(self._collect_permissions(self._probe_view))
        self._url_map = self._build_url_map()
        self._url_builder = self._url_map.bind("")

    def _new_view(self) -> Any:
        view = self.view_class()
        view.appbuilder = self.appbuilder
        view.endpoint = self.view_class.__name__
        view.route_base = str(getattr(self.plugin, "route_base", "") or getattr(view, "route_base", "") or "")
        view.class_permission_name = self.permission_view_name
        return view

    def _collect_permissions(self, view: Any) -> list[str]:
        permissions: list[str] = []
        for item in getattr(view, "base_permissions", []) or []:
            name = str(item or "").strip()
            if not name:
                continue
            if not name.startswith("can_"):
                name = f"can_{name}"
            if name not in permissions:
                permissions.append(name)
        if not permissions:
            permissions.append("can_list")
        return permissions

    def _build_url_map(self) -> Map:
        rules: list[Rule] = []
        for attr_name in dir(self.view_class):
            method = getattr(self.view_class, attr_name, None)
            for url, methods in getattr(method, "_urls", []) or []:
                route = str(url or "/").strip() or "/"
                if not route.startswith("/"):
                    route = "/" + route
                rule_methods = tuple(methods or ("GET",))
                rules.append(Rule(route, endpoint=attr_name, methods=list(rule_methods)))
        return Map(rules)

    def _match(self, subpath: str, method: str) -> tuple[str, dict[str, Any]]:
        path_info = "/" + str(subpath or "").strip().lstrip("/")
        if path_info == "/":
            path_info = "/"
        adapter = self._url_map.bind("")
        return adapter.match(path_info=path_info, method=str(method or "GET").upper())

    def permission_for(self, subpath: str, method: str) -> str:
        attr_name, _values = self._match(subpath, method)
        permission_name = self._probe_view.get_method_permission(attr_name)
        permission_text = str(permission_name or "").strip() or "list"
        if not permission_text.startswith("can_"):
            permission_text = f"can_{permission_text}"
        return permission_text

    def _build_proxy_url(self, context: HostedPluginContext, endpoint: str, values: dict[str, Any]) -> str:
        try:
            relative = self._url_builder.build(endpoint, values, force_external=False)
        except Exception:
            relative = "/"
        return context.plugin_url(relative)

    @contextmanager
    def _patched_url_for(self, context: HostedPluginContext) -> Any:
        module = sys.modules.get(getattr(self.module, "__name__", "")) or self.module
        original = getattr(module, "url_for", None)
        prefix = f"{self.view_class.__name__}."

        def _plugin_url_for(endpoint: str, **values: Any) -> str:
            endpoint_text = str(endpoint or "").strip()
            if endpoint_text.startswith(prefix):
                method_name = endpoint_text.split(".", 1)[1]
                return self._build_proxy_url(context, method_name, values)
            return flask_url_for(endpoint, **values)

        setattr(module, "url_for", _plugin_url_for)
        try:
            yield
        finally:
            if original is None:
                try:
                    delattr(module, "url_for")
                except Exception:
                    pass
            else:
                setattr(module, "url_for", original)

    def dispatch(self, context: HostedPluginContext, subpath: str) -> Any:
        try:
            attr_name, values = self._match(subpath, context.request.method)
        except NotFound:
            raise
        except MethodNotAllowed:
            raise

        view = self._new_view()
        handler = getattr(view, attr_name, None)
        if handler is None:
            raise NotFound()
        with self._patched_url_for(context):
            return handler(**values)
