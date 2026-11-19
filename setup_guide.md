# Setup Ventedaily Stock Monitor

1. Buat bot melalui [@BotFather](https://t.me/BotFather). Mulai percakapan dengan bot dan gunakan respons `getUpdates` untuk memperoleh ID chat admin.
2. Siapkan environment variable di host bot. Isi `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SUPABASE_URL`, dan `SUPABASE_SECRET_KEY` (atau `SUPABASE_SERVICE_ROLE_KEY` untuk kunci JWT lama). Simpan kunci server di host bot. Atur `QRIS_STATIC_PAYLOAD` untuk menerima pembayaran. `PAYMENT_ADMIN_CHAT_ID` opsional dan default-nya mengikuti `TELEGRAM_CHAT_ID`.
3. Jalankan [supabase_setup.sql](supabase_setup.sql) di Supabase SQL Editor sebagai pemilik database. Skrip bisa dijalankan ulang, mempertahankan data lama, dan mengaktifkan RLS pada tabel bot. Siapkan akses kunci server sebelum menjalankannya. Untuk instalasi baru, buat paket aktif melalui `/paketadd` sebelum menerima pembayaran.
4. Jalankan `pip install -r requirements.txt` dan `python monitor.py`. Untuk deploy yang sudah berjalan, hentikan proses bot lama sebelum menjalankan migrasi, lalu nyalakan versi ini dengan konfigurasi server di atas.
5. Jalankan tes lokal dengan `python -m unittest discover -s tests`.

Jika ada invoice lama berstatus `processing`, cocokkan mutasi dan masa aktif pengguna sebelum mengubah statusnya. Versi lama bisa terhenti setelah menambah masa aktif. Invoice yang menunggu verifikasi dan belum memiliki penanda pengiriman dapat memunculkan pengingat admin setelah deploy.
