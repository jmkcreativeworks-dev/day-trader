"""Phase 2 stub. Robinhood has no conventional REST trading API - live
trading happens through the Robinhood Trading MCP
(https://agent.robinhood.com/mcp/trading), which is designed for
interactive agent platforms (Claude Desktop/Code, ChatGPT, etc.) rather
than a headless server process.

Before filling this in:
  1. Confirm Robinhood Agentic Trading access has been granted
     (robinhood.com/us/en/agentic-trading).
  2. Confirm whether the MCP session/auth can run unattended long-term,
     or whether it needs a human to periodically re-authenticate. If it
     can't run headless, this adapter may need to shell out to a small
     Claude Code/Desktop-driven process instead of calling the MCP
     directly from this service.
  3. Map these methods to the MCP tools documented at
     robinhood.com/us/en/support/articles/trading-with-your-agent:
       get_cash            -> get_portfolio
       get_positions       -> get_equity_positions
       get_portfolio_value -> get_portfolio
       place_order (buy)   -> review_equity_order, then place_equity_order
       place_order (sell)  -> review_equity_order, then place_equity_order

Until this is implemented, TRADING_MODE=live will raise NotImplementedError
on the first scheduler tick (visible in logs/the dashboard staying flat)
rather than silently pretending to trade.
"""
from app.brokers import BrokerAdapter, PositionInfo, OrderResult


class RobinhoodBroker(BrokerAdapter):
    mode = "live"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "RobinhoodBroker is a Phase 2 stub - see comments in "
            "app/brokers/robinhood_broker.py before enabling live mode."
        )

    def get_cash(self) -> float:
        raise NotImplementedError

    def get_positions(self) -> list[PositionInfo]:
        raise NotImplementedError

    def get_portfolio_value(self) -> float:
        raise NotImplementedError

    def place_order(self, symbol: str, side: str, quantity: float, price: float) -> OrderResult:
        raise NotImplementedError
