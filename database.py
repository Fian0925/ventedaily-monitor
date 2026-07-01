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

def log_event(nama, event_type, stock, harga):
    payload = {
        "nama": nama,
        "event_type": event_type,
        "stock": stock,
        "harga": harga
    }
    try:
        res = requests.post(f"{SUPABASE_URL}/product_events", json=payload, headers=headers)
        res.raise_for_status()
    except Exception as e:
        print(f"Error logging event to Supabase: {e}")

def get_events(event_type=None, days=1):
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if event_type:
        url = f"{SUPABASE_URL}/product_events?event_type=eq.{event_type}&detected_at=gte.{since}&order=detected_at.desc"
    else:
        url = f"{SUPABASE_URL}/product_events?detected_at=gte.{since}&order=detected_at.desc"
    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"Error getting events: {e}")
        return []

def get_user_settings(chat_id):
    url = f"{SUPABASE_URL}/user_settings?chat_id=eq.{chat_id}"
    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()
        if data:
            return data[0]
        
        # Create default settings if not exists
        default_settings = {
            "chat_id": str(chat_id),
            "marketplace": "shopee",
            "admin_fee": 6.5,
            "markup_type": "percent",
            "markup_value": 0.0
        }
        requests.post(f"{SUPABASE_URL}/user_settings", json=default_settings, headers=headers)
        return default_settings
    except Exception as e:
        print(f"Error getting/creating user settings: {e}")
        return {
            "chat_id": str(chat_id),
            "marketplace": "shopee",
            "admin_fee": 6.5,
            "markup_type": "percent",
            "markup_value": 0.0
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
        res = requests.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert)
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
        res = requests.post(f"{SUPABASE_URL}/user_settings", json=payload, headers=headers_upsert)
        res.raise_for_status()
    except Exception as e:
        print(f"Error setting markup: {e}")
