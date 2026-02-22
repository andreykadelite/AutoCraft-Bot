from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from ..db import db


class SavedFilter(db.Model):
    __tablename__ = "panel_saved_filters"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    name = Column(String(128), nullable=False)
    log_name = Column(String(128), default="System")
    level = Column(String(32), default="")
    provider = Column(String(128), default="")
    event_id = Column(String(64), default="")
    payload = Column(Text, default="")

    def __repr__(self) -> str:
        return self.name
