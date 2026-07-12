"""Market data adapter interface - pluggable so the yfinance source can
later be swapped for Robinhood's own market-data MCP tools or another
provider without touching the decision engine."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Quote:
    symbol: str
    price: float
    prior_close: Optional[float] = None
    day_change_pct: Optional[float] = None


@dataclass
class Indicators:
    symbol: str
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    rsi_14: Optional[float] = None


class MarketDataAdapter(ABC):
    @abstractmethod
    def get_quote(self, symbol: str) -> Optional[Quote]:
        ...

    @abstractmethod
    def get_indicators(self, symbol: str) -> Optional[Indicators]:
        ...

    @abstractmethod
    def scan_movers(self, max_candidates: int) -> list[str]:
        """Return up to max_candidates tickers for 'scan' watchlist mode."""
        ...
