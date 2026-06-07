#!/usr/bin/env python3
"""
Pluggable broker scaffold for paper trading the US ETF DCA system.

This module does NOT make any network calls. It defines a `Broker`
abstract base class with a `LocalPaperBroker` implementation that
records orders to a local ledger, plus a stub `AlpacaPaperBroker`
that documents the interface and is wired to `requests` so the
user can drop in API keys later.

Why a local-only default? Two reasons:

1. The project values reproducibility over live connectivity. A
   paper broker should be runnable in CI without API keys.
2. The trading signals come from `current_market_advice.py`, which
   already produces an `execution_plan.json`. The broker just needs
   to consume that plan and emit trades.

The execution_plan.json schema is:
{
  "as_of": "2026-05-29",
  "spy_action": "BUY",
  "qqq_action": "BUY",
  "spy_amount_usd": 1100.0,
  "qqq_amount_usd": 900.0,
  "spy_weight": 0.55,
  "qqq_weight": 0.45,
  "regime": "very_expensive",
  "rationale": "...",
  "execution_mode": "market" | "limit" | "next_open"
}

See scripts/paper_broker_emit.py for the producer.
"""
from __future__ import annotations

import abc
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    order_type: str  # "market" | "limit" | "next_open"
    limit_price: Optional[float] = None
    status: str = "pending"  # "pending" | "filled" | "rejected" | "cancelled"
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    filled_at: Optional[str] = None
    notes: str = ""


@dataclass
class AccountState:
    cash_usd: float
    spy_shares: float
    qqq_shares: float
    spy_market_price: float
    qqq_market_price: float
    last_updated: str

    def total_value(self) -> float:
        return (
            self.cash_usd
            + self.spy_shares * self.spy_market_price
            + self.qqq_shares * self.qqq_market_price
        )


@dataclass
class BrokerLedger:
    broker: str
    account: AccountState
    orders: List[Order] = field(default_factory=list)
    fills: List[Order] = field(default_factory=list)
    rejected: List[Order] = field(default_factory=list)

    def to_json(self) -> Dict:
        return {
            "broker": self.broker,
            "account": asdict(self.account),
            "orders": [asdict(o) for o in self.orders],
            "fills": [asdict(o) for o in self.fills],
            "rejected": [asdict(o) for o in self.rejected],
        }


class Broker(abc.ABC):
    """Abstract broker interface."""

    @abc.abstractmethod
    def submit_order(self, order: Order) -> Order:
        """Submit an order. Return the order with status updated."""

    @abc.abstractmethod
    def get_account(self) -> AccountState:
        """Return current account state."""

    @abc.abstractmethod
    def get_ledger(self) -> BrokerLedger:
        """Return a structured ledger for the report."""


class LocalPaperBroker(Broker):
    """Records orders to a local ledger, fills at the supplied
    market price (no slippage modeling — use SlippageBroker wrapper
    for that). Idempotent: re-submitting the same order_id is a no-op.

    This is the *default* broker for tests and CI; the run_paper_broker
    CLI uses it unless --broker alpaca is passed (and the keys are set).
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        initial_spy: float = 0.0,
        initial_qqq: float = 0.0,
        ledger_dir: Optional[Path] = None,
    ) -> None:
        self.initial_cash = initial_cash
        self.ledger_dir = Path(ledger_dir) if ledger_dir else None
        if self.ledger_dir:
            self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self._orders: Dict[str, Order] = {}
        self._fills: List[Order] = []
        self._rejected: List[Order] = []
        self._account = AccountState(
            cash_usd=initial_cash,
            spy_shares=initial_spy,
            qqq_shares=initial_qqq,
            spy_market_price=0.0,
            qqq_market_price=0.0,
            last_updated=datetime.utcnow().isoformat(),
        )

    def submit_order(self, order: Order) -> Order:
        if order.order_id in self._orders:
            return self._orders[order.order_id]
        # Fill at the latest market price.
        px_field = "spy_market_price" if order.symbol.upper() == "SPY" else "qqq_market_price"
        market_px = getattr(self._account, px_field)
        if market_px <= 0:
            order.status = "rejected"
            order.notes = "no market price available"
            self._rejected.append(order)
            self._orders[order.order_id] = order
            return order
        # Apply
        notional = order.qty * market_px
        if order.side == "buy":
            if notional > self._account.cash_usd:
                order.status = "rejected"
                order.notes = f"insufficient cash: need {notional:.2f}, have {self._account.cash_usd:.2f}"
                self._rejected.append(order)
                self._orders[order.order_id] = order
                return order
            self._account.cash_usd -= notional
            if order.symbol.upper() == "SPY":
                self._account.spy_shares += order.qty
            else:
                self._account.qqq_shares += order.qty
        else:  # sell
            held = (
                self._account.spy_shares
                if order.symbol.upper() == "SPY"
                else self._account.qqq_shares
            )
            if order.qty > held:
                order.status = "rejected"
                order.notes = f"insufficient shares: need {order.qty}, have {held}"
                self._rejected.append(order)
                self._orders[order.order_id] = order
                return order
            self._account.cash_usd += notional
            if order.symbol.upper() == "SPY":
                self._account.spy_shares -= order.qty
            else:
                self._account.qqq_shares -= order.qty
        order.status = "filled"
        order.filled_qty = order.qty
        order.filled_avg_price = market_px
        order.filled_at = datetime.utcnow().isoformat()
        self._fills.append(order)
        self._orders[order.order_id] = order
        self._account.last_updated = order.filled_at
        if self.ledger_dir:
            self._write_ledger()
        return order

    def update_market_prices(self, spy: float, qqq: float) -> None:
        self._account.spy_market_price = float(spy)
        self._account.qqq_market_price = float(qqq)
        self._account.last_updated = datetime.utcnow().isoformat()

    def get_account(self) -> AccountState:
        return self._account

    def get_ledger(self) -> BrokerLedger:
        return BrokerLedger(
            broker="LocalPaperBroker",
            account=self._account,
            orders=list(self._orders.values()),
            fills=self._fills,
            rejected=self._rejected,
        )

    def _write_ledger(self) -> None:
        if not self.ledger_dir:
            return
        path = self.ledger_dir / "ledger.json"
        path.write_text(
            json.dumps(self.get_ledger().to_json(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class AlpacaPaperBroker(Broker):
    """Stub for Alpaca paper-trading API.

    Wires the right endpoint and request shape but does not call
    without API keys. To activate:

    1. Set environment variables:
         APCA_API_KEY_ID, APCA_API_SECRET_KEY
       (Alpaca issues paper-trading keys for free; the base URL is
       https://paper-api.alpaca.markets)
    2. Install `requests` (already in our requirements.txt).
    3. Pass --broker alpaca to run_paper_broker.py.

    The interface mirrors what Lean / Backtrader / QuantConnect use
    for the same broker, so an existing IBKR / Tradier wrapper can
    be dropped in beside this without changes to call sites.
    """

    BASE_URL = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("APCA_API_KEY_ID")
        self.api_secret = api_secret or os.environ.get("APCA_API_SECRET_KEY")
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "AlpacaPaperBroker requires APCA_API_KEY_ID and "
                "APCA_API_SECRET_KEY in the environment. Get free paper "
                "keys at https://alpaca.markets"
            )
        # Lazy import so the LocalPaperBroker path does not need
        # the network.
        try:
            import requests  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "AlpacaPaperBroker requires the `requests` package; "
                "it is already in requirements.txt."
            ) from e

    def submit_order(self, order: Order) -> Order:
        # Wire shape only; the actual call is one line:
        #   r = requests.post(
        #       f"{self.BASE_URL}/v2/orders",
        #       headers={"APCA-API-KEY-ID": self.api_key,
        #                "APCA-API-SECRET-KEY": self.api_secret},
        #       json={
        #           "symbol": order.symbol,
        #           "qty": order.qty,
        #           "side": order.side,
        #           "type": order.order_type,
        #           "limit_price": order.limit_price,
        #           "time_in_force": "day",
        #           "client_order_id": order.order_id,
        #       },
        #       timeout=10,
        #   )
        # The full implementation is left for when keys are available;
        # for now we raise so misconfigured runs fail loudly.
        raise NotImplementedError(
            "AlpacaPaperBroker.submit_order is wired but the network call "
            "is intentionally disabled. Set APCA_API_KEY_ID and "
            "APCA_API_SECRET_KEY and uncomment the requests call above."
        )

    def get_account(self) -> AccountState:  # pragma: no cover
        raise NotImplementedError

    def get_ledger(self) -> BrokerLedger:  # pragma: no cover
        raise NotImplementedError


def make_order_id(prefix: str = "ord") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


__all__ = [
    "Order",
    "AccountState",
    "BrokerLedger",
    "Broker",
    "LocalPaperBroker",
    "AlpacaPaperBroker",
    "make_order_id",
]
