from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from ..db import db


class Server(db.Model):
    __tablename__ = "panel_servers"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    address = Column(String(256), nullable=False)
    tags = Column(String(256), default="")
    connection_method = Column(String(64), default="local")
    last_seen = Column(DateTime, default=datetime.now)
    health = Column(String(32), default="unknown")
    notes = Column(Text, default="")

    def __repr__(self) -> str:
        return f"{self.name} ({self.address})"
