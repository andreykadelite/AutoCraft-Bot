from __future__ import annotations

import datetime as dt
import time

from flask import Blueprint, current_app, jsonify, request, g

from .config import load_config, save_config, tail_panel_log
from .db import db
from .models.audit import AuditLog
from .models.jobs import Job
from .models.metrics import Metric
from .ops.operations.eventlog import query_event_logs
from .tasks.scheduler import _ensure_metrics_schema
from .ops.operations.processes import list_processes
from .ops.operations.services import list_services
from .ops.operations.networking import list_interfaces
from .ops.operations.autocraft import (
    collect_autocraft_status,
    list_autocraft_plugins,
    collect_autocraft_logs,
)
from .utils import parse_bool, parse_int

api_bp = Blueprint("panel_api", __name__)


def _get_cfg():
    base_dir = current_app.config.get("BASE_DIR")
    return load_config(base_dir)


def _write_audit(action: str, result: bool, details: str = "", actor: str = "api") -> None:
    try:
        ip = request.remote_addr or ""
    except Exception:
        ip = ""
    try:
        log = AuditLog(
            user=str(actor or "api"),
            action=str(action),
            target="",
            result="ok" if result else "fail",
            source="api",
            ip=str(ip or ""),
            details=str(details or ""),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _extract_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.headers.get("X-Panel-Token", "")


def _require_token() -> bool:
    cfg = _get_cfg()
    token = _extract_token()
    return bool(token and token == cfg.api_token)


@api_bp.before_request
def _auth_guard():
    path = request.path or ""
    if path.endswith("/health") or path.endswith("/ready") or path.endswith("/login"):
        return None
    if not _require_token():
        return jsonify({"error": "unauthorized"}), 401
    return None


@api_bp.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "version": "1.0",
            "db_ok": True,
            "last_metrics": current_app.config.get("LAST_METRICS_AT"),
            "uptime": int(time.time() - current_app.config.get("STARTED_AT", time.time())),
        }
    )


@api_bp.route("/ready")
def ready():
    return jsonify({"ready": True})


@api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username") or request.form.get("username")
    password = data.get("password") or request.form.get("password")

    appbuilder = getattr(current_app, "appbuilder", None)
    if not appbuilder:
        return jsonify({"error": "appbuilder not available"}), 500

    user = appbuilder.sm.auth_user_db(username, password)
    if not user:
        retry_after = getattr(g, "login_retry_after", None)
        if retry_after:
            return (
                jsonify(
                    {
                        "error": "rate_limited",
                        "message": "Слишком много неудачных попыток входа. Попробуйте позже.",
                        "retry_after": retry_after,
                    }
                ),
                429,
            )
        return jsonify({"error": "invalid credentials", "message": "Неверный логин или пароль."}), 401

    cfg = _get_cfg()
    return jsonify({"token": cfg.api_token, "user": username})


@api_bp.route("/overview")
def overview():
    _ensure_metrics_schema()
    latest_metric = db.session.query(Metric).order_by(Metric.created_at.desc()).first()
    jobs_count = db.session.query(Job).count()
    audit_count = db.session.query(AuditLog).count()

    return jsonify(
        {
            "metrics": {
                "cpu": latest_metric.cpu if latest_metric else 0,
                "memory": latest_metric.memory if latest_metric else 0,
                "disk": latest_metric.disk if latest_metric else 0,
                "net_sent": latest_metric.net_sent if latest_metric else 0,
                "net_recv": latest_metric.net_recv if latest_metric else 0,
                "net_sent_rate": getattr(latest_metric, "net_sent_rate", 0) if latest_metric else 0,
                "net_recv_rate": getattr(latest_metric, "net_recv_rate", 0) if latest_metric else 0,
                "disk_read_rate": getattr(latest_metric, "disk_read_rate", 0) if latest_metric else 0,
                "disk_write_rate": getattr(latest_metric, "disk_write_rate", 0) if latest_metric else 0,
                "proc_count": getattr(latest_metric, "proc_count", 0) if latest_metric else 0,
            },
            "jobs_count": jobs_count,
            "audit_count": audit_count,
        }
    )


@api_bp.route("/metrics")
def metrics():
    _ensure_metrics_schema()
    minutes = parse_int(request.args.get("minutes"), 60)
    minutes = max(1, minutes)
    since = dt.datetime.now() - dt.timedelta(minutes=minutes)
    rows = (
        db.session.query(Metric)
        .filter(Metric.created_at >= since)
        .order_by(Metric.created_at.asc())
        .all()
    )
    data = [
        {
            "ts": row.created_at.isoformat(),
            "cpu": row.cpu,
            "memory": row.memory,
            "disk": row.disk,
            "net_sent": row.net_sent,
            "net_recv": row.net_recv,
            "net_sent_rate": getattr(row, "net_sent_rate", None),
            "net_recv_rate": getattr(row, "net_recv_rate", None),
            "disk_read_rate": getattr(row, "disk_read_rate", None),
            "disk_write_rate": getattr(row, "disk_write_rate", None),
            "proc_count": getattr(row, "proc_count", None),
        }
        for row in rows
    ]
    return jsonify({"items": data})


@api_bp.route("/processes")
def processes():
    return jsonify(list_processes())


@api_bp.route("/services")
def services():
    return jsonify(list_services())


@api_bp.route("/network")
def network():
    return jsonify(list_interfaces())


@api_bp.route("/logs/tail")
def logs_tail():
    base_dir = current_app.config.get("BASE_DIR")
    lines = parse_int(request.args.get("lines"), 100)
    lines = max(1, lines)
    return jsonify({"data": tail_panel_log(base_dir, lines=lines)})


@api_bp.route("/windows/events")
def windows_events():
    log_name = request.args.get("log", "System")
    level = request.args.get("level", "")
    provider = request.args.get("provider", "")
    event_id = request.args.get("event_id", "")
    limit = parse_int(request.args.get("limit"), 20)
    limit = max(1, limit)
    data = query_event_logs(log_name, level, provider, event_id, limit)
    return jsonify({"data": data})


@api_bp.route("/alerts")
def alerts():
    return jsonify({"items": []})


@api_bp.route("/audit")
def audit():
    rows = (
        db.session.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
        .all()
    )
    data = [
        {
            "ts": row.created_at.isoformat(),
            "user": row.user,
            "action": row.action,
            "target": row.target,
            "result": row.result,
            "source": row.source,
            "ip": row.ip,
        }
        for row in rows
    ]
    return jsonify({"items": data})


@api_bp.route("/settings", methods=["GET", "PUT"])
def settings():
    cfg = _get_cfg()
    base_dir = current_app.config.get("BASE_DIR")
    if request.method == "GET":
        return jsonify(
            {
                "host": cfg.host,
                "port": cfg.port,
                "debug": cfg.debug,
                "retention_days": cfg.retention_days,
                "overview_refresh_seconds": cfg.overview_refresh_seconds,
            }
        )

    data = request.get_json(silent=True) or {}
    cfg.host = data.get("host", cfg.host)
    cfg.port = parse_int(data.get("port"), cfg.port)
    cfg.debug = parse_bool(str(data.get("debug", cfg.debug)))
    cfg.retention_days = parse_int(data.get("retention_days"), cfg.retention_days)
    cfg.overview_refresh_seconds = parse_int(
        data.get("overview_refresh_seconds"),
        cfg.overview_refresh_seconds,
    )
    if cfg.overview_refresh_seconds <= 0:
        cfg.overview_refresh_seconds = 0
    else:
        cfg.overview_refresh_seconds = max(2, min(cfg.overview_refresh_seconds, 120))
    save_config(base_dir, cfg)
    _write_audit(
        "panel_settings_update_api",
        True,
        details=(
            f"host={cfg.host} port={cfg.port} debug={int(bool(cfg.debug))} "
            f"retention={cfg.retention_days} refresh={cfg.overview_refresh_seconds}"
        ),
    )
    return jsonify({"ok": True})


@api_bp.route("/actions/diagnostic-bundle")
def diagnostic_bundle():
    base_dir = current_app.config.get("BASE_DIR")
    tail = tail_panel_log(base_dir, lines=200)
    return jsonify({"log_tail": tail})


@api_bp.route("/autocraft/status")
def autocraft_status():
    base_dir = current_app.config.get("BASE_DIR")
    data = collect_autocraft_status(base_dir)
    return jsonify(data)


@api_bp.route("/autocraft/plugins")
def autocraft_plugins():
    base_dir = current_app.config.get("BASE_DIR")
    data = list_autocraft_plugins(base_dir)
    return jsonify(data)


@api_bp.route("/autocraft/logs")
def autocraft_logs():
    base_dir = current_app.config.get("BASE_DIR")
    lines = parse_int(request.args.get("lines"), 140)
    lines = max(20, min(lines, 800))
    data = collect_autocraft_logs(base_dir, lines=lines)
    return jsonify(data)
