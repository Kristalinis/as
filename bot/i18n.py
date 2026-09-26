"""User-facing texts in English, Lithuanian and Russian. Admin texts stay English."""
from __future__ import annotations

LANGS = {"en": "🇬🇧 English", "lt": "🇱🇹 Lietuvių", "ru": "🇷🇺 Русский"}
DEFAULT = "en"


def detect(language_code: str | None) -> str:
    code = (language_code or "").lower().split("-")[0]
    if code == "lt":
        return "lt"
    if code in ("ru", "uk", "be", "kk"):
        return "ru"
    return "en"


T: dict[str, dict[str, str]] = {
    "welcome_default": {
        "en": "👋 Welcome to <b>{name}</b>!\n\nGet instant access to our private community. Pay with Solana "
              "(SOL / USDC / USDT) or Telegram Stars — access is granted automatically.",
        "lt": "👋 Sveiki atvykę į <b>{name}</b>!\n\nGaukite akimirksniu prieigą prie mūsų privačios bendruomenės. "
              "Mokėkite Solana (SOL / USDC / USDT) arba Telegram Stars — prieiga suteikiama automatiškai.",
        "ru": "👋 Добро пожаловать в <b>{name}</b>!\n\nМгновенный доступ в наше закрытое сообщество. Оплата в Solana "
              "(SOL / USDC / USDT) или Telegram Stars — доступ выдаётся автоматически.",
    },
    "btn_catalog": {"en": "💎 Plans & shop", "lt": "💎 Planai ir parduotuvė", "ru": "💎 Тарифы и магазин"},
    "btn_trial": {"en": "🎁 Free {days}-day trial", "lt": "🎁 Nemokamas {days} d. bandymas",
                  "ru": "🎁 Бесплатно {days} дн."},
    "btn_account": {"en": "👤 My account", "lt": "👤 Mano paskyra", "ru": "👤 Мой аккаунт"},
    "btn_ref": {"en": "🤝 Invite & earn", "lt": "🤝 Pakviesk ir uždirbk", "ru": "🤝 Приглашай и зарабатывай"},
    "btn_help": {"en": "❓ Help", "lt": "❓ Pagalba", "ru": "❓ Помощь"},
    "btn_lang": {"en": "🌐 Language", "lt": "🌐 Kalba", "ru": "🌐 Язык"},
    "btn_back": {"en": "« Back", "lt": "« Atgal", "ru": "« Назад"},
    "btn_cancel": {"en": "« Cancel", "lt": "« Atšaukti", "ru": "« Отмена"},
    "btn_promo": {"en": "🏷 I have a promo code", "lt": "🏷 Turiu nuolaidos kodą", "ru": "🏷 У меня есть промокод"},
    "btn_pay_token": {"en": "{icon} Pay with {token}", "lt": "{icon} Mokėti {token}", "ru": "{icon} Оплатить {token}"},
    "btn_pay_stars": {"en": "⭐ Pay with Telegram Stars ({stars} ⭐)", "lt": "⭐ Mokėti Telegram Stars ({stars} ⭐)",
                      "ru": "⭐ Оплатить Telegram Stars ({stars} ⭐)"},
    "btn_check": {"en": "🔄 I've paid — check now", "lt": "🔄 Sumokėjau — patikrinti",
                  "ru": "🔄 Я оплатил — проверить"},
    "btn_back_shop": {"en": "« Back to shop", "lt": "« Atgal į parduotuvę", "ru": "« Назад в магазин"},
    "btn_links": {"en": "🔗 Get invite links", "lt": "🔗 Gauti kvietimo nuorodas", "ru": "🔗 Получить ссылки"},
    "btn_extend": {"en": "💎 Extend / buy more", "lt": "💎 Pratęsti / pirkti", "ru": "💎 Продлить / купить"},
    "btn_renew": {"en": "💎 Renew", "lt": "💎 Atnaujinti", "ru": "💎 Продлить"},
    "btn_join": {"en": "➡️ Join {title}", "lt": "➡️ Prisijungti: {title}", "ru": "➡️ Вступить: {title}"},
    "btn_withdraw": {"en": "💸 Withdraw", "lt": "💸 Išsiimti", "ru": "💸 Вывести"},
    "btn_winback": {"en": "🔥 Claim {pct}% off", "lt": "🔥 Pasinaudoti -{pct}%", "ru": "🔥 Получить скидку {pct}%"},
    "btn_upsell": {"en": "⬆️ {title} — save {save}%", "lt": "⬆️ {title} — sutaupykite {save}%",
                   "ru": "⬆️ {title} — экономия {save}%"},
    "was": {"en": "was", "lt": "buvo", "ru": "было"},
    "days": {"en": "{n} days", "lt": "{n} d.", "ru": "{n} дн."},
    "lifetime": {"en": "lifetime", "lt": "visam laikui", "ru": "навсегда"},
    "never": {"en": "never (lifetime)", "lt": "niekada (visam laikui)", "ru": "никогда (навсегда)"},
    "catalog_title": {"en": "💎 <b>Choose a product</b>", "lt": "💎 <b>Pasirinkite produktą</b>",
                      "ru": "💎 <b>Выберите продукт</b>"},
    "catalog_empty": {"en": "Nothing for sale yet — check back soon.", "lt": "Kol kas nieko neparduodama.",
                      "ru": "Пока ничего нет в продаже."},
    "plans_title": {"en": "<b>{product}</b>\nChoose your plan:", "lt": "<b>{product}</b>\nPasirinkite planą:",
                    "ru": "<b>{product}</b>\nВыберите тариф:"},
    "promo_applied": {"en": "🏷 Promo <b>{code}</b> applied: -{pct}%",
                      "lt": "🏷 Pritaikytas kodas <b>{code}</b>: -{pct}%",
                      "ru": "🏷 Промокод <b>{code}</b> применён: -{pct}%"},
    "winback_applied": {"en": "🎁 Your comeback discount: -{pct}% (until {date})",
                        "lt": "🎁 Jūsų sugrįžimo nuolaida: -{pct}% (iki {date})",
                        "ru": "🎁 Ваша скидка за возвращение: -{pct}% (до {date})"},
    "choose_pay": {"en": "<b>{title}</b> · {duration}\nPrice: <b>${price}</b>\n\nHow would you like to pay?",
                   "lt": "<b>{title}</b> · {duration}\nKaina: <b>${price}</b>\n\nKaip norėtumėte mokėti?",
                   "ru": "<b>{title}</b> · {duration}\nЦена: <b>${price}</b>\n\nКак хотите оплатить?"},
    "upsell": {"en": "💡 Better deal: <b>{title}</b> for ${price} — you save <b>{save}%</b>.",
               "lt": "💡 Geresnis pasiūlymas: <b>{title}</b> už ${price} — sutaupote <b>{save}%</b>.",
               "ru": "💡 Выгоднее: <b>{title}</b> за ${price} — экономия <b>{save}%</b>."},
    "unavailable": {"en": "This item is no longer available.", "lt": "Šis produktas nebepasiekiamas.",
                    "ru": "Этот товар больше недоступен."},
    "promo_ask": {"en": "🏷 Send your promo code:", "lt": "🏷 Atsiųskite nuolaidos kodą:",
                  "ru": "🏷 Отправьте промокод:"},
    "promo_bad": {"en": "❌ This code is invalid or expired. Try another or go back.",
                  "lt": "❌ Kodas neteisingas arba nebegalioja. Bandykite kitą arba grįžkite.",
                  "ru": "❌ Промокод недействителен или истёк. Попробуйте другой или вернитесь."},
    "promo_ok": {"en": "✅ Code accepted: -{pct}%", "lt": "✅ Kodas priimtas: -{pct}%",
                 "ru": "✅ Промокод принят: -{pct}%"},
    "invoice_error": {"en": "⚠️ Couldn't create an invoice right now. Please try again in a minute.",
                      "lt": "⚠️ Nepavyko sukurti sąskaitos. Bandykite po minutės.",
                      "ru": "⚠️ Не удалось создать счёт. Попробуйте через минуту."},
    "invoice": {
        "en": "🧾 <b>Invoice #{id}</b> — {title}\n\nAmount: <code>{amount}</code> <b>{token}</b>  (≈ ${usd})\n"
              "Network: <b>Solana</b>\nAddress:\n<code>{address}</code>\n\n"
              "⚠️ Send the <b>exact</b> amount — it identifies your payment.\n"
              "📱 Or scan the QR with Phantom / Solflare / Backpack.\n\n"
              "⏳ Valid for {mins} min. Access is granted automatically after payment.",
        "lt": "🧾 <b>Sąskaita #{id}</b> — {title}\n\nSuma: <code>{amount}</code> <b>{token}</b>  (≈ ${usd})\n"
              "Tinklas: <b>Solana</b>\nAdresas:\n<code>{address}</code>\n\n"
              "⚠️ Siųskite <b>tikslią</b> sumą — pagal ją atpažįstamas jūsų mokėjimas.\n"
              "📱 Arba nuskenuokite QR su Phantom / Solflare / Backpack.\n\n"
              "⏳ Galioja {mins} min. Prieiga suteikiama automatiškai po apmokėjimo.",
        "ru": "🧾 <b>Счёт #{id}</b> — {title}\n\nСумма: <code>{amount}</code> <b>{token}</b>  (≈ ${usd})\n"
              "Сеть: <b>Solana</b>\nАдрес:\n<code>{address}</code>\n\n"
              "⚠️ Отправьте <b>точную</b> сумму — по ней определяется ваш платёж.\n"
              "📱 Или отсканируйте QR в Phantom / Solflare / Backpack.\n\n"
              "⏳ Действителен {mins} мин. Доступ выдаётся автоматически после оплаты.",
    },
    "invoice_paid": {"en": "✅ Invoice #{id} paid — {amount}", "lt": "✅ Sąskaita #{id} apmokėta — {amount}",
                     "ru": "✅ Счёт #{id} оплачен — {amount}"},
    "checking": {"en": "Checking the blockchain…", "lt": "Tikrinama blokų grandinė…", "ru": "Проверяем блокчейн…"},
    "already_paid": {"en": "✅ Already paid — check your messages.", "lt": "✅ Jau apmokėta — patikrinkite žinutes.",
                     "ru": "✅ Уже оплачено — проверьте сообщения."},
    "invoice_not_found": {"en": "Invoice not found.", "lt": "Sąskaita nerasta.", "ru": "Счёт не найден."},
    "not_found_yet": {
        "en": "⏳ Payment not found yet. Transfers usually confirm in under a minute — the bot keeps checking "
              "automatically and will message you as soon as it arrives.\n\nMake sure you sent exactly "
              "<code>{amount}</code> on the Solana network.",
        "lt": "⏳ Mokėjimas dar nerastas. Pervedimai dažniausiai patvirtinami per minutę — botas tikrina "
              "automatiškai ir parašys, kai tik mokėjimas ateis.\n\nĮsitikinkite, kad išsiuntėte tiksliai "
              "<code>{amount}</code> Solana tinklu.",
        "ru": "⏳ Платёж пока не найден. Обычно перевод подтверждается меньше чем за минуту — бот проверяет "
              "автоматически и напишет, как только он поступит.\n\nУбедитесь, что отправили ровно "
              "<code>{amount}</code> в сети Solana.",
    },
    "payment_received": {"en": "✅ <b>Payment received!</b>\n{title}", "lt": "✅ <b>Mokėjimas gautas!</b>\n{title}",
                         "ru": "✅ <b>Оплата получена!</b>\n{title}"},
    "view_tx": {"en": "View transaction", "lt": "Peržiūrėti transakciją", "ru": "Посмотреть транзакцию"},
    "access_until": {"en": "⏳ Access until: <b>{date}</b>", "lt": "⏳ Prieiga iki: <b>{date}</b>",
                     "ru": "⏳ Доступ до: <b>{date}</b>"},
    "links_below": {"en": "Your personal one-time invite links (valid 24h) are below 👇",
                    "lt": "Jūsų asmeninės vienkartinės kvietimo nuorodos (galioja 24 val.) žemiau 👇",
                    "ru": "Ваши личные одноразовые ссылки-приглашения (действуют 24 ч) ниже 👇"},
    "delivered": {"en": "📦 <b>{title}</b> — here is your purchase:", "lt": "📦 <b>{title}</b> — jūsų pirkinys:",
                  "ru": "📦 <b>{title}</b> — ваша покупка:"},
    "account_title": {"en": "👤 <b>My account</b>", "lt": "👤 <b>Mano paskyra</b>", "ru": "👤 <b>Мой аккаунт</b>"},
    "sub_line": {"en": "✅ <b>{product}</b> — until {date}", "lt": "✅ <b>{product}</b> — iki {date}",
                 "ru": "✅ <b>{product}</b> — до {date}"},
    "no_subs": {"en": "You don't have an active subscription.", "lt": "Neturite aktyvios prenumeratos.",
                "ru": "У вас нет активной подписки."},
    "purchases": {"en": "📦 Your purchases (tap to get again):", "lt": "📦 Jūsų pirkiniai (paspauskite, kad gautumėte dar kartą):",
                  "ru": "📦 Ваши покупки (нажмите, чтобы получить снова):"},
    "links_wait": {"en": "New links were sent recently — please wait a few minutes.",
                   "lt": "Naujos nuorodos ką tik išsiųstos — palaukite kelias minutes.",
                   "ru": "Новые ссылки уже отправлены — подождите несколько минут."},
    "sub_inactive": {"en": "Your subscription is not active.", "lt": "Jūsų prenumerata neaktyvi.",
                     "ru": "Ваша подписка не активна."},
    "fresh_links": {"en": "🔗 <b>Here are your fresh invite links.</b>", "lt": "🔗 <b>Štai naujos kvietimo nuorodos.</b>",
                    "ru": "🔗 <b>Ваши новые ссылки-приглашения.</b>"},
    "trial_unavail": {"en": "Trial is not available for your account.", "lt": "Bandomasis laikotarpis jums negalimas.",
                      "ru": "Пробный период для вас недоступен."},
    "trial_active": {"en": "🎁 <b>Your {days}-day free trial is active!</b>",
                     "lt": "🎁 <b>Jūsų {days} d. nemokamas bandymas aktyvuotas!</b>",
                     "ru": "🎁 <b>Бесплатный пробный период на {days} дн. активирован!</b>"},
    "granted": {"en": "🎁 <b>You've been granted access!</b>", "lt": "🎁 <b>Jums suteikta prieiga!</b>",
                "ru": "🎁 <b>Вам выдан доступ!</b>"},
    "free_promo": {"en": "🎁 <b>{title}</b> activated with promo {code}!",
                   "lt": "🎁 <b>{title}</b> aktyvuota su kodu {code}!",
                   "ru": "🎁 <b>{title}</b> активировано по промокоду {code}!"},
    "ref_title": {"en": "🤝 <b>Invite friends &amp; earn</b>", "lt": "🤝 <b>Pakviesk draugus ir uždirbk</b>",
                  "ru": "🤝 <b>Приглашай друзей и зарабатывай</b>"},
    "ref_you_get": {"en": "You get {perks}.", "lt": "Jūs gaunate {perks}.", "ru": "Вы получаете {perks}."},
    "ref_perk_pct": {"en": "<b>{pct}%</b> of every payment they make (paid out in crypto)",
                     "lt": "<b>{pct}%</b> nuo kiekvieno jų mokėjimo (išmokama kriptovaliuta)",
                     "ru": "<b>{pct}%</b> с каждого их платежа (выплата в крипте)"},
    "ref_perk_days": {"en": "<b>{days} free days</b> when they buy for the first time",
                      "lt": "<b>{days} nemokamų dienų</b>, kai jie nusiperka pirmą kartą",
                      "ru": "<b>{days} бесплатных дней</b> за их первую покупку"},
    "and": {"en": "and", "lt": "ir", "ru": "и"},
    "ref_stats": {"en": "Your link:\n<code>{link}</code>\n\nInvited: <b>{count}</b>",
                  "lt": "Jūsų nuoroda:\n<code>{link}</code>\n\nPakviesta: <b>{count}</b>",
                  "ru": "Ваша ссылка:\n<code>{link}</code>\n\nПриглашено: <b>{count}</b>"},
    "ref_balance": {"en": "Earned total: <b>${earned}</b>\nBalance: <b>${balance}</b> (min payout ${min})",
                    "lt": "Iš viso uždirbta: <b>${earned}</b>\nBalansas: <b>${balance}</b> (min. išmoka ${min})",
                    "ru": "Всего заработано: <b>${earned}</b>\nБаланс: <b>${balance}</b> (мин. выплата ${min})"},
    "ref_reward": {"en": "🎉 Someone you invited just paid! You earned {what}.",
                   "lt": "🎉 Jūsų pakviestas žmogus ką tik sumokėjo! Uždirbote {what}.",
                   "ru": "🎉 Приглашённый вами человек оплатил! Вы получили {what}."},
    "reward_commission": {"en": "${amount} commission", "lt": "${amount} komisinių", "ru": "${amount} комиссии"},
    "reward_days": {"en": "{n} bonus days", "lt": "{n} papildomų dienų", "ru": "{n} бонусных дней"},
    "withdraw_ask": {"en": "💸 Send your Solana wallet address for the payout:",
                     "lt": "💸 Atsiųskite savo Solana piniginės adresą išmokai:",
                     "ru": "💸 Отправьте адрес вашего Solana-кошелька для выплаты:"},
    "wallet_bad": {"en": "❌ That doesn't look like a Solana address. Try again:",
                   "lt": "❌ Tai nepanašu į Solana adresą. Bandykite dar kartą:",
                   "ru": "❌ Это не похоже на адрес Solana. Попробуйте ещё раз:"},
    "below_min": {"en": "Balance is below the minimum payout.", "lt": "Balansas mažesnis už minimalią išmoką.",
                  "ru": "Баланс меньше минимальной выплаты."},
    "payout_submitted": {"en": "✅ Payout request #{id} for ${amount} submitted. You'll be notified when it's sent.",
                         "lt": "✅ Išmokos prašymas #{id} (${amount}) pateiktas. Pranešime, kai išsiųsime.",
                         "ru": "✅ Заявка на выплату #{id} (${amount}) отправлена. Мы сообщим, когда переведём."},
    "payout_sent": {"en": "💸 Your referral payout of ${amount} was sent to <code>{wallet}</code>. Thank you!",
                    "lt": "💸 Jūsų ${amount} išmoka išsiųsta į <code>{wallet}</code>. Ačiū!",
                    "ru": "💸 Ваша выплата ${amount} отправлена на <code>{wallet}</code>. Спасибо!"},
    "help": {
        "en": "<b>How it works</b>\n1. Pick a product and a plan.\n2. Pay with Telegram Stars, or with SOL / USDC / "
              "USDT on the <b>Solana</b> network (scan the QR or send the <b>exact</b> amount).\n"
              "3. The bot detects the payment automatically and sends your access.\n\n"
              "Paying from an exchange? The amount that <b>arrives</b> must match the invoice exactly.\n"
              "Renewing early adds time on top of your current subscription.",
        "lt": "<b>Kaip tai veikia</b>\n1. Pasirinkite produktą ir planą.\n2. Mokėkite Telegram Stars arba SOL / USDC / "
              "USDT <b>Solana</b> tinklu (nuskenuokite QR arba siųskite <b>tikslią</b> sumą).\n"
              "3. Botas automatiškai aptinka mokėjimą ir atsiunčia prieigą.\n\n"
              "Mokate iš biržos? <b>Gauta</b> suma turi tiksliai sutapti su sąskaita.\n"
              "Pratęsus anksčiau, laikas pridedamas prie esamos prenumeratos.",
        "ru": "<b>Как это работает</b>\n1. Выберите продукт и тариф.\n2. Оплатите Telegram Stars или SOL / USDC / "
              "USDT в сети <b>Solana</b> (отсканируйте QR или отправьте <b>точную</b> сумму).\n"
              "3. Бот автоматически увидит платёж и выдаст доступ.\n\n"
              "Платите с биржи? <b>Зачисленная</b> сумма должна точно совпадать со счётом.\n"
              "При досрочном продлении время добавляется к текущей подписке.",
    },
    "support": {"en": "Support: {contact}", "lt": "Pagalba: {contact}", "ru": "Поддержка: {contact}"},
    "expired": {"en": "⌛ Your <b>{product}</b> subscription has expired and access was removed.\n"
                      "Renew any time to get back in:",
                "lt": "⌛ Jūsų <b>{product}</b> prenumerata baigėsi, prieiga panaikinta.\n"
                      "Atnaujinkite bet kada ir grįžkite:",
                "ru": "⌛ Ваша подписка <b>{product}</b> закончилась, доступ закрыт.\nПродлите в любой момент:"},
    "reminder": {"en": "⏰ Your <b>{product}</b> subscription ends in less than {left} ({date}).\n"
                       "Renew now so you don't lose access:",
                 "lt": "⏰ Jūsų <b>{product}</b> prenumerata baigsis greičiau nei per {left} ({date}).\n"
                       "Pratęskite dabar, kad neprarastumėte prieigos:",
                 "ru": "⏰ Ваша подписка <b>{product}</b> закончится меньше чем через {left} ({date}).\n"
                       "Продлите сейчас, чтобы не потерять доступ:"},
    "left_3d": {"en": "3 days", "lt": "3 dienas", "ru": "3 дня"},
    "left_1d": {"en": "24 hours", "lt": "24 valandas", "ru": "24 часа"},
    "winback": {"en": "🎁 We miss you! Come back to <b>{product}</b> with <b>{pct}% off</b> any plan — "
                      "offer valid until {date}.",
                "lt": "🎁 Pasiilgome jūsų! Grįžkite į <b>{product}</b> su <b>{pct}% nuolaida</b> bet kuriam planui — "
                      "pasiūlymas galioja iki {date}.",
                "ru": "🎁 Мы скучаем! Возвращайтесь в <b>{product}</b> со скидкой <b>{pct}%</b> на любой тариф — "
                      "предложение действует до {date}."},
    "guard_kick": {"en": "🔒 That chat is for subscribers only. Get access here: /start",
                   "lt": "🔒 Šis pokalbis tik prenumeratoriams. Prieigą gausite čia: /start",
                   "ru": "🔒 Этот чат только для подписчиков. Получить доступ: /start"},
    "lang_choose": {"en": "🌐 Choose your language:", "lt": "🌐 Pasirinkite kalbą:", "ru": "🌐 Выберите язык:"},
    "refunded": {"en": "↩️ Your payment for <b>{title}</b> was refunded.",
                 "lt": "↩️ Jūsų mokėjimas už <b>{title}</b> grąžintas.",
                 "ru": "↩️ Ваш платёж за <b>{title}</b> возвращён."},
    "stars_desc": {"en": "{title} · {duration}", "lt": "{title} · {duration}", "ru": "{title} · {duration}"},
    "stars_failed": {"en": "This invoice is no longer valid, please create a new one.",
                     "lt": "Ši sąskaita nebegalioja, sukurkite naują.",
                     "ru": "Этот счёт больше не действителен, создайте новый."},
}


def t(lang: str | None, key: str, **kw) -> str:
    entry = T[key]
    text = entry.get(lang or DEFAULT) or entry[DEFAULT]
    return text.format(**kw) if kw else text
