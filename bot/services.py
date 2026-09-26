"""Payment detection, order fulfilment, access control and background jobs."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import LIFETIME_TS, TOKEN_DECIMALS, Config
from .db import DB, MATCH_WINDOW, now
from .i18n import t
from .solana import RPCError, SolanaRPC, has_reference, received_amount, units_to_str

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)


def fmt_date(ts: int, lang: str | None = None) -> str:
    if ts >= LIFETIME_TS:
        return t(lang, "never")
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def fmt_amount(units: int, method: str) -> str:
    if method == "XTR":
        return f"{units} ⭐"
    return f"{units_to_str(units, TOKEN_DECIMALS[method])} {method}"


def fmt_usd(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def duration(days: int, lang: str | None) -> str:
    return t(lang, "lifetime") if days <= 0 else t(lang, "days", n=days)


def renew_kb(lang: str | None, pid: str, key: str = "btn_renew", **kw):
    kb = InlineKeyboardBuilder()
    kb.button(text=t(lang, key, **kw), callback_data=f"prod:{pid}")
    return kb.as_markup()


class Access:
    """Grants / revokes access to premium chats and sends messages safely."""

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

    async def invite_links(self, uid: int, chats: list[int]) -> list[tuple[str, str]]:
        """Fresh single-use links (valid 24h) for the given chats."""
        links = []
        for chat_id in chats:
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
        return links

    async def send_access(self, uid: int, pid: str, header: str) -> None:
        lang = await self.db.lang(uid)
        product = await self.db.product(pid)
        chats = json.loads(product["chats"]) if product else []
        links = await self.invite_links(uid, chats)
        await self.db.touch_link(uid, pid)
        sub = await self.db.get_sub(uid, pid)
        text = header
        if sub:
            text += "\n\n" + t(lang, "access_until", date=fmt_date(sub["expires_at"], lang))
        kb = InlineKeyboardBuilder()
        for title, url in links:
            kb.button(text=t(lang, "btn_join", title=title), url=url)
        kb.adjust(1)
        if links:
            text += "\n\n" + t(lang, "links_below")
        await self.safe_send(uid, text, reply_markup=kb.as_markup(), disable_web_page_preview=True)

    async def deliver(self, uid: int, product: aiosqlite.Row, header: str) -> None:
        lang = await self.db.lang(uid)
        await self.safe_send(uid, header + "\n\n" + t(lang, "delivered", title=html.escape(product["title"])),
                             disable_web_page_preview=True)
        fid, kind = product["file_id"], product["file_kind"]
        try:
            if fid:
                send = {"photo": self.bot.send_photo, "video": self.bot.send_video,
                        "audio": self.bot.send_audio}.get(kind, self.bot.send_document)
                await send(uid, fid, protect_content=True)
            if product["content"]:
                await self.bot.send_message(uid, product["content"], parse_mode=None, protect_content=True)
            if not fid and not product["content"]:
                await self.notify_admins(f"⚠️ Digital product {product['id']} has no content to deliver to {uid}!")
        except TelegramAPIError as e:
            log.error("delivery of %s to %s failed: %s", product["id"], uid, e)
            await self.notify_admins(f"⚠️ Delivery of {product['id']} to <code>{uid}</code> failed: "
                                     f"{html.escape(str(e))}")

    async def is_chat_admin(self, chat_id: int, uid: int) -> bool:
        try:
            m = await self.bot.get_chat_member(chat_id, uid)
        except TelegramAPIError:
            return False
        return m.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)

    async def remove(self, uid: int, chats: list[int]) -> None:
        """Kick from the given chats, except chats still covered by another active subscription."""
        if self.cfg.is_admin(uid):
            return
        for chat_id in chats:
            if await self.db.has_chat_access(uid, chat_id):
                continue
            try:
                if await self.is_chat_admin(chat_id, uid):
                    continue
                await self.bot.ban_chat_member(chat_id, uid)
                await self.bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            except TelegramAPIError as e:
                log.warning("cannot remove %s from %s: %s", uid, chat_id, e)

    async def remove_product(self, uid: int, pid: str) -> None:
        product = await self.db.product(pid)
        if product:
            await self.remove(uid, json.loads(product["chats"]))

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
                await self.bot.send_message(aid, text, disable_web_page_preview=True)
            except TelegramAPIError:
                pass


class Orders:
    """Turns a paid invoice into access / delivery, rewards referrers and notifies admins."""

    def __init__(self, bot: Bot, db: DB, cfg: Config, access: Access):
        self.bot, self.db, self.cfg, self.access = bot, db, cfg, access

    async def fulfil(self, inv: aiosqlite.Row, signature: str) -> bool:
        if not await self.db.mark_paid(inv["id"], signature):
            return False
        uid = inv["user_id"]
        lang = await self.db.lang(uid)
        log.info("invoice %s paid by %s: %s", inv["id"], uid, signature)
        if inv["discount"] == "WINBACK":
            await self.db.set_winback(uid, 0, 0)
        elif inv["discount"]:
            await self.db.use_promo(inv["discount"])

        if inv["chat_id"] and inv["message_id"]:
            try:
                await self.bot.edit_message_caption(
                    chat_id=inv["chat_id"], message_id=inv["message_id"],
                    caption=t(lang, "invoice_paid", id=inv["id"], amount=fmt_amount(inv["amount_units"], inv["method"])),
                )
            except TelegramAPIError:
                pass
        header = t(lang, "payment_received", title=f"<b>{html.escape(inv['title'])}</b>")
        if inv["method"] != "XTR":
            header += f"\n<a href=\"https://solscan.io/tx/{signature}\">{t(lang, 'view_tx')}</a>"
        await self.grant(uid, inv["product_id"], inv["plan_id"], inv["days"], header, inv["id"])
        await self.reward_referrer(uid, inv)

        user = await self.db.get_user(uid)
        who = f"@{user['username']}" if user and user["username"] else f"id {uid}"
        await self.access.notify_admins(
            f"💰 <b>New sale</b> #{inv['id']}\n{html.escape(who)} (<code>{uid}</code>) — {html.escape(inv['title'])}\n"
            f"{fmt_amount(inv['amount_units'], inv['method'])} (${inv['usd']:.2f})"
            + (f" · {html.escape(inv['discount'])}" if inv["discount"] else "")
            + (f"\n<a href=\"https://solscan.io/tx/{signature}\">tx</a>" if inv["method"] != "XTR" else "")
        )
        return True

    async def grant(self, uid: int, pid: str, plan_id: str | None, days: int, header: str,
                    invoice_id: int | None = None) -> None:
        product = await self.db.product(pid)
        if product and product["kind"] == "digital":
            await self.db.add_purchase(uid, pid, invoice_id)
            await self.access.deliver(uid, product, header)
        else:
            await self.db.extend_sub(uid, pid, plan_id, days)
            await self.access.send_access(uid, pid, header)

    async def reward_referrer(self, uid: int, inv: aiosqlite.Row) -> None:
        user = await self.db.get_user(uid)
        ref = user["referrer_id"] if user else None
        if not ref:
            return
        lang = await self.db.lang(ref)
        parts = []
        if self.cfg.referral_percent > 0:
            cents = int(inv["usd"] * self.cfg.referral_percent)  # usd * pct/100 * 100 cents
            if cents > 0:
                await self.db.add_ref_commission(ref, cents)
                parts.append(t(lang, "reward_commission", amount=f"{cents / 100:.2f}"))
        if self.cfg.referral_bonus_days > 0 and await self.db.paid_count(uid) == 1:
            products = [p for p in await self.db.products() if p["kind"] == "sub"]
            if products:
                await self.db.extend_sub(ref, products[0]["id"], "referral", self.cfg.referral_bonus_days)
                parts.append(t(lang, "reward_days", n=self.cfg.referral_bonus_days))
        if parts:
            await self.access.safe_send(ref, t(lang, "ref_reward", what=f" {t(lang, 'and')} ".join(parts)))


class Payments:
    """Detects on-chain Solana payments.

    Two independent detection paths (deduplicated by tx signature):
      1. Solana Pay reference lookup per pending invoice (wallet payments).
      2. Scan of every incoming tx to the receiver wallet / its token accounts, matched by the
         invoice's unique exact amount (works for exchange withdrawals without a reference).
    """

    def __init__(self, db: DB, cfg: Config, rpc: SolanaRPC, orders: Orders):
        self.db, self.cfg, self.rpc, self.orders = db, cfg, rpc, orders
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

    def match(self, tx: dict, token: str, pending: list[aiosqlite.Row]) -> aiosqlite.Row | None:
        got = received_amount(tx, self.cfg.receiver, self.mint(token))
        if got <= 0:
            return None
        bt = tx.get("blockTime") or now()
        cands = [i for i in pending if i["method"] == token and i["created_at"] - 120 <= bt]
        for inv in cands:
            if has_reference(tx, inv["reference"]) and got >= inv["amount_units"]:
                return inv
        for inv in cands:
            if got == inv["amount_units"]:
                return inv
        return None

    async def _new_signatures(self, addr: str) -> list[dict]:
        until = await self.db.kv_get(f"cursor:{addr}")
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
                        if inv and await self.orders.fulfil(inv, sig):
                            pending = [p for p in pending if p["id"] != inv["id"]]
                    last_ok = sig
                if last_ok:
                    await self.db.kv_set(f"cursor:{addr}", last_ok)

    async def check_reference(self, inv: aiosqlite.Row) -> bool:
        for s in await self.rpc.signatures(inv["reference"], limit=10):
            if s.get("err") is not None or await self.db.signature_used(s["signature"]):
                continue
            tx = await self.rpc.transaction(s["signature"])
            if tx and self.match(tx, inv["method"], [inv]):
                return await self.orders.fulfil(inv, s["signature"])
        return False

    async def check(self, only: int | None = None) -> None:
        if not self.cfg.tokens:
            return
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


class Jobs:
    """Periodic maintenance: renewal reminders, removal of expired members, win-back offers."""

    REMINDERS = ((3 * 86400, 1, "left_3d"), (86400, 2, "left_1d"))
    WINBACK = ((86400, 1), (7 * 86400, 2))  # (delay after expiry, stage)

    def __init__(self, db: DB, cfg: Config, access: Access):
        self.db, self.cfg, self.access = db, cfg, access

    async def _product_title(self, pid: str) -> str:
        p = await self.db.product(pid)
        return html.escape(p["title"]) if p else pid

    async def tick(self) -> None:
        for sub in await self.db.expired_subs():
            uid, pid = sub["user_id"], sub["product_id"]
            lang = await self.db.lang(uid)
            await self.db.deactivate_sub(uid, pid)
            await self.access.remove_product(uid, pid)
            await self.access.safe_send(uid, t(lang, "expired", product=await self._product_title(pid)),
                                        reply_markup=renew_kb(lang, pid))
        for within, stage, left in self.REMINDERS:
            for sub in await self.db.subs_to_remind(within, stage):
                uid, pid = sub["user_id"], sub["product_id"]
                await self.db.set_reminded(uid, pid, stage)
                if within > 86400 and sub["expires_at"] - now() <= 86400:
                    continue  # skip the 3-day reminder if we're already inside the last day
                lang = await self.db.lang(uid)
                await self.access.safe_send(
                    uid, t(lang, "reminder", product=await self._product_title(pid), left=t(lang, left),
                           date=fmt_date(sub["expires_at"], lang)),
                    reply_markup=renew_kb(lang, pid),
                )
        await self.winback()
        await self.db.expire_stale_invoices()

    async def winback(self) -> None:
        pct = self.cfg.winback_percent
        for after, stage in self.WINBACK:
            for sub in await self.db.winback_due(after, stage):
                uid, pid = sub["user_id"], sub["product_id"]
                await self.db.set_winback_stage(uid, pid, stage)
                if pct <= 0 or await self.db.active_subs(uid):
                    continue
                if stage == 1 and sub["ended_at"] < now() - 7 * 86400:
                    continue  # too old for the first nudge; the 7-day one covers it
                product = await self.db.product(pid)
                if not product or not product["active"]:
                    continue
                until = now() + self.cfg.winback_valid_days * 86400
                await self.db.set_winback(uid, pct, until)
                lang = await self.db.lang(uid)
                await self.access.safe_send(
                    uid, t(lang, "winback", product=html.escape(product["title"]), pct=pct,
                           date=fmt_date(until, lang)),
                    reply_markup=renew_kb(lang, pid, "btn_winback", pct=pct),
                )

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("jobs tick failed")
            await asyncio.sleep(60)
