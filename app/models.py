"""Database models: everything needed to reconstruct portfolio state and
show the full reasoning trail behind every trade."""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey
)
from sqlalchemy.orm import relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class BotState(Base):
    __tablename__ = "bot_state"

    id = Column(Integer, primary_key=True, default=1)
    mode = Column(String, default="paper")  # paper | live
    running = Column(Boolean, default=True)  # manual pause/resume
    paused_reason = Column(Text, nullable=True)  # set when risk manager kills it
    last_tick_at = Column(DateTime, nullable=True)
    paper_starting_cash = Column(Float, default=10000)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class DashboardUser(Base):
    __tablename__ = "dashboard_users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class RiskConfig(Base):
    __tablename__ = "risk_config"

    id = Column(Integer, primary_key=True, default=1)
    max_position_pct = Column(Float, default=0.05)
    max_daily_loss_pct = Column(Float, default=0.02)
    max_open_positions = Column(Integer, default=8)
    allow_options = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    mode = Column(String)
    cash = Column(Float)
    positions_value = Column(Float)
    total_value = Column(Float)
    daily_pnl_pct = Column(Float, nullable=True)


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    mode = Column(String, index=True)
    symbol = Column(String, index=True)
    quantity = Column(Float)
    avg_cost = Column(Float)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class AIDecision(Base):
    __tablename__ = "ai_decisions"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    mode = Column(String)
    symbol = Column(String, index=True)
    action = Column(String)  # buy | sell | hold
    quantity = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text)
    risk_adjusted = Column(Boolean, default=False)
    risk_note = Column(Text, nullable=True)
    executed = Column(Boolean, default=False)
    raw_response = Column(Text, nullable=True)

    trades = relationship("Trade", back_populates="decision")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    mode = Column(String)
    symbol = Column(String, index=True)
    side = Column(String)  # buy | sell
    quantity = Column(Float)
    price = Column(Float)
    realized_pnl = Column(Float, nullable=True)  # sells only; null for buys
    decision_id = Column(Integer, ForeignKey("ai_decisions.id"), nullable=True)

    decision = relationship("AIDecision", back_populates="trades")
