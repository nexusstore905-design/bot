import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(BASE_DIR, '.env')
load_dotenv(env_path)


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


def _int_list(key: str, default: str = "") -> list[int]:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


BOT_TOKEN: str = _require("BOT_TOKEN")
ADMIN_IDS: list[int] = _int_list("ADMIN_IDS")
SUPPLIER_CHAT_ID: int = int(os.getenv("SUPPLIER_CHAT_ID", "0"))
STORE_NAME: str = os.getenv("STORE_NAME", "Nexus Store")
CURRENCY: str = os.getenv("CURRENCY", "USDT")
BINANCE_ID: str = os.getenv("BINANCE_ID", "")
MAX_PIN_ATTEMPTS: int = int(os.getenv("MAX_PIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES: int = int(os.getenv("LOCKOUT_MINUTES", "30"))
SESSION_HOURS: int = int(os.getenv("SESSION_HOURS", "0"))
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{os.path.join(BASE_DIR, 'nexus_bot.db')}")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# REST API
API_KEY: str = os.getenv("API_KEY", "change-this-to-a-secret-key")
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))
