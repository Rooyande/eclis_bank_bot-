# bot.py
import os
import logging
import uuid
import sys
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from db import (
    init_db, create_user, get_user_by_tgid, get_user_by_account,
    transfer_funds, create_transaction, add_register_code,
    get_account_balance, adjust_account_balance, # new helpers for ACC-001
    add_admin, remove_admin, list_admins,
    can_use_account, create_business_account, transfer_account_ownership,
    list_all_users, list_user_accounts, is_admin, is_bank_owner
)
from receipt import generate_receipt_image

# ---------------- Config (from ENV) ----------------
BOT_TOKEN = os.environ["BOT_TOKEN"] # REQUIRED
BANK_GROUP_ID = int(os.environ.get("BANK_GROUP_ID", "0")) # REQUIRED in production (group/channel id)
BANK_OWNER_ID = int(os.environ.get("BANK_OWNER_ID", "0")) # REQUIRED (only this person can manage admins)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bankbot")

WELCOME_TEXT = (
    "👋 Welcome to Solen Bank!\n"
    "Use /register <code> to open a personal account.\n"
    "Use /help to see available commands."
)

HELP_TEXT = (
    "📖 Commands:\n\n"
    "— Everyone —\n"
    "/start — Show welcome\n"
    "/help — Show this help\n"
    "/register <code> — Open a personal account using a registration code\n"
    "/balance — Show your main (personal) account balance\n"
    "/myaccounts — List all accounts you can use\n"
    "/transfer <to_account_id> <amount> — Transfer Solen to another account\n\n"
    "— Business Owners —\n"
    "/paysalary <from_business_acc> <to_account_id> <amount> — Pay salary from your business account\n\n"
    "— Bank Admins —\n"
    "/newcode <code> — Create a registration code\n"
    "/createbusiness <name> — Create a business account for the current user\n"
    "/transferowner <account_id> <new_owner_tg_id> — Transfer business ownership\n"
    "/bankadd <amount> — Add funds to ACC-001 (bank)\n"
    "/banktake <amount> — Take funds from ACC-001 (bank)\n"
    "/listusers — List registered users\n\n"
    "— Bank Owner —\n"
    "/addadmin <telegram_id> <name> — Add admin\n"
    "/removeadmin <telegram_id> — Remove admin\n"
    "/listadmins — List admins\n"
)

# ---------------- Helpers ----------------
async def _send_receipt(context: ContextTypes.DEFAULT_TYPE, receipt_path: str, sender_tg_id: int, receiver_tg_id: int | None):
    # send to sender
    try:
        await context.bot.send_photo(chat_id=sender_tg_id, photo=open(receipt_path, "rb"))
    except Exception as e:
        logger.warning(f"Failed sending receipt to sender: {e}")
    # send to receiver
    if receiver_tg_id:
        try:
            await context.bot.send_photo(chat_id=receiver_tg_id, photo=open(receipt_path, "rb"))
        except Exception as e:
            logger.warning(f"Failed sending receipt to receiver: {e}")
    # send to bank group
    if BANK_GROUP_ID:
        try:
            await context.bot.send_photo(chat_id=BANK_GROUP_ID, photo=open(receipt_path, "rb"))
        except Exception as e:
            logger.warning(f"Failed sending receipt to bank group: {e}")

# ---------------- User Commands ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: /register <code>")
        return
    code = context.args[0].strip()
    account_id, msg = await create_user(user.id, user.username or "", user.full_name or "", code)
    if not account_id:
        await update.message.reply_text(f"❌ Error: {msg}")
        return

    # notify user
    await update.message.reply_text(f"✅ Account created!\nAccount ID: {account_id}\nBalance: 0 Solen")

    # notify bank group
    if BANK_GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=BANK_GROUP_ID,
                text=f"🟢 New user registered: {user.full_name} (@{user.username or 'no-username'}) — TGID: {user.id}\n"
                     f"Personal Account: {account_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to notify bank group on registration: {e}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_by_tgid(update.effective_user.id)
    if not user:
        await update.message.reply_text("⛔ You don’t have an account. Use /register <code> first.")
        return
    # default: show the first account in their list (personal) or explicit personal type
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("⛔ No accounts found. Use /register <code> first.")
        return
    # Prefer PERSONAL if exists
    main_acc = next((a for a in accounts if a["type"] == "PERSONAL"), accounts[0])
    bal = main_acc["balance"]
    await update.message.reply_text(f"📊 Account: {main_acc['account_id']}\nBalance: {bal} Solen")

async def myaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("You have no accounts yet.")
        return
    lines = []
    for a in accounts:
        lines.append(f"- {a['account_id']} | {a['type']}{' — ' + a['name'] if a['name'] else ''} | Balance: {a['balance']}")
    await update.message.reply_text("👛 Your accounts:\n" + "\n".join(lines))

async def transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = await get_user_by_tgid(update.effective_user.id)
    if not sender:
        await update.message.reply_text("⛔ You don’t have an account. Use /register <code> first.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /transfer <to_account_id> <amount>")
        return

    to_account = context.args[0].strip().upper()
    try:
        amount = float(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Amount must be a positive number.")
        return

    # Choose default from_account = first account user can use (prefer PERSONAL)
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("⛔ You don’t have any usable account.")
        return
    from_account = next((a["account_id"] for a in accounts if a["type"] == "PERSONAL"), accounts[0]["account_id"])

    # Permission check
    allowed = await can_use_account(update.effective_user.id, from_account)
    if not allowed:
        await update.message.reply_text("⛔ You are not allowed to use this account.")
        return

    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_account, to_account, amount)
    receiver = await get_user_by_account(to_account)

    await create_transaction(
        txid, from_account, to_account, amount,
        "Completed" if success else "Incomplete"
    )

    # generate receipt with live timestamp
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt_path = generate_receipt_image(
        txid, now_str, from_account, to_account, amount,
        "Completed" if success else "Incomplete"
    )

    # send receipts
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt_path, update.effective_user.id, receiver_tg)

    if success:
        await update.message.reply_text("✅ Transfer successful.")
    else:
        await update.message.reply_text(f"❌ Transfer failed. {status}")

# ---------------- Business Owner Commands ----------------
async def paysalary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /paysalary <from_business_acc> <to_account_id> <amount>")
        return
    from_acc = context.args[0].strip().upper()
    to_acc = context.args[1].strip().upper()
    try:
        amount = float(context.args[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Amount must be a positive number.")
        return

    # must be business account and owned/accessible by user
    if not await can_use_account(update.effective_user.id, from_acc, must_be_type="BUSINESS"):
        await update.message.reply_text("⛔ You are not allowed to use this business account.")
        return

    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_acc, to_acc, amount)
    receiver = await get_user_by_account(to_acc)
    await create_transaction(
        txid, from_acc, to_acc, amount,
        "Completed" if success else "Incomplete"
    )
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt_path = generate_receipt_image(
        txid, now_str, from_acc, to_acc, amount,
        "Completed" if success else "Incomplete"
    )
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt_path, update.effective_user.id, receiver_tg)
    await update.message.reply_text("✅ Salary paid." if success else f"❌ Error: {status}")

# ---------------- Bank Admin Commands ----------------
async def newcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only bank admins can use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /newcode <code>")
        return
    code = context.args[0].strip()
    ok, msg = await add_register_code(code)
    await update.message.reply_text("✅ New register code added." if ok else f"❌ {msg}")

async def bank_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only bank admins can use this command.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /bankadd <amount>")
        return
    try:
        amount = float(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Amount must be positive.")
        return
    ok, msg = await adjust_account_balance("ACC-001", amount)
    await update.message.reply_text("✅ Added to bank balance." if ok else f"❌ {msg}")

async def bank_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only bank admins can use this command.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /banktake <amount>")
        return
    try:
        amount = float(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Amount must be positive.")
        return
    ok, msg = await adjust_account_balance("ACC-001", -amount)
    await update.message.reply_text("✅ Taken from bank balance." if ok else f"❌ {msg}")

async def create_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only bank admins can create business accounts.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /createbusiness <name>")
        return
    name = " ".join(context.args).strip()
    acc_id, msg = await create_business_account(update.effective_user.id, update.effective_chat.id, name)
    if acc_id:
        await update.message.reply_text(f"✅ Business account created: {name}\nAccount ID: {acc_id}")
    else:
        await update.message.reply_text(f"❌ {msg}")

async def transfer_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only bank admins can transfer ownership.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /transferowner <account_id> <new_owner_tg_id>")
        return
    acc = context.args[0].strip().upper()
    try:
        new_owner = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ new_owner_tg_id must be a number.")
        return
    ok, msg = await transfer_account_ownership(acc, new_owner)
    await update.message.reply_text(f"✅ Ownership of {acc} transferred to {new_owner}" if ok else f"❌ {msg}")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Only bank admins can use this command.")
        return
    users = await list_all_users()
    if not users:
        await update.message.reply_text("No users yet.")
        return
    lines = [f"- {u['full_name']} (@{u['username'] or 'no-username'}) — TGID: {u['tg_id']} — Account: {u['account_id']}" for u in users]
    await update.message.reply_text("👥 Users:\n" + "\n".join(lines))

# ---------------- Bank Owner Commands ----------------
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        await update.message.reply_text("⛔ Only the bank owner can add admins.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addadmin <telegram_id> <name>")
        return
    try:
        tg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ telegram_id must be a number.")
        return
    name = context.args[1]
    await add_admin(tg_id, name)
    await update.message.reply_text(f"✅ Admin added: {name} ({tg_id})")

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        await update.message.reply_text("⛔ Only the bank owner can remove admins.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /removeadmin <telegram_id>")
        return
    try:
        tg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ telegram_id must be a number.")
        return
    await remove_admin(tg_id)
    await update.message.reply_text(f"✅ Admin with ID {tg_id} removed.")

async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        await update.message.reply_text("⛔ Only the bank owner can view the admin list.")
        return
    admins = await list_admins()
    if not admins:
        await update.message.reply_text("No admins yet.")
    else:
        text = "👑 Admins:\n" + "\n".join([f"- {name} ({tid})" for tid, name in admins])
        await update.message.reply_text(text)

# ---------------- Main ----------------
if __name__ == "__main__":
    # Fix for Windows event loop
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Init DB
    import asyncio
    asyncio.get_event_loop().run_until_complete(init_db(BANK_OWNER_ID))

    app = Application.builder().token(BOT_TOKEN).build()

    # Everyone
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("myaccounts", myaccounts))
    app.add_handler(CommandHandler("transfer", transfer))

    # Business owners
    app.add_handler(CommandHandler("paysalary", paysalary))

    # Bank admins
    app.add_handler(CommandHandler("newcode", newcode))
    app.add_handler(CommandHandler("bankadd", bank_add))
    app.add_handler(CommandHandler("banktake", bank_take))
    app.add_handler(CommandHandler("createbusiness", create_business))
    app.add_handler(CommandHandler("transferowner", transfer_owner))
    app.add_handler(CommandHandler("listusers", list_users))

    # Bank owner
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
    app.add_handler(CommandHandler("listadmins", list_admins_cmd))

    print("🤖 Bot is running...")
    app.run_polling()