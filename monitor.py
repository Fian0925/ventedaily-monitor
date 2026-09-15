import requests
import json
import time
import schedule
import os
from datetime import datetime
import threading
from flask import Flask, send_file, make_response
import telebot
import config
import database
import commands
import payments
DATA_FILE = 'data_snapshot.json'

API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "VentedailyMonitorBot/2.0",
}

app = Flask(__name__)
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)

@app.route('/')
def home():
    return "Bot Monitoring Ventedaily Sedang Berjalan 24/7!"

@app.route('/api/data')
def get_data():
    if os.path.exists(DATA_FILE):
        response = make_response(send_file(DATA_FILE, mimetype='application/json'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
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


def fetch_all():
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
    if stock_items:
        for s in stock_items:
            pn = s.get("product_name", "").strip()
            # Hapus prefix defect
            pn = pn.replace("[DEFECT]", "").replace("(Defect)", "").strip()
            color = s.get("color", "").strip()
            price_key = f"{pn.lower()} {color.lower()}".strip()
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
    price_miss = 0
    for item in catalog_items:
        nama = item.get("name", "").strip()
        status = item.get("status", "").strip()
        selling_price = item.get("selling_price", 0)

        if not nama:
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

        if reseller_price > 0:
            harga = f"Rp {reseller_price:,}".replace(",", ".")
        else:
            # Fallback ke selling_price dari catalog
            harga = f"Rp {selling_price:,}".replace(",", ".")
            price_miss += 1

        data[nama] = {"stock": status, "harga": harga}

    if price_miss > 0:
        print(f"  Info: {price_miss} produk pakai harga fallback (tidak cocok di stock)")
    print(f"Selesai! Total {len(data)} varian dari catalog, {len(price_lookup)} harga reseller dari stock.")
    return data


def compare_data(old_data, new_data):
    changes = []
    MAX_LOG_EVENTS = 50  # Batasi agar tidak freeze saat ratusan produk berubah
    logged = 0
    
    for prod_nama, new_item in new_data.items():
        if prod_nama not in old_data:
            changes.append(
                f"🟢 <b>PRODUK BARU</b>\n"
                f"🏷️ {prod_nama}\n"
                f"Stok: {new_item['stock']} | Harga: {new_item['harga']}"
            )
            if logged < MAX_LOG_EVENTS:
                database.log_event(prod_nama, 'new', new_item['stock'], new_item['harga'])
                logged += 1
        else:
            old_item = old_data[prod_nama]
            prod_changes = []
            
            if old_item['stock'] != new_item['stock']:
                old_st = old_item['stock'].lower()
                new_st = new_item['stock'].lower()
                icon = "🔄"
                if "habis" in old_st and ("ready" in new_st or "aman" in new_st): 
                    icon = "✔️"
                    if logged < MAX_LOG_EVENTS:
                        database.log_event(prod_nama, 'restock', new_item['stock'], new_item['harga'])
                        logged += 1
                elif ("ready" in old_st or "aman" in old_st) and "habis" in new_st: 
                    icon = "❌"
                    if logged < MAX_LOG_EVENTS:
                        database.log_event(prod_nama, 'habis', old_item['stock'], new_item['harga'])
                        logged += 1
                prod_changes.append(f"{icon} Stok: {old_item['stock']} ➡️ <b>{new_item['stock']}</b>")
                
            if old_item['harga'] != new_item['harga']:
                prod_changes.append(f"💰 Harga: {old_item['harga']} ➡️ {new_item['harga']}")
                if logged < MAX_LOG_EVENTS:
                    database.log_event(prod_nama, 'price_change', old_item['harga'], new_item['harga'])
                    logged += 1
                
            if prod_changes:
                msg = f"⚠️ <b>PERUBAHAN DATA</b>\n🏷️ {prod_nama}\n" + "\n".join(prod_changes)
                changes.append(msg)
                
    for prod_nama, old_item in old_data.items():
        if prod_nama not in new_data:
            changes.append(f"🔴 <b>PRODUK DIHAPUS</b>\n🏷️ {prod_nama}")
            
    return changes


def job():
    from datetime import timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now_str = datetime.now(wib).strftime('%H:%M:%S WIB')
    
    try:
        new_data = fetch_all()
        if not new_data:
            msg = f"⚠️ [{now_str}] fetch_all() gagal — return None. Siklus dilewati."
            print(msg)
            send_admin_message(msg)
            return

        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
            except Exception as e:
                old_data = None
                send_admin_message(f"⚠️ [{now_str}] Gagal baca snapshot: {e}")

            # Deteksi format lama (key numerik atau value berisi field 'nama')
            if old_data:
                sample_key = next(iter(old_data), "")
                sample_val = old_data.get(sample_key, {})
                is_old_format = sample_key.isdigit() or "nama" in sample_val
                
                if is_old_format:
                    print("Format snapshot lama terdeteksi. Membuat snapshot baru.")
                    send_admin_message(
                        f"🔄 [{now_str}] <b>Migrasi Data</b>\n"
                        f"Snapshot lama: {len(old_data)} key (format lama)\n"
                        f"Data API baru: {len(new_data)} varian\n"
                        f"Menyimpan snapshot baru. Perbandingan dimulai siklus berikutnya."
                    )
                    old_data = None

            if old_data:
                changes = compare_data(old_data, new_data)
                
                if changes:
                    max_msgs = min(len(changes), 10)
                    for msg in changes[:max_msgs]:
                        broadcast_telegram_message(msg)
                        time.sleep(1)
                        
                    if len(changes) > 10:
                        broadcast_telegram_message(f"ℹ️ <i>Dan {len(changes) - 10} perubahan lainnya tidak ditampilkan...</i>")
                    
                    send_admin_message(f"📊 [{now_str}] Job selesai: {len(changes)} perubahan terdeteksi, {max_msgs} dikirim.")
                else:
                    send_admin_message(f"✅ [{now_str}] Job OK: 0 perubahan. Snapshot: {len(old_data)}→{len(new_data)}")
            else:
                send_admin_message(
                    f"🤖 [{now_str}] <b>Snapshot baru disimpan</b>\n"
                    f"Total: {len(new_data)} varian produk.\n"
                    f"Perbandingan dimulai siklus berikutnya."
                )
        else:
            send_admin_message(
                f"🤖 [{now_str}] <b>Snapshot awal dibuat</b>\n"
                f"Total: {len(new_data)} varian produk.\n"
                f"Monitoring dimulai siklus berikutnya."
            )
            
        temp_file = f"{DATA_FILE}.tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, DATA_FILE)
            
    except Exception as e:
        print(f"Error during job execution: {e}")
        import traceback
        traceback.print_exc()
        try:
            send_admin_message(f"⚠️ [{now_str}] <b>ERROR job():</b>\n<code>{e}</code>")
        except:
            pass

def check_expirations_job():
    users = database.get_all_users()
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    for u in users:
        if u.get('role') == 'admin': continue
        
        v_str = u.get('valid_until', '2000-01-01T00:00:00Z')
        try:
            valid_until = datetime.fromisoformat(v_str.replace('Z', '+00:00'))
        except:
            continue
            
        time_left = valid_until - now
        
        if timedelta(hours=0) < time_left <= timedelta(hours=1):
            if u.get('reminder_sent') != '1_hour':
                try:
                    bot.send_message(
                        u['chat_id'], 
                        "⏰ <b>SISA WAKTU 1 JAM!</b>\n\nMasa aktif langganan Ventedaily Monitor kamu akan habis dalam waktu kurang dari 1 jam.\n👉 Segera hubungi Admin untuk perpanjangan agar tidak ketinggalan info restock!", 
                        parse_mode="HTML"
                    )
                    database.update_subscription(u['chat_id'], u.get('plan_type','pro'), v_str, "1_hour")
                except: pass
        elif timedelta(hours=1) < time_left <= timedelta(hours=24):
            if u.get('reminder_sent') not in ['1_day', '1_hour']:
                try:
                    bot.send_message(
                        u['chat_id'], 
                        "⏰ <b>PENGINGAT (H-1)</b>\n\nMasa aktif langganan Ventedaily Monitor kamu akan habis besok.\n👉 Hubungi Admin untuk perpanjangan paket kamu.", 
                        parse_mode="HTML"
                    )
                    database.update_subscription(u['chat_id'], u.get('plan_type','pro'), v_str, "1_day")
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
    
    schedule.every().monday.at("08:00").do(weekly_report_job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    commands.register_handlers(bot)
    payments.register_handlers(bot)
    
    # Start the background job
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    
    # Start the telegram bot listener
    t2 = threading.Thread(target=lambda: bot.infinity_polling(), daemon=True)
    t2.start()
    
    # Start the web server (needed for Render.com to not crash)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
