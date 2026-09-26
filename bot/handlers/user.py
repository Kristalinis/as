"""Customer-facing handlers: menu, catalog, checkout (Solana + Telegram Stars), account, referrals."""
from __future__ import annotations

import html
import io
import json
import logging
import math

import qrcode
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, LabeledPrice, Message, PreCheckoutQuery, User,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import TOKEN_DECIMALS, Config
from ..db import DB, now
from ..i18n import LANGS, detect, t
from ..services import Access, Orders, Payments, duration, fmt_amount, fmt_date, fmt_usd
from ..solana import PriceFeed, is_valid_address, new_reference, pay_url, quote_amount, units_to_str

log = logging.getLogger(__name__)
router = Router(name="user")
router.message.filter(F.chat.type == "private")

TOKEN_ICON = {"SOL": "◎", "USDC": "💵", "USDT": "💵"}


class Form(StatesGroup):
    promo = State()
    wallet = State()


async def lang_of(db: DB, user: User) -> str:
    return await db.lang(user.id) or detect(user.language_code)


# ---------------------------------------------------------------- helpers

def back_kb(lang: str, to: str = "menu", key: str = "btn_back"):
    kb = InlineKeyboardBuilder()
    kb.button(text=t(lang, key), callback_data=to)
    return kb.as_markup()


async def show(cb: CallbackQuery, text: str, markup=None) -> None:
    """Edit the current message in place, or send a new one if it can't be edited (e.g. a photo)."""
    edited = False
    if isinstance(cb.message, Message) and not cb.message.photo and not cb.message.invoice:
        try:
            await cb.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            edited = True
        except TelegramAPIError as e:
            edited = "not modified" in str(e)
    if not edited:
        await cb.bot.send_message(cb.from_user.id, text, reply_markup=markup, disable_web_page_preview=True)
    await cb.answer()


async def trial_product(db: DB):
    return next((p for p in await db.products() if p["kind"] == "sub"), None)


async def can_trial(db: DB, cfg: Config, uid: int) -> bool:
    if cfg.trial_days <= 0 or not await trial_product(db):
        return False
    user = await db.get_user(uid)
    return bool(user) and not user["trial_used"] and not await db.user_subs(uid)


async def discount(state: FSMContext, db: DB, uid: int) -> tuple[int, str | None]:
    """Best available discount: (percent, source) where source is a promo code or 'WINBACK'."""
    data = await state.get_data()
    pct, src = 0, None
    if data.get("promo"):
        promo = await db.valid_promo(data["promo"])
        if promo:
            pct, src = promo["percent"], promo["code"]
        else:
            await state.update_data(promo=None)
    user = await db.get_user(uid)
    if user and user["winback_pct"] > pct and user["winback_until"] > now():
        pct, src = user["winback_pct"], "WINBACK"
    return pct, src


def discounted(price: float, pct: int) -> float:
    return round(price * (100 - pct) / 100, 2)


async def discount_banner(lang: str, db: DB, uid: int, pct: int, src: str | None) -> str:
    if not pct:
        return ""
    if src == "WINBACK":
        user = await db.get_user(uid)
        return t(lang, "winback_applied", pct=pct, date=fmt_date(user["winback_until"], lang)) + "\n"
    return t(lang, "promo_applied", code=html.escape(src), pct=pct) + "\n"


async def main_kb(db: DB, cfg: Config, lang: str, uid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text=t(lang, "btn_catalog"), callback_data="shop")
    if await can_trial(db, cfg, uid):
        kb.button(text=t(lang, "btn_trial", days=cfg.trial_days), callback_data="trial")
    kb.button(text=t(lang, "btn_account"), callback_data="account")
    if cfg.referral_percent > 0 or cfg.referral_bonus_days > 0:
        kb.button(text=t(lang, "btn_ref"), callback_data="ref")
    kb.button(text=t(lang, "btn_help"), callback_data="help")
    kb.button(text=t(lang, "btn_lang"), callback_data="lang")
    kb.adjust(1)
    return kb.as_markup()


def menu_text(cfg: Config, lang: str) -> str:
    return cfg.welcome_text.get(lang) or cfg.welcome_text.get("en") or t(
        lang, "welcome_default", name=html.escape(cfg.project_name))


# ---------------------------------------------------------------- start / menu / language

@router.message(CommandStart())
async def start(msg: Message, command: CommandObject, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    ref = None
    if command.args and command.args.startswith("ref") and command.args[3:].isdigit():
        ref = int(command.args[3:])
    u = msg.from_user
    await db.upsert_user(u.id, u.username, u.first_name, detect(u.language_code), ref)
    lang = await lang_of(db, u)
    await msg.answer(menu_text(cfg, lang), reply_markup=await main_kb(db, cfg, lang, u.id),
                     disable_web_page_preview=True)


@router.message(Command("menu"))
async def menu_cmd(msg: Message, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    u = msg.from_user
    await db.upsert_user(u.id, u.username, u.first_name, detect(u.language_code))
    lang = await lang_of(db, u)
    await msg.answer(menu_text(cfg, lang), reply_markup=await main_kb(db, cfg, lang, u.id),
                     disable_web_page_preview=True)


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    lang = await lang_of(db, cb.from_user)
    await show(cb, menu_text(cfg, lang), await main_kb(db, cfg, lang, cb.from_user.id))


@router.callback_query(F.data == "lang")
async def lang_cb(cb: CallbackQuery, db: DB):
    lang = await lang_of(db, cb.from_user)
    kb = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        kb.button(text=("• " if code == lang else "") + name, callback_data=f"setlang:{code}")
    kb.button(text=t(lang, "btn_back"), callback_data="menu")
    kb.adjust(1)
    await show(cb, t(lang, "lang_choose"), kb.as_markup())


@router.callback_query(F.data.startswith("setlang:"))
async def setlang_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    code = cb.data.split(":", 1)[1]
    if code in LANGS:
        u = cb.from_user
        await db.upsert_user(u.id, u.username, u.first_name, code)
        await db.set_lang(u.id, code)
    await menu_cb(cb, state, db, cfg)


@router.callback_query(F.data == "help")
async def help_cb(cb: CallbackQuery, db: DB, cfg: Config):
    lang = await lang_of(db, cb.from_user)
    text = t(lang, "help")
    if cfg.support:
        text += "\n\n" + t(lang, "support", contact=html.escape(cfg.support))
    await show(cb, text, back_kb(lang))


# ---------------------------------------------------------------- catalog

@router.callback_query(F.data == "shop")
async def shop_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    u = cb.from_user
    await db.upsert_user(u.id, u.username, u.first_name, detect(u.language_code))
    lang = await lang_of(db, u)
    products = await db.products()
    if len(products) == 1:
        return await product_view(cb, state, db, cfg, products[0]["id"])
    pct, src = await discount(state, db, u.id)
    text = await discount_banner(lang, db, u.id, pct, src) + t(lang, "catalog_title")
    kb = InlineKeyboardBuilder()
    for p in products:
        if p["kind"] == "digital":
            label = f"📦 {p['title']} — ${fmt_usd(discounted(p['price_usd'], pct))}"
        else:
            prices = [pl["price_usd"] for pl in await db.plans(p["id"])]
            if not prices:
                continue
            label = f"💎 {p['title']} — ${fmt_usd(discounted(min(prices), pct))}+"
        kb.button(text=label, callback_data=f"prod:{p['id']}")
    if not products:
        text += "\n\n" + t(lang, "catalog_empty")
    kb.button(text=t(lang, "btn_promo"), callback_data="promo")
    kb.button(text=t(lang, "btn_back"), callback_data="menu")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data.startswith("prod:"))
async def prod_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    await state.set_state(None)
    await product_view(cb, state, db, cfg, cb.data.split(":", 1)[1])


async def product_view(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config, pid: str):
    u = cb.from_user
    await db.upsert_user(u.id, u.username, u.first_name, detect(u.language_code))
    lang = await lang_of(db, u)
    p = await db.product(pid)
    if not p or not p["active"]:
        return await cb.answer(t(lang, "unavailable"), show_alert=True)
    single = len(await db.products()) == 1
    pct, src = await discount(state, db, u.id)
    text = await discount_banner(lang, db, u.id, pct, src)
    kb = InlineKeyboardBuilder()
    if p["kind"] == "digital":
        price = discounted(p["price_usd"], pct)
        text += f"📦 <b>{html.escape(p['title'])}</b>\n"
        if p["description"]:
            text += html.escape(p["description"]) + "\n"
        text += "\n" + t(lang, "choose_pay", title=html.escape(p["title"]), duration="📦",
                         price=fmt_usd(price)).split("\n", 1)[1]
        pay_buttons(kb, cfg, lang, f"pr:{p['id']}", price)
    else:
        text += t(lang, "plans_title", product=html.escape(p["title"]))
        if p["description"]:
            text += "\n" + html.escape(p["description"])
        for pl in await db.plans(pid):
            price = discounted(pl["price_usd"], pct)
            label = f"{pl['title']} — ${fmt_usd(price)}"
            if pct:
                label += f" ({t(lang, 'was')} ${fmt_usd(pl['price_usd'])})"
            kb.button(text=label, callback_data=f"plan:{pl['id']}")
    if single:
        kb.button(text=t(lang, "btn_promo"), callback_data="promo")
    kb.button(text=t(lang, "btn_back"), callback_data="menu" if single else "shop")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


def pay_buttons(kb: InlineKeyboardBuilder, cfg: Config, lang: str, target: str, usd: float) -> None:
    for tok in cfg.tokens:
        kb.button(text=t(lang, "btn_pay_token", icon=TOKEN_ICON[tok], token=tok), callback_data=f"pay:{target}:{tok}")
    if cfg.stars_enabled:
        kb.button(text=t(lang, "btn_pay_stars", stars=stars_for(cfg, usd)), callback_data=f"pay:{target}:XTR")


def stars_for(cfg: Config, usd: float) -> int:
    return max(1, math.ceil(usd * cfg.stars_per_usd))


def best_upsell(plans, cur) -> tuple[object, int] | None:
    if cur["days"] <= 0 or cur["price_usd"] <= 0:
        return None
    per_day = cur["price_usd"] / cur["days"]
    best = None
    for pl in plans:
        if pl["days"] > cur["days"] and pl["price_usd"] > 0:
            save = round((1 - (pl["price_usd"] / pl["days"]) / per_day) * 100)
            if save >= 5 and (best is None or save > best[1]):
                best = (pl, save)
    return best


@router.callback_query(F.data.startswith("plan:"))
async def plan_cb(cb: CallbackQuery, state: FSMContext, db: DB, cfg: Config):
    lang = await lang_of(db, cb.from_user)
    plan = await db.plan(cb.data.split(":", 1)[1])
    product = await db.product(plan["product_id"]) if plan else None
    if not plan or not plan["active"] or not product or not product["active"]:
        return await cb.answer(t(lang, "unavailable"), show_alert=True)
    pct, _ = await discount(state, db, cb.from_user.id)
    price = discounted(plan["price_usd"], pct)
    text = t(lang, "choose_pay", title=html.escape(f"{product['title']} · {plan['title']}"),
             duration=duration(plan["days"], lang), price=fmt_usd(price))
    kb = InlineKeyboardBuilder()
    pay_buttons(kb, cfg, lang, f"pl:{plan['id']}", price)
    up = best_upsell(await db.plans(product["id"]), plan) if cfg.upsell else None
    if up:
        better, save = up
        text += "\n\n" + t(lang, "upsell", title=html.escape(better["title"]),
                           price=fmt_usd(discounted(better["price_usd"], pct)), save=save)
        kb.button(text=t(lang, "btn_upsell", title=better["title"], save=save), callback_data=f"plan:{better['id']}")
    kb.button(text=t(lang, "btn_back"), callback_data=f"prod:{product['id']}")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data == "promo")
async def promo_cb(cb: CallbackQuery, state: FSMContext, db: DB):
    lang = await lang_of(db, cb.from_user)
    await state.set_state(Form.promo)
    await show(cb, t(lang, "promo_ask"), back_kb(lang, "shop", "btn_cancel"))


@router.message(Form.promo, F.text)
async def promo_msg(msg: Message, state: FSMContext, db: DB):
    lang = await lang_of(db, msg.from_user)
    promo = await db.valid_promo(msg.text.strip()[:32])
    if not promo:
        return await msg.answer(t(lang, "promo_bad"), reply_markup=back_kb(lang, "shop"))
    await state.set_state(None)
    await state.update_data(promo=promo["code"])
    kb = InlineKeyboardBuilder()
    kb.button(text=t(lang, "btn_catalog"), callback_data="shop")
    await msg.answer(t(lang, "promo_ok", pct=promo["percent"]), reply_markup=kb.as_markup())


# ---------------------------------------------------------------- checkout

@router.callback_query(F.data.startswith("pay:"))
async def pay_cb(cb: CallbackQuery, state: FSMContext, bot: Bot, db: DB, cfg: Config,
                 prices: PriceFeed, orders: Orders):
    _, kind, target, method = cb.data.split(":")
    uid = cb.from_user.id
    lang = await lang_of(db, cb.from_user)
    if kind == "pl":
        plan = await db.plan(target)
        product = await db.product(plan["product_id"]) if plan else None
        ok = plan and plan["active"] and product and product["active"]
        base = plan["price_usd"] if ok else 0
    else:
        plan, product = None, await db.product(target)
        ok = product and product["active"] and product["kind"] == "digital"
        base = product["price_usd"] if ok else 0
    if not ok or (method not in cfg.tokens and not (method == "XTR" and cfg.stars_enabled)):
        return await cb.answer(t(lang, "unavailable"), show_alert=True)

    pct, src = await discount(state, db, uid)
    usd = discounted(base, pct)
    title = product["title"] + (f" · {plan['title']}" if plan else "")
    days = plan["days"] if plan else 0
    plan_id = plan["id"] if plan else None

    if usd <= 0:  # 100% discount: grant immediately
        if src and src != "WINBACK":
            await db.use_promo(src)
        await state.update_data(promo=None)
        await cb.answer()
        await orders.grant(uid, product["id"], plan_id, days, t(lang, "free_promo", title=html.escape(title),
                                                                  code=html.escape(src or "")))
        await orders.access.notify_admins(f"🎁 Free activation via {src}: <code>{uid}</code> — {html.escape(title)}")
        return

    if method == "XTR":
        stars = stars_for(cfg, usd)
        inv = await db.open_invoice(uid, product["id"], plan_id, "XTR", src)
        if not inv or inv["amount_units"] != stars:
            inv_id = await db.create_invoice(uid, product["id"], plan_id, title, days, "XTR", stars, usd,
                                             new_reference(), src, cfg.invoice_ttl_min)
            inv = await db.get_invoice(inv_id)
        await cb.answer()
        await bot.send_invoice(
            uid, title=title[:32], description=t(lang, "stars_desc", title=title, duration=duration(days, lang))[:255],
            payload=f"inv:{inv['id']}", currency="XTR", prices=[LabeledPrice(label=title[:32], amount=stars)],
        )
        return

    inv = await db.open_invoice(uid, product["id"], plan_id, method, src)
    if not inv:
        await cb.answer("…")
        try:
            sol_price = await prices.sol_usd() if method == "SOL" else None
            amount = quote_amount(method, usd, sol_price, await db.taken_amounts(method))
        except Exception as e:  # noqa: BLE001
            log.error("quote failed: %s", e)
            return await bot.send_message(uid, t(lang, "invoice_error"))
        inv_id = await db.create_invoice(uid, product["id"], plan_id, title, days, method, amount, usd,
                                         new_reference(), src, cfg.invoice_ttl_min)
        inv = await db.get_invoice(inv_id)
    else:
        await cb.answer()

    amount_str = units_to_str(inv["amount_units"], TOKEN_DECIMALS[method])
    mint = None if method == "SOL" else cfg.mints[method]
    url = pay_url(cfg.receiver, amount_str, inv["reference"], mint, cfg.project_name, f"{title} #{inv['id']}")
    buf = io.BytesIO()
    qrcode.make(url, border=2).save(buf, format="PNG")
    caption = t(lang, "invoice", id=inv["id"], title=html.escape(title), amount=amount_str, token=method,
                usd=fmt_usd(inv["usd"]), address=cfg.receiver, mins=max(1, (inv["expires_at"] - now()) // 60))
    kb = InlineKeyboardBuilder()
    kb.button(text=t(lang, "btn_check"), callback_data=f"check:{inv['id']}")
    kb.button(text=t(lang, "btn_back_shop"), callback_data="shop")
    kb.adjust(1)
    sent = await bot.send_photo(uid, BufferedInputFile(buf.getvalue(), "invoice.png"), caption=caption,
                                reply_markup=kb.as_markup())
    await db.set_invoice_message(inv["id"], sent.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("check:"))
async def check_cb(cb: CallbackQuery, bot: Bot, db: DB, payments: Payments):
    lang = await lang_of(db, cb.from_user)
    inv = await db.get_invoice(int(cb.data.split(":")[1]))
    if not inv or inv["user_id"] != cb.from_user.id:
        return await cb.answer(t(lang, "invoice_not_found"), show_alert=True)
    if inv["status"] == "paid":
        return await cb.answer(t(lang, "already_paid"), show_alert=True)
    await cb.answer(t(lang, "checking"))
    try:
        await payments.check(only=inv["id"])
    except Exception as e:  # noqa: BLE001
        log.warning("manual check failed: %s", e)
    inv = await db.get_invoice(inv["id"])
    if inv["status"] != "paid":
        await bot.send_message(cb.from_user.id, t(lang, "not_found_yet",
                                                  amount=fmt_amount(inv["amount_units"], inv["method"])))


# Telegram Stars ---------------------------------------------------

stars_router = Router(name="stars")


def _invoice_id(payload: str) -> int | None:
    return int(payload[4:]) if payload.startswith("inv:") and payload[4:].isdigit() else None


@stars_router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, db: DB):
    inv_id = _invoice_id(q.invoice_payload)
    inv = await db.get_invoice(inv_id) if inv_id else None
    ok = bool(inv and inv["status"] == "pending" and inv["method"] == "XTR" and inv["user_id"] == q.from_user.id
              and inv["amount_units"] == q.total_amount and q.currency == "XTR")
    if ok:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message=t(await lang_of(db, q.from_user), "stars_failed"))


@stars_router.message(F.successful_payment)
async def stars_paid(msg: Message, db: DB, orders: Orders):
    sp = msg.successful_payment
    inv_id = _invoice_id(sp.invoice_payload)
    inv = await db.get_invoice(inv_id) if inv_id else None
    if not inv:
        log.error("successful payment with unknown payload %s", sp.invoice_payload)
        await orders.access.notify_admins(
            f"⚠️ Stars payment with unknown invoice from <code>{msg.from_user.id}</code>: "
            f"{sp.total_amount} ⭐, charge <code>{sp.telegram_payment_charge_id}</code>")
        return
    if inv["status"] != "pending":  # e.g. expired between checkout and payment — still honour it
        await db.reopen_invoice(inv["id"])
        inv = await db.get_invoice(inv["id"])
    await orders.fulfil(inv, f"stars:{sp.telegram_payment_charge_id}")


# ---------------------------------------------------------------- account

@router.callback_query(F.data == "account")
async def account_cb(cb: CallbackQuery, db: DB):
    uid = cb.from_user.id
    lang = await lang_of(db, cb.from_user)
    text = t(lang, "account_title") + "\n\n"
    kb = InlineKeyboardBuilder()
    subs = await db.active_subs(uid)
    for sub in subs:
        p = await db.product(sub["product_id"])
        name = p["title"] if p else sub["product_id"]
        text += t(lang, "sub_line", product=html.escape(name), date=fmt_date(sub["expires_at"], lang)) + "\n"
        if p and json.loads(p["chats"]):
            kb.button(text=f"{t(lang, 'btn_links')} · {name}", callback_data=f"links:{sub['product_id']}")
    if not subs:
        text += t(lang, "no_subs") + "\n"
    purchases = await db.user_purchases(uid)
    if purchases:
        text += "\n" + t(lang, "purchases")
        for p in purchases:
            kb.button(text=f"📦 {p['title']}", callback_data=f"get:{p['id']}")
    kb.button(text=t(lang, "btn_extend"), callback_data="shop")
    kb.button(text=t(lang, "btn_back"), callback_data="menu")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data.startswith("links:"))
async def links_cb(cb: CallbackQuery, db: DB, access: Access):
    uid, pid = cb.from_user.id, cb.data.split(":", 1)[1]
    lang = await lang_of(db, cb.from_user)
    if not await db.is_active(uid, pid):
        return await cb.answer(t(lang, "sub_inactive"), show_alert=True)
    if now() - (await db.get_sub(uid, pid))["last_link_at"] < 300:
        return await cb.answer(t(lang, "links_wait"), show_alert=True)
    await cb.answer()
    await access.send_access(uid, pid, t(lang, "fresh_links"))


@router.callback_query(F.data.startswith("get:"))
async def get_cb(cb: CallbackQuery, db: DB, access: Access):
    uid, pid = cb.from_user.id, cb.data.split(":", 1)[1]
    lang = await lang_of(db, cb.from_user)
    product = await db.product(pid)
    if not product or not await db.owns(uid, pid):
        return await cb.answer(t(lang, "unavailable"), show_alert=True)
    await cb.answer()
    await access.deliver(uid, product, "")


@router.callback_query(F.data == "trial")
async def trial_cb(cb: CallbackQuery, db: DB, cfg: Config, access: Access):
    uid = cb.from_user.id
    lang = await lang_of(db, cb.from_user)
    if not await can_trial(db, cfg, uid):
        return await cb.answer(t(lang, "trial_unavail"), show_alert=True)
    product = await trial_product(db)
    await db.mark_trial_used(uid)
    await db.extend_sub(uid, product["id"], "trial", cfg.trial_days)
    await cb.answer()
    await access.send_access(uid, product["id"], t(lang, "trial_active", days=cfg.trial_days))


# ---------------------------------------------------------------- referrals

@router.callback_query(F.data == "ref")
async def ref_cb(cb: CallbackQuery, bot: Bot, db: DB, cfg: Config):
    uid = cb.from_user.id
    u = cb.from_user
    await db.upsert_user(uid, u.username, u.first_name, detect(u.language_code))
    lang = await lang_of(db, u)
    me = await bot.me()
    user = await db.get_user(uid)
    perks = []
    if cfg.referral_percent > 0:
        perks.append(t(lang, "ref_perk_pct", pct=f"{cfg.referral_percent:g}"))
    if cfg.referral_bonus_days > 0:
        perks.append(t(lang, "ref_perk_days", days=cfg.referral_bonus_days))
    text = (t(lang, "ref_title") + "\n\n" + t(lang, "ref_you_get", perks=f" {t(lang, 'and')} ".join(perks)) + "\n\n"
            + t(lang, "ref_stats", link=f"https://t.me/{me.username}?start=ref{uid}",
                count=await db.referral_count(uid)))
    kb = InlineKeyboardBuilder()
    if cfg.referral_percent > 0:
        text += "\n" + t(lang, "ref_balance", earned=f"{user['ref_earned_cents'] / 100:.2f}",
                         balance=f"{user['ref_balance_cents'] / 100:.2f}", min=f"{cfg.min_payout_usd:g}")
        if user["ref_balance_cents"] >= cfg.min_payout_usd * 100:
            kb.button(text=t(lang, "btn_withdraw"), callback_data="withdraw")
    kb.button(text=t(lang, "btn_back"), callback_data="menu")
    kb.adjust(1)
    await show(cb, text, kb.as_markup())


@router.callback_query(F.data == "withdraw")
async def withdraw_cb(cb: CallbackQuery, state: FSMContext, db: DB):
    lang = await lang_of(db, cb.from_user)
    await state.set_state(Form.wallet)
    await show(cb, t(lang, "withdraw_ask"), back_kb(lang, "ref", "btn_cancel"))


@router.message(Form.wallet, F.text)
async def wallet_msg(msg: Message, state: FSMContext, db: DB, cfg: Config, access: Access):
    lang = await lang_of(db, msg.from_user)
    wallet = msg.text.strip()
    if not is_valid_address(wallet):
        return await msg.answer(t(lang, "wallet_bad"), reply_markup=back_kb(lang, "ref", "btn_cancel"))
    await state.set_state(None)
    user = await db.get_user(msg.from_user.id)
    if not user or user["ref_balance_cents"] < cfg.min_payout_usd * 100:
        return await msg.answer(t(lang, "below_min"), reply_markup=back_kb(lang))
    res = await db.request_payout(msg.from_user.id, wallet)
    if not res:
        return await msg.answer(t(lang, "below_min"), reply_markup=back_kb(lang))
    pid, cents = res
    await msg.answer(t(lang, "payout_submitted", id=pid, amount=f"{cents / 100:.2f}"), reply_markup=back_kb(lang))
    await access.notify_admins(
        f"💸 <b>Payout request #{pid}</b>\nUser <code>{msg.from_user.id}</code>"
        f" (@{html.escape(msg.from_user.username or '-')})\nAmount: <b>${cents / 100:.2f}</b>\n"
        f"Wallet: <code>{wallet}</code>\n\nApprove in /admin → Payouts after sending."
    )
