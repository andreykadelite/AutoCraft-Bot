from __future__ import annotations

import datetime as dt

from sqlalchemy import Index, UniqueConstraint

from ..db import db


class AdminBroadcast(db.Model):
    __tablename__ = "admin_broadcasts"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False, index=True)
    created_by_id = db.Column(db.Integer, nullable=False, index=True)
    created_by_username = db.Column(db.String(150), nullable=False, index=True)
    subject = db.Column(db.String(200), nullable=True)
    body = db.Column(db.Text, nullable=False)
    recipients_count = db.Column(db.Integer, nullable=False, default=0)
    show_on_login = db.Column(db.Boolean, nullable=False, default=False, index=True)
    notify_authenticated = db.Column(db.Boolean, nullable=False, default=False, index=True)


class AdminBroadcastDelivery(db.Model):
    __tablename__ = "admin_broadcast_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    broadcast_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    delivered_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_admin_broadcast_delivery_user"),
        Index("ix_admin_broadcast_delivery_user_delivered", "user_id", "delivered_at"),
    )


class AdminLoginBanner(db.Model):
    __tablename__ = "admin_login_banners"

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False, index=True)
    subject = db.Column(db.String(200), nullable=True)
    body = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False, index=True)
    updated_by_id = db.Column(db.Integer, nullable=False, index=True)
    updated_by_username = db.Column(db.String(150), nullable=False, index=True)
