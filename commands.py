import json
import os
import re
from datetime import datetime
import telebot

import database
import calculator
import config

DATA_FILE = 'data_snapshot.json'

def register_handlers(bot):
    
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        help_text = (
            "🤖 *VENTEDAILY STOCK MONITOR*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📖 *DAFTAR PERINTAH:*\n\n"
            "🔍 `/cari [nama]`\n"
            "   Cari produk ready berdasarkan nama\n\n"
            "🆕 `/baru` atau `/baru [hari]`\n"
            "   Lihat produk baru (default: hari ini)\n\n"
            "🔄 `/restock` atau `/restock [hari]`\n"
            "   Lihat produk restock (default: hari ini)\n\n"
            "🧮 `/hitung [nama]` atau `/hitung [nama] [profit]`\n"
            "   Kalkulator harga jual marketplace\n\n"
            "🏪 `/setmp [marketplace]`\n"
            "   Set default marketplace (shopee/tokopedia/tiktok/lazada)\n\n"
            "📋 `/katalog` atau `/katalog [nama]`\n"
            "   Generate katalog siap copas ke WA\n\n"
            "🟢 `/status` — Cek status server\n"
            "❓ `/help` — Tampilkan menu ini\n"
        )
        bot.reply_to(message, help_text, parse_mode="Markdown")

    @bot.message_handler(commands=['status', 'cek'])
    def handle_status(message):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            mod_time = os.path.getmtime(DATA_FILE)
            last_check = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
            total_items = len(data)
            reply = f"🟢 <b>Server Berjalan Normal!</b>\n\n📦 Total Produk Dipantau: <b>{total_items}</b>\n⏱️ Pengecekan Terakhir: <b>{last_check}</b>"
        else:
            reply = "🟡 Server berjalan, tetapi belum ada data produk yang diambil (sedang diproses)."
        bot.reply_to(message, reply, parse_mode="HTML")

    @bot.message_handler(commands=['cari'])
    def handle_cari(message):
        query = message.text.replace('/cari', '').strip().lower()
        if not query:
            bot.reply_to(message, "⚠️ Gunakan format: `/cari [nama produk]`\nContoh: `/cari ziva`", parse_mode="Markdown")
            return
            
        if not os.path.exists(DATA_FILE):
            bot.reply_to(message, "⚠️ Data produk belum tersedia. Menunggu snapshot pertama selesai.")
            return
            
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        results = [(k, v) for k, v in data.items() if query in k.lower()]
        
        if not results:
            bot.reply_to(message, f"😔 Tidak menemukan produk dengan nama *{query}*.", parse_mode="Markdown")
            return
            
        ready = [(k, v) for k, v in results if v['stock'].lower() in ['aman', 'ready']]
        habis = [(k, v) for k, v in results if v['stock'].lower() == 'habis']
        
        # Format the response
        reply = f"🔍 <b>Hasil Pencarian: \"{query}\"</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if ready:
            reply += f"✅ <b>READY / AMAN ({len(ready)} produk):</b>\n\n"
            count = 1
            for k, v in ready:
                icon = "✅" if v['stock'].lower() == "aman" else "📦"
                reply += f"{count}. <b>{k}</b>\n   Stok: {icon} {v['stock']}\n   Harga: {v['harga']}\n\n"
                count += 1
                if len(reply) > 3500: # Telegram limit safe threshold
                    reply += "<i>... terpotong (terlalu banyak data) ...</i>\n\n"
                    break
        else:
            reply += f"😔 <b>Semua produk \"{query}\" sedang HABIS.</b>\n\n"
            
        if habis:
            reply += f"━━━━━━━━━━━━━━━━━━━━━\n❌ <b>Habis ({len(habis)} produk):</b>\n"
            habis_names = [k for k, v in habis]
            habis_text = ", ".join(habis_names[:10])
            if len(habis_names) > 10:
                habis_text += f", dan {len(habis_names)-10} lainnya."
            reply += f"{habis_text}\n"
            
        reply += f"\n📊 Total ditemukan: {len(results)}\n✅ Ready/Aman: {len(ready)} | ❌ Habis: {len(habis)}"
        
        bot.reply_to(message, reply, parse_mode="HTML")

    @bot.message_handler(commands=['baru'])
    def handle_baru(message):
        args = message.text.split()
        days = 1
        if len(args) > 1 and args[1].isdigit():
            days = int(args[1])
            
        events = database.get_events('new', days)
        
        if not events:
            bot.reply_to(message, f"Belum ada produk baru dalam {days} hari terakhir.")
            return
            
        reply = f"🆕 <b>PRODUK BARU ({days} hari terakhir)</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for idx, ev in enumerate(events[:20]):
            reply += f"{idx+1}. <b>{ev['nama']}</b>\n   Stok: {ev['stock']} | Harga: {ev['harga']}\n\n"
            
        if len(events) > 20:
            reply += f"<i>... dan {len(events)-20} produk lainnya.</i>"
            
        bot.reply_to(message, reply, parse_mode="HTML")

    @bot.message_handler(commands=['restock'])
    def handle_restock(message):
        args = message.text.split()
        days = 1
        if len(args) > 1 and args[1].isdigit():
            days = int(args[1])
            
        events = database.get_events('restock', days)
        
        if not events:
            bot.reply_to(message, f"Belum ada produk restock dalam {days} hari terakhir.")
            return
            
        reply = f"🔄 <b>PRODUK RESTOCK ({days} hari terakhir)</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for idx, ev in enumerate(events[:20]):
            reply += f"{idx+1}. <b>{ev['nama']}</b>\n   Stok: {ev['stock']} | Harga: {ev['harga']}\n\n"
            
        if len(events) > 20:
            reply += f"<i>... dan {len(events)-20} produk lainnya.</i>"
            
        bot.reply_to(message, reply, parse_mode="HTML")

    @bot.message_handler(commands=['setmp'])
    def handle_setmp(message):
        chat_id = message.chat.id
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "Gunakan: `/setmp [shopee/tokopedia/tiktok/lazada]`\nContoh: `/setmp tokopedia`", parse_mode="Markdown")
            return
            
        mp_key = args[1].lower()
        if mp_key in calculator.MARKETPLACES:
            fee = calculator.MARKETPLACES[mp_key]['fee']
            database.set_user_marketplace(chat_id, mp_key, fee)
            bot.reply_to(message, f"✅ Default marketplace disimpan: <b>{calculator.MARKETPLACES[mp_key]['name']} (Fee {fee}%)</b>", parse_mode="HTML")
        else:
            bot.reply_to(message, "❌ Marketplace tidak dikenal. Pilihan: shopee, tokopedia, tiktok, lazada.")

    @bot.message_handler(commands=['hitung'])
    def handle_hitung(message):
        chat_id = message.chat.id
        args = message.text.split()
        
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Gunakan format: `/hitung [nama produk]` atau `/hitung [nama produk] [profit]`\nContoh: `/hitung ziva`", parse_mode="Markdown")
            return
            
        if not os.path.exists(DATA_FILE):
            bot.reply_to(message, "Data produk belum tersedia.")
            return
            
        # Parse arguments (check if last arg is profit)
        profit_str = None
        profit_val = 0
        profit_type = 'percent'
        
        last_arg = args[-1]
        if '%' in last_arg or last_arg.isdigit():
            profit_str = last_arg
            query = " ".join(args[1:-1]).strip().lower()
            if '%' in profit_str:
                profit_type = 'percent'
                profit_val = float(profit_str.replace('%',''))
            else:
                profit_type = 'nominal'
                profit_val = float(profit_str)
        else:
            query = " ".join(args[1:]).strip().lower()
            
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Cari produk exact match atau best match
        matches = [(k, v) for k, v in data.items() if query in k.lower()]
        if not matches:
            bot.reply_to(message, f"❌ Produk '{query}' tidak ditemukan.")
            return
            
        target_product_name, target_product_data = matches[0] # Ambil hasil pertama
        
        # Ambil user settings
        settings = database.get_user_settings(chat_id)
        
        if profit_str is None:
            # Gunakan profit dari setting
            profit_type = settings.get('markup_type', 'percent')
            profit_val = settings.get('markup_value', 0)
            
        mp_key = settings.get('marketplace', 'shopee')
        mp_name = calculator.MARKETPLACES.get(mp_key, calculator.MARKETPLACES['shopee'])['name']
        admin_fee = float(settings.get('admin_fee', 6.5))
        
        calc_result = calculator.calculate_price(target_product_data['harga'], profit_val, profit_type, admin_fee)
        
        if not calc_result:
            bot.reply_to(message, "❌ Gagal menghitung harga.")
            return
            
        profit_label = f"{profit_val}%" if profit_type == 'percent' else f"Rp {int(profit_val):,}".replace(",", ".")
        
        reply = (
            f"🧮 <b>HASIL KALKULASI ({mp_name})</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 <b>{target_product_name}</b>\n\n"
            f"💰 Harga Modal:       {target_product_data['harga']}\n"
            f"💵 Target Profit:     {calculator.format_price(calc_result['profit_target'])}\n"
            f"📊 Subtotal:          {calculator.format_price(calc_result['modal'] + calc_result['profit_target'])}\n"
            f"🏪 Admin {admin_fee}%:   {calculator.format_price(calc_result['admin_fee'])}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ <b>Harga Jual:       {calculator.format_price(calc_result['harga_jual'])}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💡 Bersih kamu terima: {calculator.format_price(calc_result['net_received'])}\n"
            f"✅ Profit aktual:  {calculator.format_price(calc_result['actual_profit'])}\n"
        )
        
        bot.reply_to(message, reply, parse_mode="HTML")

    @bot.message_handler(commands=['katalog'])
    def handle_katalog(message):
        query = message.text.replace('/katalog', '').strip().lower()
        
        if not os.path.exists(DATA_FILE):
            bot.reply_to(message, "Data produk belum tersedia.")
            return
            
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        if query:
            results = [(k, v) for k, v in data.items() if query in k.lower()]
        else:
            results = [(k, v) for k, v in data.items()]
            
        ready = [(k, v) for k, v in results if v['stock'].lower() in ['aman', 'ready']]
        
        if not ready:
            bot.reply_to(message, "Tidak ada produk ready untuk query tersebut.")
            return
            
        reply = "🛍️ <b>KATALOG VENTEDAILY</b>\n"
        reply += f"📅 {datetime.now().strftime('%d %b %Y | %H:%M WIB')}\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        count = 1
        for k, v in ready:
            reply += f"• {k} ✅ — {v['harga']}\n"
            count += 1
            if len(reply) > 3500:
                break
                
        reply += f"\n━━━━━━━━━━━━━━━━━━━━━\n📦 Total Ready: {len(ready)} produk\n📱 Order: 08xx-xxxx-xxxx"
        bot.reply_to(message, reply, parse_mode="HTML")
