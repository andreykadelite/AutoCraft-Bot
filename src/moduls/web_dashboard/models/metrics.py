from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer

from ..db import db


class Metric(db.Model):
    __tablename__ = "panel_metrics"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    cpu = Column(Float, default=0.0)
    memory = Column(Float, default=0.0)
    disk = Column(Float, default=0.0)
    net_sent = Column(Float, default=0.0)
    net_recv = Column(Float, default=0.0)
    net_sent_rate = Column(Float, default=0.0)
    net_recv_rate = Column(Float, default=0.0)
    disk_read_bytes = Column(Float, default=0.0)
    disk_write_bytes = Column(Float, default=0.0)
    disk_read_rate = Column(Float, default=0.0)
    disk_write_rate = Column(Float, default=0.0)
    proc_count = Column(Integer, default=0)

    def __repr__(self) -> str:
        return f"Metric {self.created_at}"
