# -*- coding: utf-8 -*-
from __future__ import annotations

import codecs
import collections
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from flask import Blueprint, current_app, render_template_string, url_for
from flask_appbuilder import BaseView, expose
from .external_host import ExternalPluginMenuProxy, LegacyViewHostedRuntime, TemplateHostedRuntime
from .external_plugins import (
    INSTALL_STATE_ERROR,
    INSTALL_STATE_INSTALLED,
    INSTALL_STATE_NOT_APPLICABLE,
    INSTALL_STATE_NOT_INSTALLED,
    collect_external_plugin_installation_info,
    ensure_external_plugin_environment,
    resolve_external_plugin_roots,
)
from .security import panel_has_access as has_access

_PLUGIN_LOGGER = logging.getLogger("panel.plugins")

_DEFAULT_CATEGORY = "Расширения"
_DEFAULT_ROLES = ["Admin", "Operator"]
_MANIFEST_FILE = "plugin.json"
_ENTRYPOINT_EXTS = (".py", ".pyc", ".pyd", ".so", ".dll")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MISSING_TEMPLATE = (
    "{% extends \"panel_base.html\" %}"
    "{% block panel_content %}"
    "<div class=\"panel-card\">"
    "<h3>Расширение недоступно</h3>"
    "<p>Шаблон расширения не найден.</p>"
    "</div>"
    "{% endblock %}"
)
_VIEW_NAME_RE = re.compile(r"[^0-9A-Za-z_]+")
_CATEGORY_ALIASES = {
    "extension": _DEFAULT_CATEGORY,
    "extensions": _DEFAULT_CATEGORY,
    "plugin": _DEFAULT_CATEGORY,
    "plugins": _DEFAULT_CATEGORY,
    "плагин": _DEFAULT_CATEGORY,
    "плагины": _DEFAULT_CATEGORY,
    "расширение": _DEFAULT_CATEGORY,
    "расширения": _DEFAULT_CATEGORY,
}
_ROLE_ALIASES = {
    "super admin": "Super Admin",
    "admin": "Admin",
    "operator": "Operator",
    "viewer": "Viewer",
    "auditor": "Auditor",
}
_IGNORED_PLUGIN_DIRS = {"__pycache__", ".git", ".svn", ".hg"}
_PLUGIN_SOURCE_INTEGRATED = "integrated"
_PLUGIN_SOURCE_EXTERNAL = "external"
_EXTERNAL_PLUGIN_HOST_VIEW_NAME = "ExternalPluginHostView"
_EXTERNAL_PLUGIN_VIEW_PREFIX = "ExternalPluginView::"
_EXTERNAL_PLUGIN_MENU_PREFIX = "ExternalPluginMenu::"
_PLUGIN_SOURCE_LABELS = {
    _PLUGIN_SOURCE_INTEGRATED: "Интегрированное",
    _PLUGIN_SOURCE_EXTERNAL: "Внешнее",
}
_PLUGIN_ARCHIVE_MAX_UPLOAD_SIZE = 1024 * 1024 * 1024
_PLUGIN_ARCHIVE_MAX_UNPACKED_SIZE = 1024 * 1024 * 1024
_PLUGIN_ARCHIVE_MAX_FILE_SIZE = 256 * 1024 * 1024
_PLUGIN_ARCHIVE_MAX_FILES = 40000


@dataclass
class PluginCheck:
    key: str
    label: str
    path: str
    level: str
    message: str
    chip: str


@dataclass
class PluginSpec:
    plugin_id: str
    name: str
    version: str
    description: str
    root: Path
    entrypoint: Optional[Path]
    entrypoint_hint: Optional[str]
    template: Optional[Path]
    menu_label: str
    category: str
    order: int
    roles: list[str]
    route_base: str
    url: str
    manifest_path: Path
    source_type: str = _PLUGIN_SOURCE_INTEGRATED
    manifest_encoding: str = ""
    template_hint: str = "index.html"
    entrypoint_required: bool = False
    discovered_root: Optional[Path] = None
    blueprint: Optional[Blueprint] = None
    module: Any = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[PluginCheck] = field(default_factory=list)
    module_loaded: bool = False
    module_origin: str = ""
    fallback_view_used: bool = False
    resolved_views: list[str] = field(default_factory=list)
    registered_views: list[str] = field(default_factory=list)
    load_state: str = "discovered"
    dependencies: list[str] = field(default_factory=list)
    requirements_path: Optional[Path] = None
    base_python: Optional[Path] = None
    base_python_source: str = ""
    base_python_source_label: str = ""
    venv_dir: Optional[Path] = None
    venv_python: Optional[Path] = None
    venv_pip: Optional[Path] = None
    venv_site_packages: Optional[Path] = None
    install_state: str = INSTALL_STATE_NOT_APPLICABLE
    install_message: str = ""
    installed: bool = False
    permission_view_name: str = ""
    menu_name: str = ""
    menu_registered: bool = False
    runtime_mode: str = "view"
    runtime_label: str = ""


def _safe_plugin_id(value: str) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().lower()
    if _SAFE_ID_RE.match(cleaned):
        return cleaned
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", cleaned).strip("_")
    if _SAFE_ID_RE.match(cleaned):
        return cleaned
    return None


def _external_plugin_route_base(plugin_id: str) -> str:
    return f"/plugins/{plugin_id}"


def _external_plugin_view_name(plugin_id: str) -> str:
    return f"{_EXTERNAL_PLUGIN_VIEW_PREFIX}{plugin_id}"


def _external_plugin_menu_name(plugin_id: str) -> str:
    return f"{_EXTERNAL_PLUGIN_MENU_PREFIX}{plugin_id}"


def _normalize_permission_name(permission_name: str) -> str:
    text = _normalize_text(permission_name) or "can_list"
    if not text.startswith("can_"):
        text = f"can_{text}"
    return text


def _chip_by_level(level: str) -> str:
    if level == "error":
        return "bad"
    if level == "warning":
        return "warn"
    return "ok"


def _append_check(plugin: PluginSpec, key: str, label: str, path: Path | str, level: str, message: str) -> None:
    chip = _chip_by_level(level)
    check = PluginCheck(
        key=key,
        label=label,
        path=str(path),
        level=level,
        message=message,
        chip=chip,
    )
    plugin.checks.append(check)


def _append_issue(plugin: PluginSpec, message: str, *, level: str = "error") -> None:
    text = _normalize_text(message)
    if not text:
        return
    if level == "warning":
        plugin.warnings.append(text)
    else:
        plugin.errors.append(text)

    try:
        if level == "warning":
            _PLUGIN_LOGGER.warning("plugin %s: %s", plugin.plugin_id, text)
        else:
            _PLUGIN_LOGGER.error("plugin %s: %s", plugin.plugin_id, text)
    except Exception:
        pass


def _log_plugin_warning(plugin: PluginSpec, message: str) -> None:
    _append_issue(plugin, message, level="warning")


def _log_plugin_error(plugin: PluginSpec, message: str) -> None:
    _append_issue(plugin, message, level="error")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_menu_label(value: Any, fallback: str) -> str:
    label = _normalize_text(value)
    if not label:
        label = _normalize_text(fallback)
    return label or fallback


def _normalize_category(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return _DEFAULT_CATEGORY
    alias = _CATEGORY_ALIASES.get(text.casefold())
    return alias or text


def _normalize_roles(value: Any) -> list[str]:
    roles = value or _DEFAULT_ROLES
    if isinstance(roles, str):
        roles = [roles]
    if not isinstance(roles, list):
        roles = _DEFAULT_ROLES

    cleaned: list[str] = []
    for role in roles:
        role_text = _normalize_text(role)
        if not role_text:
            continue
        canonical = _ROLE_ALIASES.get(role_text.casefold(), role_text)
        if canonical not in cleaned:
            cleaned.append(canonical)

    if not cleaned:
        cleaned = list(_DEFAULT_ROLES)
    if "Super Admin" not in cleaned:
        cleaned.append("Super Admin")
    return cleaned


def _normalize_route_base(value: Any, plugin_id: str) -> str:
    try:
        route_base = str(value or "").strip()
    except Exception:
        route_base = ""
    if not route_base:
        route_base = f"/plugins/{plugin_id}"
    if not route_base.startswith("/"):
        route_base = "/" + route_base
    if route_base != "/":
        route_base = route_base.rstrip("/")
    return route_base


def _collect_view_names(appbuilder) -> set[str]:
    names: set[str] = set()
    for attr in ("baseviews", "views", "_views"):
        items = getattr(appbuilder, attr, None)
        if isinstance(items, dict):
            names.update(str(key) for key in items.keys() if key)
            continue
        if isinstance(items, list):
            for item in items:
                name = getattr(item, "__name__", None)
                if name:
                    names.add(str(name))
    return names


def _sanitize_view_name(name: str) -> str:
    cleaned = _VIEW_NAME_RE.sub("_", name or "").strip("_")
    if not cleaned:
        return "View"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def _unique_view_name(base_name: str, plugin: PluginSpec, used: set[str]) -> str:
    prefix = _sanitize_view_name(f"Plugin{plugin.plugin_id.title()}")
    base_clean = _sanitize_view_name(base_name)
    candidate = f"{prefix}{base_clean}"
    if candidate not in used:
        return candidate
    idx = 2
    while True:
        candidate = f"{prefix}{base_clean}{idx}"
        if candidate not in used:
            return candidate
        idx += 1


def _prepare_plugin_view(view: type, plugin: PluginSpec, used: set[str]) -> type:
    view_name = getattr(view, "__name__", "") or "View"
    safe_name = _sanitize_view_name(view_name)
    route_base = getattr(view, "route_base", None)
    route_base_text = str(route_base).strip() if isinstance(route_base, str) else ""
    normalized_route = _normalize_route_base(route_base_text, plugin.plugin_id) if route_base_text else plugin.route_base
    needs_route = not route_base_text or normalized_route != route_base_text
    needs_name = safe_name != view_name or view_name in used or safe_name in used

    if not needs_route and not needs_name:
        return view

    attrs = {"__module__": getattr(view, "__module__", __name__)}
    if needs_route:
        attrs["route_base"] = normalized_route

    if needs_name:
        if safe_name != view_name and safe_name not in used:
            new_name = safe_name
        else:
            new_name = _unique_view_name(view_name, plugin, used)
    else:
        new_name = view_name

    return type(new_name, (view,), attrs)


def _candidate_integrated_plugin_roots(base_dir: str, resource_root: Optional[Path]) -> list[Path]:
    roots: list[Path] = []
    base_path = Path(base_dir)

    roots.append(base_path / "web_plugins")
    roots.append(base_path / "moduls" / "web_dashboard" / "web_plugins")
    roots.append(base_path / "web_dashboard" / "web_plugins")

    try:
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir / "web_plugins")
        roots.append(exe_dir / "moduls" / "web_dashboard" / "web_plugins")
        roots.append(exe_dir / "web_dashboard" / "web_plugins")
    except Exception:
        pass

    try:
        argv_dir = Path(sys.argv[0]).resolve().parent
        roots.append(argv_dir / "web_plugins")
        roots.append(argv_dir / "moduls" / "web_dashboard" / "web_plugins")
        roots.append(argv_dir / "web_dashboard" / "web_plugins")
    except Exception:
        pass

    try:
        roots.append(Path(__file__).resolve().parent / "web_plugins")
    except Exception:
        pass

    if resource_root:
        roots.append(resource_root / "web_plugins")

    unique: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = Path(str(root))
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _candidate_plugin_sources(base_dir: str, resource_root: Optional[Path]) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for root in _candidate_integrated_plugin_roots(base_dir, resource_root):
        sources.append((_PLUGIN_SOURCE_INTEGRATED, root))
    for root in resolve_external_plugin_roots(base_dir):
        sources.append((_PLUGIN_SOURCE_EXTERNAL, root))

    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source_type, root in sources:
        key = os.path.normcase(str(_safe_resolve(root)))
        if key in seen:
            continue
        seen.add(key)
        unique.append((source_type, _safe_resolve(root)))
    return unique


def _candidate_plugin_roots(base_dir: str, resource_root: Optional[Path]) -> list[Path]:
    return [root for _source_type, root in _candidate_plugin_sources(base_dir, resource_root)]


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return Path(str(path))


def _plugin_source_label(source_type: str) -> str:
    return _PLUGIN_SOURCE_LABELS.get(source_type, _PLUGIN_SOURCE_LABELS[_PLUGIN_SOURCE_INTEGRATED])


def _source_type_for_root(base_dir: str, root: Path | None) -> str:
    if root is None:
        return _PLUGIN_SOURCE_INTEGRATED
    root_key = os.path.normcase(str(_safe_resolve(root)))
    external_keys = {
        os.path.normcase(str(_safe_resolve(item)))
        for item in resolve_external_plugin_roots(base_dir)
    }
    if root_key in external_keys:
        return _PLUGIN_SOURCE_EXTERNAL
    return _PLUGIN_SOURCE_INTEGRATED


def _apply_external_plugin_runtime(plugin: PluginSpec, base_dir: str, manifest: dict[str, Any] | None = None) -> None:
    info = collect_external_plugin_installation_info(base_dir, plugin.root, manifest)
    plugin.dependencies = list(info.get("dependencies") or [])
    requirements_path = str(info.get("requirements_path") or "").strip()
    plugin.requirements_path = Path(requirements_path) if requirements_path else None
    base_python = str(info.get("base_python") or "").strip()
    plugin.base_python = Path(base_python) if base_python else None
    plugin.base_python_source = str(info.get("base_python_source") or "")
    plugin.base_python_source_label = str(info.get("base_python_source_label") or "")
    venv_dir = str(info.get("venv_dir") or "").strip()
    venv_python = str(info.get("venv_python") or "").strip()
    venv_pip = str(info.get("venv_pip") or "").strip()
    venv_site_packages = str(info.get("venv_site_packages") or "").strip()
    plugin.venv_dir = Path(venv_dir) if venv_dir else None
    plugin.venv_python = Path(venv_python) if venv_python else None
    plugin.venv_pip = Path(venv_pip) if venv_pip else None
    plugin.venv_site_packages = Path(venv_site_packages) if venv_site_packages else None
    plugin.install_state = str(info.get("state") or INSTALL_STATE_NOT_INSTALLED)
    plugin.install_message = str(info.get("message") or "")
    plugin.installed = bool(info.get("installed"))

    if plugin.base_python:
        _append_check(
            plugin,
            "external_python",
            "Python расширения",
            plugin.base_python,
            "ok",
            f"Используется интерпретатор: {plugin.base_python_source_label or 'Python'}",
        )
    else:
        _append_check(
            plugin,
            "external_python",
            "Python расширения",
            plugin.root / "python",
            "error",
            "Не найден Python для установки внешнего расширения.",
        )

    if plugin.dependencies:
        _append_check(
            plugin,
            "external_dependencies",
            "Зависимости",
            plugin.requirements_path or plugin.root,
            "ok",
            f"Заявлено pip-зависимостей: {len(plugin.dependencies)}",
        )
    elif plugin.requirements_path:
        _append_check(
            plugin,
            "external_dependencies",
            "Зависимости",
            plugin.requirements_path,
            "ok",
            "Используется requirements.txt внешнего расширения.",
        )
    else:
        _append_check(
            plugin,
            "external_dependencies",
            "Зависимости",
            plugin.root,
            "warning",
            "Дополнительные pip-зависимости не заявлены.",
        )

    venv_probe = plugin.venv_dir or (plugin.root / "venv")
    if plugin.install_state == INSTALL_STATE_INSTALLED:
        _append_check(
            plugin,
            "external_venv",
            "Среда расширения",
            venv_probe,
            "ok",
            plugin.install_message or "Среда внешнего расширения готова.",
        )
    elif plugin.install_state == INSTALL_STATE_ERROR:
        _log_plugin_error(plugin, plugin.install_message or "Среда внешнего расширения недоступна.")
        _append_check(
            plugin,
            "external_venv",
            "Среда расширения",
            venv_probe,
            "error",
            plugin.install_message or "Среда внешнего расширения недоступна.",
        )
    else:
        _log_plugin_warning(plugin, plugin.install_message or "Среда внешнего расширения ещё не создана.")
        _append_check(
            plugin,
            "external_venv",
            "Среда расширения",
            venv_probe,
            "warning",
            plugin.install_message or "Среда внешнего расширения ещё не создана.",
        )


def _extend_plugin_import_paths(plugin: PluginSpec) -> None:
    candidates: list[Path] = [plugin.root]
    if plugin.source_type == _PLUGIN_SOURCE_EXTERNAL and plugin.venv_site_packages:
        candidates.insert(0, plugin.venv_site_packages)

    changed = False
    for candidate in candidates:
        candidate_str = str(candidate)
        if not candidate_str:
            continue
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
            changed = True

    if changed:
        importlib.invalidate_caches()


def _is_plugin_dir_candidate(path: Path) -> bool:
    name = path.name.strip()
    if not name:
        return False
    if name.startswith("."):
        return False
    if name.casefold() in _IGNORED_PLUGIN_DIRS:
        return False
    markers = [
        path / _MANIFEST_FILE,
        path / "index.html",
        path / "static",
    ]
    for marker in markers:
        try:
            if marker.exists():
                return True
        except Exception:
            continue

    for ext in _ENTRYPOINT_EXTS:
        try:
            if (path / f"plugin{ext}").exists():
                return True
        except Exception:
            continue

    return False


def _is_compiled_runtime() -> bool:
    try:
        if bool(globals().get("__compiled__", False)):
            return True
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        return True

    try:
        if getattr(sys, "_MEIPASS", None):
            return True
    except Exception:
        pass

    for env_name in ("NUITKA_ONEFILE_PARENT", "NUITKA_ONEFILE_TEMP", "NUITKA_ONEFILE_TEMP_DIR"):
        if str(os.environ.get(env_name, "") or "").strip():
            return True

    try:
        exe_name = Path(str(getattr(sys, "executable", "") or "")).name.casefold()
        if exe_name.endswith(".exe") and exe_name not in ("python.exe", "pythonw.exe"):
            return True
    except Exception:
        pass

    return False


def _format_exc(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _load_manifest_detailed(plugin_dir: Path) -> tuple[Optional[dict[str, Any]], str, str]:
    manifest_path = plugin_dir / _MANIFEST_FILE
    if not manifest_path.is_file():
        return None, "Файл plugin.json не найден.", ""

    try:
        raw_bytes = manifest_path.read_bytes()
    except Exception as exc:
        return None, f"Не удалось прочитать plugin.json: {_format_exc(exc)}", ""

    encodings: list[str] = []
    if raw_bytes.startswith(codecs.BOM_UTF8):
        encodings = ["utf-8-sig", "utf-8"]
    elif raw_bytes.startswith(codecs.BOM_UTF16_LE) or raw_bytes.startswith(codecs.BOM_UTF16_BE):
        encodings = ["utf-16"]
    else:
        encodings = ["utf-8", "utf-8-sig", "utf-16", "cp1251"]

    last_decode_error = ""
    last_json_error = ""
    for encoding in encodings:
        try:
            raw = raw_bytes.decode(encoding)
        except Exception as exc:
            last_decode_error = _format_exc(exc)
            continue

        raw = raw.lstrip("\ufeff")
        try:
            data = json.loads(raw)
        except Exception as exc:
            last_json_error = _format_exc(exc)
            continue

        if not isinstance(data, dict):
            return None, "plugin.json должен содержать JSON-объект.", encoding
        return data, "", encoding

    if last_json_error:
        return None, f"Неверный JSON в plugin.json: {last_json_error}", ""
    if last_decode_error:
        return None, f"Не удалось декодировать plugin.json: {last_decode_error}", ""
    return None, "Не удалось прочитать plugin.json.", ""


def _load_manifest(plugin_dir: Path) -> Optional[dict[str, Any]]:
    data, _, _ = _load_manifest_detailed(plugin_dir)
    return data


def _decode_text_file(path: Path) -> tuple[str, str, str]:
    last_error = ""
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "utf-16"):
        try:
            data = path.read_text(encoding=encoding)
            return data.lstrip("\ufeff"), "", encoding
        except Exception as exc:
            last_error = _format_exc(exc)
    return "", last_error or "decode error", ""


def _scan_static_files(static_dir: Path) -> tuple[int, list[str]]:
    files: list[str] = []
    total = 0
    try:
        for item in sorted(static_dir.rglob("*")):
            if not item.is_file():
                continue
            total += 1
            rel = item.relative_to(static_dir).as_posix()
            if len(files) < 12:
                files.append(rel)
    except Exception:
        return total, files
    return total, files


def _scan_plugin_files(plugin_dir: Path) -> tuple[int, list[str]]:
    files: list[str] = []
    total = 0
    try:
        for item in sorted(plugin_dir.rglob("*")):
            if not item.is_file():
                continue
            if "__pycache__" in item.parts:
                continue
            total += 1
            rel = item.relative_to(plugin_dir).as_posix()
            if len(files) < 120:
                files.append(rel)
    except Exception:
        return total, files
    return total, files


def _find_entrypoint_path(plugin_dir: Path, entrypoint: Optional[str]) -> Optional[Path]:
    if not entrypoint:
        return None
    try:
        entrypoint = str(entrypoint).strip()
    except Exception:
        return None
    if not entrypoint:
        return None

    rel_path = Path(entrypoint)
    if rel_path.is_absolute():
        return rel_path.resolve() if rel_path.is_file() else None

    candidates = [rel_path]
    if rel_path.suffix:
        for ext in _ENTRYPOINT_EXTS:
            if ext != rel_path.suffix:
                candidates.append(rel_path.with_suffix(ext))
    else:
        for ext in _ENTRYPOINT_EXTS:
            candidates.append(rel_path.with_suffix(ext))

    for candidate in candidates:
        full_path = plugin_dir / candidate
        if full_path.is_file():
            return full_path.resolve()

    search_dir = plugin_dir / rel_path.parent
    if search_dir.is_dir():
        stem = rel_path.stem
        for ext in _ENTRYPOINT_EXTS:
            pattern = f"{stem}*{ext}"
            for found in sorted(search_dir.glob(pattern)):
                if found.is_file():
                    return found.resolve()

    return None


def _build_plugin_spec(
    plugin_dir: Path,
    data: dict[str, Any],
    *,
    source_type: str = _PLUGIN_SOURCE_INTEGRATED,
    base_dir: str = "",
) -> Optional[PluginSpec]:
    manifest_path = _safe_resolve(plugin_dir / _MANIFEST_FILE)
    plugin_id = _safe_plugin_id(str(data.get("id") or plugin_dir.name))
    if not plugin_id:
        return None
    name = _normalize_menu_label(data.get("name") or plugin_id, plugin_id)
    version = _normalize_text(data.get("version") or "1.0")
    description = _normalize_text(data.get("description") or "")
    menu_label = _normalize_menu_label(data.get("menu_label") or name, name)
    category = _normalize_category(data.get("category"))
    order = data.get("order")
    try:
        order_value = int(order)
    except Exception:
        order_value = 100

    roles = _normalize_roles(data.get("roles"))

    route_source = data.get("route") or data.get("route_base")
    if source_type == _PLUGIN_SOURCE_EXTERNAL:
        route_base = _external_plugin_route_base(plugin_id)
    else:
        route_base = _normalize_route_base(route_source, plugin_id)
    url = route_base.rstrip("/") + "/"

    entrypoint = data.get("entrypoint")
    entrypoint_required = bool(_normalize_text(entrypoint))
    entrypoint_hint = _normalize_text(entrypoint) or "plugin.py"
    entrypoint_path = _find_entrypoint_path(plugin_dir, entrypoint_hint)

    template = data.get("template") or "index.html"
    template_hint = str(template).strip() if template else "index.html"
    template_path = None
    if template_hint:
        template_path = _safe_resolve(plugin_dir / template_hint)
        if not template_path.is_file():
            template_path = None

    spec = PluginSpec(
        plugin_id=plugin_id,
        name=name,
        version=version,
        description=description,
        root=plugin_dir,
        entrypoint=entrypoint_path,
        entrypoint_hint=entrypoint_hint,
        template=template_path,
        menu_label=menu_label,
        category=category,
        order=order_value,
        roles=roles,
        route_base=route_base,
        url=url,
        manifest_path=manifest_path,
        source_type=source_type,
        template_hint=template_hint or "index.html",
        entrypoint_required=entrypoint_required,
        permission_view_name=_external_plugin_view_name(plugin_id) if source_type == _PLUGIN_SOURCE_EXTERNAL else "",
        menu_name=_external_plugin_menu_name(plugin_id) if source_type == _PLUGIN_SOURCE_EXTERNAL else "",
    )

    if source_type == _PLUGIN_SOURCE_EXTERNAL:
        _apply_external_plugin_runtime(spec, base_dir, data)

    _append_check(spec, "manifest", "plugin.json", manifest_path, "ok", "Манифест загружен.")

    if entrypoint_path:
        _append_check(
            spec,
            "entrypoint",
            "Entrypoint",
            entrypoint_path,
            "ok",
            f"Entrypoint найден: {entrypoint_hint}",
        )
        if entrypoint_path.suffix.lower() == ".py":
            _, decode_error, _ = _decode_text_file(entrypoint_path)
            if decode_error:
                _append_check(
                    spec,
                    "entrypoint_encoding",
                    "Кодировка entrypoint",
                    entrypoint_path,
                    "warning",
                    f"Не удалось проверить кодировку entrypoint: {decode_error}",
                )
    elif entrypoint_required:
        missing_path = plugin_dir / entrypoint_hint
        if _is_compiled_runtime():
            _append_check(
                spec,
                "entrypoint",
                "Entrypoint",
                missing_path,
                "ok",
                (
                    "Файл entrypoint не найден на диске, но это допустимо для EXE/compiled-сборки. "
                    "Будет проверена загрузка модуля через import."
                ),
            )
        else:
            _log_plugin_warning(
                spec,
                f"Entrypoint не найден на диске: {entrypoint_hint}. Будет попытка загрузки через import.",
            )
            _append_check(
                spec,
                "entrypoint",
                "Entrypoint",
                missing_path,
                "warning",
                f"Файл не найден: {entrypoint_hint}. Ожидается импорт модуля.",
            )
    else:
        _log_plugin_warning(spec, "Entrypoint не указан. Будет попытка статической загрузки.")
        _append_check(
            spec,
            "entrypoint",
            "Entrypoint",
            plugin_dir / entrypoint_hint,
            "warning",
            "Entrypoint не указан, используется fallback.",
        )

    if template_hint and template_path:
        template_text, template_error, template_encoding = _decode_text_file(template_path)
        if template_text:
            _append_check(
                spec,
                "template",
                "Шаблон",
                template_path,
                "ok",
                f"Шаблон найден ({template_encoding}).",
            )
        else:
            _log_plugin_error(spec, f"Не удалось прочитать шаблон: {template_error}")
            _append_check(
                spec,
                "template",
                "Шаблон",
                template_path,
                "error",
                f"Не удалось прочитать шаблон: {template_error}",
            )
    elif template_hint:
        _log_plugin_error(spec, f"Шаблон не найден: {template_hint}")
        _append_check(
            spec,
            "template",
            "Шаблон",
            plugin_dir / template_hint,
            "error",
            f"Файл шаблона не найден: {template_hint}",
        )

    static_dir = _safe_resolve(plugin_dir / "static")
    if static_dir.is_dir():
        static_total, static_preview = _scan_static_files(static_dir)
        if static_total > 0:
            preview_suffix = f" ({', '.join(static_preview[:4])})" if static_preview else ""
            _append_check(
                spec,
                "static",
                "Статика",
                static_dir,
                "ok",
                f"Найдено файлов статики: {static_total}{preview_suffix}",
            )
        else:
            _log_plugin_warning(spec, "Папка static есть, но в ней нет файлов.")
            _append_check(
                spec,
                "static",
                "Статика",
                static_dir,
                "warning",
                "Папка static пуста.",
            )
    else:
        _log_plugin_warning(spec, "Папка static не найдена.")
        _append_check(
            spec,
            "static",
            "Статика",
            static_dir,
            "warning",
            "Папка static не найдена.",
        )

    return spec


def discover_plugins(base_dir: str, resource_root: Optional[Path]) -> list[PluginSpec]:
    plugins: list[PluginSpec] = []
    seen: set[str] = set()
    for source_type, root in _candidate_plugin_sources(base_dir, resource_root):
        if not root.is_dir():
            continue
        for item in sorted(root.iterdir()):
            if not item.is_dir():
                continue
            if not _is_plugin_dir_candidate(item):
                continue
            manifest, manifest_error, manifest_encoding = _load_manifest_detailed(item)
            if not manifest:
                if manifest_error and manifest_error != "Файл plugin.json не найден.":
                    try:
                        _PLUGIN_LOGGER.warning("plugin dir %s: %s", item, manifest_error)
                    except Exception:
                        pass
                continue
            spec = _build_plugin_spec(item, manifest, source_type=source_type, base_dir=base_dir)
            if not spec:
                try:
                    _PLUGIN_LOGGER.warning("plugin dir %s ignored: invalid id in plugin.json", item)
                except Exception:
                    pass
                continue
            spec.discovered_root = _safe_resolve(root)
            spec.manifest_encoding = manifest_encoding
            if spec.plugin_id in seen:
                try:
                    _PLUGIN_LOGGER.warning("plugin %s ignored (duplicate)", spec.plugin_id)
                except Exception:
                    pass
                continue
            seen.add(spec.plugin_id)
            plugins.append(spec)

    plugins.sort(key=lambda p: (p.order, p.name.lower()))
    return plugins


def _register_plugin_blueprint(app, plugin: PluginSpec) -> None:
    static_dir = _safe_resolve(plugin.root / "static")
    if not static_dir.is_dir():
        _append_check(
            plugin,
            "blueprint",
            "Blueprint static",
            static_dir,
            "warning",
            "Blueprint статики не зарегистрирован (папка static отсутствует).",
        )
        return
    bp_name = f"web_plugin_{plugin.plugin_id}"
    url_prefix = f"/plugins/{plugin.plugin_id}/static"
    existing = getattr(app, "blueprints", {}).get(bp_name)
    if existing is not None:
        plugin.blueprint = existing
        _append_check(
            plugin,
            "blueprint",
            "Blueprint static",
            static_dir,
            "ok",
            f"Статика уже зарегистрирована по префиксу: {url_prefix}",
        )
        return
    blueprint = Blueprint(
        bp_name,
        __name__,
        static_folder=str(static_dir),
        static_url_path=url_prefix,
    )
    app.register_blueprint(blueprint)
    plugin.blueprint = blueprint
    _append_check(
        plugin,
        "blueprint",
        "Blueprint static",
        static_dir,
        "ok",
        f"Статика зарегистрирована по префиксу: {url_prefix}",
    )


def _module_candidates(plugin: PluginSpec) -> list[str]:
    stem = "plugin"
    if plugin.entrypoint_hint:
        try:
            stem = Path(plugin.entrypoint_hint).stem or stem
        except Exception:
            stem = "plugin"
    names: list[str] = []
    for value in (plugin.root.name, plugin.plugin_id, plugin.plugin_id.replace("-", "_")):
        if value and value not in names:
            names.append(value)
    candidates: list[str] = []
    for name in names:
        candidates.append(f"web_dashboard.web_plugins.{name}.{stem}")
        candidates.append(f"web_dashboard.web_plugins.{name}")
        candidates.append(f"moduls.web_dashboard.web_plugins.{name}.{stem}")
        candidates.append(f"moduls.web_dashboard.web_plugins.{name}")
    return candidates


def _load_plugin_module(plugin: PluginSpec) -> Optional[Any]:
    module_name = f"web_plugin_{plugin.plugin_id}"
    last_error: Exception | None = None
    runtime_compiled = _is_compiled_runtime()
    missing_entrypoint_path = plugin.root / (plugin.entrypoint_hint or "plugin.py")
    has_module_error_check = False
    if plugin.source_type == _PLUGIN_SOURCE_EXTERNAL:
        if plugin.install_state != INSTALL_STATE_INSTALLED or not plugin.venv_site_packages:
            _append_check(
                plugin,
                "module_load_external",
                "Загрузка модуля",
                plugin.venv_dir or (plugin.root / "venv"),
                "warning" if plugin.install_state != INSTALL_STATE_ERROR else "error",
                plugin.install_message or "Внешнее расширение ещё не установлено.",
            )
            return None
        _extend_plugin_import_paths(plugin)

    try:
        if plugin.entrypoint:
            spec = importlib.util.spec_from_file_location(module_name, plugin.entrypoint)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                plugin.module_loaded = True
                plugin.module_origin = str(plugin.entrypoint)
                _append_check(
                    plugin,
                    "module_load",
                    "Загрузка модуля",
                    plugin.entrypoint,
                    "ok",
                    "Модуль успешно загружен из entrypoint.",
                )
                return module
    except Exception as exc:
        last_error = exc
        if plugin.entrypoint:
            _append_check(
                plugin,
                "module_load",
                "Загрузка модуля",
                plugin.entrypoint,
                "error",
                f"Ошибка импорта entrypoint: {_format_exc(exc)}",
            )
            has_module_error_check = True

    for candidate in _module_candidates(plugin):
        try:
            module = importlib.import_module(candidate)
            try:
                _PLUGIN_LOGGER.info("plugin %s: module loaded via import %s", plugin.plugin_id, candidate)
            except Exception:
                pass
            sys.modules[module_name] = module
            plugin.module_loaded = True
            module_path = getattr(module, "__file__", "") or candidate
            plugin.module_origin = str(module_path)
            _append_check(
                plugin,
                "module_load",
                "Загрузка модуля",
                module_path,
                "ok",
                f"Модуль загружен через import: {candidate}",
            )
            if plugin.entrypoint_required and not plugin.entrypoint:
                if runtime_compiled:
                    _append_check(
                        plugin,
                        "entrypoint_runtime",
                        "Entrypoint (runtime)",
                        missing_entrypoint_path,
                        "ok",
                        "Entrypoint отсутствует на диске, но модуль успешно загружен из bundled-пакета.",
                    )
                else:
                    _append_check(
                        plugin,
                        "entrypoint_runtime",
                        "Entrypoint (runtime)",
                        missing_entrypoint_path,
                        "warning",
                        "Entrypoint отсутствует на диске, модуль загружен через import.",
                    )
            return module
        except Exception as exc:
            last_error = exc

    if last_error:
        if plugin.entrypoint_required:
            _log_plugin_error(plugin, f"Не удалось загрузить модуль: {_format_exc(last_error)}")
            if not has_module_error_check:
                _append_check(
                    plugin,
                    "module_load",
                    "Загрузка модуля",
                    plugin.entrypoint or missing_entrypoint_path,
                    "error",
                    f"Импорт модуля завершился ошибкой: {_format_exc(last_error)}",
                )
        else:
            _log_plugin_warning(plugin, f"Не удалось загрузить модуль: {_format_exc(last_error)}")
            if not has_module_error_check:
                _append_check(
                    plugin,
                    "module_load",
                    "Загрузка модуля",
                    plugin.entrypoint or missing_entrypoint_path,
                    "warning",
                    f"Импорт модуля завершился ошибкой: {_format_exc(last_error)}",
                )
    elif plugin.entrypoint_hint:
        if plugin.entrypoint_required:
            _log_plugin_error(plugin, f"Entrypoint не найден: {plugin.entrypoint_hint}")
            _append_check(
                plugin,
                "entrypoint_runtime",
                "Entrypoint (runtime)",
                missing_entrypoint_path,
                "error",
                "Entrypoint не найден и модуль не удалось импортировать.",
            )
        else:
            _log_plugin_warning(plugin, f"Entrypoint не найден: {plugin.entrypoint_hint}")
            _append_check(
                plugin,
                "entrypoint_runtime",
                "Entrypoint (runtime)",
                missing_entrypoint_path,
                "warning",
                "Entrypoint не найден.",
            )
    return None


def _plugin_static_url(plugin: PluginSpec, filename: str) -> str:
    if not plugin.blueprint:
        return ""
    try:
        return url_for(f"{plugin.blueprint.name}.static", filename=filename)
    except Exception:
        return ""


def _read_template(plugin: PluginSpec) -> str:
    if not plugin.template:
        return ""
    try:
        return plugin.template.read_text(encoding="utf-8")
    except Exception:
        try:
            return plugin.template.read_text(encoding="utf-8-sig")
        except Exception:
            return ""


def _make_static_view(plugin: PluginSpec):
    template_source = _read_template(plugin)

    class _PluginView(BaseView):
        route_base = plugin.route_base
        base_permissions = ["can_list"]
        plugin_id = plugin.plugin_id
        plugin_name = plugin.name

        @expose("/")
        @has_access
        def list(self):
            if not template_source:
                return render_template_string(
                    _MISSING_TEMPLATE,
                    plugin=plugin,
                    base_template=self.appbuilder.base_template,
                    appbuilder=self.appbuilder,
                    current_app=current_app,
                )
            return render_template_string(
                template_source,
                plugin=plugin,
                static_url=_plugin_static_url(plugin, "").rstrip("/"),
                run_url="",
                plugin_static=lambda filename: _plugin_static_url(plugin, filename),
                base_template=self.appbuilder.base_template,
                appbuilder=self.appbuilder,
                current_app=current_app,
            )

    _PluginView.__name__ = f"Plugin{plugin.plugin_id.title()}View"
    return _PluginView


def _resolve_views(module: Any, plugin: PluginSpec, appbuilder, app) -> list[type]:
    if module is None:
        _append_check(
            plugin,
            "views",
            "Поиск View",
            plugin.root,
            "warning",
            "Модуль не загружен. Используется fallback-view (если доступен шаблон).",
        )
        return []

    if hasattr(module, "register"):
        try:
            result = module.register(appbuilder, app, plugin)
        except Exception as exc:
            _log_plugin_error(plugin, f"Ошибка register(): {_format_exc(exc)}")
            _append_check(
                plugin,
                "register",
                "register()",
                plugin.module_origin or plugin.root,
                "error",
                f"register() завершился ошибкой: {_format_exc(exc)}",
            )
            return []
        if result:
            if isinstance(result, (list, tuple)):
                valid = [item for item in result if isinstance(item, type)]
                invalid_count = len(result) - len(valid)
                if invalid_count > 0:
                    _log_plugin_warning(plugin, f"register() вернул {invalid_count} некорректных элементов.")
                    _append_check(
                        plugin,
                        "register",
                        "register()",
                        plugin.module_origin or plugin.root,
                        "warning",
                        f"register() вернул некорректные элементы: {invalid_count}",
                    )
                if valid:
                    _append_check(
                        plugin,
                        "register",
                        "register()",
                        plugin.module_origin or plugin.root,
                        "ok",
                        f"register() вернул view-классы: {len(valid)}",
                    )
                return valid
            if isinstance(result, type):
                _append_check(
                    plugin,
                    "register",
                    "register()",
                    plugin.module_origin or plugin.root,
                    "ok",
                    "register() вернул один view-класс.",
                )
                return [result]

    for attr in ("VIEW", "VIEW_CLASS", "View"):
        value = getattr(module, attr, None)
        if isinstance(value, type):
            _append_check(
                plugin,
                "view_export",
                "Экспорт View",
                plugin.module_origin or plugin.root,
                "ok",
                f"Найден экспорт класса в атрибуте {attr}.",
            )
            return [value]

    _log_plugin_warning(plugin, "В модуле не найдено view-классов.")
    _append_check(
        plugin,
        "view_export",
        "Экспорт View",
        plugin.module_origin or plugin.root,
        "warning",
        "В модуле нет register()/VIEW/VIEW_CLASS/View.",
    )
    return []


def _view_menu_label(view: type, default: str) -> str:
    label = getattr(view, "menu_label", None) or getattr(view, "__menu_label__", None)
    return _normalize_menu_label(label, default)


def _grant_permissions(
    appbuilder,
    view_name: str,
    roles: Iterable[str],
    menu_label: str | None = None,
    category_label: str | None = None,
    permissions: Iterable[str] | None = None,
) -> None:
    try:
        sm = appbuilder.sm
    except Exception:
        return
    try:
        session = sm.get_session() if callable(getattr(sm, "get_session", None)) else sm.get_session
    except Exception:
        session = None
    normalized_permissions: list[str] = []
    for permission_name in permissions or ("can_list",):
        normalized = _normalize_permission_name(str(permission_name or ""))
        if normalized not in normalized_permissions:
            normalized_permissions.append(normalized)
    try:
        for permission_name in normalized_permissions:
            sm.add_permission_view_menu(permission_name, view_name)
        if menu_label:
            sm.add_permissions_menu(menu_label)
        if category_label:
            sm.add_permissions_menu(category_label)
    except Exception:
        pass
    for role_name in roles:
        try:
            role = sm.find_role(role_name)
            if not role:
                continue
            for permission_name in normalized_permissions:
                perm_view = sm.find_permission_view_menu(permission_name, view_name)
                if perm_view:
                    sm.add_permission_role(role, perm_view)
            if menu_label:
                menu_perm = sm.find_permission_view_menu("menu_access", menu_label)
                if menu_perm:
                    sm.add_permission_role(role, menu_perm)
            if category_label:
                category_perm = sm.find_permission_view_menu("menu_access", category_label)
                if category_perm:
                    sm.add_permission_role(role, category_perm)
        except Exception:
            continue
    try:
        if session is not None:
            session.commit()
    except Exception:
        pass


def _register_dynamic_security_labels(plugin: PluginSpec) -> None:
    try:
        from . import security as security_module
    except Exception:
        return
    try:
        security_module._VIEW_LABELS[_EXTERNAL_PLUGIN_HOST_VIEW_NAME] = "Внешние расширения"
        if plugin.permission_view_name:
            security_module._VIEW_LABELS[plugin.permission_view_name] = plugin.menu_label or plugin.name
        if plugin.menu_name:
            security_module._VIEW_LABELS[plugin.menu_name] = plugin.menu_label or plugin.name
    except Exception:
        pass


def _remove_menu_item(appbuilder, item_name: str) -> None:
    menu = getattr(appbuilder, "menu", None)
    if not menu or not item_name:
        return
    try:
        menu.menu = [item for item in menu.menu if getattr(item, "name", None) != item_name]
    except Exception:
        pass
    for category in getattr(menu, "menu", []) or []:
        childs = getattr(category, "childs", None)
        if not isinstance(childs, list):
            continue
        category.childs = [item for item in childs if getattr(item, "name", None) != item_name]


def _upsert_external_plugin_menu(appbuilder, plugin: PluginSpec) -> None:
    plugin.menu_name = plugin.menu_name or _external_plugin_menu_name(plugin.plugin_id)
    plugin.permission_view_name = plugin.permission_view_name or _external_plugin_view_name(plugin.plugin_id)
    _register_dynamic_security_labels(plugin)
    _remove_menu_item(appbuilder, plugin.menu_name)
    appbuilder.add_link(
        plugin.menu_name,
        plugin.url,
        label=plugin.menu_label,
        category=plugin.category,
        category_label=plugin.category,
        baseview=ExternalPluginMenuProxy(plugin.permission_view_name),
    )
    plugin.menu_registered = True


def _runtime_permission_names(runtime: Any) -> list[str]:
    names: list[str] = []
    if hasattr(runtime, "permission_names"):
        try:
            raw_names = list(runtime.permission_names())
        except Exception:
            raw_names = []
        for item in raw_names:
            normalized = _normalize_permission_name(str(item or ""))
            if normalized not in names:
                names.append(normalized)
    if "can_list" not in names:
        names.insert(0, "can_list")
    return names or ["can_list"]


def _resolve_external_runtime(module: Any, plugin: PluginSpec, appbuilder, app) -> Any | None:
    if module is not None and hasattr(module, "register_hosted"):
        try:
            runtime = module.register_hosted(appbuilder, app, plugin)
        except Exception as exc:
            _log_plugin_error(plugin, f"Ошибка register_hosted(): {_format_exc(exc)}")
            _append_check(
                plugin,
                "register_hosted",
                "register_hosted()",
                plugin.module_origin or plugin.root,
                "error",
                f"register_hosted() завершился ошибкой: {_format_exc(exc)}",
            )
            return None
        if runtime is not None and hasattr(runtime, "dispatch"):
            plugin.runtime_mode = "hosted"
            plugin.runtime_label = type(runtime).__name__
            _append_check(
                plugin,
                "register_hosted",
                "register_hosted()",
                plugin.module_origin or plugin.root,
                "ok",
                f"Внешнее расширение использует hosted-рантайм: {plugin.runtime_label}",
            )
            return runtime
        if runtime is not None:
            _log_plugin_warning(plugin, "register_hosted() вернул объект без метода dispatch().")
            _append_check(
                plugin,
                "register_hosted",
                "register_hosted()",
                plugin.module_origin or plugin.root,
                "warning",
                "register_hosted() вернул неподдерживаемый объект.",
            )

    if module is not None:
        views = _resolve_views(module, plugin, appbuilder, app)
        if views:
            if len(views) > 1:
                _log_plugin_warning(plugin, "У внешнего расширения найдено несколько view; используется первая.")
                _append_check(
                    plugin,
                    "external_views",
                    "View внешнего расширения",
                    plugin.module_origin or plugin.root,
                    "warning",
                    f"Найдено view-классов: {len(views)}. Для hot-load используется первый.",
                )
            view = views[0]
            try:
                runtime = LegacyViewHostedRuntime(plugin, module, view, appbuilder)
            except Exception as exc:
                _log_plugin_error(plugin, f"Не удалось подготовить proxy view: {_format_exc(exc)}")
                _append_check(
                    plugin,
                    "external_proxy",
                    "Proxy view",
                    plugin.module_origin or plugin.root,
                    "error",
                    f"Не удалось подготовить proxy view: {_format_exc(exc)}",
                )
                return None
            plugin.runtime_mode = "legacy_proxy"
            plugin.runtime_label = view.__name__
            plugin.resolved_views = [view.__name__]
            _append_check(
                plugin,
                "external_proxy",
                "Proxy view",
                plugin.module_origin or plugin.root,
                "ok",
                f"View {view.__name__} подключен через общий диспетчер.",
            )
            return runtime

    if plugin.template and not plugin.entrypoint_required:
        plugin.fallback_view_used = True
        plugin.runtime_mode = "template"
        plugin.runtime_label = "TemplateHostedRuntime"
        _append_check(
            plugin,
            "template_runtime",
            "Template runtime",
            plugin.template,
            "ok",
            "Используется template-only режим через общий диспетчер.",
        )
        return TemplateHostedRuntime(plugin)
    return None


def _activate_external_runtime(appbuilder, app, plugin: PluginSpec, runtime: Any) -> PluginSpec:
    registry = app.config.setdefault("WEB_EXTERNAL_PLUGIN_RUNTIMES", {})
    plugin.permission_view_name = plugin.permission_view_name or _external_plugin_view_name(plugin.plugin_id)
    plugin.menu_name = plugin.menu_name or _external_plugin_menu_name(plugin.plugin_id)
    plugin.url = plugin.route_base.rstrip("/") + "/"

    _grant_permissions(
        appbuilder,
        _EXTERNAL_PLUGIN_HOST_VIEW_NAME,
        plugin.roles,
        permissions=("can_list",),
    )
    _upsert_external_plugin_menu(appbuilder, plugin)
    _grant_permissions(
        appbuilder,
        plugin.permission_view_name,
        plugin.roles,
        menu_label=plugin.menu_name,
        category_label=plugin.category,
        permissions=_runtime_permission_names(runtime),
    )

    registry[plugin.plugin_id] = runtime
    plugin.menu_registered = True
    plugin.registered_views = [plugin.permission_view_name]
    if not plugin.runtime_label:
        plugin.runtime_label = type(runtime).__name__
    if not plugin.resolved_views:
        plugin.resolved_views = [plugin.runtime_label]
    _append_check(
        plugin,
        "dispatcher",
        "Диспетчер",
        plugin.route_base,
        "ok",
        f"Внешнее расширение подключено без перезапуска через маршрут {plugin.route_base}/",
    )
    _append_check(
        plugin,
        "menu",
        "Пункт меню",
        plugin.menu_name or plugin.menu_label,
        "ok",
        f"Пункт меню зарегистрирован в категории «{plugin.category}».",
    )
    return plugin


def get_external_plugin_spec(app, plugin_id: str) -> PluginSpec | None:
    for plugin in app.config.get("WEB_PLUGINS") or []:
        if getattr(plugin, "plugin_id", "") != plugin_id:
            continue
        if getattr(plugin, "source_type", "") != _PLUGIN_SOURCE_EXTERNAL:
            continue
        return plugin
    return None


def get_external_plugin_runtime(app, plugin_id: str) -> Any | None:
    registry = app.config.get("WEB_EXTERNAL_PLUGIN_RUNTIMES") or {}
    return registry.get(plugin_id)


def _finalize_plugin_state(plugin: PluginSpec) -> PluginSpec:
    if plugin.errors:
        plugin.load_state = "error"
    elif plugin.warnings:
        plugin.load_state = "warning"
    elif plugin.registered_views or plugin.menu_registered:
        plugin.load_state = "loaded"
    else:
        plugin.load_state = "not_loaded"
    return plugin


def _status_payload(errors: list[str], warnings: list[str], loaded: bool) -> tuple[str, str, str]:
    if errors:
        return "error", "Ошибка", "bad"
    if warnings:
        return "warning", "Предупреждение", "warn"
    if loaded:
        return "ok", "Загружено", "ok"
    return "warning", "Не загружено", "warn"


def _entry_append_check(entry: dict[str, Any], key: str, label: str, path: Path | str, level: str, message: str) -> None:
    entry.setdefault("checks", []).append(
        {
            "key": key,
            "label": label,
            "path": str(path),
            "level": level,
            "message": message,
            "chip": _chip_by_level(level),
        }
    )


def _entry_append_issue(entry: dict[str, Any], message: str, *, level: str = "error") -> None:
    text = _normalize_text(message)
    if not text:
        return
    if level == "warning":
        entry.setdefault("warnings", []).append(text)
    else:
        entry.setdefault("errors", []).append(text)


def _install_state_payload(state: str) -> tuple[str, str]:
    if state == INSTALL_STATE_INSTALLED:
        return "Установлено", "ok"
    if state == INSTALL_STATE_ERROR:
        return "Ошибка среды", "bad"
    if state == INSTALL_STATE_NOT_INSTALLED:
        return "Не установлено", "warn"
    return "Не требуется", "ok"


def _entry_apply_external_runtime(
    entry: dict[str, Any],
    *,
    base_dir: str,
    plugin_dir: Path,
    manifest: dict[str, Any] | None = None,
) -> None:
    info = collect_external_plugin_installation_info(base_dir, plugin_dir, manifest)
    state = str(info.get("state") or INSTALL_STATE_NOT_INSTALLED)
    state_label, state_chip = _install_state_payload(state)
    entry["source_type"] = _PLUGIN_SOURCE_EXTERNAL
    entry["source_label"] = _plugin_source_label(_PLUGIN_SOURCE_EXTERNAL)
    entry["dependencies"] = list(info.get("dependencies") or [])
    entry["requirements_path"] = str(info.get("requirements_path") or "")
    entry["base_python"] = str(info.get("base_python") or "")
    entry["base_python_source"] = str(info.get("base_python_source") or "")
    entry["base_python_source_label"] = str(info.get("base_python_source_label") or "")
    entry["venv_dir"] = str(info.get("venv_dir") or "")
    entry["venv_python"] = str(info.get("venv_python") or "")
    entry["venv_pip"] = str(info.get("venv_pip") or "")
    entry["venv_site_packages"] = str(info.get("venv_site_packages") or "")
    entry["install_state"] = state
    entry["install_state_label"] = state_label
    entry["install_state_chip"] = state_chip
    entry["install_message"] = str(info.get("message") or "")
    entry["installed"] = bool(info.get("installed"))
    entry["install_action_label"] = (
        "Переустановить расширение" if entry["installed"] else "Установить расширение"
    )
    entry["can_install"] = True

    python_path = entry["base_python"] or str(plugin_dir / "python")
    if entry["base_python"]:
        _entry_append_check(
            entry,
            "external_python",
            "Python расширения",
            python_path,
            "ok",
            f"Используется интерпретатор: {entry['base_python_source_label'] or 'Python'}",
        )
    else:
        _entry_append_issue(entry, "Не найден Python для установки внешнего расширения.")
        _entry_append_check(
            entry,
            "external_python",
            "Python расширения",
            python_path,
            "error",
            "Не найден Python для установки внешнего расширения.",
        )

    if entry["dependencies"]:
        _entry_append_check(
            entry,
            "external_dependencies",
            "Зависимости",
            entry["requirements_path"] or plugin_dir,
            "ok",
            f"Заявлено pip-зависимостей: {len(entry['dependencies'])}",
        )
    elif entry["requirements_path"]:
        _entry_append_check(
            entry,
            "external_dependencies",
            "Зависимости",
            entry["requirements_path"],
            "ok",
            "Используется requirements.txt внешнего расширения.",
        )
    else:
        _entry_append_issue(entry, "Внешнее расширение не заявило pip-зависимости.", level="warning")
        _entry_append_check(
            entry,
            "external_dependencies",
            "Зависимости",
            plugin_dir,
            "warning",
            "Дополнительные pip-зависимости не заявлены.",
        )

    level = "ok" if entry["installed"] else ("error" if state == INSTALL_STATE_ERROR else "warning")
    if state == INSTALL_STATE_ERROR:
        _entry_append_issue(entry, entry["install_message"] or "Среда внешнего расширения недоступна.")
    elif state == INSTALL_STATE_NOT_INSTALLED:
        _entry_append_issue(entry, entry["install_message"] or "Среда внешнего расширения ещё не создана.", level="warning")

    _entry_append_check(
        entry,
        "external_venv",
        "Среда расширения",
        entry["venv_dir"] or (plugin_dir / "venv"),
        level,
        entry["install_message"] or "Среда внешнего расширения ещё не создана.",
    )


def _entry_base(plugin_dir: Path, source_root: Optional[Path]) -> dict[str, Any]:
    return {
        "plugin_id": plugin_dir.name,
        "name": plugin_dir.name,
        "version": "-",
        "description": "",
        "category": _DEFAULT_CATEGORY,
        "menu_label": plugin_dir.name,
        "roles": [],
        "url": "",
        "route_base": "",
        "root": str(_safe_resolve(plugin_dir)),
        "source_root": str(_safe_resolve(source_root)) if source_root else "",
        "source_type": _PLUGIN_SOURCE_INTEGRATED,
        "source_label": _plugin_source_label(_PLUGIN_SOURCE_INTEGRATED),
        "source": "filesystem",
        "manifest_path": str(_safe_resolve(plugin_dir / _MANIFEST_FILE)),
        "manifest_encoding": "",
        "entrypoint_hint": "",
        "entrypoint_path": "",
        "template_hint": "index.html",
        "template_path": "",
        "module_origin": "",
        "module_loaded": False,
        "fallback_view_used": False,
        "resolved_views": [],
        "registered_views": [],
        "load_state": "not_loaded",
        "errors": [],
        "warnings": [],
        "checks": [],
        "files_total": 0,
        "files_preview": [],
        "files_truncated": False,
        "can_launch": False,
        "status": "warning",
        "status_label": "Не загружено",
        "status_chip": "warn",
        "dependencies": [],
        "requirements_path": "",
        "base_python": "",
        "base_python_source": "",
        "base_python_source_label": "",
        "venv_dir": "",
        "venv_python": "",
        "venv_pip": "",
        "venv_site_packages": "",
        "install_state": INSTALL_STATE_NOT_APPLICABLE,
        "install_state_label": "Не требуется",
        "install_state_chip": "ok",
        "install_message": "",
        "installed": False,
        "install_action_label": "",
        "can_install": False,
        "permission_view_name": "",
        "menu_name": "",
        "menu_registered": False,
        "runtime_mode": "",
        "runtime_label": "",
    }


def _entry_from_plugin(plugin: PluginSpec) -> dict[str, Any]:
    source_root = plugin.discovered_root or plugin.root.parent
    entry = _entry_base(plugin.root, source_root)
    entry.update(
        {
            "plugin_id": plugin.plugin_id,
            "name": plugin.name,
            "version": plugin.version,
            "description": plugin.description,
            "category": plugin.category,
            "menu_label": plugin.menu_label,
            "roles": list(plugin.roles or []),
            "url": plugin.url,
            "route_base": plugin.route_base,
            "source_type": plugin.source_type,
            "source_label": _plugin_source_label(plugin.source_type),
            "source": "loaded",
            "manifest_path": str(plugin.manifest_path),
            "manifest_encoding": plugin.manifest_encoding,
            "entrypoint_hint": plugin.entrypoint_hint or "",
            "entrypoint_path": str(plugin.entrypoint) if plugin.entrypoint else "",
            "template_hint": plugin.template_hint or "index.html",
            "template_path": str(plugin.template) if plugin.template else "",
            "module_origin": plugin.module_origin,
            "module_loaded": bool(plugin.module_loaded),
            "fallback_view_used": bool(plugin.fallback_view_used),
            "resolved_views": list(plugin.resolved_views or []),
            "registered_views": list(plugin.registered_views or []),
            "load_state": plugin.load_state,
            "dependencies": list(plugin.dependencies or []),
            "requirements_path": str(plugin.requirements_path) if plugin.requirements_path else "",
            "base_python": str(plugin.base_python) if plugin.base_python else "",
            "base_python_source": plugin.base_python_source,
            "base_python_source_label": plugin.base_python_source_label,
            "venv_dir": str(plugin.venv_dir) if plugin.venv_dir else "",
            "venv_python": str(plugin.venv_python) if plugin.venv_python else "",
            "venv_pip": str(plugin.venv_pip) if plugin.venv_pip else "",
            "venv_site_packages": str(plugin.venv_site_packages) if plugin.venv_site_packages else "",
            "install_state": plugin.install_state,
            "install_message": plugin.install_message,
            "installed": bool(plugin.installed),
            "permission_view_name": plugin.permission_view_name,
            "menu_name": plugin.menu_name,
            "menu_registered": bool(plugin.menu_registered),
            "runtime_mode": plugin.runtime_mode,
            "runtime_label": plugin.runtime_label,
            "errors": list(plugin.errors or []),
            "warnings": list(plugin.warnings or []),
            "checks": [
                {
                    "key": check.key,
                    "label": check.label,
                    "path": check.path,
                    "level": check.level,
                    "message": check.message,
                    "chip": check.chip,
                }
                for check in (plugin.checks or [])
            ],
        }
    )

    state_label, state_chip = _install_state_payload(entry["install_state"])
    entry["install_state_label"] = state_label
    entry["install_state_chip"] = state_chip
    entry["install_action_label"] = (
        "Переустановить расширение"
        if entry["source_type"] == _PLUGIN_SOURCE_EXTERNAL and entry["installed"]
        else ("Установить расширение" if entry["source_type"] == _PLUGIN_SOURCE_EXTERNAL else "")
    )
    entry["can_install"] = entry["source_type"] == _PLUGIN_SOURCE_EXTERNAL

    files_total, files_preview = _scan_plugin_files(plugin.root)
    entry["files_total"] = files_total
    entry["files_preview"] = files_preview
    entry["files_truncated"] = files_total > len(files_preview)

    loaded = bool(entry["registered_views"])
    status, label, chip = _status_payload(entry["errors"], entry["warnings"], loaded)
    entry["status"] = status
    entry["status_label"] = label
    entry["status_chip"] = chip
    entry["can_launch"] = bool(entry["url"]) and loaded and not entry["errors"]
    return entry


def _entry_from_directory(plugin_dir: Path, source_root: Path, base_dir: str) -> dict[str, Any]:
    entry = _entry_base(plugin_dir, source_root)
    source_type = _source_type_for_root(base_dir, source_root)
    entry["source_type"] = source_type
    entry["source_label"] = _plugin_source_label(source_type)
    manifest_path = plugin_dir / _MANIFEST_FILE
    manifest, manifest_error, manifest_encoding = _load_manifest_detailed(plugin_dir)
    if not manifest:
        _entry_append_issue(entry, manifest_error or "Не удалось прочитать plugin.json.")
        _entry_append_check(
            entry,
            "manifest",
            "plugin.json",
            manifest_path,
            "error",
            manifest_error or "Не удалось прочитать plugin.json.",
        )
        files_total, files_preview = _scan_plugin_files(plugin_dir)
        entry["files_total"] = files_total
        entry["files_preview"] = files_preview
        entry["files_truncated"] = files_total > len(files_preview)
        status, label, chip = _status_payload(entry["errors"], entry["warnings"], False)
        entry["status"] = status
        entry["status_label"] = label
        entry["status_chip"] = chip
        return entry

    entry["manifest_encoding"] = manifest_encoding
    if source_type == _PLUGIN_SOURCE_EXTERNAL:
        _entry_apply_external_runtime(entry, base_dir=base_dir, plugin_dir=plugin_dir, manifest=manifest)
    _entry_append_check(
        entry,
        "manifest",
        "plugin.json",
        manifest_path,
        "ok",
        f"Манифест прочитан ({manifest_encoding or 'auto'}).",
    )

    raw_id = str(manifest.get("id") or plugin_dir.name)
    plugin_id = _safe_plugin_id(raw_id)
    if not plugin_id:
        _entry_append_issue(entry, f"Некорректный ID расширения: {raw_id!r}")
        _entry_append_check(
            entry,
            "plugin_id",
            "ID",
            manifest_path,
            "error",
            f"ID {raw_id!r} не соответствует формату [a-z0-9_-].",
        )
    else:
        entry["plugin_id"] = plugin_id

    entry["name"] = _normalize_menu_label(manifest.get("name") or entry["plugin_id"], entry["plugin_id"])
    entry["version"] = _normalize_text(manifest.get("version") or "1.0")
    entry["description"] = _normalize_text(manifest.get("description") or "")
    entry["menu_label"] = _normalize_menu_label(manifest.get("menu_label") or entry["name"], entry["name"])
    entry["category"] = _normalize_category(manifest.get("category"))
    entry["roles"] = _normalize_roles(manifest.get("roles"))
    if source_type == _PLUGIN_SOURCE_EXTERNAL:
        entry["route_base"] = _external_plugin_route_base(entry["plugin_id"])
        entry["permission_view_name"] = _external_plugin_view_name(entry["plugin_id"])
        entry["menu_name"] = _external_plugin_menu_name(entry["plugin_id"])
    else:
        entry["route_base"] = _normalize_route_base(manifest.get("route") or manifest.get("route_base"), entry["plugin_id"])
    entry["url"] = entry["route_base"].rstrip("/") + "/"

    entrypoint_raw = _normalize_text(manifest.get("entrypoint"))
    entrypoint_required = bool(entrypoint_raw)
    entrypoint_hint = entrypoint_raw or "plugin.py"
    entry["entrypoint_hint"] = entrypoint_hint
    entrypoint_path = _find_entrypoint_path(plugin_dir, entrypoint_hint)
    runtime_compiled = _is_compiled_runtime()
    if entrypoint_path:
        entry["entrypoint_path"] = str(entrypoint_path)
        _entry_append_check(
            entry,
            "entrypoint",
            "Entrypoint",
            entrypoint_path,
            "ok",
            f"Файл найден: {entrypoint_hint}",
        )
    elif entrypoint_required:
        if runtime_compiled:
            _entry_append_issue(
                entry,
                (
                    f"Entrypoint не найден на диске: {entrypoint_hint}. "
                    "Для EXE/compiled это может быть нормой; нужна проверка реального импорта при запуске."
                ),
                level="warning",
            )
            _entry_append_check(
                entry,
                "entrypoint",
                "Entrypoint",
                plugin_dir / entrypoint_hint,
                "warning",
                (
                    f"Файл не найден: {entrypoint_hint}. "
                    "В compiled-режиме модуль может быть встроен в EXE."
                ),
            )
        else:
            _entry_append_issue(entry, f"Entrypoint не найден: {entrypoint_hint}")
            _entry_append_check(
                entry,
                "entrypoint",
                "Entrypoint",
                plugin_dir / entrypoint_hint,
                "error",
                f"Файл не найден: {entrypoint_hint}",
            )
    else:
        _entry_append_issue(entry, "Entrypoint не указан. Возможен только статический режим.", level="warning")
        _entry_append_check(
            entry,
            "entrypoint",
            "Entrypoint",
            plugin_dir / entrypoint_hint,
            "warning",
            "Entrypoint не указан в plugin.json.",
        )

    template_hint = _normalize_text(manifest.get("template") or "index.html") or "index.html"
    entry["template_hint"] = template_hint
    template_path = _safe_resolve(plugin_dir / template_hint)
    if template_path.is_file():
        template_text, template_error, template_encoding = _decode_text_file(template_path)
        if template_text:
            entry["template_path"] = str(template_path)
            _entry_append_check(
                entry,
                "template",
                "Шаблон",
                template_path,
                "ok",
                f"Шаблон найден ({template_encoding}).",
            )
        else:
            _entry_append_issue(entry, f"Не удалось прочитать шаблон: {template_error}")
            _entry_append_check(
                entry,
                "template",
                "Шаблон",
                template_path,
                "error",
                f"Ошибка чтения шаблона: {template_error}",
            )
    else:
        _entry_append_issue(entry, f"Шаблон не найден: {template_hint}")
        _entry_append_check(
            entry,
            "template",
            "Шаблон",
            template_path,
            "error",
            f"Файл не найден: {template_hint}",
        )

    static_dir = _safe_resolve(plugin_dir / "static")
    if static_dir.is_dir():
        static_total, static_preview = _scan_static_files(static_dir)
        if static_total > 0:
            preview_suffix = f" ({', '.join(static_preview[:4])})" if static_preview else ""
            _entry_append_check(
                entry,
                "static",
                "Статика",
                static_dir,
                "ok",
                f"Найдено файлов: {static_total}{preview_suffix}",
            )
        else:
            _entry_append_issue(entry, "Папка static есть, но файлов не найдено.", level="warning")
            _entry_append_check(
                entry,
                "static",
                "Статика",
                static_dir,
                "warning",
                "Папка static пуста.",
            )
    else:
        _entry_append_issue(entry, "Папка static не найдена.", level="warning")
        _entry_append_check(
            entry,
            "static",
            "Статика",
            static_dir,
            "warning",
            "Папка static отсутствует.",
        )

    files_total, files_preview = _scan_plugin_files(plugin_dir)
    entry["files_total"] = files_total
    entry["files_preview"] = files_preview
    entry["files_truncated"] = files_total > len(files_preview)

    status, label, chip = _status_payload(entry["errors"], entry["warnings"], False)
    entry["status"] = status
    entry["status_label"] = label
    entry["status_chip"] = chip
    return entry


def _summarize_entries(entries: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(entries),
        "error": sum(1 for item in entries if item.get("status") == "error"),
        "warning": sum(1 for item in entries if item.get("status") == "warning"),
        "ok": sum(1 for item in entries if item.get("status") == "ok"),
        "loaded": sum(1 for item in entries if item.get("registered_views")),
        "launchable": sum(1 for item in entries if item.get("can_launch")),
    }


def build_plugin_diagnostics(
    base_dir: str,
    resource_root: Optional[Path],
    plugins: Optional[list[PluginSpec]] = None,
) -> dict[str, Any]:
    runtime_compiled = _is_compiled_runtime()
    loaded_plugins = list(plugins or [])
    loaded_by_root: dict[Path, PluginSpec] = {}
    for plugin in loaded_plugins:
        loaded_by_root[_safe_resolve(plugin.root)] = plugin

    source_roots = _candidate_plugin_sources(base_dir, resource_root)
    roots = [root for _source_type, root in source_roots]
    roots_by_source = {
        _PLUGIN_SOURCE_INTEGRATED: [str(root) for source_type, root in source_roots if source_type == _PLUGIN_SOURCE_INTEGRATED],
        _PLUGIN_SOURCE_EXTERNAL: [str(root) for source_type, root in source_roots if source_type == _PLUGIN_SOURCE_EXTERNAL],
    }
    entries: list[dict[str, Any]] = []
    seen_dirs: set[Path] = set()
    for source_type, root in source_roots:
        if not root.is_dir():
            continue
        for item in sorted(root.iterdir()):
            if not item.is_dir():
                continue
            if not _is_plugin_dir_candidate(item):
                continue
            item_resolved = _safe_resolve(item)
            if item_resolved in seen_dirs:
                continue
            seen_dirs.add(item_resolved)
            plugin = loaded_by_root.get(item_resolved)
            if plugin is not None:
                entries.append(_entry_from_plugin(plugin))
            else:
                entries.append(_entry_from_directory(item, root, base_dir))

    for plugin in loaded_plugins:
        item_resolved = _safe_resolve(plugin.root)
        if item_resolved in seen_dirs:
            continue
        entries.append(_entry_from_plugin(plugin))

    by_id: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for entry in entries:
        plugin_id = _normalize_text(entry.get("plugin_id"))
        if plugin_id:
            by_id[plugin_id].append(entry)

    for plugin_id, group in by_id.items():
        if len(group) <= 1:
            continue
        for item in group:
            duplicate_msg = f"Обнаружен дубликат ID расширения: {plugin_id} ({len(group)} шт.)."
            if item.get("source") == "loaded":
                _entry_append_issue(item, duplicate_msg, level="warning")
                _entry_append_check(
                    item,
                    "duplicate",
                    "Дубликат ID",
                    item.get("manifest_path") or item.get("root") or plugin_id,
                    "warning",
                    duplicate_msg,
                )
            else:
                _entry_append_issue(item, duplicate_msg)
                _entry_append_check(
                    item,
                    "duplicate",
                    "Дубликат ID",
                    item.get("manifest_path") or item.get("root") or plugin_id,
                    "error",
                    duplicate_msg,
                )

    for entry in entries:
        loaded = bool(entry.get("registered_views"))
        status, label, chip = _status_payload(entry.get("errors") or [], entry.get("warnings") or [], loaded)
        entry["status"] = status
        entry["status_label"] = label
        entry["status_chip"] = chip
        entry["can_launch"] = bool(entry.get("url")) and loaded and not entry.get("errors")
        entry["issues_count"] = len(entry.get("errors") or []) + len(entry.get("warnings") or [])

    severity_rank = {"error": 0, "warning": 1, "ok": 2}
    entries.sort(
        key=lambda item: (
            0 if item.get("source_type") == _PLUGIN_SOURCE_INTEGRATED else 1,
            severity_rank.get(str(item.get("status")), 3),
            str(item.get("name") or item.get("plugin_id") or "").casefold(),
        )
    )

    summary = _summarize_entries(entries)
    integrated_items = [item for item in entries if item.get("source_type") == _PLUGIN_SOURCE_INTEGRATED]
    external_items = [item for item in entries if item.get("source_type") == _PLUGIN_SOURCE_EXTERNAL]

    return {
        "items": entries,
        "summary": summary,
        "roots": [str(root) for root in roots],
        "roots_by_source": roots_by_source,
        "groups": {
            "integrated": {
                "label": "Интегрированные расширения",
                "items": integrated_items,
                "summary": _summarize_entries(integrated_items),
            },
            "external": {
                "label": "Внешние расширения",
                "items": external_items,
                "summary": _summarize_entries(external_items),
            },
        },
        "runtime": {
            "compiled": runtime_compiled,
            "mode": "compiled" if runtime_compiled else "python",
        },
    }


def register_plugins(appbuilder, app, base_dir: str, resource_root: Optional[Path]) -> list[PluginSpec]:
    plugins = discover_plugins(base_dir, resource_root)
    app.config["WEB_EXTERNAL_PLUGIN_RUNTIMES"] = {}
    if not plugins:
        try:
            roots = _candidate_plugin_roots(base_dir, resource_root)
            _PLUGIN_LOGGER.warning(
                "plugins not found; roots=%s",
                [str(root) for root in roots],
            )
        except Exception:
            pass
        app.config["WEB_PLUGINS"] = []
        app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(base_dir, resource_root, plugins=[])
        return []

    used_view_names = _collect_view_names(appbuilder)

    for plugin in plugins:
        plugin.load_state = "loading"
        if plugin.source_type == _PLUGIN_SOURCE_EXTERNAL:
            plugin.module = _load_plugin_module(plugin)
            runtime = _resolve_external_runtime(plugin.module, plugin, appbuilder, app)
            if runtime is not None:
                _activate_external_runtime(appbuilder, app, plugin, runtime)
            _finalize_plugin_state(plugin)
            try:
                _PLUGIN_LOGGER.info(
                    "plugin_loaded id=%s name=%s version=%s views=%s state=%s runtime=%s",
                    plugin.plugin_id,
                    plugin.name,
                    plugin.version,
                    len(plugin.registered_views),
                    plugin.load_state,
                    plugin.runtime_mode or "external",
                )
            except Exception:
                pass
            continue
        try:
            _register_plugin_blueprint(app, plugin)
        except Exception as exc:
            _log_plugin_error(plugin, f"Ошибка static blueprint: {_format_exc(exc)}")
            _append_check(
                plugin,
                "blueprint",
                "Blueprint static",
                plugin.root / "static",
                "error",
                f"Не удалось зарегистрировать blueprint: {_format_exc(exc)}",
            )
        plugin.module = _load_plugin_module(plugin)
        views = _resolve_views(plugin.module, plugin, appbuilder, app)
        if not views and plugin.source_type == _PLUGIN_SOURCE_EXTERNAL and plugin.install_state != INSTALL_STATE_INSTALLED:
            plugin.load_state = "error" if plugin.errors else "warning"
            continue
        if not views:
            plugin.fallback_view_used = True
            _append_check(
                plugin,
                "fallback_view",
                "Fallback view",
                plugin.template or (plugin.root / (plugin.template_hint or "index.html")),
                "warning",
                "Использован fallback-view из шаблона.",
            )
            views = [_make_static_view(plugin)]

        prepared_views: list[type] = []
        reserved_names = set(used_view_names)
        for view in views:
            prepared_view = _prepare_plugin_view(view, plugin, reserved_names)
            prepared_views.append(prepared_view)
            reserved_names.add(prepared_view.__name__)

        plugin.resolved_views = [view.__name__ for view in prepared_views]

        route_base = getattr(prepared_views[0], "route_base", None) if prepared_views else None
        if isinstance(route_base, str) and route_base.strip():
            plugin.route_base = _normalize_route_base(route_base, plugin.plugin_id)
            plugin.url = plugin.route_base.rstrip("/") + "/"

        for view in prepared_views:
            view_label = _view_menu_label(view, plugin.menu_label)
            try:
                appbuilder.add_view(view, view_label, category=plugin.category)
                _grant_permissions(
                    appbuilder,
                    view.__name__,
                    plugin.roles,
                    menu_label=view_label,
                    category_label=plugin.category,
                )
                used_view_names.add(view.__name__)
                plugin.registered_views.append(view.__name__)
            except Exception as exc:
                _log_plugin_error(plugin, f"Не удалось зарегистрировать view {view.__name__}: {_format_exc(exc)}")
                _append_check(
                    plugin,
                    "view_register",
                    "Регистрация view",
                    view.__name__,
                    "error",
                    f"Ошибка регистрации: {_format_exc(exc)}",
                )

        _finalize_plugin_state(plugin)

        try:
            _PLUGIN_LOGGER.info(
                "plugin_loaded id=%s name=%s version=%s views=%s state=%s",
                plugin.plugin_id,
                plugin.name,
                plugin.version,
                len(plugin.registered_views),
                plugin.load_state,
            )
        except Exception:
            pass

    app.config["WEB_PLUGINS"] = plugins
    app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(base_dir, resource_root, plugins=plugins)
    return plugins


def load_plugin_into_runtime(appbuilder, app, plugin: PluginSpec) -> PluginSpec:
    used_view_names = _collect_view_names(appbuilder)
    plugin.load_state = "loading"
    if plugin.source_type == _PLUGIN_SOURCE_EXTERNAL:
        plugin.module = _load_plugin_module(plugin)
        runtime = _resolve_external_runtime(plugin.module, plugin, appbuilder, app)
        if runtime is not None:
            _activate_external_runtime(appbuilder, app, plugin, runtime)
        _finalize_plugin_state(plugin)
        try:
            _PLUGIN_LOGGER.info(
                "plugin_loaded id=%s name=%s version=%s views=%s state=%s runtime=%s",
                plugin.plugin_id,
                plugin.name,
                plugin.version,
                len(plugin.registered_views),
                plugin.load_state,
                plugin.runtime_mode or "external",
            )
        except Exception:
            pass
        return plugin
    try:
        _register_plugin_blueprint(app, plugin)
    except Exception as exc:
        _log_plugin_error(plugin, f"Ошибка static blueprint: {_format_exc(exc)}")
        _append_check(
            plugin,
            "blueprint",
            "Blueprint static",
            plugin.root / "static",
            "error",
            f"Не удалось зарегистрировать blueprint: {_format_exc(exc)}",
        )

    plugin.module = _load_plugin_module(plugin)
    views = _resolve_views(plugin.module, plugin, appbuilder, app)
    if not views and plugin.source_type == _PLUGIN_SOURCE_EXTERNAL and plugin.install_state != INSTALL_STATE_INSTALLED:
        plugin.load_state = "error" if plugin.errors else "warning"
        return plugin
    if not views:
        plugin.fallback_view_used = True
        _append_check(
            plugin,
            "fallback_view",
            "Fallback view",
            plugin.template or (plugin.root / (plugin.template_hint or "index.html")),
            "warning",
            "Использован fallback-view из шаблона.",
        )
        views = [_make_static_view(plugin)]

    prepared_views: list[type] = []
    reserved_names = set(used_view_names)
    for view in views:
        prepared_view = _prepare_plugin_view(view, plugin, reserved_names)
        prepared_views.append(prepared_view)
        reserved_names.add(prepared_view.__name__)

    plugin.resolved_views = [view.__name__ for view in prepared_views]

    route_base = getattr(prepared_views[0], "route_base", None) if prepared_views else None
    if isinstance(route_base, str) and route_base.strip():
        plugin.route_base = _normalize_route_base(route_base, plugin.plugin_id)
        plugin.url = plugin.route_base.rstrip("/") + "/"

    for view in prepared_views:
        view_label = _view_menu_label(view, plugin.menu_label)
        try:
            appbuilder.add_view(view, view_label, category=plugin.category)
            _grant_permissions(
                appbuilder,
                view.__name__,
                plugin.roles,
                menu_label=view_label,
                category_label=plugin.category,
            )
            used_view_names.add(view.__name__)
            plugin.registered_views.append(view.__name__)
        except Exception as exc:
            _log_plugin_error(plugin, f"Не удалось зарегистрировать view {view.__name__}: {_format_exc(exc)}")
            _append_check(
                plugin,
                "view_register",
                "Регистрация view",
                view.__name__,
                "error",
                f"Ошибка регистрации: {_format_exc(exc)}",
            )

    _finalize_plugin_state(plugin)

    try:
        _PLUGIN_LOGGER.info(
            "plugin_loaded id=%s name=%s version=%s views=%s state=%s",
            plugin.plugin_id,
            plugin.name,
            plugin.version,
            len(plugin.registered_views),
            plugin.load_state,
        )
    except Exception:
        pass
    return plugin


def install_external_plugin(
    appbuilder,
    app,
    base_dir: str,
    resource_root: Optional[Path],
    plugin_id: str,
    *,
    recreate: bool = False,
    ensure_environment: bool = True,
) -> dict[str, Any]:
    plugin_id = _safe_plugin_id(plugin_id or "") or ""
    if not plugin_id:
        return {"ok": False, "message": "Некорректный ID расширения.", "details": []}

    discovered_plugins = discover_plugins(base_dir, resource_root)
    target = next(
        (
            item
            for item in discovered_plugins
            if item.plugin_id == plugin_id and item.source_type == _PLUGIN_SOURCE_EXTERNAL
        ),
        None,
    )
    if target is None:
        return {
            "ok": False,
            "message": "Внешнее расширение не найдено в data/web_plugins.",
            "details": [],
        }

    manifest = _load_manifest(target.root) or {}
    if ensure_environment:
        install_result = ensure_external_plugin_environment(
            base_dir,
            target.root,
            manifest,
            recreate=recreate,
        )
    else:
        install_result = {
            "ok": True,
            "message": "Файлы расширения подготовлены. Установка среды пропущена.",
            "details": [],
        }

    loaded_plugins = list(app.config.get("WEB_PLUGINS") or [])
    existing = next(
        (
            item
            for item in loaded_plugins
            if item.plugin_id == plugin_id and item.source_type == _PLUGIN_SOURCE_EXTERNAL
        ),
        None,
    )

    if ensure_environment and not install_result.get("ok"):
        if existing is not None and (existing.registered_views or existing.menu_registered):
            result = dict(install_result)
            result["message"] = (
                f"{install_result.get('message') or 'Не удалось обновить среду расширения.'} "
                "Текущее уже загруженное расширение продолжит работать в этой сессии панели."
            )
            app.config["WEB_PLUGINS"] = loaded_plugins
            app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
                base_dir,
                resource_root,
                plugins=loaded_plugins,
            )
            return result
        if existing is not None:
            updated_plugins = [target if item.plugin_id == plugin_id else item for item in loaded_plugins]
        else:
            updated_plugins = loaded_plugins
        app.config["WEB_PLUGINS"] = updated_plugins
        app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
            base_dir,
            resource_root,
            plugins=updated_plugins,
        )
        return install_result

    discovered_after_install = discover_plugins(base_dir, resource_root)
    refreshed = next(
        (
            item
            for item in discovered_after_install
            if item.plugin_id == plugin_id and item.source_type == _PLUGIN_SOURCE_EXTERNAL
        ),
        None,
    )
    if refreshed is not None:
        target = refreshed

    loaded_target = load_plugin_into_runtime(appbuilder, app, target)
    if loaded_target.errors and existing is not None and (existing.registered_views or existing.menu_registered):
        app.config["WEB_PLUGINS"] = loaded_plugins
        app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
            base_dir,
            resource_root,
            plugins=loaded_plugins,
        )
        result = dict(install_result)
        result["ok"] = False
        if ensure_environment:
            result["message"] = (
                "Среда внешнего расширения обновлена, но новое подключение не активировалось. "
                f"Текущая загруженная версия продолжит работать. Причина: {loaded_target.errors[0]}"
            )
        else:
            result["message"] = (
                "Файлы расширения обновлены, но новое подключение не активировалось. "
                f"Текущая загруженная версия продолжит работать. Причина: {loaded_target.errors[0]}"
            )
        return result

    if existing is not None:
        updated_plugins = [loaded_target if item.plugin_id == plugin_id else item for item in loaded_plugins]
    else:
        updated_plugins = loaded_plugins + [loaded_target]
    updated_plugins.sort(key=lambda item: (item.order, item.name.lower()))

    app.config["WEB_PLUGINS"] = updated_plugins
    app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
        base_dir,
        resource_root,
        plugins=updated_plugins,
    )

    result = dict(install_result)
    if (loaded_target.registered_views or loaded_target.menu_registered) and not loaded_target.errors:
        result["message"] = (
            f"{install_result.get('message') or 'Операция с расширением завершена.'} "
            "Расширение подключено в текущую сессию панели без перезапуска."
        )
    elif loaded_target.errors:
        result["ok"] = False
        result["message"] = (
            "Операция завершена, но само расширение не активировалось. "
            f"Проверьте диагностику: {loaded_target.errors[0]}"
        )
    return result


def _path_is_inside(path: Path, root: Path) -> bool:
    resolved_path = _safe_resolve(path)
    resolved_root = _safe_resolve(root)
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except Exception:
        pass

    try:
        path_norm = os.path.normcase(os.path.normpath(str(resolved_path)))
        root_norm = os.path.normcase(os.path.normpath(str(resolved_root)))
        common = os.path.commonpath([path_norm, root_norm])
        return common == root_norm
    except Exception:
        return False


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _is_ignored_zip_member(raw_name: str) -> bool:
    normalized = str(raw_name or "").replace("\\", "/").lstrip("/")
    if not normalized:
        return True
    lowered = normalized.casefold()
    if lowered.startswith("__macosx/"):
        return True
    if lowered.endswith("/.ds_store") or lowered == ".ds_store":
        return True
    return False


def _normalize_zip_member(raw_name: str) -> Path | None:
    text = str(raw_name or "").strip().replace("\\", "/")
    if not text:
        return None

    try:
        relative = PurePosixPath(text)
    except Exception:
        return None

    if relative.is_absolute():
        return None

    parts: list[str] = []
    for chunk in relative.parts:
        item = str(chunk).strip()
        if not item or item == ".":
            continue
        if item == "..":
            return None
        if ":" in item:
            return None
        parts.append(item)

    if not parts:
        return None
    return Path(*parts)


def _save_uploaded_plugin_archive(uploaded_file: Any, destination: Path) -> dict[str, Any]:
    stream = getattr(uploaded_file, "stream", None) or uploaded_file
    reader = getattr(stream, "read", None)
    if not callable(reader):
        return {
            "ok": False,
            "message": "Не удалось прочитать загруженный файл.",
            "details": [],
        }

    total_size = 0
    try:
        with destination.open("wb") as out:
            while True:
                chunk = reader(1024 * 1024)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="ignore")
                total_size += len(chunk)
                if total_size > _PLUGIN_ARCHIVE_MAX_UPLOAD_SIZE:
                    return {
                        "ok": False,
                        "message": (
                            "ZIP-архив слишком большой. "
                            f"Максимум: {_PLUGIN_ARCHIVE_MAX_UPLOAD_SIZE // (1024 * 1024)} МБ."
                        ),
                        "details": [],
                    }
                out.write(chunk)
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Не удалось сохранить ZIP-архив: {exc}",
            "details": [],
        }

    if total_size <= 0:
        return {
            "ok": False,
            "message": "Загруженный ZIP-архив пустой.",
            "details": [],
        }

    return {
        "ok": True,
        "message": "ZIP-архив сохранён.",
        "details": [f"Размер архива: {total_size} байт."],
        "size": total_size,
    }


def _extract_plugin_archive(archive_path: Path, destination_dir: Path) -> dict[str, Any]:
    total_unpacked = 0
    file_count = 0
    extracted_files = 0
    details: list[str] = []
    safe_destination = _safe_resolve(destination_dir)

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue

                if _is_ignored_zip_member(info.filename):
                    continue

                member = _normalize_zip_member(info.filename)
                if member is None:
                    return {
                        "ok": False,
                        "message": f"Недопустимый путь в архиве: {info.filename!r}",
                        "details": details,
                    }

                if _zip_member_is_symlink(info):
                    return {
                        "ok": False,
                        "message": f"Символьные ссылки в архиве не поддерживаются: {info.filename!r}",
                        "details": details,
                    }

                if info.file_size > _PLUGIN_ARCHIVE_MAX_FILE_SIZE:
                    return {
                        "ok": False,
                        "message": (
                            "В архиве есть слишком большой файл: "
                            f"{info.filename!r} ({info.file_size} байт)."
                        ),
                        "details": details,
                    }

                file_count += 1
                if file_count > _PLUGIN_ARCHIVE_MAX_FILES:
                    return {
                        "ok": False,
                        "message": "Слишком много файлов в архиве.",
                        "details": details,
                    }

                total_unpacked += info.file_size
                if total_unpacked > _PLUGIN_ARCHIVE_MAX_UNPACKED_SIZE:
                    return {
                        "ok": False,
                        "message": (
                            "Распакованный объём архива слишком большой. "
                            f"Максимум: {_PLUGIN_ARCHIVE_MAX_UNPACKED_SIZE // (1024 * 1024)} МБ."
                        ),
                        "details": details,
                    }

                target_path = _safe_resolve(safe_destination / member)
                if not _path_is_inside(target_path, safe_destination):
                    return {
                        "ok": False,
                        "message": f"Небезопасный путь в архиве: {info.filename!r}",
                        "details": details + [f"target={target_path}", f"root={safe_destination}"],
                    }

                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target_path.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                extracted_files += 1
    except zipfile.BadZipFile:
        return {"ok": False, "message": "Файл не является корректным ZIP-архивом.", "details": details}
    except Exception as exc:
        return {"ok": False, "message": f"Не удалось распаковать архив: {exc}", "details": details}

    details.append(f"Распаковано файлов: {extracted_files}, объём: {total_unpacked} байт.")
    return {"ok": True, "message": "Архив успешно распакован.", "details": details}


def _resolve_plugin_root_from_archive(extracted_dir: Path) -> dict[str, Any]:
    direct_manifest = _safe_resolve(extracted_dir / _MANIFEST_FILE)
    if direct_manifest.is_file():
        return {"ok": True, "plugin_root": extracted_dir, "details": []}

    candidates: list[Path] = []
    seen: set[str] = set()
    for manifest in extracted_dir.rglob(_MANIFEST_FILE):
        if not manifest.is_file():
            continue
        try:
            rel = manifest.relative_to(extracted_dir)
        except Exception:
            continue
        rel_parts = [part.casefold() for part in rel.parts]
        if "__pycache__" in rel_parts or "__macosx" in rel_parts:
            continue
        root = _safe_resolve(manifest.parent)
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(root)

    if not candidates:
        return {
            "ok": False,
            "message": "В архиве не найден plugin.json.",
            "details": [],
        }

    if len(candidates) > 1:
        preview = []
        for candidate in candidates[:4]:
            try:
                preview.append(str(candidate.relative_to(extracted_dir)))
            except Exception:
                preview.append(str(candidate))
        details = [f"Найдено plugin.json: {', '.join(preview)}"]
        return {
            "ok": False,
            "message": "В архиве найдено несколько расширений. Архив должен содержать только одно расширение.",
            "details": details,
        }

    return {"ok": True, "plugin_root": candidates[0], "details": []}


def _resolve_external_install_root(base_dir: str) -> Path:
    errors: list[str] = []
    for candidate in resolve_external_plugin_roots(base_dir):
        try:
            root = _safe_resolve(candidate)
            root.mkdir(parents=True, exist_ok=True)
            return root
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            continue
    reason = "; ".join(errors) if errors else "нет доступных путей"
    raise RuntimeError(f"Не удалось подготовить каталог внешних расширений: {reason}")


def _remove_paths_from_sys_path(paths: Iterable[Path]) -> int:
    normalized: set[str] = set()
    for path in paths:
        if not path:
            continue
        normalized.add(os.path.normcase(str(_safe_resolve(path))))

    if not normalized:
        return 0

    removed = 0
    filtered: list[str] = []
    for item in sys.path:
        item_key = os.path.normcase(str(_safe_resolve(Path(item))))
        if item_key in normalized:
            removed += 1
            continue
        filtered.append(item)
    if removed:
        sys.path[:] = filtered
    return removed


def _find_external_plugin_by_id(app, base_dir: str, resource_root: Optional[Path], plugin_id: str) -> PluginSpec | None:
    for plugin in app.config.get("WEB_PLUGINS") or []:
        if plugin.plugin_id == plugin_id and plugin.source_type == _PLUGIN_SOURCE_EXTERNAL:
            return plugin
    for plugin in discover_plugins(base_dir, resource_root):
        if plugin.plugin_id == plugin_id and plugin.source_type == _PLUGIN_SOURCE_EXTERNAL:
            return plugin
    return None


def _detach_external_plugin(appbuilder, app, plugin_id: str, plugin: PluginSpec | None = None) -> list[str]:
    details: list[str] = []
    registry = app.config.get("WEB_EXTERNAL_PLUGIN_RUNTIMES")
    if isinstance(registry, dict) and plugin_id in registry:
        registry.pop(plugin_id, None)
        details.append("Рантайм расширения отключён в текущей сессии.")

    menu_names = {_external_plugin_menu_name(plugin_id)}
    if plugin is not None and plugin.menu_name:
        menu_names.add(plugin.menu_name)
    for menu_name in menu_names:
        _remove_menu_item(appbuilder, menu_name)
    details.append("Пункт меню расширения удалён из текущей сессии.")

    if plugin is not None:
        candidates: list[Path] = [plugin.root]
        if plugin.venv_site_packages:
            candidates.append(plugin.venv_site_packages)
        removed_paths = _remove_paths_from_sys_path(candidates)
        if removed_paths:
            details.append(f"Удалено путей из sys.path: {removed_paths}.")
    return details


def uninstall_external_plugin(
    appbuilder,
    app,
    base_dir: str,
    resource_root: Optional[Path],
    plugin_id: str,
) -> dict[str, Any]:
    plugin_id = _safe_plugin_id(plugin_id or "") or ""
    if not plugin_id:
        return {"ok": False, "message": "Некорректный ID расширения.", "details": []}

    loaded_plugins = list(app.config.get("WEB_PLUGINS") or [])
    target = _find_external_plugin_by_id(app, base_dir, resource_root, plugin_id)
    if target is None:
        return {
            "ok": False,
            "message": "Внешнее расширение не найдено.",
            "details": [],
        }

    plugin_root = _safe_resolve(target.root)
    details: list[str] = []
    if plugin_root.exists():
        try:
            shutil.rmtree(plugin_root)
            details.append(f"Удалена папка расширения: {plugin_root}")
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Не удалось удалить папку расширения: {exc}",
                "details": details,
            }
    else:
        details.append("Папка расширения уже отсутствует на диске.")

    details.extend(_detach_external_plugin(appbuilder, app, plugin_id, target))

    updated_plugins = [
        item
        for item in loaded_plugins
        if not (item.plugin_id == plugin_id and item.source_type == _PLUGIN_SOURCE_EXTERNAL)
    ]
    app.config["WEB_PLUGINS"] = updated_plugins
    app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
        base_dir,
        resource_root,
        plugins=updated_plugins,
    )

    return {
        "ok": True,
        "message": "Внешнее расширение удалено.",
        "details": details,
    }


def _iter_archive_source_files(plugin_root: Path, include_venv: bool) -> Iterable[tuple[Path, Path]]:
    for item in sorted(plugin_root.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(plugin_root)
        rel_lower = [part.casefold() for part in rel.parts]
        if "__pycache__" in rel_lower:
            continue
        if not include_venv and rel_lower and rel_lower[0] == "venv":
            continue
        yield item, rel


def create_external_plugin_archive(
    app,
    base_dir: str,
    resource_root: Optional[Path],
    plugin_id: str,
    *,
    include_venv: bool = False,
) -> dict[str, Any]:
    plugin_id = _safe_plugin_id(plugin_id or "") or ""
    if not plugin_id:
        return {"ok": False, "message": "Некорректный ID расширения.", "details": []}

    target = _find_external_plugin_by_id(app, base_dir, resource_root, plugin_id)
    if target is None:
        return {
            "ok": False,
            "message": "Внешнее расширение не найдено.",
            "details": [],
        }

    plugin_root = _safe_resolve(target.root)
    if not plugin_root.is_dir():
        return {
            "ok": False,
            "message": "Каталог расширения не найден.",
            "details": [str(plugin_root)],
        }

    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "_", str(target.version or "1.0")).strip("_") or "1.0"
    variant = "with_venv" if include_venv else "plugin_only"
    archive_name = f"{plugin_id}_{safe_version}_{variant}.zip"
    temp_dir = Path(tempfile.mkdtemp(prefix="panel_plugin_export_"))
    archive_path = temp_dir / archive_name

    files_count = 0
    packed_bytes = 0
    details: list[str] = []
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path, rel_path in _iter_archive_source_files(plugin_root, include_venv):
                try:
                    size = int(file_path.stat().st_size)
                except Exception:
                    size = 0

                if size > _PLUGIN_ARCHIVE_MAX_FILE_SIZE:
                    raise RuntimeError(f"Слишком большой файл в расширении: {rel_path} ({size} байт).")

                files_count += 1
                if files_count > _PLUGIN_ARCHIVE_MAX_FILES:
                    raise RuntimeError("Слишком много файлов для упаковки.")

                packed_bytes += size
                if packed_bytes > _PLUGIN_ARCHIVE_MAX_UNPACKED_SIZE:
                    raise RuntimeError("Слишком большой объём данных для упаковки.")

                arcname = str(PurePosixPath(plugin_id, *rel_path.parts))
                archive.write(file_path, arcname=arcname)
    except Exception as exc:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return {
            "ok": False,
            "message": f"Не удалось подготовить ZIP-архив: {exc}",
            "details": details,
        }

    if files_count <= 0:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return {
            "ok": False,
            "message": "В расширении нет файлов для архивации.",
            "details": details,
        }

    details.append(f"Файлов в архиве: {files_count}.")
    details.append(f"Упакованный объём (до сжатия): {packed_bytes} байт.")
    return {
        "ok": True,
        "message": "Архив расширения готов.",
        "details": details,
        "archive_path": str(archive_path),
        "cleanup_dir": str(temp_dir),
        "download_name": archive_name,
    }


def install_external_plugin_from_zip(
    appbuilder,
    app,
    base_dir: str,
    resource_root: Optional[Path],
    uploaded_file: Any,
    *,
    replace_existing: bool = False,
    ensure_environment: bool = True,
    recreate_environment: bool = False,
) -> dict[str, Any]:
    file_name = str(getattr(uploaded_file, "filename", "") or "").strip()
    if not file_name:
        return {"ok": False, "message": "ZIP-файл не выбран.", "details": []}
    if not file_name.casefold().endswith(".zip"):
        return {"ok": False, "message": "Поддерживаются только ZIP-архивы.", "details": []}

    work_dir = Path(tempfile.mkdtemp(prefix="panel_plugin_zip_install_"))
    archive_path = work_dir / "uploaded_plugin.zip"
    extracted_dir = work_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    details: list[str] = []
    previous_plugins = list(app.config.get("WEB_PLUGINS") or [])
    previous_runtimes = dict(app.config.get("WEB_EXTERNAL_PLUGIN_RUNTIMES") or {})
    destination_dir: Path | None = None
    backup_dir: Path | None = None

    try:
        saved = _save_uploaded_plugin_archive(uploaded_file, archive_path)
        if not saved.get("ok"):
            return saved
        details.extend([str(item) for item in (saved.get("details") or []) if str(item).strip()])

        extracted = _extract_plugin_archive(archive_path, extracted_dir)
        if not extracted.get("ok"):
            result = dict(extracted)
            result["details"] = details + [str(item) for item in (extracted.get("details") or []) if str(item).strip()]
            return result
        details.extend([str(item) for item in (extracted.get("details") or []) if str(item).strip()])

        resolved = _resolve_plugin_root_from_archive(extracted_dir)
        if not resolved.get("ok"):
            result = dict(resolved)
            result["details"] = details + [str(item) for item in (resolved.get("details") or []) if str(item).strip()]
            return result

        plugin_root = _safe_resolve(Path(str(resolved.get("plugin_root") or "")))
        manifest, manifest_error, _manifest_encoding = _load_manifest_detailed(plugin_root)
        if not manifest:
            return {
                "ok": False,
                "message": manifest_error or "Не удалось прочитать plugin.json из архива.",
                "details": details,
            }

        plugin_id = _safe_plugin_id(str(manifest.get("id") or plugin_root.name) or "") or ""
        if not plugin_id:
            return {
                "ok": False,
                "message": "Некорректный ID расширения в plugin.json.",
                "details": details,
            }
        details.append(f"ID расширения: {plugin_id}")

        for item in discover_plugins(base_dir, resource_root):
            if item.plugin_id == plugin_id and item.source_type != _PLUGIN_SOURCE_EXTERNAL:
                return {
                    "ok": False,
                    "message": (
                        f"ID {plugin_id} уже используется встроенным расширением. "
                        "Используйте другой ID в plugin.json."
                    ),
                    "details": details,
                }

        external_root = _resolve_external_install_root(base_dir)
        destination_dir = _safe_resolve(external_root / plugin_id)
        if destination_dir.exists():
            if not replace_existing:
                return {
                    "ok": False,
                    "message": (
                        f"Расширение {plugin_id} уже существует. "
                        "Включите замену существующей версии."
                    ),
                    "details": details,
                }
            backup_dir = _safe_resolve(
                destination_dir.parent / f".{destination_dir.name}.backup.{uuid.uuid4().hex}"
            )
            destination_dir.rename(backup_dir)
            details.append(f"Создана резервная копия: {backup_dir}")

        shutil.copytree(plugin_root, destination_dir, dirs_exist_ok=False)
        details.append(f"Файлы установлены в {destination_dir}")

        install_result = install_external_plugin(
            appbuilder,
            app,
            base_dir,
            resource_root,
            plugin_id,
            recreate=bool(recreate_environment and ensure_environment),
            ensure_environment=ensure_environment,
        )

        if not install_result.get("ok"):
            if backup_dir is not None and backup_dir.exists():
                try:
                    if destination_dir and destination_dir.exists():
                        shutil.rmtree(destination_dir, ignore_errors=True)
                    backup_dir.rename(destination_dir)
                    details.append("Выполнен откат к предыдущей версии расширения.")
                except Exception as rollback_exc:
                    details.append(f"Не удалось восстановить резервную копию: {rollback_exc}")
                app.config["WEB_PLUGINS"] = previous_plugins
                app.config["WEB_EXTERNAL_PLUGIN_RUNTIMES"] = previous_runtimes
                app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
                    base_dir,
                    resource_root,
                    plugins=previous_plugins,
                )

            result = dict(install_result)
            install_details = [str(item).strip() for item in (install_result.get("details") or []) if str(item).strip()]
            result["details"] = details + install_details
            return result

        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

        result = dict(install_result)
        install_details = [str(item).strip() for item in (install_result.get("details") or []) if str(item).strip()]
        result["details"] = details + install_details
        if not ensure_environment:
            result["message"] = (
                f"{result.get('message') or 'Расширение установлено из ZIP.'} "
                "Среда не создавалась: при необходимости нажмите «Установить расширение»."
            )
        return result
    except Exception as exc:
        if backup_dir is not None and backup_dir.exists():
            try:
                if destination_dir is not None and destination_dir.exists():
                    shutil.rmtree(destination_dir, ignore_errors=True)
                backup_dir.rename(destination_dir)
                details.append("Выполнен откат к предыдущей версии расширения.")
            except Exception as rollback_exc:
                details.append(f"Не удалось восстановить резервную копию: {rollback_exc}")
            app.config["WEB_PLUGINS"] = previous_plugins
            app.config["WEB_EXTERNAL_PLUGIN_RUNTIMES"] = previous_runtimes
            app.config["WEB_PLUGINS_DIAGNOSTICS"] = build_plugin_diagnostics(
                base_dir,
                resource_root,
                plugins=previous_plugins,
            )
        return {
            "ok": False,
            "message": f"Ошибка установки расширения из ZIP: {exc}",
            "details": details,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
