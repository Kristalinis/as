"""Payment watcher, access control and background jobs."""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import LIFETIME_TS, TOKEN_DECIMALS, Config
from .db import DB, MATCH_WINDOW, now
from .solana import RPCError, SolanaRPC, has_reference, received_amount, units_to_str

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)


def fmt_date(ts: int) -> str:
    if ts >= LIFETIME_TS:
        return "never (lifetime)"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def fmt_amount(units: int, token: str) -> str:
    return f"{units_to_str(units, TOKEN_DECIMALS[token])} {token}"


def renew_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Renew subscription", callback_data="plans")
    return kb.as_markup()


class Access:
    """Grants / revokes access to the premium chats."""

    def __init__(self, bot: Bot, db: DB, cfg: Config):
        self.bot, self.db, self.cfg = bot, db, cfg
        self._titles: dict[int, str] = {}

    async def title(self, chat_id: int) -> str:
        if chat_id not in self._titles:
            try:
                self._titles[chat_id] = (await self.bot.get_chat(chat_id)).title or str(chat_id)
            except TelegramAPIError:
                return str(chat_id)
        return self._titles[chat_id]

    async def invite_links(self, uid: int) -> list[tuple[str, str]]:
        """Fresh single-use links (valid 24h) for every premium chat."""
        links = []
        for chat_id in self.cfg.premium_chats:
            try:
                await self.bot.unban_chat_member(chat_id, uid, only_if_banned=True)
                link = await self.bot.create_chat_invite_link(
                    chat_id, name=f"user {uid}"[:32], expire_date=now() + 86400, member_limit=1
                )
                links.append((await self.title(chat_id), link.invite_link))
            except TelegramAPIError as e:
                log.error("cannot create invite link for %s: %s", chat_id, e)
                await self.notify_admins(
                    f"⚠️ Could not create an invite link for chat <code>{chat_id}</code>: {html.escape(str(e))}\n"
                    "Make sure the bot is an admin there with “Invite users” and “Ban users” rights."
                )
        await self.db.touch_link(uid)
        return links

    async def send_access(self, uid: int, header: str) -> None:
        links = await self.invite_links(uid)
        sub = await self.db.get_sub(uid)
        text = header + f"\n\n⏳ Access until: <b>{fmt_date(sub['expires_at'])}</b>"
        kb = InlineKeyboardBuilder()
        for title, url in links:
            kb.button(text=f"➡️ Join {title}", url=url)
        kb.adjust(1)
        if links:
            text += "\n\nYour personal one-time invite links (valid 24h) are below 👇"
        await self.safe_send(uid, text, reply_markup=kb.as_markup())

    async def is_chat_admin(self, chat_id: int, uid: int) -> bool:
        try:
            m = await self.bot.get_chat_member(chat_id, uid)
        except TelegramAPIError:
            return False
        return m.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)

    async def remove(self, uid: int) -> None:
        if self.cfg.is_admin(uid):
            return
        for chat_id in self.cfg.premium_chats:
            try:
                if await self.is_chat_admin(chat_id, uid):
                    continue
                await self.bot.ban_chat_member(chat_id, uid)
                await self.bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            except TelegramAPIError as e:
                log.warning("cannot remove %s from %s: %s", uid, chat_id, e)

    async def safe_send(self, uid: int, text: str, **kw) -> bool:
        try:
            await self.bot.send_message(uid, text, **kw)
            return True
        except TelegramForbiddenError:
            await self.db.set_blocked(uid)
        except TelegramAPIError as e:
            log.warning("send to %s failed: %s", uid, e)
        return False

    async def notify_admins(self, text: str) -> None:
        for aid in self.cfg.admin_ids:
            try:
                await self.bot.send_message(aid, text)
            except TelegramAPIError:
                pass


class Payments:
    """Detects on-chain payments and fulfils invoices.

    Two independent detection paths (deduplicated by tx signature):
      1. Solana Pay reference lookup per pending invoice (wallet payments).
      2. Scan of every incoming tx to the receiver wallet / its token accounts, matched by the
         invoice's unique exact amount (works for exchange withdrawals without a reference).
    """

    def __init__(self, bot: Bot, db: DB, cfg: Config, rpc: SolanaRPC, access: Access):
        self.bot, self.db, self.cfg, self.rpc, self.access = bot, db, cfg, rpc, access
        self.watched: dict[str, list[str]] = {"SOL": [cfg.receiver]} if "SOL" in cfg.tokens else {}
        self._accounts_ts = 0.0
        self._lock = asyncio.Lock()

    def mint(self, token: str) -> str | None:
        return None if token == "SOL" else self.cfg.mints[token]

    async def refresh_accounts(self, force: bool = False) -> None:
        if not force and time.time() - self._accounts_ts < 600:
            return
        for token in self.cfg.tokens:
            if token == "SOL":
                continue
            try:
                accs = await self.rpc.token_accounts(self.cfg.receiver, self.cfg.mints[token])
            except RPCError as e:
                log.warning("token account lookup for %s failed: %s", token, e)
                continue
            if not accs:
                log.warning("receiver has no %s token account yet — create one so wallets can pay", token)
            self.watched[token] = accs
        self._accounts_ts = time.time()

    # ------------------------------------------------------------ matching
    def match(self, tx: dict, token: str, pending: list[aiosqlite.Row]) -> aiosqlite.Row | None:
        got = received_amount(tx, self.cfg.receiver, self.mint(token))
        if got <= 0:
            return None
        bt = tx.get("blockTime") or now()
        cands = [i for i in pending if i["token"] == token and i["created_at"] - 120 <= bt]
        for inv in cands:
            if has_reference(tx, inv["reference"]) and got >= inv["amount_units"]:
                return inv
        for inv in cands:
            if got == inv["amount_units"]:
                return inv
        return None

    async def _new_signatures(self, addr: str) -> list[dict]:
        key = f"cursor:{addr}"
        until = await self.db.kv_get(key)
        sigs: list[dict] = []
        before = None
        for _ in range(10 if until else 1):
            page = await self.rpc.signatures(addr, limit=100 if until else 25, until=until, before=before)
            sigs += page
            if len(page) < (100 if until else 25):
                break
            before = page[-1]["signature"]
        return list(reversed(sigs))  # oldest first

    async def scan(self, pending: list[aiosqlite.Row]) -> None:
        cutoff = now() - MATCH_WINDOW
        for token, addrs in self.watched.items():
            for addr in addrs:
                sigs = await self._new_signatures(addr)
                last_ok = None
                for s in sigs:
                    sig = s["signature"]
                    if s.get("err") is None and (s.get("blockTime") or now()) >= cutoff \
                            and not await self.db.signature_used(sig) and pending:
                        tx = await self.rpc.transaction(sig)
                        if tx is None:
                            break  # not yet available; retry from here next cycle
                        inv = self.match(tx, token, pending)
                        if inv and await self.fulfil(inv, sig):
                            pending = [p for p in pending if p["id"] != inv["id"]]
                    last_ok = sig
                if last_ok:
                    await self.db.kv_set(f"cursor:{addr}", last_ok)

    async def check_reference(self, inv: aiosqlite.Row) -> bool:
        for s in await self.rpc.signatures(inv["reference"], limit=10):
            if s.get("err") is not None or await self.db.signature_used(s["signature"]):
                continue
            tx = await self.rpc.transaction(s["signature"])
            if tx and self.match(tx, inv["token"], [inv]):
                return await self.fulfil(inv, s["signature"])
        return False

    async def check(self, only: int | None = None) -> None:
        async with self._lock:
            await self.refresh_accounts()
            pending = await self.db.pending_invoices()
            if not pending:
                # keep cursors fresh so we never walk long histories later
                for addrs in self.watched.values():
                    for addr in addrs:
                        latest = await self.rpc.signatures(addr, limit=1)
                        if latest:
                            await self.db.kv_set(f"cursor:{addr}", latest[0]["signature"])
                return
            recent = [i for i in pending if i["expires_at"] + 1800 > now()]
            if only is not None:
                recent = [i for i in pending if i["id"] == only]
            for inv in recent:
                await self.check_reference(inv)
            await self.scan(await self.db.pending_invoices())

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:  # noqa: BLE001 - keep the watcher alive
                log.exception("payment check failed")
            await asyncio.sleep(self.cfg.poll_interval)

    # ------------------------------------------------------------ fulfilment
    async def fulfil(self, inv: aiosqlite.Row, signature: str) -> bool:
        if not await self.db.mark_paid(inv["id"], signature):
            return False
        uid = inv["user_id"]
        log.info("invoice %s paid by %s: %s", inv["id"], uid, signature)
        if inv["promo"]:
            await self.db.use_promo(inv["promo"])
        await self.db.extend_sub(uid, inv["plan_id"], inv["days"])
        plan = self.cfg.plan(inv["plan_id"])
        title = plan.title if plan else inv["plan_id"]

        if inv["chat_id"] and inv["message_id"]:
            try:
                await self.bot.edit_message_caption(
                    chat_id=inv["chat_id"], message_id=inv["message_id"],
                    caption=f"✅ Invoice #{inv['id']} paid — {fmt_amount(inv['amount_units'], inv['token'])}",
                )
            except TelegramAPIError:
                pass
        await self.access.send_access(
            uid,
            f"✅ <b>Payment received!</b>\nPlan: <b>{html.escape(title)}</b>\n"
            f"<a href=\"https://solscan.io/tx/{signature}\">View transaction</a>",
        )
        await self.reward_referrer(uid, inv)

        user = await self.db.get_user(uid)
        who = f"@{user['username']}" if user and user["username"] else f"id {uid}"
        await self.access.notify_admins(
            f"💰 <b>New sale</b> #{inv['id']}\n{html.escape(who)} (<code>{uid}</code>) — {html.escape(title)}\n"
            f"{fmt_amount(inv['amount_units'], inv['token'])} (${inv['usd']:.2f})"
            + (f" · promo {inv['promo']}" if inv["promo"] else "")
            + f"\n<a href=\"https://solscan.io/tx/{signature}\">tx</a>"
        )
        return True

    async def reward_referrer(self, uid: int, inv: aiosqlite.Row) -> None:
        user = await self.db.get_user(uid)
        ref = user["referrer_id"] if user else None
        if not ref:
            return
        parts = []
        if self.cfg.referral_percent > 0:
            cents = int(inv["usd"] * self.cfg.referral_percent)  # usd * pct/100 * 100 cents
            if cents > 0:
                await self.db.add_ref_commission(ref, cents)
                parts.append(f"${cents / 100:.2f} commission")
        if self.cfg.referral_bonus_days > 0 and await self.db.paid_count(uid) == 1:
            await self.db.extend_sub(ref, "referral", self.cfg.referral_bonus_days)
            parts.append(f"{self.cfg.referral_bonus_days} bonus days")
        if parts:
            await self.access.safe_send(ref, "🎉 Someone you invited just subscribed! You earned "
                                        + " and ".join(parts) + ".")


class Jobs:
    """Periodic subscription maintenance: reminders and removal of expired members."""

    REMINDERS = ((3 * 86400, 1, "3 days"), (86400, 2, "24 hours"))

    def __init__(self, db: DB, access: Access):
        self.db, self.access = db, access

    async def tick(self) -> None:
        for sub in await self.db.expired_subs():
            uid = sub["user_id"]
            await self.db.deactivate_sub(uid)
            await self.access.remove(uid)
            await self.access.safe_send(
                uid, "⌛ Your subscription has expired and access was removed.\nRenew any time to get back in:",
                reply_markup=renew_kb(),
            )
        for within, stage, label in self.REMINDERS:
            for sub in await self.db.subs_to_remind(within, stage):
                await self.db.set_reminded(sub["user_id"], stage)
                if within > 86400 and sub["expires_at"] - now() <= 86400:
                    continue  # skip the 3-day reminder if we're already inside the last day
                await self.access.safe_send(
                    sub["user_id"],
                    f"⏰ Your subscription ends in less than {label} ({fmt_date(sub['expires_at'])}).\n"
                    "Renew now so you don't lose access:",
                    reply_markup=renew_kb(),
                )
        await self.db.expire_stale_invoices()

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("jobs tick failed")
            await asyncio.sleep(60)
