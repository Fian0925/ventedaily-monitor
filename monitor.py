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

def send_telegram_message(message):
    if config.TELEGRAM_BOT_TOKEN == "GANTI_DENGAN_TOKEN_BOT_KAMU" or config.TELEGRAM_CHAT_ID == "GANTI_DENGAN_CHAT_ID_KAMU":
        print("Pesan (Tidak terkirim, token belum diatur):", message)
        return
    
    try:
        bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=message, parse_mode="HTML")
    except Exception as e:
        print(f"Error sending telegram message: {e}")



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
                prod_changes.append(f"{icon} Stok: {old_item['stock']} ➡️ <b>{new_item['stock']}</b>")
                
            if old_item['harga'] != new_item['harga']:
                prod_changes.append(f"💰 Harga: {old_item['harga']} ➡️ {new_item['harga']}")
                
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
                        send_telegram_message(msg)
                        time.sleep(1)
                        
                    if len(changes) > 10:
                        send_telegram_message(f"ℹ️ <i>Dan {len(changes) - 10} perubahan lainnya tidak ditampilkan...</i>")
            else:
                send_telegram_message("🤖 <b>Format Data Diperbarui!</b>\nBerhasil mengambil snapshot dengan format baru (berdasarkan Nama Produk). Sistem siap memonitor perubahan.")
        else:
            send_telegram_message("🤖 <b>Bot Monitoring Ventedaily Aktif!</b>\nBerhasil mengambil snapshot awal. Sistem akan mulai memonitor perubahan.")

        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=4, ensure_ascii=False)
            
    except Exception as e:
        print(f"Error during job execution: {e}")

def run_scheduler():
    print(f"=== Ventedaily Stock Monitor Started ===")
    job()
    schedule.every(config.CHECK_INTERVAL).minutes.do(job)
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
