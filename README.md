# IB Paper Trading Engine
An event-driven IBKR paper-trading engine: streams and logs delayed quotes for a configurable watchlist, submits and reconciles orders, keeps positions, open orders and prices live in Redis, and reconnects and resyncs from IB on its own. Every order passes a fail-closed pre-trade risk gate on position and notional exposure.

## What it does
- **Market data**: Streams and logs delayed quotes for a configurable watchlist.
- **Orders**: Submits market and limit orders, and cancels an open order by id, from a CLI (`scripts/submit_order.py`).
- **Pre-trade risk gate**: Position and notional exposure caps and limits.
- **Real-time state in Redis**: Positions, open orders, account cash, last-sync timestamp and latest prices.
- **Connectivity failure and recovery**: Reconnects by itself. The engine reads positions, open orders and executions from IB again. Then it repairs or deletes stale Redis keys. Subsribes to market data again.


## Prerequisites

### IBKR paper account & Gateway
An IBKR paper account and [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-latest.php) is required.

### Redis
Redis running locally is required. 
```bash
# Via docker
docker run -p 6379:6379 redis

# Via brew
brew install redis && brew services start redis
```

Verifies by
```bash
redis-cli ping # PONG
```
### Python & uv
This project requires python $\geq 3.12$ and uses [uv](https://docs.astral.sh/uv/) as the package manager.

## Setup

### 1. Install dependecies
```bash
uv sync
```

### 2. Configure IB Gateway
- Log in in Paper mode.
- Configure → Settings → API → Settings:
  - Enable ActiveX and Socket Clients
  - Uncheck Read-Only API
  - Socket port 4002 (paper Gateway)
  - Trusted IP 127.0.0.1
  - Master API client ID = 1


### 3. Configure the repo (.env)
```bash
cp .env.example .env
```
| Variable | Default | Description |
|---|---|---|
| `IB_HOST` | `127.0.0.1` | Host running IB Gateway.|
| `IB_PORT` | `4002` | Gateway API socket port; must match the port set in Step 2 |
| `IB_CLIENT_ID` | `1` | Client id the engine connects with. Must equal Gateway's *Master API client ID* from Step 2. |
| `IB_SCRIPT_CLIENT_ID` | `9` | Client id for `scripts.submit_order`. Must differ from `IB_CLIENT_ID` |
| `LOG_LEVEL` | `INFO` | Level for the logger |
| `REDIS_URL` | `redis://localhost:6379/0` | Where the engine writes state. |
| `POSITION_CAP` | `500` | Max absolute projected position per symbol, in units. |
| `NOTIONAL_LIMIT` | `100000` | Max absolute projected notional per symbol, in USD. |
| `PRICE_TTL_SECONDS` | `300` | TTL on each `price:{SYMBOL}` key.|
| `PRICE_PUBLISH_SECONDS` | `5` | How often prices are written to Redis. |
| `PRICE_LOG_SECONDS` | `30` | How often prices are logged. |
| `EXEC_WINDOW_MARGIN` | `60` | Seconds subtracted from `state:last_sync` when asking IB for executions after a reconnect. |
| `EXEC_REQUEST_TIMEOUT` | `10` | Seconds to wait for `reqExecutions` during recovery before giving up on the diff. |

## Running the engine
1. Start IB Gateway.
2. Start the engine.
```bash
uv run python -m engine.main
```
3. Order lifecycle

|Command|Result|
|---|---|
|`uv run python -m scripts.submit_order --help`|Shows the usage.|
|`uv run python -m scripts.submit_order RDDT BUY 10`|Sends a market order.|
|`uv run python -m scripts.submit_order RDDT BUY 10 --limit 150`|Sends a limit order at 150.|
|`uv run python -m scripts.submit_order GOOGL SELL 5`|Sends a market sell order.|
|`uv run python -m scripts.submit_order --cancel 123`|Cancels order 123.|
|`uv run python -m scripts.submit_order UNH BUY 10 --timeout 60`|Waits 60 seconds for a terminal status.|

## Project Structure
```
ib-paper-trading-engine/
├── config.py                 # Settings, global constants
├── engine/
│   ├── main.py               # Entry point; wires connection, handlers, recovery
│   ├── ib_client.py          # IB connection, reconnect, watchdog, price feed
│   ├── orders.py             # Order validation, submission, fill reconciliation
│   ├── risk.py               # Pre-trade risk gates
│   ├── state.py              # Redis reads and writes, resync from IB
│   ├── reconcile.py          # Compares Redis against IB before a resync
│   ├── models.py             # Dataclasses for positions, orders, verdicts, and more
│   └── logging_config.py     # Logging setup
├── scripts/
│   └── submit_order.py       # CLI to submit and cancel orders
├── tests/
│   ├── test_risk.py          # Unit tests for the risk gates
└── .env.example              # Template for the required environment variables
```
