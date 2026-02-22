from __future__ import annotations

import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import after_this_request, jsonify, request, send_file, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..db import db
from ..models.audit import AuditLog
from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from ..ops.base import run_operation
from ..ops.operations.registry import (
    RegistryError,
    build_registry_export,
    build_registry_full_export_with_progress,
    cleanup_export_file,
    default_registry_view,
    get_roots_ui,
    get_supported_types,
    get_value,
    list_key,
    registry_available,
    search_registry,
)

_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)

_DEFAULT_ROOT = "HKCU"
_EXPORT_TASK_TTL = 60 * 60


@dataclass
class ExportTask:
    task_id: str
    status: str = "running"
    progress: float = 0.0
    message: str = ""
    filename: str = ""
    file_path: Path | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)


_EXPORT_TASKS: dict[str, ExportTask] = {}
_EXPORT_TASKS_LOCK = threading.Lock()


def _create_export_task() -> ExportTask:
    task = ExportTask(task_id=str(uuid.uuid4()))
    with _EXPORT_TASKS_LOCK:
        _EXPORT_TASKS[task.task_id] = task
    return task


def _get_export_task(task_id: str) -> ExportTask | None:
    with _EXPORT_TASKS_LOCK:
        return _EXPORT_TASKS.get(task_id)


def _drop_export_task(task_id: str) -> ExportTask | None:
    with _EXPORT_TASKS_LOCK:
        return _EXPORT_TASKS.pop(task_id, None)


def _cleanup_export_tasks() -> None:
    now = time.time()
    with _EXPORT_TASKS_LOCK:
        expired = [
            task_id
            for task_id, task in _EXPORT_TASKS.items()
            if now - task.created_at > _EXPORT_TASK_TTL
        ]
        for task_id in expired:
            task = _EXPORT_TASKS.pop(task_id, None)
            if task and task.file_path:
                cleanup_export_file(task.file_path)


def _log_audit(action: str, target: str, result: str, details: str, user: str | None = None, ip: str = "") -> None:
    try:
        log = AuditLog(
            user=user or getattr(current_user, "username", "web"),
            action=action,
            target=target,
            result=result,
            source="web",
            ip=ip,
            details=details,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass


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


def _operation_response(result: dict):
    ok = bool(result.get("ok", False))
    if ok:
        message = result.get("stdout") or "Операция выполнена."
        return jsonify({"ok": True, "message": message})
    error = result.get("stderr") or "Операция не выполнена."
    status = 403 if "Недостаточно прав" in error else 400
    return jsonify({"ok": False, "error": error}), status


def _run_full_export_task(task_id: str, view: str, filename: str, actor: str) -> None:
    task = _get_export_task(task_id)
    if not task:
        return

    def _progress(value: float) -> None:
        with _EXPORT_TASKS_LOCK:
            task.progress = max(0.0, min(1.0, float(value)))
            task.message = f"{int(task.progress * 100)}%"

    try:
        export_path, download_name = build_registry_full_export_with_progress(
            view=view,
            suggested_name=filename or None,
            progress_cb=_progress,
        )
        with _EXPORT_TASKS_LOCK:
            task.status = "done"
            task.progress = 1.0
            task.message = "Готово"
            task.filename = download_name
            task.file_path = export_path
        _log_audit(
            action="registry.export_full",
            target="ALL",
            result="ok",
            details=f"view={view or 'default'} file={download_name}",
            user=actor,
            ip="",
        )
    except Exception as exc:
        with _EXPORT_TASKS_LOCK:
            task.status = "error"
            task.error = str(exc)
            task.message = "Ошибка"
        _log_audit(
            action="registry.export_full",
            target="ALL",
            result="fail",
            details=str(exc),
            user=actor,
            ip="",
        )


class RegistryEditorView(BaseView):
    route_base = "/registry"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        available, message = registry_available()
        can_write = self.appbuilder.sm.has_access("can_action", self.class_permission_name)
        default_view = default_registry_view()
        return self.render_template(
            "registry_editor.html",
            registry_available=available,
            registry_message=message,
            can_write=can_write,
            registry_roots=get_roots_ui(),
            registry_types=get_supported_types(),
            default_view=default_view,
            default_root=_DEFAULT_ROOT,
        )

    @expose("/api/key")
    @has_access_api
    @permission_name("list")
    def api_key(self):
        path = (request.args.get("path") or "").strip()
        view = (request.args.get("view") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        try:
            data = list_key(path, view=view)
            return jsonify({"ok": True, "data": data})
        except RegistryError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Ошибка чтения реестра: {exc}"}), 500

    @expose("/api/value")
    @has_access_api
    @permission_name("list")
    def api_value(self):
        path = (request.args.get("path") or "").strip()
        name = request.args.get("name", "")
        view = (request.args.get("view") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        try:
            data = get_value(path, name or "", view=view)
            return jsonify({"ok": True, "data": data})
        except RegistryError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Ошибка чтения значения: {exc}"}), 500

    @expose("/api/search")
    @has_access_api
    @permission_name("list")
    def api_search(self):
        path = (request.args.get("path") or "").strip()
        query = (request.args.get("query") or "").strip()
        view = (request.args.get("view") or "").strip()
        max_depth = int(request.args.get("depth") or 4)
        max_results = int(request.args.get("limit") or 200)
        search_keys = str(request.args.get("keys") or "1") != "0"
        search_values = str(request.args.get("values") or "1") != "0"
        search_data = str(request.args.get("data") or "0") == "1"
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        if not query:
            return jsonify({"ok": False, "error": "Введите текст для поиска."}), 400
        try:
            data = search_registry(
                path,
                query,
                view=view,
                max_depth=max_depth,
                max_results=max_results,
                search_keys=search_keys,
                search_values=search_values,
                search_data=search_data,
            )
            return jsonify({"ok": True, "data": data})
        except RegistryError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Ошибка поиска: {exc}"}), 500

    @expose("/api/export")
    @has_access_api
    @permission_name("list")
    def api_export(self):
        path = (request.args.get("path") or "").strip()
        view = (request.args.get("view") or "").strip()
        mode = (request.args.get("mode") or "").strip().lower()
        filename = (request.args.get("filename") or "").strip()
        export_all = mode == "all"
        try:
            export_path, download_name = build_registry_export(
                path,
                view=view,
                export_all=export_all,
                suggested_name=filename or None,
            )
        except RegistryError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Ошибка экспорта: {exc}"}), 500

        try:
            ip = request.remote_addr or ""
        except Exception:
            ip = ""
        _log_audit(
            action="registry.export",
            target=path if path else "ALL",
            result="ok",
            details=f"mode={mode or 'branch'} view={view or 'default'} file={download_name}",
            ip=ip,
        )

        @after_this_request
        def _cleanup(response):
            try:
                cleanup_export_file(export_path)
            except Exception:
                pass
            return response

        return send_file(
            export_path,
            as_attachment=True,
            download_name=download_name,
            mimetype="application/octet-stream",
        )

    @expose("/api/export/full/start", methods=["POST"])
    @has_access_api
    @permission_name("list")
    def api_export_full_start(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        payload = request.get_json(silent=True) or {}
        view = (payload.get("view") or "").strip()
        filename = (payload.get("filename") or "").strip()
        _cleanup_export_tasks()
        task = _create_export_task()
        actor = getattr(current_user, "username", "web")
        thread = threading.Thread(
            target=_run_full_export_task,
            args=(task.task_id, view, filename, actor),
            daemon=True,
        )
        thread.start()
        return jsonify({"ok": True, "task_id": task.task_id})

    @expose("/api/export/full/status/<task_id>")
    @has_access_api
    @permission_name("list")
    def api_export_full_status(self, task_id: str):
        _cleanup_export_tasks()
        task = _get_export_task(task_id)
        if not task:
            return jsonify({"ok": False, "error": "Задача не найдена."}), 404
        download_url = None
        if task.status == "done" and task.file_path:
            download_url = url_for("RegistryEditorView.api_export_full_download", task_id=task_id)
        return jsonify(
            {
                "ok": True,
                "status": task.status,
                "progress": task.progress,
                "message": task.message,
                "error": task.error,
                "download_url": download_url,
            }
        )

    @expose("/api/export/full/download/<task_id>")
    @has_access_api
    @permission_name("list")
    def api_export_full_download(self, task_id: str):
        task = _get_export_task(task_id)
        if not task or task.status != "done" or not task.file_path:
            return jsonify({"ok": False, "error": "Файл еще не готов."}), 400

        @after_this_request
        def _cleanup(response):
            try:
                if task.file_path:
                    cleanup_export_file(task.file_path)
            finally:
                _drop_export_task(task_id)
            return response

        return send_file(
            task.file_path,
            as_attachment=True,
            download_name=task.filename or task.file_path.name,
            mimetype="application/octet-stream",
        )

    @expose("/api/import", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def api_import(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            return jsonify({"ok": False, "error": "Файл не выбран."}), 400
        if not uploaded.filename.lower().endswith(".reg"):
            return jsonify({"ok": False, "error": "Поддерживаются только .reg файлы."}), 400

        with tempfile.NamedTemporaryFile(delete=False, suffix=".reg") as temp:
            temp_path = Path(temp.name)
            uploaded.save(temp.name)

        result = run_operation(
            operation="registry.import",
            params={"file_path": str(temp_path)},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return _operation_response(result)

    @expose("/api/key/create", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def api_key_create(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        payload = request.get_json(silent=True) or {}
        path = (payload.get("path") or "").strip()
        view = (payload.get("view") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        result = run_operation(
            operation="registry.key.create",
            params={"path": path, "view": view},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return _operation_response(result)

    @expose("/api/key/delete", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def api_key_delete(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        payload = request.get_json(silent=True) or {}
        path = (payload.get("path") or "").strip()
        view = (payload.get("view") or "").strip()
        recursive = bool(payload.get("recursive", True))
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        result = run_operation(
            operation="registry.key.delete",
            params={"path": path, "recursive": recursive, "view": view},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return _operation_response(result)

    @expose("/api/key/rename", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def api_key_rename(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        payload = request.get_json(silent=True) or {}
        path = (payload.get("path") or "").strip()
        new_name = (payload.get("new_name") or "").strip()
        view = (payload.get("view") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        if not new_name:
            return jsonify({"ok": False, "error": "Введите новое имя ключа."}), 400
        result = run_operation(
            operation="registry.key.rename",
            params={"path": path, "new_name": new_name, "view": view},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return _operation_response(result)

    @expose("/api/value/set", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def api_value_set(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        payload = request.get_json(silent=True) or {}
        path = (payload.get("path") or "").strip()
        name = payload.get("name", "")
        value_type = payload.get("value_type")
        data = payload.get("data")
        view = (payload.get("view") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        if not value_type:
            return jsonify({"ok": False, "error": "Укажите тип значения."}), 400
        result = run_operation(
            operation="registry.value.set",
            params={
                "path": path,
                "name": name or "",
                "value_type": value_type,
                "data": data,
                "view": view,
            },
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return _operation_response(result)

    @expose("/api/value/delete", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def api_value_delete(self):
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        payload = request.get_json(silent=True) or {}
        path = (payload.get("path") or "").strip()
        name = payload.get("name", "")
        view = (payload.get("view") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "Укажите путь ключа."}), 400
        result = run_operation(
            operation="registry.value.delete",
            params={"path": path, "name": name or "", "view": view},
            actor=getattr(current_user, "username", "web"),
            source="web",
        )
        return _operation_response(result)
