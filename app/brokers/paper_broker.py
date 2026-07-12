"""Simulated broker: real market prices, fake money. Ledger lives in
Postgres (positions + trades tables, mode='paper') so it survives restarts."""
from sqlalchemy.orm import Session

from app.brokers import BrokerAdapter, PositionInfo, OrderResult
from app.config import settings
from app.models import BotState, Position, Trade


class PaperBroker(BrokerAdapter):
    mode = "paper"

    def __init__(self, db: Session, price_lookup):
        """price_lookup: callable(symbol) -> latest price, from the market
        data adapter, so the paper broker never invents prices."""
        self.db = db
        self.price_lookup = price_lookup

    def _cash_row(self):
        # Cash is tracked as the implicit remainder: starting cash minus
        # net cost of all paper trades. Recomputed from the trade log so
        # there's a single source of truth. Starting cash itself lives in
        # bot_state so it can be changed/reset from the dashboard without
        # a restart.
        trades = self.db.query(Trade).filter(Trade.mode == "paper").all()
        state = self.db.query(BotState).first()
        cash = state.paper_starting_cash if state else settings.PAPER_STARTING_CASH
        for t in trades:
            if t.side == "buy":
                cash -= t.quantity * t.price
            else:
                cash += t.quantity * t.price
        return cash

    def get_cash(self) -> float:
        return self._cash_row()

    def get_positions(self) -> list[PositionInfo]:
        rows = self.db.query(Position).filter(
            Position.mode == "paper", Position.quantity > 0
        ).all()
        result = []
        for r in rows:
            price = self.price_lookup(r.symbol) or r.avg_cost
            result.append(PositionInfo(
                symbol=r.symbol, quantity=r.quantity,
                avg_cost=r.avg_cost, current_price=price,
            ))
        return result

    def get_portfolio_value(self) -> float:
        cash = self.get_cash()
        positions_value = sum(p.market_value for p in self.get_positions())
        return cash + positions_value

    def place_order(self, symbol: str, side: str, quantity: float, price: float) -> OrderResult:
        if quantity <= 0:
            return OrderResult(False, symbol, side, quantity, price, "quantity must be positive")

        if side == "buy":
            cost = quantity * price
            if cost > self.get_cash():
                return OrderResult(False, symbol, side, quantity, price, "insufficient paper cash")
        elif side == "sell":
            pos = self.db.query(Position).filter(
                Position.mode == "paper", Position.symbol == symbol
            ).first()
            held = pos.quantity if pos else 0
            if quantity > held:
                return OrderResult(False, symbol, side, quantity, price, "insufficient shares held")
        else:
            return OrderResult(False, symbol, side, quantity, price, f"unknown side '{side}'")

        # Record the trade
        trade = Trade(mode="paper", symbol=symbol, side=side, quantity=quantity, price=price)
        self.db.add(trade)

        # Update the position
        pos = self.db.query(Position).filter(
            Position.mode == "paper", Position.symbol == symbol
        ).first()
        if not pos:
            pos = Position(mode="paper", symbol=symbol, quantity=0, avg_cost=0)
            self.db.add(pos)
            self.db.flush()

        if side == "buy":
            new_qty = pos.quantity + quantity
            pos.avg_cost = ((pos.avg_cost * pos.quantity) + (price * quantity)) / new_qty
            pos.quantity = new_qty
        else:
            pos.quantity -= quantity

        self.db.commit()
        return OrderResult(True, symbol, side, quantity, price, "filled (paper)")
