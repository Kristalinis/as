"""Entry point: python -m bot"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import load_config
from .db import DB
from .handlers import admin, user
from .services import Access, Jobs, Payments
from .solana import PriceFeed, SolanaRPC, is_valid_address


def build(cfg, bot: Bot, db: DB, rpc: SolanaRPC, prices: PriceFeed):
    access = Access(bot, db, cfg)
    payments = Payments(bot, db, cfg, rpc, access)
    jobs = Jobs(db, access)
    dp = Dispatcher(storage=MemoryStorage(), db=db, cfg=cfg, access=access, payments=payments, prices=prices)
    dp.include_routers(admin.guard, admin.router, user.router)
    return dp, payments, jobs


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config()
    if not is_valid_address(cfg.receiver):
        raise SystemExit("SOLANA_RECEIVER is not a valid Solana address")
    if not cfg.premium_chats:
        logging.warning("No PREMIUM_CHAT_IDS configured — payments work but no chat access will be granted")

    db = DB(cfg.db_path)
    await db.connect()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    rpc, prices = SolanaRPC(cfg.rpc_url), PriceFeed()
    dp, payments, jobs = build(cfg, bot, db, rpc, prices)

    await bot.set_my_commands([BotCommand(command="start", description="Main menu")])
    await payments.refresh_accounts(force=True)
    tasks = [asyncio.create_task(payments.run()), asyncio.create_task(jobs.run())]
    me = await bot.me()
    logging.info("@%s started; receiver %s; tokens %s; chats %s", me.username, cfg.receiver, cfg.tokens,
                 cfg.premium_chats)
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
