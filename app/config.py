"""Central config, loaded from environment (.env in dev/prod)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://daytrader:daytrader@localhost:5434/daytrader"
    )

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

    TRADING_MODE: str = os.getenv("TRADING_MODE", "paper")  # paper | live
    PAPER_STARTING_CASH: float = float(os.getenv("PAPER_STARTING_CASH", "10000"))

    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.05"))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "8"))
    ALLOW_OPTIONS: bool = _bool("ALLOW_OPTIONS", "false")

    TICK_INTERVAL_MINUTES: int = int(os.getenv("TICK_INTERVAL_MINUTES", "15"))

    ROBINHOOD_MCP_URL: str = os.getenv(
        "ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading"
    )

    WATCHLIST_PATH: str = os.getenv("WATCHLIST_PATH", "app/watchlist.yaml")

    # HTTP Basic Auth on the whole app - defense in depth in case the
    # dashboard is ever reached directly via LAN/Tailscale IP instead of
    # through the Cloudflare-Access-gated hostname. Users are DB-backed
    # (dashboard_users, managed from /users) - these two vars only seed
    # the first account when that table is empty.
    DASHBOARD_USERNAME: str = os.getenv("DASHBOARD_USERNAME", "admin")
    DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_PASSWORD", "")


settings = Settings()
