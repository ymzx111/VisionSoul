"""Short-lived server-owned snapshots for local Python reruns."""
from collections import OrderedDict
from secrets import token_hex
from threading import Lock
from time import monotonic

LIFETIME_SECONDS = 3600
MAX_SNAPSHOTS = 64
_snapshots: OrderedDict[str, tuple[float, str]] = OrderedDict()
_lock = Lock()


def remember_python(code: str) -> str:
    token = token_hex(16)
    with _lock:
        now = monotonic()
        while _snapshots and (next(iter(_snapshots.values()))[0] <= now or len(_snapshots) >= MAX_SNAPSHOTS):
            _snapshots.popitem(last=False)
        _snapshots[token] = (now + LIFETIME_SECONDS, code)
    return token


def python_snapshot(token: str) -> str | None:
    with _lock:
        entry = _snapshots.get(token)
        if entry is None:
            return None
        if entry[0] <= monotonic():
            _snapshots.pop(token)
            return None
        return entry[1]
