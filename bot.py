"""AXIFLOW — Telegram Bot"""
import os, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

log = logging.getLogger("axiflow.bot")
TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN","")
APP_URL  = os.environ.get("MINI_APP_URL","")

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("⚡ Відкрити AXIFLOW", web_app=WebAppInfo(url=APP_URL))],
        [InlineKeyboardButton("📊 Сигнали", callback_data="sigs"),
         InlineKeyboardButton("❓ Допомога", callback_data="help")],
    ]
    await update.message.reply_text(
        "⚡ *AXIFLOW — Smart Money Trading*\n\n"
        "Система нового покоління:\n"
        "◆ Аналіз 15+ пар 24/7\n"
        "◆ OI · Funding · Ліквідації · AMD/FVG\n"
        "◆ RR = 1:4 на кожну угоду\n"
        "◆ Bybit + Binance\n"
        "◆ Авто-торгівля + сповіщення\n\n"
        "Натисни щоб відкрити 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="help":
        await q.edit_message_text(
            "📖 *Як налаштувати:*\n\n"
            "1️⃣ Відкрий Mini App\n"
            "2️⃣ Йди в *API* → вкажи ключі або натисни Демо\n"
            "3️⃣ Йди в *Агент* → Запустити\n"
            "4️⃣ Бот надсилатиме сповіщення сюди\n\n"
            "🔑 *Як отримати Bybit API:*\n"
            "bybit.com → Профіль → API Management\n"
            "Create New Key → ✅ Read ✅ Trade ❌ Withdraw\n\n"
            "🔑 *Binance API:*\n"
            "binance.com → Профіль → API Management\n"
            "✅ Enable Futures ❌ Enable Withdrawals",
            parse_mode="Markdown"
        )

async def post_init(app):
    if APP_URL:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="⚡ AXIFLOW", web_app=WebAppInfo(url=APP_URL))
        )
        log.info(f"Menu button set: {APP_URL}")

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s — %(message)s")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    log.info("Bot polling...")
    app.run_polling()

if __name__=="__main__":
    main()
