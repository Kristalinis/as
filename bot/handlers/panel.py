"""Inline-button admin panel: stats + chart, products & prices, promo codes, broadcast, payouts, settings."""
from __future__ import annotations

import asyncio
import html
import json

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..charts import revenue_chart
from ..config import EDITABLE, Config
from ..db import DB, now
from ..i18n import t
from ..services import Access, fmt_amount, fmt_date, fmt_usd
from .admin import IsAdmin

router = Router(name="panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminInput(StatesGroup):
    value = State()


PROMPTS = {
    "plan_price": "💲 Send the new price in USD (e.g. <code>19</code> or <code>9.99</code>):",
    "plan_title": "✏️ Send the new plan name:",
    "plan_days": "📅 Send the plan length in days (<code>0</code> = lifetime):",
    "plan_new": "➕ Send the new plan as <code>Name | days | price</code>\nExample: <code>6 Months | 180 | 89</code>"
                " (days 0 = lifetime)",
    "prod_title": "✏️ Send the new product name:",
    "prod_desc": "📝 Send the product description (<code>-</code> to clear):",
    "prod_price": "💲 Send the new price in USD:",
    "prod_chats": "🔗 Send the chat ids this product unlocks, comma separated (e.g. <code>-1001234567890</code>),\n"
                  "or <b>forward any post</b> from the channel to add it. Send <code>-</code> to clear.\n"
                  "The bot must be an admin there with “Invite users” and “Ban users” rights.",
    "prod_content": "📎 Send what buyers receive: a file, photo, video, audio, or a text/link message.",
    "new_sub": "➕ Send the name of the new subscription product (e.g. <code>VIP Signals</code>):",
    "new_digital": "➕ Send the new digital item as <code>Name | price</code>\nExample: <code>Trading Guide PDF | 9</code>",
    "promo_new": "🏷 Send <code>CODE percent [max_uses] [valid_days]</code>\nExample: <code>SUMMER 30 100 14</code>"
                 " (100% = free)",
    "broadcast": "📣 Send the message to broadcast (text, photo, video… anything). You'll pick the audience next.",
}


def kb_of(*rows: tuple[str, str], width: int = 1):
    kb = InlineKeyboardBuilder()
    for text, data in rows:
        kb.button(text=text, callback_data=data)
    kb.adjust(width)
    return kb.as_markup()


async def edit(cb: CallbackQuery, text: str, markup=None) -> None:
    edited = False
    if isinstance(cb.message, Message) and not cb.message.photo:
        try:
            await cb.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            edited = True
        except TelegramAPIError as e:
            edited = "not modified" in str(e)
    if not edited:
        await cb.bot.send_message(cb.from_user.id, text, reply_markup=markup, disable_web_page_preview=True)
    await cb.answer()


# ---------------------------------------------------------------- home

async def home_view(db: DB):
    s = await db.stats()
    text = (f"🛠 <b>Admin panel</b>\n\nToday: <b>${s['rev_today']:.2f}</b> · 30d: <b>${s['rev_30d']:.2f}</b>\n"
            f"Active subscribers: <b>{s['active']}</b> · users: {s['users']}")
    payouts = len(await db.pending_payouts())
    markup = kb_of(("📊 Stats & chart", "a:stats"), ("🛍 Products & prices", "a:prods"),
                   ("🏷 Promo codes", "a:promos"), ("📣 Broadcast", "a:bc"),
                   (f"💸 Payouts ({payouts})", "a:payouts"), ("⚙️ Settings", "a:settings"), width=2)
    return text, markup


@router.message(Command("admin"))
async def admin_cmd(msg: Message, state: FSMContext, db: DB):
    await state.set_state(None)
    text, markup = await home_view(db)
    await msg.answer(text, reply_markup=markup)


@router.callback_query(F.data == "a:home")
async def home_cb(cb: CallbackQuery, state: FSMContext, db: DB):
    await state.set_state(None)
    await edit(cb, *await home_view(db))


@router.message(Command("stats"))
async def stats_cmd(msg: Message, db: DB):
    await send_stats(msg.bot, msg.chat.id, db)


@router.callback_query(F.data == "a:stats")
async def stats_cb(cb: CallbackQuery, db: DB):
    await cb.answer()
    await send_stats(cb.bot, cb.from_user.id, db)


async def send_stats(bot: Bot, chat_id: int, db: DB) -> None:
    s = await db.stats()
    days = await db.daily_revenue(30)
    lines = [
        "📊 <b>Stats</b>",
        f"Users: <b>{s['users']}</b> (+{s['users_today']} today, {s['blocked']} blocked the bot)",
        f"Active subscribers: <b>{s['active']}</b>",
        f"Sales: <b>{s['sales']}</b> · open invoices: {s['pending']}",
        f"Revenue: today <b>${s['rev_today']:.2f}</b> · 30d <b>${s['rev_30d']:.2f}</b> · "
        f"total <b>${s['rev_total']:.2f}</b>",
    ]
    if s["by_method"]:
        lines.append("\n<b>By payment method</b>")
        lines += [f"• {fmt_amount(m['units'], m['method'])} — {m['n']} sales (${m['usd']:.2f})" for m in s["by_method"]]
    if s["by_product"]:
        lines.append("\n<b>Top products</b>")
        lines += [f"• {html.escape(p['title'])} — {p['n']} sales (${p['usd']:.2f})" for p in s["by_product"]]
    if s["ref_owed"]:
        lines.append(f"\nReferral commissions owed: ${s['ref_owed']:.2f}")
    png = revenue_chart(days, f"Revenue, last 30 days: ${sum(v for _, v in days):,.2f}")
    await bot.send_photo(chat_id, BufferedInputFile(png, "revenue.png"), caption="\n".join(lines)[:1024],
                         reply_markup=kb_of(("« Panel", "a:home")))


# ---------------------------------------------------------------- products

@router.callback_query(F.data == "a:prods")
async def prods_cb(cb: CallbackQuery, state: FSMContext, db: DB):
    await state.set_state(None)
    rows = []
    for p in await db.products(active_only=False):
        icon = "📦" if p["kind"] == "digital" else "💎"
        rows.append((f"{'' if p['active'] else '🚫 '}{icon} {p['title']}", f"a:prod:{p['id']}"))
    rows += [("➕ New subscription product", "a:ask:new_sub:-"), ("➕ New digital item", "a:ask:new_digital:-"),
             ("« Panel", "a:home")]
    await edit(cb, "🛍 <b>Products</b>\n💎 subscription · 📦 digital item · 🚫 hidden", kb_of(*rows))


async def product_view(db: DB, access: Access, pid: str):
    p = await db.product(pid)
    if not p:
        return "Product not found.", kb_of(("« Products", "a:prods"))
    lines = [f"{'📦' if p['kind'] == 'digital' else '💎'} <b>{html.escape(p['title'])}</b> "
             f"({'visible' if p['active'] else 'hidden'}) · id <code>{p['id']}</code>"]
    if p["description"]:
        lines.append(html.escape(p["description"]))
    rows: list[tuple[str, str]] = []
    if p["kind"] == "digital":
        content = p["file_kind"] or ("text" if p["content"] else "⚠️ none")
        lines.append(f"Price: <b>${fmt_usd(p['price_usd'])}</b> · content: {content}")
        rows += [("💲 Change price", f"a:ask:prod_price:{pid}"), ("📎 Replace content", f"a:ask:prod_content:{pid}")]
    else:
        chats = json.loads(p["chats"])
        titles = [html.escape(await access.title(c)) for c in chats]
        lines.append("Chats: " + (", ".join(titles) if titles else "⚠️ none — buyers won't get invite links"))
        plans = await db.plans(pid, active_only=False)
        lines.append("\nPlans (tap to edit):" if plans else "\n⚠️ No plans yet — add one.")
        for pl in plans:
            dur = "lifetime" if pl["days"] <= 0 else f"{pl['days']}d"
            rows.append((f"{'' if pl['active'] else '🚫 '}{pl['title']} · {dur} · ${fmt_usd(pl['price_usd'])}",
                         f"a:plan:{pl['id']}"))
        rows += [("➕ Add plan", f"a:ask:plan_new:{pid}"), ("🔗 Set chats", f"a:ask:prod_chats:{pid}")]
    rows += [("✏️ Rename", f"a:ask:prod_title:{pid}"), ("📝 Description", f"a:ask:prod_desc:{pid}"),
             ("🙈 Hide" if p["active"] else "👁 Show", f"a:ptog:{pid}"), ("🗑 Delete", f"a:pdel:{pid}"),
             ("« Products", "a:prods")]
    return "\n".join(lines), kb_of(*rows)


@router.callback_query(F.data.startswith("a:prod:"))
async def prod_cb(cb: CallbackQuery, state: FSMContext, db: DB, access: Access):
    await state.set_state(None)
    await edit(cb, *await product_view(db, access, cb.data[7:]))


@router.callback_query(F.data.startswith("a:ptog:"))
async def prod_toggle(cb: CallbackQuery, db: DB, access: Access):
    pid = cb.data[7:]
    p = await db.product(pid)
    if p:
        await db.update_product(pid, active=0 if p["active"] else 1)
    await edit(cb, *await product_view(db, access, pid))


@router.callback_query(F.data.startswith("a:pdel:"))
async def prod_delete_ask(cb: CallbackQuery, db: DB):
    pid = cb.data[7:]
    await edit(cb, "🗑 Delete this product and its plans? Existing subscribers keep access until expiry, "
                   "but won't be able to renew.\nTip: <b>Hide</b> it instead to keep it for later.",
               kb_of(("🗑 Yes, delete", f"a:pdelok:{pid}"), ("« Cancel", f"a:prod:{pid}")))


@router.callback_query(F.data.startswith("a:pdelok:"))
async def prod_delete(cb: CallbackQuery, state: FSMContext, db: DB):
    await db.delete_product(cb.data[9:])
    await prods_cb(cb, state, db)


async def plan_view(db: DB, plan_id: str):
    pl = await db.plan(plan_id)
    if not pl:
        return "Plan not found.", kb_of(("« Products", "a:prods"))
    dur = "lifetime" if pl["days"] <= 0 else f"{pl['days']} days"
    text = (f"<b>{html.escape(pl['title'])}</b> ({'visible' if pl['active'] else 'hidden'})\n"
            f"Length: {dur}\nPrice: <b>${fmt_usd(pl['price_usd'])}</b>")
    return text, kb_of(("💲 Change price", f"a:ask:plan_price:{plan_id}"), ("✏️ Rename", f"a:ask:plan_title:{plan_id}"),
                       ("📅 Change length", f"a:ask:plan_days:{plan_id}"),
                       ("🙈 Hide" if pl["active"] else "👁 Show", f"a:pltog:{plan_id}"),
                       ("🗑 Delete", f"a:pldel:{plan_id}"), ("« Product", f"a:prod:{pl['product_id']}"), width=2)


@router.callback_query(F.data.startswith("a:plan:"))
async def plan_cb(cb: CallbackQuery, state: FSMContext, db: DB):
    await state.set_state(None)
    await edit(cb, *await plan_view(db, cb.data[7:]))


@router.callback_query(F.data.startswith("a:pltog:"))
async def plan_toggle(cb: CallbackQuery, db: DB):
    pl = await db.plan(cb.data[8:])
    if pl:
        await db.update_plan(pl["id"], active=0 if pl["active"] else 1)
    await edit(cb, *await plan_view(db, cb.data[8:]))


@router.callback_query(F.data.startswith("a:pldel:"))
async def plan_delete(cb: CallbackQuery, db: DB, access: Access):
    pl = await db.plan(cb.data[8:])
    if pl:
        await db.delete_plan(pl["id"])
        await edit(cb, *await product_view(db, access, pl["product_id"]))
    else:
        await cb.answer()


# ---------------------------------------------------------------- promo codes

async def promos_view(db: DB):
    rows = await db.list_promos()
    lines = ["🏷 <b>Promo codes</b>"]
    buttons = []
    for r in rows:
        lines.append(f"<code>{r['code']}</code> -{r['percent']}% · used {r['uses']}/{r['max_uses'] or '∞'}"
                     + (f" · until {fmt_date(r['expires_at'])}" if r["expires_at"] else ""))
        buttons.append((f"🗑 {r['code']}", f"a:prdel:{r['code']}"))
    if not rows:
        lines.append("No codes yet.")
    buttons += [("➕ New promo code", "a:ask:promo_new:-"), ("« Panel", "a:home")]
    return "\n".join(lines), kb_of(*buttons)


@router.callback_query(F.data == "a:promos")
async def promos_cb(cb: CallbackQuery, state: FSMContext, db: DB):
    await state.set_state(None)
    await edit(cb, *await promos_view(db))


@router.callback_query(F.data.startswith("a:prdel:"))
async def promo_delete(cb: CallbackQuery, db: DB):
    await db.delete_promo(cb.data[8:])
    await edit(cb, *await promos_view(db))


# ---------------------------------------------------------------- payouts

async def payouts_view(db: DB):
    rows = await db.pending_payouts()
    if not rows:
        return "💸 No pending referral payouts.", kb_of(("« Panel", "a:home"))
    text = "💸 <b>Pending payouts</b> — send the amount, then mark it paid:\n\n" + "\n\n".join(
        f"#{r['id']} · user <code>{r['user_id']}</code> · <b>${r['amount_cents'] / 100:.2f}</b>\n"
        f"<code>{r['wallet']}</code>" for r in rows)
    return text, kb_of(*[(f"✅ #{r['id']} sent", f"a:paid:{r['id']}") for r in rows], ("« Panel", "a:home"))


@router.callback_query(F.data == "a:payouts")
async def payouts_cb(cb: CallbackQuery, db: DB):
    await edit(cb, *await payouts_view(db))


@router.callback_query(F.data.startswith("a:paid:"))
async def payout_paid(cb: CallbackQuery, db: DB, access: Access):
    row = await db.complete_payout(int(cb.data[7:]))
    if row:
        lang = await db.lang(row["user_id"])
        await access.safe_send(row["user_id"], t(lang, "payout_sent", amount=f"{row['amount_cents'] / 100:.2f}",
                                                 wallet=row["wallet"]))
    await edit(cb, *await payouts_view(db))


@router.message(Command("paidout"))
async def paidout_cmd(msg: Message, db: DB, access: Access):
    arg = (msg.text or "").split(maxsplit=1)[1:] or [""]
    row = await db.complete_payout(int(arg[0])) if arg[0].isdigit() else None
    if not row:
        return await msg.answer("Payout not found or already paid.")
    lang = await db.lang(row["user_id"])
    await access.safe_send(row["user_id"], t(lang, "payout_sent", amount=f"{row['amount_cents'] / 100:.2f}",
                                             wallet=row["wallet"]))
    await msg.answer(f"✅ Payout #{row['id']} marked as paid.")


# ---------------------------------------------------------------- settings

def settings_view(cfg: Config):
    lines = ["⚙️ <b>Settings</b> (tap to change)"]
    rows = []
    for name, (_, label) in EDITABLE.items():
        val = getattr(cfg, name)
        lines.append(f"{label}: <b>{val:g}</b>" if isinstance(val, float) else f"{label}: <b>{val}</b>")
        rows.append((label, f"a:ask:set:{name}"))
    rows.append(("« Panel", "a:home"))
    return "\n".join(lines), kb_of(*rows)


@router.callback_query(F.data == "a:settings")
async def settings_cb(cb: CallbackQuery, state: FSMContext, cfg: Config):
    await state.set_state(None)
    await edit(cb, *settings_view(cfg))


async def load_settings(db: DB, cfg: Config) -> None:
    for name, raw in (await db.kv_prefix("setting:")).items():
        if name in EDITABLE:
            try:
                setattr(cfg, name, EDITABLE[name][0](raw))
            except ValueError:
                pass


# ---------------------------------------------------------------- broadcast

@router.callback_query(F.data == "a:bc")
async def bc_cb(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminInput.value)
    await state.update_data(action="broadcast", target="-")
    await edit(cb, PROMPTS["broadcast"], kb_of(("« Cancel", "a:home")))


@router.callback_query(F.data.startswith("a:bcs:"))
async def bc_send(cb: CallbackQuery, state: FSMContext, bot: Bot, db: DB):
    data = await state.get_data()
    src = data.get("bc_msg")
    if not src:
        return await cb.answer("Nothing to send — start again.", show_alert=True)
    await state.update_data(bc_msg=None)
    ids = await db.audience(cb.data[6:])
    await edit(cb, f"📣 Sending to {len(ids)} users…", kb_of(("« Panel", "a:home")))
    asyncio.create_task(broadcast(bot, db, cb.from_user.id, src, ids))


async def broadcast(bot: Bot, db: DB, admin_id: int, src: list[int], ids: list[int]) -> None:
    ok = fail = 0
    for uid in ids:
        for _ in range(3):
            try:
                await bot.copy_message(uid, src[0], src[1])
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                await db.set_blocked(uid)
                fail += 1
                break
            except TelegramAPIError:
                fail += 1
                break
        await asyncio.sleep(0.05)  # stay under Telegram's ~30 msg/s limit
    try:
        await bot.send_message(admin_id, f"📣 Broadcast done: {ok} delivered, {fail} failed.")
    except TelegramAPIError:
        pass


# ---------------------------------------------------------------- generic input

@router.callback_query(F.data.startswith("a:ask:"))
async def ask_cb(cb: CallbackQuery, state: FSMContext):
    _, _, action, target = cb.data.split(":", 3)
    if action == "set":
        prompt = f"⚙️ Send the new value for <b>{EDITABLE[target][1]}</b>:"
        action, target = f"set:{target}", "-"
    else:
        prompt = PROMPTS[action]
    await state.set_state(AdminInput.value)
    await state.update_data(action=action, target=target)
    await edit(cb, prompt, kb_of(("« Cancel", "a:home")))


def _num(text: str | None, kind=float, minimum: float = 0):
    try:
        v = kind((text or "").strip().replace("$", "").replace(",", "."))
    except ValueError:
        return None
    return v if v >= minimum else None


@router.message(AdminInput.value)
async def admin_input(msg: Message, state: FSMContext, db: DB, cfg: Config, access: Access):
    data = await state.get_data()
    action, target = data.get("action"), data.get("target")
    text = (msg.text or "").strip()
    if text.startswith("/") and action != "broadcast":
        await state.set_state(None)
        raise SkipHandler()  # a command cancels the pending input and runs normally
    retry = kb_of(("« Cancel", "a:home"))
    back_to_product = None

    if action == "broadcast":
        await state.set_state(None)
        await state.update_data(bc_msg=[msg.chat.id, msg.message_id])
        counts = {s: len(await db.audience(s)) for s in ("all", "active", "inactive")}
        return await msg.answer("📣 Who should receive it?", reply_markup=kb_of(
            (f"Everyone ({counts['all']})", "a:bcs:all"), (f"Active subscribers ({counts['active']})", "a:bcs:active"),
            (f"Not subscribed ({counts['inactive']})", "a:bcs:inactive"), ("« Cancel", "a:home")))

    if action == "plan_price" or action == "prod_price":
        v = _num(text)
        if v is None:
            return await msg.answer("❌ Send a number, e.g. <code>19.99</code>", reply_markup=retry)
        if action == "plan_price":
            await db.update_plan(target, price_usd=round(v, 2))
            await state.set_state(None)
            return await msg.answer("✅ Price updated.", reply_markup=(await plan_view(db, target))[1])
        await db.update_product(target, price_usd=round(v, 2))
        back_to_product = target
    elif action == "plan_title":
        if not text:
            return await msg.answer("❌ Send a name.", reply_markup=retry)
        await db.update_plan(target, title=text[:40])
        await state.set_state(None)
        return await msg.answer("✅ Renamed.", reply_markup=(await plan_view(db, target))[1])
    elif action == "plan_days":
        v = _num(text, int)
        if v is None:
            return await msg.answer("❌ Send a whole number of days (0 = lifetime).", reply_markup=retry)
        await db.update_plan(target, days=v)
        await state.set_state(None)
        return await msg.answer("✅ Length updated.", reply_markup=(await plan_view(db, target))[1])
    elif action == "plan_new":
        parts = [p.strip() for p in text.split("|")]
        days = _num(parts[1], int) if len(parts) == 3 else None
        price = _num(parts[2]) if len(parts) == 3 else None
        if not parts[0] or days is None or price is None:
            return await msg.answer("❌ Format: <code>Name | days | price</code>", reply_markup=retry)
        await db.add_plan(target, parts[0][:40], days, round(price, 2))
        back_to_product = target
    elif action in ("prod_title", "prod_desc"):
        if not text:
            return await msg.answer("❌ Send some text.", reply_markup=retry)
        if action == "prod_title":
            await db.update_product(target, title=text[:60])
        else:
            await db.update_product(target, description="" if text == "-" else text[:700])
        back_to_product = target
    elif action == "prod_chats":
        product = await db.product(target)
        chats = json.loads(product["chats"]) if product else []
        origin_chat = getattr(msg.forward_origin, "chat", None) if msg.forward_origin else None
        if origin_chat:
            chats = sorted(set(chats) | {origin_chat.id})
        elif text == "-":
            chats = []
        else:
            try:
                chats = [int(c) for c in text.replace(" ", "").split(",") if c]
            except ValueError:
                return await msg.answer("❌ Send chat ids like <code>-1001234567890</code> or forward a post.",
                                        reply_markup=retry)
        await db.update_product(target, chats=chats)
        back_to_product = target
    elif action == "prod_content":
        if msg.document:
            fields = dict(file_id=msg.document.file_id, file_kind="document", content=msg.caption)
        elif msg.photo:
            fields = dict(file_id=msg.photo[-1].file_id, file_kind="photo", content=msg.caption)
        elif msg.video:
            fields = dict(file_id=msg.video.file_id, file_kind="video", content=msg.caption)
        elif msg.audio:
            fields = dict(file_id=msg.audio.file_id, file_kind="audio", content=msg.caption)
        elif text:
            fields = dict(file_id=None, file_kind=None, content=text)
        else:
            return await msg.answer("❌ Send a file, photo, video, audio or text.", reply_markup=retry)
        await db.update_product(target, **fields)
        back_to_product = target
    elif action == "new_sub":
        if not text:
            return await msg.answer("❌ Send a name.", reply_markup=retry)
        pid = await db.add_product("sub", text[:60], chats=cfg.default_chats)
        await state.update_data(action="plan_new", target=pid)
        return await msg.answer("✅ Product created. Now add its first plan.\n\n" + PROMPTS["plan_new"],
                                reply_markup=kb_of(("Skip", f"a:prod:{pid}")))
    elif action == "new_digital":
        parts = [p.strip() for p in text.split("|")]
        price = _num(parts[1]) if len(parts) == 2 else None
        if not parts[0] or price is None:
            return await msg.answer("❌ Format: <code>Name | price</code>", reply_markup=retry)
        pid = await db.add_product("digital", parts[0][:60], price_usd=round(price, 2))
        await state.update_data(action="prod_content", target=pid)
        return await msg.answer("✅ Item created. " + PROMPTS["prod_content"],
                                reply_markup=kb_of(("Skip", f"a:prod:{pid}")))
    elif action == "promo_new":
        parts = text.split()
        if len(parts) < 2 or not all(p.isdigit() for p in parts[1:]) or not 1 <= int(parts[1]) <= 100:
            return await msg.answer("❌ Format: <code>CODE percent [max_uses] [valid_days]</code>", reply_markup=retry)
        max_uses = int(parts[2]) if len(parts) > 2 else 0
        days = int(parts[3]) if len(parts) > 3 else 0
        await db.add_promo(parts[0][:32], int(parts[1]), max_uses, now() + days * 86400 if days else 0)
        await state.set_state(None)
        text_, markup = await promos_view(db)
        return await msg.answer("✅ Promo code saved.\n\n" + text_, reply_markup=markup)
    elif action and action.startswith("set:"):
        name = action[4:]
        kind = EDITABLE[name][0]
        v = _num(text, kind)
        if v is None:
            return await msg.answer("❌ Send a non-negative number.", reply_markup=retry)
        setattr(cfg, name, v)
        await db.kv_set(f"setting:{name}", str(v))
        await state.set_state(None)
        text_, markup = settings_view(cfg)
        return await msg.answer("✅ Saved.\n\n" + text_, reply_markup=markup)
    else:
        await state.set_state(None)
        return await msg.answer("Nothing to do.", reply_markup=kb_of(("« Panel", "a:home")))

    await state.set_state(None)
    text_, markup = await product_view(db, access, back_to_product)
    await msg.answer("✅ Saved.\n\n" + text_, reply_markup=markup)
