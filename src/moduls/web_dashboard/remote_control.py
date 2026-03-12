from __future__ import annotations

import html
import importlib
import ipaddress
import threading
import time
import uuid
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from flask import (
    Response,
    current_app,
    g,
    jsonify,
    request,
    session,
    stream_with_context,
    url_for,
)
from flask_login import UserMixin, current_user

from .config import load_config
from .remote_access_service import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    load_remote_access_settings,
    validate_grant,
)

_SESSION_REMOTE_NODE_ID = "panel_remote_node_id"
_SESSION_REMOTE_NAME = "panel_remote_name"
_SESSION_REMOTE_IP = "panel_remote_ip"
_SESSION_REMOTE_VERSION = "panel_remote_version"
_SESSION_REMOTE_PANEL_URL = "panel_remote_panel_url"
_SESSION_REMOTE_API_TOKEN = "panel_remote_api_token"
_SESSION_REMOTE_CAPS = "panel_remote_caps"
_SESSION_REMOTE_TARGET_ENABLED = "panel_remote_target_enabled"
_SESSION_REMOTE_REQUIRE_APPROVAL = "panel_remote_require_approval"
_SESSION_PROXY_CLIENT_ID = "panel_remote_proxy_client_id"
_SESSION_REMOTE_GRANT_TOKEN = "panel_remote_grant_token"
_SESSION_REMOTE_GRANT_EXPIRES_AT = "panel_remote_grant_expires_at"
_SESSION_REMOTE_REQUEST_ID = "panel_remote_request_id"
_SESSION_REMOTE_GRANT_STATUS = "panel_remote_grant_status"
_SESSION_REMOTE_LAST_MESSAGE = "panel_remote_last_message"

_HEADER_PROXY_TOKEN = "X-Autocraft-Proxy-Token"
_HEADER_PROXY_ACTOR = "X-Autocraft-Proxy-Actor"
_HEADER_PROXY_ROLES = "X-Autocraft-Proxy-Roles"
_HEADER_PROXY_HOP = "X-Autocraft-Proxy-Hop"
_HEADER_PROXY_SOURCE = "X-Autocraft-Proxy-Source"
_HEADER_PROXY_CONTROLLER_NODE = "X-Autocraft-Controller-Node"
_HEADER_PROXY_GRANT = "X-Autocraft-Remote-Grant"

_EXCLUDED_PROXY_PREFIXES = (
    "/servers",
    "/health",
    "/ready",
    "/setup",
    "/logout",
    "/login",
    "/api/login",
    "/api/health",
    "/api/ready",
)
_EXCLUDED_PROXY_EXACT = {"/favicon.ico"}

_UPSTREAM_SESSIONS_LOCK = threading.RLock()
_UPSTREAM_SESSIONS: dict[str, dict[str, Any]] = {}
_UPSTREAM_SESSION_TTL_SEC = 45 * 60

_SUPPORTED_CAPABILITIES = (
    "proxy_html_v1",
    "proxy_auth_roles_v1",
    "proxy_stream_v1",
    "proxy_static_v1",
)
_PROXY_PROTOCOL_VERSION = 1


@dataclass
class RemoteTargetContext:
    active: bool = False
    node_id: str = ""
    name: str = ""
    ip: str = ""
    panel_url: str = ""
    api_token: str = ""
    app_version: str = ""
    role: str = ""
    target_enabled: bool = True
    require_approval: bool = True
    capabilities: List[str] | None = None
    reason: str = ""

    @property
    def can_proxy(self) -> bool:
        if not self.active:
            return False
        if not self.panel_url or not self.api_token:
            return False
        caps = set(self.capabilities or [])
        if not caps:
            return False
        return "proxy_html_v1" in caps and "proxy_auth_roles_v1" in caps

    @property
    def display_name(self) -> str:
        return self.name or self.ip or self.node_id or "узел"


class _ProxyAuthUser(UserMixin):
    def __init__(self, username: str, roles: list[Any], user_id: int = 0):
        super().__init__()
        safe_username = (username or "proxy").strip() or "proxy"
        try:
            self.id = int(user_id)
        except Exception:
            self.id = 0
        self.username = safe_username
        self.roles = roles
        self.groups: list[Any] = []
        self.active = True
        self.first_name = "Proxy"
        self.last_name = safe_username
        self.email = f"{safe_username}@proxy.local"

    @property
    def is_active(self) -> bool:
        return True

    def get_full_name(self) -> str:
        first = str(getattr(self, "first_name", "") or "").strip()
        last = str(getattr(self, "last_name", "") or "").strip()
        full_name = f"{first} {last}".strip()
        return full_name or self.username


def _bind_proxy_user(user: Any) -> None:
    login_manager = getattr(current_app, "login_manager", None)
    update_user = getattr(login_manager, "_update_request_context_with_user", None)
    if callable(update_user):
        try:
            update_user(user)
            return
        except Exception:
            pass
    g._login_user = user


def _resolve_proxy_user_id(sm: Any, actor: str) -> int:
    username = str(actor or "").strip()
    if not username:
        return 0
    finder = getattr(sm, "find_user", None)
    if not callable(finder):
        return 0
    try:
        user = finder(username=username)
    except TypeError:
        try:
            user = finder(username)
        except Exception:
            user = None
    except Exception:
        user = None
    if user is None:
        lowered = username.lower()
        if lowered and lowered != username:
            try:
                user = finder(username=lowered)
            except Exception:
                user = None
    if user is None:
        return 0
    try:
        return int(getattr(user, "id", 0) or 0)
    except Exception:
        return 0


def get_supported_capabilities() -> list[str]:
    return list(_SUPPORTED_CAPABILITIES)


def get_proxy_protocol_version() -> int:
    return _PROXY_PROTOCOL_VERSION


def _load_lan_backend():
    last_error: Exception | None = None
    for module_name in (
        "gui_win.gui_win_lan_discovery_window",
        "moduls.gui_win.gui_win_lan_discovery_window",
    ):
        try:
            module = importlib.import_module(module_name)
            getter = getattr(module, "get_lan_discovery_backend", None)
            if callable(getter):
                return getter()
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Служба ЛВС недоступна")


def _normalize_panel_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return ""
    if parts.scheme not in {"http", "https"}:
        return ""
    if not parts.netloc:
        return ""
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _extract_caps(peer: Dict[str, Any]) -> list[str]:
    direct = peer.get("panel_capabilities")
    if isinstance(direct, list):
        return [str(item).strip() for item in direct if str(item).strip()]

    services = peer.get("services")
    if isinstance(services, dict):
        panel = services.get("panel")
        if isinstance(panel, dict):
            caps = panel.get("capabilities")
            if isinstance(caps, list):
                return [str(item).strip() for item in caps if str(item).strip()]
    return []


def _extract_api_token(peer: Dict[str, Any]) -> str:
    token = str(peer.get("panel_api_token") or "").strip()
    if token:
        return token
    services = peer.get("services")
    if isinstance(services, dict):
        panel = services.get("panel")
        if isinstance(panel, dict):
            token = str(panel.get("api_token") or "").strip()
            if token:
                return token
    return ""


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _find_peer(node_id: str) -> Dict[str, Any] | None:
    if not node_id:
        return None
    backend = _load_lan_backend()
    peers = list(backend.get_peers() or [])
    for peer in peers:
        if str(peer.get("node_id") or "") == node_id:
            return dict(peer)
    return None


def set_remote_target_from_peer(
    peer: Dict[str, Any],
    reset_grant: bool = True,
    persist_session: bool = True,
) -> RemoteTargetContext:
    node_id = str(peer.get("node_id") or "").strip()
    panel_url = _normalize_panel_url(str(peer.get("panel_url") or ""))
    api_token = _extract_api_token(peer)
    caps = _extract_caps(peer)
    target_enabled = _to_bool(peer.get("remote_control_target_enabled"), True)
    require_approval = _to_bool(peer.get("remote_control_require_approval"), True)
    ctx = RemoteTargetContext(
        active=bool(node_id and panel_url),
        node_id=node_id,
        name=str(peer.get("instance_name") or peer.get("hostname") or "").strip(),
        ip=str(peer.get("ip") or "").strip(),
        panel_url=panel_url,
        api_token=api_token,
        app_version=str(peer.get("app_version") or "").strip(),
        role=str(peer.get("role") or "").strip(),
        target_enabled=target_enabled,
        require_approval=require_approval,
        capabilities=caps,
        reason="" if (node_id and panel_url) else "invalid_target",
    )
    if ctx.active and persist_session:
        session[_SESSION_REMOTE_NODE_ID] = ctx.node_id
        session[_SESSION_REMOTE_NAME] = ctx.name
        session[_SESSION_REMOTE_IP] = ctx.ip
        session[_SESSION_REMOTE_VERSION] = ctx.app_version
        session[_SESSION_REMOTE_PANEL_URL] = ctx.panel_url
        session[_SESSION_REMOTE_API_TOKEN] = ctx.api_token
        session[_SESSION_REMOTE_CAPS] = ",".join(ctx.capabilities or [])
        session[_SESSION_REMOTE_TARGET_ENABLED] = "1" if ctx.target_enabled else "0"
        session[_SESSION_REMOTE_REQUIRE_APPROVAL] = "1" if ctx.require_approval else "0"
        if reset_grant:
            session.pop(_SESSION_REMOTE_GRANT_TOKEN, None)
            session.pop(_SESSION_REMOTE_GRANT_EXPIRES_AT, None)
            session.pop(_SESSION_REMOTE_REQUEST_ID, None)
            session.pop(_SESSION_REMOTE_GRANT_STATUS, None)
            session.pop(_SESSION_REMOTE_LAST_MESSAGE, None)
    return ctx


def clear_remote_target() -> None:
    node_id = str(session.get(_SESSION_REMOTE_NODE_ID) or "").strip()
    client_id = str(session.get(_SESSION_PROXY_CLIENT_ID) or "").strip()
    for key in (
        _SESSION_REMOTE_NODE_ID,
        _SESSION_REMOTE_NAME,
        _SESSION_REMOTE_IP,
        _SESSION_REMOTE_VERSION,
        _SESSION_REMOTE_PANEL_URL,
        _SESSION_REMOTE_API_TOKEN,
        _SESSION_REMOTE_CAPS,
        _SESSION_REMOTE_TARGET_ENABLED,
        _SESSION_REMOTE_REQUIRE_APPROVAL,
        _SESSION_REMOTE_GRANT_TOKEN,
        _SESSION_REMOTE_GRANT_EXPIRES_AT,
        _SESSION_REMOTE_REQUEST_ID,
        _SESSION_REMOTE_GRANT_STATUS,
        _SESSION_REMOTE_LAST_MESSAGE,
    ):
        session.pop(key, None)

    if not node_id or not client_id:
        return

    marker = f"{client_id}::{node_id}::"
    with _UPSTREAM_SESSIONS_LOCK:
        doomed = [cache_key for cache_key in _UPSTREAM_SESSIONS.keys() if cache_key.startswith(marker)]
        for cache_key in doomed:
            payload = _UPSTREAM_SESSIONS.pop(cache_key, None) or {}
            sess = payload.get("session")
            try:
                if sess is not None:
                    sess.close()
            except Exception:
                pass


def get_remote_target_context(force_refresh: bool = True) -> RemoteTargetContext:
    node_id = str(session.get(_SESSION_REMOTE_NODE_ID) or "").strip()
    if not node_id:
        return RemoteTargetContext(active=False, reason="not_selected")

    if not force_refresh:
        cached_panel = _normalize_panel_url(str(session.get(_SESSION_REMOTE_PANEL_URL) or ""))
        cached_caps = [
            item.strip()
            for item in str(session.get(_SESSION_REMOTE_CAPS) or "").split(",")
            if item.strip()
        ]
        if cached_panel:
            return RemoteTargetContext(
                active=True,
                node_id=node_id,
                name=str(session.get(_SESSION_REMOTE_NAME) or ""),
                ip=str(session.get(_SESSION_REMOTE_IP) or ""),
                panel_url=cached_panel,
                api_token=str(session.get(_SESSION_REMOTE_API_TOKEN) or ""),
                app_version=str(session.get(_SESSION_REMOTE_VERSION) or ""),
                target_enabled=_to_bool(session.get(_SESSION_REMOTE_TARGET_ENABLED), True),
                require_approval=_to_bool(session.get(_SESSION_REMOTE_REQUIRE_APPROVAL), True),
                capabilities=cached_caps,
                reason="",
            )

    try:
        peer = _find_peer(node_id)
    except Exception as exc:
        return RemoteTargetContext(
            active=False,
            node_id=node_id,
            name=str(session.get(_SESSION_REMOTE_NAME) or ""),
            ip=str(session.get(_SESSION_REMOTE_IP) or ""),
            reason=str(exc) or "backend_error",
        )

    if not peer:
        return RemoteTargetContext(
            active=False,
            node_id=node_id,
            name=str(session.get(_SESSION_REMOTE_NAME) or ""),
            ip=str(session.get(_SESSION_REMOTE_IP) or ""),
            reason="peer_not_found",
        )

    return set_remote_target_from_peer(peer, reset_grant=False)


def _safe_iso_to_epoch(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt_obj = dt.datetime.fromisoformat(text)
        if dt_obj.tzinfo is None:
            return dt_obj.timestamp()
        return dt_obj.astimezone(dt.timezone.utc).timestamp()
    except Exception:
        return 0.0


def _local_controller_identity() -> dict[str, str]:
    identity = {"node_id": "", "name": "", "ip": "", "panel_url": ""}
    try:
        backend = _load_lan_backend()
        settings = dict(backend.load_settings(force_reload=True))
        status = dict(backend.get_status() or {})
    except Exception:
        settings = {}
        status = {}

    identity["node_id"] = str(settings.get("node_id") or "").strip()
    identity["name"] = str(settings.get("instance_name") or "").strip()

    ips = status.get("local_ips")
    if isinstance(ips, list):
        for item in ips:
            raw = str(item or "").strip()
            if raw and not raw.startswith("127."):
                identity["ip"] = raw
                break
    if not identity["ip"]:
        identity["ip"] = str(request.remote_addr or "").strip()

    if not identity["panel_url"]:
        scheme = str(settings.get("panel_scheme") or "http").strip().lower() or "http"
        if scheme not in {"http", "https"}:
            scheme = "http"
        path = str(settings.get("panel_path") or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        host_override = str(settings.get("panel_host_override") or "").strip()
        host = host_override or identity["ip"] or "127.0.0.1"
        try:
            port = int(settings.get("panel_port") or 0)
        except Exception:
            port = 0
        if 1 <= port <= 65535:
            identity["panel_url"] = f"{scheme}://{host}:{port}{path}"

    if not identity["panel_url"]:
        try:
            identity["panel_url"] = request.url_root.rstrip("/") + "/"
        except Exception:
            identity["panel_url"] = ""
    return identity


def _remote_api_url(ctx: RemoteTargetContext, suffix_path: str) -> str:
    base = urlsplit(ctx.panel_url)
    base_prefix = (base.path or "").rstrip("/")
    suffix = str(suffix_path or "/").strip()
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    full = f"{base_prefix}{suffix}" if base_prefix else suffix
    if not full.startswith("/"):
        full = "/" + full
    return urlunsplit((base.scheme, base.netloc, full, "", ""))


def _store_grant_payload(payload: dict[str, Any]) -> None:
    grant = str(payload.get("grant_token") or "").strip()
    request_id = str(payload.get("request_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    message = str(payload.get("message") or "").strip()
    expires_epoch = _safe_iso_to_epoch(str(payload.get("grant_expires_at") or ""))
    if not expires_epoch:
        try:
            expires_epoch = float(payload.get("grant_expires_epoch") or 0.0)
        except Exception:
            expires_epoch = 0.0
    if not expires_epoch:
        expires_epoch = time.time() + 30.0
    if grant and status == STATUS_APPROVED:
        session[_SESSION_REMOTE_GRANT_TOKEN] = grant
        session[_SESSION_REMOTE_GRANT_EXPIRES_AT] = float(expires_epoch)
    else:
        session.pop(_SESSION_REMOTE_GRANT_TOKEN, None)
        session.pop(_SESSION_REMOTE_GRANT_EXPIRES_AT, None)
    if request_id:
        session[_SESSION_REMOTE_REQUEST_ID] = request_id
    else:
        session.pop(_SESSION_REMOTE_REQUEST_ID, None)
    session[_SESSION_REMOTE_GRANT_STATUS] = status
    session[_SESSION_REMOTE_LAST_MESSAGE] = message


def get_remote_grant_state() -> dict[str, Any]:
    now_ts = time.time()
    status = str(session.get(_SESSION_REMOTE_GRANT_STATUS) or "").strip().lower()
    message = str(session.get(_SESSION_REMOTE_LAST_MESSAGE) or "").strip()
    request_id = str(session.get(_SESSION_REMOTE_REQUEST_ID) or "").strip()
    token = str(session.get(_SESSION_REMOTE_GRANT_TOKEN) or "").strip()
    try:
        expires_at = float(session.get(_SESSION_REMOTE_GRANT_EXPIRES_AT) or 0.0)
    except Exception:
        expires_at = 0.0

    if token and expires_at > now_ts + 1.0:
        status = STATUS_APPROVED
    elif status == STATUS_APPROVED:
        status = STATUS_EXPIRED

    if not status:
        status = "idle"

    expires_in = int(expires_at - now_ts) if expires_at > 0 else None
    if isinstance(expires_in, int) and expires_in < 0:
        expires_in = 0
    return {
        "status": status,
        "message": message,
        "request_id": request_id,
        "grant_token_present": bool(token),
        "grant_expires_epoch": expires_at if expires_at > 0 else 0.0,
        "grant_expires_in_sec": expires_in,
    }


def apply_remote_grant_payload(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    _store_grant_payload(dict(payload))


def request_remote_access_for_peer(peer: Dict[str, Any]) -> dict[str, Any]:
    ctx = set_remote_target_from_peer(peer, reset_grant=True, persist_session=False)
    if not ctx.active:
        return {"ok": False, "status": "error", "message": "Удалённый целевой узел неактивен."}
    return _request_remote_access(ctx, persist_session=False)


def poll_remote_request_for_peer(peer: Dict[str, Any], request_id: str) -> dict[str, Any]:
    ctx = set_remote_target_from_peer(peer, reset_grant=False, persist_session=False)
    if not ctx.active:
        return {"ok": False, "status": "error", "message": "Удалённый целевой узел неактивен."}
    return _poll_remote_request(ctx, request_id=request_id, persist_session=False)


def _request_remote_access(
    ctx: RemoteTargetContext,
    persist_session: bool = True,
) -> dict[str, Any]:
    identity = _local_controller_identity()
    payload = {
        "controller_node_id": identity.get("node_id") or "",
        "controller_name": identity.get("name") or "",
        "controller_ip": identity.get("ip") or "",
        "controller_panel_url": identity.get("panel_url") or "",
        "requester_user": _current_actor(),
        "requested_roles": _current_roles(),
    }
    if not payload["controller_node_id"]:
        return {
            "ok": False,
            "status": "error",
            "message": "В локальных настройках ЛВС отсутствует controller node_id.",
        }

    url = _remote_api_url(ctx, "/api/remote-control/request")
    upstream_session = _get_upstream_session(ctx, _current_actor())
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ctx.api_token}",
    }
    try:
        response = upstream_session.post(url, json=payload, headers=headers, timeout=(5, 20))
        data = response.json() if response.content else {}
    except Exception as exc:
        return {"ok": False, "status": "error", "message": f"Не удалось запросить доступ: {exc}"}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("ok", response.ok)
    if not response.ok and not data.get("message"):
        data["message"] = f"Запрос завершился ошибкой HTTP {response.status_code}."
    if persist_session:
        _store_grant_payload(data)
    return data


def _poll_remote_request(
    ctx: RemoteTargetContext,
    request_id: str,
    persist_session: bool = True,
) -> dict[str, Any]:
    rid = str(request_id or "").strip()
    if not rid:
        return {"ok": False, "status": "error", "message": "Отсутствует request_id."}
    identity = _local_controller_identity()
    node_id = identity.get("node_id") or ""
    query = f"controller_node_id={quote(node_id, safe='')}" if node_id else ""
    url = _remote_api_url(ctx, f"/api/remote-control/request/{rid}/status")
    if query:
        url = f"{url}?{query}"
    upstream_session = _get_upstream_session(ctx, _current_actor())
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {ctx.api_token}",
    }
    try:
        response = upstream_session.get(url, headers=headers, timeout=(5, 15))
        data = response.json() if response.content else {}
    except Exception as exc:
        return {"ok": False, "status": "error", "message": f"Не удалось получить статус запроса: {exc}"}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("ok", response.ok)
    if not response.ok and not data.get("message"):
        data["message"] = f"Запрос статуса завершился ошибкой HTTP {response.status_code}."
    if persist_session:
        _store_grant_payload(data)
    return data


def ensure_remote_grant(
    ctx: RemoteTargetContext,
    force_new: bool = False,
    allow_create: bool = True,
    persist_session: bool = True,
) -> dict[str, Any]:
    if not ctx.active:
        return {"ok": False, "status": "error", "message": "Удалённый целевой узел не активен."}
    settings = load_remote_access_settings()
    auto_request_enabled = _to_bool(settings.get("auto_request_on_select"), True)
    auto_create_allowed = bool(allow_create)
    if not _to_bool(settings.get("controller_enabled"), True):
        return {
            "ok": False,
            "status": "disabled",
            "message": "На этом узле отключена роль управляющего узла.",
        }
    if not ctx.target_enabled:
        return {
            "ok": False,
            "status": "disabled",
            "message": "На выбранном узле отключена роль управляемого узла.",
        }
    now_ts = time.time()
    if not force_new:
        cached_token = str(session.get(_SESSION_REMOTE_GRANT_TOKEN) or "").strip()
        expires_at = float(session.get(_SESSION_REMOTE_GRANT_EXPIRES_AT) or 0.0)
        if cached_token and expires_at > now_ts + 5.0:
            return {
                "ok": True,
                "status": STATUS_APPROVED,
                "grant_token": cached_token,
                "grant_expires_epoch": expires_at,
                "request_id": str(session.get(_SESSION_REMOTE_REQUEST_ID) or ""),
                "message": str(session.get(_SESSION_REMOTE_LAST_MESSAGE) or "Доступ разрешен."),
            }

        request_id = str(session.get(_SESSION_REMOTE_REQUEST_ID) or "").strip()
        if request_id:
            polled = _poll_remote_request(
                ctx,
                request_id,
                persist_session=persist_session,
            )
            status = str(polled.get("status") or "").strip().lower()
            if polled.get("ok") and status == STATUS_APPROVED:
                return polled
            if status in {STATUS_PENDING, STATUS_DENIED}:
                return polled
            if status in {STATUS_EXPIRED, STATUS_CANCELLED} and not (
                force_new or (auto_request_enabled and auto_create_allowed)
            ):
                return polled

    if not force_new and not (auto_request_enabled and auto_create_allowed):
        return {
            "ok": False,
            "status": "manual_required",
            "message": "Автоматический запрос отключен. Используйте кнопку «Запросить доступ» на странице «Серверы».",
        }

    created = _request_remote_access(ctx, persist_session=persist_session)
    return created


def _is_proxy_excluded_path(path: str) -> bool:
    raw = str(path or "").strip() or "/"
    if raw in _EXCLUDED_PROXY_EXACT:
        return True
    return any(raw.startswith(prefix) for prefix in _EXCLUDED_PROXY_PREFIXES)


def _session_client_id() -> str:
    value = str(session.get(_SESSION_PROXY_CLIENT_ID) or "").strip()
    if value:
        return value
    value = uuid.uuid4().hex
    session[_SESSION_PROXY_CLIENT_ID] = value
    return value


def _cleanup_upstream_sessions(now_ts: float) -> None:
    stale_keys = []
    for key, payload in _UPSTREAM_SESSIONS.items():
        last_used = float(payload.get("last_used") or 0.0)
        if last_used <= 0.0:
            stale_keys.append(key)
            continue
        if now_ts - last_used > _UPSTREAM_SESSION_TTL_SEC:
            stale_keys.append(key)
    for key in stale_keys:
        payload = _UPSTREAM_SESSIONS.pop(key, None) or {}
        sess = payload.get("session")
        try:
            if sess is not None:
                sess.close()
        except Exception:
            pass


def _upstream_session_key(ctx: RemoteTargetContext, actor: str) -> str:
    return f"{_session_client_id()}::{ctx.node_id}::{actor}"


def _get_upstream_session(ctx: RemoteTargetContext, actor: str) -> requests.Session:
    now_ts = time.time()
    key = _upstream_session_key(ctx, actor)
    with _UPSTREAM_SESSIONS_LOCK:
        _cleanup_upstream_sessions(now_ts)
        existing = _UPSTREAM_SESSIONS.get(key)
        if existing and isinstance(existing.get("session"), requests.Session):
            existing["last_used"] = now_ts
            return existing["session"]

        sess = requests.Session()
        sess.headers.update({"User-Agent": "AutoCraft-Panel-Proxy/1.0"})
        _UPSTREAM_SESSIONS[key] = {"session": sess, "last_used": now_ts}
        return sess


def _current_actor() -> str:
    actor = str(getattr(current_user, "username", "") or "").strip()
    return actor or "web"


def _current_roles() -> list[str]:
    roles = []
    try:
        roles = [str(getattr(role, "name", "") or "").strip() for role in current_user.roles]  # type: ignore[attr-defined]
    except Exception:
        roles = []
    return [name for name in roles if name]


def _build_upstream_url(ctx: RemoteTargetContext) -> str:
    parts = urlsplit(ctx.panel_url)
    prefix = (parts.path or "").rstrip("/")
    req_path = request.path or "/"
    if not req_path.startswith("/"):
        req_path = "/" + req_path
    full_path = f"{prefix}{req_path}" if prefix else req_path
    if not full_path.startswith("/"):
        full_path = "/" + full_path
    query = request.query_string.decode("utf-8", errors="ignore")
    return urlunsplit((parts.scheme, parts.netloc, full_path, query, ""))


def _forwardable_headers(ctx: RemoteTargetContext) -> dict[str, str]:
    headers: dict[str, str] = {}
    skip = {
        "host",
        "content-length",
        "connection",
        "cookie",
        "set-cookie",
        _HEADER_PROXY_TOKEN.lower(),
        _HEADER_PROXY_ACTOR.lower(),
        _HEADER_PROXY_ROLES.lower(),
        _HEADER_PROXY_HOP.lower(),
        _HEADER_PROXY_SOURCE.lower(),
        _HEADER_PROXY_CONTROLLER_NODE.lower(),
        _HEADER_PROXY_GRANT.lower(),
    }

    for key, value in request.headers.items():
        low = key.lower()
        if low in skip:
            continue
        headers[key] = value

    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
    remote_addr = request.remote_addr or ""
    if forwarded_for:
        headers["X-Forwarded-For"] = f"{forwarded_for}, {remote_addr}" if remote_addr else forwarded_for
    elif remote_addr:
        headers["X-Forwarded-For"] = remote_addr

    hop = 0
    try:
        hop = int(str(request.headers.get(_HEADER_PROXY_HOP) or "0").strip() or "0")
    except Exception:
        hop = 0

    headers[_HEADER_PROXY_TOKEN] = ctx.api_token
    headers[_HEADER_PROXY_ACTOR] = _current_actor()
    headers[_HEADER_PROXY_ROLES] = ",".join(_current_roles())
    headers[_HEADER_PROXY_SOURCE] = str(current_app.config.get("DEVICE_NAME") or "")
    headers[_HEADER_PROXY_HOP] = str(max(1, hop + 1))
    headers[_HEADER_PROXY_CONTROLLER_NODE] = str(_local_controller_identity().get("node_id") or "")
    headers[_HEADER_PROXY_GRANT] = str(session.get(_SESSION_REMOTE_GRANT_TOKEN) or "")
    return headers


def _rewrite_location(location: str, ctx: RemoteTargetContext) -> str:
    raw = str(location or "").strip()
    if not raw:
        return raw

    base = urlsplit(ctx.panel_url)
    target = urlsplit(raw)
    if target.scheme and target.netloc:
        if target.netloc != base.netloc:
            return raw
        path = target.path or "/"
    else:
        path = target.path or "/"

    prefix = (base.path or "").rstrip("/")
    if prefix and path.startswith(prefix):
        path = path[len(prefix) :]
        if not path.startswith("/"):
            path = "/" + path
    if not path.startswith("/"):
        path = "/" + path
    return urlunsplit(("", "", path, target.query, target.fragment))


def _proxy_response_headers(upstream: requests.Response, ctx: RemoteTargetContext, streaming: bool) -> dict[str, str]:
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "set-cookie",
    }
    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        low = key.lower()
        if low in hop_by_hop:
            continue
        if not streaming and low in {"content-length", "content-encoding"}:
            continue
        if low == "location":
            headers[key] = _rewrite_location(value, ctx)
            continue
        headers[key] = value
    return headers


def _is_streaming(content_type: str, path: str) -> bool:
    ctype = str(content_type or "").lower()
    p = str(path or "").lower()
    if "multipart/x-mixed-replace" in ctype:
        return True
    if "text/event-stream" in ctype:
        return True
    return "/stream" in p or "/hls/" in p


def _inject_banner(body: str, ctx: RemoteTargetContext) -> str:
    if "id=\"autocraft-remote-banner\"" in body:
        return body

    full_path = request.full_path or request.path or "/"
    if full_path.endswith("?"):
        full_path = full_path[:-1]
    clear_url = f"{url_for('ServerView.clear_target')}?next={quote(full_path, safe='/?=&:%')}"
    servers_url = url_for("ServerView.list")
    safe_name = html.escape(ctx.display_name)
    safe_ip = html.escape(ctx.ip or "-")
    safe_version = html.escape(ctx.app_version or "-")
    safe_role = html.escape(ctx.role or "-")

    banner = (
        "<div id=\"autocraft-remote-banner\" "
        "style=\"position:sticky;top:0;z-index:9999;padding:8px 12px;"
        "background:#1f2937;color:#f8fafc;border-bottom:1px solid #374151;"
        "font:14px/1.4 Arial,sans-serif;\">"
        f"<strong>Режим удалённого управления:</strong> {safe_name} ({safe_ip}) "
        f"| роль={safe_role} | версия={safe_version} "
        f"| <a href=\"{servers_url}\" style=\"color:#93c5fd;\">Серверы</a> "
        f"| <a href=\"{clear_url}\" style=\"color:#fca5a5;\">Переключиться на локальный узел</a>"
        "</div>"
    )
    style = "<style>body{padding-top:0!important;}</style>"
    payload = banner + style

    marker = body.lower().find("<body")
    if marker >= 0:
        end_tag = body.find(">", marker)
        if end_tag >= 0:
            return body[: end_tag + 1] + payload + body[end_tag + 1 :]
    return payload + body


def _render_unavailable(ctx: RemoteTargetContext, reason: str, status: int = 409) -> Response:
    servers_url = url_for("ServerView.list")
    clear_url = url_for("ServerView.clear_target")
    remote_url = html.escape(ctx.panel_url or "")
    body = (
        "<html><head><meta charset=\"utf-8\"><title>Удалённое управление недоступно</title></head>"
        "<body style=\"font:16px/1.5 Arial,sans-serif;padding:24px;\">"
        "<h2>Удалённое управление недоступно</h2>"
        f"<p>{html.escape(reason)}</p>"
        f"<p>Целевой узел: <b>{html.escape(ctx.display_name)}</b></p>"
        f"<p>URL панели: {remote_url or '-'}</p>"
        f"<p><a href=\"{servers_url}\">Вернуться к серверам</a> | "
        f"<a href=\"{clear_url}\">Отключить удалённый режим</a></p>"
        "</body></html>"
    )
    return Response(body.encode("utf-8"), status=status, content_type="text/html; charset=utf-8")


def proxy_current_request(ctx: RemoteTargetContext) -> Response:
    if not ctx.active:
        return _render_unavailable(ctx, "Удалённый целевой узел не активен.", status=404)
    if not ctx.can_proxy:
        return _render_unavailable(
            ctx,
            "Выбранный узел не поддерживает проксируемое управление. Обновите удалённый узел до актуальной версии веб-панели.",
            status=409,
        )

    grant_state = ensure_remote_grant(ctx, force_new=False, allow_create=True)
    grant_status = str(grant_state.get("status") or "").strip().lower()
    if not grant_state.get("ok") or grant_status != STATUS_APPROVED:
        if grant_status == STATUS_PENDING:
            return _render_unavailable(
                ctx,
                grant_state.get("message")
                or "Запрос доступа ожидает подтверждения. Подтвердите его на целевом узле.",
                status=409,
            )
        if grant_status == STATUS_DENIED:
            return _render_unavailable(
                ctx,
                grant_state.get("message") or "Запрос доступа был отклонен целевым узлом.",
                status=403,
            )
        return _render_unavailable(
            ctx,
            grant_state.get("message") or "Не удалось получить разрешение доступа от целевого узла.",
            status=403,
        )

    method = (request.method or "GET").upper()
    upstream_url = _build_upstream_url(ctx)
    actor = _current_actor()
    upstream_session = _get_upstream_session(ctx, actor)
    headers = _forwardable_headers(ctx)
    body = request.get_data(cache=True, as_text=False) if method in {"POST", "PUT", "PATCH", "DELETE"} else None

    timeout = (5, 120)
    if "/stream" in (request.path or ""):
        timeout = (5, 300)

    try:
        upstream = upstream_session.request(
            method=method,
            url=upstream_url,
            headers=headers,
            data=body,
            allow_redirects=False,
            stream=True,
            timeout=timeout,
        )
    except Exception as exc:
        try:
            current_app.logger.warning("Ошибка прокси-запроса к удаленному узлу: %s", exc)
        except Exception:
            pass
        return _render_unavailable(ctx, f"Не удалось подключиться к удаленному узлу: {exc}", status=502)

    content_type = str(upstream.headers.get("Content-Type") or "")
    streaming = _is_streaming(content_type, request.path or "")
    proxy_headers = _proxy_response_headers(upstream, ctx, streaming=streaming)

    if streaming:
        def _iter_chunks():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

        return Response(
            stream_with_context(_iter_chunks()),
            status=upstream.status_code,
            headers=proxy_headers,
            direct_passthrough=True,
        )

    try:
        payload = upstream.content
    finally:
        try:
            upstream.close()
        except Exception:
            pass

    if upstream.status_code == 404 and not (request.path or "").startswith("/static/"):
        return _render_unavailable(
            ctx,
            "Запрошенная функция отсутствует на удаленном узле. Скорее всего, на узле установлена устаревшая версия панели.",
            status=404,
        )

    if "text/html" in content_type.lower():
        encoding = upstream.encoding or "utf-8"
        try:
            text = payload.decode(encoding, errors="replace")
        except Exception:
            text = payload.decode("utf-8", errors="replace")
        text = _inject_banner(text, ctx)
        payload = text.encode("utf-8")
        proxy_headers["Content-Type"] = "text/html; charset=utf-8"

    return Response(payload, status=upstream.status_code, headers=proxy_headers)


def register_inbound_proxy_auth(app) -> None:
    @app.before_request
    def _inbound_proxy_auth():
        token = str(request.headers.get(_HEADER_PROXY_TOKEN) or "").strip()
        if not token:
            return None

        hop = str(request.headers.get(_HEADER_PROXY_HOP) or "").strip()
        if not hop:
            return jsonify({"error": "invalid_proxy_request"}), 400

        remote_ip = str(request.remote_addr or "").strip()
        if remote_ip:
            try:
                ip_obj = ipaddress.ip_address(remote_ip)
                if not (ip_obj.is_loopback or ip_obj.is_private):
                    return jsonify({"error": "forbidden_proxy_source"}), 403
            except Exception:
                return jsonify({"error": "forbidden_proxy_source"}), 403

        cfg = load_config(current_app.config.get("BASE_DIR"))
        if token != str(cfg.api_token or "").strip():
            return jsonify({"error": "invalid_proxy_token"}), 403

        remote_settings = load_remote_access_settings()
        if not _to_bool(remote_settings.get("target_enabled"), True):
            return jsonify({"error": "managed_node_mode_disabled"}), 403

        controller_node_id = str(request.headers.get(_HEADER_PROXY_CONTROLLER_NODE) or "").strip()
        grant_token = str(request.headers.get(_HEADER_PROXY_GRANT) or "").strip()
        if not controller_node_id or not grant_token:
            return jsonify({"error": "missing_proxy_grant"}), 403
        grant_ok, grant_reason = validate_grant(controller_node_id, grant_token)
        if not grant_ok:
            return jsonify({"error": "invalid_proxy_grant", "reason": grant_reason}), 403

        appbuilder = getattr(current_app, "appbuilder", None)
        sm = getattr(appbuilder, "sm", None) if appbuilder else None
        if sm is None:
            return jsonify({"error": "security_manager_unavailable"}), 500

        actor = str(request.headers.get(_HEADER_PROXY_ACTOR) or "").strip() or "proxy"
        role_names = [
            item.strip()
            for item in str(request.headers.get(_HEADER_PROXY_ROLES) or "").split(",")
            if item.strip()
        ]
        if not role_names:
            role_names = ["Viewer"]

        role_objects = []
        for role_name in role_names:
            try:
                role = sm.find_role(role_name)
            except Exception:
                role = None
            if role is not None:
                role_objects.append(role)
        if not role_objects:
            fallback = sm.find_role("Viewer") or sm.find_role("Operator") or sm.find_role("Admin")
            if fallback is not None:
                role_objects = [fallback]

        proxy_user_id = _resolve_proxy_user_id(sm, actor)
        proxy_user = _ProxyAuthUser(actor, role_objects, user_id=proxy_user_id)
        _bind_proxy_user(proxy_user)
        g.user = proxy_user
        g.autocraft_proxy_request = True
        g.autocraft_proxy_actor = actor
        g.autocraft_proxy_roles = [str(getattr(role, "name", "")) for role in role_objects]
        g.autocraft_proxy_controller_node = controller_node_id
        return None


def register_outbound_proxy(app) -> None:
    @app.before_request
    def _outbound_proxy():
        if getattr(g, "autocraft_proxy_request", False):
            return None
        if request.method == "OPTIONS":
            return None
        if request.headers.get(_HEADER_PROXY_HOP):
            return None
        if _is_proxy_excluded_path(request.path or ""):
            return None
        if not current_user.is_authenticated:
            return None

        ctx = get_remote_target_context(force_refresh=True)
        g.remote_target_context = ctx
        if not ctx.active:
            return None
        return proxy_current_request(ctx)


def register_template_context(app) -> None:
    @app.context_processor
    def _inject_remote_target():
        try:
            ctx = get_remote_target_context(force_refresh=True)
        except Exception:
            ctx = RemoteTargetContext(active=False)
        return {"remote_target": ctx}
