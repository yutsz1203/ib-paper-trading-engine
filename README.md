# IB Paper Trading Engine
A Python trading engine for an Interactive Brokers paper account. It sends market orders and limit orders to IB Gateway, and it applies a position cap and a notional cap for each symbol first. Redis holds the live state. When the engine loses the connection, it reconnects by itself and corrects that state against the record of IB.

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