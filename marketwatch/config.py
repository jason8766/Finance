"""Settings: secrets from a .env file, everything else from config/config.yaml."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = Path.home() / "marketwatch-secrets" / ".env"
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yaml"


@dataclass
class Settings:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    watch_perps: list[str] = field(default_factory=list)
    memecoins: list[dict] = field(default_factory=list)
    news_feeds: list[str] = field(default_factory=list)
    calendar_all_impacts: list[str] = field(default_factory=lambda: ["USD"])
    calendar_high_only: list[str] = field(default_factory=list)


def load_settings(env_path: str | None = None, config_path: str | None = None) -> Settings:
    load_dotenv(env_path or os.environ.get("MARKETWATCH_ENV") or DEFAULT_ENV)
    with open(config_path or os.environ.get("MARKETWATCH_CONFIG") or DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f) or {}
    cal = cfg.get("calendar") or {}
    return Settings(
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        llm_model=cfg.get("llm_model") or "claude-opus-5-5",
        watch_perps=[str(s).upper() for s in cfg.get("watch_perps") or []],
        memecoins=cfg.get("memecoins") or [],
        news_feeds=cfg.get("news_feeds") or [],
        calendar_all_impacts=cal.get("countries_all_impacts") or ["USD"],
        calendar_high_only=cal.get("countries_high_only") or [],
    )
