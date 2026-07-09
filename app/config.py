import os

from dotenv import load_dotenv

load_dotenv()

GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v25.0")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

DATABASE_URL = os.getenv("DATABASE_URL")

CALLING_API_ENABLED = os.getenv("CALLING_API_ENABLED", "false").lower() == "true"

if not WHATSAPP_TOKEN:
    raise RuntimeError("WHATSAPP_TOKEN is missing in .env")

if not WHATSAPP_PHONE_NUMBER_ID:
    raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is missing in .env")

if not WHATSAPP_VERIFY_TOKEN:
    raise RuntimeError("WHATSAPP_VERIFY_TOKEN is missing in .env")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing in .env")