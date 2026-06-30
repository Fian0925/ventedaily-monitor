import requests
from bs4 import BeautifulSoup
import json
import time
import schedule
import os
import re
from datetime import datetime
import config

DATA_FILE = 'data_snapshot.json'

def send_telegram_message(message):
    """Mengirim pesan ke Telegram Bot."""
    if config.TELEGRAM_BOT_TOKEN == "GANTI_DENGAN_TOKEN_BOT_KAMU" or config.TELEGRAM_CHAT_ID == "GANTI_DENGAN_CHAT_ID_KAMU":
        print("Pesan (Tidak terkirim, token belum diatur):", message)
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error sending telegram message: {e}")

def scrape_page(page):
    """Mengekstrak data dari satu halaman."""
    url = f"{config.BASE_URL}?page={page}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching page {page}: {e}")
        return None, False

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Cari tabel
    table = soup.find('table')
    if not table:
        return None, False
        
    tbody = table.find('tbody')
    if not tbody:
        return None, False
        
    rows = tbody.find_all('tr')
    if not rows:
        return [], False
        
    data = {}
    for row in rows:
        # Expected columns: No, Nama, Stock, Harga Jual
        cols = row.find_all(['th', 'td'])
        if len(cols) >= 4:
            no = cols[0].get_text(strip=True)
            nama = cols[1].get_text(strip=True)
            stock = cols[2].get_text(strip=True)
            harga = cols[3].get_text(strip=True)
            
            # Jika No kosong atau bukan angka, lewati
            if not no.isdigit():
                continue
                
            data[no] = {
                "nama": nama,
                "stock": stock,
                "harga": harga
            }
            
    # Cek apakah ini halaman terakhir dengan melihat pagination info (Showing X to Y of Z)
    has_next = True
    pagination_text = soup.find(string=re.compile(r'Showing \d+ to \d+ of \d+ entries'))
    if pagination_text:
        match = re.search(r'Showing (\d+) to (\d+) of (\d+) entries', pagination_text)
        if match:
            to_entry = int(match.group(2))
            total_entry = int(match.group(3))
            if to_entry >= total_entry:
                has_next = False
    else:
        # Fallback jika teks tidak ditemukan
        has_next = len(data) >= 10
        
    return data, has_next

def scrape_all():
    """Mengekstrak seluruh data produk dari semua halaman."""
    all_data = {}
    page = 1
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Mulai scraping data...")
    while True:
        data, has_next = scrape_page(page)
        
        if data is None:
            break
            
        all_data.update(data)
        
        if not has_next:
            break
            
        page += 1
        time.sleep(0.1)  # Jeda sebentar agar tidak membebani server
        
    print(f"Selesai! Berhasil mengambil {len(all_data)} produk dari {page} halaman.")
    return all_data

def compare_data(old_data, new_data):
    """Membandingkan data lama dan baru, lalu mengembalikan list pesan perubahan."""
    changes = []
    
    # Cek produk baru dan perubahan data
    for prod_id, new_item in new_data.items():
        if prod_id not in old_data:
            changes.append(f"🟢 <b>PRODUK BARU</b>\nID: {prod_id}\nNama: {new_item['nama']}\nStok: {new_item['stock']}\nHarga: {new_item['harga']}")
        else:
            old_item = old_data[prod_id]
            prod_changes = []
            
            if old_item['stock'] != new_item['stock']:
                # Emoji berdasarkan status
                old_st = old_item['stock'].lower()
                new_st = new_item['stock'].lower()
                icon = "🔄"
                if "habis" in old_st and ("ready" in new_st or "aman" in new_st):
                    icon = "✅" # Restock
                elif ("ready" in old_st or "aman" in old_st) and "habis" in new_st:
                    icon = "❌" # Sold out
                
                prod_changes.append(f"{icon} Stok: {old_item['stock']} ➡️ <b>{new_item['stock']}</b>")
                
            if old_item['harga'] != new_item['harga']:
                prod_changes.append(f"💰 Harga: {old_item['harga']} ➡️ {new_item['harga']}")
                
            if old_item['nama'] != new_item['nama']:
                prod_changes.append(f"📝 Nama: {old_item['nama']} ➡️ {new_item['nama']}")
                
            if prod_changes:
                msg = f"⚠️ <b>PERUBAHAN DATA</b>\nID: {prod_id} | {new_item['nama']}\n" + "\n".join(prod_changes)
                changes.append(msg)
                
    # Cek produk dihapus
    for prod_id, old_item in old_data.items():
        if prod_id not in new_data:
            changes.append(f"🔴 <b>PRODUK DIHAPUS</b>\nID: {prod_id}\nNama: {old_item['nama']}")
            
    return changes

def job():
    """Fungsi utama yang dijalankan setiap interval."""
    try:
        new_data = scrape_all()
        if not new_data:
            print("Gagal mengambil data, membatalkan pengecekan kali ini.")
            return

        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                
            changes = compare_data(old_data, new_data)
            
            if changes:
                print(f"Ditemukan {len(changes)} perubahan!")
                # Kirim notifikasi (batasi max 10 pesan agar tidak spamming jika perubahan terlalu masif)
                max_msgs = min(len(changes), 10)
                for msg in changes[:max_msgs]:
                    send_telegram_message(msg)
                    time.sleep(1) # Jeda antar pesan Telegram
                    
                if len(changes) > 10:
                    send_telegram_message(f"ℹ️ <i>Dan {len(changes) - 10} perubahan lainnya tidak ditampilkan untuk mencegah spam...</i>")
            else:
                print("Tidak ada perubahan.")
        else:
            print("Snapshot pertama dibuat. Belum ada perbandingan.")
            send_telegram_message("🤖 <b>Bot Monitoring Ventedaily Aktif!</b>\nBerhasil mengambil snapshot awal. Sistem akan mulai memonitor perubahan.")

        # Simpan snapshot terbaru
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=4, ensure_ascii=False)
            
    except Exception as e:
        print(f"Error during job execution: {e}")

if __name__ == "__main__":
    print(f"=== Ventedaily Stock Monitor Started ===")
    print(f"Interval: Setiap {config.CHECK_INTERVAL} menit")
    
    # Jalankan sekali saat start
    job()
    
    # Jadwalkan running berikutnya
    schedule.every(config.CHECK_INTERVAL).minutes.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
