from __future__ import annotations

import datetime as dt

from ..db import db


class UserMessage(db.Model):
    __tablename__ = "user_messages"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False, index=True)
    recipient_id = db.Column(db.Integer, nullable=False, index=True)
    subject = db.Column(db.String(200), nullable=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_by_sender = db.Column(db.Boolean, default=False, nullable=False, index=True)
    deleted_by_recipient = db.Column(db.Boolean, default=False, nullable=False, index=True)

    def mark_read(self) -> None:
        if not self.read_at:
            self.read_at = dt.datetime.utcnow()
