"""Market-wide context: Fear & Greed index and global crypto market stats."""

from .http import get_json


def fear_greed() -> dict:
    data = get_json("https://api.alternative.me/fng/", params={"limit": 2})["data"]
    today, prev = data[0], data[1] if len(data) > 1 else None
    return {
        "value": int(today["value"]),
        "label": today["value_classification"],
        "previous": int(prev["value"]) if prev else None,
    }


def global_market() -> dict:
    d = get_json("https://api.coingecko.com/api/v3/global")["data"]
    return {
        "btc_dominance_pct": d["market_cap_percentage"]["btc"],
        "total_market_cap_usd": d["total_market_cap"]["usd"],
        "market_cap_change_24h_pct": d["market_cap_change_percentage_24h_usd"],
    }
