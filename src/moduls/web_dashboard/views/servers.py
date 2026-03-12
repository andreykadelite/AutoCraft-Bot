from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

from flask import current_app, flash, jsonify, redirect, request, session, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..app_factory import apply_runtime_debug_logging
from ..config import (
    PanelDebugLogConfig,
    get_panel_log_path,
    load_debug_log_config,
    save_debug_log_config,
)
from ..db import db
from ..models.audit import AuditLog
from ..remote_access_service import (
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_PROMPT,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    list_pending_requests,
    list_policies,
    respond_to_request,
    upsert_policy,
)
from ..remote_control import (
    apply_remote_grant_payload,
    clear_remote_target,
    ensure_remote_grant,
    get_remote_grant_state,
    get_remote_target_context,
    poll_remote_request_for_peer,
    request_remote_access_for_peer,
    set_remote_target_from_peer,
)
from ..security import panel_has_access as has_access
from ..utils import parse_bool, parse_int

_CSRF_FAILURE_MESSAGE = (
    "Проверка безопасности не пройдена или истекла. Обновите страницу и повторите действие."
)
_DEFAULT_GROUP = "239.255.67.67"
_DEFAULT_PORT = 37555
_DEFAULT_PANEL_PORT = 5212
_DEBUG_LEVEL_OPTIONS = ("MAX", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_MIN_DEBUG_MAX_BYTES = 64 * 1024
_MAX_DEBUG_MAX_BYTES = 512 * 1024 * 1024
_MIN_DEBUG_BACKUP_COUNT = 1
_MAX_DEBUG_BACKUP_COUNT = 30
_SESSION_PEER_CONTROL_STATES = "panel_peer_control_states"
_PEER_CONTROL_IDLE_STATE = {
    "status": "idle",
    "message": "",
    "request_id": "",
    "grant_token": "",
    "grant_expires_at": "",
    "grant_expires_epoch": 0.0,
    "updated_at": "",
}
_PEER_CONTROL_VALID_STATUSES = {
    "idle",
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
    "disabled",
    "manual_required",
    "error",
}


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


def _payload_data() -> Dict[str, Any]:
    json_data = request.get_json(silent=True)
    if isinstance(json_data, dict):
        return dict(json_data)
    out: Dict[str, Any] = {}
    for key in request.form.keys():
        values = request.form.getlist(key)
        if not values:
            continue
        out[key] = values[-1]
    return out


def _json_error(message: str, status: int = 400, **extra):
    payload = {"ok": False, "message": str(message or "error")}
    payload.update(extra)
    return jsonify(payload), status


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _form_checkbox_value(name: str, default: bool = False) -> bool:
    values = request.form.getlist(name)
    if not values:
        return bool(default)
    for raw in values:
        if parse_bool(raw, False):
            return True
    return False


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_path(path: str) -> str:
    text = (path or "/").strip()
    if not text.startswith("/"):
        text = "/" + text
    return text or "/"


def _remote_role_mode_from_flags(controller_enabled: bool, target_enabled: bool) -> str:
    if controller_enabled and target_enabled:
        return "dual"
    if controller_enabled:
        return "controller"
    if target_enabled:
        return "target"
    return "controller"


def _remote_role_flags_from_mode(role_mode: str) -> Tuple[bool, bool]:
    mode = str(role_mode or "").strip().lower()
    if mode == "dual":
        return True, True
    if mode == "target":
        return False, True
    return True, False


def _safe_next_url(default_endpoint: str = "ServerView.list") -> str:
    next_value = (
        request.args.get("next")
        or request.form.get("next")
        or request.args.get("return")
        or request.form.get("return")
        or ""
    )
    candidate = str(next_value or "").strip()
    if not candidate:
        return url_for(default_endpoint)
    try:
        parts = urlsplit(candidate)
        if parts.scheme or parts.netloc:
            return url_for(default_endpoint)
        path = parts.path or "/"
        query = f"?{parts.query}" if parts.query else ""
        fragment = f"#{parts.fragment}" if parts.fragment else ""
        return f"{path}{query}{fragment}"
    except Exception:
        return url_for(default_endpoint)


def _load_lan_discovery_module():
    last_error: Exception | None = None
    for module_name in (
        "gui_win.gui_win_lan_discovery_window",
        "moduls.gui_win.gui_win_lan_discovery_window",
    ):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Не удалось загрузить модуль обнаружения ЛВС.")


def _load_lan_backend():
    module = _load_lan_discovery_module()
    getter = getattr(module, "get_lan_discovery_backend", None)
    if not callable(getter):
        raise RuntimeError("Модуль обнаружения ЛВС не предоставляет get_lan_discovery_backend().")
    return getter(), module


def _safe_browser_panel_url(module, url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    normalizer = getattr(module, "_normalize_panel_url_for_browser", None)
    if callable(normalizer):
        try:
            return str(normalizer(raw) or raw).strip()
        except Exception:
            return raw
    return raw


def _build_status_text(status: Dict[str, Any]) -> str:
    running = bool(status.get("running"))
    text = (
        f"Служба: {'запущена' if running else 'остановлена'} | "
        f"Режим: {status.get('mode') or '-'} | "
        f"Узлы: {status.get('peer_count', 0)} | "
        f"Порт: {status.get('udp_port') or '-'} | "
        f"Мультикаст: {'да' if status.get('multicast_joined') else 'нет'}"
    )
    err = str(status.get("last_error") or "").strip()
    if err:
        text += f" | Ошибка: {err}"
    return text


def _settings_signature(settings: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        str(settings.get("mode") or "multi"),
        bool(settings.get("enabled_on_start")),
        str(settings.get("instance_name") or ""),
        str(settings.get("service_name") or ""),
        str(settings.get("app_version") or ""),
        int(settings.get("udp_port") or _DEFAULT_PORT),
        str(settings.get("multicast_group") or _DEFAULT_GROUP),
        bool(settings.get("multicast_enabled")),
        bool(settings.get("broadcast_enabled")),
        float(settings.get("discover_interval_sec") or 5.0),
        float(settings.get("announce_interval_sec") or 10.0),
        float(settings.get("peer_timeout_sec") or 30.0),
        bool(settings.get("advertise_panel")),
        str(settings.get("panel_scheme") or "http"),
        int(settings.get("panel_port") or _DEFAULT_PANEL_PORT),
        str(settings.get("panel_path") or "/"),
        str(settings.get("panel_host_override") or ""),
        bool(settings.get("remote_control_controller_enabled", True)),
        bool(settings.get("remote_control_target_enabled", True)),
        bool(settings.get("remote_control_require_approval", True)),
        bool(settings.get("remote_control_auto_request_on_select", True)),
        int(settings.get("remote_control_request_timeout_sec") or 180),
        int(settings.get("remote_control_grant_ttl_sec") or 1800),
    )


def _peers_signature(peers: List[Dict[str, Any]]) -> Tuple[Any, ...]:
    return tuple(
        (
            str(peer.get("node_id") or ""),
            str(peer.get("instance_name") or ""),
            str(peer.get("app") or ""),
            str(peer.get("ip") or ""),
            str(peer.get("role") or ""),
            str(peer.get("hostname") or ""),
            str(peer.get("app_version") or ""),
            str(peer.get("source") or ""),
            str(peer.get("last_seen_text") or ""),
            str(peer.get("panel_url") or ""),
            str(peer.get("panel_api_token") or ""),
            str(peer.get("panel_proxy_protocol") or ""),
            ",".join(str(v) for v in (peer.get("panel_capabilities") or [])),
            bool(peer.get("remote_control_controller_enabled", True)),
            bool(peer.get("remote_control_target_enabled", True)),
            bool(peer.get("remote_control_require_approval", True)),
        )
        for peer in peers
    )


def _write_audit(action: str, result: bool, details: str = "") -> None:
    try:
        ip = request.remote_addr or ""
    except Exception:
        ip = ""
    actor = getattr(current_user, "username", "") or "web"
    try:
        log = AuditLog(
            user=str(actor),
            action=str(action),
            target="lan_discovery",
            result="ok" if result else "fail",
            source="web",
            ip=str(ip),
            details=str(details or "")[:900],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _policy_to_dict(policy) -> Dict[str, Any]:
    return {
        "controller_node_id": str(getattr(policy, "controller_node_id", "") or ""),
        "controller_name": str(getattr(policy, "controller_name", "") or ""),
        "controller_ip": str(getattr(policy, "controller_ip", "") or ""),
        "decision": str(getattr(policy, "decision", "") or ""),
        "remember": bool(getattr(policy, "remember", False)),
        "updated_by": str(getattr(policy, "updated_by", "") or ""),
        "updated_at": (
            getattr(policy, "updated_at", None).isoformat()  # type: ignore[union-attr]
            if getattr(policy, "updated_at", None)
            else ""
        ),
    }


def _normalize_debug_level(level: Any, default: str = "MAX") -> str:
    text = str(level or default).strip().upper()
    if text not in _DEBUG_LEVEL_OPTIONS:
        return str(default).strip().upper()
    return text


def _clamp_debug_max_bytes(value: int) -> int:
    return max(_MIN_DEBUG_MAX_BYTES, min(int(value), _MAX_DEBUG_MAX_BYTES))


def _clamp_debug_backup_count(value: int) -> int:
    return max(_MIN_DEBUG_BACKUP_COUNT, min(int(value), _MAX_DEBUG_BACKUP_COUNT))


def _build_debug_logging_payload(base_dir: str, cfg: PanelDebugLogConfig | None = None) -> Dict[str, Any]:
    resolved = (cfg or load_debug_log_config(base_dir)).normalized()
    log_path = get_panel_log_path(base_dir)
    max_mb = max(1, int(round(float(resolved.max_bytes) / (1024.0 * 1024.0))))
    return {
        "enabled": bool(resolved.enabled),
        "level": resolved.level,
        "max_bytes": int(resolved.max_bytes),
        "max_mb": max_mb,
        "backup_count": int(resolved.backup_count),
        "path": str(log_path),
        "status_text": "включен" if resolved.enabled else "выключен",
        "levels": list(_DEBUG_LEVEL_OPTIONS),
    }


def _collect_debug_log_config_from_form(current_cfg: PanelDebugLogConfig) -> PanelDebugLogConfig:
    current = current_cfg.normalized()
    level = _normalize_debug_level(request.form.get("debug_log_level"), current.level)
    max_mb = parse_int(
        request.form.get("debug_log_max_mb"),
        max(1, int(round(float(current.max_bytes) / (1024.0 * 1024.0)))),
    )
    max_bytes = _clamp_debug_max_bytes(int(max(1, max_mb)) * 1024 * 1024)
    backup_count = _clamp_debug_backup_count(
        parse_int(request.form.get("debug_log_backup_count"), current.backup_count)
    )
    return PanelDebugLogConfig(
        enabled=current.enabled,
        level=level,
        max_bytes=max_bytes,
        backup_count=backup_count,
    ).normalized()


def _save_and_apply_debug_logging(base_dir: str, cfg: PanelDebugLogConfig) -> PanelDebugLogConfig:
    normalized = cfg.normalized()
    save_debug_log_config(base_dir, normalized)
    return apply_runtime_debug_logging(current_app, base_dir, normalized, persist=False)


def _frontend_log_level(level_name: Any) -> int:
    text = str(level_name or "").strip().upper()
    if text == "DEBUG":
        return logging.DEBUG
    if text == "INFO":
        return logging.INFO
    if text == "WARNING":
        return logging.WARNING
    if text == "ERROR":
        return logging.ERROR
    if text == "CRITICAL":
        return logging.CRITICAL
    return logging.ERROR


def _normalize_peer_control_state(raw: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    status = str(source.get("status") or "idle").strip().lower() or "idle"
    if status not in _PEER_CONTROL_VALID_STATUSES:
        status = "idle"
    message = str(source.get("message") or "").strip()
    request_id = str(source.get("request_id") or "").strip()
    grant_token = str(source.get("grant_token") or "").strip()
    grant_expires_at = str(source.get("grant_expires_at") or "").strip()
    updated_at = str(source.get("updated_at") or "").strip()
    try:
        grant_expires_epoch = float(source.get("grant_expires_epoch") or 0.0)
    except Exception:
        grant_expires_epoch = 0.0
    if status != STATUS_APPROVED:
        grant_token = ""
        grant_expires_at = ""
        grant_expires_epoch = 0.0
    if status == "idle":
        message = ""
        if not request_id:
            updated_at = ""
    return {
        "status": status,
        "message": message,
        "request_id": request_id,
        "grant_token": grant_token,
        "grant_expires_at": grant_expires_at,
        "grant_expires_epoch": grant_expires_epoch,
        "updated_at": updated_at,
    }


def _peer_control_state_from_payload(
    payload: Dict[str, Any] | None,
    previous: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    prev = _normalize_peer_control_state(previous)
    if not isinstance(payload, dict):
        return prev
    status = str(payload.get("status") or prev.get("status") or "idle").strip().lower() or "idle"
    if status not in _PEER_CONTROL_VALID_STATUSES:
        status = "idle"
    message = str(payload.get("message") or prev.get("message") or "").strip()
    request_id = str(payload.get("request_id") or prev.get("request_id") or "").strip()
    grant_token = str(payload.get("grant_token") or prev.get("grant_token") or "").strip()
    grant_expires_at = str(payload.get("grant_expires_at") or prev.get("grant_expires_at") or "").strip()
    updated_at = str(
        payload.get("updated_at")
        or payload.get("responded_at")
        or payload.get("created_at")
        or prev.get("updated_at")
        or ""
    ).strip()
    try:
        grant_expires_epoch = float(
            payload.get("grant_expires_epoch")
            or payload.get("grant_expires_epoch_sec")
            or prev.get("grant_expires_epoch")
            or 0.0
        )
    except Exception:
        grant_expires_epoch = float(prev.get("grant_expires_epoch") or 0.0)
    normalized = _normalize_peer_control_state(
        {
            "status": status,
            "message": message,
            "request_id": request_id,
            "grant_token": grant_token,
            "grant_expires_at": grant_expires_at,
            "grant_expires_epoch": grant_expires_epoch,
            "updated_at": updated_at,
        }
    )
    return normalized


def _load_peer_control_states() -> Dict[str, Dict[str, Any]]:
    raw = session.get(_SESSION_PEER_CONTROL_STATES)
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for node_id, state in raw.items():
        node = str(node_id or "").strip()
        if not node:
            continue
        out[node] = _normalize_peer_control_state(state if isinstance(state, dict) else None)
    return out


def _save_peer_control_states(states: Dict[str, Dict[str, Any]]) -> None:
    normalized: Dict[str, Dict[str, Any]] = {}
    for node_id, state in states.items():
        node = str(node_id or "").strip()
        if not node:
            continue
        normalized[node] = _normalize_peer_control_state(state if isinstance(state, dict) else None)
    session[_SESSION_PEER_CONTROL_STATES] = normalized
    session.modified = True


def _set_peer_control_state(node_id: str, payload: Dict[str, Any] | None) -> Dict[str, Any]:
    node = str(node_id or "").strip()
    if not node:
        return dict(_PEER_CONTROL_IDLE_STATE)
    states = _load_peer_control_states()
    next_state = _peer_control_state_from_payload(payload, states.get(node))
    if (
        next_state.get("status") == "idle"
        and not next_state.get("request_id")
        and not next_state.get("message")
        and not next_state.get("updated_at")
    ):
        states.pop(node, None)
        _save_peer_control_states(states)
        return dict(_PEER_CONTROL_IDLE_STATE)
    states[node] = next_state
    _save_peer_control_states(states)
    return dict(next_state)


def _remove_peer_control_state(node_id: str) -> None:
    node = str(node_id or "").strip()
    if not node:
        return
    states = _load_peer_control_states()
    if node not in states:
        return
    states.pop(node, None)
    _save_peer_control_states(states)


def _refresh_peer_control_states(
    peers: List[Dict[str, Any]],
    states: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], bool]:
    changed = False
    peers_by_node: Dict[str, Dict[str, Any]] = {}
    for peer in peers:
        node = str(peer.get("node_id") or "").strip()
        if node:
            peers_by_node[node] = peer

    for node in list(states.keys()):
        if node not in peers_by_node:
            states.pop(node, None)
            changed = True
            continue
        current = _normalize_peer_control_state(states.get(node))
        if current.get("status") != STATUS_PENDING:
            if current != states.get(node):
                changed = True
            states[node] = current
            continue
        request_id = str(current.get("request_id") or "").strip()
        if not request_id:
            if current != states.get(node):
                changed = True
            states[node] = current
            continue
        try:
            polled = poll_remote_request_for_peer(peers_by_node[node], request_id)
        except Exception:
            if current != states.get(node):
                changed = True
            states[node] = current
            continue
        refreshed = _peer_control_state_from_payload(polled if isinstance(polled, dict) else None, current)
        if refreshed != current:
            changed = True
        states[node] = refreshed
    return states, changed


class ServerView(BaseView):
    route_base = "/servers"
    default_view = "list"
    base_permissions = ["can_list", "can_action"]

    def _can_action(self) -> bool:
        try:
            return bool(self.appbuilder.sm.has_access("can_action", self.class_permission_name))
        except Exception:
            return False

    def _collect_settings_from_form(self, backend) -> Dict[str, Any]:
        current = dict(backend.load_settings(force_reload=True))
        mode = str(request.form.get("mode") or current.get("mode") or "multi").strip().lower()
        if mode not in {"server", "client", "multi"}:
            mode = "multi"

        current["mode"] = mode
        current["enabled_on_start"] = _form_checkbox_value(
            "enabled_on_start",
            bool(current.get("enabled_on_start")),
        )
        current["instance_name"] = (
            (request.form.get("instance_name") or "").strip()
            or str(current.get("instance_name") or "")
        )
        current["service_name"] = (
            (request.form.get("service_name") or "").strip()
            or str(current.get("service_name") or "AutoCraft-Bot")
        )
        current["app_version"] = (request.form.get("app_version") or "").strip()

        current["udp_port"] = max(
            1,
            min(
                65535,
                parse_int(request.form.get("udp_port"), int(current.get("udp_port") or _DEFAULT_PORT)),
            ),
        )
        current["multicast_group"] = (
            (request.form.get("multicast_group") or "").strip()
            or str(current.get("multicast_group") or _DEFAULT_GROUP)
        )
        current["multicast_enabled"] = _form_checkbox_value(
            "multicast_enabled",
            bool(current.get("multicast_enabled")),
        )
        current["broadcast_enabled"] = _form_checkbox_value(
            "broadcast_enabled",
            bool(current.get("broadcast_enabled")),
        )

        discover_interval = _parse_float(
            request.form.get("discover_interval_sec"),
            float(current.get("discover_interval_sec") or 5.0),
        )
        announce_interval = _parse_float(
            request.form.get("announce_interval_sec"),
            float(current.get("announce_interval_sec") or 10.0),
        )
        peer_timeout = _parse_float(
            request.form.get("peer_timeout_sec"),
            float(current.get("peer_timeout_sec") or 30.0),
        )
        current["discover_interval_sec"] = _clamp(discover_interval, 1.0, 300.0)
        current["announce_interval_sec"] = _clamp(announce_interval, 1.0, 300.0)
        current["peer_timeout_sec"] = _clamp(peer_timeout, 3.0, 3600.0)

        current["advertise_panel"] = _form_checkbox_value(
            "advertise_panel",
            bool(current.get("advertise_panel")),
        )
        panel_scheme = str(request.form.get("panel_scheme") or current.get("panel_scheme") or "http").strip().lower()
        if panel_scheme not in {"http", "https"}:
            panel_scheme = "http"
        current["panel_scheme"] = panel_scheme
        current["panel_port"] = max(
            1,
            min(
                65535,
                parse_int(request.form.get("panel_port"), int(current.get("panel_port") or _DEFAULT_PANEL_PORT)),
            ),
        )
        current["panel_path"] = _normalize_path(
            (request.form.get("panel_path") or "").strip() or str(current.get("panel_path") or "/")
        )
        current["panel_host_override"] = (request.form.get("panel_host_override") or "").strip()

        default_role_mode = _remote_role_mode_from_flags(
            bool(current.get("remote_control_controller_enabled", True)),
            bool(current.get("remote_control_target_enabled", True)),
        )
        requested_role_modes = [
            str(value or "").strip().lower()
            for value in request.form.getlist("remote_role_mode")
            if str(value or "").strip()
        ]
        requested_role_modes = [value for value in requested_role_modes if value in {"controller", "target", "dual"}]
        if requested_role_modes:
            role_mode = requested_role_modes[0]
        elif "remote_role_mode" in request.form:
            role_mode = default_role_mode
        else:
            fallback_controller = _form_checkbox_value(
                "remote_control_controller_enabled",
                bool(current.get("remote_control_controller_enabled", True)),
            )
            fallback_target = _form_checkbox_value(
                "remote_control_target_enabled",
                bool(current.get("remote_control_target_enabled", True)),
            )
            role_mode = _remote_role_mode_from_flags(bool(fallback_controller), bool(fallback_target))
        (
            current["remote_control_controller_enabled"],
            current["remote_control_target_enabled"],
        ) = _remote_role_flags_from_mode(role_mode)
        current["remote_control_require_approval"] = _form_checkbox_value(
            "remote_control_require_approval",
            bool(current.get("remote_control_require_approval", True)),
        )
        current["remote_control_auto_request_on_select"] = _form_checkbox_value(
            "remote_control_auto_request_on_select",
            bool(current.get("remote_control_auto_request_on_select", True)),
        )
        current["remote_control_request_timeout_sec"] = max(
            30,
            min(
                3600,
                parse_int(
                    request.form.get("remote_control_request_timeout_sec"),
                    int(current.get("remote_control_request_timeout_sec") or 180),
                ),
            ),
        )
        current["remote_control_grant_ttl_sec"] = max(
            60,
            min(
                24 * 3600,
                parse_int(
                    request.form.get("remote_control_grant_ttl_sec"),
                    int(current.get("remote_control_grant_ttl_sec") or 1800),
                ),
            ),
        )
        return current

    def _build_state_payload(self, backend, module) -> Dict[str, Any]:
        base_dir = str(current_app.config.get("BASE_DIR") or "")
        settings = dict(backend.load_settings(force_reload=True))
        status = dict(backend.get_status() or {})
        peers = list(backend.get_peers() or [])
        peer_control_states = _load_peer_control_states()
        remote_target = get_remote_target_context(force_refresh=True)
        grant_state = get_remote_grant_state()
        if remote_target.active:
            try:
                refreshed_grant = ensure_remote_grant(
                    remote_target,
                    force_new=False,
                    allow_create=True,
                )
                if isinstance(refreshed_grant, dict):
                    grant_state = dict(refreshed_grant)
            except Exception:
                pass
            peer_control_states[remote_target.node_id] = _peer_control_state_from_payload(
                grant_state,
                peer_control_states.get(remote_target.node_id),
            )
        logs: List[str] = []
        get_log_history = getattr(backend, "get_log_history", None)
        if callable(get_log_history):
            try:
                logs = list(get_log_history(limit=400) or [])
            except Exception:
                logs = []

        status["peer_count"] = len(peers)
        for peer in peers:
            peer["panel_url"] = _safe_browser_panel_url(module, str(peer.get("panel_url") or ""))
            peer["panel_api_token"] = str(peer.get("panel_api_token") or "")
            caps = peer.get("panel_capabilities") or []
            if isinstance(caps, list):
                peer["panel_capabilities"] = [str(item).strip() for item in caps if str(item).strip()]
            else:
                peer["panel_capabilities"] = []
            peer["remote_control_controller_enabled"] = bool(peer.get("remote_control_controller_enabled", True))
            peer["remote_control_target_enabled"] = bool(peer.get("remote_control_target_enabled", True))
            peer["remote_control_require_approval"] = bool(peer.get("remote_control_require_approval", True))

        peer_control_states, states_changed = _refresh_peer_control_states(peers, peer_control_states)
        if remote_target.active and remote_target.node_id:
            selected_state = _peer_control_state_from_payload(
                grant_state,
                peer_control_states.get(remote_target.node_id),
            )
            if selected_state != peer_control_states.get(remote_target.node_id):
                states_changed = True
            peer_control_states[remote_target.node_id] = selected_state
        if states_changed:
            _save_peer_control_states(peer_control_states)

        target_enabled = bool(settings.get("remote_control_target_enabled", True))
        pending_requests = list_pending_requests(limit=120) if target_enabled else []
        saved_policies = list_policies(limit=240)
        remote_access_state = {
            "controller_enabled": bool(settings.get("remote_control_controller_enabled", True)),
            "target_enabled": target_enabled,
            "require_approval": bool(settings.get("remote_control_require_approval", True)),
            "auto_request_on_select": bool(settings.get("remote_control_auto_request_on_select", True)),
            "request_timeout_sec": int(settings.get("remote_control_request_timeout_sec") or 180),
            "grant_ttl_sec": int(settings.get("remote_control_grant_ttl_sec") or 1800),
            "pending_requests": pending_requests,
            "saved_policies": saved_policies,
            "pending_count": len(pending_requests),
            "grant_state": grant_state,
            "peer_control_states": peer_control_states,
        }
        if base_dir:
            debug_logging = _build_debug_logging_payload(base_dir)
        else:
            debug_logging = {
                "enabled": False,
                "level": "MAX",
                "max_bytes": 0,
                "max_mb": 0,
                "backup_count": 0,
                "path": "",
                "status_text": "выключен",
                "levels": list(_DEBUG_LEVEL_OPTIONS),
            }
        return {
            "settings": settings,
            "status": status,
            "status_text": _build_status_text(status),
            "peers": peers,
            "logs": logs,
            "settings_signature": repr(_settings_signature(settings)),
            "peers_signature": repr(_peers_signature(peers)),
            "selected_node_id": remote_target.node_id if remote_target.active else "",
            "remote_target_active": bool(remote_target.active),
            "remote_access": remote_access_state,
            "debug_logging": debug_logging,
        }

    def _execute_action(self, backend, action: str, settings: Dict[str, Any]) -> str:
        if action == "save":
            backend.save_settings(settings)
            _write_audit("lan.save_settings", True, "saved")
            return "Настройки ЛВС сохранены."
        if action == "start":
            backend.save_settings(settings)
            backend.start_service(settings)
            _write_audit("lan.start", True, "service_started")
            return "Служба обнаружения ЛВС запущена."
        if action == "stop":
            backend.stop_service()
            _write_audit("lan.stop", True, "service_stopped")
            return "Служба обнаружения ЛВС остановлена."
        if action == "restart":
            backend.save_settings(settings)
            backend.restart_service(settings)
            _write_audit("lan.restart", True, "service_restarted")
            return "Служба обнаружения ЛВС перезапущена."
        if action == "refresh_now":
            backend.save_settings(settings)
            backend.refresh_now()
            _write_audit("lan.refresh_now", True, "discover_sent")
            return "Пакет DISCOVER отправлен."
        if action == "cleanup_stale":
            removed = int(backend.cleanup_stale() or 0)
            _write_audit("lan.cleanup_stale", True, f"removed={removed}")
            return f"Удалено устаревших узлов: {removed}."
        if action == "clear_peers":
            backend.clear_peers()
            _write_audit("lan.clear_peers", True, "cleared")
            return "Список узлов очищен."
        if action == "refresh_panel":
            backend.refresh_panel_advertisement_settings(persist=True, silent=False)
            _write_audit("lan.refresh_panel", True, "panel_sync")
            return "Настройки публикации веб-панели в ЛВС синхронизированы."
        if action == "debug_toggle":
            base_dir = str(current_app.config.get("BASE_DIR") or "")
            current_cfg = load_debug_log_config(base_dir)
            updated_cfg = PanelDebugLogConfig(
                enabled=not current_cfg.enabled,
                level=current_cfg.level,
                max_bytes=current_cfg.max_bytes,
                backup_count=current_cfg.backup_count,
            ).normalized()
            applied = _save_and_apply_debug_logging(base_dir, updated_cfg)
            _write_audit(
                "lan.debug_toggle",
                True,
                (
                    f"enabled={int(bool(applied.enabled))} "
                    f"level={applied.level} "
                    f"max_bytes={applied.max_bytes} "
                    f"backup_count={applied.backup_count}"
                ),
            )
            status = "включен" if applied.enabled else "выключен"
            return f"Debug-лог {status}. Файл: {get_panel_log_path(base_dir)}"
        if action == "debug_save":
            base_dir = str(current_app.config.get("BASE_DIR") or "")
            current_cfg = load_debug_log_config(base_dir)
            updated_cfg = _collect_debug_log_config_from_form(current_cfg)
            if "debug_enabled" in request.form:
                updated_cfg.enabled = _form_checkbox_value("debug_enabled", current_cfg.enabled)
            applied = _save_and_apply_debug_logging(base_dir, updated_cfg)
            _write_audit(
                "lan.debug_save",
                True,
                (
                    f"enabled={int(bool(applied.enabled))} "
                    f"level={applied.level} "
                    f"max_bytes={applied.max_bytes} "
                    f"backup_count={applied.backup_count}"
                ),
            )
            status = "включен" if applied.enabled else "выключен"
            return (
                "Настройки debug-лога сохранены: "
                f"статус={status}, уровень={applied.level}, "
                f"размер={applied.max_bytes} байт, файлов={applied.backup_count}."
            )
        return ""

    @expose("/", methods=["GET", "POST"])
    @has_access
    def list(self):
        can_action = self._can_action()
        base_dir = str(current_app.config.get("BASE_DIR") or "")
        backend = None
        module = None
        load_error = ""
        debug_logging = (
            _build_debug_logging_payload(base_dir)
            if base_dir
            else {
                "enabled": False,
                "level": "MAX",
                "max_bytes": 0,
                "max_mb": 0,
                "backup_count": 0,
                "path": "",
                "status_text": "выключен",
                "levels": list(_DEBUG_LEVEL_OPTIONS),
            }
        )

        try:
            backend, module = _load_lan_backend()
        except Exception as exc:
            load_error = str(exc) or repr(exc)

        if request.method == "POST":
            if not _is_csrf_valid():
                flash(_CSRF_FAILURE_MESSAGE, "danger")
                _write_audit("lan.post", False, "csrf_failed")
                return redirect(url_for("ServerView.list"))
            action = str(request.form.get("form_action") or "").strip().lower()
            debug_actions = {"debug_toggle", "debug_save"}
            if backend is None and action not in debug_actions:
                flash(f"Служба ЛВС недоступна: {load_error}", "danger")
                _write_audit("lan.post", False, f"backend_unavailable:{load_error}")
                return redirect(url_for("ServerView.list"))

            if not action:
                flash("Действие не выбрано.", "warning")
                return redirect(url_for("ServerView.list"))

            if not can_action:
                flash("У роли нет права can_action для управления настройками ЛВС.", "danger")
                _write_audit(f"lan.{action}", False, "permission_denied")
                return redirect(url_for("ServerView.list"))

            try:
                if action in debug_actions:
                    settings = {}
                else:
                    settings = self._collect_settings_from_form(backend)
                message = self._execute_action(backend, action, settings)
                if message:
                    flash(message, "success")
                else:
                    flash(f"Неизвестное действие: {action}", "warning")
                    _write_audit(f"lan.{action}", False, "unknown_action")
            except Exception as exc:
                _write_audit(f"lan.{action}", False, str(exc))
                flash(f"Ошибка выполнения действия ЛВС: {exc}", "danger")
            return redirect(url_for("ServerView.list"))

        if backend is None or module is None:
            return self.render_template(
                "server_list.html",
                can_action=can_action,
                load_error=load_error,
                settings={},
                status={},
                status_text="Служба ЛВС недоступна.",
                peers=[],
                logs=[],
                settings_signature="",
                peers_signature="",
                selected_node_id="",
                remote_target=get_remote_target_context(force_refresh=False),
                remote_access={
                    "controller_enabled": True,
                    "target_enabled": True,
                    "require_approval": True,
                    "auto_request_on_select": True,
                    "request_timeout_sec": 180,
                    "grant_ttl_sec": 1800,
                    "pending_requests": [],
                    "saved_policies": [],
                    "pending_count": 0,
                    "grant_state": {"status": "idle", "message": ""},
                    "peer_control_states": {},
                },
                debug_logging=debug_logging,
                state_url=url_for("ServerView.state"),
                frontend_debug_log_url=url_for("ServerView.frontend_debug_log"),
                request_control_url_template=url_for("ServerView.request_control", node_id="__NODE__"),
                respond_request_url_template=url_for("ServerView.respond_control_request", request_id="__REQ__"),
                policy_url_template=url_for("ServerView.update_control_policy", controller_node_id="__NODE__"),
                remove_peer_url_template=url_for("ServerView.remove_peer", node_id="__NODE__"),
            )

        payload = self._build_state_payload(backend, module)
        return self.render_template(
            "server_list.html",
            can_action=can_action,
            load_error="",
            settings=payload["settings"],
            status=payload["status"],
            status_text=payload["status_text"],
            peers=payload["peers"],
            logs=payload["logs"],
            settings_signature=payload["settings_signature"],
            peers_signature=payload["peers_signature"],
            selected_node_id=payload["selected_node_id"],
            remote_target=get_remote_target_context(force_refresh=False),
            remote_access=payload["remote_access"],
            debug_logging=payload["debug_logging"],
            state_url=url_for("ServerView.state"),
            frontend_debug_log_url=url_for("ServerView.frontend_debug_log"),
            request_control_url_template=url_for("ServerView.request_control", node_id="__NODE__"),
            respond_request_url_template=url_for("ServerView.respond_control_request", request_id="__REQ__"),
            policy_url_template=url_for("ServerView.update_control_policy", controller_node_id="__NODE__"),
            remove_peer_url_template=url_for("ServerView.remove_peer", node_id="__NODE__"),
        )

    @expose("/state")
    @has_access
    @permission_name("list")
    def state(self):
        try:
            backend, module = _load_lan_backend()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc) or repr(exc)}), 500
        try:
            payload = self._build_state_payload(backend, module)
            payload["ok"] = True
            payload["can_action"] = self._can_action()
            return jsonify(payload)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc) or repr(exc)}), 500

    @expose("/debug/frontend", methods=["POST"])
    @has_access
    @permission_name("list")
    def frontend_debug_log(self):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, status=400, status_code="csrf_failed")
        base_dir = str(current_app.config.get("BASE_DIR") or "")
        if not base_dir:
            return _json_error("base_dir_not_found", status=500, status_code="base_dir_not_found")
        debug_cfg = load_debug_log_config(base_dir)
        if not debug_cfg.enabled:
            return jsonify({"ok": True, "ignored": True, "reason": "debug_disabled"})

        payload = _payload_data()
        message = str(payload.get("message") or "").strip()
        if not message:
            return _json_error("message_required", status=400, status_code="message_required")

        level = _frontend_log_level(payload.get("level"))
        source = str(payload.get("source") or "frontend").strip()[:80]
        page = str(payload.get("page") or request.path or "").strip()[:300]
        stack = str(payload.get("stack") or "").strip()[:1600]
        extra = str(payload.get("extra") or "").strip()[:1000]
        actor = getattr(current_user, "username", "") or "web"
        logger = logging.getLogger("panel.frontend")
        try:
            logger.log(
                level,
                "frontend source=%s actor=%s page=%s message=%s stack=%s extra=%s",
                source,
                actor,
                page,
                message[:1000],
                stack,
                extra,
            )
        except Exception as exc:
            return _json_error(str(exc) or repr(exc), status=500, status_code="log_write_failed")

        return jsonify({"ok": True})

    @expose("/control/select/<path:node_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def select_target(self, node_id: str):
        if not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            _write_audit("lan.select_target", False, "csrf_failed")
            return redirect(_safe_next_url("ServerView.list"))

        node_id = str(node_id or "").strip()
        if not node_id:
            flash("Не указан node_id.", "warning")
            _write_audit("lan.select_target", False, "node_id_missing")
            return redirect(_safe_next_url("ServerView.list"))

        try:
            backend, _module = _load_lan_backend()
            peers = list(backend.get_peers() or [])
            peer = None
            for item in peers:
                if str(item.get("node_id") or "") == node_id:
                    peer = dict(item)
                    break
            if not peer:
                flash("Узел не найден в списке ЛВС. Обновите обнаружение и повторите попытку.", "warning")
                _write_audit("lan.select_target", False, f"peer_not_found:{node_id}")
                return redirect(_safe_next_url("ServerView.list"))

            panel_url = str(peer.get("panel_url") or "").strip()
            if not panel_url:
                flash("Выбранный узел не публикует URL веб-панели.", "warning")
                _write_audit("lan.select_target", False, f"panel_missing:{node_id}")
                return redirect(_safe_next_url("ServerView.list"))

            remote_ctx = set_remote_target_from_peer(peer)
            if not remote_ctx.active:
                flash("Не удалось включить удалённый режим для выбранного узла.", "danger")
                _write_audit("lan.select_target", False, f"context_invalid:{node_id}")
                return redirect(_safe_next_url("ServerView.list"))

            peer_control_states = _load_peer_control_states()
            selected_state = _normalize_peer_control_state(peer_control_states.get(remote_ctx.node_id))
            if selected_state.get("status") == STATUS_APPROVED and selected_state.get("grant_token"):
                apply_remote_grant_payload(selected_state)

            grant_state = ensure_remote_grant(remote_ctx, force_new=False, allow_create=True)
            grant_status = str(grant_state.get("status") or "").strip().lower()
            status_message = str(grant_state.get("message") or "").strip()
            _set_peer_control_state(remote_ctx.node_id, grant_state)
            if grant_status == STATUS_PENDING:
                flash(
                    f"Выбран удалённый узел: {remote_ctx.display_name}. Запрос доступа ожидает подтверждения.",
                    "warning",
                )
            elif grant_status in {STATUS_EXPIRED, STATUS_CANCELLED}:
                flash(
                    f"Выбран удалённый узел: {remote_ctx.display_name}. {status_message or 'Запрос доступа неактивен.'}",
                    "warning",
                )
            elif grant_state.get("ok"):
                flash(
                    f"Удалённый режим включён для {remote_ctx.display_name}. Все разделы панели теперь работают через прокси.",
                    "success",
                )
            else:
                flash(
                    (
                        f"Выбран удалённый узел: {remote_ctx.display_name}. "
                        f"{status_message or 'Разрешение ещё не подтверждено. Нажмите «Запросить доступ».'}"
                    ),
                    "warning",
                )

            _write_audit(
                "lan.select_target",
                True,
                (
                    f"node_id={remote_ctx.node_id} "
                    f"name={remote_ctx.name} "
                    f"ip={remote_ctx.ip} "
                    f"version={remote_ctx.app_version} "
                    f"grant_status={grant_status or '-'}"
                ),
            )
        except Exception as exc:
            _write_audit("lan.select_target", False, str(exc))
            flash(f"Не удалось включить удалённый режим: {exc}", "danger")
        return redirect(_safe_next_url("WacIndexView.index"))

    @expose("/control/request/<path:node_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def request_control(self, node_id: str):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, status=400, status_code="csrf_failed")

        node = str(node_id or "").strip()
        if not node:
            return _json_error("Не указан node_id.", status=400, status_code="node_id_missing")
        try:
            backend, _module = _load_lan_backend()
            peers = list(backend.get_peers() or [])
            peer = None
            for item in peers:
                if str(item.get("node_id") or "") == node:
                    peer = dict(item)
                    break
            if not peer:
                _write_audit("lan.request_control", False, f"peer_not_found:{node}")
                return _json_error("Узел не найден в списке ЛВС.", status=404, status_code="peer_not_found")
            result = request_remote_access_for_peer(peer)
            status_code = 200
            status_text = str(result.get("status") or "").strip().lower()
            if status_text in {"disabled", "denied"}:
                status_code = 403
            elif status_text in {STATUS_PENDING, "manual_required"}:
                status_code = 202
            elif not result.get("ok"):
                status_code = 400
            state_entry = _set_peer_control_state(node, result)
            _write_audit(
                "lan.request_control",
                bool(result.get("ok")),
                f"node_id={node} status={status_text or '-'}",
            )
            response_payload = dict(result)
            response_payload["node_id"] = node
            response_payload["peer_control_state"] = state_entry
            response_payload["ok"] = bool(result.get("ok", True))
            return jsonify(response_payload), status_code
        except Exception as exc:
            _write_audit("lan.request_control", False, str(exc))
            return _json_error(f"Не удалось создать запрос доступа: {exc}", status=500, status_code="internal_error")

    @expose("/control/request/<path:request_id>/respond", methods=["POST"])
    @has_access
    @permission_name("action")
    def respond_control_request(self, request_id: str):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, status=400, status_code="csrf_failed")

        rid = str(request_id or "").strip()
        if not rid:
            return _json_error("Не указан request_id.", status=400, status_code="request_id_missing")

        data = _payload_data()
        approve = parse_bool(data.get("approve"), False)
        remember = parse_bool(data.get("remember"), False)
        note = str(data.get("note") or "").strip()
        actor = getattr(current_user, "username", "") or "web"
        try:
            result = respond_to_request(
                request_id=rid,
                approve=bool(approve),
                remember=bool(remember),
                actor=str(actor),
                note=note,
            )
            status_text = str(result.get("status") or "").strip().lower()
            status_code = 200
            if result.get("status") == "not_found":
                status_code = 404
            elif status_text in {STATUS_EXPIRED, STATUS_CANCELLED}:
                status_code = 409
            elif not result.get("ok"):
                status_code = 400

            _write_audit(
                "lan.respond_control_request",
                bool(result.get("ok")),
                (
                    f"request_id={rid} "
                    f"approve={int(bool(approve))} "
                    f"remember={int(bool(remember))} "
                    f"status={status_text or '-'}"
                ),
            )
            return jsonify(result), status_code
        except Exception as exc:
            _write_audit("lan.respond_control_request", False, str(exc))
            return _json_error(f"Не удалось обработать ответ на запрос: {exc}", status=500, status_code="internal_error")

    @expose("/control/policy/<path:controller_node_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def update_control_policy(self, controller_node_id: str):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, status=400, status_code="csrf_failed")

        node = str(controller_node_id or "").strip()
        if not node:
            return _json_error("Не указан controller_node_id.", status=400, status_code="node_id_missing")

        data = _payload_data()
        decision = str(data.get("decision") or DECISION_PROMPT).strip().lower()
        if decision not in {DECISION_ALLOW, DECISION_DENY, DECISION_PROMPT}:
            return _json_error(
                "Поле decision должно быть одним из: allow, deny, prompt.",
                status=400,
                status_code="bad_decision",
            )
        remember = parse_bool(data.get("remember"), decision != DECISION_PROMPT)
        if decision == DECISION_PROMPT:
            remember = False
        controller_name = str(data.get("controller_name") or "").strip()
        controller_ip = str(data.get("controller_ip") or "").strip()
        actor = getattr(current_user, "username", "") or "web"
        try:
            row = upsert_policy(
                controller_node_id=node,
                controller_name=controller_name,
                controller_ip=controller_ip,
                decision=decision,
                remember=bool(remember),
                actor=str(actor),
            )
            result = _policy_to_dict(row)
            result.update({"ok": True})
            _write_audit(
                "lan.update_control_policy",
                True,
                f"controller={node} decision={decision} remember={int(bool(remember))}",
            )
            return jsonify(result)
        except Exception as exc:
            _write_audit("lan.update_control_policy", False, str(exc))
            return _json_error(f"Не удалось сохранить правило: {exc}", status=500, status_code="internal_error")

    @expose("/peer/remove/<path:node_id>", methods=["POST"])
    @has_access
    @permission_name("action")
    def remove_peer(self, node_id: str):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, status=400, status_code="csrf_failed")

        node = str(node_id or "").strip()
        if not node:
            return _json_error("Не указан node_id.", status=400, status_code="node_id_missing")

        try:
            backend, _module = _load_lan_backend()
            remover = getattr(backend, "remove_peer", None)
            if not callable(remover):
                _write_audit("lan.remove_peer", False, "not_supported")
                return _json_error(
                    "Текущая версия службы ЛВС не поддерживает удаление отдельных узлов.",
                    status=501,
                    status_code="not_supported",
                )

            removed = bool(remover(node))
            if not removed:
                _write_audit("lan.remove_peer", False, f"peer_not_found:{node}")
                return _json_error(
                    "Узел не найден в текущем списке обнаружения.",
                    status=404,
                    status_code="peer_not_found",
                )

            _remove_peer_control_state(node)
            cleared_target = False
            current_target = get_remote_target_context(force_refresh=False)
            if current_target.active and current_target.node_id == node:
                clear_remote_target()
                cleared_target = True

            _write_audit(
                "lan.remove_peer",
                True,
                f"node_id={node} cleared_target={int(cleared_target)}",
            )
            return jsonify(
                {
                    "ok": True,
                    "node_id": node,
                    "message": (
                        "Узел удалён из списка обнаружения. "
                        + (
                            "Текущий удалённый режим отключён."
                            if cleared_target
                            else "Связанные временные статусы очищены."
                        )
                    ),
                }
            )
        except Exception as exc:
            _write_audit("lan.remove_peer", False, str(exc))
            return _json_error(
                f"Не удалось удалить узел: {exc}",
                status=500,
                status_code="internal_error",
            )

    @expose("/control/clear", methods=["GET", "POST"])
    @has_access
    @permission_name("action")
    def clear_target(self):
        if request.method == "POST" and not _is_csrf_valid():
            flash(_CSRF_FAILURE_MESSAGE, "danger")
            _write_audit("lan.clear_target", False, "csrf_failed")
            return redirect(_safe_next_url("ServerView.list"))

        try:
            previous = get_remote_target_context(force_refresh=False)
            clear_remote_target()
            _write_audit(
                "lan.clear_target",
                True,
                f"previous_node={previous.node_id or '-'} previous_name={previous.display_name or '-'}",
            )
            flash("Удалённый режим отключён. Панель переключена на локальный узел.", "success")
        except Exception as exc:
            _write_audit("lan.clear_target", False, str(exc))
            flash(f"Не удалось отключить удалённый режим: {exc}", "danger")
        return redirect(_safe_next_url("ServerView.list"))
