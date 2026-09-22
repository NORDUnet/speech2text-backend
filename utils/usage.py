"""Bounded anonymous counters: no identifiers or individual event records."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from threading import Lock

UI_METRICS = frozenset(
    [f"upload.batch.{n}" for n in range(1, 6)]
    + ["preview.enabled", "confidence.adjusted", "confidence.listen", "subtitles.checked"]
    + [f"export.subtitles.{f}" for f in ("srt", "vtt")]
    + [f"export.transcript.{f}" for f in ("txt", "json", "rtf", "csv", "tsv")]
)
METRICS = UI_METRICS | frozenset([
    "queued.transcript", "queued.subtitles", "group.created", "group.member_added",
    "group.limit_blocked", "provision.matched", "provision.changed",
    *[f"upload.size.{n}" for n in range(5)],
])
_pending = Counter()
_lock = Lock()


def record(metric, count=1):
    if metric not in METRICS or type(count) is not int or not 0 < count <= 10000:
        return
    today = datetime.now(timezone.utc).date()
    week = today - timedelta(days=today.weekday())
    with _lock:
        # Keep at most 8 weeks if storage is unavailable for a long time.
        for key in list(_pending):
            if key[0] < week - timedelta(weeks=7):
                del _pending[key]
        _pending[(week, metric)] += count


def drain():
    with _lock:
        batch = dict(_pending)
        _pending.clear()
    return batch


def size_metric(size):
    for index, limit in enumerate((100 * 1024**2, 500 * 1024**2, 1024**3, 2 * 1024**3)):
        if size < limit:
            return f"upload.size.{index}"
    return "upload.size.4"
