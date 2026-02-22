from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DB_FILE_NAME = "activated_users.db"
_DB_LOCK = threading.RLock()


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_text(value: Any, limit: int = 255) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _data_dir(base_dir: str) -> Path:
    root = Path(base_dir).resolve() if base_dir else Path.cwd()
    return root / "data"


def get_db_path(base_dir: str) -> Path:
    return _data_dir(base_dir) / _DB_FILE_NAME


def _connect(base_dir: str) -> sqlite3.Connection:
    db_path = get_db_path(base_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_storage(base_dir: str) -> Path:
    with _DB_LOCK:
        conn = _connect(base_dir)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activated_users (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    username TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    last_name TEXT NOT NULL DEFAULT '',
                    language_code TEXT NOT NULL DEFAULT '',
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    activated_at TEXT NOT NULL,
                    last_activated_at TEXT NOT NULL,
                    activation_count INTEGER NOT NULL DEFAULT 1,
                    last_source TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activated_users_last_activated
                ON activated_users(last_activated_at)
                """
            )
            conn.commit()
        finally:
            conn.close()
    return get_db_path(base_dir)


def save_activated_user(
    base_dir: str,
    user: Any,
    chat_id: Any = None,
    source: str = "auth",
) -> bool:
    user_id = _safe_int(getattr(user, "id", None) if user is not None else None)
    if user_id is None:
        return False

    username = _safe_text(getattr(user, "username", ""), limit=255)
    first_name = _safe_text(getattr(user, "first_name", ""), limit=255)
    last_name = _safe_text(getattr(user, "last_name", ""), limit=255)
    language_code = _safe_text(getattr(user, "language_code", ""), limit=32)
    is_bot = 1 if bool(getattr(user, "is_bot", False)) else 0
    chat_id_value = _safe_int(chat_id)
    source_value = _safe_text(source, limit=64)
    now_utc = _now_iso_utc()

    with _DB_LOCK:
        ensure_storage(base_dir)
        conn = _connect(base_dir)
        try:
            row = conn.execute(
                """
                SELECT activated_at, activation_count, chat_id
                FROM activated_users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

            if row is None:
                conn.execute(
                    """
                    INSERT INTO activated_users (
                        user_id,
                        chat_id,
                        username,
                        first_name,
                        last_name,
                        language_code,
                        is_bot,
                        activated_at,
                        last_activated_at,
                        activation_count,
                        last_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        chat_id_value,
                        username,
                        first_name,
                        last_name,
                        language_code,
                        is_bot,
                        now_utc,
                        now_utc,
                        1,
                        source_value,
                    ),
                )
            else:
                activation_count = _safe_int(row["activation_count"]) or 0
                first_activated_at = _safe_text(row["activated_at"], limit=64) or now_utc
                final_chat_id = chat_id_value if chat_id_value is not None else _safe_int(row["chat_id"])
                conn.execute(
                    """
                    UPDATE activated_users
                    SET
                        chat_id = ?,
                        username = ?,
                        first_name = ?,
                        last_name = ?,
                        language_code = ?,
                        is_bot = ?,
                        activated_at = ?,
                        last_activated_at = ?,
                        activation_count = ?,
                        last_source = ?
                    WHERE user_id = ?
                    """,
                    (
                        final_chat_id,
                        username,
                        first_name,
                        last_name,
                        language_code,
                        is_bot,
                        first_activated_at,
                        now_utc,
                        activation_count + 1,
                        source_value,
                        user_id,
                    ),
                )
            conn.commit()
            return True
        finally:
            conn.close()


def list_activated_user_ids(base_dir: str) -> List[int]:
    with _DB_LOCK:
        ensure_storage(base_dir)
        conn = _connect(base_dir)
        try:
            rows = conn.execute(
                "SELECT user_id FROM activated_users ORDER BY last_activated_at DESC"
            ).fetchall()
        finally:
            conn.close()
    result: List[int] = []
    for row in rows:
        user_id = _safe_int(row["user_id"])
        if user_id is not None:
            result.append(user_id)
    return result


def list_activated_users(base_dir: str) -> List[Dict[str, Any]]:
    with _DB_LOCK:
        ensure_storage(base_dir)
        conn = _connect(base_dir)
        try:
            rows = conn.execute(
                """
                SELECT
                    user_id,
                    chat_id,
                    username,
                    first_name,
                    last_name,
                    language_code,
                    is_bot,
                    activated_at,
                    last_activated_at,
                    activation_count,
                    last_source
                FROM activated_users
                ORDER BY last_activated_at DESC
                """
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def clear_activated_users(base_dir: str) -> int:
    """
    Удаляет всех сохраненных активированных пользователей.

    Возвращает количество удаленных записей.
    """
    with _DB_LOCK:
        ensure_storage(base_dir)
        conn = _connect(base_dir)
        try:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM activated_users").fetchone()
            total = _safe_int(row["cnt"] if row else 0) or 0
            conn.execute("DELETE FROM activated_users")
            conn.commit()
            return total
        finally:
            conn.close()
