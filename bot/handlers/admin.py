"""Admin commands and premium-chat membership guard."""
from __future__ import annotations

import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import JOIN_TRANSITION, BaseFilter, ChatMemberUpdatedFilter, Command, CommandObject
from aiogram.types import ChatMemberUpdated, Message

from ..config import Config
from ..db import DB, now
from ..services import Access, fmt_amount, fmt_date

router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, msg: Message, cfg: Config) -> bool:
        return bool(msg.from_user) and cfg.is_admin(msg.from_user.id)


router.message.filter(IsAdmin())

HELP = """<b>Admin commands</b>
/stats — revenue, users, subscriptions
/user &lt;id|@username&gt; — user details
/grant &lt;id|@username&gt; &lt;days|life&gt; — give / extend access
/revoke &lt;id|@username&gt; — remove access now
/promo &lt;CODE&gt; &lt;percent&gt; [max_uses] [valid_days] — create promo (100% = free)
/promos — list promo codes
/delpromo &lt;CODE&gt; — delete promo code
/broadcast [all|active|inactive] — reply to any message to send it to users
/payouts — pending referral payouts
/paidout &lt;id&gt; — mark payout as sent
/chatid — run inside a group to see its id (for a channel: forward any channel post here)"""


@router.message(Command("admin"))
async def admin_help(msg: Message):
    await msg.answer(HELP)


@router.message(Command("chatid"))
async def chat_id(msg: Message):
    await msg.answer(f"Chat id: <code>{msg.chat.id}</code>")


@router.message(F.chat.type == "private", F.forward_origin.chat.id)
async def forwarded_chat_id(msg: Message):
    chat = msg.forward_origin.chat
    await msg.answer(f"{html.escape(chat.title or '')} id: <code>{chat.id}</code>")


@router.message(Command("stats"))
async def stats(msg: Message, db: DB):
    s = await db.stats()
    lines = [
        "📊 <b>Stats</b>",
        f"Users: <b>{s['users']}</b> (+{s['users_today']} today, {s['blocked']} blocked bot)",
        f"Active subscriptions: <b>{s['active']}</b>",
        f"Sales: <b>{s['sales']}</b> · open invoices: {s['pending']}",
        f"Revenue today: <b>${s['rev_today']:.2f}</b>",
        f"Revenue 30d: <b>${s['rev_30d']:.2f}</b>",
        f"Revenue total: <b>${s['rev_total']:.2f}</b>",
    ]
    for t in s["by_token"]:
        lines.append(f"  • {t['token']}: {fmt_amount(t['units'], t['token'])} in {t['n']} sales (${t['usd']:.2f})")
    if s["ref_owed"]:
        lines.append(f"Referral commissions owed: ${s['ref_owed']:.2f}")
    await msg.answer("\n".join(lines))


async def _target(msg: Message, db: DB, arg: str | None):
    if not arg:
        await msg.answer("Specify a user id or @username.")
        return None
    user = await db.find_user(arg.split()[0])
    if not user:
        await msg.answer("User not found (they must /start the bot first).")
    return user


@router.message(Command("user"))
async def user_info(msg: Message, command: CommandObject, db: DB):
    user = await _target(msg, db, command.args)
    if not user:
        return
    sub = await db.get_sub(user["id"])
    status = "none"
    if sub:
        status = ("active until " if sub["active"] and sub["expires_at"] > now() else "expired ") + fmt_date(
            sub["expires_at"]) + f" ({sub['plan_id']})"
    await msg.answer(
        f"👤 <code>{user['id']}</code> @{html.escape(user['username'] or '-')} {html.escape(user['first_name'] or '')}\n"
        f"Joined: {fmt_date(user['created_at'])}\nSubscription: {status}\n"
        f"Paid invoices: {await db.paid_count(user['id'])}\n"
        f"Referrer: {user['referrer_id'] or '-'} · invited: {await db.referral_count(user['id'])}\n"
        f"Referral balance: ${user['ref_balance_cents'] / 100:.2f}"
    )


@router.message(Command("grant"))
async def grant(msg: Message, command: CommandObject, db: DB, access: Access):
    parts = (command.args or "").split()
    if len(parts) != 2 or not (parts[1].isdigit() or parts[1].lower() == "life"):
        return await msg.answer("Usage: /grant &lt;id|@username&gt; &lt;days|life&gt;")
    user = await _target(msg, db, parts[0])
    if not user:
        return
    days = 0 if parts[1].lower() == "life" else int(parts[1])
    exp = await db.extend_sub(user["id"], "manual", days)
    await msg.answer(f"✅ Access for <code>{user['id']}</code> until {fmt_date(exp)}")
    await access.send_access(user["id"], "🎁 <b>You've been granted access!</b>")


@router.message(Command("revoke"))
async def revoke(msg: Message, command: CommandObject, db: DB, access: Access):
    user = await _target(msg, db, command.args)
    if not user:
        return
    await db.deactivate_sub(user["id"])
    await access.remove(user["id"])
    await msg.answer(f"⛔ Access revoked for <code>{user['id']}</code>")


@router.message(Command("promo"))
async def promo(msg: Message, command: CommandObject, db: DB):
    parts = (command.args or "").split()
    if len(parts) < 2 or not all(p.isdigit() for p in parts[1:]) or not 1 <= int(parts[1]) <= 100:
        return await msg.answer("Usage: /promo CODE percent(1-100) [max_uses] [valid_days]")
    max_uses = int(parts[2]) if len(parts) > 2 else 0
    days = int(parts[3]) if len(parts) > 3 else 0
    await db.add_promo(parts[0][:32], int(parts[1]), max_uses, now() + days * 86400 if days else 0)
    await msg.answer(f"✅ Promo <b>{html.escape(parts[0].upper())}</b>: -{parts[1]}%, "
                     f"uses: {max_uses or '∞'}, valid: {f'{days} days' if days else 'forever'}")


@router.message(Command("promos"))
async def promos(msg: Message, db: DB):
    rows = await db.list_promos()
    if not rows:
        return await msg.answer("No promo codes.")
    await msg.answer("\n".join(
        f"<code>{r['code']}</code> -{r['percent']}% · used {r['uses']}/{r['max_uses'] or '∞'}"
        + (f" · until {fmt_date(r['expires_at'])}" if r["expires_at"] else "")
        for r in rows
    ))


@router.message(Command("delpromo"))
async def delpromo(msg: Message, command: CommandObject, db: DB):
    if not command.args:
        return await msg.answer("Usage: /delpromo CODE")
    await db.delete_promo(command.args.strip())
    await msg.answer("🗑 Deleted.")


@router.message(Command("broadcast"))
async def broadcast(msg: Message, command: CommandObject, bot: Bot, db: DB):
    src = msg.reply_to_message
    if not src:
        return await msg.answer("Reply to the message you want to broadcast with /broadcast [all|active|inactive]")
    segment = (command.args or "all").strip().lower()
    if segment not in ("all", "active", "inactive"):
        return await msg.answer("Segment must be all, active or inactive.")
    ids = await db.audience(segment)
    status = await msg.answer(f"📣 Sending to {len(ids)} users…")

    async def run():
        ok = fail = 0
        for uid in ids:
            for _ in range(3):
                try:
                    await bot.copy_message(uid, msg.chat.id, src.message_id)
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
        await bot.edit_message_text(f"📣 Broadcast done: {ok} delivered, {fail} failed.",
                                    chat_id=status.chat.id, message_id=status.message_id)

    asyncio.create_task(run())


@router.message(Command("payouts"))
async def payouts(msg: Message, db: DB):
    rows = await db.pending_payouts()
    if not rows:
        return await msg.answer("No pending payouts.")
    await msg.answer("\n\n".join(
        f"#{r['id']} · user <code>{r['user_id']}</code> · <b>${r['amount_cents'] / 100:.2f}</b>\n"
        f"<code>{r['wallet']}</code>" for r in rows
    ) + "\n\nMark sent with /paidout &lt;id&gt;")


@router.message(Command("paidout"))
async def paidout(msg: Message, command: CommandObject, db: DB, access: Access):
    if not (command.args or "").strip().isdigit():
        return await msg.answer("Usage: /paidout &lt;id&gt;")
    row = await db.complete_payout(int(command.args.strip()))
    if not row:
        return await msg.answer("Payout not found or already paid.")
    await msg.answer(f"✅ Payout #{row['id']} marked as paid.")
    await access.safe_send(row["user_id"], f"💸 Your referral payout of ${row['amount_cents'] / 100:.2f} "
                                           f"was sent to <code>{row['wallet']}</code>. Thank you!")


# ---------------------------------------------------------------- membership guard

guard = Router(name="guard")


@guard.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_join(event: ChatMemberUpdated, db: DB, cfg: Config, access: Access):
    """Remove anyone who joins a premium chat without an active subscription."""
    if not cfg.strict_membership or event.chat.id not in cfg.premium_chats:
        return
    user = event.new_chat_member.user
    if user.is_bot or cfg.is_admin(user.id) or await db.is_active(user.id):
        return
    await access.remove(user.id)
    await access.safe_send(user.id, "🔒 That chat is for subscribers only. Get access here: /start")

