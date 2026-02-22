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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from flask import Blueprint, current_app, render_template_string, url_for
from flask_appbuilder import BaseView, expose
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


def _candidate_plugin_roots(base_dir: str, resource_root: Optional[Path]) -> list[Path]:
    roots: list[Path] = []
    base_path = Path(base_dir)

    roots.append(base_path / "web_plugins")
    roots.append(base_path / "data" / "web_plugins")
    roots.append(base_path / "moduls" / "web_dashboard" / "web_plugins")
    roots.append(base_path / "web_dashboard" / "web_plugins")

    try:
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir / "web_plugins")
        roots.append(exe_dir / "moduls" / "web_dashboard" / "web_plugins")
        roots.append(exe_dir / "web_dashboard" / "web_plugins")
        roots.append(exe_dir / "data" / "web_plugins")
    except Exception:
        pass

    try:
        argv_dir = Path(sys.argv[0]).resolve().parent
        roots.append(argv_dir / "web_plugins")
        roots.append(argv_dir / "moduls" / "web_dashboard" / "web_plugins")
        roots.append(argv_dir / "web_dashboard" / "web_plugins")
        roots.append(argv_dir / "data" / "web_plugins")
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


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return Path(str(path))


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


def _build_plugin_spec(plugin_dir: Path, data: dict[str, Any]) -> Optional[PluginSpec]:
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

    route_base = _normalize_route_base(data.get("route") or data.get("route_base"), plugin_id)
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
        template_hint=template_hint or "index.html",
        entrypoint_required=entrypoint_required,
    )

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
    for root in _candidate_plugin_roots(base_dir, resource_root):
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
            spec = _build_plugin_spec(item, manifest)
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
    return candidates


def _load_plugin_module(plugin: PluginSpec) -> Optional[Any]:
    module_name = f"web_plugin_{plugin.plugin_id}"
    last_error: Exception | None = None
    runtime_compiled = _is_compiled_runtime()
    missing_entrypoint_path = plugin.root / (plugin.entrypoint_hint or "plugin.py")
    has_module_error_check = False
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
) -> None:
    try:
        sm = appbuilder.sm
    except Exception:
        return
    try:
        session = sm.get_session() if callable(getattr(sm, "get_session", None)) else sm.get_session
    except Exception:
        session = None
    for role_name in roles:
        try:
            role = sm.find_role(role_name)
            if not role:
                continue
            perm_view = sm.find_permission_view_menu("can_list", view_name)
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


def _entry_from_directory(plugin_dir: Path, source_root: Path) -> dict[str, Any]:
    entry = _entry_base(plugin_dir, source_root)
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

    roots = _candidate_plugin_roots(base_dir, resource_root)
    entries: list[dict[str, Any]] = []
    seen_dirs: set[Path] = set()
    for root in roots:
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
                entries.append(_entry_from_directory(item, root))

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
            severity_rank.get(str(item.get("status")), 3),
            str(item.get("name") or item.get("plugin_id") or "").casefold(),
        )
    )

    summary = {
        "total": len(entries),
        "error": sum(1 for item in entries if item.get("status") == "error"),
        "warning": sum(1 for item in entries if item.get("status") == "warning"),
        "ok": sum(1 for item in entries if item.get("status") == "ok"),
        "loaded": sum(1 for item in entries if item.get("registered_views")),
        "launchable": sum(1 for item in entries if item.get("can_launch")),
    }

    return {
        "items": entries,
        "summary": summary,
        "roots": [str(root) for root in roots],
        "runtime": {
            "compiled": runtime_compiled,
            "mode": "compiled" if runtime_compiled else "python",
        },
    }


def register_plugins(appbuilder, app, base_dir: str, resource_root: Optional[Path]) -> list[PluginSpec]:
    plugins = discover_plugins(base_dir, resource_root)
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

        if plugin.errors:
            plugin.load_state = "error"
            continue
        if plugin.warnings:
            plugin.load_state = "warning"
        elif plugin.registered_views:
            plugin.load_state = "loaded"
        else:
            plugin.load_state = "not_loaded"

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
