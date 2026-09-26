"""Admin text commands and the premium-chat membership guard."""
from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import JOIN_TRANSITION, BaseFilter, ChatMemberUpdatedFilter, Command, CommandObject
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from ..config import Config
from ..db import DB, now
from ..i18n import t
from ..services import Access, Orders, fmt_date

router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, cfg: Config) -> bool:
        return bool(event.from_user) and cfg.is_admin(event.from_user.id)


router.message.filter(IsAdmin())


async def _target(msg: Message, db: DB, arg: str | None):
    if not arg:
        await msg.answer("Specify a user id or @username.")
        return None
    user = await db.find_user(arg.split()[0])
    if not user:
        await msg.answer("User not found (they must /start the bot first).")
    return user


async def _sub_product(msg: Message, db: DB, pid: str | None):
    products = [p for p in await db.products(active_only=False) if p["kind"] == "sub"]
    if pid:
        p = next((p for p in products if p["id"] == pid), None)
        if not p:
            await msg.answer("Unknown product id. Subscription products: "
                             + ", ".join(f"<code>{p['id']}</code> ({html.escape(p['title'])})" for p in products))
        return p
    if not products:
        await msg.answer("No subscription products exist yet.")
        return None
    return products[0]


@router.message(Command("user"))
async def user_info(msg: Message, command: CommandObject, db: DB):
    user = await _target(msg, db, command.args)
    if not user:
        return
    lines = []
    for sub in await db.user_subs(user["id"]):
        p = await db.product(sub["product_id"])
        state = "active until" if sub["active"] and sub["expires_at"] > now() else "expired"
        lines.append(f"  • {html.escape(p['title'] if p else sub['product_id'])}: {state} {fmt_date(sub['expires_at'])}")
    for p in await db.user_purchases(user["id"]):
        lines.append(f"  • 📦 {html.escape(p['title'])}")
    await msg.answer(
        f"👤 <code>{user['id']}</code> @{html.escape(user['username'] or '-')} {html.escape(user['first_name'] or '')}"
        f" · {user['lang'] or '?'}\nJoined: {fmt_date(user['created_at'])}\n"
        f"Access:\n" + ("\n".join(lines) or "  none") + "\n"
        f"Paid invoices: {await db.paid_count(user['id'])}\n"
        f"Referrer: {user['referrer_id'] or '-'} · invited: {await db.referral_count(user['id'])}\n"
        f"Referral balance: ${user['ref_balance_cents'] / 100:.2f}"
    )


@router.message(Command("grant"))
async def grant(msg: Message, command: CommandObject, db: DB, orders: Orders):
    parts = (command.args or "").split()
    if len(parts) not in (2, 3) or not (parts[1].isdigit() or parts[1].lower() == "life"):
        return await msg.answer("Usage: /grant &lt;id|@username&gt; &lt;days|life&gt; [product_id]")
    user = await _target(msg, db, parts[0])
    product = user and await _sub_product(msg, db, parts[2] if len(parts) == 3 else None)
    if not user or not product:
        return
    days = 0 if parts[1].lower() == "life" else int(parts[1])
    await orders.grant(user["id"], product["id"], "manual", days, t(user["lang"], "granted"))
    sub = await db.get_sub(user["id"], product["id"])
    await msg.answer(f"✅ {html.escape(product['title'])} for <code>{user['id']}</code> until "
                     f"{fmt_date(sub['expires_at'])}")


@router.message(Command("revoke"))
async def revoke(msg: Message, command: CommandObject, db: DB, access: Access):
    parts = (command.args or "").split()
    user = await _target(msg, db, parts[0] if parts else None)
    if not user:
        return
    subs = await db.active_subs(user["id"])
    if len(parts) > 1:
        subs = [s for s in subs if s["product_id"] == parts[1]]
    for sub in subs:
        await db.deactivate_sub(user["id"], sub["product_id"])
        await access.remove_product(user["id"], sub["product_id"])
    await msg.answer(f"⛔ Revoked {len(subs)} subscription(s) for <code>{user['id']}</code>")


@router.message(Command("refund"))
async def refund(msg: Message, command: CommandObject, bot: Bot, db: DB, access: Access):
    arg = (command.args or "").strip()
    inv = await db.get_invoice(int(arg)) if arg.isdigit() else None
    if not inv or inv["status"] != "paid" or inv["method"] != "XTR":
        return await msg.answer("Usage: /refund &lt;invoice_id&gt; — only paid Telegram Stars invoices can be "
                                "refunded automatically. Crypto refunds must be sent manually from your wallet.")
    try:
        await bot.refund_star_payment(inv["user_id"], inv["signature"].removeprefix("stars:"))
    except TelegramAPIError as e:
        return await msg.answer(f"❌ Refund failed: {html.escape(str(e))}")
    await db.mark_refunded(inv["id"])
    product = await db.product(inv["product_id"])
    if product and product["kind"] == "digital":
        await db.remove_purchase(inv["id"])
    else:
        await db.deactivate_sub(inv["user_id"], inv["product_id"])
        await access.remove_product(inv["user_id"], inv["product_id"])
    lang = await db.lang(inv["user_id"])
    await access.safe_send(inv["user_id"], t(lang, "refunded", title=html.escape(inv["title"])))
    await msg.answer(f"↩️ Invoice #{inv['id']} refunded ({inv['amount_units']} ⭐) and access removed.")


@router.message(Command("chatid"))
async def chat_id(msg: Message):
    await msg.answer(f"Chat id: <code>{msg.chat.id}</code>")


@router.message(F.chat.type == "private", F.forward_origin.chat.id)
async def forwarded_chat_id(msg: Message):
    chat = msg.forward_origin.chat
    await msg.answer(f"{html.escape(chat.title or '')} id: <code>{chat.id}</code>")


# ---------------------------------------------------------------- membership guard

guard = Router(name="guard")


@guard.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_join(event: ChatMemberUpdated, db: DB, cfg: Config, access: Access):
    """Remove anyone who joins a premium chat without an active subscription."""
    if not cfg.strict_membership or event.chat.id not in await db.all_premium_chats():
        return
    user = event.new_chat_member.user
    if user.is_bot or cfg.is_admin(user.id) or await db.has_chat_access(user.id, event.chat.id):
        return
    await access.remove(user.id, [event.chat.id])
    await access.safe_send(user.id, t(await db.lang(user.id), "guard_kick"))

