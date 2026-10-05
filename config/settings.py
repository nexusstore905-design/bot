import os

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

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
# Owners: full control. Staff: day-to-day order and customer handling only.
ADMIN_IDS: list[int] = _int_list("ADMIN_IDS")
STAFF_IDS: list[int] = [tid for tid in _int_list("STAFF_IDS") if tid not in ADMIN_IDS]
TEAM_IDS: list[int] = ADMIN_IDS + STAFF_IDS
SUPPLIER_CHAT_ID: int = int(os.getenv("SUPPLIER_CHAT_ID", "0"))
STORE_NAME: str = os.getenv("STORE_NAME", "Nexus Store")
BINANCE_ID: str = os.getenv("BINANCE_ID", "")
MAX_PIN_ATTEMPTS: int = int(os.getenv("MAX_PIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES: int = int(os.getenv("LOCKOUT_MINUTES", "30"))
SESSION_HOURS: int = int(os.getenv("SESSION_HOURS", "0"))
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{os.path.join(BASE_DIR, 'nexus_bot.db')}",
).strip()

# A relative SQLite URL otherwise resolves against the process working directory.
# WSGI and the bot's always-on task can start in different directories, silently
# giving each process a different database. Anchor relative SQLite paths here.
if DATABASE_URL.startswith("sqlite"):
    parsed_database_url = make_url(DATABASE_URL)
    database_path = parsed_database_url.database
    if database_path and database_path != ":memory:" and not os.path.isabs(database_path):
        absolute_database_path = os.path.abspath(os.path.join(BASE_DIR, database_path))
        DATABASE_URL = parsed_database_url.set(
            database=absolute_database_path
        ).render_as_string(hide_password=False)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# PythonAnywhere keeps files on network storage, where SQLite WAL mode is unsafe.
# Leave DELETE unless the database lives on a local disk.
SQLITE_JOURNAL_MODE: str = os.getenv("SQLITE_JOURNAL_MODE", "DELETE").strip().upper() or "DELETE"

# Minutes a supplier group has to act on an order part after it was delivered.
SUPPLIER_TIMEOUT_MINUTES: int = max(1, int(os.getenv("SUPPLIER_TIMEOUT_MINUTES", "10")))

# Daily database backups kept in BACKUP_DIR by the bot task.
BACKUP_DIR: str = os.getenv("BACKUP_DIR", os.path.join(BASE_DIR, "backups"))
BACKUP_KEEP_DAYS: int = max(1, int(os.getenv("BACKUP_KEEP_DAYS", "7")))

# REST API
API_RATE_LIMIT_PER_MINUTE: int = max(1, int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "60")))
API_KEY: str = os.getenv("API_KEY", "").strip()
if API_KEY == "change-this-to-a-secret-key":
    API_KEY = ""
API_CORS_ORIGINS: set[str] = {
    origin.strip()
    for origin in os.getenv("API_CORS_ORIGINS", "").split(",")
    if origin.strip()
}
