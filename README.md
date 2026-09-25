# Solana Subscription Bot

A Telegram bot that sells paid access to your private channels/groups and takes payment in **SOL, USDC or USDT on Solana**.
It checks payments on-chain, adds and removes members itself, and has referrals, promo codes, trials and an admin panel.

## What it does

- **Plans:** as many as you want (monthly, yearly, lifetime…), edited in `plans.json`.
- **Solana payments:** each invoice has a QR code. Phantom, Solflare or Backpack scan it as a Solana Pay link, or the buyer copies the address and amount.
  - Payments are found **automatically** in two ways. Wallet payments are matched by their Solana Pay `reference` key. Exchange withdrawals are matched by an exact, unique amount per invoice.
  - Every transfer is checked on-chain: the right coin, the right receiver, enough money, the transaction succeeded, and the signature was never used before.
- **Automatic access:** after payment the buyer gets personal invite links that work once and expire in 24h. When the subscription ends, the bot removes them from every premium chat.
- **Reminders** 3 days and 1 day before expiry, each with a *Renew* button. Renewing early adds time on top of what's left.
- **Membership guard:** anyone who joins a premium chat without an active subscription is removed right away (turn this off with `STRICT_MEMBERSHIP=false`).
- **Affiliate program:** each user has a referral link. Inviters earn a % of every payment their invitees make, can request a payout to their wallet, and you approve it with `/paidout`. Optional bonus days too.
- **Promo codes:** a % discount, with a use limit and an expiry date. A 100% code gives the plan for free.
- **Free trial:** optional, one per user.
- **Admin panel:** `/stats` (revenue today / 30d / total, sales per coin), `/user`, `/grant`, `/revoke`, `/promo`, `/broadcast` (to all, active or inactive users), `/payouts`. You also get a message on every sale.

## Setup (about 10 minutes)

1. **Create the bot:** message [@BotFather](https://t.me/BotFather), send `/newbot` and copy the token.
2. **Get your Telegram id:** message [@userinfobot](https://t.me/userinfobot).
3. **Wallet:** use the address of a Solana wallet you control (Phantom, Solflare…).
   To take USDC/USDT, that wallet needs a USDC/USDT token account. Receiving any small amount of the token once creates it.
4. **Premium chat:** make a *private* channel or group. Add the bot as an **admin** with *Invite users via link* and *Ban users* rights.
5. **Configure:**
   ```bash
   cp .env.example .env      # then fill in BOT_TOKEN, ADMIN_IDS, SOLANA_RECEIVER, PREMIUM_CHAT_IDS
   ```
   To find a chat id, start the bot and **forward any post from the channel to it** (admins only), or send `/chatid` inside the group.
   Edit `plans.json` to set your prices, plans and welcome text.
6. **Run:**
   ```bash
   docker compose up -d --build          # recommended, restarts automatically
   # or without Docker:
   pip install -r requirements.txt && python -m bot
   ```

For real traffic, use a free RPC key from Helius, QuickNode or Alchemy (`SOLANA_RPC_URL`) instead of the rate-limited public endpoint.

## Admin commands

| Command | What it does |
|---|---|
| `/stats` | Users, active subscriptions, revenue |
| `/user <id\|@name>` | User details |
| `/grant <id\|@name> <days\|life>` | Give or extend access |
| `/revoke <id\|@name>` | Remove access now |
| `/promo CODE 30 [max_uses] [valid_days]` | Create a promo code |
| `/promos`, `/delpromo CODE` | List or delete promo codes |
| `/broadcast [all\|active\|inactive]` | Send it as a reply to any message (text, photo, video…) |
| `/payouts`, `/paidout <id>` | Referral payouts |

## How payment matching works

- Each invoice gets a random Solana Pay `reference` key and an amount that is unique among open invoices. The unique part is a tiny extra amount: under $0.01 for stablecoins, under 0.0001 SOL.
- Every `POLL_INTERVAL_SECONDS` the bot:
  1. looks up each open invoice's reference with `getSignaturesForAddress`;
  2. scans new incoming transactions to your wallet and its USDC/USDT token accounts and matches them by exact amount.
- A payment that arrives after the invoice expired is still accepted for up to 24h.
- The SOL price comes from CoinGecko, falling back to Binance, then Kraken. It is fixed when the invoice is created.

## Tests

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest -q
```
The tests run the whole flow (start → plan → invoice → on-chain payment → invite links → expiry, plus promo codes, trial, membership guard, admin commands and payouts). They use a fake Telegram API and a fake Solana RPC.
