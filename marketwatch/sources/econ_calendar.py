"""Economic calendar (CPI, PPI, FOMC, NFP, ...) from the Forex Factory weekly feed."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from .http import client

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
FEEDS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)


def fetch_events() -> list[dict]:
    events, seen = [], set()
    with client() as c:
        for url in FEEDS:
            try:
                rows = c.get(url).raise_for_status().json()
            except httpx.HTTPError as e:
                # Next week's feed is often not published until late in the week.
                log.warning("calendar feed %s unavailable: %s", url, e)
                continue
            for row in rows:
                key = (row.get("title"), row.get("country"), row.get("date"))
                if key not in seen:
                    seen.add(key)
                    events.append(row)
    return events


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
