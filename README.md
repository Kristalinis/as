# Solana Subscription Bot

A Telegram bot that sells paid access to your private channels/groups and takes payment in **SOL, USDC or USDT on Solana**.
It checks payments on-chain, adds and removes members itself, and has referrals, promo codes, trials and an admin panel.

## What it does

- **Several products:** sell subscription tiers (for example Basic and VIP, each unlocking its own channels) and one-off digital items (files, guides, links) from one bot. The starting catalog is `catalog.json`; after that you edit everything from the **/admin** panel.
- **Two ways to pay:**
  - **Telegram Stars:** Telegram's in-app payment, which buyers can pay for by card. You set the rate with `STARS_PER_USD`, and refunds are one command.
  - **Solana (SOL, USDC or USDT):** each invoice has a QR code for Solana Pay wallets. Payments are found automatically, by the invoice's `reference` key or by an exact, unique amount (so payments from exchanges work too). Every transfer is checked on-chain.
- **Automatic access:** buyers get invite links that work once and expire in 24h. When a subscription ends they are removed from its chats, except chats that another active subscription still covers. They get reminders 3 days and 1 day before expiry.
- **Win-back offers:** 1 day and 7 days after a subscription ends, the bot offers a comeback discount (`WINBACK_PERCENT`, 20% by default) that lasts a few days.
- **Upsell:** when a buyer picks a plan, the bot suggests a longer plan that is cheaper per day ("3 Months — save 14%").
- **Languages:** English, Lithuanian and Russian, picked from the user's Telegram language. Users can switch it themselves.
- **Admin panel with buttons:** stats with a 30-day revenue chart; products, plans, prices and chats; uploading digital files; promo codes; broadcasts to all, active or inactive users; referral payouts; and settings (trial, referral %, win-back %, Stars rate, upsell).
- **Growth tools:** referral links with a % commission and payout requests, promo codes (100% makes the item free), an optional free trial, and a membership guard that kicks people who join without paying.

## Setup (about 10 minutes)

1. **Create the bot:** message [@BotFather](https://t.me/BotFather), send `/newbot` and copy the token.
2. **Get your Telegram id:** message [@userinfobot](https://t.me/userinfobot).
3. **Wallet:** use the address of a Solana wallet you control (Phantom, Solflare…).
   To take USDC/USDT, that wallet needs a USDC/USDT token account. Receiving any small amount of the token once creates it.
4. **Premium chats:** make *private* channels or groups. Add the bot as an **admin** with *Invite users via link* and *Ban users* rights. Link each chat to a product in /admin → Products → Set chats; forwarding any channel post there adds it.
5. **Configure:**
   ```bash
   cp .env.example .env      # then fill in BOT_TOKEN, ADMIN_IDS and (for crypto) SOLANA_RECEIVER
   ```
   Edit `catalog.json` for the first catalog and the welcome text (en/lt/ru). It is loaded into the database on the first start only; after that, use /admin.
6. **Run:**
   ```bash
   docker compose up -d --build          # recommended, restarts automatically
   # or without Docker:
   pip install -r requirements.txt && python -m bot
   ```

For real traffic, use a free RPC key from Helius, QuickNode or Alchemy (`SOLANA_RPC_URL`) instead of the rate-limited public endpoint.

## Admin

Send `/admin` to open the button panel. You can also use these text commands:

| Command | What it does |
|---|---|
| `/stats` | Stats and revenue chart |
| `/user <id\|@name>` | User details |
| `/grant <id\|@name> <days\|life> [product_id]` | Give or extend access |
| `/revoke <id\|@name> [product_id]` | Remove access now |
| `/refund <invoice_id>` | Refund a Telegram Stars payment and remove the access it gave |
| `/paidout <id>` | Mark a referral payout as sent |

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
The tests cover: crypto and Stars checkout, digital delivery and refund, upsell, win-back, languages, the admin panel, promo codes, trial, the membership guard and payouts. They use a fake Telegram API and a fake Solana RPC.
