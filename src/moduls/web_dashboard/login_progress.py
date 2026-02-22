from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_STEP_DEFS = [
    ("auth", "Проверка учетных данных"),
    ("session", "Создание защищенной сессии"),
    ("profile", "Загрузка профиля и ролей"),
    ("permissions", "Проверка прав доступа"),
    ("metrics", "Подготовка метрик и журналов"),
    ("system", "Сбор системной информации"),
]


@dataclass
class LoginProgressStep:
    key: str
    label: str
    status: str = "pending"
    detail: str = ""


@dataclass
class LoginProgress:
    token: str
    user_id: int
    username: str
    next_url: str
    steps: List[LoginProgressStep]
    status: str = "running"
    message: str = ""
    percent: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started: bool = False
    done: bool = False
    error: Optional[str] = None


class LoginProgressStore:
    def __init__(self, ttl_seconds: int = 900, done_ttl_seconds: int = 300) -> None:
        self._items: Dict[str, LoginProgress] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self.done_ttl_seconds = done_ttl_seconds

    def create(self, user_id: int, username: str, next_url: str) -> str:
        token = secrets.token_urlsafe(16)
        steps = [LoginProgressStep(key=key, label=label) for key, label in _STEP_DEFS]
        if steps:
            steps[0].status = "done"
            steps[0].detail = "Успешно"
        if len(steps) > 1:
            steps[1].status = "done"
            steps[1].detail = "Сессия создана"
        progress = LoginProgress(
            token=token,
            user_id=user_id,
            username=username,
            next_url=next_url,
            steps=steps,
            message="Подготовка входа",
        )
        progress.percent = self._calc_percent(progress)
        with self._lock:
            self._cleanup_locked()
            self._items[token] = progress
        return token

    def get(self, token: str) -> Optional[LoginProgress]:
        with self._lock:
            self._cleanup_locked()
            return self._items.get(token)

    def get_payload(self, token: str, user_id: int) -> Optional[Dict[str, object]]:
        with self._lock:
            self._cleanup_locked()
            progress = self._items.get(token)
            if not progress or progress.user_id != user_id:
                return None
            return self.to_payload(progress)

    def mark_started(self, token: str) -> bool:
        with self._lock:
            item = self._items.get(token)
            if not item or item.started:
                return False
            item.started = True
            item.updated_at = time.time()
            return True

    def set_step(
        self,
        token: str,
        key: str,
        status: str,
        detail: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        with self._lock:
            item = self._items.get(token)
            if not item or item.done:
                return
            for step in item.steps:
                if step.key == key:
                    step.status = status
                    if detail is not None:
                        step.detail = detail
                    if message:
                        item.message = message
                    item.updated_at = time.time()
                    item.percent = self._calc_percent(item)
                    break

    def finish(self, token: str, message: str = "Подготовка завершена") -> None:
        with self._lock:
            item = self._items.get(token)
            if not item:
                return
            item.done = True
            item.status = "done"
            item.message = message
            item.percent = 100
            item.updated_at = time.time()
            for step in item.steps:
                if step.status == "in_progress":
                    step.status = "done"

    def fail(self, token: str, error: str) -> None:
        with self._lock:
            item = self._items.get(token)
            if not item:
                return
            item.done = True
            item.status = "error"
            item.error = error
            item.message = error or "Ошибка подготовки входа"
            item.updated_at = time.time()
            for step in item.steps:
                if step.status == "in_progress":
                    step.status = "error"
                    if not step.detail:
                        step.detail = "Ошибка"

    def to_payload(self, progress: LoginProgress) -> Dict[str, object]:
        return {
            "token": progress.token,
            "user_id": progress.user_id,
            "status": progress.status,
            "message": progress.message,
            "percent": progress.percent,
            "steps": [
                {
                    "id": step.key,
                    "label": step.label,
                    "status": step.status,
                    "detail": step.detail,
                }
                for step in progress.steps
            ],
            "done": progress.done,
            "error": progress.error,
            "next_url": progress.next_url,
        }

    def _calc_percent(self, progress: LoginProgress) -> int:
        total = max(1, len(progress.steps))
        done = sum(1 for step in progress.steps if step.status == "done")
        in_progress = sum(1 for step in progress.steps if step.status == "in_progress")
        value = int(((done + (in_progress * 0.5)) / total) * 100)
        return max(0, min(100, value))

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired = []
        for token, item in self._items.items():
            if item.done and now - item.updated_at > self.done_ttl_seconds:
                expired.append(token)
            elif not item.done and now - item.created_at > self.ttl_seconds:
                expired.append(token)
        for token in expired:
            self._items.pop(token, None)


_LOGIN_PROGRESS = LoginProgressStore()


def create_login_progress(app, user, next_url: str) -> str:
    token = _LOGIN_PROGRESS.create(user.id, user.username or "", next_url)
    start_login_progress(app, token)
    return token


def start_login_progress(app, token: str) -> None:
    if not _LOGIN_PROGRESS.mark_started(token):
        return
    thread = threading.Thread(target=_run_login_preparation, args=(app, token), daemon=True)
    thread.start()


def get_login_progress_payload(token: str, user_id: int) -> Optional[Dict[str, object]]:
    return _LOGIN_PROGRESS.get_payload(token, user_id)


def _format_dt(value) -> str:
    if not value:
        return "нет данных"
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def _run_login_preparation(app, token: str) -> None:
    from .db import db
    from .models.audit import AuditLog
    from .models.metrics import Metric
    from .ops.operations.system_info import get_system_snapshot
    from .tasks.scheduler import _ensure_metrics_schema

    try:
        with app.app_context():
            appbuilder = getattr(app, "appbuilder", None)
            progress = _LOGIN_PROGRESS.get(token)
            if not appbuilder or not progress:
                return
            user = appbuilder.sm.get_user_by_id(progress.user_id)
            if not user:
                raise RuntimeError("Пользователь не найден")

            _LOGIN_PROGRESS.set_step(
                token,
                "profile",
                "in_progress",
                message="Загрузка профиля и ролей",
            )
            roles = appbuilder.sm.get_user_roles(user)
            groups = getattr(user, "groups", []) or []
            role_names = {getattr(role, "name", "") for role in roles if role}
            _LOGIN_PROGRESS.set_step(
                token,
                "profile",
                "done",
                detail=f"Ролей: {len([name for name in role_names if name])}, групп: {len(groups)}",
            )

            _LOGIN_PROGRESS.set_step(
                token,
                "permissions",
                "in_progress",
                message="Проверка прав доступа",
            )
            permissions = appbuilder.sm.get_user_permissions(user)
            _LOGIN_PROGRESS.set_step(
                token,
                "permissions",
                "done",
                detail=f"Права: {len(permissions)}",
            )

            _LOGIN_PROGRESS.set_step(
                token,
                "metrics",
                "in_progress",
                message="Подготовка метрик и журналов",
            )
            _ensure_metrics_schema()
            latest_metric = (
                db.session.query(Metric).order_by(Metric.created_at.desc()).first()
            )
            latest_audit = (
                db.session.query(AuditLog).order_by(AuditLog.created_at.desc()).first()
            )
            _LOGIN_PROGRESS.set_step(
                token,
                "metrics",
                "done",
                detail=f"Метрики: {_format_dt(getattr(latest_metric, 'created_at', None))}, аудит: {_format_dt(getattr(latest_audit, 'created_at', None))}",
            )

            _LOGIN_PROGRESS.set_step(
                token,
                "system",
                "in_progress",
                message="Сбор системной информации",
            )
            snapshot = get_system_snapshot()
            snapshot_error = ""
            if isinstance(snapshot, dict):
                snapshot_error = str(snapshot.get("error") or "").strip()
            if snapshot_error:
                _LOGIN_PROGRESS.set_step(
                    token,
                    "system",
                    "error",
                    detail=snapshot_error,
                )
                _LOGIN_PROGRESS.fail(token, snapshot_error)
                return
            system = snapshot.get("system", {}) if isinstance(snapshot, dict) else {}
            os_name = (
                system.get("os")
                or system.get("caption")
                or system.get("name")
                or "неизвестно"
            )
            host = system.get("computer_name") or system.get("hostname") or ""
            detail = f"ОС: {os_name}"
            if host:
                detail = f"{detail}, узел: {host}"
            _LOGIN_PROGRESS.set_step(
                token,
                "system",
                "done",
                detail=detail,
            )

            _LOGIN_PROGRESS.finish(token)
    except Exception as exc:
        _LOGIN_PROGRESS.fail(token, str(exc) or "Ошибка подготовки входа")
    finally:
        try:
            db.session.remove()
        except Exception:
            pass
