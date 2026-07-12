"""Loads watchlist.yaml and (in 'scan' mode) asks the market data adapter
for additional candidates."""
import yaml

from app.config import settings
from app.market_data import MarketDataAdapter


def load_watchlist(market_data: MarketDataAdapter) -> list[str]:
    with open(settings.WATCHLIST_PATH) as f:
        cfg = yaml.safe_load(f)

    tickers = list(dict.fromkeys(cfg.get("tickers", [])))  # de-dupe, keep order

    if cfg.get("mode") == "scan":
        max_candidates = int(cfg.get("max_scan_candidates", 10))
        scanned = market_data.scan_movers(max_candidates)
        for s in scanned:
            if s not in tickers:
                tickers.append(s)

    return tickers
