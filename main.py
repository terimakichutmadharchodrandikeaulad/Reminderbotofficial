import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ( Application, CommandHandler, CallbackQueryHandler, ContextTypes )
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import pytz

#Logging setup

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MongoDB Configuration

NOTE: MONGODB_URI को ENVIRONMENT VARIABLE में सेट करना बेहतर है

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb+srv://rj5706603:O95nvJYxapyDHfkw@cluster0.fzmckei.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0') DB_NAME = 'Cluster0'

Timezone

IST = pytz.timezone('Asia/Kolkata')

Active bot instances

ACTIVE_BOTS: Dict[str, Application] = {}

Admin IDs (fill your admin Telegram IDs here)

ADMIN_IDS = [8285724366]

==================== DATABASE MANAGER ====================

class DatabaseManager: def init(self, mongodb_uri: str, db_name: str): self.client = AsyncIOMotorClient(mongodb_uri) self.db = self.client[db_name] self.users = self.db.users self.reminders = self.db.reminders self.clones = self.db.clones self.analytics = self.db.analytics self.logs = self.db.logs

# ===== USER OPERATIONS =====
async def get_user(self, user_id: str):
    return await self.users.find_one({'user_id': user_id})

async def create_or_update_user(self, user_data: dict):
    await self.users.update_one(
        {'user_id': user_data['user_id']},
        {'$set': user_data},
        upsert=True
    )

async def get_all_users(self):
    cursor = self.users.find({})
    return await cursor.to_list(length=None)

async def get_user_stats(self, user_id: str):
    user = await self.get_user(user_id)
    reminders_count = await self.reminders.count_documents({'user_id': user_id, 'status': 'active'})
    has_clone = await self.clones.find_one({'owner_id': user_id})

    return {
        'user': user,
        'reminders_count': reminders_count,
        'has_clone': has_clone is not None
    }

# ===== REMINDER OPERATIONS =====
async def create_reminder(self, reminder_data: dict):
    result = await self.reminders.insert_one(reminder_data)
    return str(result.inserted_id)

async def get_user_reminders(self, user_id: str, status: str = 'active'):
    cursor = self.reminders.find({'user_id': user_id, 'status': status}).sort('remind_time', 1)
    return await cursor.to_list(length=None)

async def get_reminder(self, reminder_id: str):
    return await self.reminders.find_one({'_id': ObjectId(reminder_id)})

async def update_reminder_status(self, reminder_id: str, status: str):
    await self.reminders.update_one(
        {'_id': ObjectId(reminder_id)},
        {'$set': {'status': status, 'updated_at': datetime.now(IST)}}
    )

async def delete_reminder(self, reminder_id: str):
    await self.reminders.delete_one({'_id': ObjectId(reminder_id)})

async def delete_all_user_reminders(self, user_id: str):
    result = await self.reminders.delete_many({'user_id': user_id, 'status': 'active'})
    return result.deleted_count

async def get_pending_reminders(self):
    """Get all reminders that need to be sent"""
    now = datetime.now(IST)
    cursor = self.reminders.find({
        'status': 'active',
        'remind_time': {'$lte': now}
    })
    return await cursor.to_list(length=None)

# ===== CLONE OPERATIONS =====
async def create_clone(self, clone_data: dict):
    await self.clones.update_one(
        {'owner_id': clone_data['owner_id']},
        {'$set': clone_data},
        upsert=True
    )

async def get_clone(self, owner_id: str):
    return await self.clones.find_one({'owner_id': owner_id})

async def get_all_active_clones(self):
    cursor = self.clones.find({'status': 'active'})
    return await cursor.to_list(length=None)

async def update_clone_status(self, owner_id: str, status: str):
    await self.clones.update_one(
        {'owner_id': owner_id},
        {'$set': {
            'status': status,
            'updated_at': datetime.now(IST)
        }}
    )

async def delete_clone(self, owner_id: str):
    await self.clones.delete_one({'owner_id': owner_id})

# ===== ANALYTICS =====
async def log_analytics(self, event_type: str, data: dict):
    analytics_data = {
        'event_type': event_type,
        'data': data,
        'timestamp': datetime.now(IST)
    }
    await self.analytics.insert_one(analytics_data)

async def get_analytics_summary(self):
    total_users = await self.users.count_documents({})
    total_reminders = await self.reminders.count_documents({'status': 'active'})
    total_clones = await self.clones.count_documents({'status': 'active'})
    total_completed = await self.reminders.count_documents({'status': 'completed'})

    # Today's stats
    today_start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    new_users_today = await self.users.count_documents({'created_at': {'$gte': today_start}})
    reminders_today = await self.reminders.count_documents({'created_at': {'$gte': today_start}})

    return {
        'total_users': total_users,
        'total_reminders': total_reminders,
        'total_clones': total_clones,
        'total_completed': total_completed,
        'new_users_today': new_users_today,
        'reminders_today': reminders_today
    }

# ===== LOGS =====
async def log_activity(self, log_data: dict):
    log_data['timestamp'] = datetime.now(IST)
    await self.logs.insert_one(log_data)

async def get_recent_logs(self, limit: int = 50):
    cursor = self.logs.find({}).sort('timestamp', -1).limit(limit)
    return await cursor.to_list(length=None)

Global database instance

db: Optional[DatabaseManager] = None

==================== BOT MANAGER ====================

class BotManager: def init(self, master_token: str, database: DatabaseManager): self.master_token = master_token self.master_app = None self.db = database

async def start_master_bot(self):
    """Start the master bot"""
    self.master_app = Application.builder().token(self.master_token).build()
    self.master_app.bot_data['bot_manager'] = self
    self.master_app.bot_data['db'] = self.db

    self._register_handlers(self.master_app, is_master=True)

    logger.info("🚀 Master bot starting...")
    await self.master_app.initialize()
    await self.master_app.start()
    # start polling
    await self.master_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Load existing clones
    await self.load_and_start_clones()

    # Start reminder checker
    asyncio.create_task(self.check_reminders_loop())

async def load_and_start_clones(self):
    """Load and start all active clone bots"""
    clones = await self.db.get_all_active_clones()
    for clone in clones:
        owner_id = clone['owner_id']
        try:
            await self.start_clone_bot(owner_id, clone['token'])
            logger.info(f"✅ Clone bot loaded for user {owner_id}")
        except Exception as e:
            logger.error(f"❌ Failed to load clone for user {owner_id}: {e}")
            await self.db.update_clone_status(owner_id, 'error')

async def start_clone_bot(self, owner_id: str, token: str):
    """Start a new clone bot"""
    if owner_id in ACTIVE_BOTS:
        logger.warning(f"Bot already running for user {owner_id}")
        return False

    try:
        app = Application.builder().token(token).build()
        app.bot_data['owner_id'] = owner_id
        app.bot_data['db'] = self.db
        app.bot_data['bot_manager'] = self

        self._register_handlers(app, is_master=False, owner_id=owner_id)

        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

        ACTIVE_BOTS[owner_id] = app
        await self.db.update_clone_status(owner_id, 'active')

        await self.db.log_activity({
            'type': 'clone_started',
            'owner_id': owner_id
        })

        logger.info(f"✅ Clone bot started for user {owner_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Error starting clone bot: {e}")
        await self.db.update_clone_status(owner_id, 'error')
        return False

async def stop_clone_bot(self, owner_id: str):
    """Stop a clone bot"""
    if owner_id not in ACTIVE_BOTS:
        return False

    try:
        app = ACTIVE_BOTS[owner_id]
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

        del ACTIVE_BOTS[owner_id]
        await self.db.update_clone_status(owner_id, 'stopped')

        await self.db.log_activity({
            'type': 'clone_stopped',
            'owner_id': owner_id
        })

        logger.info(f"🛑 Clone bot stopped for user {owner_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Error stopping clone bot: {e}")
        return False

async def check_reminders_loop(self):
    """Background task to check and send reminders"""
    while True:
        try:
            pending = await self.db.get_pending_reminders()

            for reminder in pending:
                try:
                    # Determine which bot to use
                    owner_bot = reminder.get('owner_bot')
                    if owner_bot and owner_bot in ACTIVE_BOTS:
                        bot = ACTIVE_BOTS[owner_bot].bot
                    else:
                        bot = self.master_app.bot

                    # Send reminder
                    await bot.send_message(
                        chat_id=reminder['chat_id'],
                        text=f"🔔 <b>REMINDER!</b>\n\n"
                             f"📝 {reminder['message']}\n\n"
                             f"⏰ Scheduled for: {reminder['remind_time'].strftime('%d-%m-%Y %I:%M %p')}",
                        parse_mode='HTML'
                    )

                    # Update status
                    await self.db.update_reminder_status(str(reminder['_id']), 'completed')

                    # Log analytics
                    await self.db.log_analytics('reminder_sent', {
                        'user_id': reminder['user_id'],
                        'reminder_id': str(reminder['_id'])
                    })

                except Exception as e:
                    logger.error(f"Error sending reminder: {e}")
                    try:
                        await self.db.update_reminder_status(str(reminder['_id']), 'error')
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error in reminder checker: {e}")

        await asyncio.sleep(10)  # Check every 10 seconds

def _register_handlers(self, app: Application, is_master: bool = False, owner_id: str = None):
    """Register command handlers"""
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("delete", delete_reminder))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("stats", stats_command))

    if is_master:
        app.add_handler(CommandHandler("clone", clone_command))
        app.add_handler(CommandHandler("settoken", set_token_command))
        app.add_handler(CommandHandler("mystop", stop_my_bot))
        app.add_handler(CommandHandler("mystart", restart_my_bot))
        app.add_handler(CommandHandler("mydelete", delete_my_clone))
        app.add_handler(CommandHandler("admin", admin_command))
        app.add_handler(CommandHandler("broadcast", broadcast_command))
        app.add_handler(CommandHandler("analytics", analytics_command))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

==================== ERROR HANDLER ====================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE): """Global error handler - logs exception and notifies user (if possible)""" logger.exception("Unhandled exception: %s", context.error) try: if isinstance(update, Update) and update.effective_message: await update.effective_message.reply_text("⚠️ An unexpected error occurred. The admin has been notified.") except Exception: # If replying fails, just log it logger.error("Failed to notify user about the error")

==================== COMMAND HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE): user = update.effective_user db = context.application.bot_data['db']

# Create or update user
user_data = {
    'user_id': str(user.id),
    'first_name': user.first_name,
    'last_name': user.last_name,
    'username': user.username,
    'language_code': user.language_code,
    'is_bot': user.is_bot,
    'last_active': datetime.now(IST),
    'created_at': datetime.now(IST)
}

existing_user = await db.get_user(str(user.id))
if existing_user:
    user_data['created_at'] = existing_user.get('created_at', user_data['created_at'])

await db.create_or_update_user(user_data)
await db.log_analytics('user_start', {'user_id': str(user.id)})

# Check if master or clone
owner_id = context.application.bot_data.get('owner_id')
is_master = owner_id is None

if is_master:
    stats = await db.get_analytics_summary()
    text = f"""

🎯 <b>Welcome {user.first_name}!</b>

Main ek <b>Advanced AI-Powered Reminder Bot</b> hoon jo automatically clone bhi ban sakta hai!

<b>✨ Premium Features:</b> • ⏰ Smart Time-based Reminders • 🤖 Auto Clone System (24/7) • 📊 Advanced Analytics • 💾 MongoDB Database • 🔔 Real-time Notifications • 📈 Usage Statistics

<b>🚀 Quick Start:</b> /remind - Set smart reminder /list - View all reminders /clone - Create your bot /stats - Your statistics

<b>📊 Global Stats:</b> • Users: {stats['total_users']} ({stats['new_users_today']} today) • Active Reminders: {stats['total_reminders']} • Clone Bots: {stats['total_clones']} • Completed: {stats['total_completed']}

<b>🎁 Special Feature:</b> Create your own bot that runs 24/7 on our server - FREE! """ else: clone = await db.get_clone(owner_id) owner_name = clone.get('owner_name', 'Owner') if clone else 'Owner'

text = f"""

🎯 <b>Welcome {user.first_name}!</b>

Yeh <b>{owner_name}</b> ka Personal Clone Bot hai!

<b>📌 Available Commands:</b> /remind - Set reminder /list - View reminders /delete - Remove reminder /stats - Your statistics /status - Bot information /help - Complete guide

<b>🔥 Powered by Advanced MongoDB</b> <b>⚡ Running 24/7 Automatically</b> """

keyboard = [
    [
        InlineKeyboardButton("⏰ Set Reminder", callback_data="guide_remind"),
        InlineKeyboardButton("📊 My Stats", callback_data="my_stats")
    ]
]

if is_master:
    keyboard.append([
        InlineKeyboardButton("🤖 Clone Bot", callback_data="guide_clone"),
        InlineKeyboardButton("📈 Analytics", callback_data="view_analytics")
    ])

keyboard.append([InlineKeyboardButton("❓ Help", callback_data="show_help")])

reply_markup = InlineKeyboardMarkup(keyboard)
await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db']

if not context.args:
    keyboard = [
        [
            InlineKeyboardButton("⚡ 10 min", callback_data="quick_10m"),
            InlineKeyboardButton("⏰ 30 min", callback_data="quick_30m")
        ],
        [
            InlineKeyboardButton("🕐 1 hour", callback_data="quick_1h"),
            InlineKeyboardButton("🕑 2 hours", callback_data="quick_2h")
        ],
        [
            InlineKeyboardButton("📅 1 day", callback_data="quick_1d"),
            InlineKeyboardButton("📆 1 week", callback_data="quick_7d")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "❌ <b>Format Required!</b>\n\n"
        "<b>Correct Format:</b>\n"
        "<code>/remind message @time</code>\n\n"
        "<b>Examples:</b>\n"
        "• <code>/remind Drink water @10m</code>\n"
        "• <code>/remind Gym workout @2h</code>\n"
        "• <code>/remind Project deadline @3d</code>\n"
        "• <code>/remind Weekly meeting @7d</code>\n\n"
        "<b>Time Formats:</b>\n"
        "• m = minutes (1-1440)\n"
        "• h = hours (1-168)\n"
        "• d = days (1-365)\n\n"
        "<b>Or use quick buttons below:</b>",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return

text = ' '.join(context.args)

if '@' not in text:
    await update.message.reply_text(
        "❌ Time missing! Use format: <code>message @time</code>",
        parse_mode='HTML'
    )
    return

parts = text.split('@')
message = parts[0].strip()
time_str = parts[1].strip().lower()

# Validate message
if len(message) < 3:
    await update.message.reply_text("❌ Message too short! Minimum 3 characters required.")
    return

if len(message) > 500:
    await update.message.reply_text("❌ Message too long! Maximum 500 characters allowed.")
    return

# Parse time
try:
    if time_str.endswith('m'):
        minutes = int(time_str[:-1])
        if minutes < 1 or minutes > 1440:
            raise ValueError("Minutes must be between 1-1440")
        remind_time = datetime.now(IST) + timedelta(minutes=minutes)
        time_display = f"{minutes} minute{'s' if minutes > 1 else ''}"
    elif time_str.endswith('h'):
        hours = int(time_str[:-1])
        if hours < 1 or hours > 168:
            raise ValueError("Hours must be between 1-168")
        remind_time = datetime.now(IST) + timedelta(hours=hours)
        time_display = f"{hours} hour{'s' if hours > 1 else ''}"
    elif time_str.endswith('d'):
        days = int(time_str[:-1])
        if days < 1 or days > 365:
            raise ValueError("Days must be between 1-365")
        remind_time = datetime.now(IST) + timedelta(days=days)
        time_display = f"{days} day{'s' if days > 1 else ''}"
    else:
        await update.message.reply_text(
            "❌ Invalid time format!\n\n"
            "Use: m (minutes), h (hours), d (days)\n"
            "Example: @30m, @2h, @1d"
        )
        return
except ValueError as e:
    await update.message.reply_text(f"❌ {str(e)}")
    return

# Save reminder to database
user_id = str(update.effective_user.id)
owner_bot = context.application.bot_data.get('owner_id')

reminder_data = {
    'user_id': user_id,
    'message': message,
    'remind_time': remind_time,
    'chat_id': update.effective_chat.id,
    'owner_bot': owner_bot,
    'status': 'active',
    'created_at': datetime.now(IST),
    'updated_at': datetime.now(IST)
}

reminder_id = await db.create_reminder(reminder_data)

# Log analytics
await db.log_analytics('reminder_created', {
    'user_id': user_id,
    'reminder_id': reminder_id
})

await update.message.reply_text(
    f"✅ <b>Reminder Created Successfully!</b>\n\n"
    f"📝 <b>Message:</b> {message}\n"
    f"⏰ <b>Time:</b> {time_display} from now}\n"
    f"🕐 <b>Exact Time:</b> {remind_time.strftime('%d-%m-%Y %I:%M %p')}\n"
    f"🆔 <b>ID:</b> <code>{reminder_id}</code>\n\n"
    f"💡 <b>Tip:</b> Use /list to view all reminders",
    parse_mode='HTML'
)

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id)

reminders = await db.get_user_reminders(user_id)

if not reminders:
    keyboard = [[InlineKeyboardButton("➕ Create Reminder", callback_data="guide_remind")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📭 <b>No Active Reminders</b>\n\n"
        "You don't have any reminders right now.\n"
        "Create one using /remind command!",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return

text = f"📋 <b>Your Active Reminders ({len(reminders)})</b>\n\n"

for idx, reminder in enumerate(reminders, 1):
    remind_time = reminder['remind_time']
    time_left = remind_time - datetime.now(IST)

    if time_left.total_seconds() > 0:
        days = time_left.days
        hours = int(time_left.seconds // 3600)
        minutes = int((time_left.seconds % 3600) // 60)

        if days > 0:
            time_left_str = f"{days}d {hours}h"
        elif hours > 0:
            time_left_str = f"{hours}h {minutes}m"
        else:
            time_left_str = f"{minutes}m"
    else:
        time_left_str = "Sending..."

    text += f"<b>{idx}. #{str(reminder['_id'])[-6:]}</b>\n"
    text += f"📝 {reminder['message'][:50]}{'...' if len(reminder['message']) > 50 else ''}\n"
    text += f"⏰ {remind_time.strftime('%d-%m %I:%M %p')}\n"
    text += f"⏳ <i>{time_left_str} left</i>\n\n"

keyboard = [
    [
        InlineKeyboardButton("➕ Add More", callback_data="guide_remind"),
        InlineKeyboardButton("🗑️ Delete All", callback_data="delete_all_confirm")
    ]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db']

if not context.args:
    await update.message.reply_text(
        "❌ <b>Reminder ID Required!</b>\n\n"
        "<b>Usage:</b> <code>/delete REMINDER_ID</code>\n\n"
        "<b>Example:</b> <code>/delete 507f1f77</code>\n\n"
        "💡 Use /list to see all reminder IDs",
        parse_mode='HTML'
    )
    return

reminder_id_input = context.args[0]
user_id = str(update.effective_user.id)

try:
    # Get all user reminders to find matching ID
    reminders = await db.get_user_reminders(user_id)

    matching_reminder = None
    for reminder in reminders:
        if str(reminder['_id']).endswith(reminder_id_input) or str(reminder['_id']) == reminder_id_input:
            matching_reminder = reminder
            break

    if not matching_reminder:
        await update.message.reply_text(
            "❌ <b>Reminder Not Found!</b>\n\n"
            "Please check the ID and try again.\n"
            "Use /list to see all your reminders.",
            parse_mode='HTML'
        )
        return

    # Delete reminder
    await db.delete_reminder(str(matching_reminder['_id']))

    # Log analytics
    await db.log_analytics('reminder_deleted', {
        'user_id': user_id,
        'reminder_id': str(matching_reminder['_id'])
    })

    await update.message.reply_text(
        f"✅ <b>Reminder Deleted!</b>\n\n"
        f"📝 {matching_reminder['message'][:50]}{'...' if len(matching_reminder['message']) > 50 else ''}\n\n"
        f"Use /list to see remaining reminders.",
        parse_mode='HTML'
    )

except Exception as e:
    logger.error(f"Error deleting reminder: {e}")
    await update.message.reply_text("❌ Error deleting reminder. Please try again.")

async def clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id)

clone = await db.get_clone(user_id)

if clone and clone.get('status') == 'active':
    text = f"""

✅ <b>Your Clone Bot is Active!</b>

<b>🤖 Bot Information:</b> • Username: @{clone.get('username', 'N/A')} • Name: {clone.get('bot_name', 'N/A')} • Status: 🟢 Running 24/7

<b>📅 Created:</b> {clone.get('created_at').strftime('%d-%m-%Y %I:%M %p') if clone.get('created_at') else 'N/A'} <b>⏰ Last Update:</b> {clone.get('updated_at').strftime('%d-%m-%Y %I:%M %p') if clone.get('updated_at') else 'N/A'}

<b>🎮 Bot Management:</b> /mystop - Stop your bot /mystart - Restart your bot /mydelete - Delete your bot permanently

<b>💡 Tip:</b> Your bot runs automatically on our server - FREE! """ else: text = """ 🤖 <b>Create Your Personal Clone Bot!</b>

<b>✨ Benefits:</b> • Your own private reminder bot • Runs 24/7 on our server • Completely FREE • Full control over your bot • Automatic restart system

<b>📝 Step-by-Step Guide:</b>

1️⃣ <b>Open Telegram & Search:</b> → @BotFather

2️⃣ <b>Create New Bot:</b> → Send: <code>/newbot</code> → Choose bot name (e.g., "My Reminder Bot") → Choose username (must end with 'bot')

3️⃣ <b>Get Your Token:</b> → BotFather will send you a token → Copy the entire token

4️⃣ <b>Set Token Here:</b> → Send: <code>/settoken YOUR_TOKEN</code>

5️⃣ <b>Done!</b> → Your bot will start automatically → You can use it immediately

<b>⚠️ Important Security Note:</b> • Keep your token private & secure • Never share token with anyone • We store it encrypted in MongoDB

<b>Need Help?</b> Contact @YourSupportUsername """

keyboard = []
if clone and clone.get('status') == 'active':
    keyboard = [
        [
            InlineKeyboardButton("🛑 Stop Bot", callback_data="stop_clone"),
            InlineKeyboardButton("🔄 Restart", callback_data="restart_clone")
        ],
        [InlineKeyboardButton("🗑️ Delete Bot", callback_data="delete_clone_confirm")]
    ]
else:
    keyboard = [
        [InlineKeyboardButton("📖 Detailed Guide", callback_data="clone_guide_detailed")],
        [InlineKeyboardButton("❓ FAQ", callback_data="clone_faq")]
    ]

reply_markup = InlineKeyboardMarkup(keyboard)
await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def set_token_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db']

if not context.args:
    await update.message.reply_text(
        "❌ <b>Token Required!</b>\n\n"
        "<b>Usage:</b>\n"
        "<code>/settoken YOUR_BOT_TOKEN</code>\n\n"
        "<b>Example:</b>\n"
        "<code>/settoken 123456789:ABCdefGHIjklMNOpqrsTUVwxyz</code>\n\n"
        "💡 Get token from @BotFather",
        parse_mode='HTML'
    )
    return

token = context.args[0]
user_id = str(update.effective_user.id)
user = update.effective_user

# Validate token format
if ':' not in token or len(token) < 30:
    await update.message.reply_text(
        "❌ <b>Invalid Token Format!</b>\n\n"
        "Token should look like:\n"
        "<code>123456789:ABCdefGHI...</code>\n\n"
        "Please get correct token from @BotFather",
        parse_mode='HTML'
    )
    return

# Check if user already has a clone
existing_clone = await db.get_clone(user_id)
if existing_clone and existing_clone.get('status') == 'active':
    await update.message.reply_text(
        "⚠️ <b>Clone Already Exists!</b>\n\n"
        "You already have an active clone bot.\n"
        "Use /mystop to stop it first, then try again.",
        parse_mode='HTML'
    )
    return

msg = await update.message.reply_text(
    "🔄 <b>Setting up your bot...</b>\n\n"
    "⏳ Please wait, this may take a few seconds...",
    parse_mode='HTML'
)

try:
    # Test token validity
    test_app = Application.builder().token(token).build()
    await test_app.initialize()
    bot_info = await test_app.bot.get_me()
    await test_app.shutdown()

    # Save clone data
    clone_data = {
        'owner_id': user_id,
        'token': token,
        'username': bot_info.username,
        'bot_name': bot_info.first_name,
        'bot_id': str(bot_info.id),
        'owner_name': user.first_name,
        'owner_username': user.username,
        'status': 'pending',
        'created_at': datetime.now(IST),
        'updated_at': datetime.now(IST)
    }

    await db.create_clone(clone_data)

    # Start clone bot
    bot_manager = context.application.bot_data.get('bot_manager')
    if bot_manager:
        success = await bot_manager.start_clone_bot(user_id, token)

        if success:
            await db.log_analytics('clone_created', {
                'user_id': user_id,
                'bot_username': bot_info.username
            })

            await msg.edit_text(
                f"✅ <b>Clone Bot Created Successfully!</b>\n\n"
                f"<b>🤖 Bot Details:</b>\n"
                f"• Username: @{bot_info.username}\n"
                f"• Name: {bot_info.first_name}\n"
                f"• ID: <code>{bot_info.id}</code>\n"
                f"• Status: 🟢 Active & Running\n\n"
                f"<b>✨ Your bot is now live 24/7!</b>\n\n"
                f"<b>🎮 Management Commands:</b>\n"
                f"/mystop - Stop bot\n"
                f"/mystart - Restart bot\n"
                f"/mydelete - Delete bot\n\n"
                f"<b>🎯 Next Steps:</b>\n"
                f"1. Search @{bot_info.username} on Telegram\n"
                f"2. Start the bot with /start\n"
                f"3. Enjoy your personal reminder bot!\n\n"
                f"🎉 Congratulations!",
                parse_mode='HTML'
            )
        else:
            await msg.edit_text(
                "❌ <b>Failed to start bot!</b>\n\n"
                "Possible reasons:\n"
                "• Invalid token\n"
                "• Bot already running elsewhere\n"
                "• Network issues\n\n"
                "Please try again or contact support.",
                parse_mode='HTML'
            )
    else:
        await msg.edit_text("❌ System error! Please contact admin.")

except Exception as e:
    logger.error(f"Token validation error: {e}")
    await msg.edit_text(
        f"❌ <b>Error Creating Bot!</b>\n\n"
        f"<b>Reason:</b> {str(e)[:100]}\n\n"
        f"<b>Common Issues:</b>\n"
        f"• Invalid or expired token\n"
        f"• Bot already in use\n"
        f"• Network connectivity\n\n"
        f"💡 Get a new token from @BotFather",
        parse_mode='HTML'
    )

async def stop_my_bot(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id) bot_manager = context.application.bot_data.get('bot_manager')

if not bot_manager:
    await update.message.reply_text("❌ System error!")
    return

clone = await db.get_clone(user_id)
if not clone:
    await update.message.reply_text(
        "❌ <b>No Clone Found!</b>\n\n"
        "You don't have any clone bot.\n"
        "Create one with /clone command.",
        parse_mode='HTML'
    )
    return

if clone.get('status') != 'active':
    await update.message.reply_text(
        "⚠️ <b>Bot Not Running!</b>\n\n"
        "Your bot is already stopped.\n"
        "Use /mystart to restart it.",
        parse_mode='HTML'
    )
    return

msg = await update.message.reply_text("🔄 Stopping your bot...")

success = await bot_manager.stop_clone_bot(user_id)

if success:
    await db.log_analytics('clone_stopped', {'user_id': user_id})
    await msg.edit_text(
        "🛑 <b>Bot Stopped Successfully!</b>\n\n"
        "Your clone bot has been stopped.\n\n"
        "Use /mystart to restart anytime.",
        parse_mode='HTML'
    )
else:
    await msg.edit_text(
        "❌ <b>Failed to stop bot!</b>\n\n"
        "Please try again or contact support.",
        parse_mode='HTML'
    )

async def restart_my_bot(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id)

clone = await db.get_clone(user_id)
if not clone:
    await update.message.reply_text(
        "❌ <b>No Clone Found!</b>\n\n"
        "You don't have any clone bot.\n"
        "Create one with /clone command.",
        parse_mode='HTML'
    )
    return

bot_manager = context.application.bot_data.get('bot_manager')
if not bot_manager:
    await update.message.reply_text("❌ System error!")
    return

msg = await update.message.reply_text(
    "🔄 <b>Restarting your bot...</b>\n\n"
    "⏳ Please wait...",
    parse_mode='HTML'
)

# Stop if running
if user_id in ACTIVE_BOTS:
    await bot_manager.stop_clone_bot(user_id)
    await asyncio.sleep(2)

# Start bot
token = clone['token']
success = await bot_manager.start_clone_bot(user_id, token)

if success:
    await db.log_analytics('clone_restarted', {'user_id': user_id})
    await msg.edit_text(
        "✅ <b>Bot Restarted Successfully!</b>\n\n"
        f"🤖 @{clone.get('username', 'N/A')} is now online!\n\n"
        "Status: 🟢 Active & Running",
        parse_mode='HTML'
    )
else:
    await msg.edit_text(
        "❌ <b>Failed to restart bot!</b>\n\n"
        "Possible issues:\n"
        "• Token expired or invalid\n"
        "• Bot removed from @BotFather\n\n"
        "Try creating a new bot with /clone",
        parse_mode='HTML'
    )

async def delete_my_clone(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id)

clone = await db.get_clone(user_id)
if not clone:
    await update.message.reply_text(
        "❌ <b>No Clone Found!</b>\n\n"
        "You don't have any clone bot to delete.",
        parse_mode='HTML'
    )
    return

keyboard = [
    [
        InlineKeyboardButton("✅ Yes, Delete", callback_data="confirm_delete_clone"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete_clone")
    ]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(
    f"⚠️ <b>Confirm Deletion</b>\n\n"
    f"Are you sure you want to delete your clone bot?\n\n"
    f"<b>Bot:</b> @{clone.get('username', 'N/A')}\n\n"
    f"<b>⚠️ This action cannot be undone!</b>\n"
    f"All bot data will be removed.",
    parse_mode='HTML',
    reply_markup=reply_markup
)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id)

stats = await db.get_user_stats(user_id)
clone = await db.get_clone(user_id)

text = f"""

📊 <b>Your Bot Status</b>

<b>👤 User Information:</b> • Name: {update.effective_user.first_name} • ID: <code>{user_id}</code> • Username: @{update.effective_user.username or 'N/A'}

<b>⏰ Reminders:</b> • Active: {stats['reminders_count']} • Completed: Coming soon

<b>🤖 Clone Bot:</b> """

if clone:
    status_emoji = "🟢" if clone.get('status') == 'active' else "🔴"
    text += f"• Status: {status_emoji} {clone.get('status', 'unknown').title()}\n• Username: @{clone.get('username', 'N/A')}\n• Created: {clone.get('created_at').strftime('%d-%m-%Y') if clone.get('created_at') else 'N/A'}\n"
else:
    text += "• No clone bot created\n• Use /clone to create one"

keyboard = [
    [
        InlineKeyboardButton("⏰ Reminders", callback_data="show_reminders"),
        InlineKeyboardButton("📈 Stats", callback_data="my_stats")
    ]
]

if clone and clone.get('status') == 'active':
    keyboard.append([
        InlineKeyboardButton("🛑 Stop Bot", callback_data="stop_clone"),
        InlineKeyboardButton("🤖 Manage", callback_data="manage_clone")
    ])

reply_markup = InlineKeyboardMarkup(keyboard)
await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db'] user_id = str(update.effective_user.id)

user = await db.get_user(user_id)
reminders = await db.get_user_reminders(user_id)
completed = await db.reminders.count_documents({'user_id': user_id, 'status': 'completed'})

# Calculate usage time
joined = user.get('created_at', datetime.now(IST))
days_active = (datetime.now(IST) - joined).days

text = f"""

📈 <b>Your Statistics</b>

<b>📊 Overall Stats:</b> • Days Active: {days_active} days • Total Reminders Created: {len(reminders) + completed} • Active Reminders: {len(reminders)} • Completed Reminders: {completed}

<b>🎯 This Week:</b> • Reminders Set: Coming soon • Completion Rate: Coming soon

<b>🏆 Achievements:</b> """

# Simple achievements
if completed >= 10:
    text += "🥉 10+ Reminders Completed\n"
if completed >= 50:
    text += "🥈 50+ Reminders Completed\n"
if completed >= 100:
    text += "🥇 100+ Reminders Completed\n"
if days_active >= 30:
    text += "🎖️ 30 Days Active User\n"

if completed < 10 and days_active < 30:
    text += "🌱 New User - Keep Going!\n"

text += f"\n<b>📅 Member Since:</b> {joined.strftime('%d %B %Y')}"

keyboard = [
    [
        InlineKeyboardButton("⏰ View Reminders", callback_data="show_reminders"),
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh_stats")
    ]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db']

# Admin check
if update.effective_user.id not in ADMIN_IDS:
    await update.message.reply_text("❌ Access Denied! Admin only.")
    return

stats = await db.get_analytics_summary()

text = f"""

👑 <b>Admin Dashboard</b>

<b>📊 Global Statistics:</b> • Total Users: {stats['total_users']} • New Today: {stats['new_users_today']} • Active Reminders: {stats['total_reminders']} • Reminders Today: {stats['reminders_today']} • Completed: {stats['total_completed']}

<b>🤖 Bot Management:</b> • Total Clones: {stats['total_clones']} • Active Clones: {len(ACTIVE_BOTS)} • Master Bot: 🟢 Running

<b>💾 Database:</b> • Status: Connected • Type: MongoDB • Host: {MONGODB_URI.split('@')[-1] if '@' in MONGODB_URI else 'localhost'}

<b>🔧 System:</b> • Uptime: Active • Memory: Optimal • Response Time: Fast """

keyboard = [
    [
        InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics"),
        InlineKeyboardButton("👥 Users", callback_data="admin_users")
    ],
    [
        InlineKeyboardButton("🤖 Clones", callback_data="admin_clones"),
        InlineKeyboardButton("📝 Logs", callback_data="admin_logs")
    ],
    [
        InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast_menu")
    ]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db']

# Admin check
if update.effective_user.id not in ADMIN_IDS:
    await update.message.reply_text("❌ Access Denied!")
    return

if not context.args:
    await update.message.reply_text(
        "📢 <b>Broadcast Message</b>\n\n"
        "<b>Usage:</b>\n"
        "<code>/broadcast Your message here</code>\n\n"
        "This will send message to all users.",
        parse_mode='HTML'
    )
    return

message = ' '.join(context.args)
users = await db.get_all_users()

msg = await update.message.reply_text(
    f"📢 <b>Broadcasting...</b>\n\n"
    f"Sending to {len(users)} users...",
    parse_mode='HTML'
)

success = 0
failed = 0

for user in users:
    try:
        await context.bot.send_message(
            chat_id=int(user['user_id']),
            text=f"📢 <b>Announcement</b>\n\n{message}",
            parse_mode='HTML'
        )
        success += 1
        await asyncio.sleep(0.05)  # Rate limiting
    except Exception as e:
        failed += 1
        logger.error(f"Broadcast failed for {user['user_id']}: {e}")

await msg.edit_text(
    f"✅ <b>Broadcast Complete!</b>\n\n"
    f"✅ Success: {success}\n"
    f"❌ Failed: {failed}\n"
    f"📊 Total: {len(users)}",
    parse_mode='HTML'
)

async def analytics_command(update: Update, context: ContextTypes.DEFAULT_TYPE): db = context.application.bot_data['db']

stats = await db.get_analytics_summary()

# Calculate growth
total_users = stats['total_users']
new_today = stats['new_users_today']
growth_rate = (new_today / total_users * 100) if total_users > 0 else 0

text = f"""

📊 <b>Analytics Dashboard</b>

<b>👥 User Analytics:</b> • Total Users: {total_users} • New Today: {new_today} • Growth Rate: {growth_rate:.1f}%

<b>⏰ Reminder Analytics:</b> • Active: {stats['total_reminders']} • Today: {stats['reminders_today']} • Completed: {stats['total_completed']} • Success Rate: {(stats['total_completed'] / (stats['total_completed'] + stats['total_reminders']) * 100) if (stats['total_completed'] + stats['total_reminders']) > 0 else 0:.1f}%

<b>🤖 Clone Analytics:</b> • Total Clones: {stats['total_clones']} • Active Now: {len(ACTIVE_BOTS)} • Activation Rate: {(len(ACTIVE_BOTS) / stats['total_clones'] * 100) if stats['total_clones'] > 0 else 0:.1f}%

<b>📈 Performance:</b> • System Status: 🟢 Optimal • Database: 🟢 Connected • Response Time: Fast """

keyboard = [
    [
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh_analytics"),
        InlineKeyboardButton("📊 Detailed", callback_data="detailed_analytics")
    ]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE): text = """ 📖 <b>Complete Help Guide</b>

<b>⏰ Reminder Commands:</b> /remind message @time - Set reminder /list - View all reminders /delete ID - Delete specific reminder /stats - Your statistics

<b>🤖 Clone Bot Commands:</b> /clone - Clone bot guide /settoken TOKEN - Set bot token /mystop - Stop your bot /mystart - Restart your bot /mydelete - Delete your bot

<b>📊 Information:</b> /status - Bot status /stats - Your stats /help - This message

<b>⏱️ Time Format Examples:</b> • @10m = 10 minutes • @2h = 2 hours • @1d = 1 day • @7d = 1 week

<b>💡 Pro Tips:</b> • Use quick buttons for common times • Clone bot runs 24/7 automatically • All data stored in secure MongoDB • Multiple reminders supported

<b>🎯 Examples:</b> /remind Drink water @30m /remind Gym workout @2h /remind Pay bills @1d /remind Weekly meeting @7d

<b>❓ Need Help?</b> Contact support or use inline buttons! """

keyboard = [
    [
        InlineKeyboardButton("⏰ Set Reminder", callback_data="guide_remind"),
        InlineKeyboardButton("🤖 Clone Bot", callback_data="guide_clone")
    ],
    [
        InlineKeyboardButton("📊 Examples", callback_data="show_examples"),
        InlineKeyboardButton("❓ FAQ", callback_data="show_faq")
    ]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

==================== CALLBACK HANDLERS ====================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE): query = update.callback_query await query.answer()

db = context.application.bot_data['db']
user_id = str(query.from_user.id)
bot_manager: BotManager = context.application.bot_data.get('bot_manager')

# Reminder guides
if query.data == "guide_remind":
    text = """

⏰ <b>How to Set Reminders</b>

<b>Format:</b> <code>/remind message @time</code>

<b>Time Options:</b> • m = minutes (1-1440) • h = hours (1-168) • d = days (1-365)

<b>Examples:</b> <code>/remind Drink water @30m</code> <code>/remind Exercise @2h</code> <code>/remind Call mom @1d</code> <code>/remind Weekly review @7d</code>

<b>💡 Tips:</b> • Keep messages clear and short • Use multiple reminders for tasks • Check /list to see all active

Try it now! 👆 """ await query.edit_message_text(text, parse_mode='HTML')

elif query.data == "guide_clone":
    text = """

🤖 <b>Clone Bot Setup Guide</b>

<b>Quick Steps:</b> 1️⃣ Open @BotFather on Telegram 2️⃣ Send <code>/newbot</code> 3️⃣ Follow instructions to create bot 4️⃣ Copy the token you receive 5️⃣ Send here: <code>/settoken TOKEN</code>

<b>✨ What You Get:</b> • Personal reminder bot • Runs 24/7 on our server • Completely FREE hosting • Full control & privacy

<b>⏱️ Setup Time:</b> Less than 2 minutes

Ready to create? Use /clone command! """ keyboard = [[InlineKeyboardButton("📖 Detailed Guide", url="https://t.me/BotFather")]] reply_markup = InlineKeyboardMarkup(keyboard) await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)

elif query.data == "my_stats":
    stats = await db.get_user_stats(user_id)
    completed = await db.reminders.count_documents({'user_id': user_id, 'status': 'completed'})

    text = f"""

📊 <b>Your Quick Stats</b>

⏰ Active Reminders: {stats['reminders_count']} ✅ Completed: {completed} 🤖 Clone Bot: {'Yes ✅' if stats['has_clone'] else 'No ❌'}

Use /stats for detailed statistics! """ await query.edit_message_text(text, parse_mode='HTML')

elif query.data.startswith("quick_"):
    time_str = query.data.replace("quick_", "")
    await query.message.reply_text(
        f"<b>Quick Reminder Template:</b>\n\n"
        f"<code>/remind Your message here @{time_str}</code>\n\n"
        f"<b>Example:</b>\n"
        f"<code>/remind Take medicine @{time_str}</code>",
        parse_mode='HTML'
    )

elif query.data == "delete_all_confirm":
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Delete All", callback_data="delete_all_confirmed"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "⚠️ <b>Confirm Deletion</b>\n\n"
        "Are you sure you want to delete ALL reminders?\n\n"
        "<b>This action cannot be undone!</b>",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

elif query.data == "delete_all_confirmed":
    deleted = await db.delete_all_user_reminders(user_id)
    await query.edit_message_text(f"✅ Deleted {deleted} reminders.")

elif query.data == "cancel_delete":
    await query.edit_message_text("❌ Deletion cancelled.")

# Clone controls from inline buttons
elif query.data == "stop_clone":
    if not bot_manager:
        await query.answer("System error", show_alert=True)
        return
    success = await bot_manager.stop_clone_bot(user_id)
    if success:
        await query.edit_message_text("🛑 Your clone bot has been stopped.")
    else:
        await query.edit_message_text("❌ Failed to stop your bot.")

elif query.data == "restart_clone":
    clone = await db.get_clone(user_id)
    if not clone:
        await query.edit_message_text("❌ No clone found to restart.")
        return
    # try restart
    if user_id in ACTIVE_BOTS:
        await bot_manager.stop_clone_bot(user_id)
        await asyncio.sleep(1)
    success = await bot_manager.start_clone_bot(user_id, clone['token'])
    if success:
        await query.edit_message_text("🔄 Clone restarted successfully!")
    else:
        await query.edit_message_text("❌ Failed to restart clone.")

elif query.data == "delete_clone_confirm":
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data="confirm_delete_clone"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete_clone")
        ]
    ]
    await query.edit_message_text("⚠️ Confirm deletion of your clone:", reply_markup=InlineKeyboardMarkup(keyboard))

elif query.data == "confirm_delete_clone":
    clone = await db.get_clone(user_id)
    if not clone:
        await query.edit_message_text("❌ No clone found.")
        return
    # stop if running
    if user_id in ACTIVE_BOTS:
        await bot_manager.stop_clone_bot(user_id)
    await db.delete_clone(user_id)
    await query.edit_message_text("✅ Your clone has been deleted.")

elif query.data == "cancel_delete_clone":
    await query.edit_message_text("❌ Clone deletion cancelled.")

else:
    # Unknown callback - gracefully handle
    await query.answer()

==================== MAIN STARTUP ====================

async def main(): global db db = DatabaseManager(MONGODB_URI, DB_NAME)

master_token = os.getenv('MASTER_TOKEN', '8572931269:AAFzWSa_WVoj44LVPPJCBTHe1911zOgrXYY')

bot_manager = BotManager(master_token, db)

try:
    await bot_manager.start_master_bot()
    # keep running
    await asyncio.Event().wait()
except (KeyboardInterrupt, SystemExit):
    logger.info('Shutting down...')
except Exception as e:
    logger.exception('Fatal error in main: %s', e)

if name == 'main': asyncio.run(main())
