import os

import pytest

from bot.solana import (
    b58decode, b58encode, has_reference, is_valid_address, new_reference, pay_url, quote_amount,
    received_amount, units_to_str,
)

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def addr() -> str:
    return b58encode(os.urandom(32))


def sol_tx(payer: str, to: str, lamports: int, extra_keys=(), err=None, block_time=None):
    fee = 5000
    return {
        "blockTime": block_time,
        "meta": {"err": err, "fee": fee,
                 "preBalances": [10**10, 10**9] + [0] * len(extra_keys),
                 "postBalances": [10**10 - lamports - fee, 10**9 + lamports] + [0] * len(extra_keys),
                 "preTokenBalances": [], "postTokenBalances": []},
        "transaction": {"message": {"accountKeys": [
            {"pubkey": k, "signer": i == 0, "writable": i < 2, "source": "transaction"}
            for i, k in enumerate([payer, to, *extra_keys])
        ]}},
    }


def spl_tx(owner_from: str, owner_to: str, mint: str, amount: int, reference: str | None = None,
           pre_to: int = 5_000_000, to_has_account: bool = True):
    keys = [owner_from, addr(), addr(), "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"]
    if reference:
        keys.append(reference)
    pre = [{"accountIndex": 1, "mint": mint, "owner": owner_from,
            "uiTokenAmount": {"amount": str(10**9), "decimals": 6}}]
    post = [{"accountIndex": 1, "mint": mint, "owner": owner_from,
             "uiTokenAmount": {"amount": str(10**9 - amount), "decimals": 6}},
            {"accountIndex": 2, "mint": mint, "owner": owner_to,
             "uiTokenAmount": {"amount": str((pre_to if to_has_account else 0) + amount), "decimals": 6}}]
    if to_has_account:
        pre.append({"accountIndex": 2, "mint": mint, "owner": owner_to,
                    "uiTokenAmount": {"amount": str(pre_to), "decimals": 6}})
    return {
        "blockTime": None,
        "meta": {"err": None, "preBalances": [1] * len(keys), "postBalances": [1] * len(keys),
                 "preTokenBalances": pre, "postTokenBalances": post},
        "transaction": {"message": {"accountKeys": [{"pubkey": k} for k in keys]}},
    }


def test_base58_roundtrip():
    for _ in range(50):
        raw = os.urandom(32)
        assert b58decode(b58encode(raw)) == raw
    assert b58encode(b"\0\0\1") == "112"
    assert is_valid_address(USDC)
    assert is_valid_address(new_reference())
    assert not is_valid_address("0OIl")
    assert not is_valid_address("abc")


def test_units_to_str():
    assert units_to_str(93_412_000, 9) == "0.093412"
    assert units_to_str(19_000_000, 6) == "19"
    assert units_to_str(19_000_120, 6) == "19.00012"


def test_quote_amount_unique_and_close():
    taken: set[int] = set()
    for _ in range(999):
        a = quote_amount("USDC", 19, None, taken)
        assert 19_000_000 < a < 19_010_000 and a % 10 == 0
        taken.add(a)
    with pytest.raises(RuntimeError):
        quote_amount("USDC", 19, None, taken)
    a = quote_amount("SOL", 19, 150.0, set())
    base = 126_700_000  # 19/150 = 0.12666.. rounded up to 0.1267 SOL
    assert base < a < base + 100_000 and a % 1000 == 0
    with pytest.raises(ValueError):
        quote_amount("SOL", 19, None, set())


def test_pay_url():
    url = pay_url("RECV", "19.00012", "REF", USDC, "Alpha Club", "1 Month #3")
    assert url == (f"solana:RECV?amount=19.00012&spl-token={USDC}&reference=REF"
                   "&label=Alpha%20Club&message=1%20Month%20%233")


def test_received_sol():
    payer, to, ref = addr(), addr(), addr()
    tx = sol_tx(payer, to, 1234, extra_keys=[ref])
    assert received_amount(tx, to, None) == 1234
    assert received_amount(tx, addr(), None) == 0
    assert received_amount(tx, payer, None) < 0
    assert has_reference(tx, ref)
    assert not has_reference(tx, addr())
    assert received_amount(sol_tx(payer, to, 1234, err={"InstructionError": [0, "x"]}), to, None) == 0


def test_received_sol_with_lookup_table_keys():
    payer, to = addr(), addr()
    tx = sol_tx(payer, addr(), 0)
    tx["transaction"]["message"]["accountKeys"] = [{"pubkey": payer}]
    tx["meta"]["loadedAddresses"] = {"writable": [to], "readonly": []}
    tx["meta"]["preBalances"], tx["meta"]["postBalances"] = [100, 0], [50, 40]
    assert received_amount(tx, to, None) == 40


def test_received_spl():
    sender, me, ref = addr(), addr(), addr()
    tx = spl_tx(sender, me, USDC, 19_000_120, reference=ref)
    assert received_amount(tx, me, USDC) == 19_000_120
    assert received_amount(tx, me, "OtherMint111111111111111111111111111111111") == 0
    assert has_reference(tx, ref)
    # recipient token account created in the same tx (no pre balance)
    assert received_amount(spl_tx(sender, me, USDC, 7, to_has_account=False), me, USDC) == 7
