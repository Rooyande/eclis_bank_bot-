# bot.py
import os
import sys
import uuid
import logging
import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# وب‌سرور سبک برای وبهوک
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
import uvicorn

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

# ---------------- Config (توکن و شناسه‌ها دست‌نخورده) ----------------
BOT_TOKEN = "8021975466:AAGV_CanoaR3FQ-7c3WcPXbZRPpK6_K-KMQ"  # همان توکن خودت
BANK_GROUP_ID = int(os.environ.get("BANK_GROUP_ID", "-1002585326279"))
BANK_OWNER_ID = int(os.environ.get("BANK_OWNER_ID", "8423995337"))

# پورت و URL عمومی سرویس برای ست‌کردن وبهوک
PORT = int(os.environ.get("PORT", "8000"))  # Render خودش ست می‌کند
PUBLIC_URL = os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")  # اختیاری ولی توصیه می‌شود
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"  # مسیر اختصاصی وبهوک (بدون تغییر توکن)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bankbot")

WELCOME_TEXT = (
    "👋 به سولن بانک خوش آمدید!\n"
    "برای ساخت حساب شخصی: /register <code>\n"
    "برای دیدن دستورات: /help"
)

HELP_TEXT = (
    "📖 دستورات:\n\n"
    "— همه —\n"
    "/start — شروع\n"
    "/help — راهنما\n"
    "/register <code> — ساخت حساب شخصی با کد ثبت‌نام\n"
    "/balance — ماندهٔ حساب اصلی\n"
    "/myaccounts — لیست حساب‌ها\n"
    "/transfer <to_account_id> <amount> — انتقال وجه\n\n"
    "— صاحبان کسب‌وکار —\n"
    "/paysalary <from_business_acc> <to_acc> <amount>\n\n"
    "— ادمین بانک —\n"
    "/newcode <code>\n"
    "/createbusiness <name>\n"
    "/transferowner <account_id> <new_owner_tg_id>\n"
    "/listusers\n"
    "/bankadd <amount>\n"
    "/banktake <amount>\n"
    "/bankbalance\n"
    "/banktransfer <to_account_id> <amount>\n"
    "/takefrom <from_account_id> <amount>\n"
    "/closeaccount <account_id>\n"
    "/closebusiness <account_id>\n\n"
    "— مالک بانک —\n"
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

async def _reply_split(update, text: str, chunk: int = 3900):
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
        await update.message.reply_text("نحوهٔ استفاده: /register <code>")
        return
    code = context.args[0].strip()
    account_id, msg = await create_user(user.id, user.username or "", user.full_name or "", code)
    if not account_id:
        await update.message.reply_text(f"❌ {msg}")
        return
    await update.message.reply_text(f"✅ حساب ساخته شد!\nID: {account_id}\nBalance: 0 Solen")
    if BANK_GROUP_ID:
        await context.bot.send_message(
            chat_id=BANK_GROUP_ID,
            text=f"🟢 کاربر جدید: {user.full_name} (@{user.username or 'no-username'}) — TGID: {user.id}\nAccount: {account_id}"
        )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_by_tgid(update.effective_user.id)
    if not user:
        await update.message.reply_text("⛔ حسابی پیدا نشد.")
        return
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("⛔ هیچ حسابی ندارید.")
        return
    main_acc = next((a for a in accounts if a["type"] == "PERSONAL"), accounts[0])
    await update.message.reply_text(f"📊 {main_acc['account_id']}: {main_acc['balance']} Solen")

async def myaccounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("حسابی ندارید.")
        return
    text = "\n".join([f"- {a['account_id']} | {a['type']} | Balance: {a['balance']}" for a in accounts])
    await update.message.reply_text("👛 حساب‌ها:\n" + text)

async def transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("نحوهٔ استفاده: /transfer <to_account_id> <amount>")
        return
    if not await get_user_by_tgid(update.effective_user.id):
        await update.message.reply_text("⛔ شما حساب ندارید.")
        return
    to_acc = context.args[0].strip().upper()
    amount = _parse_amount(context.args[1])
    if amount is None:
        await update.message.reply_text("❌ مبلغ نامعتبر (باید > 0 باشد).")
        return
    accounts = await list_user_accounts(update.effective_user.id)
    if not accounts:
        await update.message.reply_text("⛔ هیچ حسابی ندارید.")
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
    await update.message.reply_text("✅ انجام شد!" if success else f"❌ {status}")

# ---------------- Business ----------------
async def paysalary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("نحوهٔ استفاده: /paysalary <from_business_acc> <to_acc> <amount>")
        return
    from_acc, to_acc = context.args[0].upper(), context.args[1].upper()
    amount = _parse_amount(context.args[2])
    if amount is None:
        await update.message.reply_text("❌ مبلغ نامعتبر (باید > 0 باشد).")
        return
    if not await can_use_account(update.effective_user.id, from_acc, must_be_type="BUSINESS"):
        await update.message.reply_text("⛔ این حساب بیزنسی متعلق به شما نیست.")
        return
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_acc, to_acc, amount)
    receiver = await get_user_by_account(to_acc)
    await create_transaction(txid, from_acc, to_acc, amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, from_acc, to_acc, amount, "Completed" if success else "Failed")
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt, update.effective_user.id, receiver_tg)
    await update.message.reply_text("✅ حقوق پرداخت شد." if success else f"❌ {status}")

# ---------------- Admin ----------------
async def newcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /newcode <code>")
    ok, msg = await add_register_code(context.args[0].strip())
    await update.message.reply_text("✅ کد اضافه شد." if ok else f"❌ {msg}")

async def createbusiness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /createbusiness <name>")
    name = " ".join(context.args).strip()
    acc_id, err = await create_business_account(update.effective_user.id, name)
    if err:
        return await update.message.reply_text(f"❌ {err}")
    await update.message.reply_text(f"✅ حساب بیزنسی ساخته شد: {acc_id} (مالک: {update.effective_user.full_name})")

async def transferowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if len(context.args) < 2:
        return await update.message.reply_text("نحوهٔ استفاده: /transferowner <account_id> <new_owner_tg_id>")
    acc_id = context.args[0].upper()
    try:
        new_owner = int(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ new_owner_tg_id باید عدد باشد.")
    user = await get_user_by_tgid(new_owner)
    if not user:
        return await update.message.reply_text("❌ مالک جدید هنوز /register نکرده.")
    ok, msg = await transfer_account_ownership(acc_id, new_owner)
    await update.message.reply_text("✅ مالکیت منتقل شد." if ok else f"❌ {msg}")

async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    users = await list_all_users()
    if not users:
        return await update.message.reply_text("کاربری نیست.")
    lines = [f"- {u['full_name']} (@{u['username'] or '—'}) | TGID: {u['tg_id']} | ACC: {u['account_id']}" for u in users]
    await _reply_split(update, f"👥 کاربران ({len(users)}):\n" + "\n".join(lines))

async def bank_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /bankadd <amount>")
    amount = _parse_amount(context.args[0])
    if amount is None:
        return await update.message.reply_text("❌ مبلغ نامعتبر.")
    ok, msg = await adjust_account_balance("ACC-001", amount)
    await update.message.reply_text("✅ افزوده شد." if ok else f"❌ {msg}")

async def bank_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /banktake <amount>")
    amount = _parse_amount(context.args[0])
    if amount is None:
        return await update.message.reply_text("❌ مبلغ نامعتبر.")
    ok, msg = await adjust_account_balance("ACC-001", -amount)
    await update.message.reply_text("✅ برداشت شد." if ok else f"❌ {msg}")

async def bank_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    bal = await get_account_balance("ACC-001")
    await update.message.reply_text(f"🏦 ماندهٔ بانک: {bal} Solen")

async def bank_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if len(context.args) < 2:
        return await update.message.reply_text("نحوهٔ استفاده: /banktransfer <to_acc> <amount>")
    to_acc = context.args[0].upper()
    amount = _parse_amount(context.args[1])
    if amount is None:
        return await update.message.reply_text("❌ مبلغ نامعتبر.")
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds("ACC-001", to_acc, amount)
    receiver = await get_user_by_account(to_acc)
    await create_transaction(txid, "ACC-001", to_acc, amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, "ACC-001", to_acc, amount, "Completed" if success else "Failed")
    receiver_tg = receiver["tg_id"] if receiver else None
    await _send_receipt(context, receipt, update.effective_user.id, receiver_tg)
    await update.message.reply_text("✅ انتقال انجام شد." if success else f"❌ {status}")

async def take_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if len(context.args) < 2:
        return await update.message.reply_text("نحوهٔ استفاده: /takefrom <from_acc> <amount>")
    from_acc = context.args[0].upper()
    amount = _parse_amount(context.args[1])
    if amount is None:
        return await update.message.reply_text("❌ مبلغ نامعتبر.")
    txid = "TX-" + uuid.uuid4().hex[:8].upper()
    success, status = await transfer_funds(from_acc, "ACC-001", amount)
    await create_transaction(txid, from_acc, "ACC-001", amount, "Completed" if success else "Failed")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = generate_receipt_image(txid, now, from_acc, "ACC-001", amount, "Completed" if success else "Failed")
    sender = await get_user_by_account(from_acc)
    sender_tg = sender["tg_id"] if sender else None
    await _send_receipt(context, receipt, update.effective_user.id, sender_tg)
    await update.message.reply_text("✅ برداشت شد." if success else f"❌ {status}")

async def close_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /closeaccount <account_id>")
    ok, msg = await delete_account(context.args[0].upper())
    await update.message.reply_text("✅ حذف شد." if ok else f"❌ {msg}")

async def close_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin_or_owner(update.effective_user.id):
        return await update.message.reply_text("⛔ فقط ادمین.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /closebusiness <account_id>")
    ok, msg = await delete_business_account(context.args[0].upper())
    await update.message.reply_text("✅ کسب‌وکار حذف شد." if ok else f"❌ {msg}")

# ---------------- Owner ----------------
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        return await update.message.reply_text("⛔ فقط مالک بانک.")
    if len(context.args) < 2:
        return await update.message.reply_text("نحوهٔ استفاده: /addadmin <id> <name>")
    try:
        tg_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ <id> باید عدد باشد.")
    name = " ".join(context.args[1:]).strip()
    if not name:
        return await update.message.reply_text("❌ نام خالی است.")
    await add_admin(tg_id, name)
    await update.message.reply_text(f"✅ ادمین اضافه شد: {name} ({tg_id})")

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        return await update.message.reply_text("⛔ فقط مالک بانک.")
    if not context.args:
        return await update.message.reply_text("نحوهٔ استفاده: /removeadmin <telegram_id>")
    try:
        tg_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ <telegram_id> باید عدد باشد.")
    await remove_admin(tg_id)
    await update.message.reply_text(f"✅ ادمین حذف شد: {tg_id}")

async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_bank_owner(update.effective_user.id, BANK_OWNER_ID):
        return await update.message.reply_text("⛔ فقط مالک بانک.")
    admins = await list_admins()
    if not admins:
        return await update.message.reply_text("ادمینی وجود ندارد.")
    text = "\n".join([f"- {name} ({tg_id})" for tg_id, name in admins])
    await update.message.reply_text("👑 ادمین‌ها:\n" + text)

# ---------------- Webhook Server (Starlette/Uvicorn) ----------------
async def main():
    # ویندوز لوکال: حلقهٔ ایونت سازگار
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 1) PTB Application بدون Updater (چون خودمان وب‌سرور داریم)
    application = Application.builder().token(BOT_TOKEN).updater(None).build()

    # 2) هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("myaccounts", myaccounts))
    application.add_handler(CommandHandler("transfer", transfer))
    application.add_handler(CommandHandler("paysalary", paysalary))
    application.add_handler(CommandHandler("newcode", newcode))
    application.add_handler(CommandHandler("createbusiness", createbusiness))
    application.add_handler(CommandHandler("transferowner", transferowner))
    application.add_handler(CommandHandler("listusers", listusers))
    application.add_handler(CommandHandler("bankadd", bank_add))
    application.add_handler(CommandHandler("banktake", bank_take))
    application.add_handler(CommandHandler("bankbalance", bank_balance))
    application.add_handler(CommandHandler("banktransfer", bank_transfer))
    application.add_handler(CommandHandler("takefrom", take_from))
    application.add_handler(CommandHandler("closeaccount", close_account))
    application.add_handler(CommandHandler("closebusiness", close_business))
    application.add_handler(CommandHandler("addadmin", add_admin_cmd))
    application.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
    application.add_handler(CommandHandler("listadmins", list_admins_cmd))

    # 3) DB init
    await init_db(BANK_OWNER_ID)

    # 4) ساخت URL وبهوک
    base_url = PUBLIC_URL or f"http://localhost:{PORT}"
    webhook_url = f"{base_url}{WEBHOOK_PATH}"
    logger.info(f"Setting webhook to: {webhook_url}")

    # 5) ثبت وبهوک در تلگرام
    await application.bot.set_webhook(
        url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        secret_token=(WEBHOOK_SECRET or None),
        drop_pending_updates=True
    )

    # 6) مسیر وبهوک + healthcheck
    async def telegram_webhook(request: Request) -> Response:
        # تأیید توکن مخفی هدر (اختیاری ولی امن)
        if WEBHOOK_SECRET:
            hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if hdr != WEBHOOK_SECRET:
                return PlainTextResponse("forbidden", status_code=403)

        # دریافت آپدیت و هل‌دادن به صف PTB
        data = await request.json()
        await application.update_queue.put(Update.de_json(data=data, bot=application.bot))
        return Response()

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    routes = [
        Route(WEBHOOK_PATH, telegram_webhook, methods=["POST"]),
        Route("/healthz", health, methods=["GET"]),
    ]
    starlette_app = Starlette(routes=routes)

    # 7) اجرای همزمان PTB + Uvicorn (الگوی رسمی PTB)
    webserver = uvicorn.Server(
        uvicorn.Config(app=starlette_app, host="0.0.0.0", port=PORT, use_colors=False)
    )

    async with application:
        await application.start()
        logger.info("🤖 Bot (webhook mode) is running...")
        await webserver.serve()
        await application.stop()

if __name__ == "__main__":
    asyncio.run(main())
