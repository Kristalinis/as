"""End-to-end flows with a fake Telegram API session and a fake Solana RPC."""
import asyncio
import os
import re
import string
from datetime import datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery, Chat, ChatInviteLink, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Document, Message,
    MessageId, PreCheckoutQuery, SuccessfulPayment, Update, User,
)

from bot.__main__ import build
from bot.config import KNOWN_MINTS, Config
from bot.db import DB, now
from bot.handlers import panel
from bot.i18n import LANGS, T
from bot.solana import b58encode
from tests.test_solana import sol_tx, spl_tx

ADMIN, BUYER, REFERRER = 1, 1001, 2002
CHAT, BASIC_CHAT = -1001234567890, -1009999999999
USDC = KNOWN_MINTS["USDC"]
ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}

CATALOG = {
    "products": [
        {"id": "vip", "kind": "subscription", "title": "VIP & Co", "chats": [CHAT, BASIC_CHAT],
         "plans": [{"id": "vip_m1", "title": "1 Month", "days": 30, "price_usd": 19},
                   {"id": "vip_m3", "title": "3 Months", "days": 90, "price_usd": 49},
                   {"id": "vip_life", "title": "Lifetime", "days": 0, "price_usd": 299}]},
        {"id": "basic", "kind": "subscription", "title": "Basic", "chats": [BASIC_CHAT],
         "plans": [{"id": "basic_m1", "title": "1 Month", "days": 30, "price_usd": 10}]},
        {"id": "guide", "kind": "digital", "title": "Guide <PDF>", "price_usd": 9, "content": "https://secret.example"},
    ]
}


def assert_valid_html(text, parse_mode) -> None:
    if not text or parse_mode is None:
        return
    for _ in re.finditer(r"&(?!(amp|lt|gt|quot|#\d+);)", text):
        raise AssertionError(f"unescaped & in: {text!r}")
    for tag in re.findall(r"</?([a-zA-Z-]+)", text):
        assert tag in ALLOWED_TAGS, f"unsupported tag <{tag}> in {text!r}"


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.mid = 100

    def of(self, name: str, **match):
        return [c for c in self.calls if type(c).__name__ == name
                and all(getattr(c, k, None) == v for k, v in match.items())]

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        pm = getattr(method, "parse_mode", "HTML")
        assert_valid_html(getattr(method, "text", None), pm)
        assert_valid_html(getattr(method, "caption", None), pm)
        for markup in [getattr(method, "reply_markup", None)]:
            for row in getattr(markup, "inline_keyboard", None) or []:
                for b in row:
                    assert b.callback_data is None or len(b.callback_data.encode()) <= 64
        if name == "GetMe":
            return User(id=42, is_bot=True, first_name="Bot", username="test_bot")
        if name in ("SendMessage", "SendPhoto", "SendInvoice", "SendDocument", "SendVideo", "SendAudio"):
            self.mid += 1
            return Message(message_id=self.mid, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"),
                           text=getattr(method, "text", None), caption=getattr(method, "caption", None))
        if name == "CopyMessage":
            return MessageId(message_id=1)
        if name == "CreateChatInviteLink":
            return ChatInviteLink(invite_link=f"https://t.me/+link{len(self.calls)}",
                                  creator=User(id=42, is_bot=True, first_name="Bot"),
                                  creates_join_request=False, is_primary=False, is_revoked=False, member_limit=1)
        if name == "GetChat":
            return SimpleNamespace(title=f"Chat {method.chat_id}")
        if name == "GetChatMember":
            return ChatMemberMember(user=User(id=method.user_id, is_bot=False, first_name="x"))
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""


class FakeRPC:
    def __init__(self):
        self.sigs: dict[str, list[dict]] = {}
        self.txs: dict[str, dict] = {}
        self.token_account = b58encode(os.urandom(32))

    def add(self, tx: dict, *addresses: str) -> str:
        sig = b58encode(os.urandom(64))
        if tx.get("blockTime") is None:
            tx["blockTime"] = now()
        self.txs[sig] = tx
        for a in addresses:
            self.sigs.setdefault(a, []).insert(0, {"signature": sig, "err": None, "blockTime": tx["blockTime"]})
        return sig

    async def signatures(self, address, limit=25, until=None, before=None):
        out, started = [], before is None
        for s in self.sigs.get(address, []):
            if not started:
                started = s["signature"] == before
                continue
            if s["signature"] == until:
                break
            out.append(s)
        return out[:limit]

    async def transaction(self, sig):
        return self.txs.get(sig)

    async def token_accounts(self, owner, mint):
        return [self.token_account]


class FakePrices:
    async def sol_usd(self):
        return 150.0


@pytest.fixture
async def env(tmp_path):
    receiver = b58encode(os.urandom(32))
    cfg = Config(
        bot_token="42:TEST", admin_ids={ADMIN}, receiver=receiver, rpc_url="", tokens=["SOL", "USDC"],
        project_name="Alpha & Co", db_path=str(tmp_path / "t.db"), trial_days=3, referral_percent=20,
        referral_bonus_days=7, stars_per_usd=50, winback_percent=20,
    )
    db = DB(cfg.db_path)
    await db.connect()
    await db.seed_catalog(CATALOG, [])
    session = FakeSession()
    bot = Bot(cfg.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    rpc = FakeRPC()
    dp, payments, jobs = build(cfg, bot, db, rpc, FakePrices())
    yield SimpleNamespace(cfg=cfg, db=db, bot=bot, s=session, rpc=rpc, dp=dp, payments=payments, jobs=jobs)
    await db.close()
    for r in dp.sub_routers:  # module-level routers can only be attached to one dispatcher
        r._parent_router = None


_uid = [1]


def _next():
    _uid[0] += 1
    return _uid[0]


def user(uid, lang="en"):
    return User(id=uid, is_bot=False, first_name=f"U{uid}", username=f"user{uid}", language_code=lang)


async def send(e, uid, text=None, lang="en", **kw):
    msg = Message(message_id=_next(), date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=user(uid, lang), text=text, **kw)
    await e.dp.feed_update(e.bot, Update(update_id=_next(), message=msg))


async def press(e, uid, data, lang="en"):
    msg = Message(message_id=_next(), date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=User(id=42, is_bot=True, first_name="Bot"), text="menu")
    cb = CallbackQuery(id=str(_next()), from_user=user(uid, lang), chat_instance="x", data=data, message=msg)
    await e.dp.feed_update(e.bot, Update(update_id=_next(), callback_query=cb))


def texts_to(e, uid):
    return [c.text for c in e.s.calls
            if type(c).__name__ in ("SendMessage", "EditMessageText") and c.chat_id == uid]


def buttons(e, uid):
    """Callback data / labels of the last message's keyboard sent to uid."""
    msgs = [c for c in e.s.calls if type(c).__name__ in ("SendMessage", "EditMessageText", "SendPhoto")
            and c.chat_id == uid]
    kb = msgs[-1].reply_markup
    return [(b.text, b.callback_data or b.url) for row in kb.inline_keyboard for b in row] if kb else []


# ------------------------------------------------------------------------------------------------ tests

async def test_crypto_purchase_upsell_referral_expiry(env):
    e = env
    await send(e, REFERRER, "/start")
    await send(e, BUYER, f"/start ref{REFERRER}")
    assert (await e.db.get_user(BUYER))["referrer_id"] == REFERRER
    assert "Alpha &amp; Co" in texts_to(e, BUYER)[-1]

    await press(e, BUYER, "shop")
    labels = [b[0] for b in buttons(e, BUYER)]
    assert any("VIP & Co — $19+" in x for x in labels) and any("📦 Guide <PDF> — $9" in x for x in labels)
    await press(e, BUYER, "prod:vip")
    await press(e, BUYER, "plan:vip_m1")
    assert "save <b>14%</b>" in texts_to(e, BUYER)[-1]  # upsell 3 months
    assert ("⬆️ 3 Months — save 14%", "plan:vip_m3") in buttons(e, BUYER)
    assert any(b[1] == "pay:pl:vip_m1:XTR" and "950" in b[0] for b in buttons(e, BUYER))

    await press(e, BUYER, "pay:pl:vip_m1:USDC")
    photo = e.s.of("SendPhoto", chat_id=BUYER)[-1]
    inv = (await e.db.pending_invoices())[-1]
    assert inv["method"] == "USDC" and 19_000_000 < inv["amount_units"] < 19_010_000
    assert f"<code>{e.cfg.receiver}</code>" in photo.caption
    await press(e, BUYER, "pay:pl:vip_m1:USDC")  # reuses the open invoice
    assert len(await e.db.pending_invoices()) == 1

    await e.payments.check()  # initialises cursors
    sig = e.rpc.add(spl_tx(b58encode(os.urandom(32)), e.cfg.receiver, USDC, inv["amount_units"]), e.rpc.token_account)
    e.rpc.add(spl_tx(b58encode(os.urandom(32)), e.cfg.receiver, USDC, inv["amount_units"] + 10), e.rpc.token_account)
    await e.payments.check()

    paid = await e.db.get_invoice(inv["id"])
    assert paid["status"] == "paid" and paid["signature"] == sig
    assert await e.db.is_active(BUYER, "vip")
    assert e.s.of("CreateChatInviteLink", chat_id=CHAT) and e.s.of("CreateChatInviteLink", chat_id=BASIC_CHAT)
    assert any("Payment received" in x for x in texts_to(e, BUYER))
    assert any("New sale" in x for x in texts_to(e, ADMIN))
    assert (await e.db.get_user(REFERRER))["ref_balance_cents"] == 380  # 20% of $19
    assert await e.db.is_active(REFERRER, "vip")  # 7 bonus days
    assert not await e.db.mark_paid(inv["id"], sig)

    # SOL via wallet with Solana Pay reference; renewal stacks
    exp_before = (await e.db.get_sub(BUYER, "vip"))["expires_at"]
    await press(e, BUYER, "pay:pl:vip_m3:SOL")
    inv2 = (await e.db.pending_invoices())[-1]
    e.rpc.add(sol_tx(b58encode(os.urandom(32)), e.cfg.receiver, inv2["amount_units"], extra_keys=[inv2["reference"]]),
              inv2["reference"])
    await press(e, BUYER, f"check:{inv2['id']}")
    assert (await e.db.get_invoice(inv2["id"]))["status"] == "paid"
    assert (await e.db.get_sub(BUYER, "vip"))["expires_at"] == exp_before + 90 * 86400

    await press(e, BUYER, "account")
    assert "VIP &amp; Co" in texts_to(e, BUYER)[-1]
    await press(e, REFERRER, "ref")
    assert "start=ref2002" in texts_to(e, REFERRER)[-1]

    # buyer also holds Basic; when VIP expires the shared BASIC_CHAT must be kept
    await e.db.extend_sub(BUYER, "basic", None, 30)
    await e.db.conn.execute("UPDATE subscriptions SET expires_at=? WHERE user_id=? AND product_id='vip'",
                            (now() - 1, BUYER))
    await e.db.conn.commit()
    await e.jobs.tick()
    assert not await e.db.is_active(BUYER, "vip")
    assert e.s.of("BanChatMember", chat_id=CHAT, user_id=BUYER)
    assert not e.s.of("BanChatMember", chat_id=BASIC_CHAT, user_id=BUYER)
    assert "expired" in texts_to(e, BUYER)[-1]


async def test_stars_digital_refund(env):
    e = env
    await send(e, BUYER, "/start")
    await press(e, BUYER, "prod:guide")
    assert ("⭐ Pay with Telegram Stars (450 ⭐)", "pay:pr:guide:XTR") in buttons(e, BUYER)
    await press(e, BUYER, "pay:pr:guide:XTR")
    si = e.s.of("SendInvoice", chat_id=BUYER)[-1]
    assert si.currency == "XTR" and si.prices[0].amount == 450
    inv_id = int(si.payload[4:])

    async def precheckout(amount):
        q = PreCheckoutQuery(id=str(_next()), from_user=user(BUYER), currency="XTR", total_amount=amount,
                             invoice_payload=si.payload)
        await e.dp.feed_update(e.bot, Update(update_id=_next(), pre_checkout_query=q))
        return e.s.of("AnswerPreCheckoutQuery")[-1]

    assert (await precheckout(1)).ok is False
    assert (await precheckout(450)).ok is True
    sp = SuccessfulPayment(currency="XTR", total_amount=450, invoice_payload=si.payload,
                           telegram_payment_charge_id="charge_1", provider_payment_charge_id="")
    await send(e, BUYER, successful_payment=sp)
    await send(e, BUYER, successful_payment=sp)  # duplicate update is ignored
    assert (await e.db.get_invoice(inv_id))["status"] == "paid"
    assert await e.db.owns(BUYER, "guide")
    delivered = [c for c in e.s.of("SendMessage", chat_id=BUYER) if c.text == "https://secret.example"]
    assert len(delivered) == 1 and delivered[0].parse_mode is None
    await press(e, BUYER, "account")
    assert ("📦 Guide <PDF>", "get:guide") in buttons(e, BUYER)
    await press(e, BUYER, "get:guide")
    assert len([c for c in e.s.of("SendMessage", chat_id=BUYER) if c.text == "https://secret.example"]) == 2

    await send(e, ADMIN, f"/refund {inv_id}")
    assert e.s.of("RefundStarPayment", user_id=BUYER, telegram_payment_charge_id="charge_1")
    assert not await e.db.owns(BUYER, "guide")
    assert "refunded" in texts_to(e, BUYER)[-1]


async def test_winback_offer(env):
    e = env
    await send(e, BUYER, "/start")
    await e.db.extend_sub(BUYER, "vip", "vip_m1", 30)
    await e.db.conn.execute("UPDATE subscriptions SET expires_at=? WHERE user_id=?", (now() - 1, BUYER))
    await e.db.conn.commit()
    await e.jobs.tick()  # expiry
    assert "expired" in texts_to(e, BUYER)[-1]
    await e.jobs.tick()  # too early for win-back
    assert "expired" in texts_to(e, BUYER)[-1]

    await e.db.conn.execute("UPDATE subscriptions SET ended_at=? WHERE user_id=?", (now() - 86401, BUYER))
    await e.db.conn.commit()
    await e.jobs.tick()
    assert "20% off" in texts_to(e, BUYER)[-1]
    assert ("🔥 Claim 20% off", "prod:vip") in buttons(e, BUYER)
    await e.jobs.tick()  # stage 1 not repeated
    assert sum("20% off" in x for x in texts_to(e, BUYER)) == 1

    await press(e, BUYER, "prod:vip")
    assert "comeback discount" in texts_to(e, BUYER)[-1]
    await press(e, BUYER, "pay:pl:vip_m1:USDC")
    inv = (await e.db.pending_invoices())[-1]
    assert inv["usd"] == 15.2 and inv["discount"] == "WINBACK"
    await e.payments.orders.fulfil(inv, "sigX")
    assert (await e.db.get_user(BUYER))["winback_pct"] == 0

    # 7-day nudge for someone else who never came back
    await send(e, 3003, "/start")
    await e.db.extend_sub(3003, "basic", "basic_m1", 30)
    await e.db.deactivate_sub(3003, "basic")
    await e.db.conn.execute("UPDATE subscriptions SET ended_at=? WHERE user_id=3003", (now() - 8 * 86400,))
    await e.db.conn.commit()
    await e.jobs.tick()
    assert sum("20% off" in x for x in texts_to(e, 3003)) == 1  # only the 7-day message, not both


async def test_languages(env):
    e = env
    for key, entry in T.items():
        assert set(entry) == set(LANGS), key
        fields = {lang: {f for _, f, _, _ in string.Formatter().parse(v) if f} for lang, v in entry.items()}
        assert len({frozenset(v) for v in fields.values()}) == 1, f"placeholder mismatch in {key}"

    await send(e, 5005, "/start", lang="lt")
    assert "Sveiki atvykę" in texts_to(e, 5005)[-1]
    await send(e, 6006, "/start", lang="uk")
    assert "Добро пожаловать" in texts_to(e, 6006)[-1]
    await send(e, 7007, "/start", lang="de")
    assert "Welcome" in texts_to(e, 7007)[-1]
    await press(e, 7007, "lang")
    await press(e, 7007, "setlang:lt")
    assert (await e.db.get_user(7007))["lang"] == "lt"
    await press(e, 7007, "plan:vip_m1")
    assert "Kaip norėtumėte mokėti" in texts_to(e, 7007)[-1]


async def test_admin_panel(env):
    e = env
    await send(e, BUYER, "/start")
    await send(e, BUYER, "/admin")  # non-admin: ignored by the panel
    assert not any("Admin panel" in (x or "") for x in texts_to(e, BUYER))

    await send(e, ADMIN, "/start")
    await send(e, ADMIN, "/admin")
    assert "Admin panel" in texts_to(e, ADMIN)[-1]
    await press(e, ADMIN, "a:prods")
    await press(e, ADMIN, "a:prod:vip")
    assert "Chat -1001234567890" in texts_to(e, ADMIN)[-1]

    # change a price
    await press(e, ADMIN, "a:ask:plan_price:vip_m1")
    await send(e, ADMIN, "abc")
    assert "Send a number" in texts_to(e, ADMIN)[-1]
    await send(e, ADMIN, "25")
    assert (await e.db.plan("vip_m1"))["price_usd"] == 25

    # add a plan, hide it
    await press(e, ADMIN, "a:ask:plan_new:vip")
    await send(e, ADMIN, "6 Months | 180 | 99")
    new_plan = [p for p in await e.db.plans("vip") if p["title"] == "6 Months"][0]
    await press(e, ADMIN, f"a:pltog:{new_plan['id']}")
    assert all(p["title"] != "6 Months" for p in await e.db.plans("vip"))

    # add chats by forwarding a channel post
    from aiogram.types import MessageOriginChannel
    await press(e, ADMIN, "a:ask:prod_chats:basic")
    await send(e, ADMIN, "post", forward_origin=MessageOriginChannel(
        date=datetime.now(), chat=Chat(id=-100777, type="channel", title="New"), message_id=5))
    assert -100777 in __import__("json").loads((await e.db.product("basic"))["chats"])

    # new digital item with a file
    await press(e, ADMIN, "a:ask:new_digital:-")
    await send(e, ADMIN, "Ebook | 5")
    await send(e, ADMIN, document=Document(file_id="FILE1", file_unique_id="U1"))
    ebook = [p for p in await e.db.products() if p["title"] == "Ebook"][0]
    assert ebook["file_id"] == "FILE1" and ebook["price_usd"] == 5

    # promo + settings
    await press(e, ADMIN, "a:ask:promo_new:-")
    await send(e, ADMIN, "SALE 30 10 7")
    assert (await e.db.valid_promo("sale"))["percent"] == 30
    await press(e, ADMIN, "a:ask:set:winback_percent")
    await send(e, ADMIN, "35")
    assert e.cfg.winback_percent == 35 and await e.db.kv_get("setting:winback_percent") == "35"
    e.cfg.winback_percent = 1
    await panel.load_settings(e.db, e.cfg)
    assert e.cfg.winback_percent == 35

    # a command cancels a pending input
    await press(e, ADMIN, "a:ask:plan_price:vip_m1")
    await send(e, ADMIN, "/admin")
    assert "Admin panel" in texts_to(e, ADMIN)[-1]
    assert (await e.db.plan("vip_m1"))["price_usd"] == 25

    # stats with chart
    await e.db.create_invoice(BUYER, "vip", "vip_m1", "VIP", 30, "USDC", 19_000_010, 19.0, "r1", None, 30)
    await e.db.mark_paid(1, "s1")
    await press(e, ADMIN, "a:stats")
    photo = e.s.of("SendPhoto", chat_id=ADMIN)[-1]
    assert photo.photo.data.startswith(b"\x89PNG") and "$19.00" in photo.caption

    # broadcast from the panel
    await press(e, ADMIN, "a:bc")
    await send(e, ADMIN, "Big news!")
    await press(e, ADMIN, "a:bcs:all")
    await asyncio.sleep(0.3)
    assert len(e.s.of("CopyMessage")) == 2  # buyer + admin

    # grant / revoke commands
    await send(e, ADMIN, f"/grant @user{BUYER} 10 basic")
    assert await e.db.is_active(BUYER, "basic")
    await send(e, ADMIN, f"/user {BUYER}")
    assert "active until" in texts_to(e, ADMIN)[-1]
    await send(e, ADMIN, f"/revoke {BUYER}")
    assert not await e.db.is_active(BUYER, "basic")


async def test_promo_trial_guard(env):
    e = env
    await e.db.add_promo("FREE", 100, 1, 0)
    await e.db.add_promo("HALF", 50, 0, 0)
    await send(e, BUYER, "/start")
    await press(e, BUYER, "promo")
    await send(e, BUYER, "half")
    assert "-50%" in texts_to(e, BUYER)[-1]
    await press(e, BUYER, "pay:pl:vip_m1:USDC")
    inv = (await e.db.pending_invoices())[-1]
    assert inv["usd"] == 9.5 and inv["discount"] == "HALF"

    await press(e, BUYER, "promo")
    await send(e, BUYER, "nope")
    assert "invalid" in texts_to(e, BUYER)[-1]
    await send(e, BUYER, "FREE")
    await press(e, BUYER, "pay:pl:vip_life:SOL")
    assert await e.db.is_active(BUYER, "vip")
    assert (await e.db.valid_promo("FREE")) is None

    await send(e, 3003, "/start")
    await press(e, 3003, "trial")
    assert await e.db.is_active(3003, "vip")
    await press(e, 3003, "trial")
    assert (await e.db.get_sub(3003, "vip"))["expires_at"] <= now() + 3 * 86400

    upd = ChatMemberUpdated(
        chat=Chat(id=CHAT, type="channel", title="VIP"), from_user=user(4004), date=datetime.now(),
        old_chat_member=ChatMemberLeft(user=user(4004)), new_chat_member=ChatMemberMember(user=user(4004)),
    )
    await e.dp.feed_update(e.bot, Update(update_id=_next(), chat_member=upd))
    assert e.s.of("BanChatMember", chat_id=CHAT, user_id=4004)
    upd2 = upd.model_copy(update={"old_chat_member": ChatMemberLeft(user=user(BUYER)),
                                  "new_chat_member": ChatMemberMember(user=user(BUYER))})
    await e.dp.feed_update(e.bot, Update(update_id=_next(), chat_member=upd2))
    assert not e.s.of("BanChatMember", chat_id=CHAT, user_id=BUYER)


async def test_referral_payout(env):
    e = env
    await send(e, REFERRER, "/start")
    await e.db.add_ref_commission(REFERRER, 2500)
    await press(e, REFERRER, "withdraw")
    await send(e, REFERRER, "not-a-wallet")
    assert "doesn't look like" in texts_to(e, REFERRER)[-1]
    wallet = b58encode(os.urandom(32))
    await send(e, REFERRER, wallet)
    assert "Payout request #1" in texts_to(e, REFERRER)[-1]
    assert (await e.db.get_user(REFERRER))["ref_balance_cents"] == 0
    await press(e, ADMIN, "a:payouts")
    assert wallet in texts_to(e, ADMIN)[-1]
    await press(e, ADMIN, "a:paid:1")
    assert "was sent" in texts_to(e, REFERRER)[-1]
