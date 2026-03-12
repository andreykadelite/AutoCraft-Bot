import datetime as dt

from flask import current_app, flash, jsonify, redirect, session, url_for
from flask_appbuilder import IndexView, expose
from flask_appbuilder.security.decorators import permission_name
from flask_login import current_user

from ..login_progress import get_login_progress_payload
from ..security import panel_has_access as has_access
from ..security import panel_has_access_api as has_access_api

from ..config import load_config
from ..db import db
from ..models.audit import AuditLog
from ..models.jobs import Job
from ..models.metrics import Metric
from ..ops.operations.system_info import (
    get_cached_system_snapshot,
    get_overview_snapshot,
    get_system_snapshot,
)
from ..tasks.scheduler import _ensure_metrics_schema


class WacIndexView(IndexView):
    route_base = "/"

    @expose("/")
    @has_access
    def index(self):
        _ensure_metrics_schema()
        base_dir = current_app.config["BASE_DIR"]
        cfg = load_config(base_dir)

        progress = None
        progress_token = session.get("panel_login_progress_token", "")
        if progress_token and current_user.is_authenticated:
            try:
                progress = get_login_progress_payload(progress_token, int(current_user.id))
            except Exception:
                progress = None
            if not progress:
                session.pop("panel_login_progress_token", None)
            elif progress.get("done") and not progress.get("error"):
                session.pop("panel_login_progress_token", None)
                progress = None
                progress_token = ""

        use_cached_snapshot = bool(progress_token and progress)
        try:
            snapshot = (
                get_cached_system_snapshot() if use_cached_snapshot else get_system_snapshot()
            )
        except Exception as exc:
            current_app.logger.exception("Dashboard snapshot collection failed")
            snapshot = get_cached_system_snapshot() or {}
            if not isinstance(snapshot, dict):
                snapshot = {}
            snapshot["error"] = snapshot.get("error") or f"Не удалось обновить данные системы: {exc}"
        cpu = snapshot.get("cpu_percent")
        memory = snapshot.get("memory", {})
        disks = snapshot.get("disks", [])
        disk_percent = snapshot.get("disk_percent")
        net_io = snapshot.get("net_io", {})
        interfaces = snapshot.get("interfaces", [])
        system = snapshot.get("system", {})
        winver = snapshot.get("winver", {})
        timezone = snapshot.get("timezone", {})
        security = snapshot.get("security", {})
        updates = snapshot.get("updates", {})
        services = snapshot.get("services", [])
        pagefiles = snapshot.get("pagefiles", [])
        physical_disks = snapshot.get("physical_disks", [])
        swap = snapshot.get("swap", {})
        disk_totals = snapshot.get("disk_totals", {})
        power = snapshot.get("power", {})
        hardware = snapshot.get("hardware", {})
        ps_error = snapshot.get("error")

        latest_metrics = (
            db.session.query(Metric)
            .order_by(Metric.created_at.desc())
            .limit(20)
            .all()
        )
        latest_jobs = (
            db.session.query(Job)
            .order_by(Job.created_at.desc())
            .limit(20)
            .all()
        )
        latest_audit = (
            db.session.query(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(20)
            .all()
        )

        return self.render_template(
            "dashboard.html",
            cpu=cpu,
            memory=memory,
            disks=disks,
            disk_percent=disk_percent,
            net_io=net_io,
            interfaces=interfaces,
            system=system,
            winver=winver,
            timezone=timezone,
            security=security,
            updates=updates,
            services=services,
            pagefiles=pagefiles,
            physical_disks=physical_disks,
            swap=swap,
            disk_totals=disk_totals,
            power=power,
            hardware=hardware,
            ps_error=ps_error,
            metrics=latest_metrics,
            jobs=latest_jobs,
            audit=latest_audit,
            now=dt.datetime.now(),
            overview_refresh_seconds=cfg.overview_refresh_seconds,
            login_progress=progress,
            login_progress_status_url=(
                url_for("PanelAuthDBView.login_progress_status", token=progress_token)
                if progress and progress_token
                else ""
            ),
        )

    @expose("/overview/retry")
    @has_access
    def overview_retry(self):
        try:
            snapshot = get_system_snapshot(force=True)
            # Keep lightweight overview cache in sync after manual retry.
            get_overview_snapshot(force=True)
        except Exception as exc:
            current_app.logger.exception("Manual overview retry failed")
            flash(f"Ошибка повторной загрузки данных: {exc}", "danger")
            return redirect(url_for("WacIndexView.index"))
        if snapshot.get("error"):
            flash(
                "Повторная попытка выполнена, но часть данных системы по-прежнему не получена.",
                "warning",
            )
        else:
            session.pop("panel_login_progress_token", None)
            flash("Данные системы успешно обновлены.", "success")
        return redirect(url_for("WacIndexView.index"))

    @expose("/overview/data")
    @has_access_api
    @permission_name("index")
    def overview_data(self):
        snapshot = get_overview_snapshot()
        memory = snapshot.get("memory", {}) if isinstance(snapshot, dict) else {}
        net_io = snapshot.get("net_io", {}) if isinstance(snapshot, dict) else {}
        return jsonify(
            {
                "updated_at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                "cpu_percent": snapshot.get("cpu_percent"),
                "disk_percent": snapshot.get("disk_percent"),
                "uptime_human": snapshot.get("uptime_human"),
                "memory": memory,
                "net_io": net_io,
                "disk_totals": snapshot.get("disk_totals", {}),
                "power": snapshot.get("power", {}),
                "hardware": snapshot.get("hardware", {}),
            }
        )
