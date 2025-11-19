import logging
import os
from telegram import Update, LabeledPrice
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, PreCheckoutQueryHandler
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")

logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Select a plan:
1) Basic ₹99
2) Pro ₹199
3) Premium ₹299")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = "Pro Plan"
    description = "Access to All Premium Features"
    payload = "purchase_pro"
    currency = "INR"
    prices = [LabeledPrice("Pro Plan", 19900)]
    await update.message.reply_invoice(
        title=title,
        description=description,
        payload=payload,
        provider_token=PROVIDER_TOKEN,
        currency=currency,
        prices=prices,
    )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎉 Payment Successful! Plan Activated.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.run_polling()

if __name__ == "__main__":
    main()
