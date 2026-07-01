import requests
from bs4 import BeautifulSoup
import json
import time
import schedule
import os
import re
from datetime import datetime
import threading
from flask import Flask
import telebot
import config
import database
import commands
DATA_FILE = 'data_snapshot.json'

app = Flask(__name__)
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)

@app.route('/')
def home():
    return "Bot Monitoring Ventedaily Sedang Berjalan 24/7!"

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
        except Exception as e:
            print(f"Error sending to {u.get('chat_id')}: {e}")



def scrape_page(page):
    url = f"{config.BASE_URL}?page={page}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching page {page}: {e}")
        return None, False

    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    if not table: return None, False
        
    tbody = table.find('tbody')
    if not tbody: return None, False
        
    rows = tbody.find_all('tr')
    if not rows: return [], False
        
    data = {}
    for row in rows:
        cols = row.find_all(['th', 'td'])
        if len(cols) >= 4:
            no = cols[0].get_text(strip=True)
            nama = cols[1].get_text(strip=True)
            stock = cols[2].get_text(strip=True)
            harga = cols[3].get_text(strip=True)
            
            if not nama: continue
            data[nama] = {"no": no, "stock": stock, "harga": harga}
            
    has_next = True
    pagination_text = soup.find(string=re.compile(r'Showing \d+ to \d+ of \d+ entries'))
    if pagination_text:
        match = re.search(r'Showing (\d+) to (\d+) of (\d+) entries', pagination_text)
        if match:
            to_entry = int(match.group(2))
            total_entry = int(match.group(3))
            if to_entry >= total_entry: has_next = False
    else:
        has_next = len(data) >= 10
        
    return data, has_next

def scrape_all():
    all_data = {}
    page = 1
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Mulai scraping data...")
    while True:
        data, has_next = scrape_page(page)
        if data is None: break
        all_data.update(data)
        if not has_next: break
        page += 1
        time.sleep(0.1)
        
    print(f"Selesai! Berhasil mengambil {len(all_data)} produk dari {page} halaman.")
    return all_data

def compare_data(old_data, new_data):
    changes = []
    for prod_nama, new_item in new_data.items():
        if prod_nama not in old_data:
            changes.append(f"🟢 <b>PRODUK BARU</b>\nNama: {prod_nama}\nStok: {new_item['stock']}\nHarga: {new_item['harga']}")
            database.log_event(prod_nama, 'new', new_item['stock'], new_item['harga'])
        else:
            old_item = old_data[prod_nama]
            prod_changes = []
            
            if old_item['stock'] != new_item['stock']:
                old_st = old_item['stock'].lower()
                new_st = new_item['stock'].lower()
                icon = "🔄"
                if "habis" in old_st and ("ready" in new_st or "aman" in new_st): 
                    icon = "✅"
                    database.log_event(prod_nama, 'restock', new_item['stock'], new_item['harga'])
                elif ("ready" in old_st or "aman" in old_st) and "habis" in new_st: 
                    icon = "❌"
                    database.log_event(prod_nama, 'habis', old_item['stock'], new_item['harga'])
                prod_changes.append(f"{icon} Stok: {old_item['stock']} ➡️ <b>{new_item['stock']}</b>")
                
            if old_item['harga'] != new_item['harga']:
                prod_changes.append(f"💰 Harga: {old_item['harga']} ➡️ {new_item['harga']}")
                database.log_event(prod_nama, 'price_change', old_item['harga'], new_item['harga'])
                
            if prod_changes:
                msg = f"⚠️ <b>PERUBAHAN DATA</b>\n{prod_nama}\n" + "\n".join(prod_changes)
                changes.append(msg)
                
    for prod_nama, old_item in old_data.items():
        if prod_nama not in new_data:
            changes.append(f"🔴 <b>PRODUK DIHAPUS</b>\nNama: {prod_nama}")
            
    return changes

def job():
    try:
        new_data = scrape_all()
        if not new_data: return

        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            
            # Deteksi jika file masih menggunakan format lama (ID berupa angka)
            if old_data and list(old_data.keys())[0].isdigit():
                print("Format lama terdeteksi. Membuat ulang snapshot.")
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
            else:
                send_admin_message("🤖 <b>Format Data Diperbarui!</b>\nBerhasil mengambil snapshot dengan format baru (berdasarkan Nama Produk). Sistem siap memonitor perubahan.")
        else:
            send_admin_message("🤖 <b>Bot Monitoring Ventedaily Aktif!</b>\nBerhasil mengambil snapshot awal. Sistem akan mulai memonitor perubahan.")
            
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=4, ensure_ascii=False)
            
    except Exception as e:
        print(f"Error during job execution: {e}")

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
    print(f"=== Ventedaily Stock Monitor Started ===")
    job()
    schedule.every(config.CHECK_INTERVAL).minutes.do(job)
    schedule.every().hour.do(check_expirations_job)
    
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
    
    # Start the background job
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    
    # Start the telegram bot listener
    t2 = threading.Thread(target=lambda: bot.infinity_polling(), daemon=True)
    t2.start()
    
    # Start the web server (needed for Render.com to not crash)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
