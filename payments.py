"""QRIS subscription + dynamic plan management for Ventedaily Monitor.

No payment gateway is used. A merchant static QRIS payload is transformed locally
into an amount-bearing QR. Payment confirmation is manual: the user submits a
claim and only the configured admin can approve it.

Secrets stay in environment variables. Subscription plans live in Supabase and
can be changed from Telegram without redeploying the bot.
"""
from __future__ import annotations

import io
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import qrcode
import requests
from telebot import types

import config
import database

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "fianfi").lstrip("@")
ADMIN_CHAT_ID = str(os.getenv("PAYMENT_ADMIN_CHAT_ID", config.TELEGRAM_CHAT_ID))
QRIS_STATIC_PAYLOAD = os.getenv("QRIS_STATIC_PAYLOAD", "").strip()
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "7"))

SUPABASE_URL = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1"
PAYMENT_DB_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)

_session = requests.Session()
if PAYMENT_DB_KEY:
    _session.headers.update(
        {
            "apikey": PAYMENT_DB_KEY,
            "Authorization": f"Bearer {PAYMENT_DB_KEY}",
            "Content-Type": "application/json",
        }
    )


# -----------------------------------------------------------------------------
# QRIS / EMV helpers
# -----------------------------------------------------------------------------
def _crc16_ccitt_false(text: str) -> str:
    crc = 0xFFFF
    for byte in text.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _parse_tlv(payload: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    pos = 0
    while pos < len(payload):
        if pos + 4 > len(payload):
            raise ValueError("Payload QRIS terpotong.")
        tag = payload[pos : pos + 2]
        length_text = payload[pos + 2 : pos + 4]
        if not length_text.isdigit():
            raise ValueError("Format panjang TLV QRIS tidak valid.")
        length = int(length_text)
        end = pos + 4 + length
        if end > len(payload):
            raise ValueError("Panjang field QRIS tidak sesuai payload.")
        out.append((tag, payload[pos + 4 : end]))
        pos = end
    return out


def _clean_qris_payload(payload: str) -> str:
    return payload.strip().replace("\r", "").replace("\n", "")


def validate_static_qris(payload: str) -> Dict[str, str]:
    payload = _clean_qris_payload(payload)
    items = _parse_tlv(payload)
    values = dict(items)
    if values.get("00") != "01":
        raise ValueError("Payload bukan EMV/QRIS merchant-presented yang didukung.")
    if "63" not in values or len(values["63"]) != 4:
        raise ValueError("CRC QRIS tidak ditemukan.")
    if payload[-8:-4] != "6304":
        raise ValueError("Field CRC QRIS harus berada di akhir payload.")
    expected = _crc16_ccitt_false(payload[:-4])
    if expected.upper() != values["63"].upper():
        raise ValueError("CRC QRIS statis tidak valid.")
    return values


def build_amount_qris(static_payload: str, amount: int) -> str:
    if amount <= 0:
        raise ValueError("Nominal harus lebih dari Rp0.")
    if amount > 10_000_000:
        raise ValueError("Nominal melebihi batas generator aplikasi.")

    static_payload = _clean_qris_payload(static_payload)
    validate_static_qris(static_payload)
    items = _parse_tlv(static_payload)

    rebuilt: List[Tuple[str, str]] = []
    inserted_amount = False
    for tag, value in items:
        if tag == "63":
            continue
        if tag == "01":
            value = "12"
        if tag == "54":
            continue
        if not inserted_amount and tag in {"58", "59", "60", "61", "62"}:
            rebuilt.append(("54", str(int(amount))))
            inserted_amount = True
        rebuilt.append((tag, value))

    if not inserted_amount:
        rebuilt.append(("54", str(int(amount))))

    body = "".join(f"{tag}{len(value):02d}{value}" for tag, value in rebuilt)
    body_with_crc = body + "6304"
    return body_with_crc + _crc16_ccitt_false(body_with_crc)


def qris_png(payload: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=9,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    out.name = "qris.png"
    return out


# -----------------------------------------------------------------------------
# Supabase REST helpers
# -----------------------------------------------------------------------------
def _require_db() -> None:
    if not PAYMENT_DB_KEY:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY / SUPABASE_SERVICE_ROLE_KEY belum diatur."
        )


def _get(path: str) -> List[dict]:
    _require_db()
    res = _session.get(f"{SUPABASE_URL}/{path}", timeout=15)
    res.raise_for_status()
    return res.json()


def _post(table: str, payload: dict, prefer: str = "return=representation") -> List[dict]:
    _require_db()
    res = _session.post(
        f"{SUPABASE_URL}/{table}",
        json=payload,
        headers={"Prefer": prefer},
        timeout=15,
    )
    res.raise_for_status()
    try:
        return res.json()
    except Exception:
        return []


def _patch(path: str, payload: dict) -> List[dict]:
    _require_db()
    res = _session.patch(
        f"{SUPABASE_URL}/{path}",
        json=payload,
        headers={"Prefer": "return=representation"},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


# -----------------------------------------------------------------------------
# Plan management
# -----------------------------------------------------------------------------
def _normalize_code(value: str) -> str:
    code = re.sub(r"[^a-z0-9_-]", "", value.strip().lower())
    if not code or len(code) > 24:
        raise ValueError("Kode paket harus 1-24 karakter: huruf kecil/angka/_/-.")
    return code


def get_active_plans() -> List[dict]:
    return _get(
        "subscription_plans?is_active=eq.true&order=sort_order.asc,duration_days.asc"
    )


def get_all_plans() -> List[dict]:
    return _get("subscription_plans?order=sort_order.asc,duration_days.asc")


def get_plan(code: str, active_only: bool = False) -> Optional[dict]:
    code = requests.utils.quote(_normalize_code(code), safe="")
    suffix = "&is_active=eq.true" if active_only else ""
    rows = _get(f"subscription_plans?code=eq.{code}{suffix}&limit=1")
    return rows[0] if rows else None


def add_plan(code: str, name: str, days: int, price: int, plan_type: str = "pro", sort_order: int = 100) -> dict:
    code = _normalize_code(code)
    name = name.strip()
    if not name:
        raise ValueError("Nama paket tidak boleh kosong.")
    if int(days) <= 0 or int(days) > 3650:
        raise ValueError("Durasi paket harus 1-3650 hari.")
    if int(price) <= 0 or int(price) > 10_000_000:
        raise ValueError("Harga paket harus Rp1 - Rp10.000.000.")
    rows = _post(
        "subscription_plans",
        {
            "code": code,
            "name": name,
            "plan_type": plan_type.strip() or "pro",
            "duration_days": int(days),
            "price": int(price),
            "is_active": True,
            "sort_order": int(sort_order),
        },
    )
    return rows[0]


def replace_plan(code: str, name: str, days: int, price: int, plan_type: str = "pro", sort_order: int = 100) -> Optional[dict]:
    code = _normalize_code(code)
    rows = _patch(
        "subscription_plans?code=eq." + requests.utils.quote(code, safe=""),
        {
            "name": name.strip(),
            "plan_type": plan_type.strip() or "pro",
            "duration_days": int(days),
            "price": int(price),
            "sort_order": int(sort_order),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return rows[0] if rows else None


def set_plan_active(code: str, active: bool) -> Optional[dict]:
    code = _normalize_code(code)
    rows = _patch(
        "subscription_plans?code=eq." + requests.utils.quote(code, safe=""),
        {
            "is_active": bool(active),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return rows[0] if rows else None


# -----------------------------------------------------------------------------
# Payment storage
# -----------------------------------------------------------------------------
def _invoice() -> str:
    date = datetime.now(timezone.utc).strftime("%y%m%d")
    return f"VD{date}-{secrets.token_hex(3).upper()}"


def get_payment(invoice: str) -> Optional[dict]:
    rows = _get(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&limit=1"
    )
    return rows[0] if rows else None


def latest_open_payment(chat_id: int | str) -> Optional[dict]:
    cid = requests.utils.quote(str(chat_id), safe="")
    rows = _get(
        "payment_requests?chat_id=eq."
        + cid
        + "&status=in.(pending,waiting_verification,processing)"
        + "&order=created_at.desc&limit=1"
    )
    return rows[0] if rows else None


def latest_payment(chat_id: int | str) -> Optional[dict]:
    cid = requests.utils.quote(str(chat_id), safe="")
    rows = _get(
        "payment_requests?chat_id=eq."
        + cid
        + "&order=created_at.desc&limit=1"
    )
    return rows[0] if rows else None


def create_payment(chat_id: int, username: Optional[str], plan: dict) -> dict:
    existing = latest_open_payment(chat_id)
    if existing:
        # Do not allow replacing an invoice already awaiting admin verification.
        if existing["status"] in {"waiting_verification", "processing"}:
            return existing
        # Reuse pending invoice only when the same plan is selected.
        if existing.get("plan_code") == plan["code"]:
            return existing
        _patch(
            "payment_requests?invoice=eq."
            + requests.utils.quote(existing["invoice"], safe="")
            + "&status=eq.pending",
            {"status": "cancelled"},
        )

    rows = _post(
        "payment_requests",
        {
            "invoice": _invoice(),
            "chat_id": str(chat_id),
            "username": username or "",
            "plan_code": plan["code"],
            "plan_name": plan["name"],
            "plan_type": plan.get("plan_type") or "pro",
            "duration_days": int(plan["duration_days"]),
            "amount": int(plan["price"]),
            "status": "pending",
        },
    )
    return rows[0]


def submit_payment(invoice: str) -> Optional[dict]:
    rows = _patch(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&status=eq.pending",
        {
            "status": "waiting_verification",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return rows[0] if rows else get_payment(invoice)


def claim_for_approval(invoice: str, admin_id: str) -> Optional[dict]:
    rows = _patch(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&status=eq.waiting_verification",
        {"status": "processing", "verified_by": str(admin_id)},
    )
    return rows[0] if rows else None


def finalize_paid(invoice: str, admin_id: str) -> Optional[dict]:
    rows = _patch(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&status=eq.processing",
        {
            "status": "paid",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": str(admin_id),
        },
    )
    return rows[0] if rows else None


def revert_to_waiting(invoice: str) -> None:
    _patch(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&status=eq.processing",
        {"status": "waiting_verification"},
    )


def reject_payment(invoice: str, admin_id: str) -> Optional[dict]:
    rows = _patch(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&status=in.(pending,waiting_verification)",
        {
            "status": "rejected",
            "rejected_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": str(admin_id),
        },
    )
    return rows[0] if rows else get_payment(invoice)


def cancel_payment(invoice: str, chat_id: int) -> Optional[dict]:
    rows = _patch(
        "payment_requests?invoice=eq."
        + requests.utils.quote(invoice, safe="")
        + "&chat_id=eq."
        + requests.utils.quote(str(chat_id), safe="")
        + "&status=eq.pending",
        {"status": "cancelled"},
    )
    return rows[0] if rows else get_payment(invoice)


def waiting_payments(limit: int = 10) -> List[dict]:
    return _get(
        f"payment_requests?status=eq.waiting_verification&order=submitted_at.asc&limit={int(limit)}"
    )


# -----------------------------------------------------------------------------
# Subscription / referral helpers
# -----------------------------------------------------------------------------
def _format_rupiah(value: int) -> str:
    return "Rp{:,.0f}".format(value).replace(",", ".")


def _subscription_info(chat_id: int | str) -> Tuple[dict, datetime, bool]:
    settings = database.get_user_settings(chat_id)
    raw = settings.get("valid_until", "2000-01-01T00:00:00Z")
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        until = datetime(2000, 1, 1, tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return settings, until, until > datetime.now(timezone.utc)


def _extend_subscription(chat_id: str, days: int, plan_type: str) -> Tuple[bool, datetime]:
    _, current_until, _ = _subscription_info(chat_id)
    now = datetime.now(timezone.utc)
    base = current_until if current_until > now else now
    new_until = base + timedelta(days=int(days))
    ok = database.update_subscription(chat_id, plan_type or "pro", new_until.isoformat(), "")
    return ok, new_until


def _reward_referral_once(paid_chat_id: str) -> None:
    try:
        settings = database.get_user_settings(paid_chat_id)
        inviter = str(settings.get("referred_by") or "").strip()
        if not inviter or inviter == str(paid_chat_id):
            return
        already = _get(
            "referral_rewards?referred_chat_id=eq."
            + requests.utils.quote(str(paid_chat_id), safe="")
            + "&limit=1"
        )
        if already:
            return

        inviter_settings, inviter_until, _ = _subscription_info(inviter)
        now = datetime.now(timezone.utc)
        base = inviter_until if inviter_until > now else now
        new_until = base + timedelta(days=REFERRAL_BONUS_DAYS)
        if not database.update_subscription(
            inviter,
            inviter_settings.get("plan_type") or "pro",
            new_until.isoformat(),
            "",
        ):
            return
        database.increment_referral_count(inviter)
        _post(
            "referral_rewards",
            {
                "referred_chat_id": str(paid_chat_id),
                "inviter_chat_id": inviter,
                "bonus_days": REFERRAL_BONUS_DAYS,
            },
        )
    except Exception as exc:
        print(f"Referral reward warning: {exc}")


def _status_label(status: str) -> str:
    return {
        "pending": "🟡 Menunggu pembayaran",
        "waiting_verification": "🕒 Menunggu verifikasi admin",
        "processing": "🔎 Sedang diverifikasi",
        "paid": "✅ Lunas",
        "rejected": "❌ Ditolak",
        "cancelled": "⚪ Dibatalkan",
    }.get(status, status)


def _support_button() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton("💬 Bantuan Admin", url=f"https://t.me/{SUPPORT_USERNAME}")


def _plan_label(plan: dict) -> str:
    return f"{plan['name']} — {_format_rupiah(int(plan['price']))}"


def _plans_markup(plans: List[dict]) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    for plan in plans:
        markup.add(
            types.InlineKeyboardButton(
                f"💳 {_plan_label(plan)}",
                callback_data=f"plan:{plan['code']}",
            )
        )
    markup.row(
        types.InlineKeyboardButton("📄 Cek Tagihan", callback_data="pay_latest"),
        _support_button(),
    )
    return markup


def _send_plans_menu(bot, chat_id: int, reply_to=None) -> None:
    settings, until, active = _subscription_info(chat_id)
    if settings.get("role") == "admin":
        text = "👑 Akun admin tidak membutuhkan langganan.\nGunakan /paketadmin untuk mengelola paket."
        if reply_to:
            bot.reply_to(reply_to, text)
        else:
            bot.send_message(chat_id, text)
        return
    plans = get_active_plans()
    if not plans:
        text = "⚠️ Belum ada paket langganan aktif. Hubungi admin."
        markup = types.InlineKeyboardMarkup().add(_support_button())
    else:
        until_text = until.astimezone(timezone(timedelta(hours=7))).strftime("%d-%m-%Y %H:%M WIB") if active else "-"
        lines = [
            "💎 <b>LANGGANAN VENTEDAILY</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"Status: {'🟢 Aktif' if active else '🔴 Belum aktif / expired'}",
            f"Aktif sampai: <b>{until_text}</b>",
            "",
            "Pilih paket:",
        ]
        for plan in plans:
            lines.append(
                f"• <b>{plan['name']}</b> — {int(plan['duration_days'])} hari — <b>{_format_rupiah(int(plan['price']))}</b>"
            )
        lines += [
            "",
            "Nominal QRIS akan terisi otomatis. Setelah membayar, tekan <b>Saya Sudah Bayar</b> dan tunggu verifikasi admin.",
        ]
        text = "\n".join(lines)
        markup = _plans_markup(plans)
    if reply_to:
        bot.reply_to(reply_to, text, parse_mode="HTML", reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _show_qris(bot, chat_id: int, payment: dict) -> None:
    if not QRIS_STATIC_PAYLOAD:
        bot.send_message(
            chat_id,
            "⚠️ QRIS belum dikonfigurasi. Hubungi admin.",
            reply_markup=types.InlineKeyboardMarkup().add(_support_button()),
        )
        return
    try:
        payload = build_amount_qris(QRIS_STATIC_PAYLOAD, int(payment["amount"]))
        image = qris_png(payload)
    except Exception as exc:
        bot.send_message(chat_id, f"⚠️ Gagal membuat QRIS: {exc}")
        return

    invoice = payment["invoice"]
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("✅ Saya Sudah Bayar", callback_data=f"pay_submit:{invoice}"))
    markup.row(
        types.InlineKeyboardButton("🔄 Cek Status", callback_data=f"pay_status:{invoice}"),
        types.InlineKeyboardButton("❌ Batalkan", callback_data=f"pay_cancel:{invoice}"),
    )
    markup.add(_support_button())

    caption = (
        "💳 <b>PEMBAYARAN LANGGANAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 Invoice: <code>{invoice}</code>\n"
        f"📦 Paket: <b>{payment.get('plan_name') or payment.get('plan_code') or 'Pro'}</b>\n"
        f"⏳ Durasi: {payment['duration_days']} hari\n"
        f"💰 Total: <b>{_format_rupiah(int(payment['amount']))}</b>\n\n"
        "Scan QR menggunakan DANA, GoPay, atau aplikasi bank yang mendukung QRIS. Nominal sudah terisi otomatis.\n\n"
        "Setelah pembayaran sukses, tekan <b>✅ Saya Sudah Bayar</b>. Akun baru aktif setelah admin mengecek dana masuk."
    )
    bot.send_photo(chat_id, image, caption=caption, parse_mode="HTML", reply_markup=markup)


def _notify_admin(bot, payment: dict) -> None:
    username = payment.get("username") or "-"
    if username != "-" and not username.startswith("@"):
        username = "@" + username
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("✅ UANG MASUK", callback_data=f"pay_approve:{payment['invoice']}"),
        types.InlineKeyboardButton("❌ TOLAK", callback_data=f"pay_reject:{payment['invoice']}"),
    )
    text = (
        "🔔 <b>PEMBAYARAN MENUNGGU VERIFIKASI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 Invoice: <code>{payment['invoice']}</code>\n"
        f"👤 User: {username}\n"
        f"🆔 Telegram ID: <code>{payment['chat_id']}</code>\n"
        f"📦 Paket: <b>{payment.get('plan_name') or payment.get('plan_code') or '-'}</b>\n"
        f"⏳ Durasi: {payment['duration_days']} hari\n"
        f"💰 Nominal: <b>{_format_rupiah(int(payment['amount']))}</b>\n\n"
        "Cek mutasi/notifikasi QRIS merchant. Tekan UANG MASUK hanya jika dana benar-benar diterima."
    )
    bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML", reply_markup=markup)


def _set_command_menu(bot) -> None:
    commands = [
        types.BotCommand("start", "Mulai / buka menu"),
        types.BotCommand("cari", "Cari stok produk"),
        types.BotCommand("baru", "Produk baru"),
        types.BotCommand("restock", "Produk restock"),
        types.BotCommand("hitung", "Kalkulator harga jual"),
        types.BotCommand("katalog", "Buat katalog"),
        types.BotCommand("perubahan", "Laporan harian perubahan stok"),
        types.BotCommand("laporan", "Laporan mingguan"),
        types.BotCommand("trial", "Coba gratis 5 hari"),
        types.BotCommand("profil", "Status akun & masa aktif"),
        types.BotCommand("langganan", "Pilih / perpanjang paket"),
        types.BotCommand("tagihan", "Cek status pembayaran"),
        types.BotCommand("pengaturan", "Pengaturan akun"),
        types.BotCommand("status", "Status server"),
        types.BotCommand("help", "Bantuan"),
    ]
    try:
        bot.set_my_commands(commands)
    except Exception as exc:
        print(f"Warning set_my_commands: {exc}")


def _is_admin_user(user_id: int | str) -> bool:
    return str(user_id) == ADMIN_CHAT_ID


def _admin_plan_text(plans: List[dict]) -> str:
    if not plans:
        return "📦 Belum ada paket."
    lines = ["🛠 <b>KELOLA PAKET LANGGANAN</b>", "━━━━━━━━━━━━━━━━━━━━━"]
    for p in plans:
        state = "🟢" if p.get("is_active") else "⚫"
        lines.append(
            f"{state} <code>{p['code']}</code> — <b>{p['name']}</b>\n"
            f"   {p['duration_days']} hari | {_format_rupiah(int(p['price']))} | type={p.get('plan_type','pro')}"
        )
    lines += [
        "",
        "Tambah:",
        "<code>/paketadd kode|Nama Paket|hari|harga</code>",
        "Contoh: <code>/paketadd pro90|Pro 90 Hari|90|65000</code>",
        "",
        "Ubah:",
        "<code>/paketedit kode|Nama Paket|hari|harga</code>",
        "",
        "Aktif/nonaktif:",
        "<code>/paketon kode</code> / <code>/paketoff kode</code>",
        "",
        "Paket nonaktif tidak tampil ke user, tetapi histori invoice lama tetap aman.",
    ]
    return "\n".join(lines)


def register_handlers(bot) -> None:
    _set_command_menu(bot)

    @bot.message_handler(commands=["langganan", "subscribe", "bayar"])
    def handle_subscription(message):
        try:
            _send_plans_menu(bot, message.chat.id, reply_to=message)
        except Exception as exc:
            bot.reply_to(message, f"⚠️ Gagal membuka paket: {exc}")

    @bot.message_handler(commands=["tagihan"])
    def handle_invoice(message):
        try:
            payment = latest_payment(message.chat.id)
        except Exception as exc:
            bot.reply_to(message, f"⚠️ Gagal membaca tagihan: {exc}")
            return
        if not payment:
            bot.reply_to(message, "📭 Belum ada tagihan.")
            return
        markup = types.InlineKeyboardMarkup()
        if payment["status"] == "pending":
            markup.add(types.InlineKeyboardButton("💳 Tampilkan QRIS", callback_data=f"pay_qr:{payment['invoice']}"))
        markup.add(types.InlineKeyboardButton("🔄 Cek Status", callback_data=f"pay_status:{payment['invoice']}"))
        bot.reply_to(
            message,
            f"📄 <code>{payment['invoice']}</code>\n"
            f"Paket: <b>{payment.get('plan_name') or payment.get('plan_code') or '-'}</b>\n"
            f"Total: <b>{_format_rupiah(int(payment['amount']))}</b>\n"
            f"Status: {_status_label(payment['status'])}",
            parse_mode="HTML",
            reply_markup=markup,
        )

    @bot.message_handler(commands=["payments"])
    def handle_admin_payments(message):
        if not _is_admin_user(message.chat.id):
            return
        try:
            rows = waiting_payments(15)
        except Exception as exc:
            bot.reply_to(message, f"⚠️ Gagal membaca pembayaran: {exc}")
            return
        if not rows:
            bot.reply_to(message, "✅ Tidak ada pembayaran yang menunggu verifikasi.")
            return
        lines = ["🕒 <b>MENUNGGU VERIFIKASI</b>"]
        for p in rows:
            lines.append(
                f"• <code>{p['invoice']}</code> | {p.get('plan_name') or p.get('plan_code') or '-'} | {_format_rupiah(int(p['amount']))} | ID <code>{p['chat_id']}</code>"
            )
        bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

    # ---------------- Admin dynamic plan management ----------------
    @bot.message_handler(commands=["paketadmin", "paketlist"])
    def handle_plan_admin(message):
        if not _is_admin_user(message.chat.id):
            return
        try:
            plans = get_all_plans()
            markup = types.InlineKeyboardMarkup(row_width=2)
            for p in plans:
                target = 0 if p.get("is_active") else 1
                label = "⛔ Nonaktifkan" if p.get("is_active") else "✅ Aktifkan"
                markup.add(types.InlineKeyboardButton(f"{label} {p['code']}", callback_data=f"plan_toggle:{p['code']}:{target}"))
            bot.reply_to(message, _admin_plan_text(plans), parse_mode="HTML", reply_markup=markup)
        except Exception as exc:
            bot.reply_to(message, f"⚠️ Gagal membaca paket: {exc}")

    @bot.message_handler(commands=["paketadd"])
    def handle_plan_add(message):
        if not _is_admin_user(message.chat.id):
            return
        raw = message.text.partition(" ")[2].strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            bot.reply_to(message, "Format: /paketadd kode|Nama Paket|hari|harga\nContoh: /paketadd pro90|Pro 90 Hari|90|65000")
            return
        try:
            plan = add_plan(parts[0], parts[1], int(parts[2]), int(parts[3]))
            bot.reply_to(message, f"✅ Paket <b>{plan['name']}</b> ditambahkan: {plan['duration_days']} hari / {_format_rupiah(int(plan['price']))}", parse_mode="HTML")
        except Exception as exc:
            bot.reply_to(message, f"❌ Gagal tambah paket: {exc}")

    @bot.message_handler(commands=["paketedit"])
    def handle_plan_edit(message):
        if not _is_admin_user(message.chat.id):
            return
        raw = message.text.partition(" ")[2].strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            bot.reply_to(message, "Format: /paketedit kode|Nama Paket|hari|harga\nContoh: /paketedit pro30|Pro Bulanan|30|30000")
            return
        try:
            old = get_plan(parts[0])
            if not old:
                bot.reply_to(message, "❌ Kode paket tidak ditemukan.")
                return
            plan = replace_plan(parts[0], parts[1], int(parts[2]), int(parts[3]), old.get("plan_type") or "pro", int(old.get("sort_order") or 100))
            bot.reply_to(message, f"✅ Paket <b>{plan['name']}</b> diperbarui: {plan['duration_days']} hari / {_format_rupiah(int(plan['price']))}", parse_mode="HTML")
        except Exception as exc:
            bot.reply_to(message, f"❌ Gagal edit paket: {exc}")

    @bot.message_handler(commands=["paketon", "paketoff"])
    def handle_plan_switch(message):
        if not _is_admin_user(message.chat.id):
            return
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            bot.reply_to(message, "Format: /paketon kode atau /paketoff kode")
            return
        active = message.text.split()[0].lower().startswith("/paketon")
        try:
            plan = set_plan_active(parts[1].strip(), active)
            if not plan:
                bot.reply_to(message, "❌ Paket tidak ditemukan.")
                return
            bot.reply_to(message, f"{'✅ Aktif' if active else '⛔ Nonaktif'}: {plan['name']}")
        except Exception as exc:
            bot.reply_to(message, f"❌ Gagal mengubah paket: {exc}")

    @bot.message_handler(commands=["qrisdebug"])
    def handle_qris_debug(message):
        if not _is_admin_user(message.chat.id):
            return
        
        raw = QRIS_STATIC_PAYLOAD or ""
        clean = _clean_qris_payload(raw)
        length = len(clean)
        
        if length == 0:
            bot.reply_to(message, "⚠️ QRIS_STATIC_PAYLOAD kosong!")
            return
            
        first_12 = clean[:12]
        last_12 = clean[-12:]
        starts_000201 = "Ya" if clean.startswith("000201") else "Tidak"
        
        try:
            validate_static_qris(clean)
            val_result = "VALID"
        except Exception as e:
            val_result = f"ERROR: {e}"
            
        reply = (
            "🛠️ <b>QRIS DEBUG INFO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"Panjang Payload (setelah dibersihkan): {length} (Target: 196)\n"
            f"Awalan 000201: {starts_000201}\n"
            f"12 Karakter Pertama: <code>{first_12}</code>\n"
            f"12 Karakter Terakhir: <code>{last_12}</code>\n\n"
            f"Validasi Format: <b>{val_result}</b>"
        )
        bot.reply_to(message, reply, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("plan_toggle:"))
    def callback_plan_toggle(call):
        if not _is_admin_user(call.from_user.id):
            bot.answer_callback_query(call.id, "Akses admin ditolak.", show_alert=True)
            return
        try:
            _, code, target = call.data.split(":", 2)
            plan = set_plan_active(code, target == "1")
            bot.answer_callback_query(call.id, "Paket diperbarui." if plan else "Paket tidak ditemukan.", show_alert=True)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)

    # ---------------- User payment callbacks ----------------
    @bot.callback_query_handler(func=lambda call: call.data.startswith("plan:"))
    def callback_plan(call):
        bot.answer_callback_query(call.id)
        code = call.data.split(":", 1)[1]
        try:
            plan = get_plan(code, active_only=True)
            if not plan:
                bot.send_message(call.from_user.id, "⚠️ Paket sudah tidak tersedia. Ketik /langganan untuk melihat paket terbaru.")
                return
            payment = create_payment(call.from_user.id, call.from_user.username, plan)
            # Existing invoice may be awaiting verification for a different plan.
            if payment["status"] in {"waiting_verification", "processing"}:
                bot.send_message(
                    call.from_user.id,
                    f"🕒 Kamu masih punya tagihan <code>{payment['invoice']}</code> yang menunggu verifikasi admin. Selesaikan tagihan tersebut dulu.",
                    parse_mode="HTML",
                )
                return
            _show_qris(bot, call.from_user.id, payment)
        except Exception as exc:
            bot.send_message(call.from_user.id, f"⚠️ Gagal membuat tagihan: {exc}")

    @bot.callback_query_handler(func=lambda call: call.data == "pay_latest")
    def callback_latest(call):
        bot.answer_callback_query(call.id)
        try:
            payment = latest_payment(call.from_user.id)
        except Exception as exc:
            bot.send_message(call.from_user.id, f"⚠️ Error: {exc}")
            return
        if not payment:
            bot.send_message(call.from_user.id, "📭 Belum ada tagihan.")
            return
        bot.send_message(
            call.from_user.id,
            f"📄 <code>{payment['invoice']}</code>\nPaket: <b>{payment.get('plan_name') or '-'}</b>\nStatus: {_status_label(payment['status'])}",
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_qr:"))
    def callback_qr(call):
        bot.answer_callback_query(call.id)
        invoice = call.data.split(":", 1)[1]
        try:
            payment = get_payment(invoice)
        except Exception as exc:
            bot.send_message(call.from_user.id, f"⚠️ Gagal membaca tagihan: {exc}")
            return
        if not payment or str(payment["chat_id"]) != str(call.from_user.id):
            bot.send_message(call.from_user.id, "❌ Tagihan tidak ditemukan.")
            return
        if payment["status"] != "pending":
            bot.send_message(call.from_user.id, f"Status tagihan: {_status_label(payment['status'])}")
            return
        _show_qris(bot, call.from_user.id, payment)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_status:"))
    def callback_status(call):
        invoice = call.data.split(":", 1)[1]
        try:
            payment = get_payment(invoice)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)
            return
        if not payment or str(payment["chat_id"]) != str(call.from_user.id):
            bot.answer_callback_query(call.id, "Tagihan tidak ditemukan.", show_alert=True)
            return
        bot.answer_callback_query(call.id, _status_label(payment["status"]), show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_cancel:"))
    def callback_cancel(call):
        invoice = call.data.split(":", 1)[1]
        try:
            payment = cancel_payment(invoice, call.from_user.id)
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)
            return
        if payment and payment["status"] == "cancelled":
            bot.answer_callback_query(call.id, "Tagihan dibatalkan.", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "Tagihan tidak bisa dibatalkan pada status ini.", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_submit:"))
    def callback_submit(call):
        invoice = call.data.split(":", 1)[1]
        try:
            current = get_payment(invoice)
            if not current or str(current["chat_id"]) != str(call.from_user.id):
                bot.answer_callback_query(call.id, "Tagihan tidak ditemukan.", show_alert=True)
                return
            if current["status"] == "waiting_verification":
                bot.answer_callback_query(call.id, "Sudah menunggu verifikasi admin.", show_alert=True)
                return
            if current["status"] != "pending":
                bot.answer_callback_query(call.id, _status_label(current["status"]), show_alert=True)
                return
            payment = submit_payment(invoice)
            if payment and payment["status"] == "waiting_verification":
                _notify_admin(bot, payment)
                bot.answer_callback_query(call.id, "Pembayaran dikirim untuk verifikasi.", show_alert=True)
                bot.send_message(
                    call.from_user.id,
                    "🕒 <b>Menunggu Verifikasi</b>\n\nAdmin sedang mengecek dana masuk. Akun akan aktif setelah pembayaran disetujui.",
                    parse_mode="HTML",
                    reply_markup=types.InlineKeyboardMarkup().add(_support_button()),
                )
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_approve:"))
    def callback_approve(call):
        if not _is_admin_user(call.from_user.id):
            bot.answer_callback_query(call.id, "Akses admin ditolak.", show_alert=True)
            return
        invoice = call.data.split(":", 1)[1]
        try:
            claimed = claim_for_approval(invoice, str(call.from_user.id))
            if not claimed:
                existing = get_payment(invoice)
                bot.answer_callback_query(call.id, f"Status sekarang: {_status_label(existing['status']) if existing else 'tidak ditemukan'}", show_alert=True)
                return
            ok, new_until = _extend_subscription(
                str(claimed["chat_id"]),
                int(claimed["duration_days"]),
                claimed.get("plan_type") or "pro",
            )
            if not ok:
                revert_to_waiting(invoice)
                bot.answer_callback_query(call.id, "Gagal mengaktifkan langganan.", show_alert=True)
                return
            finalize_paid(invoice, str(call.from_user.id))
            _reward_referral_once(str(claimed["chat_id"]))
            until_wib = new_until.astimezone(timezone(timedelta(hours=7))).strftime("%d-%m-%Y %H:%M WIB")
            bot.answer_callback_query(call.id, "Pembayaran disetujui ✅")
            try:
                bot.edit_message_text(
                    "✅ <b>PEMBAYARAN DISETUJUI</b>\n"
                    f"Invoice: <code>{invoice}</code>\n"
                    f"User ID: <code>{claimed['chat_id']}</code>\n"
                    f"Paket: <b>{claimed.get('plan_name') or '-'}</b>\n"
                    f"Aktif sampai: <b>{until_wib}</b>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                )
            except Exception:
                pass
            bot.send_message(
                claimed["chat_id"],
                "🎉 <b>PEMBAYARAN BERHASIL!</b>\n\n"
                f"📦 Paket: <b>{claimed.get('plan_name') or claimed.get('plan_code') or 'Pro'}</b>\n"
                f"⏳ Tambahan: <b>{claimed['duration_days']} hari</b>\n"
                f"📅 Aktif sampai: <b>{until_wib}</b>\n\n"
                "Akses premium sudah aktif. Ketik /help untuk mulai menggunakan bot.",
                parse_mode="HTML",
            )
        except Exception as exc:
            try:
                revert_to_waiting(invoice)
            except Exception:
                pass
            bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_reject:"))
    def callback_reject(call):
        if not _is_admin_user(call.from_user.id):
            bot.answer_callback_query(call.id, "Akses admin ditolak.", show_alert=True)
            return
        invoice = call.data.split(":", 1)[1]
        try:
            payment = reject_payment(invoice, str(call.from_user.id))
            if not payment:
                bot.answer_callback_query(call.id, "Tagihan tidak ditemukan.", show_alert=True)
                return
            bot.answer_callback_query(call.id, "Pembayaran ditolak.")
            try:
                bot.edit_message_text(
                    "❌ <b>PEMBAYARAN DITOLAK</b>\n"
                    f"Invoice: <code>{invoice}</code>\n"
                    f"User ID: <code>{payment['chat_id']}</code>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="HTML",
                )
            except Exception:
                pass
            bot.send_message(
                payment["chat_id"],
                "❌ <b>Pembayaran belum dapat diverifikasi.</b>\n\n"
                f"Invoice: <code>{invoice}</code>\n"
                "Jika dana sebenarnya sudah terpotong, jangan membayar ulang. Hubungi admin dan kirim bukti pembayaran.",
                parse_mode="HTML",
                reply_markup=types.InlineKeyboardMarkup().add(_support_button()),
            )
        except Exception as exc:
            bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)


def _reminder_already_sent(chat_id: str, valid_until: str, key: str) -> bool:
    rows = _get(
        "subscription_reminders?chat_id=eq."
        + requests.utils.quote(str(chat_id), safe="")
        + "&valid_until=eq."
        + requests.utils.quote(valid_until, safe="")
        + "&reminder_key=eq."
        + requests.utils.quote(key, safe="")
        + "&limit=1"
    )
    return bool(rows)


def _mark_reminder_sent(chat_id: str, valid_until: str, key: str) -> None:
    _post(
        "subscription_reminders",
        {"chat_id": str(chat_id), "valid_until": valid_until, "reminder_key": key},
    )


def send_h3_expiration_reminders(bot) -> None:
    if not PAYMENT_DB_KEY:
        return
    now = datetime.now(timezone.utc)
    try:
        users = database.get_all_users()
    except Exception:
        return
    for user in users:
        if user.get("role") == "admin":
            continue
        raw = user.get("valid_until", "")
        try:
            until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        time_left = until - now
        if not (timedelta(days=2) < time_left <= timedelta(days=3)):
            continue
        try:
            if _reminder_already_sent(str(user["chat_id"]), raw, "h3"):
                continue
            bot.send_message(
                user["chat_id"],
                "⏰ <b>PENGINGAT H-3</b>\n\nMasa aktif Ventedaily Monitor akan habis sekitar 3 hari lagi. Ketik /langganan untuk memilih paket perpanjangan.",
                parse_mode="HTML",
            )
            _mark_reminder_sent(str(user["chat_id"]), raw, "h3")
        except Exception as exc:
            print(f"H-3 reminder error for {user.get('chat_id')}: {exc}")
