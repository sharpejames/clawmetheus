"""
ui_cache.py — Persistent cache of known UI element positions.

Websites and apps change over time, so cached coords may go stale.
Always treat as a hint, not ground truth. Verify when things break.
"""
import json, os, time
from typing import Optional

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'ui_cache.json')
STALE_DAYS = 14


def _load() -> dict:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {"version": 1, "entries": {}}


def _save(data: dict):
    with open(CACHE_PATH, 'w') as f:
        json.dump(data, f, indent=2)


def get(app_key: str, element: str) -> Optional[dict]:
    """Get cached element. Returns None if not found. Sets 'stale': True if old."""
    entry = _load().get("entries", {}).get(app_key, {}).get(element)
    if not entry:
        return None
    try:
        from datetime import datetime, date
        age = (date.today() - datetime.strptime(entry["verified"], "%Y-%m-%d").date()).days
        if age > STALE_DAYS:
            entry = dict(entry, stale=True, age_days=age)
    except Exception:
        pass
    return entry


def put(app_key: str, element: str, coords: list, notes: str = ""):
    """Store or update a cached element position."""
    data = _load()
    data.setdefault("entries", {}).setdefault(app_key, {})[element] = {
        "coords": list(coords),
        "verified": time.strftime("%Y-%m-%d"),
        "notes": notes,
    }
    _save(data)
    print(f"  [ui_cache] saved {app_key}/{element} → {coords}")


def coords(app_key: str, element: str) -> Optional[list]:
    """Shorthand — returns just the coords list or None."""
    entry = get(app_key, element)
    if entry:
        if entry.get("stale"):
            print(f"  [ui_cache] WARNING: {app_key}/{element} is {entry.get('age_days', '?')} days old — may be stale")
        return entry["coords"]
    return None
