# Panduan Setup Ventedaily Stock Monitor

## Langkah 1: Buat Telegram Bot
1. Buka Telegram dan cari **@BotFather** (atau klik link ini: https://t.me/BotFather)
2. Mulai chat dan kirim command `/newbot`
3. Masukkan nama bot yang kamu inginkan (misal: `Ventedaily Monitor`)
4. Masukkan username bot (harus berakhiran "bot", misal: `ventedaily_monitor_bot`)
5. BotFather akan memberikan pesan berisi **Token HTTP API**. Token ini bentuknya panjang seperti `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`. **Simpan token ini**.

## Langkah 2: Dapatkan Chat ID
1. Cari bot yang baru saja kamu buat di Telegram (berdasarkan username).
2. Mulai chat dengan bot tersebut dan kirim pesan bebas (misal: "Halo"). Langkah ini wajib agar bot bisa membalas pesanmu.
3. Buka browser dan kunjungi URL berikut (ganti `<TOKEN>` dengan token dari langkah 1):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Kamu akan melihat respons berupa teks JSON. Cari bagian `"chat":{"id":123456789,...}`.
5. Angka `123456789` (atau serupa, bisa diawali tanda minus `-` jika itu grup) adalah **Chat ID** kamu. **Simpan Chat ID ini**.

*(Alternatif Langkah 2: Kamu juga bisa menggunakan bot pihak ketiga seperti **@userinfobot** di Telegram untuk mengetahui Chat ID-mu secara instan).*

## Langkah 3: Konfigurasi Script
1. Buka file `config.py` yang ada di dalam folder ini.
2. Ganti nilai `"GANTI_DENGAN_TOKEN_BOT_KAMU"` dengan Token dari Langkah 1.
3. Ganti nilai `"GANTI_DENGAN_CHAT_ID_KAMU"` dengan Chat ID dari Langkah 2.
4. Simpan file `config.py`.

## Langkah 4: Install Dependencies
Buka terminal/Command Prompt di folder ini, lalu jalankan:
```bash
pip install -r requirements.txt
```

## Langkah 5: Jalankan Script
Jalankan script dengan perintah:
```bash
python monitor.py
```
Script akan berjalan di terminal dan otomatis mengecek perubahan setiap 5 menit. Jangan tutup terminalnya jika ingin bot tetap memonitor!
