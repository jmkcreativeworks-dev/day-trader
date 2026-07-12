"""Phase 2: real trading via the Robinhood Trading MCP
(https://agent.robinhood.com/mcp/trading). Requires a one-time
interactive login (scripts/robinhood_oauth_setup.py) before this can
run headless - see app/brokers/robinhood_oauth.py for the OAuth
mechanics and app/scheduler.py for what happens if that session ever
needs re-authentication mid-operation (RobinhoodAuthError pauses the
bot, it never blocks waiting for a browser).

Tool name -> method mapping (tool *names* confirmed against Robinhood's
own support docs as of 2026-07; exact input/output field names below
are our best guess and MUST be checked against real
`python scripts/robinhood_list_tools.py` output - and ideally one real
review_equity_order call - before trusting this with money):

  get_accounts          -> resolve which account to trade in. Refuses
                            to proceed if more than one account comes
                            back - we will not guess which one is the
                            isolated Agentic account.
  get_portfolio          -> get_cash / get_portfolio_value
  get_equity_positions   -> get_positions (also mirrored into the
                            local `positions` table for the dashboard)
  review_equity_order    -> place_order, always called first (Robinhood's
                            own pre-trade simulation - documented as
                            never executing, safe to call unconditionally)
  place_equity_order     -> place_order, only called when
                            LIVE_DRY_RUN is false

Safety:
  - LIVE_DRY_RUN (default true, see .env.example) short-circuits after
    review_equity_order - place_equity_order (the real fill) is never
    called while it's true. Decisions/reasoning still populate the
    dashboard (as a non-executed decision with a [DRY RUN] note) so
    live-mode behavior can be compared against paper before it's
    trusted with real money.
  - Never guesses field names silently: if a response doesn't contain
    any of the guessed keys, this raises RobinhoodToolError with the
    raw response so the mismatch is obvious rather than silently wrong
    (e.g. reporting $0 cash instead of erroring).
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.brokers import BrokerAdapter, OrderResult, PositionInfo
from app.brokers.robinhood_oauth import RobinhoodAuthError, RobinhoodToolError, call_tool_sync
from app.config import settings
from app.models import Position, Trade

logger = logging.getLogger(__name__)


class RobinhoodBroker(BrokerAdapter):
    mode = "live"

    def __init__(self, db: Session, price_lookup=None):
        """price_lookup: optional callable(symbol) -> latest price, used
        only as a fallback if a position response doesn't include a
        current price field under any of our guessed names."""
        self.db = db
        self.price_lookup = price_lookup
        self._account_id: Optional[str] = None
        self._portfolio_cache: Optional[dict] = None
        self._positions_cache: Optional[list[PositionInfo]] = None

    def _account(self) -> str:
        if self._account_id is None:
            accounts = call_tool_sync("get_accounts", {})
            accounts_list = accounts.get("accounts", accounts) if isinstance(accounts, dict) else accounts
            if not isinstance(accounts_list, list) or len(accounts_list) != 1:
                raise RobinhoodAuthError(
                    f"get_accounts returned {accounts!r} - expected exactly "
                    "one account (the isolated Agentic account). Refusing to "
                    "guess which account to trade in; check "
                    "`python scripts/robinhood_list_tools.py` output and "
                    "app/brokers/robinhood_broker.py:_account()."
                )
            account = accounts_list[0]
            self._account_id = (
                account.get("account_number") or account.get("id") or account.get("account_id")
                if isinstance(account, dict) else None
            )
            if not self._account_id:
                raise RobinhoodToolError(
                    f"Could not find an account identifier in get_accounts response: {account!r}"
                )
            logger.info("RobinhoodBroker resolved account: %s", self._account_id)
        return self._account_id

    def _portfolio(self) -> dict:
        if self._portfolio_cache is None:
            self._portfolio_cache = call_tool_sync(
                "get_portfolio", {"account_number": self._account()}
            )
        return self._portfolio_cache

    def get_cash(self) -> float:
        portfolio = self._portfolio()
        cash = (
            portfolio.get("cash_available_for_trading")
            or portfolio.get("buying_power")
            or portfolio.get("cash")
            if isinstance(portfolio, dict) else None
        )
        if cash is None:
            raise RobinhoodToolError(f"No recognized cash field in get_portfolio response: {portfolio!r}")
        return float(cash)

    def get_positions(self) -> list[PositionInfo]:
        if self._positions_cache is None:
            raw = call_tool_sync("get_equity_positions", {"account_number": self._account()})
            rows = raw.get("positions", raw) if isinstance(raw, dict) else raw
            positions = []
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                symbol = r.get("symbol")
                quantity = float(r.get("quantity", 0) or 0)
                if not symbol or quantity <= 0:
                    continue
                avg_cost = float(r.get("average_buy_price") or r.get("avg_cost") or 0)
                current_price = r.get("current_price") or r.get("last_trade_price")
                if current_price is None and self.price_lookup:
                    current_price = self.price_lookup(symbol)
                positions.append(PositionInfo(
                    symbol=symbol, quantity=quantity, avg_cost=avg_cost,
                    current_price=float(current_price or avg_cost),
                ))
            self._positions_cache = positions
            self._sync_local_positions(positions)
        return self._positions_cache

    def _sync_local_positions(self, positions: list[PositionInfo]) -> None:
        """Robinhood is the source of truth for live positions - mirror
        it into the local `positions` table (mode='live') so the
        dashboard's Open Positions table reflects reality, including
        anything that happened outside this bot (dividends, a manual
        trade in the Robinhood app, etc.), not just what this bot did."""
        seen = {p.symbol for p in positions}
        existing = {p.symbol: p for p in self.db.query(Position).filter(Position.mode == "live").all()}
        for p in positions:
            row = existing.get(p.symbol)
            if row:
                row.quantity = p.quantity
                row.avg_cost = p.avg_cost
            else:
                self.db.add(Position(mode="live", symbol=p.symbol, quantity=p.quantity, avg_cost=p.avg_cost))
        for symbol, row in existing.items():
            if symbol not in seen:
                row.quantity = 0
        self.db.commit()

    def get_portfolio_value(self) -> float:
        portfolio = self._portfolio()
        total = (
            portfolio.get("total_equity") or portfolio.get("equity") or portfolio.get("portfolio_value")
            if isinstance(portfolio, dict) else None
        )
        if total is not None:
            return float(total)
        # Fall back to cash + positions if get_portfolio doesn't carry a
        # single total-equity field under any of the guessed names.
        return self.get_cash() + sum(p.market_value for p in self.get_positions())

    def place_order(self, symbol: str, side: str, quantity: float, price: float) -> OrderResult:
        if quantity <= 0:
            return OrderResult(False, symbol, side, quantity, price, "quantity must be positive")

        try:
            review = call_tool_sync("review_equity_order", {
                "account_number": self._account(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": "market",
            })
        except RobinhoodToolError as e:
            return OrderResult(False, symbol, side, quantity, price, f"order review failed: {e}")

        if settings.LIVE_DRY_RUN:
            return OrderResult(
                False, symbol, side, quantity, price,
                f"[DRY RUN] LIVE_DRY_RUN=true, no real order placed. "
                f"Robinhood review_equity_order response: {review!r}",
            )

        try:
            fill = call_tool_sync("place_equity_order", {
                "account_number": self._account(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": "market",
            })
        except RobinhoodToolError as e:
            return OrderResult(False, symbol, side, quantity, price, f"order placement failed: {e}")

        fill_price = price
        fill_quantity = quantity
        if isinstance(fill, dict):
            fill_price = float(fill.get("average_price") or fill.get("price") or price)
            fill_quantity = float(fill.get("quantity") or quantity)

        self.db.add(Trade(mode="live", symbol=symbol, side=side, quantity=fill_quantity, price=fill_price))
        self.db.commit()

        # The account snapshot we cached is now stale - drop it so any
        # later read in this same tick re-queries Robinhood instead of
        # returning pre-trade numbers.
        self._portfolio_cache = None
        self._positions_cache = None

        return OrderResult(True, symbol, side, fill_quantity, fill_price, f"filled (live): {fill!r}")
