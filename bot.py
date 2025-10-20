# bot.py
import os
import logging
import uuid
import sys
from datetime import datetime
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from db import (
    init_db, create_user, get_user_by_tgid, get_user_by_account,
    transfer_funds, create_transaction, add_register_code,
    get_account_balance, adjust_account_balance,
    add_admin, remove_admin, list_admins,
    can_use_account, create_business_account, transfer_account_ownership,
    list_all_users, list_user_accounts, is_admin, is_bank_owner,
    delete_account, delete_business_account
)
from receipt import generate_receipt_image

# ---------------- Config ----------------
BOT_TOKEN = "8021975466:AAGV_CanoaR3FQ-7c3WcPXbZRPpK6_K-KMQ"  # unchanged
BANK_GROUP_ID = int(os.environ.get("BANK_GROUP_ID", "-1002585326279"))
BANK_OWNER_ID = int(os.environ.get("BANK_OWNER_ID", "8423995337"))

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
    "/balance — Show your main account balance\n"
    "/myaccounts — List your accounts\n"
    "/transfer <to_account_id> <amount> — Transfer money\n\n"
    "— Business Owners —\n"
    "/paysalary <from_business_acc> <to_acc> <amount>\n\n"
    "— Bank Admins —\n"
    "/newcode <code> — Add registration code\n"
    "/createbusiness <name> — Create business account\n"
    "/transferowner <account_id> <new_owner_tg_id>\n"
    "/listusers — List all users\n"
    "/bankadd <amount> — Add to main bank\n"
    "/banktake <amount> — Take from main bank\n"
    "/bankbalance — Show main bank balance\n"
    "/banktransfer <to_account_id> <amount>\n"
    "/takefrom <from_account_id> <amount> — Withdraw from any account\n"
    "/closeaccount <account_id> — Delete account\n"
    "/closebusiness <account_id> — Delete business account\n\n"
    "— Bank Owner —\n"
    "/addadmin <telegram_id> <name>\n"
    "/removeadmin <telegram_id>\n"
    "/listadmins\n"
)

# ---------------- Helpers ----------------
async def _send_receipt(context: ContextTypes.DEFAULT_TYPE, receipt_path: str, sender_tg_id: int, receiver_tg_id: int | None):
    try:
        with open(receipt_path, "rb") as f:
            await context.bot.send_photo(chat_id=sender_tg_id, photo=f)
    except Exception as e:
        logger.warning(f"Failed sending receipt to sender: {e}")
    if receiver_tg_id:
        try:
            with open(receipt_path, "rb") as f:
                await context.bot.send_photo(chat_id=receiver_tg_id, photo=f)
        except Exception as e:
            logger.warning(f"Failed sending receipt to receiver: {e}")
    if BANK_GROUP_ID:
        try:
            with open(receipt_path, "rb") as f:
                await context.bot.send_photo(chat_id=BANK_GROUP_ID, photo=f)
        except Exception as e:
            logger.warning(f"Failed sending receipt to group: {e}")

def _parse_amount(s: str) -> float | None:
    try:
        v = float(s)
        return v if v > 0 else None
    except:
        return None

async def _reply_split(update: Update, text: str, chunk: int = 3900):
    while text:
        await update.message.reply_text(text[:chunk])
        text = text[chunk:]

async def _is_admin_or_owner(tg_id: int) -> bool:
    return (await is_admin(tg_id)) or (await is_bank_owner(tg_id, BANK_OWNER_ID))

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
        await update.message.reply_text(f"❌ {msg}")
        return
    await update.message.reply_text(f"✅ Account created!\nAccount ID: {account_id}\nBalance: 0 Solen")
    if BANK_GROUP_ID:
        await context.bot.send_message(
            chat_id=BANK_GROUP_ID,
            text=f"🟢 New user: {user.full_name} (@{user.username or 'no-username'}) — TGID: {user.id}\nAccount: {account_id}"
        )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_by_tgid(update.effective_user.id)
    if not user:
        await update.message.reply_text("⛔ No account found.")
        return
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("⛔ No accounts found.")
        return
    main_acc = next((a for a in accounts if a["type"] == "PERSONAL"), accounts[0])
    await update.message.reply_text(f"📊 {main_acc['account_id']}: {main_acc['balance']} Solen")

async def myaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("You have no accounts.")
        return
    text = "\n".join([f"- {a['account_id']} | {a['type']} | Balance: {a['balance']}" for a in accounts])
    await update.message.reply_text("👛 Accounts:\n" + text)

async def transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /transfer <to_account_id> <amount>")
        return
    if not await get_user_by_tgid(update.effective_user.id):
        await update.message.reply_text("⛔ You don’t have an account.")
        return
    to_acc = context.args[0].strip().upper()
    amount = _parse_amount(context.args[1])
    if amount is None:
        await update.message.reply_text("❌ Invalid amount. Must be > 0.")
        return
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("⛔ No accounts found.")
        return
    from_acc = next((a["account_id"] for a in accounts if a["type"] == "PERSONAL"), accounts[0]["account_id"])
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_acc, to_acc, amount)
    receiver = await get_user_by_account(to_acc)
    await create_transaction(txid, from_acc, to_acc, amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, from_acc, to_acc, amount, "Completed" if success else "Failed")
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt, update.effective_user.id, receiver_tg)
    await update.message.reply_text("✅ Done!" if success else f"❌ {status}")

# ---------------- Business ----------------
async def paysalary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /paysalary <from_business_acc> <to_acc> <amount>")
        return
    from_acc, to_acc = context.args[0].upper(), context.args[1].upper()
    amount = _parse_amount(context.args[2])
    if amount is None:
        await update.message.reply_text("❌ Invalid amount. Must be > 0.")
        return
    if not await can_use_account(update.effective_user.id, from_acc, must_be_type="BUSINESS"):
        await update.message.reply_text("⛔ Not your business account.")
        return
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_acc, to_acc, amount)
    receiver = await get_user_by_account(to_acc)
    await create_transaction(txid, from_acc, to_acc, amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, from_acc, to_acc, amount, "Completed" if success else "Failed")
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt, update.effective_user.id, receiver_tg)
    await update.message.reply_text("✅ Salary paid." if success else f"❌ {status}")

# ---------------- Admin ----------------
async def newcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /newcode <code>")
    ok, msg = await add_register_code(context.args[0].strip())
    await update.message.reply_text("✅ Code added." if ok else f"❌ {msg}")

async def createbusiness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /createbusiness <name>")
    name = " ".join(context.args).strip()
    acc_id, err = await create_business_account(update.effective_user.id, name)
    if err:
        return await update.message.reply_text(f"❌ {err}")
    await update.message.reply_text(f"✅ Business account created: {acc_id} (owner: {update.effective_user.full_name})")

async def transferowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /transferowner <account_id> <new_owner_tg_id>")
    acc_id = context.args[0].upper()
    try:
        new_owner = int(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ new_owner_tg_id must be a number.")
    user = await get_user_by_tgid(new_owner)
    if not user:
        return await update.message.reply_text("❌ New owner has no user record. Ask them to /register first.")
    ok, msg = await transfer_account_ownership(acc_id, new_owner)
    await update.message.reply_text("✅ Ownership transferred." if ok else f"❌ {msg}")

async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    users = await list_all_users()
    if not users:
        return await update.message.reply_text("No users found.")
    lines = [f"- {u['full_name']} (@{u['username'] or '—'}) | TGID: {u['tg_id']} | ACC: {u['account_id']}" for u in users]
    await _reply_split(update, "👥 Users (" + str(len(users)) + "):\n" + "\n".join(lines))

async def bank_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /bankadd <amount>")
    amount = _parse_amount(context.args[0])
    if amount is None:
        return await update.message.reply_text("❌ Invalid amount. Must be > 0.")
    ok, msg = await adjust_account_balance("ACC-001", amount)
    await update.message.reply_text("✅ Added." if ok else f"❌ {msg}")

async def bank_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /banktake <amount>")
    amount = _parse_amount(context.args[0])
    if amount is None:
        return await update.message.reply_text("❌ Invalid amount. Must be > 0.")
    ok, msg = await adjust_account_balance("ACC-001", -amount)
    await update.message.reply_text("✅ Taken." if ok else f"❌ {msg}")

async def bank_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    bal = await get_account_balance("ACC-001")
    await update.message.reply_text(f"🏦 Bank balance: {bal} Solen")

async def bank_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /banktransfer <to_acc> <amount>")
    to_acc = context.args[0].upper()
    amount = _parse_amount(context.args[1])
    if amount is None:
        return await update.message.reply_text("❌ Invalid amount. Must be > 0.")
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds("ACC-001", to_acc, amount)
    receiver = await get_user_by_account(to_acc)
    await create_transaction(txid, "ACC-001", to_acc, amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, "ACC-001", to_acc, amount, "Completed" if success else "Failed")
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt, update.effective_user.id, receiver_tg)
    await update.message.reply_text("✅ Transfer done." if success else f"❌ {status}")

async def take_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /takefrom <from_acc> <amount>")
    from_acc = context.args[0].upper()
    amount = _parse_amount(context.args[1])
    if amount is None:
        return await update.message.reply_text("❌ Invalid amount. Must be > 0.")
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_acc, "ACC-001", amount)
    await create_transaction(txid, from_acc, "ACC-001", amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, from_acc, "ACC-001", amount, "Completed" if success else "Failed")
    sender = await get_user_by_account(from_acc)
    sender_tg = sender["tg_id"] if sender else None
    await _send_receipt(context, receipt, update.effective_user.id, sender_tg)
    await update.message.reply_text("✅ Taken." if success else f"❌ {status}")

async def close_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /closeaccount <account_id>")
    ok, msg = await delete_account(context.args[0].upper())
    await update.message.reply_text(f"✅ Deleted." if ok else f"❌ {msg}")

async def close_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ Admins only.")
    if not context.args:
        return await update.message.reply_text("Usage: /closebusiness <account_id>")
    ok, msg = await delete_business_account(context.args[0].upper())
    await update.message.reply_text(f"✅ Business deleted." if ok else f"❌ {msg}")

# ---------------- Owner ----------------
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        return await update.message.reply_text("⛔ Owner only.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /addadmin <id> <name>")
    try:
        tg_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ <id> must be a number.")
    name = " ".join(context.args[1:]).strip()
    if not name:
        return await update.message.reply_text("❌ Name cannot be empty.")
    await add_admin(tg_id, name)
    await update.message.reply_text(f"✅ Admin added: {name} ({tg_id})")

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        return await update.message.reply_text("⛔ Owner only.")
    if not context.args:
        return await update.message.reply_text("Usage: /removeadmin <telegram_id>")
    try:
        tg_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ <telegram_id> must be a number.")
    await remove_admin(tg_id)
    await update.message.reply_text(f"✅ Removed admin {tg_id}")

async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        return await update.message.reply_text("⛔ Owner only.")
    admins = await list_admins()
    if not admins:
        return await update.message.reply_text("No admins.")
    text = "\n".join([f"- {name} ({tg_id})" for tg_id, name in admins])
    await update.message.reply_text("👑 Admins:\n" + text)

# ---------------- PTB post-init: run DB init inside PTB's own loop ----------------
async def _post_init(app: Application):
    await init_db(BANK_OWNER_ID)

# ---------------- Main (no asyncio.run; no manual loops) ----------------
if __name__ == "__main__":
    if sys.platform == "win32":
        # Ensure PTB creates a selector-based loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)  # run init_db inside PTB-managed loop
        .build()
    )

    # User
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("myaccounts", myaccounts))
    app.add_handler(CommandHandler("transfer", transfer))

    # Business
    app.add_handler(CommandHandler("paysalary", paysalary))

    # Admin
    app.add_handler(CommandHandler("newcode", newcode))
    app.add_handler(CommandHandler("createbusiness", createbusiness))
    app.add_handler(CommandHandler("transferowner", transferowner))
    app.add_handler(CommandHandler("listusers", listusers))
    app.add_handler(CommandHandler("bankadd", bank_add))
    app.add_handler(CommandHandler("banktake", bank_take))
    app.add_handler(CommandHandler("bankbalance", bank_balance))
    app.add_handler(CommandHandler("banktransfer", bank_transfer))
    app.add_handler(CommandHandler("takefrom", take_from))
    app.add_handler(CommandHandler("closeaccount", close_account))
    app.add_handler(CommandHandler("closebusiness", close_business))

    # Owner
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
    app.add_handler(CommandHandler("listadmins", list_admins_cmd))

    # Single synchronous call; PTB manages its own loop safely
    app.run_polling(drop_pending_updates=True)
