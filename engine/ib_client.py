import asyncio
import logging
import math
import random
import time
from typing import Awaitable, Callable

import redis
from ib_async import IB, Contract, Stock, Ticker

from config import (
    BASE,
    CAP,
    GROWTH_FACTOR,
    IB_CLIENT_ID,
    IB_HOST,
    IB_PORT,
    PRICE_LOG_SECONDS,
    PRICE_PUBLISH_SECONDS,
    PROBE_TIMEOUT,
    WATCHDOG_TIMEOUT,
    WATCHLIST,
)

from .state import write_price

DATA_FARM_STATUS_CODES = {2104, 2106, 2107, 2158, 10167}
CONNECTIVITY_CODES = {1100, 1101, 1102}

log = logging.getLogger(__name__)


def on_error(
    reqId: int, errorCode: int, errorString: str, contract: Contract | None
) -> None:
    """
    Error event handler.
    """
    symbol = contract.symbol if contract else "-"
    msg = f"symbol: {symbol}, errorCode: {errorCode}, errorString: {errorString}, reqId: {reqId}"
    if errorCode in DATA_FARM_STATUS_CODES:
        log.debug(msg)
    elif errorCode in CONNECTIVITY_CODES:
        log.warning(msg)
    else:
        log.error(msg)


def on_connect() -> None:
    """
    Connection event handler.
    """
    log.info("Connection established.")


def on_disconnect() -> None:
    """
    Disconnection event handler.
    """
    log.info("Disconnected.")


def on_pending(tickers: set[Ticker]) -> None:
    """Pending ticker event handler."""
    for ticker in tickers:
        log.debug(
            f"{ticker.contract.symbol} - Last: {ticker.last}; "
            f"Close: {ticker.close}; Time: {ticker.time}"
        )


def _usable(price: float) -> bool:
    """A price is usable when it is a real, strictly positive number.

    NaN is the value a Ticker field holds before its first tick, and it is
    invisible to comparison: `nan > 0` is False, and so is `nan <= 0`. It has
    to be tested for by name. Zero is rejected as well, because a notional of
    zero passes every limit.
    """
    return price is not None and not math.isnan(price) and price > 0


def pick_price(ticker: Ticker) -> tuple[float | None, str]:
    """Choose the price to publish for one ticker.

    `last` first, because it is the current trade. `close` second, because it
    stays populated outside regular hours, which is exactly when `last` is
    NaN.

    Args:
        ticker: One subscribed ticker.

    Returns:
        The price and the name of the field it came from, or `(None, "none")`
        when neither field is usable.
    """
    if _usable(ticker.last):
        return ticker.last, "last"
    if _usable(ticker.close):
        return ticker.close, "close"
    return None, "none"


async def publish_prices(ib: IB, client: redis.Redis) -> None:
    """Write one price per subscribed symbol to Redis on a fixed interval.
    Args:
        ib: The connected IB instance holding the subscriptions.
        client: The Redis client.
    """
    last_log = float("-inf")
    while True:
        await asyncio.sleep(PRICE_PUBLISH_SECONDS)

        if not ib.isConnected():
            log.debug("Price publish skipped. The connection is down.")
            continue

        written: list[str] = []
        try:
            for ticker in ib.tickers():
                symbol = ticker.contract.symbol
                price, source = pick_price(ticker)
                if price is None:
                    log.debug(
                        f"{symbol} - no usable price. "
                        f"Last: {ticker.last}; Close: {ticker.close}"
                    )
                    continue
                write_price(client, symbol, price)
                written.append(f"{symbol}={price:g}({source})")
        except redis.RedisError as e:
            log.error(f"Price publish failed. Redis is unreachable: {e!r}")
            continue
        except Exception:
            log.exception("Price publish cycle failed. The task continues.")
            continue

        now = time.monotonic()
        if written and now - last_log >= PRICE_LOG_SECONDS:
            log.info(f"Prices published: {', '.join(sorted(written))}")
            last_log = now


def build_ib() -> IB:
    ib = IB()
    ib.connectedEvent += on_connect
    ib.disconnectedEvent += on_disconnect
    ib.errorEvent += on_error

    attach_watchdog(ib)
    return ib


async def connect_ib(ib: IB, client_id: int) -> None:
    await ib.connectAsync(IB_HOST, IB_PORT, client_id)
    ib.setTimeout(WATCHDOG_TIMEOUT)


async def connect(client_id: int = IB_CLIENT_ID) -> IB:
    ib = build_ib()
    await connect_ib(ib, client_id)
    return ib


def build_contracts(symbols: list[str] = WATCHLIST) -> list[Stock]:
    return [
        Stock(symbol=symbol, exchange="SMART", currency="USD") for symbol in symbols
    ]


async def qualify_contracts(ib: IB, contracts: list[Stock]) -> list[Stock]:
    res = await ib.qualifyContractsAsync(*contracts)
    qualified_contracts = []
    for contract, qualified in zip(contracts, res):
        if qualified is None:
            log.warning(f"{contract.symbol} not qualified.")
        else:
            qualified_contracts.append(qualified)

    return qualified_contracts


async def subscribe(ib: IB) -> list[Stock]:
    ib.reqMarketDataType(3)  # set to delayed quote
    log.info("Set to delayed quote.")

    contracts = build_contracts(WATCHLIST)

    subscribed_contracts = await qualify_contracts(ib, contracts)

    log.info(f"Qualified symbols: {[qual.symbol for qual in subscribed_contracts]}")

    tickers = [ib.reqMktData(con) for con in subscribed_contracts]
    log.info(f"Subscribed to {len(tickers)} contracts. Waiting for ticks...")

    return subscribed_contracts


def attach_reconnect_signal(ib: IB) -> asyncio.Event:
    signal = asyncio.Event()

    def on_disconnect_signal() -> None:
        signal.set()

    ib.disconnectedEvent += on_disconnect_signal

    return signal


async def reconnect_loop(
    ib: IB,
    client_id: int,
    signal: asyncio.Event,
    on_reconnect: Callable[[IB], Awaitable[None]],
) -> None:
    while True:
        await signal.wait()
        signal.clear()

        if ib.isConnected():
            log.debug("Disconnect signal ignored. The connection is up.")
            continue

        log.info("Disconnected. Entering the reconnect loop...")
        attempt = 0
        while True:
            delay = min(CAP, BASE * GROWTH_FACTOR**attempt)
            sleep = random.uniform(delay / 2, delay)
            log.info(f"Attempt #{attempt+1}: Retrying after {sleep:.2f} seconds.")
            await asyncio.sleep(sleep)
            try:
                await connect_ib(ib, client_id)
                break
            except (
                ConnectionRefusedError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
            ) as e:
                log.error(repr(e))
            attempt += 1
        try:
            await on_reconnect(ib)
        except Exception:
            log.exception(
                "Recovery after reconnect failed. Redis and the in-memory state may be stale."
            )


def attach_watchdog(ib: IB) -> None:

    probe_task: asyncio.Task | None = None

    def clear_probe(task: asyncio.Task) -> None:
        nonlocal probe_task
        if probe_task is task:
            probe_task = None

        if task.cancelled():
            return

        e = task.exception()
        if e is None:
            return

        log.error(f"The probe raised {e!r}. The watchdog is disarmed.")
        if ib.isConnected():
            log.warning("Forcing a disconnect after a failed probe.")
            ib.disconnect()

    async def probe(idle_period: float) -> None:
        try:
            server_time = await asyncio.wait_for(
                ib.reqCurrentTimeAsync(), PROBE_TIMEOUT
            )
        except (asyncio.TimeoutError, ConnectionError, OSError) as e:
            if not ib.isConnected():
                log.debug("Probe timeout ignored. The connection is already down.")
                return
            log.error(
                f"Probe failed after {idle_period:.1f}s of silence: {e!r}. "
                "The connection is presumed dead."
            )
            log.warning(f"Forcing a disconnect after {idle_period:.1f}s of silence.")
            ib.disconnect()
            return

        log.debug(f"Probe answered. Server time: {server_time}. Re-arming.")
        ib.setTimeout(WATCHDOG_TIMEOUT)

    def on_timeout(idle_period: float) -> None:
        """Timeout event handler."""
        nonlocal probe_task
        log.warning(
            f"Idle period: {idle_period:.1f}s. Timeout exceeded: {WATCHDOG_TIMEOUT}s."
        )

        if not ib.isConnected():
            log.debug("Timeout ignored. The connection is already down.")
            return

        if probe_task is not None and not probe_task.done():
            log.debug("Timeout ignored. A probe is already in flight.")
            return

        probe_task = asyncio.create_task(probe(idle_period))
        probe_task.add_done_callback(clear_probe)

    ib.timeoutEvent += on_timeout
