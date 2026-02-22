from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from ..db import db


class Job(db.Model):
    __tablename__ = "panel_jobs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    started_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(32), default="queued")
    operation = Column(String(128), nullable=False)
    params = Column(Text, default="")
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    user = Column(String(128), default="system")
    source = Column(String(64), default="system")

    def __repr__(self) -> str:
        return f"{self.operation} [{self.status}]"
