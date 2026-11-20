import json
from pathlib import Path

import requests
import config

SUPABASE_URL = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1"
SUPABASE_KEY = config.SUPABASE_SERVER_KEY

headers = {
    "apikey": SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}
if not SUPABASE_KEY.startswith(("sb_secret_", "sb_publishable_")):
    headers["Authorization"] = f"Bearer {SUPABASE_KEY}"

# Session reuses TCP+SSL connection, sehingga request ke-2 dst jauh lebih cepat
session = requests.Session()
session.headers.update(headers)


# =====================================================================
# SNAPSHOT — disimpan di Supabase agar tidak hilang saat Render restart
# Table: stock_snapshot (id TEXT PRIMARY KEY, data JSONB, updated_at TIMESTAMPTZ)
# =====================================================================

def call_rpc(name, payload):
    response = session.post(f"{SUPABASE_URL}/rpc/{name}", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def save_snapshot(data, expected_updated_at, events):
    return call_rpc("save_stock_cycle", {
        "p_expected_updated_at": expected_updated_at,
        "p_data": data,
        "p_events": events,
    })


def read_snapshot():
    response = session.get(
        f"{SUPABASE_URL}/stock_snapshot",
        params={"id": "eq.latest", "select": "data,updated_at"},
        timeout=15,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def load_snapshot():
    try:
        row = read_snapshot()
        return row["data"] if row else None
    except (requests.RequestException, ValueError) as exc:
        print(f"Error loading snapshot from Supabase: {exc}")
        return None


def load_catalog_snapshot():
    data = load_snapshot()
    if not data:
        try:
            path = Path(__file__).with_name("data_snapshot.json")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"Error loading local snapshot: {exc}")
            return None

    # Older snapshots use numeric IDs and store the searchable name in each row.
    items = {}
    stock_rank = {"aman": 2, "ready": 1}
    for key, item in data.items():
        name = item.get("nama") or key
        stock = item.get("stock", "")
        current = items.get(name)
        if current is None or stock_rank.get(stock.lower(), 0) > stock_rank.get(current["stock"].lower(), 0):
            items[name] = {"stock": stock, "harga": item.get("harga", "")}
    return items


def get_snapshot_updated_at():
    """Ambil waktu updated_at snapshot terakhir dari Supabase (ISO string atau None)."""
    try:
        res = session.get(
            f"{SUPABASE_URL}/stock_snapshot?id=eq.latest&select=updated_at",
            headers=headers,
            timeout=10
        )
        res.raise_for_status()
        rows = res.json()
        if rows:
            return rows[0].get("updated_at")
        return None
    except Exception as e:
        print(f"Error getting snapshot updated_at: {e}")
        return None


def get_user_stats():
    """Ambil statistik pengguna untuk command /statistik admin."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    try:
        res = session.get(f"{SUPABASE_URL}/user_settings", headers=headers, timeout=15)
        res.raise_for_status()
        users = res.json()
    except Exception as e:
        print(f"Error getting user stats: {e}")
        return None

    total = len(users)
    aktif = 0
    trial = 0
    expired = 0
    for u in users:
        if u.get('role') == 'admin':
            continue
        v_str = u.get('valid_until', '2000-01-01T00:00:00Z')
        pt = u.get('plan_type', 'none')
        try:
            valid_until = datetime.fromisoformat(v_str.replace('Z', '+00:00'))
        except:
            expired += 1
            continue
        if valid_until > now:
            if pt == 'trial':
                trial += 1
            else:
                aktif += 1
        else:
            expired += 1

    return {
        'total': total,
        'aktif': aktif,
        'trial': trial,
        'expired': expired,
    }


def get_events(event_type=None, days=1, specific_date=None):
    from datetime import date, datetime, time, timedelta, timezone

    params = [("order", "detected_at.desc,id.desc")]
    if event_type:
        params.append(("event_type", "in.(new,baru)" if event_type == "new" else f"eq.{event_type}"))
    if specific_date:
        day = date.fromisoformat(specific_date)
        wib = timezone(timedelta(hours=7))
        start = datetime.combine(day, time.min, wib)
        end = start + timedelta(days=1)
        params.append(("detected_at", f"lt.{end.isoformat()}"))
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        params.append(("detected_at", f"lte.{end.isoformat()}"))
    params.append(("detected_at", f"gte.{start.isoformat()}"))

    events = []
    try:
        while True:
            response = session.get(
                f"{SUPABASE_URL}/product_events",
                params=[*params, ("limit", 500), ("offset", len(events))],
                timeout=10,
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                return events
            events.extend(rows)
    except (requests.RequestException, ValueError) as exc:
        print(f"Error getting events: {exc}")
        return []


def get_user_settings(chat_id):
    url = f"{SUPABASE_URL}/user_settings?chat_id=eq.{chat_id}"
    try:
        res = session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data:
            return data[0]
        
        # Create default settings if not exists
        # Role admin for the creator, others are user
        from config import TELEGRAM_CHAT_ID
        role = 'admin' if str(chat_id) == str(TELEGRAM_CHAT_ID) else 'user'
        
        default_settings = {
            "chat_id": str(chat_id),
            "marketplace": "shopee",
            "admin_fee": 6.5,
            "markup_type": "percent",
            "markup_value": 0.0,
            "role": role,
            "plan_type": "none",
            "valid_until": "2000-01-01T00:00:00Z", # Expired by default
            "reminder_sent": "",
            "referred_by": "",
            "referral_count": 0
        }
        created = session.post(
            f"{SUPABASE_URL}/user_settings",
            json=default_settings,
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            timeout=10,
        )
        created.raise_for_status()
        rows = created.json()
        if rows:
            return rows[0]
        res = session.get(url, timeout=10)
        res.raise_for_status()
        return res.json()[0]
    except Exception as e:
        print(f"Error getting/creating user settings: {e}")
        from config import TELEGRAM_CHAT_ID
        role = 'admin' if str(chat_id) == str(TELEGRAM_CHAT_ID) else 'user'
        return {
            "chat_id": str(chat_id),
            "marketplace": "shopee",
            "admin_fee": 6.5,
            "markup_type": "percent",
            "markup_value": 0.0,
            "role": role,
            "plan_type": "none",
            "valid_until": "2000-01-01T00:00:00Z",
            "reminder_sent": "",
            "referred_by": "",
            "referral_count": 0
        }

def set_user_marketplace(chat_id, marketplace, fee):
    payload = {
        "chat_id": str(chat_id),
        "marketplace": marketplace,
        "admin_fee": float(fee)
    }
    headers_upsert = headers.copy()
    headers_upsert["Prefer"] = "resolution=merge-duplicates"
    try:
        res = session.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"Error setting marketplace: {e}")

def set_user_markup(chat_id, markup_type, value):
    payload = {
        "chat_id": str(chat_id),
        "markup_type": markup_type,
        "markup_value": float(value)
    }
    headers_upsert = headers.copy()
    headers_upsert["Prefer"] = "resolution=merge-duplicates"
    try:
        res = session.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert, timeout=10)
        res.raise_for_status()
        return True
    except Exception as e:
        print(f"Error setting markup: {e}")
        return False

def update_user_pricing(chat_id, pricing):
    response = session.patch(
        f"{SUPABASE_URL}/user_settings",
        params={"chat_id": f"eq.{chat_id}"},
        json=pricing,
        headers={"Prefer": "return=representation"},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise requests.HTTPError("Pricing update matched no account")
    return rows[0]


def get_all_users():
    url = f"{SUPABASE_URL}/user_settings"
    try:
        res = session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"Error getting all users: {e}")
        return []

def get_active_users():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    users = get_all_users()
    active = []
    for u in users:
        if u.get('role') == 'admin':
            active.append(u)
            continue
        v_str = u.get('valid_until', '2000-01-01T00:00:00Z')
        try:
            valid_until = datetime.fromisoformat(v_str.replace('Z', '+00:00'))
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if valid_until > now:
                active.append(u)
        except Exception:
            continue
    return active

def get_expired_users():
    """Ambil semua user non-admin yang masa aktifnya sudah habis / belum aktif."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    users = get_all_users()
    expired = []
    for u in users:
        if u.get('role') == 'admin':
            continue
        v_str = u.get('valid_until', '2000-01-01T00:00:00Z')
        try:
            valid_until = datetime.fromisoformat(v_str.replace('Z', '+00:00'))
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if valid_until <= now:
                expired.append(u)
        except Exception:
            expired.append(u)
    return expired

def update_subscription(chat_id, plan_type, valid_until, reminder_sent=""):
    payload = {
        "chat_id": str(chat_id),
        "plan_type": plan_type,
        "valid_until": valid_until,
        "reminder_sent": reminder_sent
    }
    headers_upsert = headers.copy()
    headers_upsert["Prefer"] = "resolution=merge-duplicates"
    try:
        res = session.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert, timeout=10)
        res.raise_for_status()
        return True
    except Exception as e:
        print(f"Error updating subscription: {e}")
        return False

def set_referred_by(chat_id, inviter_id):
    payload = {
        "chat_id": str(chat_id),
        "referred_by": str(inviter_id)
    }
    headers_upsert = headers.copy()
    headers_upsert["Prefer"] = "resolution=merge-duplicates"
    try:
        session.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert, timeout=10)
    except Exception as e:
        print(f"Error setting referred_by: {e}")

def activate_trial(chat_id):
    return call_rpc("activate_monitor_trial", {"p_chat_id": str(chat_id)})


def mark_expiration_reminder(chat_id, valid_until, previous_reminder, reminder):
    response = session.patch(
        f"{SUPABASE_URL}/user_settings",
        params={
            "chat_id": f"eq.{chat_id}",
            "valid_until": f"eq.{valid_until}",
            "reminder_sent": "is.null" if previous_reminder is None else f"eq.{previous_reminder}",
        },
        json={"reminder_sent": reminder},
        timeout=10,
    )
    response.raise_for_status()


def grant_referral(chat_id):
    return call_rpc("grant_monitor_referral", {
        "p_chat_id": str(chat_id),
        "p_bonus_days": config.REFERRAL_BONUS_DAYS,
    })
