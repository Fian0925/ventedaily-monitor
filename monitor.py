import requests
import time
import schedule
import os
from datetime import datetime
import threading
from flask import Flask, make_response
import telebot
import config
import database
import commands
import payments
import mini_app

API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "VentedailyMonitorBot/2.0",
}

class MonitorTelegramBot(telebot.TeleBot):
    def __init__(self, token):
        super().__init__(token)
        self.poll_result = None

    def _telegram_request(self, request, *args, **kwargs):
        try:
            return request(*args, **kwargs)
        except telebot.apihelper.ApiTelegramException as exc:
            if exc.error_code == 409:
                reason = "webhook_active" if "webhook" in exc.description.lower() else "polling_conflict"
            else:
                reason = f"telegram_http_{exc.error_code}"
            self.poll_result = (time.monotonic(), reason)
            raise
        except requests.RequestException:
            self.poll_result = (time.monotonic(), "network_error")
            raise
        except Exception:
            self.poll_result = (time.monotonic(), "polling_error")
            raise

    def get_me(self):
        # TeleBot fetches its identity before starting its first poll.
        return self._telegram_request(super().get_me)

    def get_updates(self, *args, **kwargs):
        updates = self._telegram_request(super().get_updates, *args, **kwargs)
        self.poll_result = (time.monotonic(), None)
        return updates


app = Flask(__name__)
app.register_blueprint(mini_app.mini_app)
bot = MonitorTelegramBot(config.TELEGRAM_BOT_TOKEN)
telegram_thread = None


@app.route('/')
def home():
    return "Bot Monitoring Ventedaily Sedang Berjalan 24/7!"


@app.get("/healthz")
def health():
    if telegram_thread is None:
        telegram = {"status": "not_started"}
    elif not telegram_thread.is_alive():
        telegram = {"status": "stopped"}
    elif bot.poll_result is None:
        telegram = {"status": "starting"}
    else:
        last_poll, reason = bot.poll_result
        if reason:
            telegram = {"status": "error", "reason": reason}
        elif time.monotonic() - last_poll > 90:
            telegram = {"status": "stalled"}
        else:
            telegram = {"status": "ready"}
    response = make_response({"web": "ok", "telegram": telegram})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/api/data')
def get_data():
    snapshot = database.load_catalog_snapshot()
    if snapshot:
        response = make_response(snapshot)
    else:
        response = make_response({"error": "Data belum tersedia, bot sedang loading."}, 404)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


def send_admin_message(message):
    try:
        bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=message, parse_mode="HTML")
    except Exception as e:
        print(f"Error sending admin message: {e}")

def broadcast_telegram_message(message):
    users = database.get_active_users()
    for u in users:
        try:
            bot.send_message(chat_id=u['chat_id'], text=message, parse_mode="HTML")
            time.sleep(0.05)  # Rate limit: max ~20 msg/sec untuk hindari 429
        except Exception as e:
            print(f"Error sending to {u.get('chat_id')}: {e}")


def _normalize_size(raw_size):
    """Normalisasi ukuran dari API: 'Size L' -> 'L', 'All Size' -> 'ALL SIZE'."""
    s = raw_size.strip()
    # Hapus prefix "Size " jika ada
    if s.lower().startswith("size "):
        s = s[5:].strip()
    return s.upper()


def _fetch_paginated(url):
    """Helper: fetch semua halaman dari endpoint yang diberi URL, return list items."""
    all_items = []
    page = 1
    while True:
        try:
            resp = requests.get(url, headers=API_HEADERS,
                                params={"page": page, "limit": config.PER_PAGE},
                                timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"Error mengambil halaman {page} dari {url}: {e}")
            return None  # Return None agar caller bisa batalkan siklus
        items = payload.get("items", [])
        all_items.extend(items)
        total_pages = payload.get("pages", 1)
        print(f"  [{url.split('/')[-1]}] Halaman {page}/{total_pages} — {len(items)} item")
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)
    return all_items


def fetch_all(previous_data=None):
    """Ambil data stok dari API Ventedaily ERP.
    
    Strategi dual-source:
    - Status stok  → /api/public/catalog  (update real-time, sama dengan website)
    - Harga reseller → /api/public/stock   (harga reseller asli, lebih akurat)
    """
    from datetime import timezone, timedelta
    wib = timezone(timedelta(hours=7))
    print(f"[{datetime.now(wib).strftime('%Y-%m-%d %H:%M:%S WIB')}] Mulai fetch data...")

    # 1. Ambil status dari catalog (sumber utama, update real-time)
    catalog_url = "https://erp.ventedaily.net/api/public/catalog"
    catalog_items = _fetch_paginated(catalog_url)
    if catalog_items is None:
        print("Gagal fetch catalog. Membatalkan siklus.")
        return None

    # 2. Ambil harga reseller dari stock (akurasi harga)
    stock_url = "https://erp.ventedaily.net/api/public/stock"
    stock_items = _fetch_paginated(stock_url)

    # Build lookup harga reseller: "product_name color" → reseller_price
    # Stock product_name format: "Abimaya Dad", color: "Blue"
    # → price_key: "abimaya dad blue"
    price_lookup = {}
    identity_lookup = {}
    if stock_items:
        for s in stock_items:
            pn = s.get("product_name", "").strip()
            # Hapus prefix defect
            pn = pn.replace("[DEFECT]", "").replace("(Defect)", "").strip()
            color = s.get("color", "").strip()
            price_key = f"{pn.lower()} {color.lower()}".strip()
            if pn and color:
                identity_lookup[price_key] = {"product_name": pn, "color": color}
            reseller = s.get("reseller_price", 0)
            # Prioritaskan harga tertinggi (produk normal, bukan defect)
            if price_key not in price_lookup or reseller > price_lookup[price_key]:
                price_lookup[price_key] = reseller

    if not catalog_items:
        print("Peringatan: catalog mengembalikan 0 item.")
        return None

    # 3. Gabungkan: status dari catalog + harga dari stock lookup
    import re as _re
    data = {}
    previous_data = previous_data or {}
    price_miss = 0
    for item in catalog_items:
        nama = item.get("name", "").strip()
        status = item.get("status", "").strip()
        selling_price = item.get("selling_price", 0)

        if not nama:
            continue

        # Skip produk defect
        nama_lower = nama.lower()
        if '[defect]' in nama_lower or '(defect)' in nama_lower:
            continue

        if status not in ("Aman", "Ready", "Habis"):
            status = "Habis"

        # Cari harga reseller dari stock lookup
        # Catalog: "(Couple) Abimaya Dad Blue SIZE Size L"
        # → strip SIZE suffix → "(couple) abimaya dad blue"
        # → strip category prefix "(couple) " → "abimaya dad blue"
        # → cocok dengan stock key "abimaya dad blue"
        name_no_size = _re.sub(r'\s+SIZE\s+.*$', '', nama, flags=_re.IGNORECASE).strip()
        name_no_cat = _re.sub(r'^\([^)]+\)\s*', '', name_no_size).strip()
        name_for_lookup = name_no_cat.lower()

        reseller_price = price_lookup.get(name_for_lookup, 0)
        previous = previous_data.get(nama, {})

        if reseller_price > 0:
            harga = f"Rp {reseller_price:,}".replace(",", ".")
            data[nama] = {"stock": status, "harga": harga}
        else:
            if previous.get("harga") and not previous.get("catalog_price"):
                data[nama] = {"stock": status, "harga": previous["harga"]}
            else:
                harga = f"Rp {selling_price:,}".replace(",", ".")
                data[nama] = {"stock": status, "harga": harga, "catalog_price": True}
                price_miss += 1

        identity = identity_lookup.get(name_for_lookup)
        if identity:
            data[nama].update(identity)
        elif previous.get("product_name") and previous.get("color"):
            data[nama].update(product_name=previous["product_name"], color=previous["color"])

    print(f"  Info: {price_miss} produk pakai harga fallback (tidak cocok di stock)")
    print(f"Selesai! Total {len(data)} varian dari catalog, {len(price_lookup)} harga reseller dari stock.")
    return data


_NOTIF_SIZE_ORDER = ['XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL', 'ALL SIZE', 'FREE SIZE', '-']


def _split_base_and_size(prod_nama):
    """Pisahkan nama produk dasar dan ukuran dari format catalog."""
    import re as _re
    m = _re.search(r'\s+SIZE\s+(?:Size\s+)?(.+)$', prod_nama, flags=_re.IGNORECASE)
    if m:
        base = prod_nama[:m.start()].strip()
        raw_sz = m.group(1).strip()
        sz_up = raw_sz.upper()
        if sz_up == "ALL SIZE":
            sz_disp = "All Size"
        elif sz_up == "FREE SIZE":
            sz_disp = "Free Size"
        else:
            sz_disp = sz_up
        return base, sz_disp
    return prod_nama.strip(), "-"


def _sort_size_items(size_items):
    """Urutkan list (size_str, status_or_none) sesuai urutan ukuran pakaian."""
    def _key(item):
        sz_up = item[0].upper()
        if sz_up in _NOTIF_SIZE_ORDER:
            return (0, _NOTIF_SIZE_ORDER.index(sz_up), sz_up)
        return (1, 99, sz_up)
    return sorted(size_items, key=_key)


def compare_data(old_data, new_data):
    """Bandingkan snapshot dan siapkan pesan serta riwayat untuk siklus ini."""
    from collections import defaultdict
    from datetime import timezone, timedelta

    events = []

    def record(nama, event_type, stock, harga):
        events.append({"nama": nama, "event_type": event_type, "stock": stock, "harga": harga})
    total_changes = 0

    restock_groups = defaultdict(lambda: {"harga": "", "sizes": []})
    new_groups = defaultdict(lambda: {"harga": "", "sizes": []})
    menipis_groups = defaultdict(lambda: {"harga": "", "sizes": []})
    aman_lagi_groups = defaultdict(lambda: {"harga": "", "sizes": []})
    habis_groups = defaultdict(lambda: {"harga": "", "sizes": []})
    price_groups = {}

    for prod_nama, new_item in new_data.items():
        base_name, sz = _split_base_and_size(prod_nama)
        new_st = new_item.get('stock', '').strip()
        new_st_low = new_st.lower()
        new_harga = new_item.get('harga', '')

        if prod_nama not in old_data:
            total_changes += 1
            record(prod_nama, 'new', new_st, new_harga)
            # Hanya tampilkan di notif jika produk baru stoknya Ready/Aman
            if new_st_low in ('aman', 'ready'):
                new_groups[base_name]["harga"] = new_harga
                new_groups[base_name]["sizes"].append((sz, new_st_low))
        else:
            old_item = old_data[prod_nama]
            old_st = old_item.get('stock', '').strip()
            old_st_low = old_st.lower()
            old_harga = old_item.get('harga', '')

            if old_st != new_st:
                total_changes += 1
                if "habis" in old_st_low and new_st_low in ("ready", "aman"):
                    record(prod_nama, 'restock', new_st, new_harga)
                    restock_groups[base_name]["harga"] = new_harga
                    restock_groups[base_name]["sizes"].append((sz, new_st_low))
                elif old_st_low in ("ready", "aman") and "habis" in new_st_low:
                    record(prod_nama, 'habis', old_st, new_harga)
                    habis_groups[base_name]["harga"] = new_harga
                    habis_groups[base_name]["sizes"].append((sz, new_st_low))
                elif old_st_low == "aman" and new_st_low == "ready":
                    menipis_groups[base_name]["harga"] = new_harga
                    menipis_groups[base_name]["sizes"].append((sz, new_st_low))
                elif old_st_low == "ready" and new_st_low == "aman":
                    aman_lagi_groups[base_name]["harga"] = new_harga
                    aman_lagi_groups[base_name]["sizes"].append((sz, new_st_low))

            if (old_harga != new_harga
                    and not old_item.get("catalog_price") and not new_item.get("catalog_price")):
                total_changes += 1
                record(prod_nama, 'price_change', old_harga, new_harga)
                price_groups[base_name] = (old_harga, new_harga)

    has_visible = any([
        restock_groups, new_groups, menipis_groups,
        aman_lagi_groups, habis_groups, price_groups
    ])
    if not has_visible:
        return [], total_changes, events

    wib = timezone(timedelta(hours=7))
    now_label = datetime.now(wib).strftime('%d %b %Y | %H:%M WIB')

    lines = [
        "🔔 <b>UPDATE STOK VENTEDAILY</b>",
        f"🕐 {now_label}",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]

    MAX_PER_SECTION = 12

    def _fmt_sizes_with_icon(sz_list):
        ordered = _sort_size_items(sz_list)
        parts = []
        for sz, st in ordered:
            icon = "✅" if st == "aman" else "⚠️"
            parts.append(f"{sz}{icon}" if sz != "-" else f"Stok{icon}")
        return " ".join(parts)

    def _fmt_sizes_plain(sz_list):
        ordered = _sort_size_items(sz_list)
        return ", ".join(sz for sz, _ in ordered if sz != "-") or "Semua ukuran"

    if restock_groups:
        items = sorted(restock_groups.items())
        lines.append(f"\n🟢 <b>RESTOCK ({len(items)} produk):</b>")
        for base, info in items[:MAX_PER_SECTION]:
            lines.append(f"• <b>{base}</b> — {info['harga']}\n  └ {_fmt_sizes_with_icon(info['sizes'])}")
        if len(items) > MAX_PER_SECTION:
            lines.append(f"  <i>... +{len(items) - MAX_PER_SECTION} produk restock lainnya (ketik /restock)</i>")

    if new_groups:
        items = sorted(new_groups.items())
        lines.append(f"\n🆕 <b>PRODUK BARU ({len(items)} produk):</b>")
        for base, info in items[:MAX_PER_SECTION]:
            lines.append(f"• <b>{base}</b> — {info['harga']}\n  └ {_fmt_sizes_with_icon(info['sizes'])}")
        if len(items) > MAX_PER_SECTION:
            lines.append(f"  <i>... +{len(items) - MAX_PER_SECTION} produk baru lainnya (ketik /baru)</i>")

    if aman_lagi_groups:
        items = sorted(aman_lagi_groups.items())
        lines.append(f"\n✅ <b>STOK KEMBALI BANYAK ({len(items)} produk):</b>")
        for base, info in items[:8]:
            lines.append(f"• <b>{base}</b> ({_fmt_sizes_plain(info['sizes'])})")
        if len(items) > 8:
            lines.append(f"  <i>... +{len(items) - 8} lainnya</i>")

    if menipis_groups:
        items = sorted(menipis_groups.items())
        lines.append(f"\n⚡ <b>STOK MENIPIS / LIMIT ({len(items)} produk):</b>")
        for base, info in items[:8]:
            lines.append(f"• <b>{base}</b> ({_fmt_sizes_plain(info['sizes'])})")
        if len(items) > 8:
            lines.append(f"  <i>... +{len(items) - 8} lainnya</i>")

    if habis_groups:
        items = sorted(habis_groups.items())
        lines.append(f"\n🔴 <b>JADI HABIS ({len(items)} produk):</b>")
        for base, info in items[:10]:
            lines.append(f"• {base} ({_fmt_sizes_plain(info['sizes'])})")
        if len(items) > 10:
            lines.append(f"  <i>... +{len(items) - 10} produk habis lainnya</i>")

    if price_groups:
        items = sorted(price_groups.items())
        lines.append(f"\n💰 <b>PERUBAHAN HARGA ({len(items)} produk):</b>")
        for base, (old_p, new_p) in items[:8]:
            lines.append(f"• <b>{base}</b>: {old_p} ➡️ <b>{new_p}</b>")
        if len(items) > 8:
            lines.append(f"  <i>... +{len(items) - 8} perubahan harga lainnya</i>")

    lines.append("\n<i>✅ Aman | ⚠️ Ready (Terbatas)\n💡 Ketik langsung nama produk di chat untuk cek & buat katalog.</i>")

    full_msg = "\n".join(lines)
    # Pecah aman jika melebihi batas Telegram 4000 karakter
    messages = []
    if len(full_msg) <= 4000:
        messages.append(full_msg)
    else:
        chunk = ""
        for ln in full_msg.split("\n"):
            if len(chunk) + len(ln) + 1 > 3900:
                messages.append(chunk)
                chunk = ln + "\n"
            else:
                chunk += ln + "\n"
        if chunk.strip():
            messages.append(chunk)

    return messages, total_changes, events


def job():
    from datetime import timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now_str = datetime.now(wib).strftime('%H:%M:%S WIB')

    try:
        snapshot = database.read_snapshot()
        old_data = snapshot["data"] if snapshot else None
        if old_data:
            key = next(iter(old_data))
            if key.isdigit() or "nama" in old_data[key]:
                print("Format snapshot lama terdeteksi. Reset snapshot.")
                old_data = None

        new_data = fetch_all(old_data)
        if not new_data:
            send_admin_message(f"⚠️ [{now_str}] Data stok gagal dimuat. Siklus dilewati.")
            return

        messages, total_changes, events = compare_data(old_data, new_data) if old_data else ([], 0, [])
        expected_updated_at = snapshot["updated_at"] if snapshot else None
        if not database.save_snapshot(new_data, expected_updated_at, events):
            print(f"[{now_str}] Snapshot sudah diperbarui oleh siklus lain.")
            return

        if not old_data:
            send_admin_message(
                f"🤖 [{now_str}] <b>Snapshot baru disimpan ke Supabase</b>\n"
                f"Total: {len(new_data)} varian produk.\n"
                "Perbandingan dimulai siklus berikutnya."
            )
        elif messages:
            for msg in messages:
                broadcast_telegram_message(msg)
                time.sleep(0.5)
            send_admin_message(
                f"📊 [{now_str}] Job selesai: {total_changes} perubahan, {len(messages)} pesan ringkas."
            )
        else:
            print(f"[{now_str}] Job OK: {total_changes} perubahan. Snapshot: {len(old_data)}→{len(new_data)}")

    except Exception as e:
        print(f"Error during job execution: {e}")
        import traceback
        traceback.print_exc()
        try:
            send_admin_message(f"⚠️ [{now_str}] <b>ERROR job():</b>\n<code>{e}</code>")
        except:
            pass

def rekap_harian_job():
    """Kirim rekap perubahan stok harian ke semua user aktif. Dijadwalkan jam 08:00 WIB."""
    from datetime import datetime, timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now_wib = datetime.now(wib)
    hari_ini = now_wib.strftime('%A, %d %B %Y')

    # Ganti nama hari ke Bahasa Indonesia
    hari_map = {
        'Monday': 'Senin', 'Tuesday': 'Selasa', 'Wednesday': 'Rabu',
        'Thursday': 'Kamis', 'Friday': "Jum'at", 'Saturday': 'Sabtu', 'Sunday': 'Minggu'
    }
    for en, id_ in hari_map.items():
        hari_ini = hari_ini.replace(en, id_)

    try:
        events = database.get_events(days=1)
        if not events:
            # Tidak ada perubahan kemarin, tidak perlu kirim rekap
            print(f"[Rekap Harian] Tidak ada perubahan stok dalam 24 jam terakhir.")
            return

        restock = [e for e in events if e.get('event_type') == 'restock']
        habis   = [e for e in events if e.get('event_type') == 'habis']
        baru    = [e for e in events if e.get('event_type') in ('new', 'baru')]
        lainnya = [e for e in events if e.get('event_type') not in ('restock', 'habis', 'new', 'baru')]

        msg = (
            f"📊 <b>REKAP STOK HARIAN</b>\n"
            f"📅 {hari_ini}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        if restock:
            msg += f"🟢 <b>Restock</b>: {len(restock)} produk\n"
        if habis:
            msg += f"🔴 <b>Habis</b>: {len(habis)} produk\n"
        if baru:
            msg += f"🆕 <b>Produk Baru</b>: {len(baru)} produk\n"
        if lainnya:
            msg += f"🔄 <b>Perubahan lain</b>: {len(lainnya)}\n"

        msg += (
            f"\n<b>Total perubahan: {len(events)}</b>\n\n"
            f"💡 Ketik /perubahan untuk detail lengkap"
        )

        users = database.get_active_users()
        sent = 0
        for u in users:
            try:
                bot.send_message(u['chat_id'], msg, parse_mode="HTML")
                sent += 1
                time.sleep(0.05)
            except Exception as e:
                print(f"Rekap harian gagal ke {u['chat_id']}: {e}")

        print(f"[Rekap Harian] Terkirim ke {sent}/{len(users)} user. "
              f"Restock={len(restock)}, Habis={len(habis)}, Baru={len(baru)}")

    except Exception as e:
        print(f"[Rekap Harian] Error: {e}")


def check_expirations_job():
    users = database.get_all_users()
    from datetime import datetime, timezone, timedelta
    from telebot import types as _types
    now = datetime.now(timezone.utc)
    for u in users:
        if u.get('role') == 'admin': continue
        
        v_str = u.get('valid_until', '2000-01-01T00:00:00Z')
        try:
            valid_until = datetime.fromisoformat(v_str.replace('Z', '+00:00'))
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
        except:
            continue
            
        time_left = valid_until - now
        kb_ext = _types.InlineKeyboardMarkup()
        kb_ext.add(_types.InlineKeyboardButton("💎 Perpanjang Sekarang (QRIS)", callback_data="open_plans"))
        
        if timedelta(hours=0) < time_left <= timedelta(hours=1):
            if u.get('reminder_sent') != '1_hour':
                try:
                    bot.send_message(
                        u['chat_id'], 
                        "⏰ <b>SISA WAKTU 1 JAM!</b>\n\nMasa aktif langganan Ventedaily Monitor kamu akan habis dalam waktu kurang dari 1 jam.\n👉 Klik tombol di bawah atau ketik /langganan agar tidak ketinggalan info restock!", 
                        parse_mode="HTML",
                        reply_markup=kb_ext
                    )
                    database.mark_expiration_reminder(u["chat_id"], v_str, u.get("reminder_sent"), "1_hour")
                except: pass
        elif timedelta(hours=1) < time_left <= timedelta(hours=24):
            if u.get('reminder_sent') not in ['1_day', '1_hour']:
                try:
                    bot.send_message(
                        u['chat_id'], 
                        "⏰ <b>PENGINGAT (H-1)</b>\n\nMasa aktif langganan Ventedaily Monitor kamu akan habis besok.\n👉 Klik tombol di bawah atau ketik /langganan untuk perpanjangan instan via QRIS.", 
                        parse_mode="HTML",
                        reply_markup=kb_ext
                    )
                    database.mark_expiration_reminder(u["chat_id"], v_str, u.get("reminder_sent"), "1_day")
                except: pass

def run_scheduler():
    import sys
    print(f"=== Ventedaily Stock Monitor Started ===")
    print(f"Python {sys.version}")
    
    # Kirim diagnostic ke admin saat bot pertama kali start
    try:
        send_admin_message(
            f"🤖 <b>Bot Starting...</b>\n"
            f"Python: {sys.version.split()[0]}\n"
            f"API: {config.BASE_URL}\n"
            f"Interval: {config.CHECK_INTERVAL} menit"
        )
    except Exception as e:
        print(f"Warning: gagal kirim startup msg: {e}")
    
    # Warmup koneksi Supabase sebelum mulai scraping
    # Ini agar request SSL pertama tidak lambat saat user pertama kali pakai bot
    print("[WARMUP] Menginisialisasi koneksi ke Supabase...")
    try:
        database.get_all_users()
        print("[WARMUP] Koneksi Supabase OK!")
    except Exception as e:
        print(f"[WARMUP] Warning: {e}")
    
    job()
    schedule.every(config.CHECK_INTERVAL).minutes.do(job)
    schedule.every().hour.do(check_expirations_job)
    schedule.every().hour.do(lambda: payments.send_h3_expiration_reminders(bot))
    schedule.every(5).minutes.do(lambda: payments.retry_admin_notifications(bot))
    if mini_app.dashboard_url():
        schedule.every().minute.do(lambda: mini_app.send_restock_alerts(bot))
    
    # Jadwalkan laporan mingguan tiap Senin jam 08:00
    def weekly_report_job():
        try:
            from commands import send_weekly_report
            active_users = database.get_active_users()
            for u in active_users:
                try:
                    send_weekly_report(bot, u['chat_id'])
                    time.sleep(1)
                except Exception as inner_e:
                    print(f"Error sending weekly report to {u['chat_id']}: {inner_e}")
        except Exception as e:
            print(f"Error sending weekly report: {e}")
    
    # Senin jam 08:00 WIB = 01:00 UTC
    schedule.every().monday.at("01:00").do(weekly_report_job)

    # Rekap harian stok — jam 08:00 WIB = 01:00 UTC
    schedule.every().day.at("01:00").do(rekap_harian_job)

    # Heartbeat harian — lapor bot masih hidup jam 12:00 WIB = 05:00 UTC
    def heartbeat_job():
        from datetime import datetime, timezone, timedelta
        wib = timezone(timedelta(hours=7))
        now_utc = datetime.now(timezone.utc)
        now_wib = now_utc.astimezone(wib).strftime('%d %b %Y %H:%M WIB')
        # Cek apakah snapshot benar-benar baru (max 30 menit lalu)
        try:
            upd_str = database.get_snapshot_updated_at()
            if upd_str:
                upd_dt = datetime.fromisoformat(upd_str.replace('Z', '+00:00'))
                age_min = int((now_utc - upd_dt).total_seconds() // 60)
                if age_min <= 30:
                    status = f"OK ({age_min} menit lalu)"
                else:
                    status = f"⚠️ Terlambat ({age_min} menit lalu)"
            else:
                status = "⚠️ Snapshot tidak tersedia"
        except Exception as e:
            status = f"⚠️ Gagal cek ({e})"
        send_admin_message(
            f"💓 <b>Bot Heartbeat</b>\n"
            f"🕐 {now_wib}\n"
            f"📦 Snapshot: {status}\n"
            f"✅ Scheduler berjalan normal"
        )
    schedule.every().day.at("05:00").do(heartbeat_job)
    
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"[SCHEDULER] Uncaught error in scheduled job: {e}")
            import traceback
            traceback.print_exc()
            try:
                send_admin_message(f"⚠️ <b>Scheduler Error</b>\n<code>{e}</code>\nBot tetap berjalan.")
            except:
                pass
        time.sleep(1)

if __name__ == "__main__":
    # Penting: daftarkan handler payments terlebih dahulu sebelum fallback/pencarian langsung di commands
    payments.register_handlers(bot)
    mini_app.register_handlers(bot)
    commands.register_handlers(bot)
    
    # Start the background job
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    
    # Start the telegram bot listener
    telegram_thread = threading.Thread(
        target=bot.infinity_polling,
        kwargs={"allowed_updates": ["message", "callback_query"]},
        name="telegram-polling",
        daemon=True,
    )
    telegram_thread.start()
    
    # Start the web server (needed for Render.com to not crash)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
