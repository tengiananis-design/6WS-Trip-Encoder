import os
import threading
from contextlib import contextmanager

_LOCAL_LOCKS = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


@contextmanager
def approval_lock(batch_id: str):
    """Prevent concurrent exports of the same batch across web instances."""
    redis_url = os.getenv("REDIS_URL")
    lock = None
    local_lock = None

    if redis_url:
        import redis
        client = redis.Redis.from_url(redis_url)
        lock = client.lock(
            f"6ws:approval:{batch_id}",
            timeout=60 * 60,
            blocking=False,
        )
        if not lock.acquire(blocking=False):
            raise RuntimeError("This batch is already being approved or exported.")
    else:
        with _LOCAL_LOCKS_GUARD:
            local_lock = _LOCAL_LOCKS.setdefault(batch_id, threading.Lock())
        if not local_lock.acquire(blocking=False):
            raise RuntimeError("This batch is already being approved or exported.")

    try:
        yield
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass
        elif local_lock is not None:
            local_lock.release()
