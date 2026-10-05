"""Cross-process file lock used to serialize database migrations.

The bot task and every WSGI worker run init_db() on startup. The lock keeps two
processes from rebuilding the same SQLite table at the same moment.
"""
import logging
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def file_lock(path: str):
    handle = open(path, "a+b")
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        continue
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        except OSError as exc:
            # Some network filesystems refuse locks; migrations still run, just unserialized.
            logger.warning("Could not acquire migration lock %s (%s)", path, type(exc).__name__)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
