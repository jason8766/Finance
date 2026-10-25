"""Claude writes the judgement sections of the brief (news, focus, risks).

Numbers and times are rendered by code, never by the model, so they can't be
misquoted. The model returns structured JSON that the renderer escapes.
"""

import json
import logging

import anthropic

log = logging.getLogger(__name__)

# Models that accept the server-side refusal fallback parameter.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

SYSTEM = """You write the judgement sections of a daily 09:00 KST market brief for a crypto trader \
who trades Binance USDⓈ-M futures (majors, alts, and stock/commodity perps) and Solana memecoins.

You receive JSON with the last 24h of headlines, the economic calendar, and market data.

Rules:
- Use only the information provided. Never invent events, numbers, quotes or sources.
- "yesterday": 3-6 items, most market-relevant first. Prefer macro data, central banks, \
politics/geopolitics, tariffs, regulation, ETF flows, exchange/hack/depeg incidents, large \
liquidations, and big protocol or listing news. Skip generic price-recap and opinion pieces. \
Merge duplicate stories. headline <= 15 words; why_it_matters <= 20 words and says how it \
could affect crypto prices or volatility.
- "today_focus": 1-2 sentences on what matters most in the next 24h given the calendar \
(times are KST). If nothing major is scheduled, say so plainly.
- "risk_flags": 0-4 short items for unusual conditions in the data (extreme funding, crowded \
positioning, sharp sentiment shifts, event risk). Empty list if nothing stands out.
- Plain text only, no markdown or HTML. English."""

SCHEMA = {
    "type": "object",
    "properties": {
        "yesterday": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                },
                "required": ["headline", "why_it_matters"],
                "additionalProperties": False,
            },
        },
        "today_focus": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["yesterday", "today_focus", "risk_flags"],
    "additionalProperties": False,
}


def write_sections(data: dict, api_key: str, model: str) -> dict | None:
    """Returns {"yesterday": [...], "today_focus": str, "risk_flags": [...]} or None on failure."""
    if not api_key:
        log.info("ANTHROPIC_API_KEY not set; brief will use headlines without commentary")
        return None

    client = anthropic.Anthropic(api_key=api_key, timeout=180.0)
    params = dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(data, default=str)}],
    )
    if model in FALLBACK_MODELS:
        params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")

    try:
        response = client.beta.messages.create(**params)
    except anthropic.APIConnectionError as e:
        log.error("Claude API unreachable: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.error("Claude API error %s: %s", e.status_code, e.message)
        return None

    if response.stop_reason == "refusal":
        log.error("Claude declined the request: %s", response.stop_details)
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        log.error("Claude returned no text (stop_reason=%s)", response.stop_reason)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.error("Claude returned invalid JSON (stop_reason=%s)", response.stop_reason)
        return None
