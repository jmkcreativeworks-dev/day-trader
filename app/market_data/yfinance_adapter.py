"""yfinance-backed market data adapter. No API key required, good enough
for a 15-minute-tick paper/day-trading loop. Swap for a paid feed later
if you need lower latency or hit rate limits."""
import logging

import pandas as pd
import yfinance as yf

from app.market_data import MarketDataAdapter, Quote, Indicators

logger = logging.getLogger(__name__)

# A small fixed universe to scan for "movers" mode - broaden as needed.
SCAN_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "AMZN", "GOOGL", "META",
    "AVGO", "NFLX", "CRM", "ORCL", "PLTR", "COIN", "SMCI", "MU",
]


def _rsi(series: pd.Series, period: int = 14) -> float | None:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    if avg_loss.iloc[-1] == 0:
        return 100.0
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1]
    return float(100 - (100 / (1 + rs)))


class YFinanceAdapter(MarketDataAdapter):
    def get_quote(self, symbol: str) -> Quote | None:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="2d", interval="1d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            prior_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
            change_pct = ((price - prior_close) / prior_close * 100) if prior_close else None
            return Quote(symbol=symbol, price=price, prior_close=prior_close,
                         day_change_pct=change_pct)
        except Exception:
            logger.exception("get_quote failed for %s", symbol)
            return None

    def get_indicators(self, symbol: str) -> Indicators | None:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="3mo", interval="1d")
            if hist.empty or len(hist) < 20:
                return Indicators(symbol=symbol)
            close = hist["Close"]
            sma_20 = float(close.rolling(20).mean().iloc[-1])
            sma_50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
            rsi_14 = _rsi(close) if len(close) >= 15 else None
            return Indicators(symbol=symbol, sma_20=sma_20, sma_50=sma_50, rsi_14=rsi_14)
        except Exception:
            logger.exception("get_indicators failed for %s", symbol)
            return Indicators(symbol=symbol)

    def scan_movers(self, max_candidates: int) -> list[str]:
        movers = []
        for sym in SCAN_UNIVERSE:
            q = self.get_quote(sym)
            if q and q.day_change_pct is not None:
                movers.append((sym, abs(q.day_change_pct)))
        movers.sort(key=lambda x: x[1], reverse=True)
        return [m[0] for m in movers[:max_candidates]]
