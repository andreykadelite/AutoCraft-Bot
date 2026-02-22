from __future__ import annotations

from typing import Any

from flask import Response, current_app, jsonify, request, send_from_directory
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from ..ops.base import run_operation
from ..ops.operations.live_stream import get_hls_dir, get_status, list_devices

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)


def _is_csrf_valid() -> bool:
    token = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _json_error(message: str, code: int = 400) -> Response:
    return Response(
        jsonify({"ok": False, "error": message}).data,
        status=code,
        mimetype="application/json",
    )


class LiveStreamView(BaseView):
    route_base = "/live-stream"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        can_write = self.appbuilder.sm.has_access("can_action", self.class_permission_name)
        return self.render_template(
            "live_stream.html",
            can_write=can_write,
        )

    @expose("/api/status")
    @has_access_api
    @permission_name("list")
    def status(self):
        data = get_status()
        return jsonify({"ok": True, "data": data})

    @expose("/api/devices")
    @has_access_api
    @permission_name("list")
    def devices(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        data = list_devices(base_dir)
        return jsonify({"ok": True, "data": data})

    @expose("/api/start", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def start(self):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, code=400)
        payload: dict[str, Any] = request.get_json(silent=True) or request.form.to_dict()
        video = (payload.get("video") or payload.get("camera") or "").strip()
        video_alt = (payload.get("video_alt") or "").strip()
        audio = (payload.get("audio") or "").strip()
        if not video:
            return _json_error("Не выбрана камера.", code=400)
        base_dir = current_app.config.get("BASE_DIR", "")
        result = run_operation(
            operation="stream.start",
            params={
                "base_dir": base_dir,
                "video_name": video,
                "video_alt": video_alt or None,
                "audio_name": audio or None,
            },
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        ok = bool(result.get("ok", False))
        if not ok:
            return _json_error(result.get("stderr") or "Ошибка запуска.", code=400)
        return jsonify({"ok": True, "message": result.get("stdout") or "Трансляция запущена."})

    @expose("/api/stop", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def stop(self):
        if not _is_csrf_valid():
            return _json_error(_CSRF_FAILURE_MESSAGE, code=400)
        result = run_operation(
            operation="stream.stop",
            params={},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        ok = bool(result.get("ok", False))
        if not ok:
            return _json_error(result.get("stderr") or "Ошибка остановки.", code=400)
        return jsonify({"ok": True, "message": result.get("stdout") or "Трансляция остановлена."})

    @expose("/hls/<path:filename>")
    @has_access_api
    @permission_name("list")
    def hls(self, filename: str):
        hls_dir = get_hls_dir()
        if not hls_dir or not filename:
            return _json_error("Поток не запущен.", code=404)
        ext = filename.lower().rsplit(".", 1)[-1]
        if ext not in ("m3u8", "m3u", "ts"):
            return _json_error("Недопустимый файл.", code=400)
        resp = send_from_directory(hls_dir, filename, conditional=True)
        if ext in ("m3u8", "m3u"):
            resp.mimetype = "application/vnd.apple.mpegurl"
        elif ext == "ts":
            resp.mimetype = "video/MP2T"
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp
