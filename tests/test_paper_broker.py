"""Tests for scripts/paper_broker.py — local paper broker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from paper_broker import (  # noqa: E402
    LocalPaperBroker,
    Order,
    AlpacaPaperBroker,
    make_order_id,
)


def test_buy_fills_at_market_price():
    b = LocalPaperBroker(initial_cash=100_000.0)
    b.update_market_prices(spy=500.0, qqq=400.0)
    o = Order(order_id="t1", symbol="SPY", side="buy", qty=10, order_type="market")
    o2 = b.submit_order(o)
    assert o2.status == "filled"
    assert o2.filled_qty == 10
    assert o2.filled_avg_price == 500.0
    assert b.get_account().cash_usd == pytest.approx(95_000.0)
    assert b.get_account().spy_shares == 10


def test_sell_fills_and_credits_cash():
    b = LocalPaperBroker(initial_cash=0.0, initial_spy=10.0)
    b.update_market_prices(spy=500.0, qqq=400.0)
    o = Order(order_id="t1", symbol="SPY", side="sell", qty=5, order_type="market")
    o2 = b.submit_order(o)
    assert o2.status == "filled"
    assert b.get_account().cash_usd == pytest.approx(2_500.0)
    assert b.get_account().spy_shares == 5


def test_buy_rejected_on_insufficient_cash():
    b = LocalPaperBroker(initial_cash=100.0)
    b.update_market_prices(spy=500.0, qqq=400.0)
    o = Order(order_id="t1", symbol="SPY", side="buy", qty=10, order_type="market")
    o2 = b.submit_order(o)
    assert o2.status == "rejected"
    assert "insufficient cash" in o2.notes


def test_sell_rejected_on_insufficient_shares():
    b = LocalPaperBroker(initial_cash=0.0, initial_spy=1.0)
    b.update_market_prices(spy=500.0, qqq=400.0)
    o = Order(order_id="t1", symbol="SPY", side="sell", qty=10, order_type="market")
    o2 = b.submit_order(o)
    assert o2.status == "rejected"


def test_duplicate_order_id_is_idempotent():
    b = LocalPaperBroker(initial_cash=10_000.0)
    b.update_market_prices(spy=500.0, qqq=400.0)
    o = Order(order_id="t1", symbol="SPY", side="buy", qty=2, order_type="market")
    o2 = b.submit_order(o)
    o3 = b.submit_order(o2)
    assert o2.order_id == o3.order_id
    assert b.get_account().spy_shares == 2  # not 4


def test_alpaca_broker_raises_without_keys(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="APCA_API_KEY_ID"):
        AlpacaPaperBroker()


def test_alpaca_broker_submit_raises_not_implemented(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "test")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test")
    b = AlpacaPaperBroker()
    o = Order(order_id="t1", symbol="SPY", side="buy", qty=1, order_type="market")
    with pytest.raises(NotImplementedError):
        b.submit_order(o)


def test_make_order_id_unique():
    a = make_order_id("ord")
    b = make_order_id("ord")
    assert a != b
    assert a.startswith("ord-")
