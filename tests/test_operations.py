"""Backups and the hourly maintenance job."""
import os
import sqlite3

from bot import workers
from conftest import TEST_DB, FakeBot, run, sql
from utils.backup import backup_sqlite


def test_backup_is_a_readable_copy_and_old_ones_rotate(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for day in range(1, 6):
        (backup_dir / f"nexus_bot-2026010{day}-000000.db").write_bytes(b"old")

    path = backup_sqlite(str(TEST_DB), str(backup_dir), keep=3)

    remaining = sorted(os.listdir(backup_dir))
    assert len(remaining) == 3 and os.path.basename(path) in remaining
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    conn.close()


def test_daily_maintenance_backs_up_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "BACKUP_DIR", str(tmp_path))
    run(workers.daily_maintenance(FakeBot()))
    run(workers.daily_maintenance(FakeBot()))  # within 24h: skipped
    assert len([name for name in os.listdir(tmp_path) if name.endswith(".db")]) == 1
    assert sql("SELECT COUNT(*) FROM app_settings WHERE key = 'last_backup'") == [(1,)]
