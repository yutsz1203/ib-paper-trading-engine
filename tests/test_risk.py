"""Unit tests for the pre-trade risk gates."""

import pytest
from ib_async import Stock

from engine.models import Holding, OpenOrder, Snapshot
from engine.risk import check_notional_limit, check_position_cap


def make_order(
    symbol: str,
    action: str,
    remaining: float,
    lmtPrice: float,
    permId: int,
) -> OpenOrder:
    return OpenOrder(
        orderId=1,
        orderRef="test",
        permId=permId,
        action=action,
        orderType="LMT" if lmtPrice > 0 else "MKT",
        lmtPrice=lmtPrice,
        symbol=symbol,
        status="Submitted",
        filled=0.0,
        remaining=remaining,
    )


def make_snapshot(holdings: list[Holding], open_orders: list[OpenOrder]) -> Snapshot:
    return Snapshot(
        positions={h.symbol: h for h in holdings},
        open_orders={open_order.permId: open_order for open_order in open_orders},
        cash=None,
        last_sync=None,
    )


def test_position_cap_passes_when_projected_position_lands_exactly_on_cap(monkeypatch):
    monkeypatch.setattr("engine.risk.POSITION_CAP", 500)

    contract = Stock("RDDT", "SMART", "USD")
    snap = make_snapshot(
        holdings=[Holding("RDDT", 300.0, 149.0)],
        open_orders=[
            make_order("RDDT", "BUY", remaining=150.0, lmtPrice=0.0, permId=1),
            make_order("RDDT", "SELL", remaining=200.0, lmtPrice=0.0, permId=2),
            make_order("GOOGL", "BUY", remaining=400.0, lmtPrice=0.0, permId=3),
        ],
    )
    verdict = check_position_cap(contract, "BUY", 50.0, snap)

    assert verdict.passed is True
    assert verdict.check == "position_cap"
    assert verdict.values["pending_same_direction"] == pytest.approx(150.0)
    assert verdict.values["projected_qty"] == pytest.approx(500.0)
    assert verdict.values["cap"] == pytest.approx(500.0)


def test_position_cap_rejects_short_when_projected_position_exceeds_cap(monkeypatch):
    """The short side: the cap binds on magnitude, so -501 breaches it too."""
    monkeypatch.setattr("engine.risk.POSITION_CAP", 500)

    contract = Stock("RDDT", "SMART", "USD")
    snap = make_snapshot(
        holdings=[Holding("RDDT", -300.0, 149.0)],
        open_orders=[
            make_order("RDDT", "SELL", remaining=200.0, lmtPrice=0.0, permId=1),
            make_order("RDDT", "BUY", remaining=200.0, lmtPrice=0.0, permId=2),
            make_order("GOOGL", "SELL", remaining=400.0, lmtPrice=0.0, permId=3),
        ],
    )
    verdict = check_position_cap(contract, "SELL", 1.0, snap)

    assert verdict.passed is False
    assert verdict.check == "position_cap"
    assert verdict.values["pending_same_direction"] == pytest.approx(-200.0)
    assert verdict.values["projected_qty"] == pytest.approx(-501.0)
    assert verdict.values["exposure"] == pytest.approx(501.0)
    assert verdict.values["cap"] == pytest.approx(500.0)


def test_notional_limit_passes_when_projected_notional_lands_exactly_on_limit(
    monkeypatch,
):
    monkeypatch.setattr("engine.risk.NOTIONAL_LIMIT", 10000)

    contract = Stock("RDDT", "SMART", "USD")
    snap = make_snapshot(
        holdings=[Holding("RDDT", 50.0, 149.0)],
        open_orders=[
            make_order("RDDT", "BUY", remaining=10.0, lmtPrice=0.0, permId=1),
            make_order("RDDT", "SELL", remaining=200.0, lmtPrice=0.0, permId=2),
            make_order("GOOGL", "BUY", remaining=300.0, lmtPrice=0.0, permId=3),
        ],
    )

    verdict = check_notional_limit(contract, "BUY", 40.0, 100.0, None, snap)

    assert verdict.passed is True
    assert verdict.check == "notional_limit"
    assert verdict.values["market_price"] == pytest.approx(100.0)
    assert verdict.values["pending_same_direction"] == pytest.approx(1000.0)
    assert verdict.values["projected_value"] == pytest.approx(10000.0)
    assert verdict.values["cap"] == pytest.approx(10000.0)


def test_notional_limit_rejects_short_when_projected_notional_exceeds_limit(
    monkeypatch,
):
    """The limit price is recorded on the verdict but never valued against."""
    monkeypatch.setattr("engine.risk.NOTIONAL_LIMIT", 10000)

    contract = Stock("RDDT", "SMART", "USD")
    snap = make_snapshot(
        holdings=[Holding("RDDT", -50.0, 149.0)],
        open_orders=[
            make_order("RDDT", "SELL", remaining=10.0, lmtPrice=0.0, permId=1),
            make_order("RDDT", "BUY", remaining=200.0, lmtPrice=0.0, permId=2),
            make_order("GOOGL", "SELL", remaining=400.0, lmtPrice=0.0, permId=3),
        ],
    )

    verdict = check_notional_limit(contract, "SELL", 50.0, 100.0, 110.0, snap)

    assert verdict.passed is False
    assert verdict.check == "notional_limit"
    assert verdict.values["market_price"] == pytest.approx(100.0)
    assert verdict.values["limit_price"] == pytest.approx(110.0)
    assert verdict.values["pending_same_direction"] == pytest.approx(-1000.0)
    assert verdict.values["projected_value"] == pytest.approx(-11000.0)
    assert verdict.values["exposure"] == pytest.approx(11000.0)
    assert verdict.values["cap"] == pytest.approx(10000.0)
