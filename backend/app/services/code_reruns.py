"""Short-lived server-owned snapshots for local sandbox reruns."""
from collections import OrderedDict
from pathlib import Path
from secrets import token_hex
from threading import Lock
from time import monotonic

from app.services.file_row_check import FileRowCheck

LIFETIME_SECONDS = 3600
MAX_SNAPSHOTS = 64
_snapshots: OrderedDict[str, tuple[float, str, Path, str, bool, str | None, FileRowCheck | None]] = OrderedDict()
_lock = Lock()


def remember_source(code: str, source: Path, language: str, requires_file: bool = False,
                    output_name: str | None = None, row_check: FileRowCheck | None = None) -> str:
    token = token_hex(16)
    with _lock:
        now = monotonic()
        while _snapshots and (next(iter(_snapshots.values()))[0] <= now or len(_snapshots) >= MAX_SNAPSHOTS):
            _snapshots.popitem(last=False)
        _snapshots[token] = (now + LIFETIME_SECONDS, code, source, language, requires_file, output_name, row_check)
    return token


def source_snapshot(token: str) -> tuple[str, Path, str, bool, str | None, FileRowCheck | None] | None:
    with _lock:
        entry = _snapshots.get(token)
        if entry is None:
            return None
        if entry[0] <= monotonic():
            _snapshots.pop(token)
            return None
        return entry[1:]
