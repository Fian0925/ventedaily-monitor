import json
import os
import re
from datetime import datetime
from collections import defaultdict

import database
import calculator
import config

DATA_FILE = 'data_snapshot.json'


def _load_snapshot():
    """Load data snapshot dan normalize ke format {nama: {stock, harga}}"""
    if not os.path.exists(DATA_FILE):
        return None
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # Handle format lama (key numerik)
    if data and list(data.keys())[0].isdigit():
        normalized = {}
        for k, v in data.items():
            nama = v.get('nama', '')
            if nama:
                normalized[nama] = {"stock": v.get('stock', ''), "harga": v.get('harga', '')}
        return normalized
    return data


def _send_long_message(bot, chat_id, text, reply_to=None, parse_mode="HTML"):
    """Kirim pesan panjang, pecah jadi beberapa pesan jika > 4000 char"""
    MAX_LEN = 4000
    if len(text) <= MAX_LEN:
        if reply_to:
            bot.reply_to(reply_to, text, parse_mode=parse_mode)
        else:
            bot.send_message(chat_id, text, parse_mode=parse_mode)
        return

    # Split by lines, accumulate chunks
    lines = text.split('\n')
    chunk = ""
    first = True
    for line in lines:
        if len(chunk) + len(line) + 1 > MAX_LEN:
            if first and reply_to:
                bot.reply_to(reply_to, chunk, parse_mode=parse_mode)
                first = False
            else:
                bot.send_message(chat_id, chunk, parse_mode=parse_mode)
            chunk = line + '\n'
        else:
            chunk += line + '\n'
    if chunk.strip():
        if first and reply_to:
            bot.reply_to(reply_to, chunk, parse_mode=parse_mode)
        else:
            bot.send_message(chat_id, chunk, parse_mode=parse_mode)


def _group_products_by_variant(products):
    """
    Kelompokkan produk berdasarkan varian (nama tanpa SIZE) dan warna.
    Input: list of (nama, {stock, harga})
    Output: dict {base_name: {warna: {size: {stock, harga}}}}
    """
    groups = defaultdict(lambda: defaultdict(dict))
    price_map = {}

    size_pattern = re.compile(r'\s+SIZE\s+(X{0,3}S|X{0,3}L|M|ALL\s*SIZE|FREE\s*SIZE)\s*$', re.IGNORECASE)

    for nama, info in products:
        size_match = size_pattern.search(nama)
        if size_match:
            size = size_match.group(1).upper()
            base = nama[:size_match.start()].strip()
        else:
            size = "-"
            base = nama.strip()

        # Pisahkan warna dari base name
        # Coba deteksi warna dari kata-kata terakhir
        color_keywords = [
            'Dusty Pink', 'Baby Pink', 'Baby Blue', 'Cool Mint', 'Deep Red',
            'Dark Grey', 'Light Grey', 'Army Green', 'Soft Pink', 'Hot Pink',
            'Dusty Purple', 'Cream Gold',
            'Choco', 'Purple', 'Maroon', 'Navy', 'Cream', 'White', 'Black',
            'Grey', 'Green', 'Blue', 'Red', 'Pink', 'Brown', 'Orange',
            'Mustard', 'Olive', 'Tosca', 'Sage', 'Lilac', 'Mocca',
            'Charcoal', 'Lavender', 'Burgundy', 'Khaki', 'Peach', 'Coral',
            'Caramel', 'Coffee', 'Denim', 'Plum'
        ]

        warna = "-"
        variant_base = base
        for color in color_keywords:
            if base.lower().endswith(color.lower()):
                warna = color
                variant_base = base[:-(len(color))].strip()
                break

        groups[variant_base][warna][size] = info['stock']
        price_map[variant_base] = info['harga']

    return groups, price_map


def register_handlers(bot):

    # =====================
    # /start dan /help
    # =====================
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        help_text = (
            "🤖 <b>VENTEDAILY STOCK MONITOR v2.0</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📖 <b>DAFTAR PERINTAH:</b>\n\n"
            "🔍 /cari [nama]\n"
            "   └ Cari produk, grouped by varian & size\n\n"
            "🆕 /baru atau /baru [hari]\n"
            "   └ Lihat produk baru (default: hari ini)\n\n"
            "🔄 /restock atau /restock [hari]\n"
            "   └ Lihat produk restock (default: hari ini)\n\n"
            "🧮 /hitung [nama] [profit]\n"
            "   └ Kalkulator harga jual semua marketplace\n"
            "   └ Contoh: /hitung ziva 20% atau /hitung ziva 30000\n\n"
            "🏪 /setmp [marketplace]\n"
            "   └ Set default marketplace\n"
            "   └ Pilihan: shopee, tokopedia, tiktok, lazada\n\n"
            "💰 /setmarkup [nilai]\n"
            "   └ Set default profit margin\n"
            "   └ Contoh: /setmarkup 20% atau /setmarkup 30000\n\n"
            "📋 /katalog [nama]\n"
            "   └ Generate katalog siap copas ke WA\n\n"
            "📊 /laporan\n"
            "   └ Laporan mingguan produk baru & restock\n\n"
            "🟢 /status — Cek status server\n"
            "❓ /help — Tampilkan menu ini\n"
        )
        bot.reply_to(message, help_text, parse_mode="HTML")

    # =====================
    # /status
    # =====================
    @bot.message_handler(commands=['status', 'cek'])
    def handle_status(message):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            mod_time = os.path.getmtime(DATA_FILE)
            last_check = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
            total_items = len(data)
            reply = (
                f"🟢 <b>Server Berjalan Normal!</b>\n\n"
                f"📦 Total Produk Dipantau: <b>{total_items}</b>\n"
                f"⏱️ Pengecekan Terakhir: <b>{last_check}</b>"
            )
        else:
            reply = "🟡 Server berjalan, tetapi belum ada data produk yang diambil (sedang diproses)."
        bot.reply_to(message, reply, parse_mode="HTML")

    # =====================
    # /cari [nama] — Grouped by variant & size
    # =====================
    @bot.message_handler(commands=['cari'])
    def handle_cari(message):
        query = message.text.replace('/cari', '').strip().lower()
        if not query:
            bot.reply_to(message, "⚠️ Gunakan format: /cari [nama produk]\nContoh: /cari sasmita")
            return

        data = _load_snapshot()
        if data is None:
            bot.reply_to(message, "⚠️ Data produk belum tersedia.")
            return

        results = [(k, v) for k, v in data.items() if query in k.lower()]

        if not results:
            bot.reply_to(message, f"😔 Tidak menemukan produk dengan nama \"{query}\".")
            return

        ready = [(k, v) for k, v in results if v.get('stock', '').lower() in ['aman', 'ready']]

        if not ready:
            bot.reply_to(message, f"😔 Semua produk \"{query}\" sedang HABIS.")
            return

        reply = f"🔍 <b>Hasil Pencarian: \"{query}\"</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        reply += f"✅ <b>READY / AMAN ({len(ready)} produk):</b>\n\n"

        groups, price_map = _group_products_by_variant(ready)
        size_order = ['S', 'M', 'L', 'XL', 'XXL', 'XXXL', '-']

        for variant_base in sorted(groups.keys()):
            colors = groups[variant_base]
            harga = price_map.get(variant_base, '')
            reply += f"📦 <b>{variant_base}</b> — {harga}\n"

            for warna in sorted(colors.keys()):
                sizes = colors[warna]
                size_display = []
                for sz in size_order:
                    if sz in sizes:
                        stock = sizes[sz].lower()
                        icon = "✅" if stock == "aman" else "📦"
                        size_display.append(f"{sz}{icon}")
                if size_display:
                    if warna != "-":
                        reply += f"  • {warna}: {' '.join(size_display)}\n"
                    else:
                        reply += f"  • {' '.join(size_display)}\n"
            reply += "\n"

        reply += f"📊 Total Ready: {len(ready)} produk"

        _send_long_message(bot, message.chat.id, reply, reply_to=message)

    # =====================
    # /baru [hari] — Grouped by day
    # =====================
    @bot.message_handler(commands=['baru'])
    def handle_baru(message):
        args = message.text.split()
        days = 1
        if len(args) > 1 and args[1].isdigit():
            days = int(args[1])

        events = database.get_events('new', days)

        if not events:
            bot.reply_to(message, f"📭 Belum ada produk baru dalam {days} hari terakhir.")
            return

        reply = f"🆕 <b>PRODUK BARU ({days} hari terakhir)</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"

        # Group by day
        by_day = defaultdict(list)
        for ev in events:
            dt_str = ev.get('detected_at', '')
            try:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                day_key = dt.strftime('%d %b %Y')
                time_str = dt.strftime('%H:%M')
            except Exception:
                day_key = 'Unknown'
                time_str = ''
            by_day[day_key].append((ev, time_str))

        count = 0
        for day in sorted(by_day.keys(), reverse=True):
            items = by_day[day]
            reply += f"📅 <b>{day}</b>\n"
            for ev, time_str in items:
                count += 1
                if count > 30:
                    reply += f"<i>... dan {len(events) - 30} produk lainnya.</i>\n"
                    break
                time_label = f" ({time_str})" if time_str else ""
                reply += f"  {count}. {ev['nama']}{time_label}\n     Stok: {ev.get('stock','-')} | {ev.get('harga','-')}\n"
            reply += "\n"
            if count > 30:
                break

        reply += f"📊 Total produk baru: {len(events)}"
        _send_long_message(bot, message.chat.id, reply, reply_to=message)

    # =====================
    # /restock [hari] — Grouped by day
    # =====================
    @bot.message_handler(commands=['restock'])
    def handle_restock(message):
        args = message.text.split()
        days = 1
        if len(args) > 1 and args[1].isdigit():
            days = int(args[1])

        events = database.get_events('restock', days)

        if not events:
            bot.reply_to(message, f"📭 Belum ada produk restock dalam {days} hari terakhir.")
            return

        reply = f"🔄 <b>PRODUK RESTOCK ({days} hari terakhir)</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"

        # Group by day
        by_day = defaultdict(list)
        for ev in events:
            dt_str = ev.get('detected_at', '')
            try:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                day_key = dt.strftime('%d %b %Y')
                time_str = dt.strftime('%H:%M')
            except Exception:
                day_key = 'Unknown'
                time_str = ''
            by_day[day_key].append((ev, time_str))

        count = 0
        for day in sorted(by_day.keys(), reverse=True):
            items = by_day[day]
            reply += f"📅 <b>{day}</b>\n"
            for ev, time_str in items:
                count += 1
                if count > 30:
                    reply += f"<i>... dan {len(events) - 30} produk lainnya.</i>\n"
                    break
                time_label = f" ({time_str})" if time_str else ""
                reply += f"  {count}. ✅ {ev['nama']}{time_label}\n     Habis ➡️ {ev.get('stock','-')} | {ev.get('harga','-')}\n"
            reply += "\n"
            if count > 30:
                break

        reply += f"📊 Total restock: {len(events)}"
        _send_long_message(bot, message.chat.id, reply, reply_to=message)

    # =====================
    # /setmp [marketplace] [fee%]
    # =====================
    @bot.message_handler(commands=['setmp'])
    def handle_setmp(message):
        chat_id = message.chat.id
        args = message.text.split()
        if len(args) < 2:
            current = database.get_user_settings(chat_id)
            mp = current.get('marketplace', 'shopee')
            fee = current.get('admin_fee', 6.5)
            mp_info = calculator.MARKETPLACES.get(mp, calculator.MARKETPLACES['shopee'])
            reply = (
                f"🏪 <b>SETTING MARKETPLACE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Marketplace aktif: <b>{mp_info['name']}</b>\n"
                f"Admin fee: <b>{fee}%</b>\n\n"
                f"Untuk mengubah:\n"
                f"/setmp shopee\n"
                f"/setmp tokopedia\n"
                f"/setmp tiktok\n"
                f"/setmp lazada\n\n"
                f"💡 Kamu juga bisa set admin fee custom:\n"
                f"/setmp shopee 4.5\n"
                f"  └ Set Shopee dengan fee 4.5%\n"
            )
            bot.reply_to(message, reply, parse_mode="HTML")
            return

        mp_key = args[1].lower()
        if mp_key in calculator.MARKETPLACES:
            # Cek apakah user memasukkan custom fee
            default_fee = calculator.MARKETPLACES[mp_key]['fee']
            if len(args) >= 3:
                try:
                    custom_fee = float(args[2].replace('%', ''))
                    fee = custom_fee
                except ValueError:
                    fee = default_fee
            else:
                fee = default_fee

            database.set_user_marketplace(chat_id, mp_key, fee)
            mp_name = calculator.MARKETPLACES[mp_key]['name']
            if fee != default_fee:
                bot.reply_to(message, f"✅ Marketplace: <b>{mp_name}</b>\n💰 Admin fee custom: <b>{fee}%</b> (default: {default_fee}%)", parse_mode="HTML")
            else:
                bot.reply_to(message, f"✅ Marketplace: <b>{mp_name}</b>\n💰 Admin fee: <b>{fee}%</b>", parse_mode="HTML")
        else:
            bot.reply_to(message, "❌ Marketplace tidak dikenal.\nPilihan: shopee, tokopedia, tiktok, lazada")

    # =====================
    # /setmarkup [nilai]
    # =====================
    @bot.message_handler(commands=['setmarkup'])
    def handle_setmarkup(message):
        chat_id = message.chat.id
        args = message.text.split()
        if len(args) < 2:
            current = database.get_user_settings(chat_id)
            mt = current.get('markup_type', 'percent')
            mv = current.get('markup_value', 0)
            if mt == 'percent':
                label = f"{mv}%"
            else:
                label = f"Rp {int(mv):,}".replace(",", ".")
            reply = (
                f"💰 <b>SETTING MARKUP</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Markup aktif: <b>{label}</b>\n\n"
                f"Untuk mengubah:\n"
                f"/setmarkup 20%  (profit 20%)\n"
                f"/setmarkup 30000  (profit Rp 30.000)\n"
            )
            bot.reply_to(message, reply, parse_mode="HTML")
            return

        value_str = args[1]
        if '%' in value_str:
            markup_type = 'percent'
            try:
                markup_value = float(value_str.replace('%', ''))
            except ValueError:
                bot.reply_to(message, "❌ Format salah. Contoh: /setmarkup 20% atau /setmarkup 30000")
                return
            label = f"{markup_value}%"
        else:
            markup_type = 'nominal'
            try:
                markup_value = float(value_str)
            except ValueError:
                bot.reply_to(message, "❌ Format salah. Contoh: /setmarkup 20% atau /setmarkup 30000")
                return
            label = f"Rp {int(markup_value):,}".replace(",", ".")

        database.set_user_markup(chat_id, markup_type, markup_value)
        bot.reply_to(message, f"✅ Default markup disimpan: <b>{label}</b>", parse_mode="HTML")

    # =====================
    # /hitung [nama] [profit] — Compare all marketplaces
    # =====================
    @bot.message_handler(commands=['hitung'])
    def handle_hitung(message):
        chat_id = message.chat.id
        args = message.text.split()

        if len(args) < 2:
            bot.reply_to(
                message,
                "🧮 <b>KALKULATOR HARGA JUAL</b>\n\n"
                "Format:\n"
                "/hitung [nama] [profit]\n\n"
                "Contoh:\n"
                "/hitung sasmita bro 20%\n"
                "/hitung ziva 35000\n"
                "/hitung sasmita bro\n"
                "  └ pakai markup dari /setmarkup",
                parse_mode="HTML"
            )
            return

        data = _load_snapshot()
        if data is None:
            bot.reply_to(message, "Data produk belum tersedia.")
            return

        # Parse arguments: cek apakah arg terakhir adalah profit
        profit_str = None
        profit_val = 0
        profit_type = 'percent'

        last_arg = args[-1]
        if '%' in last_arg:
            try:
                profit_val = float(last_arg.replace('%', ''))
                profit_type = 'percent'
                profit_str = last_arg
                query = " ".join(args[1:-1]).strip().lower()
            except ValueError:
                query = " ".join(args[1:]).strip().lower()
        elif last_arg.isdigit() and len(args) > 2:
            profit_val = float(last_arg)
            profit_type = 'nominal'
            profit_str = last_arg
            query = " ".join(args[1:-1]).strip().lower()
        else:
            query = " ".join(args[1:]).strip().lower()

        if not query:
            bot.reply_to(message, "⚠️ Masukkan nama produk. Contoh: /hitung sasmita bro 20%")
            return

        # Cari produk
        matches = [(k, v) for k, v in data.items() if query in k.lower() and v.get('stock', '').lower() in ['aman', 'ready']]
        if not matches:
            # Coba cari semua (termasuk habis)
            matches = [(k, v) for k, v in data.items() if query in k.lower()]
        if not matches:
            bot.reply_to(message, f"❌ Produk '{query}' tidak ditemukan.")
            return

        target_name, target_data = matches[0]

        # Ambil settings user
        settings = database.get_user_settings(chat_id)

        if profit_str is None:
            profit_type = settings.get('markup_type', 'percent')
            profit_val = float(settings.get('markup_value', 0))

        if profit_val == 0:
            bot.reply_to(
                message,
                f"📦 <b>{target_name}</b>\n"
                f"💰 Modal: {target_data['harga']}\n\n"
                f"⚠️ Belum ada profit yang diset.\n"
                f"Gunakan: /hitung {query} 20%\n"
                f"Atau set default: /setmarkup 20%",
                parse_mode="HTML"
            )
            return

        profit_label = f"{profit_val}%" if profit_type == 'percent' else f"Rp {int(profit_val):,}".replace(",", ".")

        # Hitung di semua marketplace
        comparisons = calculator.compare_all_marketplaces(target_data['harga'], profit_val, profit_type)

        if not comparisons:
            bot.reply_to(message, "❌ Gagal menghitung harga.")
            return

        reply = (
            f"🧮 <b>KALKULASI HARGA JUAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 <b>{target_name}</b>\n"
            f"💰 Modal: {target_data['harga']}\n"
            f"💵 Target Profit: {profit_label}\n\n"
            f"🏪 <b>PERBANDINGAN MARKETPLACE:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )

        default_mp = settings.get('marketplace', 'shopee')

        for mp_key in ['shopee', 'tokopedia', 'tiktok', 'lazada']:
            if mp_key not in comparisons:
                continue
            comp = comparisons[mp_key]
            marker = " ⬅️" if mp_key == default_mp else ""
            reply += (
                f"\n{'🟠' if mp_key == 'shopee' else '🟢' if mp_key == 'tokopedia' else '⚫' if mp_key == 'tiktok' else '🔵'} "
                f"<b>{comp['name']}</b> (Fee {comp['fee_pct']}%){marker}\n"
                f"   🏷️ Jual: <b>{calculator.format_price(comp['harga_jual'])}</b>\n"
                f"   ✅ Profit: {calculator.format_price(comp['actual_profit'])}\n"
            )

        reply += f"\n━━━━━━━━━━━━━━━━━━━━━\n💡 Default MP: {calculator.MARKETPLACES.get(default_mp, {}).get('name', 'Shopee')} (ubah: /setmp)"

        bot.reply_to(message, reply, parse_mode="HTML")

    # =====================
    # /katalog [nama] — Grouped by brand, with user markup
    # =====================
    @bot.message_handler(commands=['katalog'])
    def handle_katalog(message):
        chat_id = message.chat.id
        query = message.text.replace('/katalog', '').strip().lower()

        data = _load_snapshot()
        if data is None:
            bot.reply_to(message, "Data produk belum tersedia.")
            return

        if query:
            results = [(k, v) for k, v in data.items() if query in k.lower()]
        else:
            bot.reply_to(message, "⚠️ Gunakan format: /katalog [nama brand/produk]\nContoh: /katalog sasmita")
            return

        ready = [(k, v) for k, v in results if v.get('stock', '').lower() in ['aman', 'ready']]

        if not ready:
            bot.reply_to(message, f"📭 Tidak ada produk \"{query}\" yang ready saat ini.")
            return

        # Get user settings for markup
        settings = database.get_user_settings(chat_id)
        markup_type = settings.get('markup_type', 'percent')
        markup_value = float(settings.get('markup_value', 0))
        default_mp = settings.get('marketplace', 'shopee')
        admin_fee = float(settings.get('admin_fee', 6.5))
        has_markup = markup_value > 0

        reply = "🛍️ <b>KATALOG VENTEDAILY</b>\n"
        reply += f"📅 {datetime.now().strftime('%d %b %Y | %H:%M WIB')}\n"
        reply += f"━━━━━━━━━━━━━━━━━━━━━\n\n"

        # Group by brand (first word of product name, after removing (Glamloc) prefix)
        groups, price_map = _group_products_by_variant(ready)

        for variant_base in sorted(groups.keys()):
            colors = groups[variant_base]
            harga_str = price_map.get(variant_base, '')

            # Calculate sell price if markup is set
            price_label = harga_str
            if has_markup:
                calc = calculator.calculate_price(harga_str, markup_value, markup_type, admin_fee)
                if calc:
                    price_label = calculator.format_price(calc['harga_jual'])

            reply += f"📦 <b>{variant_base}</b> — {price_label}\n"
            for warna in sorted(colors.keys()):
                sizes = colors[warna]
                size_order = ['S', 'M', 'L', 'XL', 'XXL', 'XXXL', '-']
                avail = [sz for sz in size_order if sz in sizes]
                if warna != "-":
                    reply += f"  • {warna}: {', '.join(avail)}\n"
                else:
                    reply += f"  • Size: {', '.join(avail)}\n"
            reply += "\n"

        reply += f"━━━━━━━━━━━━━━━━━━━━━\n"
        reply += f"📦 Total Ready: {len(ready)} produk\n"
        if has_markup:
            mp_name = calculator.MARKETPLACES.get(default_mp, {}).get('name', 'Shopee')
            reply += f"💰 Harga jual untuk {mp_name}\n"
        reply += f"✅ = Aman | 📦 = Ready\n"
        reply += f"\n📱 Minat? Chat admin ya!"

        _send_long_message(bot, message.chat.id, reply, reply_to=message)

    # =====================
    # /laporan — Laporan mingguan
    # =====================
    @bot.message_handler(commands=['laporan'])
    def handle_laporan(message):
        _generate_report(bot, message.chat.id, reply_to=message)


def _generate_report(bot, chat_id, reply_to=None):
    """Generate laporan mingguan produk baru & restock (7 hari terakhir)"""
    new_events = database.get_events('new', 7)
    restock_events = database.get_events('restock', 7)

    data = _load_snapshot()
    total_produk = len(data) if data else 0

    if data:
        aman_count = sum(1 for v in data.values() if v.get('stock', '').lower() == 'aman')
        ready_count = sum(1 for v in data.values() if v.get('stock', '').lower() == 'ready')
        habis_count = sum(1 for v in data.values() if v.get('stock', '').lower() == 'habis')
    else:
        aman_count = ready_count = habis_count = 0

    reply = (
        f"📊 <b>LAPORAN MINGGUAN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {datetime.now().strftime('%d %b %Y | %H:%M WIB')}\n\n"
        f"📦 <b>Total Produk:</b> {total_produk}\n"
        f"   ✅ Aman: {aman_count}\n"
        f"   📦 Ready: {ready_count}\n"
        f"   ❌ Habis: {habis_count}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆕 <b>Produk Baru (7 hari):</b> {len(new_events)}\n"
    )

    if new_events:
        for ev in new_events[:10]:
            reply += f"  • {ev['nama']} — {ev.get('harga', '-')}\n"
        if len(new_events) > 10:
            reply += f"  <i>... dan {len(new_events) - 10} lainnya</i>\n"

    reply += (
        f"\n🔄 <b>Produk Restock (7 hari):</b> {len(restock_events)}\n"
    )

    if restock_events:
        # Cari produk yang paling sering restock
        restock_count = defaultdict(int)
        for ev in restock_events:
            # Remove size info for grouping
            base_name = re.sub(r'\s+SIZE\s+\S+$', '', ev['nama'], flags=re.IGNORECASE)
            restock_count[base_name] += 1

        for ev in restock_events[:10]:
            reply += f"  • {ev['nama']} — {ev.get('harga', '-')}\n"
        if len(restock_events) > 10:
            reply += f"  <i>... dan {len(restock_events) - 10} lainnya</i>\n"

        # Top restocked
        top_restock = sorted(restock_count.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_restock:
            reply += f"\n🔥 <b>Paling Sering Restock (Terlaris?):</b>\n"
            for name, cnt in top_restock:
                reply += f"  🏆 {name} ({cnt}x restock)\n"

    reply += f"\n━━━━━━━━━━━━━━━━━━━━━\n💡 Laporan otomatis dikirim setiap Senin 08:00 WIB"

    _send_long_message(bot, chat_id, reply, reply_to=reply_to)


def send_weekly_report(bot, chat_id):
    """Dipanggil oleh scheduler di monitor.py setiap Senin jam 08:00"""
    _generate_report(bot, chat_id)
