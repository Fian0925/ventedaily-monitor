import os

# Konfigurasi Telegram Bot
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "GANTI_DENGAN_TOKEN_BOT_KAMU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "457147602")

# Konfigurasi Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://yjfodwmoouhmajdollku.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "GANTI_DENGAN_KUNCI_SUPABASE")

# Konfigurasi Scraping
BASE_URL = "https://pos.ventedaily.net/product-stock"
PER_PAGE = 200

# Interval Pengecekan (dalam menit)
CHECK_INTERVAL = 5

# Biaya tambahan per produk (ditambahkan ke modal)
BIAYA_TAMBAHAN = 1250
