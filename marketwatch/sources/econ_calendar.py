"""Economic calendar (CPI, PPI, FOMC, NFP, ...) from the Forex Factory weekly feed."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .http import client

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
# The feed rate-limits aggressively (429), so download at most once per hour and
# fall back to the last good copy when it refuses.
CACHE_TTL = timedelta(hours=1)
KEEP_PAST = timedelta(days=2)


def default_cache_path() -> Path:
    base = os.environ.get("MARKETWATCH_CACHE") or Path.home() / ".cache" / "marketwatch"
    return Path(base) / "calendar.json"


def fetch_events(cache_path: Path | None = None, now: datetime | None = None) -> list[dict]:
    path = cache_path or default_cache_path()
    now = now or datetime.now(timezone.utc)
    cached = _read_cache(path)
    if cached and now - cached["fetched_at"] < CACHE_TTL:
        return cached["events"]

    fresh = _download()
    if fresh is None:
        if cached:
            log.warning("calendar feed unavailable; using copy from %s", cached["fetched_at"].isoformat())
            return cached["events"]
        raise RuntimeError("calendar feed unavailable and no cached copy")

    events = merge(cached["events"] if cached else [], fresh, now)
    _write_cache(path, now, events)
    return events


def _download() -> list[dict] | None:
    """This week's events (required) plus next week's when published (optional)."""
    with client() as c:
        try:
            events = c.get(THIS_WEEK).raise_for_status().json()
        except httpx.HTTPError as e:
            log.warning("calendar feed unavailable: %s", e)
            return None
        try:
            events += c.get(NEXT_WEEK).raise_for_status().json()
        except httpx.HTTPError as e:
            # Next week's feed is often not published until late in the week.
            log.info("next-week calendar not available yet: %s", e)
    return events


def merge(old: list[dict], fresh: list[dict], now: datetime) -> list[dict]:
    """Fresh rows win; cached rows survive (e.g. last week's feed) until 2 days past."""
    by_key = {}
    for row in old + fresh:
        try:
            when = datetime.fromisoformat(row["date"])
        except (KeyError, ValueError):
            continue
        if when >= now - KEEP_PAST:
            by_key[(row.get("title"), row.get("country"), row["date"])] = row
    return list(by_key.values())


def _read_cache(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text())
        return {"fetched_at": datetime.fromisoformat(raw["fetched_at"]), "events": raw["events"]}
    except (OSError, ValueError, KeyError):
        return None


def _write_cache(path: Path, now: datetime, events: list[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": now.isoformat(), "events": events}))
    except OSError as e:
        log.warning("could not write calendar cache %s: %s", path, e)


def select(events: list[dict], all_impacts: list[str], high_only: list[str]) -> list[dict]:
    """Keep USD High/Medium and other majors' High-impact events, as KST-timed dicts."""
    out = []
    for e in events:
        country, impact = e.get("country"), e.get("impact")
        keep = (country in all_impacts and impact in ("High", "Medium")) or (
            country in high_only and impact == "High"
        )
        if not keep:
            continue
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(KST)
        except (KeyError, ValueError):
            continue
        out.append({
            "time_kst": when,
            "country": country,
            "title": e.get("title", ""),
            "impact": impact,
            "forecast": e.get("forecast") or None,
            "previous": e.get("previous") or None,
        })
    return sorted(out, key=lambda x: x["time_kst"])


def split_today_week(events: list[dict], now: datetime) -> tuple[list[dict], list[dict]]:
    """Today = next 24h from the brief; week ahead = the 6 days after that, High impact only."""
    day_end = now + timedelta(hours=24)
    week_end = now + timedelta(days=7)
    today = [e for e in events if now <= e["time_kst"] < day_end]
    week = [e for e in events if day_end <= e["time_kst"] < week_end and e["impact"] == "High"]
    return today, week
