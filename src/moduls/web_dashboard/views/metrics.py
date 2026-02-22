import datetime as dt
from typing import Any

from flask import current_app, request
from flask_appbuilder import BaseView, expose
from ..security import panel_has_access as has_access

from ..config import get_db_path, load_config, tail_panel_log
from ..db import db
from ..models.audit import AuditLog
from ..models.jobs import Job
from ..models.metrics import Metric
from ..tasks.scheduler import _ensure_metrics_schema

_RANGE_OPTIONS = {
    "15m": ("Последние 15 минут", dt.timedelta(minutes=15)),
    "1h": ("Последний час", dt.timedelta(hours=1)),
    "6h": ("Последние 6 часов", dt.timedelta(hours=6)),
    "24h": ("Последние 24 часа", dt.timedelta(hours=24)),
    "7d": ("Последние 7 дней", dt.timedelta(days=7)),
    "30d": ("Последние 30 дней", dt.timedelta(days=30)),
    "custom": ("Произвольный период", None),
}

_STEP_OPTIONS = {
    "auto": ("Авто", None),
    "1m": ("1 мин", 60),
    "5m": ("5 мин", 300),
    "15m": ("15 мин", 900),
    "1h": ("1 час", 3600),
}


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except Exception:
        return None


def _format_input_dt(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M")


def _aggregate_metrics(rows: list[Metric], step_seconds: int | None) -> list[dict[str, Any]]:
    if not step_seconds:
        return [
            {
                "ts": row.created_at,
                "cpu": row.cpu,
                "memory": row.memory,
                "disk": row.disk,
                "net_sent_rate": getattr(row, "net_sent_rate", None),
                "net_recv_rate": getattr(row, "net_recv_rate", None),
                "disk_read_rate": getattr(row, "disk_read_rate", None),
                "disk_write_rate": getattr(row, "disk_write_rate", None),
                "proc_count": getattr(row, "proc_count", None),
            }
            for row in rows
        ]

    buckets: dict[int, dict[str, Any]] = {}
    fields = [
        "cpu",
        "memory",
        "disk",
        "net_sent_rate",
        "net_recv_rate",
        "disk_read_rate",
        "disk_write_rate",
        "proc_count",
    ]
    for row in rows:
        if not row.created_at:
            continue
        bucket_key = int(row.created_at.timestamp() // step_seconds)
        bucket = buckets.setdefault(
            bucket_key,
            {"ts": dt.datetime.fromtimestamp(bucket_key * step_seconds), "sums": {}, "counts": {}},
        )
        for field in fields:
            value = getattr(row, field, None)
            if value is None:
                continue
            bucket["sums"][field] = bucket["sums"].get(field, 0.0) + float(value)
            bucket["counts"][field] = bucket["counts"].get(field, 0) + 1

    points: list[dict[str, Any]] = []
    for bucket in sorted(buckets.values(), key=lambda item: item["ts"]):
        item = {"ts": bucket["ts"]}
        for field in fields:
            count = bucket["counts"].get(field, 0)
            item[field] = bucket["sums"].get(field, 0.0) / count if count else None
        points.append(item)
    return points


def _calc_stats(points: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [p.get(field) for p in points if p.get(field) is not None]
    if not values:
        return {}
    return {
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
    }


class MetricsView(BaseView):
    route_base = "/metrics"
    base_permissions = ["can_list"]

    @expose("/")
    @has_access
    def list(self):
        now = dt.datetime.now()
        _ensure_metrics_schema()
        range_key = (request.args.get("range") or "6h").strip()
        step_key = (request.args.get("step") or "auto").strip()
        start_raw = request.args.get("from")
        end_raw = request.args.get("to")

        if range_key not in _RANGE_OPTIONS:
            range_key = "6h"
        if step_key not in _STEP_OPTIONS:
            step_key = "auto"

        start = None
        end = None
        if range_key == "custom":
            start = _parse_datetime(start_raw)
            end = _parse_datetime(end_raw)
            if not end:
                end = now
            if not start:
                start = end - dt.timedelta(hours=6)
        else:
            delta = _RANGE_OPTIONS[range_key][1] or dt.timedelta(hours=6)
            start = now - delta
            end = now

        if start > end:
            start, end = end, start

        rows = (
            db.session.query(Metric)
            .filter(Metric.created_at >= start, Metric.created_at <= end)
            .order_by(Metric.created_at.asc())
            .all()
        )

        step_seconds = _STEP_OPTIONS[step_key][1]
        if step_seconds is None:
            span_seconds = max((end - start).total_seconds(), 60)
            target_points = 240
            step_seconds = max(int(span_seconds / target_points), 60)

        points = _aggregate_metrics(rows, step_seconds)
        latest_metric = (
            db.session.query(Metric)
            .order_by(Metric.created_at.desc())
            .first()
        )

        stats = {
            "cpu": _calc_stats(points, "cpu"),
            "memory": _calc_stats(points, "memory"),
            "disk": _calc_stats(points, "disk"),
            "net_sent_rate": _calc_stats(points, "net_sent_rate"),
            "net_recv_rate": _calc_stats(points, "net_recv_rate"),
            "disk_read_rate": _calc_stats(points, "disk_read_rate"),
            "disk_write_rate": _calc_stats(points, "disk_write_rate"),
            "proc_count": _calc_stats(points, "proc_count"),
        }

        cfg = load_config(current_app.config.get("BASE_DIR"))
        last_metrics_at_raw = current_app.config.get("LAST_METRICS_AT")
        last_metrics_at = "-"
        if last_metrics_at_raw:
            parsed = _parse_datetime(last_metrics_at_raw)
            if parsed:
                last_metrics_at = parsed.strftime("%d.%m.%Y %H:%M")
        try:
            log_lines = int(request.args.get("log_lines", 200))
        except Exception:
            log_lines = 200
        log_lines = max(20, min(log_lines, 2000))
        log_query = (request.args.get("log_query") or "").strip().lower()
        log_tail = tail_panel_log(current_app.config.get("BASE_DIR"), lines=log_lines)
        if log_query:
            log_tail = "\n".join(
                line for line in log_tail.splitlines() if log_query in line.lower()
            )

        latest_jobs = (
            db.session.query(Job)
            .order_by(Job.created_at.desc())
            .limit(10)
            .all()
        )
        latest_audit = (
            db.session.query(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(10)
            .all()
        )

        return self.render_template(
            "metrics.html",
            metrics=points,
            latest=latest_metric,
            stats=stats,
            range_key=range_key,
            step_key=step_key,
            range_options=_RANGE_OPTIONS,
            step_options=_STEP_OPTIONS,
            start_input=_format_input_dt(start),
            end_input=_format_input_dt(end),
            log_tail=log_tail,
            log_lines=log_lines,
            log_query=log_query,
            retention_days=cfg.retention_days,
            db_path=str(get_db_path(current_app.config.get("BASE_DIR"))),
            last_metrics_at=last_metrics_at,
            jobs=latest_jobs,
            audit=latest_audit,
        )
