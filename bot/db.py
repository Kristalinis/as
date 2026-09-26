"""SQLite storage (aiosqlite)."""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import aiosqlite

from .config import LIFETIME_TS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    lang TEXT,
    referrer_id INTEGER,
    created_at INTEGER NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0,
    trial_used INTEGER NOT NULL DEFAULT 0,
    ref_balance_cents INTEGER NOT NULL DEFAULT 0,
    ref_earned_cents INTEGER NOT NULL DEFAULT 0,
    winback_pct INTEGER NOT NULL DEFAULT 0,
    winback_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'sub' or 'digital'
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    chats TEXT NOT NULL DEFAULT '[]', -- json list of chat ids (sub)
    price_usd REAL NOT NULL DEFAULT 0, -- digital
    file_id TEXT,                    -- digital: telegram file id
    file_kind TEXT,                  -- document / photo / video / audio
    content TEXT,                    -- digital: text / link
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    title TEXT NOT NULL,
    days INTEGER NOT NULL,           -- 0 = lifetime
    price_usd REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    plan_id TEXT,
    expires_at INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    reminded INTEGER NOT NULL DEFAULT 0,
    winback_stage INTEGER NOT NULL DEFAULT 0,
    ended_at INTEGER NOT NULL DEFAULT 0,
    last_link_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, product_id)
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    plan_id TEXT,
    title TEXT NOT NULL,
    days INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL,            -- SOL / USDC / USDT / XTR
    amount_units INTEGER NOT NULL,
    usd REAL NOT NULL,
    reference TEXT UNIQUE NOT NULL,
    discount TEXT,                   -- promo code or 'WINBACK'
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    paid_at INTEGER,
    signature TEXT UNIQUE,
    chat_id INTEGER,
    message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status, created_at);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    invoice_id INTEGER,
    created_at INTEGER NOT NULL
);
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

# How long after creation a pending crypto invoice is still matched (late payments are honoured).
MATCH_WINDOW = 24 * 3600
CRYPTO = ("SOL", "USDC", "USDT")


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(3)


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

    # ------------------------------------------------------------ kv / settings
    async def kv_get(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        await self._exec("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, value))

    async def kv_prefix(self, prefix: str) -> dict[str, str]:
        rows = await self._all("SELECT key, value FROM kv WHERE key LIKE ?", (prefix + "%",))
        return {r["key"][len(prefix):]: r["value"] for r in rows}

    # ------------------------------------------------------------ catalog
    async def seed_catalog(self, data: dict, default_chats: list[int]) -> bool:
        if await self._one("SELECT 1 FROM products LIMIT 1"):
            return False
        for i, p in enumerate(data.get("products", [])):
            kind = "digital" if p.get("kind") == "digital" else "sub"
            await self.conn.execute(
                "INSERT INTO products(id,kind,title,description,chats,price_usd,content,sort) VALUES(?,?,?,?,?,?,?,?)",
                (p["id"], kind, p["title"], p.get("description", ""),
                 json.dumps(p.get("chats") or (default_chats if kind == "sub" else [])),
                 float(p.get("price_usd", 0)), p.get("content"), i),
            )
            for j, pl in enumerate(p.get("plans", [])):
                await self.conn.execute(
                    "INSERT INTO plans(id,product_id,title,days,price_usd,sort) VALUES(?,?,?,?,?,?)",
                    (pl["id"], p["id"], pl["title"], int(pl.get("days", 0)), float(pl["price_usd"]), j),
                )
        await self.conn.commit()
        return True

    async def products(self, active_only: bool = True) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM products" + (" WHERE active=1" if active_only else "") + " ORDER BY sort, rowid"
        return await self._all(sql)

    async def product(self, pid: str) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM products WHERE id=?", (pid,))

    async def plans(self, pid: str, active_only: bool = True) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM plans WHERE product_id=?" + (" AND active=1" if active_only else "")
        return await self._all(sql + " ORDER BY sort, rowid", (pid,))

    async def plan(self, plan_id: str) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM plans WHERE id=?", (plan_id,))

    async def add_product(self, kind: str, title: str, chats: list[int] | None = None,
                          price_usd: float = 0) -> str:
        pid = new_id("p")
        row = await self._one("SELECT COALESCE(MAX(sort),0)+1 s FROM products")
        await self._exec("INSERT INTO products(id,kind,title,chats,price_usd,sort) VALUES(?,?,?,?,?,?)",
                         (pid, kind, title, json.dumps(chats or []), price_usd, row["s"]))
        return pid

    async def update_product(self, pid: str, **fields) -> None:
        if "chats" in fields:
            fields["chats"] = json.dumps(fields["chats"])
        cols = ", ".join(f"{k}=?" for k in fields)
        await self._exec(f"UPDATE products SET {cols} WHERE id=?", (*fields.values(), pid))

    async def delete_product(self, pid: str) -> None:
        await self.conn.execute("DELETE FROM plans WHERE product_id=?", (pid,))
        await self._exec("DELETE FROM products WHERE id=?", (pid,))

    async def add_plan(self, pid: str, title: str, days: int, price_usd: float) -> str:
        plan_id = new_id("pl")
        row = await self._one("SELECT COALESCE(MAX(sort),0)+1 s FROM plans WHERE product_id=?", (pid,))
        await self._exec("INSERT INTO plans(id,product_id,title,days,price_usd,sort) VALUES(?,?,?,?,?,?)",
                         (plan_id, pid, title, days, price_usd, row["s"]))
        return plan_id

    async def update_plan(self, plan_id: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        await self._exec(f"UPDATE plans SET {cols} WHERE id=?", (*fields.values(), plan_id))

    async def delete_plan(self, plan_id: str) -> None:
        await self._exec("DELETE FROM plans WHERE id=?", (plan_id,))

    async def all_premium_chats(self) -> set[int]:
        chats: set[int] = set()
        for p in await self._all("SELECT chats FROM products WHERE kind='sub'"):
            chats.update(json.loads(p["chats"]))
        return chats

    # ------------------------------------------------------------ users
    async def upsert_user(self, uid: int, username: str | None, first_name: str | None,
                          lang: str | None = None, referrer_id: int | None = None) -> bool:
        """Insert or refresh a user. Returns True if the user is new."""
        existing = await self._one("SELECT id FROM users WHERE id=?", (uid,))
        if existing:
            await self._exec("UPDATE users SET username=?, first_name=?, blocked=0 WHERE id=?",
                             (username, first_name, uid))
            return False
        if referrer_id == uid or not (referrer_id and await self.get_user(referrer_id)):
            referrer_id = None
        await self._exec(
            "INSERT INTO users(id,username,first_name,lang,referrer_id,created_at) VALUES(?,?,?,?,?,?)",
            (uid, username, first_name, lang, referrer_id, now()),
        )
        return True

    async def get_user(self, uid: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM users WHERE id=?", (uid,))

    async def lang(self, uid: int) -> str | None:
        row = await self._one("SELECT lang FROM users WHERE id=?", (uid,))
        return row["lang"] if row else None

    async def set_lang(self, uid: int, lang: str) -> None:
        await self._exec("UPDATE users SET lang=? WHERE id=?", (lang, uid))

    async def find_user(self, query: str) -> aiosqlite.Row | None:
        q = query.lstrip("@")
        if q.lstrip("-").isdigit():
            return await self.get_user(int(q))
        return await self._one("SELECT * FROM users WHERE lower(username)=lower(?)", (q,))

    async def set_blocked(self, uid: int, blocked: bool = True) -> None:
        await self._exec("UPDATE users SET blocked=? WHERE id=?", (int(blocked), uid))

    async def mark_trial_used(self, uid: int) -> None:
        await self._exec("UPDATE users SET trial_used=1 WHERE id=?", (uid,))

    async def set_winback(self, uid: int, pct: int, until: int) -> None:
        await self._exec("UPDATE users SET winback_pct=?, winback_until=? WHERE id=?", (pct, until, uid))

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
        active = "SELECT user_id FROM subscriptions WHERE active=1 AND expires_at>?"
        if segment == "active":
            rows = await self._all(f"SELECT id FROM users WHERE blocked=0 AND id IN ({active})", (t,))
        elif segment == "inactive":
            rows = await self._all(f"SELECT id FROM users WHERE blocked=0 AND id NOT IN ({active})", (t,))
        else:
            rows = await self._all("SELECT id FROM users WHERE blocked=0")
        return [r["id"] for r in rows]

    # ------------------------------------------------------------ subscriptions
    async def get_sub(self, uid: int, pid: str) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM subscriptions WHERE user_id=? AND product_id=?", (uid, pid))

    async def user_subs(self, uid: int) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM subscriptions WHERE user_id=? ORDER BY expires_at DESC", (uid,))

    async def active_subs(self, uid: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM subscriptions WHERE user_id=? AND active=1 AND expires_at>?", (uid, now()))

    async def is_active(self, uid: int, pid: str) -> bool:
        sub = await self.get_sub(uid, pid)
        return bool(sub and sub["active"] and sub["expires_at"] > now())

    async def has_chat_access(self, uid: int, chat_id: int) -> bool:
        for sub in await self.active_subs(uid):
            p = await self.product(sub["product_id"])
            if p and chat_id in json.loads(p["chats"]):
                return True
        return False

    async def extend_sub(self, uid: int, pid: str, plan_id: str | None, days: int) -> int:
        """Extend (or create) a subscription. days<=0 means lifetime. Returns new expiry."""
        sub = await self.get_sub(uid, pid)
        t = now()
        if days <= 0:
            exp = LIFETIME_TS
        else:
            start = sub["expires_at"] if sub and sub["active"] and sub["expires_at"] > t else t
            exp = min(start + days * 86400, LIFETIME_TS)
        await self._exec(
            "INSERT INTO subscriptions(user_id,product_id,plan_id,expires_at,active) VALUES(?,?,?,?,1) "
            "ON CONFLICT(user_id,product_id) DO UPDATE SET plan_id=excluded.plan_id, "
            "expires_at=excluded.expires_at, active=1, reminded=0, winback_stage=0, ended_at=0",
            (uid, pid, plan_id, exp),
        )
        return exp

    async def deactivate_sub(self, uid: int, pid: str) -> None:
        await self._exec(
            "UPDATE subscriptions SET active=0, ended_at=?, expires_at=MIN(expires_at, ?) "
            "WHERE user_id=? AND product_id=?", (now(), now(), uid, pid))

    async def expired_subs(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM subscriptions WHERE active=1 AND expires_at<=?", (now(),))

    async def subs_to_remind(self, within: int, stage: int) -> list[aiosqlite.Row]:
        t = now()
        return await self._all(
            "SELECT * FROM subscriptions WHERE active=1 AND expires_at>? AND expires_at<=? AND reminded<?",
            (t, t + within, stage),
        )

    async def set_reminded(self, uid: int, pid: str, stage: int) -> None:
        await self._exec("UPDATE subscriptions SET reminded=? WHERE user_id=? AND product_id=?", (stage, uid, pid))

    async def winback_due(self, after: int, stage: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM subscriptions WHERE active=0 AND ended_at>0 AND ended_at<=? AND winback_stage<?",
            (now() - after, stage),
        )

    async def set_winback_stage(self, uid: int, pid: str, stage: int) -> None:
        await self._exec("UPDATE subscriptions SET winback_stage=? WHERE user_id=? AND product_id=?",
                         (stage, uid, pid))

    async def touch_link(self, uid: int, pid: str) -> None:
        await self._exec("UPDATE subscriptions SET last_link_at=? WHERE user_id=? AND product_id=?", (now(), uid, pid))

    # ------------------------------------------------------------ purchases
    async def add_purchase(self, uid: int, pid: str, invoice_id: int | None) -> None:
        await self._exec("INSERT INTO purchases(user_id,product_id,invoice_id,created_at) VALUES(?,?,?,?)",
                         (uid, pid, invoice_id, now()))

    async def owns(self, uid: int, pid: str) -> bool:
        return await self._one("SELECT 1 FROM purchases WHERE user_id=? AND product_id=?", (uid, pid)) is not None

    async def user_purchases(self, uid: int) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT DISTINCT p.* FROM purchases u JOIN products p ON p.id=u.product_id WHERE u.user_id=?", (uid,))

    async def remove_purchase(self, invoice_id: int) -> None:
        await self._exec("DELETE FROM purchases WHERE invoice_id=?", (invoice_id,))

    # ------------------------------------------------------------ invoices
    async def taken_amounts(self, method: str) -> set[int]:
        rows = await self._all(
            "SELECT amount_units FROM invoices WHERE status='pending' AND method=? AND created_at>?",
            (method, now() - MATCH_WINDOW),
        )
        return {r["amount_units"] for r in rows}

    async def create_invoice(self, uid: int, pid: str, plan_id: str | None, title: str, days: int, method: str,
                             amount_units: int, usd: float, reference: str, discount: str | None,
                             ttl_min: int) -> int:
        t = now()
        return await self._exec(
            "INSERT INTO invoices(user_id,product_id,plan_id,title,days,method,amount_units,usd,reference,discount,"
            "created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, pid, plan_id, title, days, method, amount_units, usd, reference, discount, t, t + ttl_min * 60),
        )

    async def open_invoice(self, uid: int, pid: str, plan_id: str | None, method: str,
                           discount: str | None) -> aiosqlite.Row | None:
        return await self._one(
            "SELECT * FROM invoices WHERE user_id=? AND product_id=? AND IFNULL(plan_id,'')=? AND method=? "
            "AND IFNULL(discount,'')=? AND status='pending' AND expires_at>? ORDER BY id DESC LIMIT 1",
            (uid, pid, plan_id or "", method, discount or "", now() + 120),
        )

    async def set_invoice_message(self, inv_id: int, chat_id: int, message_id: int) -> None:
        await self._exec("UPDATE invoices SET chat_id=?, message_id=? WHERE id=?", (chat_id, message_id, inv_id))

    async def get_invoice(self, inv_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM invoices WHERE id=?", (inv_id,))

    async def pending_invoices(self) -> list[aiosqlite.Row]:
        return await self._all(
            "SELECT * FROM invoices WHERE status='pending' AND method IN ('SOL','USDC','USDT') AND created_at>? "
            "ORDER BY created_at", (now() - MATCH_WINDOW,),
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

    async def reopen_invoice(self, inv_id: int) -> None:
        await self._exec("UPDATE invoices SET status='pending' WHERE id=? AND status='expired'", (inv_id,))

    async def mark_refunded(self, inv_id: int) -> None:
        await self._exec("UPDATE invoices SET status='refunded' WHERE id=?", (inv_id,))

    async def expire_stale_invoices(self) -> None:
        await self._exec("UPDATE invoices SET status='expired' WHERE status='pending' AND "
                         "((method='XTR' AND expires_at<=?) OR created_at<=?)", (now(), now() - MATCH_WINDOW))

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
    async def daily_revenue(self, days: int = 30) -> list[tuple[str, float]]:
        start = (now() // 86400 - days + 1) * 86400
        rows = await self._all(
            "SELECT (paid_at/86400)*86400 d, SUM(usd) usd FROM invoices WHERE status='paid' AND paid_at>=? "
            "GROUP BY d", (start,))
        by_day = {r["d"]: r["usd"] for r in rows}
        return [(time.strftime("%m-%d", time.gmtime(start + i * 86400)), by_day.get(start + i * 86400, 0.0))
                for i in range(days)]

    async def stats(self) -> dict:
        t = now()
        day, month = t - 86400, t - 30 * 86400

        async def scalar(sql: str, args: tuple = ()) -> float:
            row = await self._one(sql, args)
            return row[0] or 0

        by_method = await self._all(
            "SELECT method, COUNT(*) n, SUM(amount_units) units, SUM(usd) usd FROM invoices "
            "WHERE status='paid' GROUP BY method"
        )
        by_product = await self._all(
            "SELECT title, COUNT(*) n, SUM(usd) usd FROM invoices WHERE status='paid' "
            "GROUP BY product_id ORDER BY usd DESC LIMIT 10"
        )
        return {
            "users": await scalar("SELECT COUNT(*) FROM users"),
            "users_today": await scalar("SELECT COUNT(*) FROM users WHERE created_at>?", (day,)),
            "blocked": await scalar("SELECT COUNT(*) FROM users WHERE blocked=1"),
            "active": await scalar("SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE active=1 AND expires_at>?",
                                   (t,)),
            "sales": await scalar("SELECT COUNT(*) FROM invoices WHERE status='paid'"),
            "rev_total": await scalar("SELECT SUM(usd) FROM invoices WHERE status='paid'"),
            "rev_30d": await scalar("SELECT SUM(usd) FROM invoices WHERE status='paid' AND paid_at>?", (month,)),
            "rev_today": await scalar("SELECT SUM(usd) FROM invoices WHERE status='paid' AND paid_at>?", (day,)),
            "pending": await scalar("SELECT COUNT(*) FROM invoices WHERE status='pending' AND expires_at>?", (t,)),
            "by_method": [dict(r) for r in by_method],
            "by_product": [dict(r) for r in by_product],
            "ref_owed": await scalar("SELECT SUM(ref_balance_cents) FROM users") / 100
            + await scalar("SELECT SUM(amount_cents) FROM payouts WHERE status='pending'") / 100,
        }
