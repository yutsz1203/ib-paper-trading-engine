import asyncio
import logging
from contextlib import suppress

import redis
from ib_async import IB

from config import IB_CLIENT_ID

from .ib_client import (
    attach_reconnect_signal,
    connect,
    on_pending,
    publish_prices,
    reconnect_loop,
    subscribe,
)
from .logging_config import setup_logging
from .orders import (
    on_commission_report,
    on_exec_details,
    on_order_status,
    seed_from_ib,
    seed_trades_from_ib,
    seen_exec,
)
from .reconcile import diff_state, log_differences
from .state import (
    check_redis,
    read_ib_open_trades,
    read_ib_positions,
    read_ib_window_executions,
    redis_client,
    resync_from_ib,
    snapshot,
    window_start,
)

setup_logging()
log = logging.getLogger(__name__)


async def recover(ib: IB) -> None:
    try:
        redis_snapshot = snapshot(redis_client)
        since = window_start(redis_snapshot.last_sync)
        ib_execs = await read_ib_window_executions(ib, since)
        ib_positions = read_ib_positions(ib)
        ib_open_trades = read_ib_open_trades(ib)
        differences = diff_state(
            redis_snapshot.positions,
            ib_positions,
            redis_snapshot.open_orders,
            ib_open_trades,
            ib_execs,
            seen_exec,
        )
        log_differences(differences)
    except (TimeoutError, redis.RedisError):
        log.error("Diff skipped. The resync still runs.")
    try:
        resync_from_ib(ib, redis_client)
    except redis.RedisError:
        log.error("Could not connect to Redis. IB remains the source of truth.")
    seed_from_ib(ib)
    seed_trades_from_ib(ib)
    await subscribe(ib)


async def main():
    log.info("Started")

    if not check_redis(redis_client):
        log.error("Redis is unreachable. The engine runs and every state write fails.")

    ib = await connect()
    signal = attach_reconnect_signal(ib)
    await recover(ib)
    reconnect_task = asyncio.create_task(
        reconnect_loop(ib, IB_CLIENT_ID, signal, recover)
    )
    ib.pendingTickersEvent += on_pending
    price_task = asyncio.create_task(publish_prices(ib, redis_client))

    ib.orderStatusEvent += on_order_status
    ib.execDetailsEvent += on_exec_details
    ib.commissionReportEvent += on_commission_report
    log.info("Order handlers attached. This process owns the Redis state.")

    try:
        await reconnect_task
    finally:
        price_task.cancel()
        with suppress(asyncio.CancelledError):
            await price_task
        for ticker in ib.tickers():
            ib.cancelMktData(ticker.contract)
        ib.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
