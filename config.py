import os
from dotenv import load_dotenv

# Muat variabel dari file .env (jika tersedia)
load_dotenv()


class Config:
    """
    Konfigurasi utama aplikasi.
    Semua nilai diambil dari environment variable (.env atau Docker Compose).
    """

    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY")
    DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

    # Database (gunakan URL tunggal)
    DATABASE_URL = os.getenv("DATABASE_URL")

    # WhatsApp API
    WA_API_URL = os.getenv("WA_API_URL")

    # MikroTik Router Admin
    ROUTER_ADMIN_BASE_URL = os.getenv("ROUTER_ADMIN_BASE_URL")
    ROUTER_ADMIN_USER = os.getenv("ROUTER_ADMIN_USER")
    ROUTER_ADMIN_PASSWORD = os.getenv("ROUTER_ADMIN_PASSWORD")

    # Admin Panel
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

    # Opsi tambahan
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Jakarta")
    ITEMS_PER_PAGE = int(os.getenv("ITEMS_PER_PAGE", "20"))