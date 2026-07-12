"""Builds the market/portfolio context, asks Claude for a structured
trading decision per symbol, and returns parsed results. This is the only
place that talks to the Anthropic API."""
import json
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.brokers import BrokerAdapter
from app.config import settings
from app.market_data import MarketDataAdapter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a disciplined day-trading assistant operating a \
{mode} brokerage account. You will be shown the current portfolio and \
recent market data for a watchlist of tickers. For EACH ticker, decide \
one action: "buy", "sell", or "hold".

Rules:
- Be conservative. Most ticks should result in mostly "hold" decisions -
  only act when there is a clear signal.
- You do not control position sizing limits or risk limits - a separate
  risk manager enforces those and may shrink or reject your order. Just
  state what you'd ideally do and how confident you are (0-1).
- Never assume options trading is allowed unless told so explicitly.
- Respond with ONLY a JSON array, no prose, no markdown fences. Each
  element: {{"symbol": str, "action": "buy"|"sell"|"hold", \
"dollar_amount": number (0 if hold), "confidence": number 0-1, \
"reasoning": short string explaining why}}.
"""


@dataclass
class Decision:
    symbol: str
    action: str
    dollar_amount: float
    confidence: float
    reasoning: str
    raw: str


class ClaudeDecisionEngine:
    def __init__(self, broker: BrokerAdapter, market_data: MarketDataAdapter):
        self.broker = broker
        self.market_data = market_data
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def _build_context(self, tickers: list[str]) -> str:
        cash = self.broker.get_cash()
        positions = self.broker.get_positions()
        portfolio_value = self.broker.get_portfolio_value()

        lines = [
            f"Portfolio value: ${portfolio_value:,.2f}",
            f"Cash available: ${cash:,.2f}",
            "Open positions:",
        ]
        if positions:
            for p in positions:
                lines.append(
                    f"  {p.symbol}: {p.quantity} shares @ avg ${p.avg_cost:.2f}, "
                    f"current ${p.current_price:.2f}, "
                    f"unrealized P&L ${p.unrealized_pnl:,.2f}"
                )
        else:
            lines.append("  (none)")

        lines.append("\nWatchlist data:")
        for sym in tickers:
            q = self.market_data.get_quote(sym)
            ind = self.market_data.get_indicators(sym)
            if not q:
                lines.append(f"  {sym}: quote unavailable, skip")
                continue
            change = f"{q.day_change_pct:+.2f}%" if q.day_change_pct is not None else "n/a"
            sma20 = f"{ind.sma_20:.2f}" if ind and ind.sma_20 else "n/a"
            sma50 = f"{ind.sma_50:.2f}" if ind and ind.sma_50 else "n/a"
            rsi = f"{ind.rsi_14:.1f}" if ind and ind.rsi_14 else "n/a"
            lines.append(
                f"  {sym}: price ${q.price:.2f} ({change} today), "
                f"SMA20 {sma20}, SMA50 {sma50}, RSI14 {rsi}"
            )

        return "\n".join(lines)

    def get_decisions(self, tickers: list[str]) -> list[Decision]:
        context = self._build_context(tickers)
        system = SYSTEM_PROMPT.format(mode=self.broker.mode)

        message = self.client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": context}],
        )
        text = "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()

        try:
            # Be tolerant of accidental markdown fences despite instructions
            cleaned = text.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Could not parse Claude response as JSON: %s", text)
            return []

        decisions = []
        for item in parsed:
            try:
                decisions.append(Decision(
                    symbol=item["symbol"],
                    action=item["action"],
                    dollar_amount=float(item.get("dollar_amount", 0) or 0),
                    confidence=float(item.get("confidence", 0) or 0),
                    reasoning=item.get("reasoning", ""),
                    raw=json.dumps(item),
                ))
            except (KeyError, ValueError, TypeError):
                logger.exception("Skipping malformed decision item: %s", item)
        return decisions
