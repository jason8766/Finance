"""Solana memecoin prices from DexScreener (free, no key)."""

from .http import get_json

URL = "https://api.dexscreener.com/latest/dex/tokens/{}"


def fetch_tokens(memecoins: list[dict]) -> list[dict]:
    if not memecoins:
        return []
    mints = [m["mint"] for m in memecoins]
    pairs = get_json(URL.format(",".join(mints))).get("pairs") or []
    return [best_pair(m, pairs) for m in memecoins]


def best_pair(coin: dict, pairs: list[dict]) -> dict:
    """The deepest-liquidity Solana pair where this mint is the base token."""
    candidates = [
        p for p in pairs
        if p.get("chainId") == "solana" and p.get("baseToken", {}).get("address") == coin["mint"]
    ]
    if not candidates:
        return {"symbol": coin["symbol"], "mint": coin["mint"], "found": False}
    p = max(candidates, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    change = p.get("priceChange") or {}
    return {
        "symbol": coin["symbol"],
        "mint": coin["mint"],
        "found": True,
        "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
        "change_1h_pct": change.get("h1"),
        "change_24h_pct": change.get("h24"),
        "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
        "volume_24h_usd": (p.get("volume") or {}).get("h24"),
        "market_cap_usd": p.get("marketCap") or p.get("fdv"),
        "url": p.get("url"),
    }
