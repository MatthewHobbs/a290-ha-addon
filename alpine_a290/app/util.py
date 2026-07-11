"""Shared pure primitives for the Alpine A290 add-on.

A leaf seam alongside config: it imports nothing internal, so any module (main, charge, mqtt)
can depend on it without a cycle. Deliberately scoped to genuinely-shared, side-effect-free
helpers — the wall clock (`now_ts`), epoch→ISO formatting (`iso`), and numeric coercion
(`_num`) — used by both the poll loop and the charge-session reconciliation. Not a junk drawer:
main-only helpers (unit conversion, schedule formatting) stay in main.
"""
import time
from datetime import datetime, timezone


def now_ts():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None
