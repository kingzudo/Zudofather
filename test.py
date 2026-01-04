import logging
import os
import asyncio
import signal
import json
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, ConversationHandler, filters

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage
DATA_FILE = "bots_data.json"
running_bots = {}  # {sid: {"process": proc, "user_id": int, "name": str, "file": str}}
script_counter = 1

# Conversation states for gitclone
REPO_URL, CONFIRM_RUN = range(2)

def load_data():
    global script_counter, running_bots
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                script_counter = data.get("next_id", 1)
                for sid_str, info in data.get("bots", {}).items():
                    sid = int(sid_str)
                    file_path = info["file"]
                    if os.path.exists(file_path):
                        proc = asyncio.create_subprocess_exec("python", file_path,
                                                              stdout=asyncio.subprocess.PIPE,
                                                              stderr=asyncio.subprocess.PIPE)
                        running_bots[sid] = {
                            "process": proc,
                            "user_id": info["user_id"],
                            "name": info["name"],
                            "file": file_path
                        }
                        asyncio.create_task(proc)
        except Exception as e:
            logger.error(f"Load error: {e}")

def save_data():
    data = {"next_id": script_counter, "bots": {}}
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
        logger.error(f"Save error: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>Zudofather - High Level Multi Bot Hoster</b>\n\n"
        "/start - Deploy a new bot script (manual paste)\n"
        "/gitclone - Clone & deploy bots from GitHub repositories\n"
        "/allscripts - List all running bots\n"
        "/stop <number> - Stop a bot\n"
        "/restart <number> - Restart a bot\n"
        "/help - Show this menu\n\n"
        "<i>All bots run permanently with auto-restart on reboot.</i>"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Paste the full Python script for the bot you want to deploy.\n"
        "Use python-telegram-bot v20+ format.\n\n/cancel to abort."
    )
    return REPO_URL  # Reuse state for script paste

async def receive_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global script_counter
    user_id = update.message.from_user.id
    script = update.message.text
    context.user_data['pending_script'] = script
    context.user_data['script_name'] = f"Script {script_counter}"
    await update.message.reply_text(
        f"✅ Script received!\n\nDeploy as <b>{context.user_data['script_name']}</b>\n\nSend /run to start\n/cancel to abort",
        parse_mode='HTML'
    )
    return CONFIRM_RUN

async def gitclone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cloned_files'] = []
    await update.message.reply_text(
        "🔗 Send GitHub repository URLs one by one (public repos only).\n"
        "Send /skip to skip current\n"
        "Send /done when finished\n"
        "Then /run to deploy all cloned bots."
    )
    return REPO_URL

async def receive_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("https://github.com/"):
        await update.message.reply_text("Cloning repository... Please wait.")
        try:
            repo_name = text.split("/")[-1].replace(".git", "")
            result = subprocess.run(["git", "clone", text, f"repo_{repo_name}"], capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                context.user_data['cloned_files'].append(f"repo_{repo_name}")
                await update.message.reply_text(f"✅ Cloned: {repo_name}\nSend next URL or /done")
            else:
                await update.message.reply_text(f"❌ Clone failed: {result.stderr[:500]}")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")
    elif text == "/done":
        if context.user_data.get('cloned_files'):
            await update.message.reply_text(
                f"Cloned {len(context.user_data['cloned_files'])} repos.\n"
                "Send /run to deploy all bots from them."
            )
            return CONFIRM_RUN
        else:
            await update.message.reply_text("No repos cloned. /cancel")
            return ConversationHandler.END
    elif text == "/skip":
        await update.message.reply_text("Skipped. Send next URL or /done")
    else:
        await update.message.reply_text("Invalid URL. Send proper GitHub link.")
    return REPO_URL

async def run_deployment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global script_counter
    user_id = update.message.from_user.id
    deployed_count = 0

    # Manual script deployment
    if 'pending_script' in context.user_data:
        script = context.user_data['pending_script']
        name = context.user_data['script_name']
        file = f"bot_{script_counter}_{user_id}.py"
        with open(file, "w") as f:
            f.write(script + "\n\nif __name__ == '__main__':\n    application.run_polling()")
        proc = await asyncio.create_subprocess_exec("python", file,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        running_bots[script_counter] = {"process": proc, "user_id": user_id, "name": name, "file": file}
        script_counter += 1
        deployed_count += 1
        save_data()

    # Git cloned repos deployment
    if context.user_data.get('cloned_files'):
        for folder in context.user_data['cloned_files']:
            for py_file in os.listdir(folder):
                if py_file.endswith(".py") and py_file not in ["__init__.py"]:
                    file_path = os.path.join(folder, py_file)
                    proc = await asyncio.create_subprocess_exec("python", file_path,
                                                                stdout=asyncio.subprocess.PIPE,
                                                                stderr=asyncio.subprocess.PIPE)
                    sid = script_counter
                    running_bots[sid] = {
                        "process": proc,
                        "user_id": user_id,
                        "name": f"{folder}/{py_file}",
                        "file": file_path
                    }
                    script_counter += 1
                    deployed_count += 1
        save_data()

    await update.message.reply_text(f"✅ Deployed {deployed_count} bot(s) successfully!\nUse /allscripts to view.")
    context.user_data.clear()
    return ConversationHandler.END

async def allscripts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not running_bots:
        await update.message.reply_text("No bots running currently.")
        return
    text = "<b>📋 Running Bots:</b>\n\n"
    for sid in sorted(running_bots.keys()):
        info = running_bots[sid]
        owner = "You" if info["user_id"] == update.message.from_user.id else f"User {info['user_id']}"
        text += f"<b>{sid}.</b> {info['name']} — {owner} — <i>Running</i>\n"
    await update.message.reply_text(text, parse_mode='HTML')

# stop, restart, cancel same as before (copy from previous)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

if __name__ == '__main__':
    load_data()
    token = os.getenv("BOT_TOKEN", "7636825715:AAGc-t4nUfO_9NTsWCWVmD96SDZjlsCybvM")  # Deploy pe env se lega
    application = ApplicationBuilder().token(token).build()

    # Two conversation handlers
    manual_conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REPO_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_script)],
            CONFIRM_RUN: [CommandHandler('run', run_deployment)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    git_conv = ConversationHandler(
        entry_points=[CommandHandler('gitclone', gitclone)],
        states={
            REPO_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_repo)],
            CONFIRM_RUN: [CommandHandler('run', run_deployment)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(manual_conv)
    application.add_handler(git_conv)
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('allscripts', allscripts))
    # add stop/restart handlers too

    print("🖤 Zudofather High Level Hoster is ONLINE!")
    application.run_polling(drop_pending_updates=True)
