"""Configuration loaded from environment (.env) and plans.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LIFETIME_TS = 4102444800  # 2100-01-01, used as "never expires"

# Mainnet SPL mints (6 decimals each).
KNOWN_MINTS = {
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
TOKEN_DECIMALS = {"SOL": 9, "USDC": 6, "USDT": 6}


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    days: int  # 0 = lifetime
    price_usd: float
    description: str = ""

    @property
    def lifetime(self) -> bool:
        return self.days <= 0

    @property
    def duration_text(self) -> str:
        return "lifetime" if self.lifetime else f"{self.days} days"


@dataclass
class Config:
    bot_token: str
    admin_ids: set[int]
    receiver: str
    rpc_url: str
    tokens: list[str]
    premium_chats: list[int]
    plans: list[Plan]
    project_name: str = "Premium Club"
    welcome_text: str = ""
    support: str = ""
    db_path: str = "data/bot.db"
    invoice_ttl_min: int = 30
    trial_days: int = 0
    referral_percent: float = 0.0
    referral_bonus_days: int = 0
    min_payout_usd: float = 20.0
    strict_membership: bool = True
    poll_interval: int = 15
    mints: dict[str, str] = field(default_factory=lambda: dict(KNOWN_MINTS))

    def plan(self, plan_id: str) -> Plan | None:
        return next((p for p in self.plans if p.id == plan_id), None)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def _ints(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def load_config() -> Config:
    missing = [k for k in ("BOT_TOKEN", "SOLANA_RECEIVER", "ADMIN_IDS") if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)} (see .env.example)")

    plans_path = Path(os.getenv("PLANS_FILE", "plans.json"))
    data = json.loads(plans_path.read_text(encoding="utf-8"))
    plans = [
        Plan(
            id=str(p["id"]),
            title=p["title"],
            days=int(p.get("days", 0)),
            price_usd=float(p["price_usd"]),
            description=p.get("description", ""),
        )
        for p in data["plans"]
    ]
    if not plans:
        raise SystemExit("plans.json has no plans")

    tokens = [t.strip().upper() for t in os.getenv("ACCEPT_TOKENS", "SOL,USDC").split(",") if t.strip()]
    bad = [t for t in tokens if t not in TOKEN_DECIMALS]
    if bad:
        raise SystemExit(f"Unsupported tokens in ACCEPT_TOKENS: {bad}. Use SOL, USDC, USDT.")

    chats = _ints(os.getenv("PREMIUM_CHAT_IDS", "")) or [int(c) for c in data.get("chats", [])]

    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        admin_ids=set(_ints(os.environ["ADMIN_IDS"])),
        receiver=os.environ["SOLANA_RECEIVER"].strip(),
        rpc_url=os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),
        tokens=tokens,
        premium_chats=chats,
        plans=plans,
        project_name=data.get("project_name", "Premium Club"),
        welcome_text=data.get("welcome_text", ""),
        support=os.getenv("SUPPORT_CONTACT", ""),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        invoice_ttl_min=int(os.getenv("INVOICE_TTL_MINUTES", "30")),
        trial_days=int(os.getenv("TRIAL_DAYS", "0")),
        referral_percent=float(os.getenv("REFERRAL_PERCENT", "0")),
        referral_bonus_days=int(os.getenv("REFERRAL_BONUS_DAYS", "0")),
        min_payout_usd=float(os.getenv("MIN_PAYOUT_USD", "20")),
        strict_membership=os.getenv("STRICT_MEMBERSHIP", "true").lower() in ("1", "true", "yes"),
        poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "15")),
    )
