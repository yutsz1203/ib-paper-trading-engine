"""Compare Redis against IB, before a resync overwrites Redis."""

import logging
from dataclasses import dataclass

from ib_async import Fill, Trade
from ib_async.objects import UNSET_DOUBLE

from .models import Holding, OpenOrder

log = logging.getLogger(__name__)

QTY_TOLERANCE = 1e-9
PRICE_PRECISION = 4


@dataclass(frozen=True)
class Difference:
    """One difference between Redis and IB.

    Attributes:
        kind: `position`, `open_order` or `execution`.
        key: The symbol, the permId or the execId.
        detail: What differs, with both values.
    """

    kind: str
    key: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind} {self.key} | {self.detail}"


def _same_qty(left: float, right: float) -> bool:
    """Compare two share counts. Both are floats that hold whole shares."""
    return abs(left - right) <= QTY_TOLERANCE


def _same_price(left: float, right: float) -> bool:
    """Compare two prices at the precision Redis stores."""
    return round(left, PRICE_PRECISION) == round(right, PRICE_PRECISION)


def diff_positions(
    redis_positions: dict[str, Holding], ib_positions: dict[str, Holding]
) -> list[Difference]:
    """Compare the positions in Redis against the positions IB reports.
    Args:
        redis_positions: `snapshot().positions`.
        ib_positions: The result of `apply_commissions` over `read_ib_positions`.

    Returns:
        One Difference per discrepancy, in symbol order.
    """
    differences: list[Difference] = []

    for symbol in sorted(set(redis_positions) | set(ib_positions)):
        in_redis = redis_positions.get(symbol)
        in_ib = ib_positions.get(symbol)

        if in_redis is None:
            differences.append(
                Difference(
                    "position",
                    symbol,
                    f"absent from Redis, IB holds {in_ib.qty}@{in_ib.avg_cost:.4f}",
                )
            )
            continue

        if in_ib is None:
            differences.append(
                Difference(
                    "position",
                    symbol,
                    f"Redis holds {in_redis.qty}@{in_redis.avg_cost:.4f}, "
                    f"IB reports no position",
                )
            )
            continue

        if not _same_qty(in_redis.qty, in_ib.qty):
            differences.append(
                Difference(
                    "position", symbol, f"qty moved {in_redis.qty} -> {in_ib.qty}"
                )
            )

        if not _same_price(in_redis.avg_cost, in_ib.avg_cost):
            differences.append(
                Difference(
                    "position",
                    symbol,
                    f"avg_cost moved {in_redis.avg_cost:.4f} -> {in_ib.avg_cost:.4f}",
                )
            )

    return differences


def diff_open_orders(
    redis_orders: dict[int, OpenOrder],
    ib_trades: dict[int, Trade],
    executions: dict[str, Fill],
) -> list[Difference]:
    """Compare the open orders in Redis against the open orders IB reports.

    An order that is in Redis and not in IB left for one of two reasons. The
    executions of the window say which: an execution carrying that permId means
    it filled, and no execution means it was cancelled or rejected.

    Args:
        redis_orders: `snapshot().open_orders`.
        ib_trades: The result of `read_ib_open_trades`.
        executions: The result of `read_ib_window_executions`.

    Returns:
        One Difference per discrepancy, in permId order.
    """
    differences: list[Difference] = []

    for perm_id in sorted(set(redis_orders) | set(ib_trades)):
        in_redis = redis_orders.get(perm_id)
        trade = ib_trades.get(perm_id)
        key = str(perm_id)

        if in_redis is None:
            order = trade.order
            differences.append(
                Difference(
                    "open_order",
                    key,
                    f"absent from Redis, IB holds {order.action} "
                    f"{order.totalQuantity} {trade.contract.symbol} "
                    f"at {trade.orderStatus.status}",
                )
            )
            continue

        if trade is None:
            fills = [
                fill for fill in executions.values() if fill.execution.permId == perm_id
            ]
            if fills:
                shares = sum(fill.execution.shares for fill in fills)
                differences.append(
                    Difference(
                        "open_order",
                        key,
                        f"gone from IB, filled {shares} {in_redis.symbol} "
                        f"in {len(fills)} executions during the window",
                    )
                )
            else:
                differences.append(
                    Difference(
                        "open_order",
                        key,
                        f"gone from IB with no execution in the window, "
                        f"{in_redis.action} {in_redis.symbol} was "
                        f"{in_redis.status}, presumed cancelled",
                    )
                )
            continue

        order = trade.order
        order_status = trade.orderStatus
        lmt_price = order.lmtPrice if order.lmtPrice != UNSET_DOUBLE else 0.0

        if in_redis.status != order_status.status:
            differences.append(
                Difference(
                    "open_order",
                    key,
                    f"status moved {in_redis.status} -> {order_status.status}",
                )
            )

        if not _same_qty(in_redis.filled, order_status.filled):
            differences.append(
                Difference(
                    "open_order",
                    key,
                    f"filled moved {in_redis.filled} -> {order_status.filled}",
                )
            )

        if not _same_qty(in_redis.remaining, order_status.remaining):
            differences.append(
                Difference(
                    "open_order",
                    key,
                    f"remaining moved {in_redis.remaining} -> {order_status.remaining}",
                )
            )

        if not _same_price(in_redis.lmtPrice, lmt_price):
            differences.append(
                Difference(
                    "open_order",
                    key,
                    f"lmtPrice moved {in_redis.lmtPrice:.4f} -> {lmt_price:.4f}",
                )
            )

    return differences


def diff_executions(
    executions: dict[str, Fill], seen_exec: dict[str, Fill]
) -> list[Difference]:
    """Find the executions of the window that the engine never handled.

    An execId in `seen_exec` and not in the window is not a difference. It
    executed before the window opened, which is the ordinary case.

    Args:
        executions: The result of `read_ib_window_executions`.
        seen_exec: The execIds `on_exec_details` has already applied.

    Returns:
        One Difference per missed execution, in execId order.
    """
    differences: list[Difference] = []

    for exec_id in sorted(set(executions) - set(seen_exec)):
        execution = executions[exec_id].execution
        symbol = executions[exec_id].contract.symbol
        differences.append(
            Difference(
                "execution",
                exec_id,
                f"missed fill {execution.side} {execution.shares} {symbol} "
                f"@ {execution.price} | permId={execution.permId} "
                f"orderId={execution.orderId} time={execution.time.isoformat()}",
            )
        )

    return differences


def diff_state(
    redis_positions: dict[str, Holding],
    ib_positions: dict[str, Holding],
    redis_orders: dict[int, OpenOrder],
    ib_trades: dict[int, Trade],
    executions: dict[str, Fill],
    seen_exec: dict[str, Fill],
) -> list[Difference]:
    """Run the three diffs in one call.
    Returns:
        Every Difference, positions first, then open orders, then executions.
    """
    return (
        diff_positions(redis_positions, ib_positions)
        + diff_open_orders(redis_orders, ib_trades, executions)
        + diff_executions(executions, seen_exec)
    )


def log_differences(differences: list[Difference]) -> None:
    """Log every difference, and log the empty case too.

    Args:
        differences: The result of `diff_state`.
    """
    if not differences:
        log.info("Reconcile: no difference between Redis and IB.")
        return

    for difference in differences:
        log.warning(f"Reconcile: {difference}")

    log.info(
        f"Reconcile: {len(differences)} differences found. Overwriting Redis from IB."
    )
