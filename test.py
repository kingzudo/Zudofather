import logging
import os
import asyncio
import signal
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, ConversationHandler, filters

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Files and storage
DATA_FILE = "bots_data.json"
running_bots = {}  # {script_id: {"process": proc, "user_id": int, "name": str, "file": str}}
script_counter = 1

def load_data():
    global script_counter, running_bots
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                script_counter = data.get("next_id", 1)
                saved_bots = data.get("bots", {})
                
                # Restart all saved bots
                for sid_str, info in saved_bots.items():
                    sid = int(sid_str)
                    file_path = info["file"]
                    user_id = info["user_id"]
                    name = info["name"]
                    
                    if os.path.exists(file_path):
                        proc = asyncio.create_subprocess_exec(
                            "python", file_path,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        running_bots[sid] = {
                            "process": proc,
                            "user_id": user_id,
                            "name": name,
                            "file": file_path
                        }
                        # Actually start the process
                        asyncio.create_task(proc)  # Fire and forget (it runs in background)
                    else:
                        logger.warning(f"Script file missing for bot {sid}: {file_path}")
                
                logger.info(f"Loaded {len(running_bots)} bots from storage.")
        except Exception as e:
            logger.error(f"Error loading data: {e}")

def save_data():
    data = {
        "next_id": script_counter,
        "bots": {}
    }
    for sid, info in running_bots.items():
        data["bots"][str(sid)] = {
            "user_id": info["user_id"],
            "name": info["name"],
            "file": info["file"]
        }
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

WAITING_FOR_SCRIPT, CONFIRM_RUN = range(2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 <b>Multi Bot Runner - Help</b>\n\n"
        "/start - Deploy a new bot script\n"
        "/allscripts - List all running bots\n"
        "/stop <number> - Stop a bot (e.g. /stop 1)\n"
        "/restart <number> - Restart a bot (e.g. /restart 2)\n"
        "/help - Show this help\n\n"
        "<i>All your deployed bots are saved permanently and will auto-restart on bot restart.</i>"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please paste the full Python script for the bot you want to deploy.\n"
        "It must use python-telegram-bot v20+ and create an 'application' object.\n"
        "Polling will be added automatically.\n\n"
        "Send /cancel to abort."
    )
    return WAITING_FOR_SCRIPT

async def receive_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global script_counter
    user_id = update.message.from_user.id
    script = update.message.text

    context.user_data['pending_script'] = script
    context.user_data['script_name'] = f"Script {script_counter}"

    await update.message.reply_text(
        f"✅ Script received!\n\n"
        f"Will be deployed as: <b>{context.user_data['script_name']}</b>\n\n"
        f"Send /run to confirm deployment\n"
        f"Or /cancel to abort.",
        parse_mode='HTML'
    )
    return CONFIRM_RUN

async def run_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global script_counter
    user_id = update.message.from_user.id

    if 'pending_script' not in context.user_data:
        await update.message.reply_text("No pending script. Use /start first.")
        return ConversationHandler.END

    script = context.user_data['pending_script']
    script_name = context.user_data['script_name']
    script_file = f"bot_{script_counter}_{user_id}.py"

    full_script = script + "\n\n# Auto-run polling\nif __name__ == '__main__':\n    application.run_polling(drop_pending_updates=True)"

    try:
        with open(script_file, "w") as f:
            f.write(full_script)

        await update.message.reply_text(f"🚀 Deploying {script_name}...")

        proc = await asyncio.create_subprocess_exec(
            "python", script_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        script_id = script_counter
        running_bots[script_id] = {
            "process": proc,
            "user_id": user_id,
            "name": script_name,
            "file": script_file
        }

        script_counter += 1
        save_data()  # Save immediately

        await update.message.reply_text(
            f"✅ <b>{script_name}</b> is now <b>RUNNING permanently!</b>\n\n"
            f"It will auto-restart even if the hoster bot restarts.\n"
            f"Check with /allscripts",
            parse_mode='HTML'
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Deployment failed: {str(e)}")
        if os.path.exists(script_file):
            os.remove(script_file)

    finally:
        context.user_data.clear()

    return ConversationHandler.END

async def allscripts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if not running_bots:
        await update.message.reply_text("No bots are currently running.")
        return

    text = "<b>📋 Currently Running Bots:</b>\n\n"
    for sid in sorted(running_bots.keys()):
        info = running_bots[sid]
        owner = "You" if info["user_id"] == user_id else f"User {info['user_id']}"
        text += f"<b>{sid}.</b> {info['name']} — Owned by: {owner} — <i>Running</i>\n"

    text += "\nUse /stop <number> or /restart <number> to manage your bots."
    await update.message.reply_text(text, parse_mode='HTML')

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /stop <number>")
        return

    try:
        script_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid number.")
        return

    if script_id not in running_bots:
        await update.message.reply_text("Bot not found.")
        return

    info = running_bots[script_id]
    if info["user_id"] != update.message.from_user.id:
        await update.message.reply_text("You can only stop your own bots.")
        return

    info["process"].terminate()
    await info["process"].wait()

    if os.path.exists(info["file"]):
        os.remove(info["file"])

    del running_bots[script_id]
    save_data()

    await update.message.reply_text(f"🛑 {info['name']} stopped and removed.")

async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /restart <number>")
        return

    try:
        script_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid number.")
        return

    if script_id not in running_bots:
        await update.message.reply_text("Bot not found.")
        return

    info = running_bots[script_id]
    if info["user_id"] != update.message.from_user.id:
        await update.message.reply_text("You can only restart your own bots.")
        return

    info["process"].terminate()
    await info["process"].wait()

    new_proc = await asyncio.create_subprocess_exec(
        "python", info["file"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    running_bots[script_id]["process"] = new_proc

    await update.message.reply_text(f"🔄 {info['name']} restarted successfully.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

async def shutdown():
    logger.info("Shutting down... Stopping all child bots.")
    for info in running_bots.values():
        info["process"].terminate()
    save_data()

if __name__ == '__main__':
    # Load previous data on startup
    load_data()

    application = ApplicationBuilder().token("7636825715:AAGc-t4nUfO_9NTsWCWVmD96SDZjlsCybvM").build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_FOR_SCRIPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_script)],
            CONFIRM_RUN: [CommandHandler('run', run_script)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('allscripts', allscripts))
    application.add_handler(CommandHandler('stop', stop_bot))
    application.add_handler(CommandHandler('restart', restart_bot))

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, lambda: asyncio.create_task(shutdown()))

    print("🤖 Professional Multi-Bot Runner with PERSISTENT STORAGE is ONLINE!")
    print("All previous bots have been auto-restarted if files exist.")
    application.run_polling(drop_pending_updates=True)
