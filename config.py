import os

# Konfigurasi Telegram Bot
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "GANTI_DENGAN_TOKEN_BOT_KAMU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "457147602")

# Konfigurasi Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://yjfodwmoouhmajdollku.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "GANTI_DENGAN_KUNCI_SUPABASE")

SUPABASE_SERVER_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or SUPABASE_KEY
)
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "7"))

# Konfigurasi API Ventedaily
BASE_URL = "https://erp.ventedaily.net/api/public/catalog"
PER_PAGE = 100

# Interval Pengecekan (dalam menit)
CHECK_INTERVAL = 5

# Biaya tambahan per produk (ditambahkan ke modal)
BIAYA_TAMBAHAN = 1250

# URL HTTPS untuk rute /mini/ pada hosting bot.
MINI_APP_URL = os.getenv("MINI_APP_URL", "").strip()
