"""Entry point: python -m bot"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from .config import Config, load_config
from .db import DB
from .handlers import admin, panel, user
from .services import Access, Jobs, Orders, Payments
from .solana import PriceFeed, SolanaRPC, is_valid_address

log = logging.getLogger("bot")


def apply_catalog_texts(cfg: Config, data: dict) -> None:
    cfg.project_name = data.get("project_name", cfg.project_name)
    welcome = data.get("welcome_text") or {}
    cfg.welcome_text = welcome if isinstance(welcome, dict) else {"en": welcome}


def build(cfg: Config, bot: Bot, db: DB, rpc: SolanaRPC, prices: PriceFeed):
    access = Access(bot, db, cfg)
    orders = Orders(bot, db, cfg, access)
    payments = Payments(db, cfg, rpc, orders)
    jobs = Jobs(db, cfg, access)
    dp = Dispatcher(storage=MemoryStorage(), db=db, cfg=cfg, access=access, orders=orders, payments=payments,
                    prices=prices)
    dp.include_routers(admin.guard, user.stars_router, panel.router, admin.router, user.router)
    return dp, payments, jobs


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config()
    if cfg.receiver and not is_valid_address(cfg.receiver):
        raise SystemExit("SOLANA_RECEIVER is not a valid Solana address")

    db = DB(cfg.db_path)
    await db.connect()
    catalog = Path(cfg.catalog_file)
    data = json.loads(catalog.read_text(encoding="utf-8")) if catalog.exists() else {}
    apply_catalog_texts(cfg, data)
    if await db.seed_catalog(data, cfg.default_chats):
        log.info("catalog seeded from %s", catalog)
    await panel.load_settings(db, cfg)
    if not cfg.tokens and not cfg.stars_enabled:
        log.warning("No payment method enabled: set SOLANA_RECEIVER and/or STARS_PER_USD")

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    rpc, prices = SolanaRPC(cfg.rpc_url), PriceFeed()
    dp, payments, jobs = build(cfg, bot, db, rpc, prices)

    await bot.set_my_commands([BotCommand(command="start", description="Menu")])
    for aid in cfg.admin_ids:
        try:
            await bot.set_my_commands([BotCommand(command="start", description="Menu"),
                                       BotCommand(command="admin", description="Admin panel")],
                                      scope=BotCommandScopeChat(chat_id=aid))
        except Exception:  # noqa: BLE001 - admin hasn't started the bot yet
            pass
    if cfg.tokens:
        await payments.refresh_accounts(force=True)
    tasks = [asyncio.create_task(payments.run()), asyncio.create_task(jobs.run())]
    me = await bot.me()
    log.info("@%s started; crypto %s; stars %s", me.username, cfg.tokens or "off",
             f"{cfg.stars_per_usd:g}/USD" if cfg.stars_enabled else "off")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in tasks:
            t.cancel()
        await rpc.close()
        await prices.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
