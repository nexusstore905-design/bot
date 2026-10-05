"""Consistent SQLite snapshots with simple rotation."""
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PREFIX = "nexus_bot-"
SUFFIX = ".db"


def backup_sqlite(database_path: str, backup_dir: str, keep: int) -> str:
    """Copy the live database with SQLite's online backup API and keep the newest `keep` files."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = os.path.join(backup_dir, f"{PREFIX}{stamp}{SUFFIX}")
    source = sqlite3.connect(database_path, timeout=30)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    backups = sorted(
        name for name in os.listdir(backup_dir) if name.startswith(PREFIX) and name.endswith(SUFFIX)
    )
    for old in backups[:-keep]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError as exc:
            logger.warning("Could not remove old backup %s (%s)", old, type(exc).__name__)
    return target
