import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

import requests
from flask import Blueprint, g, render_template, request
from telebot import apihelper, types

import calculator
import commands
import config
import database

mini_app = Blueprint("mini_app", __name__, url_prefix="/mini")
AUTH_MAX_AGE_SECONDS = 3600
CATALOG_COLORS = sorted((*commands.PRODUCT_COLORS,
    'Taupe', 'Stone', 'Mahogany', 'Creame', 'Latte', 'Rose', 'Peanut', 'Avocado',
    'Rosewood', 'Emerald', 'Bone', 'Vanilla', 'Cappucino', 'Almond', 'Berry',
    'Beige', 'Ocean', 'Millo', 'Army', 'Smoke', 'Matcha', 'Blush', 'Grape',
    'Wood', 'Salem', 'Dove', 'Moccachino', 'Butter', 'Lemon', 'Midnight',
    'Pistacio', 'Orchid', 'Cherry', 'Terracota', 'Frappucino', 'Turqoise',
    'Redwood', 'Gold', 'Walnut', 'Sand', 'Mocha',
), key=len, reverse=True)


def verify_telegram_user(init_data, bot_token, now):
    if not init_data or len(init_data) > 8192:
        return None
    try:
        pairs = parse_qsl(init_data, strict_parsing=True, max_num_fields=30)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            return None
        received_hash = fields.pop("hash")
        signed = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
        expected = hmac.new(secret, signed.encode(), hashlib.sha256).hexdigest()
        if not received_hash.isascii() or not hmac.compare_digest(received_hash, expected):
            return None
        age = now - int(fields["auth_date"])
        if not -30 <= age <= AUTH_MAX_AGE_SECONDS:
            return None
        user_id = json.loads(fields["user"])["id"]
        if type(user_id) is int and user_id > 0:
            return str(user_id)
    except (TypeError, ValueError, KeyError):
        return None
    return None


def dashboard_url():
    url = config.MINI_APP_URL
    try:
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password:
            return url
        return ""
    except ValueError:
        return ""


def register_handlers(bot):
    @bot.message_handler(commands=["dashboard"])
    def open_dashboard(message):
        url = dashboard_url()
        if not url:
            bot.reply_to(message, "Dashboard belum dikonfigurasi.")
        elif message.chat.type != "private":
            bot.reply_to(message, "Buka chat pribadi dengan bot untuk mengakses dashboard.")
        elif commands._check_subscription(bot, message):
            try:
                database.call_rpc("get_mini_watchlist", {"p_chat_id": str(message.chat.id)})
            except requests.RequestException as exc:
                print(f"Mini App readiness check failed: {exc}")
                bot.reply_to(message, "Dashboard belum siap. Silakan coba lagi nanti.")
                return
            keyboard = types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("Buka Dashboard", web_app=types.WebAppInfo(url))
            )
            bot.reply_to(message, "Cari stok, pantau varian, dan buat katalog di dashboard.", reply_markup=keyboard)


@mini_app.get("/")
def dashboard():
    return render_template("mini_app.html")


@mini_app.before_request
def require_subscription():
    if request.endpoint == "mini_app.dashboard":
        return None
    prefix, _, init_data = request.headers.get("Authorization", "").partition(" ")
    if prefix != "tma":
        return {"error": "Sesi berakhir. Buka ulang Dashboard dari bot."}, 401
    user_id = verify_telegram_user(init_data, config.TELEGRAM_BOT_TOKEN, time.time())
    if not user_id:
        return {"error": "Sesi berakhir. Buka ulang Dashboard dari bot."}, 401

    settings = database.get_user_settings(user_id)
    active, _ = commands._is_user_active(settings)
    if not active or settings.get("plan_type") == "suspended":
        return {"error": "Aktifkan trial atau langganan lewat bot untuk memakai Dashboard."}, 403
    g.user_id = user_id
    g.settings = settings


@mini_app.after_request
def private_api_response(response):
    if request.path.startswith("/mini/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


@mini_app.errorhandler(requests.RequestException)
def handle_database_error(exc):
    print(f"Mini App database request failed: {exc}")
    return {"error": "Layanan stok sedang tidak tersedia. Coba lagi sebentar."}, 503


def snapshot_products():
    row = database.read_snapshot()
    if not row or not row["data"]:
        return {}, None
    return {(item.get("nama") or key): item for key, item in row["data"].items()}, row["updated_at"]


def product_details(name, item):
    base, size = commands.split_product_size(name)
    category_match = re.match(r"^\(([^)]+)\)\s*", base)
    category = ""
    if category_match:
        category = category_match.group(1).strip()
        base = base[category_match.end():]
    if size == "-" and (size_suffix := re.search(r"\s+(ALL|FREE)\s+SIZE$", base, re.IGNORECASE)):
        size = size_suffix.group(0).strip().upper()
        base = base[:size_suffix.start()]
    product_name, color = commands.split_product_color(base, CATALOG_COLORS)
    return {
        "id": name,
        "name": item.get("product_name") or product_name,
        "color": item.get("color") or color,
        "category": category,
        "size": size if size != "-" else "Semua ukuran",
        "stock": item.get("stock", ""),
        "price": item.get("harga", ""),
    }


@mini_app.get("/api/dashboard")
def dashboard_data():
    products, updated_at = snapshot_products()
    watched = database.call_rpc("get_mini_watchlist", {"p_chat_id": g.user_id})
    for row in watched:
        name = row["product_name"]
        row["product"] = product_details(name, products.get(name, {}))
    return {
        "updated_at": updated_at,
        "watchlist": watched,
        "products": [product_details(name, products[name]) for name in sorted(products, key=str.casefold)],
    }


@mini_app.route("/api/watchlist", methods=["POST", "DELETE"])
def update_watchlist():
    body = request.get_json(silent=True)
    product = body.get("product") if isinstance(body, dict) else None
    if not isinstance(product, str) or not 1 <= len(product) <= 500:
        return {"error": "Pilih varian yang valid."}, 400
    enabled = request.method == "POST"
    if enabled:
        products, _ = snapshot_products()
        if product not in products:
            return {"error": "Varian sudah tidak tersedia di snapshot."}, 404
    last_restock = database.call_rpc("set_mini_watch", {
        "p_chat_id": g.user_id, "p_product_name": product, "p_enabled": enabled,
    })
    if enabled:
        return {"last_restock": last_restock}
    return "", 204


@mini_app.post("/api/catalog")
def create_catalog():
    body = request.get_json(silent=True)
    names = body.get("products") if isinstance(body, dict) else None
    if (not isinstance(names, list) or not 1 <= len(names) <= 50
            or any(not isinstance(name, str) or not 1 <= len(name) <= 500 for name in names)):
        return {"error": "Pilih 1–50 varian untuk katalog."}, 400
    products, _ = snapshot_products()
    selected = []
    unavailable = []
    for name in dict.fromkeys(names):
        item = products.get(name)
        if item and item.get("stock", "").lower() in ("aman", "ready"):
            selected.append((name, item))
        else:
            unavailable.append(name)
    if unavailable:
        return {"error": "Sebagian varian sudah habis. Muat ulang stok sebelum membuat katalog."}, 409

    markup = float(g.settings.get("markup_value") or 0)
    fee = float(g.settings.get("admin_fee") or 0)
    markup_type = g.settings.get("markup_type", "percent")
    lines = ["KATALOG PRODUK TERSEDIA"]
    has_missing_price = False
    for (name, price), colors in sorted(commands._group_products_by_variant(selected).items()):
        if calculator.parse_price(price) > 0:
            if markup > 0:
                result = calculator.calculate_price(price, markup, markup_type, fee, config.BIAYA_TAMBAHAN)
                price_label = calculator.format_price(result["harga_jual"])
            else:
                price_label = calculator.format_price(calculator.parse_price(price) + config.BIAYA_TAMBAHAN)
        else:
            price_label = "Tanya harga"
            has_missing_price = True
        lines.extend(("", f"{name} — {price_label}"))
        for color, sizes in sorted(colors.items()):
            prefix = f"{color}: " if color != "-" else ""
            labels = [commands._format_size_label(size) for size in commands._ordered_sizes(sizes) if size != "-"]
            lines.append(f"• {prefix}{', '.join(labels) or 'Tersedia'}")
    lines.extend(("", "Chat untuk cek ketersediaan dan pesan."))
    return {"text": "\n".join(lines), "uses_cost": markup <= 0, "missing_price": has_missing_price}


def send_restock_alerts(bot):
    try:
        alerts = database.call_rpc("claim_watch_restock_alerts", {})
    except requests.RequestException as exc:
        if exc.response is None or exc.response.status_code != 404:
            print(f"Watchlist alert query failed: {exc}")
        return
    for alert in alerts:
        keyboard = None
        if url := dashboard_url():
            keyboard = types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("Buka Dashboard", web_app=types.WebAppInfo(url))
            )
        try:
            bot.send_message(
                alert["chat_id"], f"🔔 RESTOCK PANTAUAN\n\n{alert['product_name']}\n"
                "Varian ini kembali tersedia saat stok terakhir diperiksa.", reply_markup=keyboard,
            )
        except apihelper.ApiTelegramException as exc:
            if exc.error_code != 403:
                print(f"Watchlist Telegram alert failed: {exc}")
                continue
        except Exception as exc:
            print(f"Watchlist Telegram alert failed: {exc}")
            continue
        try:
            response = database.session.delete(
                f"{database.SUPABASE_URL}/watch_restock_alerts",
                params={"chat_id": f"eq.{alert['chat_id']}", "event_id": f"eq.{alert['event_id']}"},
                headers={"Prefer": "return=minimal"}, timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Watchlist alert acknowledgement failed: {exc}")
        time.sleep(0.05)
