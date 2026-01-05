import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

RECEIPT_GROUP_ID = int(os.getenv("RECEIPT_GROUP_ID", "0").strip() or "0")

ROOT_ADMINS = [
    int(x.strip())
    for x in os.getenv("ROOT_ADMINS", "").split(",")
    if x.strip().isdigit()
]

API_SECRET = os.getenv("API_SECRET", "").strip()
API_HOST = os.getenv("API_HOST", "0.0.0.0").strip()
API_PORT = int(os.getenv("API_PORT", "8000").strip() or "8000")


def require_env():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Put it in .env")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Put it in .env")
    if not API_SECRET:
        raise RuntimeError("API_SECRET is missing. Put it in .env")
    if len(ROOT_ADMINS) < 2:
        raise RuntimeError("ROOT_ADMINS must contain 2 tg_ids (comma-separated).")

