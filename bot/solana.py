"""Minimal Solana JSON-RPC client + Solana Pay helpers and transfer verification."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from decimal import ROUND_UP, Decimal
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_IDX = {c: i for i, c in enumerate(_B58)}


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        if c not in _B58_IDX:
            raise ValueError("invalid base58 character")
        n = n * 58 + _B58_IDX[c]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + body


def is_valid_address(addr: str) -> bool:
    try:
        return 32 <= len(addr) <= 44 and len(b58decode(addr)) == 32
    except ValueError:
        return False


def new_reference() -> str:
    """Random 32-byte key used as a Solana Pay `reference` to tag one invoice."""
    return b58encode(os.urandom(32))


def units_to_str(units: int, decimals: int) -> str:
    q = Decimal(units) / (Decimal(10) ** decimals)
    s = format(q, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def quote_amount(token: str, usd: float, sol_price: float | None, taken: set[int]) -> int:
    """Return an amount in base units that is unique among `taken` pending invoices.

    A small random tail (< $0.01 for stablecoins, < 0.0001 SOL) makes each pending invoice amount unique, so payments
    sent from exchanges (which can't attach a Solana Pay reference) are still matched.
    """
    if token == "SOL":
        if not sol_price:
            raise ValueError("SOL price unavailable")
        base = Decimal(str(usd)) / Decimal(str(sol_price))
        base = base.quantize(Decimal("0.0001"), rounding=ROUND_UP)
        base_units, step, n = int(base * 10**9), 1000, 99  # tail: 0.000001 SOL steps
    else:
        base_units, step, n = int(Decimal(str(usd)).quantize(Decimal("0.01")) * 10**6), 10, 999  # $0.00001 steps
    tails = list(range(1, n + 1))
    random.shuffle(tails)
    for t in tails:
        amount = base_units + t * step
        if amount not in taken:
            return amount
    raise RuntimeError("too many pending invoices for this amount, try again later")


def pay_url(recipient: str, amount: str, reference: str, mint: str | None, label: str, message: str) -> str:
    url = f"solana:{recipient}?amount={amount}"
    if mint:
        url += f"&spl-token={mint}"
    url += f"&reference={reference}&label={quote(label)}&message={quote(message)}"
    return url


# ---------------------------------------------------------------- verification

def account_keys(tx: dict) -> list[str]:
    msg = tx["transaction"]["message"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg["accountKeys"]]
    meta = tx.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    if len(keys) < len(meta.get("preBalances", [])):
        keys += loaded.get("writable", []) + loaded.get("readonly", [])
    return keys


def received_amount(tx: dict, recipient: str, mint: str | None) -> int:
    """Net amount (base units) that `recipient` gained in this tx. SOL if mint is None."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return 0
    if mint is None:
        keys = account_keys(tx)
        if recipient not in keys:
            return 0
        i = keys.index(recipient)
        return meta["postBalances"][i] - meta["preBalances"][i]

    def total(balances: list[dict] | None) -> int:
        return sum(
            int(b["uiTokenAmount"]["amount"])
            for b in balances or []
            if b.get("owner") == recipient and b.get("mint") == mint
        )

    return total(meta.get("postTokenBalances")) - total(meta.get("preTokenBalances"))


def has_reference(tx: dict, reference: str) -> bool:
    return reference in account_keys(tx)


# ---------------------------------------------------------------- RPC client

class RPCError(Exception):
    pass


class SolanaRPC:
    def __init__(self, url: str):
        self.url = url
        self.client = httpx.AsyncClient(timeout=20)
        self._id = 0

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, params: list) -> object:
        for attempt in range(4):
            self._id += 1
            try:
                r = await self.client.post(
                    self.url, json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
                )
                if r.status_code == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("RPC %s failed (%s), retrying", method, e)
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if "error" in data:
                raise RPCError(f"{method}: {data['error']}")
            return data.get("result")
        raise RPCError(f"{method}: RPC unavailable")

    async def signatures(self, address: str, limit: int = 25, until: str | None = None,
                         before: str | None = None) -> list[dict]:
        opts: dict = {"limit": limit, "commitment": "confirmed"}
        if until:
            opts["until"] = until
        if before:
            opts["before"] = before
        return await self.call("getSignaturesForAddress", [address, opts]) or []

    async def transaction(self, signature: str) -> dict | None:
        return await self.call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "commitment": "confirmed",
                         "maxSupportedTransactionVersion": 0}],
        )

    async def token_accounts(self, owner: str, mint: str) -> list[str]:
        res = await self.call(
            "getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}]
        )
        return [a["pubkey"] for a in (res or {}).get("value", [])]


# ---------------------------------------------------------------- SOL/USD price

class PriceFeed:
    SOURCES = (
        ("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
         lambda d: d["solana"]["usd"]),
        ("https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT", lambda d: d["price"]),
        ("https://api.kraken.com/0/public/Ticker?pair=SOLUSD",
         lambda d: next(iter(d["result"].values()))["c"][0]),
    )

    def __init__(self, ttl: int = 60):
        self.ttl = ttl
        self._price: float | None = None
        self._ts = 0.0
        self.client = httpx.AsyncClient(timeout=10)

    async def close(self) -> None:
        await self.client.aclose()

    async def sol_usd(self) -> float:
        if self._price and time.time() - self._ts < self.ttl:
            return self._price
        for url, pick in self.SOURCES:
            try:
                r = await self.client.get(url)
                r.raise_for_status()
                price = float(pick(r.json()))
                if price > 0:
                    self._price, self._ts = price, time.time()
                    return price
            except Exception as e:  # noqa: BLE001 - try next source
                log.warning("price source %s failed: %s", url, e)
        if self._price and time.time() - self._ts < 900:
            return self._price
        raise RuntimeError("SOL price unavailable")
