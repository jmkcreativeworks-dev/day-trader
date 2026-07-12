"""The trading loop. Runs every TICK_INTERVAL_MINUTES, only during US
market hours, and only while the bot is not paused."""
import logging
from datetime import datetime, time, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.db import SessionLocal
from app.models import BotState, PortfolioSnapshot, AIDecision
from app.market_data.yfinance_adapter import YFinanceAdapter
from app.brokers.paper_broker import PaperBroker
from app.risk.risk_manager import RiskManager
from app.strategy.watchlist import load_watchlist
from app.strategy.claude_decision_engine import ClaudeDecisionEngine

logger = logging.getLogger(__name__)

# Market hours are US/Eastern 9:30-16:00. This assumes the host runs in UTC
# (typical for a Docker container) and converts naively; for DST-perfect
# behavior swap in `zoneinfo.ZoneInfo("America/New_York")` if the host tz
# handling gets fiddly.
MARKET_OPEN_UTC = time(13, 30)   # 9:30 ET in standard time; adjust for DST
MARKET_CLOSE_UTC = time(20, 0)   # 16:00 ET in standard time


def _within_market_hours(now_utc: datetime) -> bool:
    if now_utc.weekday() >= 5:  # Sat/Sun
        return False
    return MARKET_OPEN_UTC <= now_utc.time() <= MARKET_CLOSE_UTC


def get_broker(db, market_data):
    """Returns the active broker for the configured mode. Live mode is
    intentionally not wired up yet - see robinhood_broker.py."""
    if settings.TRADING_MODE == "live":
        from app.brokers.robinhood_broker import RobinhoodBroker
        return RobinhoodBroker()
    price_lookup = lambda sym: (market_data.get_quote(sym) or type("Q", (), {"price": None})).price
    return PaperBroker(db, price_lookup)


def run_tick():
    db = SessionLocal()
    try:
        state = db.query(BotState).first()
        if not state:
            state = BotState(mode=settings.TRADING_MODE)
            db.add(state)
            db.commit()

        now = datetime.now(timezone.utc)
        if not _within_market_hours(now):
            logger.info("Outside market hours, skipping tick")
            return
        if not state.running:
            logger.info("Bot is paused (%s), skipping tick", state.paused_reason)
            return

        market_data = YFinanceAdapter()
        broker = get_broker(db, market_data)
        risk = RiskManager(db, broker)

        kill_reason = risk.check_kill_switch()
        if kill_reason:
            logger.warning("Kill switch tripped: %s", kill_reason)
            return

        tickers = load_watchlist(market_data)
        engine = ClaudeDecisionEngine(broker, market_data)
        decisions = engine.get_decisions(tickers)

        for d in decisions:
            quote = market_data.get_quote(d.symbol)
            price = quote.price if quote else None

            record = AIDecision(
                mode=broker.mode, symbol=d.symbol, action=d.action,
                confidence=d.confidence, reasoning=d.reasoning, raw_response=d.raw,
            )

            if d.action == "hold" or price is None:
                record.executed = False
                db.add(record)
                db.commit()
                continue

            requested_qty = d.dollar_amount / price if price else 0
            verdict = risk.evaluate(d.symbol, d.action, requested_qty, price)
            record.quantity = verdict.quantity
            record.risk_adjusted = bool(verdict.note)
            record.risk_note = verdict.note

            if not verdict.allowed:
                record.executed = False
                db.add(record)
                db.commit()
                continue

            result = broker.place_order(d.symbol, d.action, verdict.quantity, price)
            record.executed = result.success
            if not result.success:
                record.risk_note = ((record.risk_note or "") + f" | order failed: {result.message}").strip()
            db.add(record)
            db.commit()

        # Snapshot portfolio value for the equity curve
        snapshot = PortfolioSnapshot(
            mode=broker.mode,
            cash=broker.get_cash(),
            positions_value=broker.get_portfolio_value() - broker.get_cash(),
            total_value=broker.get_portfolio_value(),
        )
        db.add(snapshot)
        state.last_tick_at = now
        db.commit()

    except Exception:
        logger.exception("Tick failed")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_tick, "interval",
        minutes=settings.TICK_INTERVAL_MINUTES,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    scheduler.start()
    return scheduler
