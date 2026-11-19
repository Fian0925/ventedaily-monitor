import math
import re

MARKETPLACES = {
    "shopee": {"name": "Shopee", "fee": 6.5},
    "tokopedia": {"name": "Tokopedia", "fee": 5.0},
    "tiktok": {"name": "TikTok Shop", "fee": 6.0},
    "lazada": {"name": "Lazada", "fee": 5.5}
}

def parse_price(price_str):
    """Mengubah string harga (misal: 'Rp. 155.000') menjadi integer"""
    # Hapus semua karakter non-digit
    clean_str = re.sub(r'[^\d]', '', price_str)
    try:
        return int(clean_str)
    except ValueError:
        return 0

def format_price(price_int):
    """Mengubah integer menjadi format Rupiah"""
    return f"Rp {price_int:,}".replace(",", ".")

NOMINAL_AMOUNT = re.compile(r"(?:rp\.?\s*)?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)", re.I)


def is_nominal_amount(value):
    return NOMINAL_AMOUNT.fullmatch(value.strip()) is not None


def parse_profit(value):
    value = value.strip()
    if value.endswith("%"):
        percent = float(value[:-1].strip().replace(",", "."))
        if math.isfinite(percent) and percent >= 0:
            return "percent", percent
    elif is_nominal_amount(value):
        return "nominal", parse_price(value)
    raise ValueError("Target profit tidak valid")


def calculate_price(modal_str, profit_value, profit_type, admin_pct, biaya_tambahan=0):
    """
    Rumus Harga Jual: (Modal + Biaya + Profit) / (1 - Admin%)
    profit_type: 'percent' atau 'nominal'
    admin_pct: persentase admin (misal 6.5)
    biaya_tambahan: biaya tambahan per produk (misal 1250)
    """
    modal_vente = parse_price(modal_str)
    if modal_vente == 0: return None
    
    modal = modal_vente + biaya_tambahan
    
    if profit_type == 'percent':
        profit_rp = modal * (profit_value / 100)
    else:
        profit_rp = profit_value
        
    admin_decimal = admin_pct / 100
    if admin_decimal >= 1: # Mencegah pembagian dengan nol atau negatif
        admin_decimal = 0.99 
        
    harga_jual_raw = (modal + profit_rp) / (1 - admin_decimal)
    
    # Pembulatan ke atas (ratusan terdekat)
    harga_jual_rounded = int(harga_jual_raw)
    if harga_jual_rounded % 100 != 0:
        harga_jual_rounded = ((harga_jual_rounded // 100) + 1) * 100
        
    # Kalkulasi ulang admin fee aktual dan net profit
    admin_fee_rp = int(harga_jual_rounded * admin_decimal)
    net_received = harga_jual_rounded - admin_fee_rp
    actual_profit = net_received - modal
    
    return {
        "modal_vente": modal_vente,
        "biaya_tambahan": biaya_tambahan,
        "modal": modal,
        "profit_target": int(profit_rp),
        "harga_jual": harga_jual_rounded,
        "admin_fee": admin_fee_rp,
        "net_received": net_received,
        "actual_profit": actual_profit
    }

def compare_all_marketplaces(modal_str, profit_value, profit_type, biaya_tambahan=0):
    """Menghitung harga jual di semua marketplace untuk dibandingkan"""
    results = {}
    for key, mp in MARKETPLACES.items():
        res = calculate_price(modal_str, profit_value, profit_type, mp['fee'], biaya_tambahan)
        if res:
            results[key] = {
                "name": mp['name'],
                "fee_pct": mp['fee'],
                "harga_jual": res['harga_jual'],
                "actual_profit": res['actual_profit']
            }
    return results
