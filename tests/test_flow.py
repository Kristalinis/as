"""End-to-end flow with a fake Telegram API session and a fake Solana RPC."""
import os
import re
from datetime import datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery, Chat, ChatInviteLink, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message,
    MessageId, Update, User,
)

from bot.__main__ import build
from bot.config import KNOWN_MINTS, Config, Plan
from bot.db import DB, now
from bot.solana import b58encode
from tests.test_solana import sol_tx, spl_tx

ADMIN, BUYER, REFERRER = 1, 1001, 2002
CHAT = -1001234567890
USDC = KNOWN_MINTS["USDC"]
ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}


def assert_valid_html(text: str | None) -> None:
    if not text:
        return
    for m in re.finditer(r"&(?!(amp|lt|gt|quot|#\d+);)", text):
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
                and all(getattr(c, k) == v for k, v in match.items())]

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        assert_valid_html(getattr(method, "text", None))
        assert_valid_html(getattr(method, "caption", None))
        if name == "GetMe":
            return User(id=42, is_bot=True, first_name="Bot", username="test_bot")
        if name in ("SendMessage", "SendPhoto"):
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
            return SimpleNamespace(title="VIP Room")
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
        tx.setdefault("blockTime", now())
        if tx["blockTime"] is None:
            tx["blockTime"] = now()
        self.txs[sig] = tx
        for a in addresses:
            self.sigs.setdefault(a, []).insert(0, {"signature": sig, "err": None, "blockTime": tx["blockTime"]})
        return sig

    async def signatures(self, address, limit=25, until=None, before=None):
        out = []
        started = before is None
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
        premium_chats=[CHAT], project_name="Alpha & Co",
        plans=[Plan("m1", "1 Month", 30, 19.0, "desc <30 days>"), Plan("m3", "3 Months", 90, 49.0),
               Plan("life", "Lifetime", 0, 299.0)],
        db_path=str(tmp_path / "t.db"), trial_days=3, referral_percent=20, referral_bonus_days=7,
    )
    db = DB(cfg.db_path)
    await db.connect()
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


def user(uid):
    return User(id=uid, is_bot=False, first_name=f"U{uid}", username=f"user{uid}")


async def send_text(e, uid, text, reply_to=None):
    msg = Message(message_id=_next(), date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=user(uid), text=text, reply_to_message=reply_to)
    await e.dp.feed_update(e.bot, Update(update_id=_next(), message=msg))


async def press(e, uid, data):
    msg = Message(message_id=_next(), date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=User(id=42, is_bot=True, first_name="Bot"), text="menu")
    cb = CallbackQuery(id=str(_next()), from_user=user(uid), chat_instance="x", data=data, message=msg)
    await e.dp.feed_update(e.bot, Update(update_id=_next(), callback_query=cb))


def texts_to(e, uid):
    return [c.text for c in e.s.calls
            if type(c).__name__ in ("SendMessage", "EditMessageText") and c.chat_id == uid]


async def test_full_purchase_flow(env):
    e = env
    await send_text(e, REFERRER, "/start")
    await send_text(e, BUYER, f"/start ref{REFERRER}")
    assert (await e.db.get_user(BUYER))["referrer_id"] == REFERRER
    assert "Alpha &amp; Co" in texts_to(e, BUYER)[-1]

    await press(e, BUYER, "plans")
    await press(e, BUYER, "plan:m1")
    await press(e, BUYER, "pay:m1:USDC")
    photo = e.s.of("SendPhoto", chat_id=BUYER)[-1]
    inv = (await e.db.pending_invoices())[-1]
    assert inv["token"] == "USDC" and 19_000_000 < inv["amount_units"] < 19_010_000
    assert f"<code>{e.cfg.receiver}</code>" in photo.caption
    # pressing pay again reuses the open invoice
    await press(e, BUYER, "pay:m1:USDC")
    assert len(await e.db.pending_invoices()) == 1

    # payment arrives from an exchange: no reference, exact amount, into the receiver's token account
    await e.payments.check()  # initialises cursors
    sig = e.rpc.add(spl_tx(b58encode(os.urandom(32)), e.cfg.receiver, USDC, inv["amount_units"]),
                    e.rpc.token_account)
    # a wrong amount must not match
    e.rpc.add(spl_tx(b58encode(os.urandom(32)), e.cfg.receiver, USDC, inv["amount_units"] + 10),
              e.rpc.token_account)
    await e.payments.check()

    paid = await e.db.get_invoice(inv["id"])
    assert paid["status"] == "paid" and paid["signature"] == sig
    assert await e.db.is_active(BUYER)
    assert e.s.of("CreateChatInviteLink", chat_id=CHAT)
    assert any("Payment received" in t for t in texts_to(e, BUYER))
    assert any("New sale" in t for t in texts_to(e, ADMIN))
    ref = await e.db.get_user(REFERRER)
    assert ref["ref_balance_cents"] == 380  # 20% of $19
    assert await e.db.is_active(REFERRER)  # 7 bonus days

    # the same signature can never pay twice
    assert not await e.db.mark_paid(inv["id"], sig)

    # SOL payment through a wallet with a Solana Pay reference; renewal stacks on top
    exp_before = (await e.db.get_sub(BUYER))["expires_at"]
    await press(e, BUYER, "pay:m3:SOL")
    inv2 = (await e.db.pending_invoices())[-1]
    assert inv2["token"] == "SOL"
    e.rpc.add(sol_tx(b58encode(os.urandom(32)), e.cfg.receiver, inv2["amount_units"], extra_keys=[inv2["reference"]]),
              inv2["reference"])
    await press(e, BUYER, f"check:{inv2['id']}")
    assert (await e.db.get_invoice(inv2["id"]))["status"] == "paid"
    assert (await e.db.get_sub(BUYER))["expires_at"] == exp_before + 90 * 86400

    # my subscription + links (rate-limited because links were just sent)
    await press(e, BUYER, "mysub")
    assert "Active" in texts_to(e, BUYER)[-1]
    await press(e, BUYER, "ref")
    await press(e, REFERRER, "ref")
    assert "start=ref2002" in texts_to(e, REFERRER)[-1]

    # admin stats
    await send_text(e, ADMIN, "/stats")
    assert "$68.00" in texts_to(e, ADMIN)[-1]

    # expiry removes the user from the chat
    await e.db.conn.execute("UPDATE subscriptions SET expires_at=? WHERE user_id=?", (now() - 1, BUYER))
    await e.db.conn.commit()
    await e.jobs.tick()
    assert not await e.db.is_active(BUYER)
    assert e.s.of("BanChatMember", chat_id=CHAT, user_id=BUYER)
    assert e.s.of("UnbanChatMember", chat_id=CHAT, user_id=BUYER)


async def test_promo_trial_guard_admin(env):
    e = env
    await send_text(e, ADMIN, "/promo FREE 100 1")
    await send_text(e, ADMIN, "/promo HALF 50")
    await send_text(e, BUYER, "/start")

    await press(e, BUYER, "promo")
    await send_text(e, BUYER, "half")
    assert "-50%" in texts_to(e, BUYER)[-1]
    await press(e, BUYER, "pay:m1:USDC")
    inv = (await e.db.pending_invoices())[-1]
    assert inv["usd"] == 9.5 and inv["promo"] == "HALF"

    await press(e, BUYER, "promo")
    await send_text(e, BUYER, "nope")
    assert "invalid" in texts_to(e, BUYER)[-1]
    await send_text(e, BUYER, "FREE")
    await press(e, BUYER, "pay:life:SOL")
    assert await e.db.is_active(BUYER)
    assert (await e.db.valid_promo("FREE")) is None  # single use consumed

    # trial: only for users who never had a subscription
    await send_text(e, 3003, "/start")
    await press(e, 3003, "trial")
    assert await e.db.is_active(3003)
    await press(e, 3003, "trial")
    assert (await e.db.get_sub(3003))["expires_at"] <= now() + 3 * 86400

    # membership guard kicks a non-subscriber joining the premium chat
    upd = ChatMemberUpdated(
        chat=Chat(id=CHAT, type="channel", title="VIP"), from_user=user(4004), date=datetime.now(),
        old_chat_member=ChatMemberLeft(user=user(4004)), new_chat_member=ChatMemberMember(user=user(4004)),
    )
    await e.dp.feed_update(e.bot, Update(update_id=_next(), chat_member=upd))
    assert e.s.of("BanChatMember", chat_id=CHAT, user_id=4004)
    upd2 = upd.model_copy(update={"from_user": user(BUYER), "old_chat_member": ChatMemberLeft(user=user(BUYER)),
                                  "new_chat_member": ChatMemberMember(user=user(BUYER))})
    await e.dp.feed_update(e.bot, Update(update_id=_next(), chat_member=upd2))
    assert not e.s.of("BanChatMember", chat_id=CHAT, user_id=BUYER)

    # admin commands
    await send_text(e, ADMIN, f"/grant @user{3003} 10")
    await send_text(e, ADMIN, f"/user {3003}")
    assert "active until" in texts_to(e, ADMIN)[-1]
    await send_text(e, ADMIN, "/revoke 3003")
    assert not await e.db.is_active(3003)
    await send_text(e, ADMIN, "/promos")
    assert "HALF" in texts_to(e, ADMIN)[-1]
    await send_text(e, ADMIN, "/admin")
    # non-admins can't use admin commands
    await send_text(e, BUYER, "/grant 1001 life")
    assert (await e.db.get_sub(BUYER))["plan_id"] == "life"

    # broadcast (reply to a message)
    src = Message(message_id=5, date=datetime.now(), chat=Chat(id=ADMIN, type="private"), text="hi")
    await send_text(e, ADMIN, "/broadcast all", reply_to=src)
    import asyncio
    await asyncio.sleep(0.5)
    assert len(e.s.of("CopyMessage")) >= 2


async def test_referral_payout(env):
    e = env
    await send_text(e, REFERRER, "/start")
    await e.db.add_ref_commission(REFERRER, 2500)
    await press(e, REFERRER, "withdraw")
    await send_text(e, REFERRER, "not-a-wallet")
    assert "doesn't look like" in texts_to(e, REFERRER)[-1]
    wallet = b58encode(os.urandom(32))
    await send_text(e, REFERRER, wallet)
    assert "Payout request #1" in texts_to(e, REFERRER)[-1]
    assert (await e.db.get_user(REFERRER))["ref_balance_cents"] == 0
    await send_text(e, ADMIN, "/payouts")
    assert wallet in texts_to(e, ADMIN)[-1]
    await send_text(e, ADMIN, "/paidout 1")
    assert "was sent" in texts_to(e, REFERRER)[-1]
