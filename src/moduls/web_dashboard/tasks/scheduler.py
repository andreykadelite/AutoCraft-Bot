import datetime as dt

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import inspect, text

from ..db import db
from ..models.metrics import Metric
from ..ops.operations.metrics import collect_metrics
from .power_actions import process_due_power_actions
from .power_recurring import process_due_power_recurring_schedules

_METRICS_SCHEMA_READY = False


def _to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value):
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _calc_rate(current, previous, delta_seconds):
    if current is None or previous is None:
        return None
    if delta_seconds <= 0:
        return None
    try:
        if current < previous:
            return None
    except Exception:
        return None
    try:
        return (current - previous) / delta_seconds
    except Exception:
        return None


def _ensure_metrics_schema() -> None:
    global _METRICS_SCHEMA_READY
    if _METRICS_SCHEMA_READY:
        return
    try:
        engine = db.engine
        inspector = inspect(engine)
        if not inspector.has_table(Metric.__tablename__):
            return
        columns = {col["name"] for col in inspector.get_columns(Metric.__tablename__)}
        required = {
            "net_sent_rate": "FLOAT",
            "net_recv_rate": "FLOAT",
            "disk_read_bytes": "FLOAT",
            "disk_write_bytes": "FLOAT",
            "disk_read_rate": "FLOAT",
            "disk_write_rate": "FLOAT",
            "proc_count": "INTEGER",
        }
        missing = {name: col_type for name, col_type in required.items() if name not in columns}
        if not missing:
            _METRICS_SCHEMA_READY = True
            return
        with engine.begin() as conn:
            for name, col_type in missing.items():
                conn.execute(text(f"ALTER TABLE {Metric.__tablename__} ADD COLUMN {name} {col_type}"))
        _METRICS_SCHEMA_READY = True
    except Exception:
        _METRICS_SCHEMA_READY = True


def start_scheduler(app, cfg):
    scheduler = BackgroundScheduler()

    def _collect():
        with app.app_context():
            try:
                _ensure_metrics_schema()
                res = collect_metrics()
                data = res.get("data", {})
                now = dt.datetime.now()
                last_metric = (
                    db.session.query(Metric)
                    .order_by(Metric.created_at.desc())
                    .first()
                )
                delta_seconds = None
                if last_metric and last_metric.created_at:
                    delta_seconds = (now - last_metric.created_at).total_seconds()
                metric = Metric(
                    cpu=_to_float(data.get("cpu")) or 0.0,
                    memory=_to_float(data.get("memory")) or 0.0,
                    disk=_to_float(data.get("disk")) or 0.0,
                    net_sent=_to_float(data.get("net_sent")) or 0.0,
                    net_recv=_to_float(data.get("net_recv")) or 0.0,
                    net_sent_rate=_calc_rate(
                        _to_float(data.get("net_sent")),
                        _to_float(getattr(last_metric, "net_sent", None)),
                        delta_seconds or 0.0,
                    ),
                    net_recv_rate=_calc_rate(
                        _to_float(data.get("net_recv")),
                        _to_float(getattr(last_metric, "net_recv", None)),
                        delta_seconds or 0.0,
                    ),
                    disk_read_bytes=_to_float(data.get("disk_read_bytes")),
                    disk_write_bytes=_to_float(data.get("disk_write_bytes")),
                    disk_read_rate=_calc_rate(
                        _to_float(data.get("disk_read_bytes")),
                        _to_float(getattr(last_metric, "disk_read_bytes", None)),
                        delta_seconds or 0.0,
                    ),
                    disk_write_rate=_calc_rate(
                        _to_float(data.get("disk_write_bytes")),
                        _to_float(getattr(last_metric, "disk_write_bytes", None)),
                        delta_seconds or 0.0,
                    ),
                    proc_count=_to_int(data.get("proc_count")) or 0,
                )
                db.session.add(metric)

                # retention
                if cfg.retention_days:
                    cutoff = now - dt.timedelta(days=int(cfg.retention_days))
                    db.session.query(Metric).filter(Metric.created_at < cutoff).delete()

                db.session.commit()
                app.config["LAST_METRICS_AT"] = now.isoformat()
            except Exception:
                db.session.rollback()
                try:
                    app.logger.exception("Metrics collection failed")
                except Exception:
                    pass

    def _process_power():
        with app.app_context():
            try:
                process_due_power_actions()
                process_due_power_recurring_schedules()
            except Exception:
                db.session.rollback()
                try:
                    app.logger.exception("Power action scheduler failed")
                except Exception:
                    pass

    scheduler.add_job(_collect, "interval", minutes=1, id="metrics_collect", replace_existing=True)
    scheduler.add_job(
        _process_power,
        "interval",
        seconds=5,
        id="power_actions_dispatch",
        replace_existing=True,
    )
    scheduler.start()
    try:
        app.logger.info("scheduler_started metrics_interval=1m power_interval=5s")
    except Exception:
        pass
    _collect()
    _process_power()
    return scheduler, app.config.get("LAST_METRICS_AT")
