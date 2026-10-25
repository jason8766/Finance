"""Recent headlines from RSS feeds."""

import calendar
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import httpx

from .http import client

log = logging.getLogger(__name__)


def fetch_headlines(feeds: list[str], since: datetime, limit: int = 80) -> list[dict]:
    items, seen = [], set()
    with client(timeout=15) as c:
        for url in feeds:
            try:
                parsed = feedparser.parse(c.get(url).raise_for_status().content)
            except httpx.HTTPError as e:
                log.warning("news feed %s unavailable: %s", url, e)
                continue
            source = urlparse(url).netloc.removeprefix("www.").removeprefix("feeds.")
            for entry in parsed.entries:
                published = entry_time(entry)
                title = (entry.get("title") or "").strip()
                if not title or published is None or published < since:
                    continue
                key = normalize(title)
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "title": title,
                    "source": source,
                    "published_utc": published.isoformat(timespec="minutes"),
                    "summary": clean(entry.get("summary", ""))[:280],
                })
    items.sort(key=lambda i: i["published_utc"], reverse=True)
    return items[:limit]


def entry_time(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None


def normalize(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def last_24h() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=24)
