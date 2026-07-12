"""Broker adapter interface. PaperBroker (simulated) and RobinhoodBroker
(real, Phase 2) both implement this so the rest of the app never needs to
know which one is active."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionInfo:
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.quantity


@dataclass
class OrderResult:
    success: bool
    symbol: str
    side: str
    quantity: float
    price: float
    message: Optional[str] = None


class BrokerAdapter(ABC):
    """Every broker implementation (paper or real) must implement this."""

    mode: str  # "paper" or "live"

    @abstractmethod
    def get_cash(self) -> float:
        ...

    @abstractmethod
    def get_positions(self) -> list[PositionInfo]:
        ...

    @abstractmethod
    def get_portfolio_value(self) -> float:
        ...

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: float, price: float) -> OrderResult:
        """side: 'buy' or 'sell'. price is the reference/limit price used
        for the simulated or real fill."""
        ...
