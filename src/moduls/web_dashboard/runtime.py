import time
from typing import Optional

from .config import PanelConfig, load_config


class PanelRuntime:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.config: PanelConfig = load_config(base_dir)
        self.app = None
        self.appbuilder = None
        self.scheduler = None
        self.started_at = time.time()
        self.last_metrics_at: Optional[float] = None

    def ensure_app(self):
        if self.app is not None:
            return self.app

        from .app_factory import create_app

        app, appbuilder, context = create_app(self.base_dir, start_scheduler=True)
        self.app = app
        self.appbuilder = appbuilder
        self.scheduler = context.get("scheduler")
        self.last_metrics_at = context.get("last_metrics_at")
        self.config = context.get("config", self.config)
        return self.app

    def audit(
        self,
        actor: str,
        action: str,
        result: bool,
        source: str = "system",
        target: str = "",
        details: str = "",
        ip: str = "",
    ) -> None:
        app = self.ensure_app()
        from .models.audit import AuditLog
        from .db import db

        with app.app_context():
            log = AuditLog(
                user=actor,
                action=action,
                target=target,
                result="ok" if result else "fail",
                source=source,
                ip=ip,
                details=details,
            )
            db.session.add(log)
            db.session.commit()

    def record_job(
        self,
        actor: str,
        operation: str,
        status: str,
        stdout: str = "",
        stderr: str = "",
        params: str = "",
        source: str = "system",
    ) -> None:
        app = self.ensure_app()
        from .models.jobs import Job
        from .db import db

        with app.app_context():
            job = Job(
                user=actor,
                operation=operation,
                status=status,
                stdout=stdout,
                stderr=stderr,
                params=params,
                source=source,
            )
            db.session.add(job)
            db.session.commit()
