# main.py
# Plus30Chatbot - Anonymous Chat Bot with Coin Economy (Upgrade 7)
# - Uses python-telegram-bot v20.x
# - SQLite persistence (plus30.db)
# - Commands: /start, /profile, /find, /leave, /balance, /daily, /fastmatch, /gift, /topcoins, /ticket
# - Admin: /picklottery (admin only, for debugging)
#
import os
import logging
import sqlite3
import time
import random
from datetime import datetime, date, timedelta
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN') or "AAGuLUaVrNmjdAqbprpdaabLs48X_jG9lVY"
ADMIN_ID = os.getenv('ADMIN_ID')  # optional

DB_PATH = os.getenv('DATABASE_PATH', 'plus30_upgrade7.db')
DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', '15'))

# --- Database helpers ---
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        gender TEXT,
        age INTEGER,
        vip INTEGER DEFAULT 0,
        last_seen TEXT,
        coins INTEGER DEFAULT 0,
        last_daily TEXT,
        streak INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS matches (
        user1 INTEGER,
        user2 INTEGER,
        started_at DATETIME,
        PRIMARY KEY (user1, user2)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS daily_counts (
        user_id INTEGER,
        day TEXT,
        count INTEGER,
        PRIMARY KEY (user_id, day)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS tickets (
        user_id INTEGER,
        ticket_time DATETIME
    )""")
    conn.commit()
    conn.close()

# In-memory queues (lost on restart)
WAITING = []  # list of dicts {user_id, gender_pref, age_pref, ts}
ACTIVE = {}   # user_id -> partner_id mapping

# --- Coin economy helpers ---
def ensure_user(user_id: int, username: Optional[str]=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users(user_id, username, last_seen) VALUES (?, ?, ?)" ,
                (user_id, username or '', date.today().isoformat()))
    conn.commit()
    conn.close()

def get_coins(user_id: int) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row['coins'] if row else 0

def add_coins(user_id: int, n: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users(user_id, last_seen) VALUES (?, ?)", (user_id, date.today().isoformat()))
    cur.execute("UPDATE users SET coins = coins + ? WHERE user_id=?", (n, user_id))
    conn.commit()
    conn.close()

def deduct_coins(user_id: int, n: int) -> bool:
    if get_coins(user_id) < n:
        return False
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET coins = coins - ? WHERE user_id=?", (n, user_id))
    conn.commit()
    conn.close()
    return True

# daily reward and streak
def give_daily(user_id: int) -> (int, int):
    conn = get_conn()
    cur = conn.cursor()
    today = date.today().isoformat()
    cur.execute("INSERT OR IGNORE INTO users(user_id, last_seen) VALUES (?, ?)", (user_id, today))
    cur.execute("SELECT last_daily, streak FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    last = row['last_daily'] if row else None
    streak = row['streak'] if row else 0
    if last == today:
        conn.close()
        return (0, streak)
    # determine reward
    streak = streak + 1 if last == (date.today() - timedelta(days=1)).isoformat() else 1
    reward = 5 if streak == 1 else min(20, 5 + (streak-1)*2)
    cur.execute("UPDATE users SET coins = coins + ?, last_daily=?, streak=? WHERE user_id=?", (reward, today, streak, user_id))
    conn.commit()
    conn.close()
    return (reward, streak)

# tickets / lottery
def buy_ticket(user_id: int) -> bool:
    cost = 5
    if not deduct_coins(user_id, cost):
        return False
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO tickets(user_id, ticket_time) VALUES(?, datetime('now'))", (user_id,))
    conn.commit()
    conn.close()
    return True

def pick_lottery_winner():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM tickets")
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return None
    winner = random.choice(rows)['user_id']
    add_coins(winner, 100)
    # clear tickets
    cur.execute("DELETE FROM tickets")
    conn.commit()
    conn.close()
    return winner

# --- matchmaking helpers ---
def remove_waiting(uid: int):
    global WAITING
    WAITING = [w for w in WAITING if w['user_id'] != uid]

def find_partner_for(user_id: int, gender_pref: Optional[str], age_pref: Optional[int]) -> Optional[int]:
    # simple matching: find first waiting user that matches criteria
    for i, w in enumerate(WAITING):
        if w['user_id'] == user_id:
            continue
        # gender check if both specified
        if gender_pref and w.get('gender_pref') and gender_pref != 'any' and w['gender_pref'] != 'any' and gender_pref != w['gender_pref']:
            continue
        # age pref if both specified: allow +-5
        if age_pref and w.get('age_pref'):
            try:
                if abs(int(age_pref) - int(w['age_pref'])) > 5:
                    continue
            except:
                pass
        # match
        partner = w['user_id']
        WAITING.pop(i)
        return partner
    return None

def pair_users(u1: int, u2: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO matches(user1,user2,started_at) VALUES (?,?,datetime('now'))", (u1,u2))
    cur.execute("INSERT OR REPLACE INTO matches(user1,user2,started_at) VALUES (?,?,datetime('now'))", (u2,u1))
    conn.commit()
    conn.close()
    ACTIVE[u1] = u2
    ACTIVE[u2] = u1

def unpair_user(user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user2 FROM matches WHERE user1=?", (user_id,))
    row = cur.fetchone()
    if row:
        other = row['user2']
        cur.execute("DELETE FROM matches WHERE user1=? OR user2=?", (user_id, user_id))
        cur.execute("DELETE FROM matches WHERE user1=? OR user2=?", (other, other))
        conn.commit()
        conn.close()
        ACTIVE.pop(user_id, None)
        ACTIVE.pop(other, None)
        return other
    conn.close()
    return None

# --- handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Find", callback_data='find')],[InlineKeyboardButton("Balance", callback_data='balance')]])
    await update.message.reply_text("Welcome to Plus30Chatbot! Use /find to search or /balance to view coins.", reply_markup=kb)

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    ensure_user(uid, update.effective_user.username)
    if args and args[0].lower() == 'set':
        gender = args[1] if len(args)>1 else None
        age = int(args[2]) if len(args)>2 else None
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO users(user_id, username, gender, age, last_seen) VALUES(?,?,?,?,?)", (uid, update.effective_user.username or '', gender, age, date.today().isoformat()))
        conn.commit(); conn.close()
        await update.message.reply_text(f"Profile set: gender={gender}, age={age}")
        return
    row = get_conn().cursor().execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if row:
        await update.message.reply_text(f"Profile:\nGender: {row['gender']}\nAge: {row['age']}\nCoins: {row['coins']}")
    else:
        await update.message.reply_text("No profile. Use /profile set <gender> <age>")

async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    gender_pref = args[0] if len(args)>0 else None
    age_pref = int(args[1]) if len(args)>1 else None
    ensure_user(uid, update.effective_user.username)
    # check VIP/coins for free limit skipped here; in this version free unlimited find but coins for fastmatch
    partner = find_partner_for(uid, gender_pref, age_pref)
    if partner:
        pair_users(uid, partner)
        await update.message.reply_text("Partner found! You're now connected anonymously.")
        try:
            await context.bot.send_message(partner, "Partner found! You're now connected anonymously.")
        except:
            pass
        return
    # add to waiting
    remove_waiting(uid)
    WAITING.append({'user_id': uid, 'gender_pref': gender_pref or 'any', 'age_pref': age_pref or None, 'ts': int(time.time())})
    await update.message.reply_text("Searching for partner... Use /leave to cancel or /fastmatch to pay 5 coins for instant match.")

async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    other = unpair_user(uid)
    remove_waiting(uid)
    if other:
        await update.message.reply_text("You left the chat.")
        try:
            await context.bot.send_message(other, "Your partner left the chat.")
        except:
            pass
    else:
        await update.message.reply_text("You were not in a chat.")

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ACTIVE:
        return
    other = ACTIVE.get(uid)
    if not other:
        return
    # forward text
    if update.message.text:
        await context.bot.send_message(other, update.message.text)
    # forward media minimal support
    if update.message.photo:
        await context.bot.send_photo(other, update.message.photo[-1].file_id)
    if update.message.sticker:
        await context.bot.send_sticker(other, update.message.sticker.file_id)

# economy handlers
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    coins = get_coins(uid)
    await update.message.reply_text(f"Your balance: {coins} coins. Use /daily to claim daily reward.")

async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    reward, streak = give_daily(uid)
    if reward == 0:
        await update.message.reply_text("You already claimed daily reward today.")
    else:
        await update.message.reply_text(f"You received {reward} coins! Current streak: {streak} days.")

async def fastmatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    cost = 5
    if not deduct_coins(uid, cost):
        await update.message.reply_text("Not enough coins. Buy coins or use /daily for free coins.")
        return
    # try to match immediately by checking waiting list for first
    partner = None
    if WAITING:
        w = WAITING.pop(0)
        partner = w['user_id']
    if partner:
        pair_users(uid, partner)
        await update.message.reply_text("Fast match successful! You are connected.")
        try:
            await context.bot.send_message(partner, "You were matched with a fast matcher! Say hi.")
        except:
            pass
    else:
        # if none, place user at front of queue
        remove_waiting(uid)
        WAITING.insert(0, {'user_id': uid, 'gender_pref': 'any', 'age_pref': None, 'ts': int(time.time())})
        await update.message.reply_text("No one waiting right now. You're placed at front of the queue.")

async def gift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /gift <user_id> <amount>")
        return
    target = int(args[0])
    amount = int(args[1])
    if amount <= 0:
        await update.message.reply_text("Invalid amount.")
        return
    if not deduct_coins(uid, amount):
        await update.message.reply_text("Not enough coins.")
        return
    add_coins(target, amount)
    await update.message.reply_text(f"You gifted {amount} coins to {target}.")
    try:
        await context.bot.send_message(target, f"You received {amount} coins from {uid}.")
    except:
        pass

async def buy_ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    if buy_ticket(uid):
        await update.message.reply_text("Ticket bought for 5 coins. Good luck!")
    else:
        await update.message.reply_text("Not enough coins to buy a ticket (cost 5). Use /balance to check.")

async def topcoins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, coins FROM users ORDER BY coins DESC LIMIT 10")
    rows = cur.fetchall()
    conn.close()
    text = 'Top coin holders:\n' + '\n'.join([f"{r['user_id']}: {r['coins']}" for r in rows])
    await update.message.reply_text(text)

# admin pick lottery (manual)
async def picklot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ADMIN_ID or str(uid) != str(ADMIN_ID):
        await update.message.reply_text("Unauthorized.")
        return
    winner = pick_lottery_winner()
    if winner:
        await update.message.reply_text(f"Winner: {winner} (+100 coins)")
        try:
            await context.bot.send_message(winner, "🎉 You won the daily lottery! +100 coins")
        except:
            pass
    else:
        await update.message.reply_text("No tickets were sold.")

# periodic tasks
async def lottery_job(context: ContextTypes.DEFAULT_TYPE):
    winner = pick_lottery_winner()
    if winner:
        try:
            await context.bot.send_message(winner, "🎉 You won the lottery! +100 coins")
        except:
            pass

async def cleanup_waiting_job(context: ContextTypes.DEFAULT_TYPE):
    # remove old queue entries older than 10 minutes
    cutoff = int(time.time()) - 600
    global WAITING
    WAITING = [w for w in WAITING if w['ts'] >= cutoff]

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('profile', profile_cmd))
    app.add_handler(CommandHandler('find', find_cmd))
    app.add_handler(CommandHandler('leave', leave_cmd))
    app.add_handler(CommandHandler('balance', balance_cmd))
    app.add_handler(CommandHandler('daily', daily_cmd))
    app.add_handler(CommandHandler('fastmatch', fastmatch_cmd))
    app.add_handler(CommandHandler('gift', gift_cmd))
    app.add_handler(CommandHandler('ticket', buy_ticket_cmd))
    app.add_handler(CommandHandler('topcoins', topcoins_cmd))
    app.add_handler(CommandHandler('picklot', picklot_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_router))
    # jobs
    app.job_queue.run_repeating(lottery_job, interval=86400, first=60)  # daily lottery
    app.job_queue.run_repeating(cleanup_waiting_job, interval=60, first=30)
    log.info('Bot starting (Upgrade7 economy)...')
    app.run_polling()

if __name__ == '__main__':
    main()
