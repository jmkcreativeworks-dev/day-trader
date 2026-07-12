"""Enforces hard limits independent of what Claude decides. Every proposed
decision passes through here before it reaches the broker. The risk
manager can shrink an order, block it outright, or trip the kill switch
for the rest of the trading day."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.brokers import BrokerAdapter
from app.models import RiskConfig, BotState, PortfolioSnapshot


@dataclass
class RiskVerdict:
    allowed: bool
    quantity: float  # possibly reduced from the requested amount
    note: Optional[str] = None


class RiskManager:
    def __init__(self, db: Session, broker: BrokerAdapter):
        self.db = db
        self.broker = broker

    def _config(self) -> RiskConfig:
        cfg = self.db.query(RiskConfig).first()
        if not cfg:
            cfg = RiskConfig()
            self.db.add(cfg)
            self.db.commit()
        return cfg

    def _state(self) -> BotState:
        state = self.db.query(BotState).first()
        if not state:
            state = BotState()
            self.db.add(state)
            self.db.commit()
        return state

    def daily_loss_pct(self) -> Optional[float]:
        """Compares current portfolio value to the first snapshot of the
        current UTC day."""
        today = datetime.now(timezone.utc).date()
        first_today = (
            self.db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.mode == self.broker.mode)
            .filter(PortfolioSnapshot.timestamp >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc))
            .order_by(PortfolioSnapshot.timestamp.asc())
            .first()
        )
        if not first_today:
            return None
        current = self.broker.get_portfolio_value()
        if first_today.total_value == 0:
            return None
        return (current - first_today.total_value) / first_today.total_value * 100

    def check_kill_switch(self) -> Optional[str]:
        """Returns a reason string and pauses the bot if the daily loss
        limit has been breached. Call once per tick before evaluating
        any new decisions."""
        cfg = self._config()
        loss_pct = self.daily_loss_pct()
        if loss_pct is not None and loss_pct <= -abs(cfg.max_daily_loss_pct) * 100:
            state = self._state()
            state.running = False
            state.paused_reason = (
                f"Auto-paused: daily loss {loss_pct:.2f}% breached limit "
                f"of -{cfg.max_daily_loss_pct * 100:.2f}%"
            )
            self.db.commit()
            return state.paused_reason
        return None

    def evaluate(self, symbol: str, action: str, requested_qty: float,
                 price: float, is_options: bool = False) -> RiskVerdict:
        cfg = self._config()

        if is_options and not cfg.allow_options:
            return RiskVerdict(False, 0, "options trading disabled in risk config")

        if action == "hold":
            return RiskVerdict(True, 0, None)

        if action == "buy":
            portfolio_value = self.broker.get_portfolio_value()
            max_dollar = portfolio_value * cfg.max_position_pct
            existing = next(
                (p for p in self.broker.get_positions() if p.symbol == symbol), None
            )
            existing_value = existing.market_value if existing else 0
            room = max_dollar - existing_value
            if room <= 0:
                return RiskVerdict(False, 0, f"{symbol} already at max position size")

            open_positions = len(self.broker.get_positions())
            if not existing and open_positions >= cfg.max_open_positions:
                return RiskVerdict(False, 0, "max open positions reached")

            max_qty_by_room = room / price if price > 0 else 0
            final_qty = min(requested_qty, max_qty_by_room)
            note = None
            if final_qty < requested_qty:
                note = f"trimmed from {requested_qty} to {final_qty:.4f} to respect position size limit"
            if final_qty <= 0:
                return RiskVerdict(False, 0, "no room left under position size limit")
            return RiskVerdict(True, final_qty, note)

        if action == "sell":
            existing = next(
                (p for p in self.broker.get_positions() if p.symbol == symbol), None
            )
            held = existing.quantity if existing else 0
            final_qty = min(requested_qty, held)
            if final_qty <= 0:
                return RiskVerdict(False, 0, f"no shares of {symbol} held to sell")
            note = None
            if final_qty < requested_qty:
                note = f"trimmed sell from {requested_qty} to {final_qty} (position size)"
            return RiskVerdict(True, final_qty, note)

        return RiskVerdict(False, 0, f"unknown action '{action}'")
