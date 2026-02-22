from __future__ import annotations

import os
import shutil
import string
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from flask import current_app, jsonify, request, send_file, url_for
from flask_appbuilder import BaseView, expose
from flask_appbuilder.security.decorators import permission_name

from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api
from flask_login import current_user
from flask_wtf.csrf import validate_csrf

from ..db import db
from ..models.audit import AuditLog
from ..utils import ensure_dir

_BUFFER_SIZE = 1024 * 1024 * 2
_TASK_TTL_SECONDS = 2 * 60 * 60
_DOWNLOAD_TTL_SECONDS = 2 * 60 * 60
_CSRF_FAILURE_MESSAGE = (
    "Подтверждение не прошло или истекло. "
    "Обновите страницу и повторите действие."
)


@dataclass
class FileTask:
    task_id: str
    action: str
    progress: float = 0.0
    status: str = "running"
    message: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    result: dict[str, Any] = field(default_factory=dict)


_TASKS: dict[str, FileTask] = {}
_TASKS_LOCK = threading.Lock()
_DOWNLOADS: dict[str, tuple[Path, float]] = {}


def _is_csrf_valid() -> bool:
    token = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or request.args.get("csrf_token")
        or ""
    )
    if not token:
        return False
    try:
        validate_csrf(token)
    except Exception:
        return False
    return True


def _normalize_path(value: str | None, base_dir: str) -> Path | None:
    value = (value or "").strip()
    if not value or value in ("/", "root"):
        return None
    if os.name == "nt" and len(value) == 2 and value[1] == ":":
        value = value + "\\"
    if not os.path.isabs(value):
        value = str(Path(base_dir) / value)
    path = Path(value)
    try:
        return path.resolve(strict=False)
    except Exception:
        return path


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except Exception:
        return False


def _is_drive_root(path: Path) -> bool:
    try:
        anchor = Path(path.anchor)
        return path.resolve(strict=False) == anchor.resolve(strict=False)
    except Exception:
        return str(path) == path.anchor


def _format_datetime(ts: float | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _can_read(path: Path) -> bool:
    try:
        return os.access(path, os.R_OK)
    except Exception:
        return False


def _can_write(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except Exception:
        return False


def _list_drives() -> list[Path]:
    drives: list[Path] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            candidate = Path(f"{letter}:\\")
            if candidate.exists():
                drives.append(candidate)
    else:
        drives.append(Path("/"))
        for root in (Path("/mnt"), Path("/media")):
            if root.is_dir():
                for child in root.iterdir():
                    if child.is_dir():
                        drives.append(child)
    return drives


def _quick_links(base_dir: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []

    def _add(label: str, path: Path | None) -> None:
        if not path:
            return
        try:
            if path.exists():
                links.append({"label": label, "path": str(path)})
        except Exception:
            return

    home = Path.home()
    _add("Домашняя папка", home)
    _add("Рабочий стол", home / "Desktop")
    _add("Документы", home / "Documents")
    _add("Загрузки", home / "Downloads")
    _add("Изображения", home / "Pictures")
    _add("Каталог программы", Path(base_dir))
    _add("Данные", Path(base_dir) / "data")
    _add("Логи", Path(base_dir) / "log")
    _add("Временные файлы", Path(tempfile.gettempdir()))
    return links


def _breadcrumbs(path: Path) -> list[dict[str, str]]:
    parts = path.parts
    crumbs: list[dict[str, str]] = []
    if os.name == "nt":
        if not parts:
            return crumbs
        current = Path(parts[0])
        crumbs.append({"label": parts[0].rstrip("\\/"), "path": str(current)})
        for part in parts[1:]:
            current = current / part
            crumbs.append({"label": part, "path": str(current)})
        return crumbs

    if not parts:
        return crumbs
    current = Path("/")
    crumbs.append({"label": "/", "path": str(current)})
    for part in parts[1:]:
        current = current / part
        crumbs.append({"label": part, "path": str(current)})
    return crumbs


def _describe_entry(entry: Path) -> dict[str, Any]:
    try:
        stat = entry.stat()
        modified = _format_datetime(stat.st_mtime)
        size = stat.st_size if entry.is_file() else None
    except Exception:
        modified = ""
        size = None
    return {
        "name": entry.name,
        "path": str(entry),
        "is_dir": entry.is_dir(),
        "is_file": entry.is_file(),
        "is_symlink": entry.is_symlink(),
        "size": size,
        "modified": modified,
        "readable": _can_read(entry),
        "writable": _can_write(entry),
    }


def _list_directory(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    entries = []
    try:
        entries = list(path.iterdir())
    except Exception:
        entries = []

    entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    for entry in entries:
        items.append(_describe_entry(entry))
    return items


def _scan_dir(path: Path, limit: int = 50000) -> dict[str, Any]:
    total_bytes = 0
    total_files = 0
    total_dirs = 0
    truncated = False

    for root, dirs, files in os.walk(path):
        total_dirs += len(dirs)
        for name in files:
            total_files += 1
            if total_files + total_dirs > limit:
                truncated = True
                break
            file_path = Path(root) / name
            try:
                total_bytes += file_path.stat().st_size
            except Exception:
                continue
        if truncated:
            break

    return {
        "total_bytes": total_bytes,
        "total_files": total_files,
        "total_dirs": total_dirs,
        "truncated": truncated,
    }


def _sanitize_name(name: str) -> str:
    clean = (name or "").strip()
    clean = clean.replace("/", "_").replace("\\", "_")
    return clean or "Новый объект"


def _unique_destination(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    for i in range(1, 500):
        if i == 1:
            name = f"{stem} (копия){suffix}"
        else:
            name = f"{stem} (копия {i}){suffix}"
        candidate = parent / name
        if not candidate.exists():
            return candidate
    return dest


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for root, _dirs, files in os.walk(path):
                for name in files:
                    yield Path(root) / name


def _total_bytes(paths: Iterable[Path]) -> int:
    total = 0
    for file_path in _iter_files(paths):
        try:
            total += file_path.stat().st_size
        except Exception:
            continue
    return total


def _copy_file(src: Path, dst: Path, progress_cb: Callable[[int], None]) -> None:
    ensure_dir(dst.parent)
    with src.open("rb") as in_f, dst.open("wb") as out_f:
        while True:
            chunk = in_f.read(_BUFFER_SIZE)
            if not chunk:
                break
            out_f.write(chunk)
            progress_cb(len(chunk))
    try:
        shutil.copystat(src, dst, follow_symlinks=False)
    except Exception:
        pass


def _copy_path(src: Path, dest_dir: Path, progress_cb: Callable[[int], None]) -> Path:
    if src.is_file():
        target = _unique_destination(dest_dir / src.name)
        _copy_file(src, target, progress_cb)
        return target

    target_root = _unique_destination(dest_dir / src.name)
    ensure_dir(target_root)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        current = target_root / rel
        for dirname in dirs:
            ensure_dir(current / dirname)
        for filename in files:
            source_file = Path(root) / filename
            target_file = current / filename
            _copy_file(source_file, target_file, progress_cb)
    return target_root


def _delete_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _count_delete_targets(paths: Iterable[Path]) -> int:
    count = 0
    for path in paths:
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                count += len(dirs) + len(files)
        count += 1
    return count


def _iter_delete_targets(path: Path) -> Iterable[Path]:
    if path.is_dir():
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                yield Path(root) / name
            for name in dirs:
                yield Path(root) / name
        yield path
    else:
        yield path


def _create_task(action: str, message: str = "") -> FileTask:
    task = FileTask(task_id=uuid.uuid4().hex, action=action, message=message)
    with _TASKS_LOCK:
        _TASKS[task.task_id] = task
    _cleanup_tasks()
    return task


def _cleanup_tasks() -> None:
    now = time.time()
    with _TASKS_LOCK:
        expired = [tid for tid, t in _TASKS.items() if now - t.created_at > _TASK_TTL_SECONDS]
        for tid in expired:
            _TASKS.pop(tid, None)


def _update_task(task_id: str, *, progress: float | None = None, message: str | None = None) -> None:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        if progress is not None:
            task.progress = max(0.0, min(100.0, progress))
        if message is not None:
            task.message = message


def _finish_task(
    task_id: str,
    *,
    status: str,
    message: str = "",
    error: str = "",
    result: dict[str, Any] | None = None,
) -> None:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task.status = status
        task.progress = 100.0
        task.message = message
        task.error = error
        if result is not None:
            task.result = result


def _cleanup_downloads() -> None:
    now = time.time()
    expired: list[str] = []
    for token, (_path, created_at) in list(_DOWNLOADS.items()):
        if now - created_at > _DOWNLOAD_TTL_SECONDS:
            expired.append(token)
    for token in expired:
        path, _created_at = _DOWNLOADS.pop(token, (None, None))
        if path and path.exists():
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def _audit_action(user: str, action: str, target: str, ok: bool, source: str, ip: str, details: str = "") -> None:
    try:
        log = AuditLog(
            user=user or "web",
            action=action,
            target=target,
            result="ok" if ok else "fail",
            source=source,
            ip=ip,
            details=details,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _handle_delete_task(task_id: str, paths: list[Path], user: str, ip: str, app) -> None:
    errors: list[str] = []
    total_targets = max(_count_delete_targets(paths), 1)
    processed = 0

    def _update() -> None:
        progress = (processed / total_targets) * 100
        _update_task(task_id, progress=progress, message="Удаление файлов...")

    with app.app_context():
        for path in paths:
            for target in _iter_delete_targets(path):
                try:
                    _delete_path(target)
                except Exception as exc:
                    errors.append(f"{target}: {exc}")
                processed += 1
                _update()

        if errors:
            details = "\n".join(errors[:5])
            _finish_task(
                task_id,
                status="error",
                message="Удаление завершено с ошибками.",
                error=details,
            )
            _audit_action(user, "file.delete", ",".join(str(p) for p in paths), False, "web", ip, details)
        else:
            _finish_task(task_id, status="done", message="Удаление завершено.")
            _audit_action(user, "file.delete", ",".join(str(p) for p in paths), True, "web", ip)


def _handle_copy_task(
    task_id: str,
    paths: list[Path],
    destination: Path,
    move: bool,
    user: str,
    ip: str,
    app,
) -> None:
    total_bytes = max(_total_bytes(paths), 1)
    processed = 0
    errors: list[str] = []

    def _progress(delta: int) -> None:
        nonlocal processed
        processed += delta
        progress = (processed / total_bytes) * 100
        _update_task(task_id, progress=progress, message="Копирование файлов...")

    with app.app_context():
        for path in paths:
            try:
                _copy_path(path, destination, _progress)
            except Exception as exc:
                errors.append(f"{path}: {exc}")

        if move and not errors:
            for path in paths:
                try:
                    _delete_path(path)
                except Exception as exc:
                    errors.append(f"{path}: {exc}")

        if errors:
            details = "\n".join(errors[:5])
            _finish_task(
                task_id,
                status="error",
                message="Операция завершена с ошибками.",
                error=details,
            )
            action = "file.move" if move else "file.copy"
            _audit_action(user, action, ",".join(str(p) for p in paths), False, "web", ip, details)
        else:
            _finish_task(task_id, status="done", message="Операция завершена.")
            action = "file.move" if move else "file.copy"
            _audit_action(user, action, ",".join(str(p) for p in paths), True, "web", ip)


def _handle_zip_task(task_id: str, paths: list[Path], zip_path: Path, user: str, ip: str, app) -> None:
    total_bytes = max(_total_bytes(paths), 1)
    processed = 0

    def _progress(delta: int) -> None:
        nonlocal processed
        processed += delta
        progress = (processed / total_bytes) * 100
        _update_task(task_id, progress=progress, message="Подготовка архива...")

    with app.app_context():
        try:
            ensure_dir(zip_path.parent)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for path in paths:
                    if path.is_file():
                        zipf.write(path, arcname=path.name)
                        _progress(path.stat().st_size)
                    elif path.is_dir():
                        for root, _dirs, files in os.walk(path):
                            for name in files:
                                file_path = Path(root) / name
                                arcname = Path(path.name) / file_path.relative_to(path)
                                zipf.write(file_path, arcname=str(arcname))
                                try:
                                    _progress(file_path.stat().st_size)
                                except Exception:
                                    _progress(0)
            _finish_task(
                task_id,
                status="done",
                message="Архив готов к скачиванию.",
                result={"download_token": zip_path.stem},
            )
            _audit_action(user, "file.download", ",".join(str(p) for p in paths), True, "web", ip)
        except Exception as exc:
            try:
                if zip_path.exists():
                    zip_path.unlink(missing_ok=True)
            except Exception:
                pass
            _finish_task(
                task_id,
                status="error",
                message="Не удалось подготовить архив.",
                error=str(exc),
            )
            _audit_action(user, "file.download", ",".join(str(p) for p in paths), False, "web", ip, str(exc))


class FileManagerView(BaseView):
    route_base = "/files"
    base_permissions = ["can_list", "can_action"]

    @expose("/")
    @has_access
    def list(self):
        return self.render_template("file_manager.html")

    @expose("/list")
    @has_access_api
    @permission_name("list")
    def list_api(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        raw_path = request.args.get("path")
        path = _normalize_path(raw_path, base_dir)
        root_mode = path is None

        if root_mode:
            items = []
            for drive in _list_drives():
                info = {
                    "name": str(drive),
                    "path": str(drive),
                    "is_dir": True,
                    "is_file": False,
                    "is_drive": True,
                    "size": None,
                    "modified": "",
                    "readable": _can_read(drive),
                    "writable": _can_write(drive),
                }
                try:
                    usage = shutil.disk_usage(drive)
                    info["size"] = usage.total
                    info["free"] = usage.free
                except Exception:
                    info["free"] = None
                items.append(info)

            return jsonify(
                {
                    "ok": True,
                    "root": True,
                    "current": "",
                    "parent": "",
                    "display": "Корень",
                    "breadcrumbs": [],
                    "items": items,
                    "quick_links": _quick_links(base_dir),
                }
            )

        if not path.exists():
            return jsonify({"ok": False, "error": "Путь не найден."}), 404
        if not path.is_dir():
            return jsonify({"ok": False, "error": "Указанный путь не является папкой."}), 400

        parent = str(path.parent) if path.parent != path else ""
        return jsonify(
            {
                "ok": True,
                "root": False,
                "current": str(path),
                "parent": parent,
                "display": str(path),
                "breadcrumbs": _breadcrumbs(path),
                "items": _list_directory(path),
                "quick_links": _quick_links(base_dir),
            }
        )

    @expose("/properties")
    @has_access_api
    @permission_name("list")
    def properties(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        raw_path = request.args.get("path")
        path = _normalize_path(raw_path, base_dir)
        if path is None or not path.exists():
            return jsonify({"ok": False, "error": "Путь не найден."}), 404

        try:
            stat = path.stat()
            props = {
                "name": path.name,
                "path": str(path),
                "is_dir": path.is_dir(),
                "is_file": path.is_file(),
                "size": stat.st_size if path.is_file() else None,
                "created": _format_datetime(stat.st_ctime),
                "modified": _format_datetime(stat.st_mtime),
                "readable": _can_read(path),
                "writable": _can_write(path),
            }
            if path.is_dir():
                if _is_drive_root(path):
                    try:
                        usage = shutil.disk_usage(path)
                        props.update(
                            {
                                "drive": True,
                                "total_bytes": usage.total,
                                "free_bytes": usage.free,
                                "used_bytes": usage.used,
                            }
                        )
                    except Exception:
                        props.update({"drive": True})
                else:
                    scan = _scan_dir(path)
                    props.update(scan)
            return jsonify({"ok": True, "data": props})
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Не удалось получить свойства: {exc}"}), 500

    @expose("/mkdir", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def mkdir(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        data = request.get_json(silent=True) or {}
        raw_path = data.get("path")
        raw_name = data.get("name", "")
        if not str(raw_name).strip():
            return jsonify({"ok": False, "error": "Введите имя папки."}), 400
        name = _sanitize_name(raw_name)
        parent = _normalize_path(raw_path, base_dir)
        if parent is None or not parent.exists():
            return jsonify({"ok": False, "error": "Папка не найдена."}), 404
        if not parent.is_dir():
            return jsonify({"ok": False, "error": "Указанный путь не является папкой."}), 400
        try:
            target = parent / name
            if target.exists():
                return jsonify({"ok": False, "error": "Папка с таким именем уже существует."}), 400
            ensure_dir(target)
            _audit_action(
                getattr(current_user, "username", "web"),
                "file.mkdir",
                str(target),
                True,
                "web",
                request.remote_addr or "",
            )
            return jsonify({"ok": True, "message": "Папка создана."})
        except Exception as exc:
            _audit_action(
                getattr(current_user, "username", "web"),
                "file.mkdir",
                str(parent),
                False,
                "web",
                request.remote_addr or "",
                str(exc),
            )
            return jsonify({"ok": False, "error": f"Не удалось создать папку: {exc}"}), 500

    @expose("/rename", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def rename(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        data = request.get_json(silent=True) or {}
        raw_path = data.get("path")
        raw_name = data.get("new_name", "")
        if not str(raw_name).strip():
            return jsonify({"ok": False, "error": "Введите новое имя."}), 400
        new_name = _sanitize_name(raw_name)
        path = _normalize_path(raw_path, base_dir)
        if path is None or not path.exists():
            return jsonify({"ok": False, "error": "Файл или папка не найдены."}), 404
        try:
            target = path.with_name(new_name)
            if target.exists():
                return jsonify({"ok": False, "error": "Файл с таким именем уже существует."}), 400
            path.rename(target)
            _audit_action(
                getattr(current_user, "username", "web"),
                "file.rename",
                f"{path} -> {target}",
                True,
                "web",
                request.remote_addr or "",
            )
            return jsonify({"ok": True, "message": "Переименование выполнено."})
        except Exception as exc:
            _audit_action(
                getattr(current_user, "username", "web"),
                "file.rename",
                str(path),
                False,
                "web",
                request.remote_addr or "",
                str(exc),
            )
            return jsonify({"ok": False, "error": f"Не удалось переименовать: {exc}"}), 500

    @expose("/delete", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def delete(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        data = request.get_json(silent=True) or {}
        raw_paths = data.get("paths", [])
        paths = []
        for raw in raw_paths:
            path = _normalize_path(raw, base_dir)
            if path and path.exists():
                paths.append(path)
        if not paths:
            return jsonify({"ok": False, "error": "Не выбран ни один файл."}), 400

        task = _create_task("delete", "Удаление файлов...")
        user = getattr(current_user, "username", "web")
        ip = request.remote_addr or ""
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_handle_delete_task,
            args=(task.task_id, paths, user, ip, app),
            daemon=True,
        )
        thread.start()
        return jsonify({"ok": True, "task_id": task.task_id})

    @expose("/copy", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def copy(self):
        return self._copy_move(move=False)

    @expose("/move", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def move(self):
        return self._copy_move(move=True)

    def _copy_move(self, move: bool):
        base_dir = current_app.config.get("BASE_DIR", "")
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        data = request.get_json(silent=True) or {}
        raw_paths = data.get("paths", [])
        raw_dest = data.get("destination")
        destination = _normalize_path(raw_dest, base_dir)
        if destination is None or not destination.exists():
            return jsonify({"ok": False, "error": "Папка назначения не найдена."}), 404
        if not destination.is_dir():
            return jsonify({"ok": False, "error": "Путь назначения должен быть папкой."}), 400

        paths = []
        for raw in raw_paths:
            path = _normalize_path(raw, base_dir)
            if path and path.exists():
                paths.append(path)
        if not paths:
            return jsonify({"ok": False, "error": "Не выбран ни один файл."}), 400

        for path in paths:
            if path.is_dir():
                try:
                    dest_resolved = destination.resolve(strict=False)
                    src_resolved = path.resolve(strict=False)
                    if dest_resolved == src_resolved or _is_relative_to(dest_resolved, src_resolved):
                        return jsonify(
                            {
                                "ok": False,
                                "error": "Нельзя копировать папку в саму себя или вложенный каталог.",
                            }
                        ), 400
                except Exception:
                    continue

        action = "Перемещение" if move else "Копирование"
        task = _create_task("move" if move else "copy", f"{action} файлов...")
        user = getattr(current_user, "username", "web")
        ip = request.remote_addr or ""
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_handle_copy_task,
            args=(task.task_id, paths, destination, move, user, ip, app),
            daemon=True,
        )
        thread.start()
        return jsonify({"ok": True, "task_id": task.task_id})

    @expose("/prepare_download", methods=["POST"])
    @has_access_api
    @permission_name("list")
    def prepare_download(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        data = request.get_json(silent=True) or {}
        raw_paths = data.get("paths", [])
        paths = []
        for raw in raw_paths:
            path = _normalize_path(raw, base_dir)
            if path and path.exists():
                paths.append(path)
        if not paths:
            return jsonify({"ok": False, "error": "Не выбран ни один файл."}), 400

        if len(paths) == 1 and paths[0].is_file():
            return jsonify(
                {
                    "ok": True,
                    "download_url": self._download_url(path=str(paths[0])),
                }
            )

        token = uuid.uuid4().hex
        temp_dir = Path(tempfile.gettempdir()) / "autocraft_fm"
        zip_path = temp_dir / f"{token}.zip"
        _DOWNLOADS[token] = (zip_path, time.time())
        _cleanup_downloads()

        task = _create_task("download", "Подготовка архива...")
        user = getattr(current_user, "username", "web")
        ip = request.remote_addr or ""
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_handle_zip_task,
            args=(task.task_id, paths, zip_path, user, ip, app),
            daemon=True,
        )
        thread.start()
        return jsonify({"ok": True, "task_id": task.task_id})

    @expose("/download")
    @has_access
    @permission_name("list")
    def download(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        token = request.args.get("token")
        raw_path = request.args.get("path")
        if token:
            entry = _DOWNLOADS.get(token)
            if not entry:
                return "Файл для скачивания не найден.", 404
            zip_path, _created_at = entry
            if not zip_path.exists():
                return "Файл для скачивания не найден.", 404
            response = send_file(zip_path, as_attachment=True, download_name=zip_path.name)

            def _cleanup() -> None:
                _DOWNLOADS.pop(token, None)
                try:
                    zip_path.unlink(missing_ok=True)
                except Exception:
                    pass

            response.call_on_close(_cleanup)
            return response

        if not raw_path:
            return "Не указан файл для скачивания.", 400
        path = _normalize_path(raw_path, base_dir)
        if path is None or not path.exists() or not path.is_file():
            return "Файл для скачивания не найден.", 404
        return send_file(path, as_attachment=True, download_name=path.name)

    @expose("/upload", methods=["POST"])
    @has_access_api
    @permission_name("action")
    def upload(self):
        base_dir = current_app.config.get("BASE_DIR", "")
        if not _is_csrf_valid():
            return jsonify({"ok": False, "error": _CSRF_FAILURE_MESSAGE}), 403
        raw_path = request.args.get("path")
        target_dir = _normalize_path(raw_path, base_dir)
        if target_dir is None or not target_dir.exists():
            return jsonify({"ok": False, "error": "Папка не найдена."}), 404
        if not target_dir.is_dir():
            return jsonify({"ok": False, "error": "Путь назначения должен быть папкой."}), 400

        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "error": "Файлы не выбраны."}), 400

        errors = []
        saved = 0
        for storage in files:
            name = _sanitize_name(storage.filename or "file")
            target = _unique_destination(target_dir / name)
            try:
                storage.save(target)
                saved += 1
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        if errors:
            return jsonify(
                {
                    "ok": False,
                    "error": "Некоторые файлы не удалось сохранить.",
                    "details": errors[:5],
                    "saved": saved,
                }
            ), 500
        _audit_action(
            getattr(current_user, "username", "web"),
            "file.upload",
            str(target_dir),
            True,
            "web",
            request.remote_addr or "",
        )
        return jsonify({"ok": True, "message": "Файлы загружены.", "saved": saved})

    @expose("/task/<task_id>")
    @has_access_api
    @permission_name("list")
    def task_status(self, task_id: str):
        try:
            _cleanup_tasks()
            task = _TASKS.get(task_id)
            if not task:
                return jsonify({"ok": False, "error": "Задача не найдена."}), 404

            download_url = None
            token = task.result.get("download_token") if task.result else None
            if token:
                download_url = self._download_url(token=token)

            return jsonify(
                {
                    "ok": True,
                    "task": {
                        "id": task.task_id,
                        "action": task.action,
                        "progress": task.progress,
                        "status": task.status,
                        "message": task.message,
                        "error": task.error,
                    },
                    "download_url": download_url,
                }
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Ошибка получения статуса: {exc}"}), 500

    def _download_url(self, *, path: str | None = None, token: str | None = None) -> str:
        if token:
            return url_for("FileManagerView.download", token=token)
        params: dict[str, Any] = {}
        if path:
            params["path"] = path
        return url_for("FileManagerView.download", **params)
