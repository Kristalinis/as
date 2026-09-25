"""Customer-facing handlers: menu, plans, checkout, subscription, referrals."""
from __future__ import annotations

import html
import io
import logging

import qrcode
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import TOKEN_DECIMALS, Config, Plan
from ..db import DB, now
from ..services import Access, Payments, fmt_amount, fmt_date
from ..solana import PriceFeed, is_valid_address, new_reference, pay_url, quote_amount, units_to_str

log = logging.getLogger(__name__)
router = Router(name="user")
router.message.filter(F.chat.type == "private")

TOKEN_ICON = {"SOL": "◎", "USDC": "💵", "USDT": "💵"}


class Form(StatesGroup):
    promo = State()
    wallet = State()


# ---------------------------------------------------------------- keyboards

def main_kb(cfg: Config, show_trial: bool):
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Plans & pricing", callback_data="plans")
    if show_trial:
        kb.button(text=f"🎁 Free {cfg.trial_days}-day trial", callback_data="trial")
    kb.button(text="👤 My subscription", callback_data="mysub")
    if cfg.referral_percent > 0 or cfg.referral_bonus_days > 0:
        kb.button(text="🤝 Invite & earn", callback_data="ref")
    kb.button(text="❓ Help", callback_data="help")
    kb.adjust(1)
    return kb.as_markup()


def back_kb(to: str = "menu", text: str = "« Back"):
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=to)
    return kb.as_markup()


def price_for(plan: Plan, percent: int) -> float:
    return round(plan.price_usd * (100 - percent) / 100, 2)


async def show(cb: CallbackQuery, text: str, markup=None) -> None:
    """Edit the current message in place, or send a new one if it can't be edited (e.g. a photo)."""
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


async def can_trial(db: DB, cfg: Config, uid: int) -> bool:
    if cfg.trial_days <= 0:
        return False
    user = await db.get_user(uid)
    return bool(user) and not user["trial_used"] and await db.get_sub(uid) is None


async def menu_text(cfg: Config) -> str:
    return cfg.welcome_text or (
        f"👋 Welcome to <b>{html.escape(cfg.project_name)}</b>!\n\n"
        "Get instant access to our private community. Pay with Solana (SOL / USDC) — "
        "access is granted automatically within a minute of payment."
    )


# ---------------------------------------------------------------- start / menu

@router.message(CommandStart())
async def start(msg: Message, command: CommandObject, state: FSMContext, db: DB, cfg: Config):
    await state.clear()
    ref = None
    if command.args and command.args.startswith("ref") and command.args[3:].isdigit():
        ref = int(command.args[3:])
    await db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name, ref)
    await msg.answer(await menu_text(cfg), reply_markup=main_kb(cfg, await can_trial(db, cfg, msg.from_user.id)),
                     disable_web_page_preview=True)


@router.message(Command("menu"))
async def menu_cmd(msg: Message, state: FSMContext, db: DB, cfg: Config):
    await state.clear()
    await db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    await msg.answer(await menu_text(cfg), reply_markup=main_kb(cfg, await can_trial(db, cfg, msg.from_user.id)))


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    await show(cb, await menu_text(cfg), main_kb(cfg, await can_trial(db, cfg, cb.from_user.id)))


@router.callback_query(F.data == "help")
async def help_cb(cb: CallbackQuery, cfg: Config):
    text = (
        "<b>How it works</b>\n"
        "1. Pick a plan and a coin (SOL, USDC or USDT on the <b>Solana</b> network).\n"
        "2. Scan the QR code with Phantom / Solflare / Backpack, or send the <b>exact</b> amount to the address.\n"
        "3. The bot detects the payment automatically and sends your personal invite links.\n\n"
        "Paying from an exchange? Make sure the amount that <b>arrives</b> is exactly the invoice amount "
        "and the network is Solana.\n\n"
        "Renewing early adds time on top of your current subscription."
    )
    if cfg.support:
        text += f"\n\nSupport: {html.escape(cfg.support)}"
    await show(cb, text, back_kb())


# ---------------------------------------------------------------- plans & checkout

async def plans_view(cfg: Config, state: FSMContext):
    data = await state.get_data()
    pct = data.get("promo_pct", 0)
    text = "💎 <b>Choose your plan</b>\n"
    if pct:
        text += f"🏷 Promo <b>{html.escape(data['promo'])}</b> applied: -{pct}%\n"
    kb = InlineKeyboardBuilder()
    for p in cfg.plans:
        price = price_for(p, pct)
        label = f"{p.title} — ${price:g}" + (f" (was ${p.price_usd:g})" if pct else "")
        kb.button(text=label, callback_data=f"plan:{p.id}")
        if p.description:
            text += f"\n<b>{html.escape(p.title)}</b> · {p.duration_text}\n{html.escape(p.description)}\n"
    kb.button(text="🏷 I have a promo code", callback_data="promo")
    kb.button(text="« Back", callback_data="menu")
    kb.adjust(1)
    return text, kb.as_markup()


@router.callback_query(F.data == "plans")
async def plans_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    await db.upsert_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await show(cb, *await plans_view(cfg, state))


@router.callback_query(F.data.startswith("plan:"))
async def plan_cb(cb: CallbackQuery, state: FSMContext, cfg: Config):
    plan = cfg.plan(cb.data.split(":", 1)[1])
    if not plan:
        return await cb.answer("This plan is no longer available.", show_alert=True)
    pct = (await state.get_data()).get("promo_pct", 0)
    price = price_for(plan, pct)
    text = (f"<b>{html.escape(plan.title)}</b> · {plan.duration_text}\n"
            f"Price: <b>${price:g}</b>\n\nHow would you like to pay?")
    kb = InlineKeyboardBuilder()
    for t in cfg.tokens:
        kb.button(text=f"{TOKEN_ICON[t]} Pay with {t}", callback_data=f"pay:{plan.id}:{t}")
    kb.button(text="« Back", callback_data="plans")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data == "promo")
async def promo_cb(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.promo)
    await show(cb, "🏷 Send your promo code:", back_kb("plans", "« Cancel"))


@router.message(Form.promo, F.text)
async def promo_msg(msg: Message, state: FSMContext, db: DB, cfg: Config):
    code = msg.text.strip().upper()[:32]
    promo = await db.valid_promo(code)
    if not promo:
        return await msg.answer("❌ This code is invalid or expired. Try another or go back.",
                                reply_markup=back_kb("plans"))
    await state.set_state(None)
    await state.update_data(promo=promo["code"], promo_pct=promo["percent"])
    text, kb = await plans_view(cfg, state)
    await msg.answer(f"✅ Code accepted: -{promo['percent']}%\n\n" + text, reply_markup=kb)


@router.callback_query(F.data.startswith("pay:"))
async def pay_cb(cb: CallbackQuery, state: FSMContext, bot: Bot, db: DB, cfg: Config,
                 prices: PriceFeed, access: Access):
    _, plan_id, token = cb.data.split(":")
    plan = cfg.plan(plan_id)
    if not plan or token not in cfg.tokens:
        return await cb.answer("Unavailable, please start again.", show_alert=True)
    uid = cb.from_user.id
    data = await state.get_data()
    promo_code, pct = data.get("promo"), 0
    if promo_code:
        promo = await db.valid_promo(promo_code)
        if promo:
            pct = promo["percent"]
        else:
            promo_code = None
            await state.update_data(promo=None, promo_pct=0)
    usd = price_for(plan, pct)

    if usd <= 0:  # 100% promo: grant immediately
        await db.use_promo(promo_code)
        await db.extend_sub(uid, plan.id, plan.days)
        await state.update_data(promo=None, promo_pct=0)
        await cb.answer()
        await access.send_access(uid, f"🎁 <b>{html.escape(plan.title)}</b> activated with promo {promo_code}!")
        await access.notify_admins(f"🎁 Free activation via promo {promo_code}: <code>{uid}</code> — {plan.title}")
        return

    inv = await db.open_invoice(uid, plan.id, token, promo_code)
    if not inv:
        await cb.answer("Creating invoice…")
        try:
            sol_price = await prices.sol_usd() if token == "SOL" else None
            amount = quote_amount(token, usd, sol_price, await db.taken_amounts(token))
        except Exception as e:  # noqa: BLE001
            log.error("quote failed: %s", e)
            return await bot.send_message(uid, "⚠️ Couldn't create an invoice right now. Please try again in a minute.")
        inv_id = await db.create_invoice(uid, plan.id, plan.days, token, amount, usd, new_reference(),
                                         promo_code, cfg.invoice_ttl_min)
        inv = await db.get_invoice(inv_id)
    else:
        await cb.answer()

    amount_str = units_to_str(inv["amount_units"], TOKEN_DECIMALS[token])
    mint = None if token == "SOL" else cfg.mints[token]
    url = pay_url(cfg.receiver, amount_str, inv["reference"], mint, cfg.project_name,
                  f"{plan.title} #{inv['id']}")
    buf = io.BytesIO()
    qrcode.make(url, border=2).save(buf, format="PNG")
    mins = max(1, (inv["expires_at"] - now()) // 60)
    caption = (
        f"🧾 <b>Invoice #{inv['id']}</b> — {html.escape(plan.title)}\n\n"
        f"Amount: <code>{amount_str}</code> <b>{token}</b>  (≈ ${inv['usd']:g})\n"
        f"Network: <b>Solana</b>\n"
        f"Address:\n<code>{cfg.receiver}</code>\n\n"
        f"⚠️ Send the <b>exact</b> amount — it identifies your payment.\n"
        f"📱 Or scan the QR with Phantom / Solflare / Backpack.\n\n"
        f"⏳ Valid for {mins} min. Access is granted automatically after payment."
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 I've paid — check now", callback_data=f"check:{inv['id']}")
    kb.button(text="« Back to plans", callback_data="plans")
    kb.adjust(1)
    sent = await bot.send_photo(uid, BufferedInputFile(buf.getvalue(), "invoice.png"), caption=caption,
                                reply_markup=kb.as_markup())
    await db.set_invoice_message(inv["id"], sent.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("check:"))
async def check_cb(cb: CallbackQuery, bot: Bot, db: DB, payments: Payments):
    inv_id = int(cb.data.split(":")[1])
    inv = await db.get_invoice(inv_id)
    if not inv or inv["user_id"] != cb.from_user.id:
        return await cb.answer("Invoice not found.", show_alert=True)
    if inv["status"] == "paid":
        return await cb.answer("✅ Already paid — check your messages for the invite links.", show_alert=True)
    await cb.answer("Checking the blockchain…")
    try:
        await payments.check(only=inv_id)
    except Exception as e:  # noqa: BLE001
        log.warning("manual check failed: %s", e)
    inv = await db.get_invoice(inv_id)
    if inv["status"] != "paid":
        await bot.send_message(
            cb.from_user.id,
            "⏳ Payment not found yet. Transfers usually confirm in under a minute — the bot keeps "
            "checking automatically and will message you as soon as it arrives.\n\n"
            f"Make sure you sent exactly <code>{fmt_amount(inv['amount_units'], inv['token'])}</code> "
            "on the Solana network."
        )


# ---------------------------------------------------------------- subscription

@router.callback_query(F.data == "mysub")
async def mysub_cb(cb: CallbackQuery, db: DB, cfg: Config):
    sub = await db.get_sub(cb.from_user.id)
    kb = InlineKeyboardBuilder()
    if sub and sub["active"] and sub["expires_at"] > now():
        plan = cfg.plan(sub["plan_id"])
        text = (f"✅ <b>Active</b>\nPlan: {html.escape(plan.title if plan else sub['plan_id'])}\n"
                f"Expires: <b>{fmt_date(sub['expires_at'])}</b>")
        kb.button(text="🔗 Get invite links", callback_data="links")
        kb.button(text="💎 Extend", callback_data="plans")
    else:
        text = "You don't have an active subscription."
        kb.button(text="💎 See plans", callback_data="plans")
    kb.button(text="« Back", callback_data="menu")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data == "links")
async def links_cb(cb: CallbackQuery, db: DB, access: Access):
    uid = cb.from_user.id
    sub = await db.get_sub(uid)
    if not await db.is_active(uid):
        return await cb.answer("Your subscription is not active.", show_alert=True)
    if now() - sub["last_link_at"] < 300:
        return await cb.answer("New links were sent recently — please wait a few minutes.", show_alert=True)
    await cb.answer()
    await access.send_access(uid, "🔗 <b>Here are your fresh invite links.</b>")


@router.callback_query(F.data == "trial")
async def trial_cb(cb: CallbackQuery, db: DB, cfg: Config, access: Access):
    uid = cb.from_user.id
    if not await can_trial(db, cfg, uid):
        return await cb.answer("Trial is not available for your account.", show_alert=True)
    await db.mark_trial_used(uid)
    await db.extend_sub(uid, "trial", cfg.trial_days)
    await cb.answer()
    await access.send_access(uid, f"🎁 <b>Your {cfg.trial_days}-day free trial is active!</b>")


# ---------------------------------------------------------------- referrals

@router.callback_query(F.data == "ref")
async def ref_cb(cb: CallbackQuery, bot: Bot, db: DB, cfg: Config):
    uid = cb.from_user.id
    await db.upsert_user(uid, cb.from_user.username, cb.from_user.first_name)
    me = await bot.me()
    user = await db.get_user(uid)
    link = f"https://t.me/{me.username}?start=ref{uid}"
    perks = []
    if cfg.referral_percent > 0:
        perks.append(f"<b>{cfg.referral_percent:g}%</b> of every payment they make (paid out in SOL/USDC)")
    if cfg.referral_bonus_days > 0:
        perks.append(f"<b>{cfg.referral_bonus_days} free days</b> when they buy their first plan")
    text = (
        "🤝 <b>Invite friends &amp; earn</b>\n\nYou get " + " and ".join(perks) + ".\n\n"
        f"Your link:\n<code>{link}</code>\n\n"
        f"Invited: <b>{await db.referral_count(uid)}</b>\n"
    )
    kb = InlineKeyboardBuilder()
    if cfg.referral_percent > 0:
        text += (f"Earned total: <b>${user['ref_earned_cents'] / 100:.2f}</b>\n"
                 f"Balance: <b>${user['ref_balance_cents'] / 100:.2f}</b> (min payout ${cfg.min_payout_usd:g})")
        if user["ref_balance_cents"] >= cfg.min_payout_usd * 100:
            kb.button(text="💸 Withdraw", callback_data="withdraw")
    kb.button(text="« Back", callback_data="menu")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data == "withdraw")
async def withdraw_cb(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wallet)
    await show(cb, "💸 Send your Solana wallet address for the payout:", back_kb("ref", "« Cancel"))


@router.message(Form.wallet, F.text)
async def wallet_msg(msg: Message, state: FSMContext, db: DB, cfg: Config, access: Access):
    wallet = msg.text.strip()
    if not is_valid_address(wallet):
        return await msg.answer("❌ That doesn't look like a Solana address. Try again:",
                                reply_markup=back_kb("ref", "« Cancel"))
    user = await db.get_user(msg.from_user.id)
    if not user or user["ref_balance_cents"] < cfg.min_payout_usd * 100:
        await state.clear()
        return await msg.answer("Balance is below the minimum payout.", reply_markup=back_kb())
    res = await db.request_payout(msg.from_user.id, wallet)
    await state.clear()
    if not res:
        return await msg.answer("Nothing to withdraw.", reply_markup=back_kb())
    pid, cents = res
    await msg.answer(f"✅ Payout request #{pid} for ${cents / 100:.2f} submitted. You'll be notified when sent.",
                     reply_markup=back_kb())
    await access.notify_admins(
        f"💸 <b>Payout request #{pid}</b>\nUser <code>{msg.from_user.id}</code>"
        f" (@{html.escape(msg.from_user.username or '-')})\nAmount: <b>${cents / 100:.2f}</b>\n"
        f"Wallet: <code>{wallet}</code>\n\nAfter sending, run /paidout {pid}"
    )
