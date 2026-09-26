"""Configuration loaded from environment (.env). The product catalog lives in the DB
(seeded from catalog.json on first start, then editable from the admin panel)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

LIFETIME_TS = 4102444800  # 2100-01-01, used as "never expires"

# Mainnet SPL mints (6 decimals each).
KNOWN_MINTS = {
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
TOKEN_DECIMALS = {"SOL": 9, "USDC": 6, "USDT": 6, "XTR": 0}

# Settings an admin can change at runtime from the panel (name -> (type, label)).
EDITABLE = {
    "trial_days": (int, "Free trial days (0 = off)"),
    "referral_percent": (float, "Referral commission %"),
    "referral_bonus_days": (int, "Referral bonus days"),
    "winback_percent": (int, "Win-back discount % (0 = off)"),
    "winback_valid_days": (int, "Win-back offer valid days"),
    "stars_per_usd": (float, "Telegram Stars per $1 (0 = Stars off)"),
    "upsell": (int, "Upsell longer plans (1 = on, 0 = off)"),
}


@dataclass
class Config:
    bot_token: str
    admin_ids: set[int]
    receiver: str
    rpc_url: str
    tokens: list[str]
    default_chats: list[int] = field(default_factory=list)
    project_name: str = "Premium Club"
    welcome_text: dict[str, str] = field(default_factory=dict)
    support: str = ""
    db_path: str = "data/bot.db"
    catalog_file: str = "catalog.json"
    invoice_ttl_min: int = 30
    trial_days: int = 0
    referral_percent: float = 0.0
    referral_bonus_days: int = 0
    min_payout_usd: float = 20.0
    winback_percent: int = 20
    winback_valid_days: int = 3
    stars_per_usd: float = 0.0
    upsell: int = 1
    strict_membership: bool = True
    poll_interval: int = 15
    mints: dict[str, str] = field(default_factory=lambda: dict(KNOWN_MINTS))

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    @property
    def stars_enabled(self) -> bool:
        return self.stars_per_usd > 0


def _ints(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def load_config() -> Config:
    missing = [k for k in ("BOT_TOKEN", "ADMIN_IDS") if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)} (see .env.example)")

    tokens = [t.strip().upper() for t in os.getenv("ACCEPT_TOKENS", "SOL,USDC").split(",") if t.strip()]
    bad = [t for t in tokens if t not in ("SOL", "USDC", "USDT")]
    if bad:
        raise SystemExit(f"Unsupported tokens in ACCEPT_TOKENS: {bad}. Use SOL, USDC, USDT.")
    receiver = os.getenv("SOLANA_RECEIVER", "").strip()
    if not receiver:
        tokens = []  # crypto off; Stars only

    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        admin_ids=set(_ints(os.environ["ADMIN_IDS"])),
        receiver=receiver,
        rpc_url=os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),
        tokens=tokens,
        default_chats=_ints(os.getenv("PREMIUM_CHAT_IDS", "")),
        support=os.getenv("SUPPORT_CONTACT", ""),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        catalog_file=os.getenv("CATALOG_FILE", "catalog.json"),
        invoice_ttl_min=int(os.getenv("INVOICE_TTL_MINUTES", "30")),
        trial_days=int(os.getenv("TRIAL_DAYS", "0")),
        referral_percent=float(os.getenv("REFERRAL_PERCENT", "0")),
        referral_bonus_days=int(os.getenv("REFERRAL_BONUS_DAYS", "0")),
        min_payout_usd=float(os.getenv("MIN_PAYOUT_USD", "20")),
        winback_percent=int(os.getenv("WINBACK_PERCENT", "20")),
        winback_valid_days=int(os.getenv("WINBACK_VALID_DAYS", "3")),
        stars_per_usd=float(os.getenv("STARS_PER_USD", "50")),
        upsell=int(os.getenv("UPSELL", "1")),
        strict_membership=os.getenv("STRICT_MEMBERSHIP", "true").lower() in ("1", "true", "yes"),
        poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "15")),
    )
