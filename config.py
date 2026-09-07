import os

from dotenv import load_dotenv

load_dotenv()

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4002"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
IB_SCRIPT_CLIENT_ID = int(os.getenv("IB_SCRIPT_CLIENT_ID", "9"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

DB_PATH = os.getenv("DB_PATH", "data/risk_monitor.db")

WATCHLIST = ["SPYM", "RDDT", "GOOGL", "JPM"]

BASE_CURRENCY = "USD"

# Connectivity recovery variables
CAP = 30
GROWTH_FACTOR = 2
BASE = 1
WATCHDOG_TIMEOUT = 60
PROBE_TIMEOUT = 5

# Execution-window resync
EXEC_WINDOW_MARGIN = int(os.getenv("EXEC_WINDOW_MARGIN", "60"))
EXEC_REQUEST_TIMEOUT = float(os.getenv("EXEC_REQUEST_TIMEOUT", "10"))

# Risk checking variables
POSITION_CAP = int(os.getenv("POSITION_CAP", 500))
NOTIONAL_LIMIT = float(os.getenv("NOTIONAL_LIMIT", 100000))
PRICE_TTL_SECONDS = int(os.getenv("PRICE_TTL_SECONDS", 300))
PRICE_PUBLISH_SECONDS = int(os.getenv("PRICE_PUBLISH_SECONDS", "5"))
PRICE_LOG_SECONDS = int(os.getenv("PRICE_LOG_SECONDS", "30"))
