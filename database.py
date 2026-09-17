import requests
import config

SUPABASE_URL = f"{config.SUPABASE_URL}/rest/v1"
SUPABASE_KEY = config.SUPABASE_KEY

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Session reuses TCP+SSL connection, sehingga request ke-2 dst jauh lebih cepat
session = requests.Session()
session.headers.update(headers)


# =====================================================================
# SNAPSHOT — disimpan di Supabase agar tidak hilang saat Render restart
# Table: stock_snapshot (id TEXT PRIMARY KEY, data JSONB, updated_at TIMESTAMPTZ)
# =====================================================================

def save_snapshot(data) -> bool:
    """Simpan snapshot stok ke Supabase. Return True jika berhasil."""
    from datetime import datetime, timezone
    payload = {
        "id": "latest",
        "data": data,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    upsert_headers = headers.copy()
    upsert_headers["Prefer"] = "resolution=merge-duplicates"
    try:
        res = session.post(
            f"{SUPABASE_URL}/stock_snapshot",
            json=payload,
            headers=upsert_headers,
            timeout=30
        )
        res.raise_for_status()
        return True
    except Exception as e:
        print(f"Error saving snapshot to Supabase: {e}")
        return False


def load_snapshot():
    """Ambil snapshot stok dari Supabase. Return dict atau None jika belum ada."""
    try:
        res = session.get(
            f"{SUPABASE_URL}/stock_snapshot?id=eq.latest",
            headers=headers,
            timeout=15
        )
        res.raise_for_status()
        rows = res.json()
        if rows:
            return rows[0].get("data")
        return None
    except Exception as e:
        print(f"Error loading snapshot from Supabase: {e}")
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


def log_event(nama, event_type, stock, harga):
    payload = {
        "nama": nama,
        "event_type": event_type,
        "stock": stock,
        "harga": harga
    }
    try:
        res = session.post(f"{SUPABASE_URL}/product_events", json=payload, headers=headers, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"Error logging event to Supabase: {e}")

def get_events(event_type=None, days=1, specific_date=None):
    from datetime import datetime, timedelta, timezone
    
    if specific_date:
        start = f"{specific_date}T00:00:00Z"
        end = f"{specific_date}T23:59:59Z"
        if event_type:
            url = f"{SUPABASE_URL}/product_events?event_type=eq.{event_type}&detected_at=gte.{start}&detected_at=lte.{end}&order=detected_at.desc"
        else:
            url = f"{SUPABASE_URL}/product_events?detected_at=gte.{start}&detected_at=lte.{end}&order=detected_at.desc"
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace('+', '%2B')
        if event_type:
            url = f"{SUPABASE_URL}/product_events?event_type=eq.{event_type}&detected_at=gte.{since}&order=detected_at.desc"
        else:
            url = f"{SUPABASE_URL}/product_events?detected_at=gte.{since}&order=detected_at.desc"
    try:
        res = session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"Error getting events: {e}")
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
        session.post(f"{SUPABASE_URL}/user_settings", json=default_settings, headers=headers, timeout=10)
        return default_settings
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
    except Exception as e:
        print(f"Error setting markup: {e}")

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
    now = datetime.now(timezone.utc).isoformat().replace('+', '%2B')
    url = f"{SUPABASE_URL}/user_settings?valid_until=gt.{now}"
    try:
        res = session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"Error getting active users: {e}")
        return []

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

def increment_referral_count(chat_id):
    settings = get_user_settings(chat_id)
    count = (settings.get("referral_count") or 0) + 1
    payload = {
        "chat_id": str(chat_id),
        "referral_count": count
    }
    headers_upsert = headers.copy()
    headers_upsert["Prefer"] = "resolution=merge-duplicates"
    try:
        session.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert, timeout=10)
    except Exception as e:
        print(f"Error incrementing referral count: {e}")

