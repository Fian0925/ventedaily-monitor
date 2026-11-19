import re
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import requests
from telebot import types

import database
import calculator
import config

WIB = timezone(timedelta(hours=7))

SIZE_ORDER = [
    'XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL',
    'ALL SIZE', 'FREE SIZE', '-'
]

# Label tombol Reply Keyboard bawah layar
BTN_CARI = "🔍 Cari Stok"
BTN_BARU = "🆕 Produk Baru"
BTN_RESTOCK = "🔄 Restock"
BTN_KATALOG = "📋 Buat Katalog"
BTN_HITUNG = "🧮 Hitung Harga"
BTN_LAPORAN = "📊 Laporan"
BTN_PROFIL = "👤 Profil & Pengaturan"
BTN_LANGGANAN = "💎 Langganan"
BTN_PAKET = "💎 Paket Langganan"
BTN_TRIAL = "🎁 Coba Gratis 5 Hari"
BTN_TAGIHAN = "📄 Cek Tagihan"

MENU_BUTTON_TEXTS = {
    BTN_CARI, BTN_BARU, BTN_RESTOCK, BTN_KATALOG, BTN_HITUNG,
    BTN_LAPORAN, BTN_PROFIL, BTN_LANGGANAN, BTN_PAKET, BTN_TRIAL, BTN_TAGIHAN
}


def get_main_keyboard(is_active=True):
    """Reply Keyboard permanen di bawah layar agar user awam tidak perlu hafal perintah."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if is_active:
        kb.row(types.KeyboardButton(BTN_CARI), types.KeyboardButton(BTN_BARU), types.KeyboardButton(BTN_RESTOCK))
        kb.row(types.KeyboardButton(BTN_KATALOG), types.KeyboardButton(BTN_HITUNG), types.KeyboardButton(BTN_LAPORAN))
        kb.row(types.KeyboardButton(BTN_PROFIL), types.KeyboardButton(BTN_LANGGANAN))
    else:
        kb.row(types.KeyboardButton(BTN_TRIAL), types.KeyboardButton(BTN_PAKET))
        kb.row(types.KeyboardButton(BTN_TAGIHAN), types.KeyboardButton(BTN_PROFIL))
    return kb


def _safe_cb(prefix, value):
    """Buat callback_data yang aman <= 64 byte UTF-8 (batas Telegram)."""
    raw = f"{prefix}:{value}"
    encoded = raw.encode('utf-8')
    if len(encoded) <= 64:
        return raw
    trimmed = encoded[:64].decode('utf-8', errors='ignore')
    return trimmed


def _send_long_message(bot, chat_id, text, reply_to=None, parse_mode="HTML", reply_markup=None):
    """Kirim pesan panjang, pecah jadi beberapa pesan jika > 4000 char.
    reply_markup ditempel pada potongan terakhir agar tombol selalu di paling bawah.
    """
    MAX_LEN = 4000
    if len(text) <= MAX_LEN:
        if reply_to:
            bot.reply_to(reply_to, text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
        return

    lines = text.split('\n')
    chunks = []
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > MAX_LEN:
            chunks.append(chunk)
            chunk = line + '\n'
        else:
            chunk += line + '\n'
    if chunk.strip():
        chunks.append(chunk)

    for idx, ch in enumerate(chunks):
        is_last = (idx == len(chunks) - 1)
        mk = reply_markup if is_last else None
        if idx == 0 and reply_to:
            bot.reply_to(reply_to, ch, parse_mode=parse_mode, reply_markup=mk)
        else:
            bot.send_message(chat_id, ch, parse_mode=parse_mode, reply_markup=mk)


def split_product_size(name):
    match = re.search(r"\s+SIZE\s+(?:Size\s+)?(.+)$", name, re.IGNORECASE)
    if not match:
        return name.strip(), "-"
    return name[:match.start()].strip(), re.sub(r"\s+", " ", match.group(1).strip().upper())


def _strip_size_suffix(nama):
    """Hapus akhiran 'SIZE ...' (termasuk 'SIZE Size XL' atau 'SIZE All Size')."""
    return re.sub(r'\s+SIZE\s+.*$', '', nama, flags=re.IGNORECASE).strip()


def _clean_product_name(nama):
    """Rapikan nama mentah ERP '(Couple) Abimaya Blue SIZE Size XL' -> '(Couple) Abimaya Blue (XL)'."""
    m = re.search(r'\s+SIZE\s+(?:Size\s+)?(.+)$', nama, flags=re.IGNORECASE)
    if not m:
        return nama.strip()
    base = nama[:m.start()].strip()
    sz_raw = m.group(1).strip()
    sz_up = sz_raw.upper()
    if sz_up == 'ALL SIZE':
        sz_label = 'All Size'
    elif sz_up == 'FREE SIZE':
        sz_label = 'Free Size'
    else:
        sz_label = sz_up
    return f"{base} ({sz_label})"


def _format_size_label(sz):
    """Format label ukuran agar enak dibaca ('ALL SIZE' -> 'All Size')."""
    sz_up = sz.upper()
    if sz_up == 'ALL SIZE':
        return 'All Size'
    if sz_up == 'FREE SIZE':
        return 'Free Size'
    return sz


def _ordered_sizes(sizes_dict):
    """Urutkan key ukuran dari sizes_dict tanpa ada ukuran yang terlewat."""
    ordered = [sz for sz in SIZE_ORDER if sz in sizes_dict]
    extras = sorted(sz for sz in sizes_dict.keys() if sz not in SIZE_ORDER)
    return ordered + extras


PRODUCT_COLORS = (
    'Dusty Pink', 'Baby Pink', 'Baby Blue', 'Cool Mint', 'Deep Red',
    'Dark Grey', 'Light Grey', 'Army Green', 'Soft Pink', 'Hot Pink',
    'Dusty Purple', 'Cream Gold', 'Dark Brown', 'Dark Choco',
    'Idul Fitri', 'Light Brown', 'Broken White', 'Off White', 'Sage Green',
    'Emerald Green', 'Royal Blue', 'Sky Blue', 'Dusty Blue', 'Rose Gold',
    'Choco', 'Purple', 'Maroon', 'Navy', 'Cream', 'White', 'Black',
    'Grey', 'Green', 'Blue', 'Red', 'Pink', 'Brown', 'Orange',
    'Mustard', 'Olive', 'Tosca', 'Sage', 'Lilac', 'Mocca',
    'Charcoal', 'Lavender', 'Burgundy', 'Khaki', 'Peach', 'Coral',
    'Caramel', 'Coffee', 'Denim', 'Plum', 'Hazelnut', 'Espresso',
    'Coksu', 'Mint', 'Milo', 'Teracotta', 'Terracotta', 'Mauve', 'Nude', 'Oat',
)


def split_product_color(base, colors=PRODUCT_COLORS):
    normalized = base.casefold()
    for color in colors:
        if normalized.endswith(" " + color.casefold()):
            return base[:-len(color)].rstrip(), color
    return base, ""


def _group_products_by_variant(products):
    """
    Kelompokkan produk berdasarkan varian (nama tanpa SIZE) dan warna.
    Input: list of (nama, {stock, harga})
    Output: dict {(base_name, harga): {warna: {size: stock}}}
    """
    groups = defaultdict(lambda: defaultdict(dict))

    for nama, info in products:
        base, size = split_product_size(nama)

        variant_base, color = split_product_color(base)
        groups[variant_base, info['harga']][color or "-"][size] = info['stock']

    return groups


def _parse_day_bucket(dt_str):
    """Ubah ISO timestamp ke (iso_date_sort_key, label_tanggal, jam_wib)."""
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00')).astimezone(WIB)
        return dt.strftime('%Y-%m-%d'), dt.strftime('%d %b %Y'), dt.strftime('%H:%M')
    except Exception:
        return '0000-00-00', 'Unknown', ''


def _is_user_active(settings):
    if settings.get('role') == 'admin':
        return True, datetime(2099, 12, 31, tzinfo=timezone.utc)
    valid_until_str = settings.get('valid_until', '2000-01-01T00:00:00Z')
    try:
        valid_until = datetime.fromisoformat(valid_until_str.replace('Z', '+00:00'))
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
    except Exception:
        valid_until = datetime(2000, 1, 1, tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= valid_until, valid_until


def _can_use_trial(settings):
    """Cek apakah user benar-benar baru dan belum pernah memakai trial/langganan/suspend."""
    if settings.get('role') == 'admin':
        return False
    trial_origin = datetime(2000, 1, 1, tzinfo=timezone.utc)
    raw_until = settings.get("valid_until")
    try:
        valid_until = datetime.fromisoformat(raw_until.replace("Z", "+00:00")) if raw_until else trial_origin
    except ValueError:
        return False
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    plan_type = (settings.get("plan_type") or "none").strip().lower()
    return valid_until == trial_origin and plan_type in ("", "none")


def _check_subscription(bot, message):
    settings = database.get_user_settings(message.chat.id)
    is_active, _ = _is_user_active(settings)
    if is_active:
        return True

    kb = types.InlineKeyboardMarkup(row_width=1)
    if _can_use_trial(settings):
        kb.add(types.InlineKeyboardButton("🎁 Aktifkan Free Trial 5 Hari", callback_data="act_trial"))
    kb.add(types.InlineKeyboardButton("💎 Pilih Paket / Perpanjang (QRIS)", callback_data="open_plans"))
    kb.add(types.InlineKeyboardButton("💬 Hubungi Admin", url="https://t.me/fianfi"))

    trial_hint = (
        "\n🎁 <b>Belum pernah coba?</b> Klik tombol <b>Free Trial 5 Hari</b> di bawah!\n"
        if _can_use_trial(settings) else ""
    )

    bot.reply_to(
        message,
        "⚠️ <b>Masa Aktif Habis / Belum Aktif</b>\n\n"
        "Aktifkan langganan untuk menikmati:\n"
        "✅ Notifikasi otomatis Restock & Produk Baru 24/7\n"
        "✅ Pencarian stok instan & buat Katalog WA 1-klik\n"
        "✅ Kalkulator harga jual otomatis semua Marketplace\n"
        f"{trial_hint}\n"
        "👇 Klik tombol di bawah untuk mulai:",
        parse_mode="HTML",
        reply_markup=kb
    )
    return False


def _check_admin(bot, message):
    settings = database.get_user_settings(message.chat.id)
    if settings.get('role') != 'admin':
        bot.reply_to(message, "❌ Akses ditolak. Perintah ini hanya untuk Admin.")
        return False
    return True


def _activate_trial_for_user(bot, chat_id, reply_to=None):
    settings = database.get_user_settings(chat_id)
    if settings.get('role') == 'admin':
        msg = "👑 Akun Admin sudah memiliki akses penuh tanpa batas."
        if reply_to:
            bot.reply_to(reply_to, msg)
        else:
            bot.send_message(chat_id, msg)
        return

    if not _can_use_trial(settings):
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💎 Pilih Paket Langganan (QRIS)", callback_data="open_plans"))
        msg = (
            "❌ <b>Free Trial Tidak Tersedia</b>\n\n"
            "Kamu sudah pernah menggunakan Free Trial atau sudah pernah berlangganan sebelumnya.\n"
            "Silakan pilih paket langganan di bawah untuk mengaktifkan kembali akses bot:"
        )
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML", reply_markup=kb)
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=kb)
        return

    try:
        activated_until = database.activate_trial(chat_id)
        if activated_until:
            new_valid = datetime.fromisoformat(activated_until.replace("Z", "+00:00"))
            msg = None
        else:
            msg = "⚠️ Trial tidak dapat diaktifkan. Cek status akun lewat /profil."
    except (requests.RequestException, ValueError) as exc:
        print(f"Error activating trial: {exc}")
        msg = "⚠️ Aktivasi trial gagal. Silakan coba lagi."
    if msg:
        if reply_to:
            bot.reply_to(reply_to, msg)
        else:
            bot.send_message(chat_id, msg)
        return
    until_wib = new_valid.astimezone(WIB).strftime('%d %b %Y, %H:%M WIB')

    welcome_trial = (
        "🎉 <b>FREE TRIAL 5 HARI AKTIF!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Berlaku s/d: <b>{until_wib}</b>\n\n"
        "✨ <b>Cara pakai paling gampang:</b>\n"
        "• Langsung ketik <b>nama produk</b> di chat (contoh: <code>sasmita</code>) untuk cek stok!\n"
        "• Atau gunakan <b>tombol menu</b> di bagian bawah layar.\n"
        "• Kamu juga akan otomatis menerima notifikasi saat ada produk Baru / Restock."
    )
    if reply_to:
        bot.reply_to(reply_to, welcome_trial, parse_mode="HTML", reply_markup=get_main_keyboard(True))
    else:
        bot.send_message(chat_id, welcome_trial, parse_mode="HTML", reply_markup=get_main_keyboard(True))


def _run_search(bot, chat_id, query, reply_to=None):
    """Jalankan pencarian produk dan tampilkan tombol cepat Katalog & Hitung Harga."""
    query = query.strip().lower()
    if not query:
        msg = "🔍 Ketik langsung <b>nama produk</b> yang ingin dicari.\nContoh: <code>sasmita</code> atau <code>abimaya</code>"
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    data = database.load_catalog_snapshot()
    if data is None:
        msg = "⚠️ Data produk sedang dimuat dari server, coba beberapa saat lagi."
        if reply_to:
            bot.reply_to(reply_to, msg)
        else:
            bot.send_message(chat_id, msg)
        return

    results = [(k, v) for k, v in data.items() if query in k.lower()]
    if not results:
        msg = (
            f"😔 Tidak menemukan produk dengan kata kunci <b>\"{query}\"</b>.\n"
            f"💡 Coba ketik sebagian nama saja (misal 1 kata utama)."
        )
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    ready = [(k, v) for k, v in results if v.get('stock', '').lower() in ['aman', 'ready']]
    if not ready:
        msg = f"😔 Produk <b>\"{query}\"</b> ditemukan ({len(results)} varian), tetapi saat ini semuanya sedang <b>HABIS</b>."
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    reply = f"🔍 <b>Hasil Pencarian: \"{query}\"</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    groups = _group_products_by_variant(ready)

    for variant_base, harga in sorted(groups):
        colors = groups[variant_base, harga]
        reply += f"📦 <b>{variant_base}</b> — {harga}\n"

        for warna in sorted(colors.keys()):
            sizes = colors[warna]
            size_display = []
            for sz in _ordered_sizes(sizes):
                stock = sizes[sz].lower()
                icon = "✅" if stock == "aman" else "⚠️"
                sz_lbl = _format_size_label(sz)
                if sz_lbl != "-":
                    size_display.append(f"{sz_lbl}{icon}")
                else:
                    size_display.append(f"Ready{icon}")
            if size_display:
                if warna != "-":
                    reply += f"  • {warna}: {' '.join(size_display)}\n"
                else:
                    reply += f"  • {' '.join(size_display)}\n"
        reply += "\n"

    reply += f"📊 Total Varian Ready: <b>{len(ready)}</b>\n"
    reply += "<i>✅ = Stok Aman (Banyak) | ⚠️ = Stok Ready (Limit 1 pcs)</i>"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📋 Buat Teks Katalog", callback_data=_safe_cb("qkatalog", query)),
        types.InlineKeyboardButton("🧮 Hitung Harga Jual", callback_data=_safe_cb("qhitung", query)),
    )

    _send_long_message(bot, chat_id, reply, reply_to=reply_to, reply_markup=kb)


def _run_katalog(bot, chat_id, query, reply_to=None):
    """Generate katalog bersih khusus pembeli WA (tanpa menyebut supplier Ventedaily)."""
    query = query.strip().lower()
    if not query:
        msg = "⚠️ Ketik nama produk/koleksi yang ingin dibuatkan katalog.\nContoh: <code>/katalog sasmita</code>"
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    data = database.load_catalog_snapshot()
    if data is None:
        bot.send_message(chat_id, "⚠️ Data produk belum tersedia.")
        return

    results = [(k, v) for k, v in data.items() if query in k.lower()]
    ready = [(k, v) for k, v in results if v.get('stock', '').lower() in ['aman', 'ready']]

    if not ready:
        msg = f"📭 Tidak ada produk <b>\"{query}\"</b> yang ready saat ini."
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    settings = database.get_user_settings(chat_id)
    markup_type = settings.get('markup_type', 'percent')
    markup_value = float(settings.get('markup_value', 0))
    admin_fee = float(settings.get('admin_fee', 6.5))
    has_markup = markup_value > 0

    reply = "🛍️ <b>KATALOG PRODUK READY</b>\n"
    reply += f"📅 Update: {datetime.now(WIB).strftime('%d %b %Y | %H:%M WIB')}\n"
    reply += "━━━━━━━━━━━━━━━━━━━━━\n\n"

    groups = _group_products_by_variant(ready)

    for variant_base, harga_str in sorted(groups):
        colors = groups[variant_base, harga_str]

        if has_markup:
            calc = calculator.calculate_price(harga_str, markup_value, markup_type, admin_fee, config.BIAYA_TAMBAHAN)
            price_label = calculator.format_price(calc['harga_jual']) if calc else harga_str
        else:
            # Jika belum set markup, minimal tambahkan biaya tambahan tapi nanti beri peringatan terpisah
            modal_plus = calculator.parse_price(harga_str) + config.BIAYA_TAMBAHAN
            price_label = calculator.format_price(modal_plus) if modal_plus > 0 else harga_str

        reply += f"✨ <b>{variant_base}</b> — {price_label}\n"
        for warna in sorted(colors.keys()):
            sizes = colors[warna]
            size_display = []
            for sz in _ordered_sizes(sizes):
                stock = sizes[sz].lower()
                icon = "✅" if stock == "aman" else "⚡"
                sz_lbl = _format_size_label(sz)
                if sz_lbl != "-":
                    size_display.append(f"{sz_lbl}{icon}")
                else:
                    size_display.append(f"Ready{icon}")

            if size_display:
                if warna != "-":
                    reply += f"  • {warna}: {' '.join(size_display)}\n"
                else:
                    reply += f"  • {' '.join(size_display)}\n"
        reply += "\n"

    reply += "━━━━━━━━━━━━━━━━━━━━━\n"
    reply += "<i>Ket: ✅ Tersedia | ⚡ Stok Terbatas</i>\n"
    reply += "📱 <b>Minat? Silakan chat untuk order sebelum kehabisan ya!</b>"

    _send_long_message(bot, chat_id, reply, reply_to=reply_to)

    # Jika reseller belum mengatur profit, kirim catatan pengaman di bubble terpisah agar tidak salah forward harga modal!
    if not has_markup:
        kb_warn = types.InlineKeyboardMarkup()
        kb_warn.add(types.InlineKeyboardButton("💰 Atur Target Profit Default", callback_data="set_profit"))
        bot.send_message(
            chat_id,
            "⚠️ <b>Catatan Reseller (Jangan ikut di-forward):</b>\n"
            "Kamu belum mengatur Target Profit (masih <b>0%</b>), sehingga harga pada katalog di atas masih memakai <b>Harga Modal</b>.\n\n"
            "Klik tombol di bawah agar setiap buat katalog otomatis memakai harga jualmu!",
            parse_mode="HTML",
            reply_markup=kb_warn
        )


def _save_profit_setting(bot, message, value):
    try:
        profit_type, profit = calculator.parse_profit(value)
    except ValueError:
        bot.reply_to(message, "❌ Format salah. Contoh: 20% atau 30.000.")
        return

    if not database.set_user_markup(message.chat.id, profit_type, profit):
        bot.reply_to(message, "⚠️ Target profit gagal disimpan. Silakan coba lagi.")
        return

    label = f"{profit:g}%" if profit_type == "percent" else calculator.format_price(profit)
    bot.reply_to(message, f"✅ Target profit default disimpan: <b>{label}</b>", parse_mode="HTML")


def _run_hitung(bot, chat_id, raw_args_text, reply_to=None):
    """Jalankan kalkulator harga jual marketplace."""
    tokens = raw_args_text.strip().split()
    if not tokens:
        msg = (
            "🧮 <b>KALKULATOR HARGA JUAL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Ketik nama produk diikuti target profit (opsional):\n\n"
            "• <code>/hitung sasmita 20%</code> (profit 20%)\n"
            "• <code>/hitung ziva 30000</code> (profit Rp 30.000)\n"
            "• <code>/hitung sasmita</code> (pakai profit default)\n\n"
            f"💡 Biaya packing/tambahan: {calculator.format_price(config.BIAYA_TAMBAHAN)}/pcs"
        )
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    data = database.load_catalog_snapshot()
    if data is None:
        bot.send_message(chat_id, "⚠️ Data produk belum tersedia.")
        return

    profit_str = None
    profit_val = 0
    profit_type = 'percent'
    query = " ".join(tokens).lower()

    last_arg = tokens[-1]
    if len(tokens) >= 2 and (last_arg.endswith("%") or calculator.is_nominal_amount(last_arg)):
        try:
            profit_type, profit_val = calculator.parse_profit(last_arg)
        except ValueError:
            bot.send_message(chat_id, "❌ Target profit tidak valid. Contoh: 20% atau 30.000.")
            return
        if profit_type == "percent" or profit_val >= 100:
            profit_str = last_arg
            query = " ".join(tokens[:-1]).lower()

    if not query:
        bot.send_message(chat_id, "⚠️ Masukkan nama produk. Contoh: <code>/hitung sasmita 20%</code>", parse_mode="HTML")
        return

    matches = [(k, v) for k, v in data.items() if query in k.lower() and v.get('stock', '').lower() in ['aman', 'ready']]
    if not matches:
        matches = [(k, v) for k, v in data.items() if query in k.lower()]
    if not matches:
        msg = f"❌ Produk <b>\"{query}\"</b> tidak ditemukan."
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML")
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        return

    settings = database.get_user_settings(chat_id)
    used_simulation = False

    if profit_str is None:
        profit_type = settings.get('markup_type', 'percent')
        profit_val = float(settings.get('markup_value', 0))
        if profit_val <= 0:
            # Berikan simulasi 20% otomatis agar user langsung mendapat gambaran tanpa buntu
            profit_type = 'percent'
            profit_val = 20.0
            used_simulation = True

    profit_label = f"{profit_val:g}%" if profit_type == 'percent' else f"Rp {int(profit_val):,}".replace(",", ".")
    default_mp = settings.get('marketplace', 'shopee')
    user_fee = float(settings.get('admin_fee', 6.5))
    mp_name = calculator.MARKETPLACES.get(default_mp, calculator.MARKETPLACES['shopee'])['name']
    biaya = config.BIAYA_TAMBAHAN

    variant_prices = {}
    for k, v in matches:
        harga = v.get('harga', '')
        base = _strip_size_suffix(k)
        if harga not in variant_prices:
            variant_prices[harga] = base

    reply = (
        f"🧮 <b>KALKULASI HARGA JUAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Produk: <b>\"{query}\"</b>\n"
        f"🎯 Target Profit: <b>{profit_label}</b>{' <i>(Simulasi)</i>' if used_simulation else ''}\n"
        f"🏪 Marketplace: <b>{mp_name}</b> (Admin {user_fee}%)\n\n"
    )

    for harga_str, variant_name in variant_prices.items():
        calc = calculator.calculate_price(harga_str, profit_val, profit_type, user_fee, biaya)
        if not calc:
            continue
        reply += (
            f"📦 <b>{variant_name}</b>\n"
            f"   Modal: {harga_str} + {calculator.format_price(biaya)} = {calculator.format_price(calc['modal'])}\n"
            f"   🏷️ Harga Jual: <b>{calculator.format_price(calc['harga_jual'])}</b>\n"
            f"   💰 Potongan Admin: -{calculator.format_price(calc['admin_fee'])}\n"
            f"   ✅ Profit Bersih: <b>{calculator.format_price(calc['actual_profit'])}</b>\n\n"
        )

    if len(variant_prices) == 1:
        harga_str = list(variant_prices.keys())[0]
        reply += "🏪 <b>PERBANDINGAN MARKETPLACE:</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        for mp_key in ['shopee', 'tokopedia', 'tiktok', 'lazada']:
            mp_info = calculator.MARKETPLACES[mp_key]
            fee = user_fee if mp_key == default_mp else mp_info['fee']
            comp = calculator.calculate_price(harga_str, profit_val, profit_type, fee, biaya)
            if not comp:
                continue
            marker = " ⬅️" if mp_key == default_mp else ""
            icon = '🟠' if mp_key == 'shopee' else '🟢' if mp_key == 'tokopedia' else '⚫' if mp_key == 'tiktok' else '🔵'
            reply += (
                f"{icon} {mp_info['name']} ({fee}%){marker}\n"
                f"   Jual: <b>{calculator.format_price(comp['harga_jual'])}</b> | Untung: {calculator.format_price(comp['actual_profit'])}\n"
            )

    if used_simulation:
        reply += "\n<i>ℹ️ Menampilkan simulasi profit 20%. Klik tombol di bawah untuk menyimpan profit default kamu.</i>"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💰 Ubah Target Profit", callback_data="set_profit"),
        types.InlineKeyboardButton("🛒 Ubah Marketplace", callback_data="set_mp"),
    )

    _send_long_message(bot, chat_id, reply, reply_to=reply_to, reply_markup=kb)


def _generate_report(bot, chat_id, reply_to=None):
    """Generate laporan mingguan produk baru & restock (7 hari terakhir)."""
    new_events = database.get_events('new', 7)
    restock_events = database.get_events('restock', 7)

    data = database.load_catalog_snapshot()
    total_produk = len(data) if data else 0

    if data:
        aman_count = sum(1 for v in data.values() if v.get('stock', '').lower() == 'aman')
        ready_count = sum(1 for v in data.values() if v.get('stock', '').lower() == 'ready')
        habis_count = sum(1 for v in data.values() if v.get('stock', '').lower() == 'habis')
    else:
        aman_count = ready_count = habis_count = 0

    reply = (
        f"📊 <b>LAPORAN MINGGUAN (7 HARI)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {datetime.now(WIB).strftime('%d %b %Y | %H:%M WIB')}\n\n"
        f"📦 <b>Total Varian Dipantau:</b> {total_produk}\n"
        f"   ✅ Aman: <b>{aman_count}</b>\n"
        f"   ⚠️ Ready (Terbatas): <b>{ready_count}</b>\n"
        f"   ❌ Habis: <b>{habis_count}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆕 <b>Produk Baru (7 hari):</b> {len(new_events)}\n"
    )

    if new_events:
        for ev in new_events[:10]:
            reply += f"  • {_clean_product_name(ev['nama'])} — {ev.get('harga', '-')}\n"
        if len(new_events) > 10:
            reply += f"  <i>... dan {len(new_events) - 10} lainnya</i>\n"

    reply += f"\n🔄 <b>Produk Restock (7 hari):</b> {len(restock_events)}\n"

    if restock_events:
        restock_count = defaultdict(int)
        for ev in restock_events:
            base_name = _strip_size_suffix(ev['nama'])
            restock_count[base_name] += 1

        for ev in restock_events[:10]:
            reply += f"  • {_clean_product_name(ev['nama'])} — {ev.get('harga', '-')}\n"
        if len(restock_events) > 10:
            reply += f"  <i>... dan {len(restock_events) - 10} lainnya</i>\n"

        top_restock = sorted(restock_count.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_restock:
            reply += "\n🔥 <b>Paling Sering Restock (Laris):</b>\n"
            for name, cnt in top_restock:
                reply += f"  🏆 {name} ({cnt}x varian restock)\n"

    reply += "\n━━━━━━━━━━━━━━━━━━━━━\n💡 <i>Laporan otomatis dikirim setiap Senin 08:00 WIB</i>"

    _send_long_message(bot, chat_id, reply, reply_to=reply_to)


def send_weekly_report(bot, chat_id):
    """Dipanggil oleh scheduler di monitor.py setiap Senin jam 08:00 WIB."""
    _generate_report(bot, chat_id)


def _render_events_list(bot, chat_id, event_type, days, title_emoji, title_text, empty_label, reply_to=None):
    """Helper rapi untuk /baru dan /restock dengan urutan tanggal kronologis."""
    events = database.get_events(event_type, days)
    kb = types.InlineKeyboardMarkup(row_width=3)
    prefix = "ev_baru" if event_type == 'new' else "ev_restock"
    kb.add(
        types.InlineKeyboardButton("📅 Hari Ini", callback_data=f"{prefix}:1"),
        types.InlineKeyboardButton("📅 3 Hari", callback_data=f"{prefix}:3"),
        types.InlineKeyboardButton("📅 7 Hari", callback_data=f"{prefix}:7"),
    )

    if not events:
        msg = f"📭 Belum ada {empty_label} dalam <b>{days} hari</b> terakhir."
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML", reply_markup=kb)
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=kb)
        return

    reply = f"{title_emoji} <b>{title_text} ({days} HARI TERAKHIR)</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"

    by_day = defaultdict(list)
    day_labels = {}
    for ev in events:
        sort_key, day_lbl, time_str = _parse_day_bucket(ev.get('detected_at', ''))
        by_day[sort_key].append((ev, time_str))
        day_labels[sort_key] = day_lbl

    count = 0
    for sort_key in sorted(by_day.keys(), reverse=True):
        items = by_day[sort_key]
        reply += f"📅 <b>{day_labels[sort_key]}</b>\n"
        for ev, time_str in items:
            count += 1
            if count > 30:
                reply += f"  <i>... dan {len(events) - 30} varian lainnya.</i>\n"
                break
            time_label = f" <i>({time_str})</i>" if time_str else ""
            clean_nm = _clean_product_name(ev['nama'])
            if event_type == 'restock':
                reply += f"  {count}. ✅ <b>{clean_nm}</b>{time_label}\n     Habis ➡️ {ev.get('stock','-')} | {ev.get('harga','-')}\n"
            else:
                reply += f"  {count}. 🆕 <b>{clean_nm}</b>{time_label}\n     Stok: {ev.get('stock','-')} | {ev.get('harga','-')}\n"
        reply += "\n"
        if count > 30:
            break

    reply += f"📊 Total: <b>{len(events)} varian</b>\n<i>💡 Ketik nama produk untuk cek stok lengkapnya.</i>"
    _send_long_message(bot, chat_id, reply, reply_to=reply_to, reply_markup=kb)


def _render_perubahan(bot, chat_id, filter_type=None, days=1, show_all=False, specific_date=None, reply_to=None):
    """Render laporan /perubahan dengan urutan tanggal kronologis dan tombol filter cepat."""
    events = database.get_events(filter_type, days, specific_date)

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Restock", callback_data="perub:restock:1"),
        types.InlineKeyboardButton("❌ Habis", callback_data="perub:habis:1"),
        types.InlineKeyboardButton("🆕 Baru", callback_data="perub:new:1"),
        types.InlineKeyboardButton("💰 Harga", callback_data="perub:price_change:1"),
    )
    kb.row(
        types.InlineKeyboardButton("📋 Semua Hari Ini", callback_data="perub:all:1"),
        types.InlineKeyboardButton("📊 Laporan 7 Hari", callback_data="perub:mingguan:7"),
    )

    if not events:
        filter_name = {
            'habis': 'produk HABIS',
            'restock': 'produk RESTOCK',
            'price_change': 'perubahan HARGA',
            'new': 'PRODUK BARU'
        }.get(filter_type, 'perubahan stok')
        rentang = f"tanggal {specific_date}" if specific_date else f"{days} hari terakhir"
        msg = f"📭 Belum ada data {filter_name} pada {rentang}."
        if reply_to:
            bot.reply_to(reply_to, msg, parse_mode="HTML", reply_markup=kb)
        else:
            bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=kb)
        return

    by_day = defaultdict(lambda: defaultdict(list))
    day_labels = {}
    total_events = defaultdict(int)

    for ev in events:
        sort_key, day_lbl, time_str = _parse_day_bucket(ev.get('detected_at', ''))
        ev_type = ev.get('event_type')
        if ev_type == 'baru':
            ev_type = 'new'
        by_day[sort_key][ev_type].append((ev, time_str))
        day_labels[sort_key] = day_lbl
        total_events[ev_type] += 1

    is_specific = filter_type is not None
    title_map = {
        'restock': ('✅', 'RESTOCK'),
        'new': ('🆕', 'PRODUK BARU'),
        'habis': ('❌', 'JADI HABIS'),
        'price_change': ('💰', 'PERUBAHAN HARGA'),
    }

    if specific_date:
        try:
            date_str = datetime.strptime(specific_date, "%Y-%m-%d").strftime("%d %b %Y")
        except Exception:
            date_str = specific_date
        reply = f"📋 <b>LAPORAN PERUBAHAN ({date_str})</b>\n"
    else:
        reply = f"📋 <b>LAPORAN PERUBAHAN ({days} HARI)</b>\n"

    reply += "━━━━━━━━━━━━━━━━━━━━━\n\n"

    for sort_key in sorted(by_day.keys(), reverse=True):
        day = day_labels[sort_key]
        day_data = by_day[sort_key]

        if is_specific:
            items = day_data.get(filter_type, [])
            if not items:
                continue
            icon, label_title = title_map.get(filter_type, ('📌', filter_type.upper()))
            reply += f"📅 <b>{day} — {icon} {label_title} ({len(items)})</b>\n"

            for idx, (ev, time_str) in enumerate(items):
                if not show_all and idx >= 30:
                    reply += f"  <i>... dan {len(items) - 30} lainnya.</i>\n"
                    break
                time_label = f" ({time_str})" if time_str else ""
                clean_nm = _clean_product_name(ev['nama'])
                if filter_type == 'habis':
                    reply += f"  {idx+1}. {clean_nm}{time_label}\n     {ev.get('stock','-')} ➡️ Habis | {ev.get('harga','-')}\n"
                elif filter_type == 'restock':
                    reply += f"  {count_or_idx(idx)}. {clean_nm}{time_label}\n     Habis ➡️ {ev.get('stock','-')} | {ev.get('harga','-')}\n"
                elif filter_type == 'price_change':
                    reply += f"  {idx+1}. {clean_nm}{time_label}\n     {ev.get('stock','-')} ➡️ {ev.get('harga','-')}\n"
                elif filter_type == 'new':
                    reply += f"  {idx+1}. {clean_nm}{time_label}\n     Stok: {ev.get('stock','-')} | {ev.get('harga','-')}\n"
            reply += "\n"
        else:
            reply += f"📅 <b>{day}</b>\n"
            day_summary = []
            for ev_t, (icon, _) in title_map.items():
                cnt = len(day_data.get(ev_t, []))
                lbl = {'restock': 'restock', 'new': 'baru', 'habis': 'habis', 'price_change': 'harga'}[ev_t]
                day_summary.append(f"{icon} {cnt} {lbl}")
            reply += "  " + " | ".join(day_summary) + "\n\n"

            if days == 1 or specific_date:
                for ev_t, (icon, title) in title_map.items():
                    items = day_data.get(ev_t, [])
                    if not items:
                        continue
                    reply += f"{icon} <b>{title} ({len(items)}):</b>\n"
                    for idx, (ev, time_str) in enumerate(items):
                        if not show_all and idx >= 10:
                            reply += f"  <i>... dan {len(items) - 10} lainnya.</i>\n"
                            break
                        time_label = f" ({time_str})" if time_str else ""
                        clean_nm = _clean_product_name(ev['nama'])
                        if ev_t == 'habis':
                            reply += f"  {idx+1}. {clean_nm}{time_label} — {ev.get('harga','-')}\n"
                        elif ev_t == 'restock':
                            reply += f"  {idx+1}. {clean_nm}{time_label} ➡️ {ev.get('stock','-')} ({ev.get('harga','-')})\n"
                        elif ev_t == 'price_change':
                            reply += f"  {idx+1}. {clean_nm}{time_label}: {ev.get('stock','-')} ➡️ {ev.get('harga','-')}\n"
                        elif ev_t == 'new':
                            reply += f"  {idx+1}. {clean_nm}{time_label} — {ev.get('stock','-')} ({ev.get('harga','-')})\n"
                    reply += "\n"

    reply += "━━━━━━━━━━━━━━━━━━━━━\n"
    if is_specific:
        reply += f"📊 Total: <b>{len(events)} varian</b>\n"
    else:
        total_summary = []
        if total_events['restock']: total_summary.append(f"+{total_events['restock']} restock")
        if total_events['new']: total_summary.append(f"+{total_events['new']} baru")
        if total_events['habis']: total_summary.append(f"-{total_events['habis']} habis")
        if total_events['price_change']: total_summary.append(f"{total_events['price_change']} harga")
        reply += f"📊 Ringkasan: " + (", ".join(total_summary) if total_summary else "0 perubahan")

    _send_long_message(bot, chat_id, reply, reply_to=reply_to, reply_markup=kb)


def count_or_idx(idx):
    return idx + 1


def register_handlers(bot):
    _broadcast_pending = {}  # chat_id -> pesan broadcast admin

    # =====================
    # /start dan /help
    # =====================
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        chat_id = message.chat.id
        cmd = message.text.split()[0].lower()

        args = message.text.split()
        ref_id = None
        if len(args) > 1 and args[1].startswith('ref_'):
            ref_id = args[1].replace('ref_', '')

        settings = database.get_user_settings(chat_id)

        if ref_id and str(ref_id) != str(chat_id) and not settings.get('referred_by'):
            database.set_referred_by(chat_id, ref_id)
            settings['referred_by'] = ref_id

        is_active, valid_until = _is_user_active(settings)

        if is_active:
            sisa_info = (
                "Unlimited (Admin)"
                if settings.get('role') == 'admin'
                else f"{max(0, (valid_until - datetime.now(timezone.utc)).days)} hari lagi"
            )
            help_text = (
                "🤖 <b>VENTEDAILY STOCK MONITOR</b>\n"
                f"🟢 Status Akun: <b>Aktif ({sisa_info})</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✨ <b>CARA PAKAI PALING CEPAT:</b>\n"
                "Langsung ketik <b>nama produk</b> di chat tanpa garis miring!\n"
                "Contoh: ketik <code>sasmita</code> atau <code>abimaya</code>\n\n"
                "📦 <b>STOK & KATALOG:</b>\n"
                "• /cari <i>[nama]</i> — Cek stok per warna & ukuran\n"
                "• /katalog <i>[nama]</i> — Buat teks katalog siap kirim ke WA pembeli\n"
                "• /baru & /restock — Daftar produk baru & restock\n\n"
                "🧮 <b>HARGA & PENGATURAN:</b>\n"
                "• /hitung <i>[nama] [profit]</i> — Hitung harga jual semua marketplace\n"
                "• /profil — Atur Marketplace, Profit default & Link Referral\n\n"
                "📊 <b>LAPORAN & LANGGANAN:</b>\n"
                "• /perubahan — Riwayat perubahan stok harian\n"
                "• /laporan — Ringkasan mingguan & produk terlaris\n"
                "• /langganan — Perpanjang masa aktif (QRIS)\n"
                "• /status — Cek koneksi & update data terakhir\n"
            )
            if settings.get('role') == 'admin':
                help_text += (
                    "\n🛠 <b>MENU ADMIN:</b>\n"
                    "• /statistik — Ringkasan user & aktivitas stok\n"
                    "• /broadcast <i>[pesan]</i> — Kirim pengumuman/promo\n"
                    "• /payments — Verifikasi pembayaran QRIS\n"
                    "• /paketadmin — Kelola harga & paket langganan\n"
                    "• /users | /check <i>[id]</i> | /adduser | /extend | /suspend\n"
                )
            bot.reply_to(message, help_text, parse_mode="HTML", reply_markup=get_main_keyboard(True))
        else:
            # Ambil harga paket termurah secara dinamis jika tersedia
            promo_line = "Mulai dari <b>Rp 800-an / hari</b> untuk menghemat waktumu!"
            try:
                import payments as _pay
                plans = _pay.get_active_plans()
                if plans:
                    cheapest = min(plans, key=lambda p: int(p.get('price', 9999999)))
                    promo_line = (
                        f"Paket mulai <b>{_pay._format_rupiah(int(cheapest['price']))}</b> "
                        f"({cheapest['duration_days']} hari)"
                    )
            except Exception:
                pass

            can_trial = _can_use_trial(settings)
            kb = types.InlineKeyboardMarkup(row_width=1)
            if can_trial:
                kb.add(types.InlineKeyboardButton("🎁 Aktifkan Free Trial 5 Hari (Gratis!)", callback_data="act_trial"))
            kb.add(types.InlineKeyboardButton("💎 Lihat Paket Langganan (QRIS)", callback_data="open_plans"))
            kb.add(types.InlineKeyboardButton("💬 Tanya Admin (@fianfi)", url="https://t.me/fianfi"))

            trial_section = (
                "\n🎁 <b>COBA GRATIS 5 HARI!</b>\n"
                "Klik tombol <b>Aktifkan Free Trial</b> di bawah untuk langsung mencoba semua fitur secara gratis.\n"
                if can_trial else ""
            )

            welcome_msg = (
                "👋 <b>Selamat datang di Ventedaily Monitor Bot!</b>\n\n"
                "Asisten otomatis andalan reseller Ventedaily:\n"
                "✅ <b>Cek Stok Super Cepat:</b> Tinggal ketik nama produk, stok per warna & ukuran langsung tampil rapi.\n"
                "✅ <b>Katalog WA 1-Klik:</b> Buat teks katalog bersih siap kirim ke pembeli otomatis dengan harga jualmu.\n"
                "✅ <b>Notifikasi Real-Time 24/7:</b> Info instan begitu ada Restock & Produk Baru.\n"
                "✅ <b>Kalkulator Marketplace:</b> Hitung otomatis harga jual + potongan admin Shopee, TikTok, Tokopedia, Lazada.\n\n"
                f"💳 {promo_line}\n"
                f"{trial_section}\n"
                f"🆔 ID Telegram kamu: <code>{chat_id}</code>"
            )
            bot.reply_to(message, welcome_msg, parse_mode="HTML", reply_markup=kb)
            if cmd == '/start':
                bot.send_message(
                    chat_id,
                    "👇 Gunakan tombol menu di bawah layar untuk navigasi cepat:",
                    reply_markup=get_main_keyboard(False)
                )

    # =====================
    # /trial
    # =====================
    @bot.message_handler(commands=['trial'])
    def handle_trial(message):
        _activate_trial_for_user(bot, message.chat.id, reply_to=message)

    # =====================
    # /status
    # =====================
    @bot.message_handler(commands=['status', 'cek'])
    def handle_status(message):
        data = database.load_catalog_snapshot()
        if data:
            total_items = len(data)
            aman_c = sum(1 for v in data.values() if v.get('stock', '').lower() == 'aman')
            ready_c = sum(1 for v in data.values() if v.get('stock', '').lower() == 'ready')
            habis_c = sum(1 for v in data.values() if v.get('stock', '').lower() == 'habis')

            upd_str = database.get_snapshot_updated_at()
            if upd_str:
                try:
                    updated = datetime.fromisoformat(upd_str.replace('Z', '+00:00')).astimezone(WIB)
                    last_check = updated.strftime('%d %b %Y, %H:%M:%S WIB')
                except Exception:
                    last_check = "baru saja"
            else:
                last_check = "tidak diketahui"

            reply = (
                f"🟢 <b>Server Berjalan Normal!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Total Varian Dipantau: <b>{total_items}</b>\n"
                f"   • ✅ Aman: <b>{aman_c}</b>\n"
                f"   • ⚠️ Ready: <b>{ready_c}</b>\n"
                f"   • ❌ Habis: <b>{habis_c}</b>\n\n"
                f"⏱️ Update Terakhir: <b>{last_check}</b>\n"
                f"🔄 Interval Cek: Setiap <b>{config.CHECK_INTERVAL} menit</b>"
            )
        else:
            reply = "🟡 Server berjalan, sedang mengambil data produk pertama kali..."
        bot.reply_to(message, reply, parse_mode="HTML")

    # =====================
    # /cari [nama]
    # =====================
    @bot.message_handler(commands=['cari'])
    def handle_cari(message):
        if not _check_subscription(bot, message):
            return
        query = re.sub(r'^/cari(@\S+)?', '', message.text, flags=re.IGNORECASE).strip()
        _run_search(bot, message.chat.id, query, reply_to=message)

    # =====================
    # /baru [hari]
    # =====================
    @bot.message_handler(commands=['baru'])
    def handle_baru(message):
        if not _check_subscription(bot, message):
            return
        args = message.text.split()
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        _render_events_list(bot, message.chat.id, 'new', days, "🆕", "PRODUK BARU", "produk baru", reply_to=message)

    # =====================
    # /restock [hari]
    # =====================
    @bot.message_handler(commands=['restock'])
    def handle_restock(message):
        if not _check_subscription(bot, message):
            return
        args = message.text.split()
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        _render_events_list(bot, message.chat.id, 'restock', days, "🔄", "PRODUK RESTOCK", "produk restock", reply_to=message)

    # =====================
    # /setmp [marketplace] [fee%]
    # =====================
    @bot.message_handler(commands=['setmp'])
    def handle_setmp(message):
        if not _check_subscription(bot, message):
            return
        chat_id = message.chat.id
        args = message.text.split()
        if len(args) < 2:
            current = database.get_user_settings(chat_id)
            mp = current.get('marketplace', 'shopee')
            fee = current.get('admin_fee', 6.5)
            mp_info = calculator.MARKETPLACES.get(mp, calculator.MARKETPLACES['shopee'])
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🟠 Shopee", callback_data="mp_shopee"),
                types.InlineKeyboardButton("🟢 Tokopedia", callback_data="mp_tokopedia"),
                types.InlineKeyboardButton("⚫ TikTok Shop", callback_data="mp_tiktok"),
                types.InlineKeyboardButton("🔵 Lazada", callback_data="mp_lazada"),
            )
            reply = (
                f"🏪 <b>SETTING MARKETPLACE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Marketplace aktif: <b>{mp_info['name']}</b>\n"
                f"Potongan Admin: <b>{fee}%</b>\n\n"
                f"👇 Klik tombol di bawah untuk mengubah:"
            )
            bot.reply_to(message, reply, parse_mode="HTML", reply_markup=kb)
            return

        mp_key = args[1].lower()
        if mp_key in calculator.MARKETPLACES:
            default_fee = calculator.MARKETPLACES[mp_key]['fee']
            if len(args) >= 3:
                try:
                    fee = float(args[2].replace('%', '').replace(',', '.'))
                except ValueError:
                    fee = default_fee
            else:
                fee = default_fee

            database.set_user_marketplace(chat_id, mp_key, fee)
            mp_name = calculator.MARKETPLACES[mp_key]['name']
            bot.reply_to(message, f"✅ Marketplace diubah ke <b>{mp_name}</b> (Admin fee: <b>{fee}%</b>)", parse_mode="HTML")
        else:
            bot.reply_to(message, "❌ Marketplace tidak dikenal. Pilihan: shopee, tokopedia, tiktok, lazada")

    # =====================
    # /setmarkup [nilai]
    # =====================
    @bot.message_handler(commands=['setmarkup'])
    def handle_setmarkup(message):
        if not _check_subscription(bot, message):
            return
        chat_id = message.chat.id
        args = message.text.split()
        if len(args) < 2:
            current = database.get_user_settings(chat_id)
            mt = current.get('markup_type', 'percent')
            mv = current.get('markup_value', 0)
            label = f"{mv:g}%" if mt == 'percent' else f"Rp {int(mv):,}".replace(",", ".")
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💰 Ubah Target Profit", callback_data="set_profit"))
            reply = (
                f"💰 <b>SETTING TARGET PROFIT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Target Profit saat ini: <b>{label}</b>\n\n"
                f"Klik tombol di bawah atau ketik:\n"
                f"• <code>/setmarkup 20%</code> (untung 20%)\n"
                f"• <code>/setmarkup 30000</code> (untung Rp 30.000)"
            )
            bot.reply_to(message, reply, parse_mode="HTML", reply_markup=kb)
            return

        _save_profit_setting(bot, message, " ".join(args[1:]))

    # =====================
    # /hitung [nama] [profit]
    # =====================
    @bot.message_handler(commands=['hitung'])
    def handle_hitung(message):
        if not _check_subscription(bot, message):
            return
        raw_args = re.sub(r'^/hitung(@\S+)?', '', message.text, flags=re.IGNORECASE).strip()
        _run_hitung(bot, message.chat.id, raw_args, reply_to=message)

    # =====================
    # /katalog [nama]
    # =====================
    @bot.message_handler(commands=['katalog'])
    def handle_katalog(message):
        if not _check_subscription(bot, message):
            return
        query = re.sub(r'^/katalog(@\S+)?', '', message.text, flags=re.IGNORECASE).strip()
        _run_katalog(bot, message.chat.id, query, reply_to=message)

    # =====================
    # /perubahan [filter] [hari] [semua]
    # =====================
    @bot.message_handler(commands=['perubahan'])
    def handle_perubahan(message):
        if not _check_subscription(bot, message):
            return
        args = message.text.lower().split()

        filter_type = None
        days = 1
        show_all = False
        specific_date = None

        for arg in args[1:]:
            if arg in ['habis', 'restock', 'harga', 'baru', 'new']:
                filter_type = {'harga': 'price_change', 'baru': 'new'}.get(arg, arg)
            elif arg.isdigit():
                days = int(arg)
            elif arg == 'semua':
                show_all = True
            elif '-' in arg or '/' in arg:
                try:
                    clean_arg = arg.replace('/', '-')
                    parts = clean_arg.split('-')
                    if len(parts) == 3:
                        if len(parts[0]) == 4:
                            specific_date = clean_arg
                        else:
                            specific_date = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
                except Exception:
                    pass

        if specific_date:
            try:
                specific_date = datetime.strptime(specific_date, "%Y-%m-%d").date().isoformat()
            except ValueError:
                bot.reply_to(message, "❌ Tanggal tidak valid. Contoh: /perubahan 19-11-2026.")
                return
        _render_perubahan(
            bot, message.chat.id,
            filter_type=filter_type, days=days,
            show_all=show_all, specific_date=specific_date,
            reply_to=message
        )

    # =====================
    # /laporan — Laporan mingguan
    # =====================
    @bot.message_handler(commands=['laporan'])
    def handle_laporan(message):
        if not _check_subscription(bot, message):
            return
        _generate_report(bot, message.chat.id, reply_to=message)

    # =====================
    # /profil & /pengaturan
    # =====================
    @bot.message_handler(commands=['profil', 'pengaturan'])
    def handle_profil(message):
        chat_id = message.chat.id
        settings = database.get_user_settings(chat_id)
        is_active, valid_until = _is_user_active(settings)

        if is_active:
            status = "🟢 <b>Aktif</b>"
            if settings.get('role') == 'admin':
                sisa = "Unlimited (Admin)"
            else:
                rem = valid_until - datetime.now(timezone.utc)
                until_lbl = valid_until.astimezone(WIB).strftime('%d %b %Y')
                sisa = f"{max(0, rem.days)} Hari (s/d {until_lbl})"
        else:
            status = "🔴 <b>Expired / Belum Aktif</b>"
            sisa = "0 Hari"

        mv = float(settings.get('markup_value') or 0)
        if settings.get('markup_type') == 'percent':
            markup = f"{mv:g}%" if mv > 0 else "Belum diatur (0%)"
        else:
            markup = f"Rp {int(mv):,}".replace(",", ".") if mv > 0 else "Belum diatur (Rp 0)"

        mp_key = settings.get('marketplace', 'shopee')
        mp_name = calculator.MARKETPLACES.get(mp_key, {'name': mp_key.capitalize()})['name']
        admin_fee = settings.get('admin_fee', 6.5)

        try:
            bot_info = bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start=ref_{chat_id}"
        except Exception:
            ref_link = f"ref_{chat_id}"

        share_url = f"https://t.me/share/url?url={ref_link}&text=Yuk%20pakai%20Ventedaily%20Stock%20Monitor%20biar%20nggak%20ketinggalan%20restock!"

        reply = (
            f"👤 <b>PROFIL & PENGATURAN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID Telegram: <code>{chat_id}</code>\n"
            f"📊 Status: {status}\n"
            f"⏳ Masa Aktif: <b>{sisa}</b>\n\n"
            f"⚙️ <b>PENGATURAN KALKULATOR & KATALOG:</b>\n"
            f"🛒 Marketplace: <b>{mp_name}</b> (Admin {admin_fee}%)\n"
            f"💰 Target Profit: <b>{markup}</b>\n\n"
            f"🤝 <b>PROGRAM REFERRAL (+{config.REFERRAL_BONUS_DAYS} HARI GRATIS):</b>\n"
            f"Ajak teman reseller pakai link di bawah. Begitu mereka langganan, masa aktifmu otomatis <b>+{config.REFERRAL_BONUS_DAYS} Hari!</b>\n"
            f"🔗 <code>{ref_link}</code>\n"
            f"👥 Teman Bergabung: <b>{settings.get('referral_count') or 0} orang</b>"
        )

        markup_kb = types.InlineKeyboardMarkup(row_width=2)
        markup_kb.add(
            types.InlineKeyboardButton("🛒 Ubah Marketplace", callback_data="set_mp"),
            types.InlineKeyboardButton("💰 Ubah Target Profit", callback_data="set_profit"),
        )
        markup_kb.row(
            types.InlineKeyboardButton("📤 Bagikan Link Referral", url=share_url),
        )
        if settings.get('role') != 'admin':
            markup_kb.row(
                types.InlineKeyboardButton("💎 Perpanjang / Pilih Paket", callback_data="open_plans"),
            )

        bot.reply_to(message, reply, parse_mode="HTML", reply_markup=markup_kb)

    # =====================
    # ADMIN COMMANDS
    # =====================
    @bot.message_handler(commands=['adduser'])
    def handle_adduser(message):
        if not _check_admin(bot, message):
            return
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, "⚠️ Format: /adduser [chat_id] [hari]\nContoh: /adduser 12345 30")
            return
        target_id = args[1]
        try:
            days = int(args[2])
            valid_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            if database.update_subscription(target_id, 'pro', valid_until, ""):
                bot.reply_to(message, f"✅ User {target_id} berhasil diaktifkan selama {days} hari.")
                try:
                    bot.send_message(
                        target_id,
                        f"🎉 <b>Langganan Aktif!</b>\n\n"
                        f"Masa aktif kamu telah ditambahkan selama <b>{days} hari</b>.\n"
                        f"Langsung ketik nama produk di bawah untuk mulai mencari stok!",
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard(True)
                    )
                except Exception:
                    pass

                try:
                    inviter = database.grant_referral(target_id)
                    if inviter:
                        bot.send_message(
                            inviter,
                            f"🎁 <b>BONUS REFERRAL!</b>\n\nTeman yang kamu undang telah berlangganan. "
                            f"Kamu mendapat bonus <b>+{config.REFERRAL_BONUS_DAYS} hari</b>.",
                            parse_mode="HTML",
                        )
                except Exception as exc:
                    print(f"Referral error for {target_id}: {exc}")
            else:
                bot.reply_to(message, "❌ Gagal update database.")
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}")

    @bot.message_handler(commands=['extend'])
    def handle_extend(message):
        if not _check_admin(bot, message):
            return
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, "⚠️ Format: /extend [chat_id] [hari]")
            return
        target_id = args[1]
        try:
            days = int(args[2])
            settings = database.get_user_settings(target_id)
            _, current_valid = _is_user_active(settings)
            if current_valid < datetime.now(timezone.utc):
                current_valid = datetime.now(timezone.utc)

            new_valid = (current_valid + timedelta(days=days)).isoformat()
            plan = settings.get('plan_type') if settings.get('plan_type') not in ('', 'none', 'suspended') else 'pro'
            if database.update_subscription(target_id, plan, new_valid, ""):
                bot.reply_to(message, f"✅ Masa aktif user {target_id} diperpanjang {days} hari.")
                try:
                    bot.send_message(
                        target_id,
                        f"🎉 <b>Perpanjangan Berhasil!</b>\n\nMasa aktif kamu telah ditambah <b>{days} hari</b>.",
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard(True)
                    )
                except Exception:
                    pass
            else:
                bot.reply_to(message, "❌ Gagal update database.")
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}")

    @bot.message_handler(commands=['suspend'])
    def handle_suspend(message):
        if not _check_admin(bot, message):
            return
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Format: /suspend [chat_id]")
            return
        target_id = args[1]
        # Gunakan plan_type 'suspended' agar tidak bisa mengklaim ulang /trial
        if database.update_subscription(target_id, 'suspended', '2000-01-01T00:00:00Z', ""):
            bot.reply_to(message, f"✅ User {target_id} berhasil di-suspend.")
            try:
                bot.send_message(
                    target_id,
                    "⚠️ Masa aktif akun kamu telah dinonaktifkan oleh Admin.",
                    reply_markup=get_main_keyboard(False)
                )
            except Exception:
                pass
        else:
            bot.reply_to(message, "❌ Gagal update database.")

    @bot.message_handler(commands=['check'])
    def handle_check(message):
        if not _check_admin(bot, message):
            return
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Format: /check [chat_id]")
            return
        target_id = args[1]
        settings = database.get_user_settings(target_id)
        is_active, valid_until = _is_user_active(settings)
        if is_active:
            sisa = "Unlimited" if settings.get('role') == 'admin' else f"{(valid_until - datetime.now(timezone.utc)).days} hari"
            status = "🟢 Aktif"
        else:
            sisa = "0 hari (Expired)"
            status = "🔴 Expired"
        valid_until_str = settings.get('valid_until', '2000-01-01T00:00:00Z')
        mp = settings.get('marketplace', 'shopee').capitalize()
        fee = settings.get('admin_fee', '-')
        ref_count = settings.get('referral_count') or 0
        bot.reply_to(
            message,
            f"🔍 <b>Info User</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Chat ID: <code>{target_id}</code>\n"
            f"📋 Plan: {settings.get('plan_type', '-')}\n"
            f"📊 Status: {status}\n"
            f"⏳ Sisa: <b>{sisa}</b>\n"
            f"📅 Berlaku s/d: {valid_until_str[:10]}\n"
            f"🛒 Marketplace: {mp} ({fee}%)\n"
            f"👥 Referral: {ref_count} orang\n"
            f"🔗 Diajak oleh: {settings.get('referred_by') or '-'}",
            parse_mode="HTML"
        )

    @bot.message_handler(commands=['users'])
    def handle_users(message):
        if not _check_admin(bot, message):
            return
        users = database.get_all_users()
        if not users:
            bot.reply_to(message, "Tidak ada user.")
            return

        reply = "👥 <b>DAFTAR USER</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        active = 0
        for u in users:
            ok, v = _is_user_active(u)
            status = "🟢" if ok else "🔴"
            if ok:
                active += 1
            sisa_lbl = "admin" if u.get('role') == 'admin' else (f"{(v - datetime.now(timezone.utc)).days}h" if ok else "exp")
            reply += f"{status} <code>{u.get('chat_id')}</code> ({u.get('plan_type', 'none')} | {sisa_lbl})\n"

        reply += f"\nTotal: <b>{len(users)}</b> | Aktif: <b>{active}</b> | Nonaktif: <b>{len(users) - active}</b>"
        _send_long_message(bot, message.chat.id, reply, reply_to=message)

    @bot.message_handler(commands=['statistik'])
    def handle_statistik(message):
        if not _check_admin(bot, message):
            return
        now_wib = datetime.now(WIB).strftime('%d %B %Y, %H:%M WIB')

        stats = database.get_user_stats()
        if not stats:
            bot.reply_to(message, "❌ Gagal mengambil data statistik.")
            return

        events_today = database.get_events(days=1)
        restock_today = sum(1 for e in events_today if e.get('event_type') == 'restock')
        habis_today = sum(1 for e in events_today if e.get('event_type') == 'habis')
        baru_today = sum(1 for e in events_today if e.get('event_type') in ('new', 'baru'))
        harga_today = sum(1 for e in events_today if e.get('event_type') == 'price_change')

        events_week = database.get_events(days=7)

        reply = (
            f"📊 <b>STATISTIK BOT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now_wib}\n\n"
            f"👥 <b>Pengguna</b>\n"
            f"• Total terdaftar: <b>{stats['total']}</b>\n"
            f"• Aktif berbayar: <b>{stats['aktif']}</b>\n"
            f"• Trial aktif: <b>{stats['trial']}</b>\n"
            f"• Expired / belum aktif: <b>{stats['expired']}</b>\n\n"
            f"📦 <b>Aktivitas Stok (24 jam)</b>\n"
            f"• Restock: <b>{restock_today}</b> varian\n"
            f"• Produk baru: <b>{baru_today}</b> varian\n"
            f"• Jadi habis: <b>{habis_today}</b> varian\n"
            f"• Perubahan harga: <b>{harga_today}</b> varian\n"
            f"• Total perubahan hari ini: <b>{len(events_today)}</b>\n\n"
            f"📈 <b>7 Hari Terakhir</b>\n"
            f"• Total perubahan: <b>{len(events_week)}</b>\n"
        )
        bot.reply_to(message, reply, parse_mode="HTML")

    # =====================
    # /broadcast (Aktif / Expired Promo / Semua)
    # =====================
    @bot.message_handler(commands=['broadcast'])
    def handle_broadcast(message):
        if not _check_admin(bot, message):
            return

        args = message.text.split(None, 1)
        if len(args) < 2 or not args[1].strip():
            bot.reply_to(
                message,
                "📢 <b>BROADCAST PENGUMUMAN / PROMO</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Format:\n"
                "<code>/broadcast [isi pesan]</code>\n\n"
                "Setelah itu kamu bisa memilih target penerima:\n"
                "• 🟢 User Aktif saja\n"
                "• 🔴 User Expired / Bekas Trial (cocok untuk info diskon perpanjangan!)\n"
                "• 👥 Semua User",
                parse_mode="HTML"
            )
            return

        pesan = args[1].strip()
        active_users = database.get_active_users()
        expired_users = database.get_expired_users()
        all_users = database.get_all_users()

        _broadcast_pending[message.chat.id] = pesan

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"🟢 Kirim ke User Aktif ({len(active_users)})", callback_data="bcast:active"),
            types.InlineKeyboardButton(f"🔴 Kirim ke User Expired/Nonaktif ({len(expired_users)})", callback_data="bcast:expired"),
            types.InlineKeyboardButton(f"👥 Kirim ke Semua User ({len(all_users)})", callback_data="bcast:all"),
            types.InlineKeyboardButton("❌ Batalkan", callback_data="bcast:cancel"),
        )

        bot.reply_to(
            message,
            f"📢 <b>KONFIRMASI BROADCAST</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Preview pesan:\n\n"
            f"{pesan}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 Pilih target penerima di bawah:",
            parse_mode="HTML",
            reply_markup=kb
        )

    def _execute_broadcast(chat_id, target_mode, pesan):
        if target_mode == 'active':
            targets = database.get_active_users()
            label = "User Aktif"
            header = "📢 <b>INFO DARI ADMIN</b>"
            extra_kb = None
        elif target_mode == 'expired':
            targets = database.get_expired_users()
            label = "User Expired/Nonaktif"
            header = "🎁 <b>INFO SPESIAL VENTEDAILY MONITOR</b>"
            extra_kb = types.InlineKeyboardMarkup()
            extra_kb.add(types.InlineKeyboardButton("💎 Lihat Paket Langganan", callback_data="open_plans"))
        else:
            targets = database.get_all_users()
            label = "Semua User"
            header = "📢 <b>PENGUMUMAN ADMIN</b>"
            extra_kb = None

        if not targets:
            bot.send_message(chat_id, f"⚠️ Tidak ada user pada kategori <b>{label}</b>.", parse_mode="HTML")
            return

        bot.send_message(chat_id, f"📤 Mengirim pesan ke <b>{len(targets)} {label}</b>...", parse_mode="HTML")
        berhasil = 0
        gagal = 0
        for u in targets:
            try:
                bot.send_message(
                    u['chat_id'],
                    f"{header}\n\n{pesan}",
                    parse_mode="HTML",
                    reply_markup=extra_kb
                )
                berhasil += 1
                time.sleep(0.05)
            except Exception as e:
                gagal += 1
                print(f"Broadcast gagal ke {u.get('chat_id')}: {e}")

        bot.send_message(
            chat_id,
            f"✅ <b>Broadcast Selesai ({label})</b>\n"
            f"• Berhasil: <b>{berhasil}</b>\n"
            f"• Gagal/Blokir: <b>{gagal}</b>",
            parse_mode="HTML"
        )

    @bot.message_handler(commands=['konfirmbroadcast'])
    def handle_konfirm_broadcast(message):
        if not _check_admin(bot, message):
            return
        pesan = _broadcast_pending.pop(message.chat.id, None)
        if not pesan:
            bot.reply_to(message, "⚠️ Tidak ada broadcast yang pending. Gunakan /broadcast [pesan] dulu.")
            return
        _execute_broadcast(message.chat.id, 'active', pesan)

    # =====================
    # INLINE CALLBACK HANDLERS
    # =====================
    @bot.callback_query_handler(func=lambda call: call.data == 'act_trial')
    def callback_act_trial(call):
        bot.answer_callback_query(call.id)
        _activate_trial_for_user(bot, call.message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('qkatalog:'))
    def callback_quick_katalog(call):
        bot.answer_callback_query(call.id, "Membuat katalog...")
        settings = database.get_user_settings(call.message.chat.id)
        ok, _ = _is_user_active(settings)
        if not ok:
            bot.send_message(call.message.chat.id, "⚠️ Masa aktif langganan sudah habis. Ketik /langganan.")
            return
        query = call.data.split(':', 1)[1]
        _run_katalog(bot, call.message.chat.id, query)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('qhitung:'))
    def callback_quick_hitung(call):
        bot.answer_callback_query(call.id, "Menghitung harga jual...")
        settings = database.get_user_settings(call.message.chat.id)
        ok, _ = _is_user_active(settings)
        if not ok:
            bot.send_message(call.message.chat.id, "⚠️ Masa aktif langganan sudah habis. Ketik /langganan.")
            return
        query = call.data.split(':', 1)[1]
        _run_hitung(bot, call.message.chat.id, query)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ev_baru:') or call.data.startswith('ev_restock:'))
    def callback_events_range(call):
        bot.answer_callback_query(call.id)
        settings = database.get_user_settings(call.message.chat.id)
        ok, _ = _is_user_active(settings)
        if not ok:
            return
        kind, days_s = call.data.split(':', 1)
        days = int(days_s) if days_s.isdigit() else 1
        if kind == 'ev_baru':
            _render_events_list(bot, call.message.chat.id, 'new', days, "🆕", "PRODUK BARU", "produk baru")
        else:
            _render_events_list(bot, call.message.chat.id, 'restock', days, "🔄", "PRODUK RESTOCK", "produk restock")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('perub:'))
    def callback_perubahan_filter(call):
        bot.answer_callback_query(call.id)
        settings = database.get_user_settings(call.message.chat.id)
        ok, _ = _is_user_active(settings)
        if not ok:
            return
        _, mode, days_s = call.data.split(':', 2)
        days = int(days_s) if days_s.isdigit() else 1
        if mode == 'mingguan':
            _generate_report(bot, call.message.chat.id)
        elif mode == 'all':
            _render_perubahan(bot, call.message.chat.id, filter_type=None, days=days)
        else:
            _render_perubahan(bot, call.message.chat.id, filter_type=mode, days=days)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('bcast:'))
    def callback_broadcast_action(call):
        settings = database.get_user_settings(call.from_user.id)
        if settings.get('role') != 'admin':
            bot.answer_callback_query(call.id, "Akses ditolak.", show_alert=True)
            return
        mode = call.data.split(':', 1)[1]
        pesan = _broadcast_pending.pop(call.message.chat.id, None)
        if mode == 'cancel':
            bot.answer_callback_query(call.id, "Broadcast dibatalkan.")
            try:
                bot.edit_message_text("❌ Broadcast dibatalkan.", call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            return
        if not pesan:
            bot.answer_callback_query(call.id, "Broadcast sudah kadaluarsa / diproses.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Mengirim broadcast...")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        _execute_broadcast(call.message.chat.id, mode, pesan)

    @bot.callback_query_handler(func=lambda call: call.data in ['set_mp', 'set_profit'])
    def handle_settings_callback(call):
        chat_id = call.message.chat.id
        bot.answer_callback_query(call.id)

        if call.data == 'set_mp':
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🟠 Shopee", callback_data="mp_shopee"),
                types.InlineKeyboardButton("🟢 Tokopedia", callback_data="mp_tokopedia"),
                types.InlineKeyboardButton("⚫ TikTok Shop", callback_data="mp_tiktok"),
                types.InlineKeyboardButton("🔵 Lazada", callback_data="mp_lazada"),
            )
            bot.send_message(chat_id, "🏪 Pilih <b>Marketplace Default</b> toko kamu:", parse_mode="HTML", reply_markup=kb)

        elif call.data == 'set_profit':
            msg = bot.send_message(
                chat_id,
                "💰 Ketik nilai <b>Target Profit</b> baru kamu:\n"
                "• Pakai persen, contoh: <code>20%</code>\n"
                "• Atau pakai rupiah, contoh: <code>30000</code>\n\n"
                "<i>(Ketik 'batal' untuk membatalkan)</i>",
                parse_mode="HTML"
            )
            bot.register_next_step_handler(msg, process_profit_step)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('mp_'))
    def handle_mp_selection(call):
        chat_id = call.message.chat.id
        mp = call.data.split('_', 1)[1]
        default_fee = calculator.MARKETPLACES.get(mp, {'fee': 6.5})['fee']
        mp_name = calculator.MARKETPLACES.get(mp, {'name': mp.capitalize()})['name']
        bot.answer_callback_query(call.id)

        msg = bot.send_message(
            chat_id,
            f"🏪 Berapa % potongan Admin Fee di toko <b>{mp_name}</b> kamu?\n"
            f"• Ketik angka persen, contoh: <code>{default_fee}</code> atau <code>8.5</code>\n"
            f"• Atau ketik <code>default</code> untuk memakai standar ({default_fee}%)\n\n"
            f"<i>(Ketik 'batal' untuk membatalkan)</i>",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_mp_fee_step, mp)

    # =====================
    # STEP HANDLERS (Aman jika user klik tombol menu lain di tengah jalan)
    # =====================
    def _intercept_menu_or_cancel(message):
        if not message.text:
            bot.reply_to(message, "✅ Aksi dibatalkan.")
            return True
        txt = message.text.strip()
        if txt.lower() in ['batal', 'cancel', '❌ batal']:
            bot.reply_to(message, "✅ Aksi dibatalkan.")
            return True
        if txt in MENU_BUTTON_TEXTS or txt.startswith('/'):
            # Jika user malah menekan tombol menu atau command lain, batalkan step dan jalankan tombolnya
            if txt in MENU_BUTTON_TEXTS:
                handle_menu_buttons(message)
            else:
                bot.reply_to(message, "ℹ️ Input sebelumnya dibatalkan. Silakan ulangi perintah yang kamu inginkan.")
            return True
        return False

    def process_mp_fee_step(message, mp):
        if _intercept_menu_or_cancel(message):
            return
        chat_id = message.chat.id
        text = message.text.strip().lower().replace(',', '.')
        default_fee = calculator.MARKETPLACES.get(mp, {'fee': 6.5})['fee']
        mp_name = calculator.MARKETPLACES.get(mp, {'name': mp.capitalize()})['name']

        if text == 'default':
            fee = default_fee
        else:
            try:
                fee = float(text.replace('%', ''))
            except Exception:
                bot.reply_to(message, f"❌ Format salah. Ketik angka saja, contoh: <code>{default_fee}</code>", parse_mode="HTML")
                return

        database.set_user_marketplace(chat_id, mp, fee)
        bot.reply_to(message, f"✅ Marketplace diubah ke <b>{mp_name}</b> dengan Admin Fee <b>{fee}%</b>.", parse_mode="HTML")

    def process_profit_step(message):
        if not _intercept_menu_or_cancel(message):
            _save_profit_setting(bot, message, message.text)

    def process_katalog_step(message):
        if _intercept_menu_or_cancel(message):
            return
        _run_katalog(bot, message.chat.id, message.text.strip(), reply_to=message)

    def process_hitung_step(message):
        if _intercept_menu_or_cancel(message):
            return
        _run_hitung(bot, message.chat.id, message.text.strip(), reply_to=message)

    # =====================
    # REPLY KEYBOARD BUTTON HANDLER
    # =====================
    @bot.message_handler(func=lambda m: m.text in MENU_BUTTON_TEXTS, content_types=['text'])
    def handle_menu_buttons(message):
        txt = message.text
        chat_id = message.chat.id

        if txt == BTN_TRIAL:
            _activate_trial_for_user(bot, chat_id, reply_to=message)
            return

        if txt in (BTN_LANGGANAN, BTN_PAKET):
            import payments as _pay
            _pay._send_plans_menu(bot, chat_id, reply_to=message)
            return

        if txt == BTN_TAGIHAN:
            import payments as _pay
            _pay.send_invoice_info(bot, chat_id, reply_to=message)
            return

        if txt == BTN_PROFIL:
            handle_profil(message)
            return

        # Tombol fitur utama membutuhkan langganan aktif
        if not _check_subscription(bot, message):
            return

        if txt == BTN_CARI:
            bot.reply_to(
                message,
                "🔍 <b>Pencarian Cepat</b>\n\n"
                "Langsung ketik <b>nama produk</b> yang ingin kamu cari di bawah (tidak perlu ketik /cari).\n"
                "Contoh: <code>sasmita</code> atau <code>abimaya</code>",
                parse_mode="HTML"
            )
        elif txt == BTN_BARU:
            _render_events_list(bot, chat_id, 'new', 1, "🆕", "PRODUK BARU", "produk baru", reply_to=message)
        elif txt == BTN_RESTOCK:
            _render_events_list(bot, chat_id, 'restock', 1, "🔄", "PRODUK RESTOCK", "produk restock", reply_to=message)
        elif txt == BTN_KATALOG:
            msg = bot.reply_to(
                message,
                "📋 Ketik <b>nama produk / koleksi</b> yang ingin dibuatkan teks Katalog WA:\n"
                "Contoh: <code>sasmita</code>\n\n"
                "<i>(Ketik 'batal' untuk membatalkan)</i>",
                parse_mode="HTML"
            )
            bot.register_next_step_handler(msg, process_katalog_step)
        elif txt == BTN_HITUNG:
            msg = bot.reply_to(
                message,
                "🧮 Ketik <b>nama produk</b> (boleh diikuti profit):\n"
                "Contoh: <code>sasmita 20%</code> atau <code>sasmita</code>\n\n"
                "<i>(Ketik 'batal' untuk membatalkan)</i>",
                parse_mode="HTML"
            )
            bot.register_next_step_handler(msg, process_hitung_step)
        elif txt == BTN_LAPORAN:
            _render_perubahan(bot, chat_id, filter_type=None, days=1, reply_to=message)

    # =====================
    # HANDLER FOTO BUKTI PEMBAYARAN (Bantu user yang kirim screenshot transfer)
    # =====================
    @bot.message_handler(content_types=['photo'])
    def handle_photo_upload(message):
        chat_id = message.chat.id
        try:
            import payments as _pay
            open_pay = _pay.latest_open_payment(chat_id)
            if open_pay:
                inv = open_pay['invoice']
                if open_pay['status'] == 'pending':
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(
                        types.InlineKeyboardButton("✅ Klik Di Sini: Saya Sudah Bayar", callback_data=f"pay_submit:{inv}"),
                        types.InlineKeyboardButton("💬 Bantuan Admin", url="https://t.me/fianfi"),
                    )
                    bot.reply_to(
                        message,
                        f"📸 <b>Foto diterima!</b>\n\n"
                        f"Untuk menyelesaikan konfirmasi tagihan <code>{inv}</code>, silakan klik tombol <b>✅ Saya Sudah Bayar</b> di bawah agar Admin segera mengaktifkan akunmu:",
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                    # Teruskan bukti foto ke admin agar admin mudah mengecek
                    try:
                        bot.forward_message(_pay.ADMIN_CHAT_ID, chat_id, message.message_id)
                    except Exception:
                        pass
                    return
                elif open_pay['status'] in ('waiting_verification', 'processing'):
                    try:
                        bot.forward_message(_pay.ADMIN_CHAT_ID, chat_id, message.message_id)
                        bot.send_message(
                            _pay.ADMIN_CHAT_ID,
                            f"📎 Foto bukti tambahan dari ID <code>{chat_id}</code> untuk invoice <code>{inv}</code>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    bot.reply_to(
                        message,
                        f"✅ Bukti foto untuk tagihan <code>{inv}</code> sudah diteruskan ke Admin. Mohon tunggu verifikasi ya!",
                        parse_mode="HTML"
                    )
                    return
        except Exception as e:
            print(f"Photo handler check error: {e}")

        bot.reply_to(
            message,
            "ℹ️ Ketik langsung <b>nama produk</b> berupa teks untuk mencari stok, atau ketik /langganan untuk info paket.",
            parse_mode="HTML"
        )

    # =====================
    # PENCARIAN LANGSUNG TANPA /CARI & FALLBACK HANDLER
    # =====================
    @bot.message_handler(func=lambda message: True, content_types=['text', 'audio', 'document', 'sticker', 'video', 'video_note', 'voice', 'location', 'contact'])
    def handle_direct_search_or_unknown(message):
        if message.content_type != 'text' or not message.text:
            return

        txt = message.text.strip()

        # Jika diawali '/' berarti salah ketik perintah
        if txt.startswith('/'):
            bot.reply_to(
                message,
                "❓ Perintah tidak ditemukan.\n"
                "Ketik /help untuk melihat daftar perintah, atau langsung ketik <b>nama produk</b> tanpa garis miring untuk mencari stok.",
                parse_mode="HTML"
            )
            return

        # Cek langganan sebelum pencarian langsung
        if not _check_subscription(bot, message):
            return

        if len(txt) < 2 or len(txt) > 80:
            bot.reply_to(
                message,
                "🔍 Ketik minimal 2 huruf nama produk untuk mencari stok (contoh: <code>sasmita</code>), atau gunakan tombol menu di bawah.",
                parse_mode="HTML"
            )
            return

        # Jalankan pencarian stok langsung!
        _run_search(bot, message.chat.id, txt, reply_to=message)
