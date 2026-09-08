"""Performing per-trade risk checks."""

import logging
import math

import redis
from ib_async import Contract

from config import NOTIONAL_LIMIT, POSITION_CAP

from .models import Snapshot, Verdict
from .state import snapshot

log = logging.getLogger(__name__)

_SIGN = {"BUY": 1.0, "SELL": -1.0}


class RiskRejected(Exception):
    def __init__(self, verdict: Verdict):
        self.verdict = verdict
        super().__init__(
            f"""Risk check failed. Order Rejected.
                Check: {verdict.check}
                Reason: {verdict.reason}
                Detailed values: {', '.join(f'{k}: {v:+.4f}' for k, v in verdict.values.items())}"""
        )


def check_position_cap(
    contract: Contract, action: str, qty: float, snap: Snapshot
) -> Verdict:
    """Check the projected position in one symbol against `POSITION_CAP`.

    projected = current position + this order + every open order on this symbol in the same direction

    Args:
        contract: The qualified contract of the order.

        action: The normalized action from `validate_order_request`, so
            `"BUY"` or `"SELL"`.

        qty: The order quantity. Positive, as `validate_order_request` has
            already established.

        snapshot: One read of the state, taken once and shared by every check,
            so that the four checks cannot disagree about the position.

    Returns:
        A Verdict. `values` holds the position arithmetic.

    Raises:
        ValueError: `action` is neither `"BUY"` nor `"SELL"`.
    """
    if action not in _SIGN:
        raise ValueError(f"Action must be 'BUY' or 'SELL', got {action!r}.")

    symbol = contract.symbol
    sign = _SIGN[action]

    holding = snap.positions.get(symbol)
    current = holding.qty if holding is not None else 0.0

    pending = sign * sum(
        order.remaining
        for order in snap.open_orders.values()
        if order.symbol == symbol and order.action == action
    )

    projected = current + sign * qty + pending
    exposure = abs(projected)
    passed = exposure <= POSITION_CAP

    values = {
        "current_qty": current,
        "order_qty": sign * qty,
        "pending_same_direction": pending,
        "projected_qty": projected,
        "exposure": exposure,
        "cap": float(POSITION_CAP),
    }
    reason = (
        f"{symbol} projected {projected:+g} "
        f"(current {current:+g}, order {sign * qty:+g}, "
        f"pending {pending:+g}), exposure {exposure:g} vs cap {POSITION_CAP}"
    )

    if passed:
        log.info(f"risk pass position_cap: {reason}")
    else:
        log.warning(f"risk REJECT position_cap: {reason}")

    return Verdict(check="position_cap", passed=passed, reason=reason, values=values)


def get_market_price(client: redis.Redis, contract: Contract) -> float | None:
    raw = client.get(f"price:{contract.symbol}")
    if raw is None:
        return None
    try:
        price = float(raw)
    except ValueError:
        log.exception(f"{raw} couldn't be convert to float.")
        return None
    if math.isnan(price) or price <= 0:
        return None
    return price


def check_notional_limit(
    contract: Contract,
    action: str,
    qty: float,
    market_price: float,
    limit_price: float | None,
    snap: Snapshot,
) -> Verdict:
    """Check the projected mark-to-market exposure in one symbol against
    `NOTIONAL_LIMIT`.

    Every share is valued at `market_price`, whether it is already held, is
    sitting unfilled in an open order, or belongs to this order. Notional
    exposure is what a position is worth, not what it cost to acquire, so the
    provenance of a share must not change its value.

    Args:
        contract: The qualified contract of the order.
        action: The normalized action, so `"BUY"` or `"SELL"`.
        qty: The order quantity, positive.
        market_price: The resolved market price of the symbol.
        limit_price: The limit price of the order, or None for a market order.
            Logged only.
        snap: One read of the state, shared by every check.

    Returns:
        A Verdict. `values` holds the exposure arithmetic.

    Raises:
        ValueError: `action` is neither `"BUY"` nor `"SELL"`.
    """
    if action not in _SIGN:
        raise ValueError(f"Action must be 'BUY' or 'SELL', got {action!r}.")

    symbol = contract.symbol
    sign = _SIGN[action]

    holding = snap.positions.get(symbol)
    current_qty = holding.qty if holding is not None else 0.0

    pending_qty = sign * sum(
        order.remaining
        for order in snap.open_orders.values()
        if order.symbol == symbol and order.action == action
    )

    projected_qty = current_qty + sign * qty + pending_qty
    projected = projected_qty * market_price
    exposure = abs(projected)
    passed = exposure <= NOTIONAL_LIMIT

    current_value = current_qty * market_price
    order_value = sign * qty * market_price
    pending_value = pending_qty * market_price

    values = {
        "current_value": current_value,
        "order_value": order_value,
        "pending_same_direction": pending_value,
        "projected_value": projected,
        "exposure": exposure,
        "cap": float(NOTIONAL_LIMIT),
        "market_price": market_price,
        "limit_price": limit_price,
    }
    reason = (
        f"{symbol} projected {projected:+.4f} "
        f"(current {current_value:+.4f}, order {order_value:+.4f}, "
        f"pending {pending_value:+.4f}), marked at {market_price:.4f}, "
        f"exposure {exposure:.4f} vs cap {NOTIONAL_LIMIT}"
    )

    if passed:
        log.info(f"risk pass notional_limit: {reason}")
    else:
        log.warning(f"risk REJECT notional_limit: {reason}")

    return Verdict(check="notional_limit", passed=passed, reason=reason, values=values)


def risk_check(
    client: redis.Redis,
    contract: Contract,
    action: str,
    qty: float,
    limit_price: float | None,
) -> None:
    try:
        snap = snapshot(client)
        market_price = get_market_price(client, contract)
    except redis.RedisError:
        log.error("Could not connect to Redis.")
        raise RiskRejected(
            Verdict(
                check="redis_unavailable",
                passed=False,
                reason="Redis is unavailable.",
                values={"redis": None},
            )
        )
    if snap.last_sync is None:
        raise RiskRejected(
            Verdict(
                check="redis_staleness",
                passed=False,
                reason="Redis last_sync is None. It is flushed.",
                values={"last_sync": None},
            )
        )
    if not market_price:
        raise RiskRejected(
            Verdict(
                check="market_price",
                passed=False,
                reason=f"No market price available for {contract.symbol}.",
                values={"market_price": None},
            )
        )
    position_cap_verdict = check_position_cap(contract, action, qty, snap)
    if not position_cap_verdict.passed:
        raise RiskRejected(position_cap_verdict)

    notional_limit_verdict = check_notional_limit(
        contract, action, qty, market_price, limit_price, snap
    )
    if not notional_limit_verdict.passed:
        raise RiskRejected(notional_limit_verdict)

    log.info(
        f"Risk check passed for order: {contract.symbol} - {action} {qty} at {market_price}."
    )
