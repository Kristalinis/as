"""SQLite storage (aiosqlite)."""
from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from .config import LIFETIME_TS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    referrer_id INTEGER,
    created_at INTEGER NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0,
    trial_used INTEGER NOT NULL DEFAULT 0,
    ref_balance_cents INTEGER NOT NULL DEFAULT 0,
    ref_earned_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    plan_id TEXT,
    expires_at INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    reminded INTEGER NOT NULL DEFAULT 0,
    last_link_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    days INTEGER NOT NULL,
    token TEXT NOT NULL,
    amount_units INTEGER NOT NULL,
    usd REAL NOT NULL,
    reference TEXT UNIQUE NOT NULL,
    promo TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    paid_at INTEGER,
    signature TEXT UNIQUE,
    chat_id INTEGER,
    message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status, created_at);
CREATE TABLE IF NOT EXISTS promos (
    code TEXT PRIMARY KEY,
    percent INTEGER NOT NULL,
    max_uses INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    wallet TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    paid_at INTEGER
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

# How long after creation a pending invoice is still matched (late payments are honoured).
MATCH_WINDOW = 24 * 3600


def now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _one(self, sql: str, args: tuple = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, args: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def _exec(self, sql: str, args: tuple = ()) -> int:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # ------------------------------------------------------------ kv
    async def kv_get(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        await self._exec("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, value))

    # ------------------------------------------------------------ users
    async def upsert_user(self, uid: int, username: str | None, first_name: str | None,
                          referrer_id: int | None = None) -> bool:
        """Insert or refresh a user. Returns True if the user is new."""
        existing = await self._one("SELECT id FROM users WHERE id=?", (uid,))
        if existing:
            await self._exec("UPDATE users SET username=?, first_name=?, blocked=0 WHERE id=?",
                             (username, first_name, uid))
            return False
        if referrer_id == uid or not (referrer_id and await self.get_user(referrer_id)):
            referrer_id = None
        await self._exec(
            "INSERT INTO users(id,username,first_name,referrer_id,created_at) VALUES(?,?,?,?,?)",
            (uid, username, first_name, referrer_id, now()),
        )
        return True

    async def get_user(self, uid: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM users WHERE id=?", (uid,))

    async def find_user(self, query: str) -> aiosqlite.Row | None:
        q = query.lstrip("@")
        if q.lstrip("-").isdigit():
            return await self.get_user(int(q))
        return await self._one("SELECT * FROM users WHERE lower(username)=lower(?)", (q,))

    async def set_blocked(self, uid: int, blocked: bool = True) -> None:
        await self._exec("UPDATE users SET blocked=? WHERE id=?", (int(blocked), uid))

    async def mark_trial_used(self, uid: int) -> None:
        await self._exec("UPDATE users SET trial_used=1 WHERE id=?", (uid,))

    async def referral_count(self, uid: int) -> int:
        row = await self._one("SELECT COUNT(*) c FROM users WHERE referrer_id=?", (uid,))
        return row["c"]

    async def add_ref_commission(self, uid: int, cents: int) -> None:
        await self._exec(
            "UPDATE users SET ref_balance_cents=ref_balance_cents+?, ref_earned_cents=ref_earned_cents+? WHERE id=?",
            (cents, cents, uid),
        )

    async def audience(self, segment: str) -> list[int]:
        t = now()
        if segment == "active":
            sql = ("SELECT u.id FROM users u JOIN subscriptions s ON s.user_id=u.id "
                   "WHERE u.blocked=0 AND s.active=1 AND s.expires_at>?")
            rows = await self._all(sql, (t,))
        elif segment == "inactive":
            sql = ("SELECT u.id FROM users u LEFT JOIN subscriptions s ON s.user_id=u.id "
                   "WHERE u.blocked=0 AND (s.user_id IS NULL OR s.active=0 OR s.expires_at<=?)")
            rows = await self._all(sql, (t,))
        else:
            rows = await self._all("SELECT id FROM users WHERE blocked=0")
        return [r["id"] for r in rows]

    # ------------------------------------------------------------ subscriptions
    async def get_sub(self, uid: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM subscriptions WHERE user_id=?", (uid,))

    async def is_active(self, uid: int) -> bool:
        sub = await self.get_sub(uid)
        return bool(sub and sub["active"] and sub["expires_at"] > now())

    async def extend_sub(self, uid: int, plan_id: str, days: int) -> int:
        """Extend (or create) a subscription. days<=0 means lifetime. Returns new expiry."""
        sub = await self.get_sub(uid)
        t = now()
        if days <= 0:
            exp = LIFETIME_TS
        else:
            start = sub["expires_at"] if sub and sub["active"] and sub["expires_at"] > t else t
            exp = min(start + days * 86400, LIFETIME_TS)
        await self._exec(
            "INSERT INTO subscriptions(user_id,plan_id,expires_at,active,reminded) VALUES(?,?,?,1,0) "
            "ON CONFLICT(user_id) DO UPDATE SET plan_id=excluded.plan_id, expires_at=excluded.expires_at, "
            "active=1, reminded=0",
            (uid, plan_id, exp),
        )
        return exp

    async def deactivate_sub(self, uid: int) -> None:
        await self._exec("UPDATE subscriptions SET active=0, expires_at=MIN(expires_at, ?) WHERE user_id=?",
                         (now(), uid))

    async def expired_subs(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM subscriptions WHERE active=1 AND expires_at<=?", (now(),))

    async def subs_to_remind(self, within: int, stage: int) -> list[aiosqlite.Row]:
        t = now()
        return await self._all(
            "SELECT * FROM subscriptions WHERE active=1 AND expires_at>? AND expires_at<=? AND reminded<?",
            (t, t + within, stage),
        )

    async def set_reminded(self, uid: int, stage: int) -> None:
        await self._exec("UPDATE subscriptions SET reminded=? WHERE user_id=?", (stage, uid))

    async def touch_link(self, uid: int) -> None:
        await self._exec("UPDATE subscriptions SET last_link_at=? WHERE user_id=?", (now(), uid))

    # ------------------------------------------------------------ invoices
    async def taken_amounts(self, token: str) -> set[int]:
        rows = await self._all(
            "SELECT amount_units FROM invoices WHERE status='pending' AND token=? AND created_at>?",
            (token, now() - MATCH_WINDOW),
        )
        return {r["amount_units"] for r in rows}

    async def create_invoice(self, uid: int, plan_id: str, days: int, token: str, amount_units: int,
                             usd: float, reference: str, promo: str | None, ttl_min: int) -> int:
        t = now()
        return await self._exec(
            "INSERT INTO invoices(user_id,plan_id,days,token,amount_units,usd,reference,promo,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, plan_id, days, token, amount_units, usd, reference, promo, t, t + ttl_min * 60),
        )

    async def open_invoice(self, uid: int, plan_id: str, token: str, promo: str | None) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM invoices WHERE user_id=? AND plan_id=? AND token=? AND IFNULL(promo,'')=? "
            "AND status='pending' AND expires_at>? ORDER BY id DESC LIMIT 1",
            (uid, plan_id, token, promo or "", now() + 120),
        )

    async def set_invoice_message(self, inv_id: int, chat_id: int, message_id: int) -> None:
        await self._exec("UPDATE invoices SET chat_id=?, message_id=? WHERE id=?", (chat_id, message_id, inv_id))

    async def get_invoice(self, inv_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM invoices WHERE id=?", (inv_id,))

    async def pending_invoices(self) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM invoices WHERE status='pending' AND created_at>? ORDER BY created_at",
            (now() - MATCH_WINDOW,),
        )

    async def signature_used(self, sig: str) -> bool:
        return await self._one("SELECT 1 FROM invoices WHERE signature=?", (sig,)) is not None

    async def mark_paid(self, inv_id: int, signature: str) -> bool:
        """Atomically mark paid. Returns False if already paid or signature reused."""
        try:
            cur = await self.conn.execute(
                "UPDATE invoices SET status='paid', paid_at=?, signature=? WHERE id=? AND status='pending'",
                (now(), signature, inv_id),
            )
            await self.conn.commit()
        except aiosqlite.IntegrityError:
            await self.conn.rollback()
            return False
        return cur.rowcount == 1

    async def cancel_invoice(self, inv_id: int, uid: int) -> None:
        await self._exec("UPDATE invoices SET status='cancelled' WHERE id=? AND user_id=? AND status='pending'",
                         (inv_id, uid))

    async def expire_stale_invoices(self) -> None:
        await self._exec("UPDATE invoices SET status='expired' WHERE status='pending' AND created_at<=?",
                         (now() - MATCH_WINDOW,))

    async def paid_count(self, uid: int) -> int:
        row = await self._one("SELECT COUNT(*) c FROM invoices WHERE user_id=? AND status='paid'", (uid,))
        return row["c"]

    # ------------------------------------------------------------ promos
    async def add_promo(self, code: str, percent: int, max_uses: int, expires_at: int) -> None:
        await self._exec(
            "INSERT INTO promos(code,percent,max_uses,expires_at) VALUES(?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET percent=excluded.percent, max_uses=excluded.max_uses, "
            "expires_at=excluded.expires_at",
            (code.upper(), percent, max_uses, expires_at),
        )

    async def valid_promo(self, code: str) -> aiosqlite.Row | None:
        row = await self._one("SELECT * FROM promos WHERE code=?", (code.upper(),))
        if not row:
            return None
        if row["max_uses"] and row["uses"] >= row["max_uses"]:
            return None
        if row["expires_at"] and row["expires_at"] < now():
            return None
        return row

    async def use_promo(self, code: str) -> None:
        await self._exec("UPDATE promos SET uses=uses+1 WHERE code=?", (code.upper(),))

    async def list_promos(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM promos ORDER BY code")

    async def delete_promo(self, code: str) -> None:
        await self._exec("DELETE FROM promos WHERE code=?", (code.upper(),))

    # ------------------------------------------------------------ payouts
    async def request_payout(self, uid: int, wallet: str) -> tuple[int, int] | None:
        user = await self.get_user(uid)
        cents = user["ref_balance_cents"] if user else 0
        if cents <= 0:
            return None
        await self.conn.execute("UPDATE users SET ref_balance_cents=0 WHERE id=?", (uid,))
        cur = await self.conn.execute(
            "INSERT INTO payouts(user_id,amount_cents,wallet,created_at) VALUES(?,?,?,?)",
            (uid, cents, wallet, now()),
        )
        await self.conn.commit()
        return cur.lastrowid, cents

    async def pending_payouts(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM payouts WHERE status='pending' ORDER BY id")

    async def complete_payout(self, pid: int) -> aiosqlite.Row | None:
        row = await self._one("SELECT * FROM payouts WHERE id=? AND status='pending'", (pid,))
        if row:
            await self._exec("UPDATE payouts SET status='paid', paid_at=? WHERE id=?", (now(), pid))
        return row

    # ------------------------------------------------------------ stats
    async def stats(self) -> dict:
        t = now()
        day, month = t - 86400, t - 30 * 86400

        async def scalar(sql: str, args: tuple = ()) -> float:
            row = await self._one(sql, args)
            return row[0] or 0

        by_token = await self._all(
            "SELECT token, COUNT(*) n, SUM(amount_units) units, SUM(usd) usd FROM invoices "
            "WHERE status='paid' GROUP BY token"
        )
        return {
            "users": await scalar("SELECT COUNT(*) FROM users"),
            "users_today": await scalar("SELECT COUNT(*) FROM users WHERE created_at>?", (day,)),
            "blocked": await scalar("SELECT COUNT(*) FROM users WHERE blocked=1"),
            "active": await scalar("SELECT COUNT(*) FROM subscriptions WHERE active=1 AND expires_at>?", (t,)),
            "sales": await scalar("SELECT COUNT(*) FROM invoices WHERE status='paid'"),
            "rev_total": await scalar("SELECT SUM(usd) FROM invoices WHERE status='paid'"),
            "rev_30d": await scalar("SELECT SUM(usd) FROM invoices WHERE status='paid' AND paid_at>?", (month,)),
            "rev_today": await scalar("SELECT SUM(usd) FROM invoices WHERE status='paid' AND paid_at>?", (day,)),
            "pending": await scalar("SELECT COUNT(*) FROM invoices WHERE status='pending' AND expires_at>?", (t,)),
            "by_token": [dict(r) for r in by_token],
            "ref_owed": await scalar("SELECT SUM(ref_balance_cents) FROM users") / 100
            + await scalar("SELECT SUM(amount_cents) FROM payouts WHERE status='pending'") / 100,
        }
