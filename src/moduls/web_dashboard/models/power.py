from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from ..db import db


class PowerAction(db.Model):
    __tablename__ = "panel_power_actions"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    scheduled_for = Column(DateTime, nullable=False)
    action = Column(String(32), nullable=False)
    status = Column(String(32), default="pending", nullable=False)
    requested_by = Column(String(128), default="system")
    request_source = Column(String(64), default="web")
    request_ip = Column(String(64), default="")
    delay_code = Column(String(32), default="")
    delay_label = Column(String(64), default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(String(128), default="")
    verification = Column(String(32), default="pending")
    verification_details = Column(Text, default="")

    def __repr__(self) -> str:
        return f"{self.action} [{self.status}] @{self.scheduled_for}"


class PowerRecurringSchedule(db.Model):
    __tablename__ = "panel_power_recurring_schedules"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    action = Column(String(32), nullable=False)
    weekdays_mask = Column(Integer, nullable=False, default=0)
    time_of_day = Column(String(5), nullable=False, default="00:00")
    enabled = Column(Boolean, nullable=False, default=True)
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    requested_by = Column(String(128), default="system")
    request_source = Column(String(64), default="web")
    request_ip = Column(String(64), default="")

    def __repr__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"{self.action} {self.time_of_day} [{state}]"
