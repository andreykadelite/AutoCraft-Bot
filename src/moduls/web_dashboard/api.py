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
from .remote_access_service import (
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_PROMPT,
    cancel_request,
    create_control_request,
    get_request_status,
    list_pending_requests,
    list_policies,
    respond_to_request,
    upsert_policy,
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
    if getattr(g, "autocraft_proxy_request", False):
        return None
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


@api_bp.route("/remote-control/request", methods=["POST"])
def remote_control_request():
    data = request.get_json(silent=True) or {}
    result = create_control_request(
        controller_node_id=data.get("controller_node_id"),
        controller_name=data.get("controller_name"),
        controller_ip=data.get("controller_ip"),
        controller_panel_url=data.get("controller_panel_url"),
        requester_user=data.get("requester_user"),
        requested_roles=data.get("requested_roles"),
    )
    _write_audit(
        "remote_control.request",
        bool(result.get("ok")),
        details=(
            f"controller={data.get('controller_node_id') or '-'} "
            f"status={result.get('status') or '-'}"
        ),
        actor=str(data.get("requester_user") or "api"),
    )
    status = str(result.get("status") or "").strip().lower()
    if status == "disabled":
        return jsonify(result), 403
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route("/remote-control/request/<request_id>/status")
def remote_control_request_status(request_id: str):
    controller_node_id = request.args.get("controller_node_id", "")
    result = get_request_status(request_id, controller_node_id=controller_node_id)
    if not result.get("ok"):
        if result.get("status") == "not_found":
            return jsonify(result), 404
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route("/remote-control/request/<request_id>/cancel", methods=["POST"])
def remote_control_request_cancel(request_id: str):
    data = request.get_json(silent=True) or {}
    actor = data.get("actor") or "api"
    result = cancel_request(request_id, actor=str(actor))
    _write_audit(
        "remote_control.request_cancel",
        bool(result.get("ok")),
        details=f"request_id={request_id} status={result.get('status')}",
        actor=str(actor or "api"),
    )
    if not result.get("ok"):
        if result.get("status") == "not_found":
            return jsonify(result), 404
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route("/remote-control/pending")
def remote_control_pending():
    limit = parse_int(request.args.get("limit"), 80)
    limit = max(1, min(limit, 500))
    data = list_pending_requests(limit=limit)
    return jsonify({"ok": True, "items": data, "count": len(data)})


@api_bp.route("/remote-control/request/<request_id>/respond", methods=["POST"])
def remote_control_request_respond(request_id: str):
    data = request.get_json(silent=True) or {}
    approve = parse_bool(data.get("approve"), False)
    remember = parse_bool(data.get("remember"), False)
    note = str(data.get("note") or "").strip()
    actor = str(data.get("actor") or "api")
    result = respond_to_request(
        request_id=request_id,
        approve=bool(approve),
        remember=bool(remember),
        actor=actor,
        note=note,
    )
    _write_audit(
        "remote_control.request_respond",
        bool(result.get("ok")),
        details=(
            f"request_id={request_id} approve={int(bool(approve))} "
            f"remember={int(bool(remember))} status={result.get('status')}"
        ),
        actor=actor,
    )
    status = str(result.get("status") or "").strip().lower()
    if not result.get("ok"):
        if status == "not_found":
            return jsonify(result), 404
        if status in {"expired", "cancelled"}:
            return jsonify(result), 409
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route("/remote-control/policies")
def remote_control_policies():
    limit = parse_int(request.args.get("limit"), 200)
    limit = max(1, min(limit, 1000))
    data = list_policies(limit=limit)
    return jsonify({"ok": True, "items": data, "count": len(data)})


@api_bp.route("/remote-control/policy/<controller_node_id>", methods=["PUT"])
def remote_control_policy_upsert(controller_node_id: str):
    data = request.get_json(silent=True) or {}
    decision = str(data.get("decision") or DECISION_PROMPT).strip().lower()
    if decision not in {DECISION_ALLOW, DECISION_DENY, DECISION_PROMPT}:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "invalid_decision",
                    "message": "decision must be allow, deny, or prompt",
                }
            ),
            400,
        )
    remember = parse_bool(data.get("remember"), decision != DECISION_PROMPT)
    if decision == DECISION_PROMPT:
        remember = False
    actor = str(data.get("actor") or "api")
    try:
        row = upsert_policy(
            controller_node_id=controller_node_id,
            controller_name=str(data.get("controller_name") or ""),
            controller_ip=str(data.get("controller_ip") or ""),
            decision=decision,
            remember=bool(remember),
            actor=actor,
        )
        result = {
            "ok": True,
            "controller_node_id": row.controller_node_id,
            "controller_name": row.controller_name,
            "controller_ip": row.controller_ip,
            "decision": row.decision,
            "remember": bool(row.remember),
            "updated_by": row.updated_by,
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
        _write_audit(
            "remote_control.policy_upsert",
            True,
            details=(
                f"controller={controller_node_id} decision={decision} "
                f"remember={int(bool(remember))}"
            ),
            actor=actor,
        )
        return jsonify(result)
    except Exception as exc:
        _write_audit(
            "remote_control.policy_upsert",
            False,
            details=f"controller={controller_node_id} error={exc}",
            actor=actor,
        )
        return jsonify({"ok": False, "error": "policy_upsert_failed", "message": str(exc)}), 500
