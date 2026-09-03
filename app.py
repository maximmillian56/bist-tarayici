"""
BIST Hisse Senedi Tarayıcısı v2.1
Direkt TradingView Scanner API (tradingview_screener kütüphanesi yok)
"""
import os, threading, time
from datetime import datetime
import requests
from flask import Flask, jsonify, send_file

PORT     = int(os.environ.get("PORT", 5000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app      = Flask(__name__)

# ==============================================================================
#  TRADİNGVİEW DİREKT API
# ==============================================================================

SCAN_URL = "https://scanner.tradingview.com/turkey/scan"

SCAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

SCAN_COLUMNS = [
    "name", "description", "close", "change",
    "price_earnings_ttm",        # F/K
    "price_book_ratio",           # PD/DD
    "price_sales_ratio",          # P/S
    "market_cap_basic",           # Piyasa Değeri
    "total_revenue_ttm",          # Toplam Gelir (FAVÖK/Gelir için)
    "gross_margin",               # Brüt Kar Marjı (TV'de: gross_margin)
    "ebitda_ttm",                 # FAVÖK
    "enterprise_value_ebitda_ttm",# EV/FAVÖK
    "net_income_ttm",             # Net Kar (TTM)
    "net_income_fq",              # Net Kar (Son Çeyrek)
    "net_income_fq_prev",         # Net Kar (Önceki Çeyrek)
    "after_tax_margin",           # Net Kar Marjı
    "dividend_yield_recent",      # Temettü
    "sector",                     # Sektör (geniş)
    "industry",                   # Sektör (detaylı — banka/GYO ayrımı için)
    "Perf.1Y", "Perf.3Y", "Perf.5Y", "Perf.6M", "Perf.1M",
    "volume",
    "average_volume_10d_calc",
    "average_volume_5d_calc",
    "average_volume_30d_calc",    # 30 günlük ortalama hacim
    "relative_volume_10d_calc",
    "RSI",
    "MACD.macd", "MACD.signal",
    "Recommend.All", "Recommend.MA", "Recommend.Other",
    # EMA (Üstel Hareketli Ortalamalar)
    "EMA20", "EMA25", "EMA50", "EMA100", "EMA200",
    # Bollinger Bantları (20 günlük, 2 std)
    "BB.upper", "BB.lower", "BB.basis",
    # Beta (endekse göre oynaklık)
    "beta_1_year",
    # Günlük OHLC (pivot hesabı için)
    "open", "high", "low",
    # Aylık OHLC (aylık pivot için)
    "High.1M", "Low.1M",
    # Aylık pivot (TradingView destekler)
    "Pivot.M.Classic.S1", "Pivot.M.Classic.S2",
    "Pivot.M.Classic.R1", "Pivot.M.Classic.R2",
    "Pivot.M.Classic.Middle",
]

COL_IDX = {col: i for i, col in enumerate(SCAN_COLUMNS)}

# ==============================================================================
#  ÖNBELLEKLER
# ==============================================================================

_cache = {"stocks": None, "updated_at": None, "loading": False, "error": None}
_seasonal_cache = {"data": None, "updated_at": None, "loading": False}
_progress = {"done": 0, "total": 1000}
_lock = threading.Lock()

# ==============================================================================
#  YARDIMCI FONKSİYONLAR
# ==============================================================================

def _sf(val):
    if val is None:
        return None
    try:
        s = str(val).strip().lower()
        if s in ("nan", "none", "null", "inf", "-inf", ""):
            return None
        f = float(val)
        return None if f != f else f
    except Exception:
        return None

def _sr(val, n=2):
    f = _sf(val)
    return round(f, n) if f is not None else None

def _signal(recommend):
    if recommend is None:
        return {"label": "\u2014", "cls": "neutral", "icon": "\u27a1\ufe0f"}
    r = float(recommend)
    if r >= 0.5:
        return {"label": "G\u00fc\u00e7l\u00fc Al",  "cls": "strong-buy",  "icon": "\U0001f680"}
    elif r >= 0.1:
        return {"label": "Al",        "cls": "buy",         "icon": "\U0001f4c8"}
    elif r > -0.1:
        return {"label": "N\u00f6tr",      "cls": "neutral",     "icon": "\u27a1\ufe0f"}
    elif r > -0.5:
        return {"label": "Sat",       "cls": "sell",        "icon": "\U0001f4c9"}
    else:
        return {"label": "G\u00fc\u00e7l\u00fc Sat", "cls": "strong-sell", "icon": "\U0001f53b"}

def _rsi_signal(rsi):
    if rsi is None:
        return {"label": "\u2014", "cls": "neutral", "icon": ""}
    r = float(rsi)
    if r < 30:
        return {"label": f"A\u015f\u0131r\u0131 Sat\u0131m ({r:.0f})", "cls": "oversold",   "icon": "\U0001f535"}
    elif r > 70:
        return {"label": f"A\u015f\u0131r\u0131 Al\u0131m ({r:.0f})",  "cls": "overbought", "icon": "\U0001f534"}
    elif r <= 45:
        return {"label": f"D\u00fc\u015f\u00fck ({r:.0f})",        "cls": "low-rsi",    "icon": "\u2b07\ufe0f"}
    elif r >= 55:
        return {"label": f"Y\u00fcksek ({r:.0f})",       "cls": "high-rsi",   "icon": "\u2b06\ufe0f"}
    else:
        return {"label": f"Normal ({r:.0f})",        "cls": "neutral",    "icon": "\u27a1\ufe0f"}

# ==============================================================================
#  SEKTÖR EŞLEŞTİRME — TradingView Turkey Gerçek Değerleri
#  Kaynak: scanner.tradingview.com/turkey/scan → sector + industry alanları
# ==============================================================================

# industry alanına göre öncelikli eşleştirme (daha spesifik)
INDUSTRY_MAP = {
    # ── BANKA ──
    "Major Banks":               "bank",
    "Regional Banks":            "bank",
    "Investment Banks/Brokers":  "bank",
    "Life/Health Insurance":     "bank",
    "Multi-Line Insurance":      "bank",
    "Property/Casualty Insurance": "bank",
    "Finance/Rental/Leasing":   "bank",
    "Financial Conglomerates":  "bank",
    "Investment Managers":      "bank",
    # ── GYO ──
    "Real Estate Investment Trusts": "gyo",
    "Real Estate Development":       "gyo",
    "Homebuilding":                  "gyo",
    # ── GIDA & İÇECEK ──
    "Food: Major Diversified":    "gida",
    "Food: Meat/Fish/Dairy":      "gida",
    "Food: Specialty/Candy":      "gida",
    "Food Retail":                "gida",
    "Food Distributors":          "gida",
    "Beverages: Alcoholic":       "gida",
    "Beverages: Non-Alcoholic":   "gida",
    "Agricultural Commodities/Milling": "gida",
    "Restaurants":                "gida",
    # ── HOLDİNG & YATIRIM ──
    "Industrial Conglomerates":   "holding",
    "Investment Trusts/Mutual Funds": "holding",
    "Miscellaneous":              "holding",
    # ── KİMYA, İLAÇ, PETROL, PLASTİK ──
    "Chemicals: Major Diversified": "kimya",
    "Chemicals: Specialty":        "kimya",
    "Chemicals: Agricultural":     "kimya",
    "Pharmaceuticals: Major":      "kimya",
    "Pharmaceuticals: Other":      "kimya",
    "Biotechnology":               "kimya",
    "Oil Refining/Marketing":      "kimya",
    "Integrated Oil":              "kimya",
    "Industrial Specialties":      "kimya",
    "Pulp & Paper":                "kimya",
    "Containers/Packaging":        "kimya",
    # ── METAL, MAKİNE, ELEKTRİKLİ, ULAŞIM ──
    "Steel":                       "metal",
    "Aluminum":                    "metal",
    "Other Metals/Minerals":       "metal",
    "Precious Metals":             "metal",
    "Non-Energy Minerals":         "metal",
    "Construction Materials":      "metal",
    "Metal Fabrication":           "metal",
    "Industrial Machinery":        "metal",
    "Trucks/Construction/Farm Machinery": "metal",
    "Auto Parts: OEM":             "metal",
    "Motor Vehicles":              "metal",
    "Aerospace & Defense":         "metal",
    "Electrical Products":         "metal",
    "Electronic Equipment/Instruments": "metal",
    "Telecommunications Equipment": "metal",
    "Airlines":                    "metal",
    "Air Freight/Couriers":        "metal",
    "Marine Shipping":             "metal",
    "Other Transportation":        "metal",
    "Railroads":                   "metal",
    "Trucking":                    "metal",
    "Building Products":           "metal",
    # ── PERAKENDE TİCARET ──
    "Food Retail":                 "perakende",
    "Department Stores":           "perakende",
    "Specialty Stores":            "perakende",
    "Apparel/Footwear Retail":     "perakende",
    "Electronics/Appliance Stores": "perakende",
    "Internet Retail":             "perakende",
    "Wholesale Distributors":      "perakende",
    "Medical Distributors":        "perakende",
    "Electronics Distributors":    "perakende",
    # ── TEKNOLOJİ & BİLİŞİM ──
    "Information Technology Services": "teknoloji",
    "Packaged Software":           "teknoloji",
    "Data Processing Services":    "teknoloji",
    "Computer Communications":     "teknoloji",
    "Computer Processing Hardware": "teknoloji",
    "Semiconductors":              "teknoloji",
    "Electronic Production Equipment": "teknoloji",
    "Utilities: Alternative Power Generation": "teknoloji",
    "Alternative Power Generation": "teknoloji",
    "Electric Utilities":          "teknoloji",
    "Gas Distributors":            "teknoloji",
    # ── TEKSTİL, GİYİM ──
    "Textiles":                    "tekstil",
    "Apparel/Footwear":            "tekstil",
    "Consumer Sundries":           "tekstil",
    "Household/Personal Care":     "tekstil",
}

def _map_sector(sector_raw, industry_raw=None):
    """
    TradingView'ın döndürdüğü sector + industry string'lerini
    uygulama sektör koduna çevirir.
    industry daha spesifik olduğu için önce ona bakılır.
    """
    ind = str(industry_raw or "")
    sec = str(sector_raw or "")

    # 1. Önce industry'e tam eşleştirme
    if ind in INDUSTRY_MAP:
        return INDUSTRY_MAP[ind]

    # 2. Sonra industry'e kısmi eşleştirme
    ind_lo = ind.lower()
    for key, val in INDUSTRY_MAP.items():
        if key.lower() in ind_lo:
            return val

    # 3. sector bazlı geniş eşleştirme (fallback)
    sec_lo = sec.lower()
    if "finance" in sec_lo:            return "bank"
    if "retail" in sec_lo:             return "perakende"
    if "transport" in sec_lo:          return "metal"
    if "technology" in sec_lo:         return "teknoloji"
    if "consumer non-durable" in sec_lo: return "gida"
    if "consumer durable" in sec_lo:   return "metal"
    if "process" in sec_lo:            return "kimya"
    if "producer" in sec_lo:           return "metal"
    if "electronic" in sec_lo:         return "teknoloji"
    if "non-energy" in sec_lo:         return "metal"
    if "energy" in sec_lo:             return "kimya"
    if "health" in sec_lo:             return "kimya"
    if "industrial" in sec_lo:         return "metal"
    if "utilities" in sec_lo:          return "teknoloji"

    return "diger"


# ==============================================================================
#  BIST 100 LİSTESİ (Sabit — Güncelleme: Ağustos 2026)
# ==============================================================================

BIST100 = [
    "THYAO","GARAN","ASELS","EREGL","KCHOL","SAHOL","AKBNK","ISCTR","SISE","TOASO",
    "FROTO","PGSUS","BIMAS","MGROS","TCELL","TURSG","ENKAI","PETKM","ARCLK","TUPRS",
    "SOKM","HALKB","VAKBN","YKBNK","SASA","TAVHL","CIMSA","LOGO","AEFES","EKGYO",
    "ALARK","TTKOM","DOHOL","VESTL","OTKAR","TKFEN","TSKB","KRDMD","ENJSA","AKENR",
    "SELEC","AKSEN","KONTR","GUBRF","OYAKC","BRSAN","ISFIN","AKFGY","AGHOL","TTRAK",
    "SODA","KOZAL","KERVT","MPARK","ZOREN","HLGYO","IHLGM","TRGYO","ULAS","BTCIM",
    "CEMTS","ULKER","TMSN","ALKIM","EGEEN","INDES","DOAS","BERA","ODAS","GENIL",
    "ISDMR","KAREL","KOPOL","KLRHO","MIPAZ","NETAS","PRKAB","RYSAS","SNPAM","ULUSE",
    "YATAS","YESIL","YGYO","ZEDUR","ADANA","ADEL","AKGRT","ALCO","ALTINS","ANFAS",
    "ASUZU","ATAGY","ATAKP","ATEKS","ATLAS","AVSA","AYEN","AZTEK","BAGFS","BAKAB",
]
BIST100_SET = set(t + ".IS" for t in BIST100)

# ==============================================================================
#  XKTUM — BIST KATİLIM 100 ENDEKSİ (Sabit Liste — Eylül 2026)
# ==============================================================================
# Faizli geliri toplam gelirin %5'inden az olan, Katılım finans kurallarına
# uyan şirketler. Kaynak: BIST Katılım Endeksi bileşenleri.

XKTUM = [
    "THYAO","ASELS","EREGL","KCHOL","TOASO","FROTO","PGSUS","BIMAS","ARCLK","TUPRS",
    "SOKM","SASA","TAVHL","CIMSA","LOGO","ALARK","DOHOL","VESTL","OTKAR","TKFEN",
    "KRDMD","ENJSA","AKENR","SELEC","AKSEN","KONTR","GUBRF","OYAKC","BRSAN","TTRAK",
    "SODA","KOZAL","KERVT","MPARK","ZOREN","IHLGM","ULAS","BTCIM","CEMTS","ULKER",
    "TMSN","ALKIM","EGEEN","INDES","DOAS","BERA","ODAS","GENIL","ISDMR","KAREL",
    "KOPOL","KLRHO","MIPAZ","NETAS","PRKAB","RYSAS","SNPAM","ULUSE","YATAS","YESIL",
    "ADANA","ADEL","ALCO","ALTINS","ANFAS","ASUZU","ATAKP","ATEKS","AVSA","AYEN",
    "BAGFS","BAKAB","BFREN","BIOEN","BOSSA","BRKO","BURCE","CEMAS","CIMSA","CWENE",
    "DEVA","DGKLB","DYOBY","ECILC","ECZYT","EDIP","EGGUB","EGPRO","EMKEL","ENERY",
    "FMIZP","GESAN","GLYHO","GOODY","GRSEL","GSDHO","GUBRF","HATEK","HEKTS","HLGYO",
]
XKTUM_SET = set(t + ".IS" for t in XKTUM)

# ==============================================================================
#  HACİM SİNYALİ HESABI
# ==============================================================================

def _hacim_sinyal(volume, avg_7d, avg_30d):
    """
    Günlük hacim → 7 günlük ortalama kıyasına göre sinyal üret.
    1.5x → Al, 2x+ → Güçlü Al, yoksa Normal
    """
    if volume is None:
        return {"label": "—", "cls": "neutral", "icon": ""}
    ref = avg_7d if avg_7d else avg_30d
    if ref is None or ref == 0:
        return {"label": "Normal", "cls": "neutral", "icon": "📊"}
    ratio = volume / ref
    if ratio >= 2.0:
        return {"label": f"Güçlü Al ({ratio:.1f}x)", "cls": "strong-buy", "icon": "🚀"}
    if ratio >= 1.5:
        return {"label": f"Al ({ratio:.1f}x)", "cls": "buy", "icon": "📈"}
    if ratio >= 0.8:
        return {"label": "Normal", "cls": "neutral", "icon": "📊"}
    return {"label": f"Düşük ({ratio:.1f}x)", "cls": "low-rsi", "icon": "📉"}


def _fetch():
    print("=== _fetch() basladi ===", flush=True)
    with _lock:
        _cache["loading"] = True
        _cache["error"]   = None
        _progress["done"] = 0
        _progress["total"] = 1000

    results = []
    offset  = 0
    batch   = 500

    try:
        while True:
            payload = {
                "columns": SCAN_COLUMNS,
                "range": [offset, offset + batch],
                "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
                "markets": ["turkey"],
            }
            print(f"POST {SCAN_URL} range=[{offset},{offset+batch}]", flush=True)

            r = requests.post(
                SCAN_URL,
                json=payload,
                headers=SCAN_HEADERS,
                timeout=(10, 30),   # (connect_timeout, read_timeout)
            )
            print(f"HTTP {r.status_code} — {len(r.content)} bytes", flush=True)
            r.raise_for_status()

            data       = r.json()
            rows       = data.get("data", [])
            total_cnt  = data.get("totalCount", 0)

            with _lock:
                _progress["total"] = total_cnt or 1000

            print(f"  totalCount={total_cnt}, rows_in_page={len(rows)}", flush=True)

            if not rows:
                break

            for item in rows:
                d = item.get("d", [])

                def gc(col):
                    i = COL_IDX.get(col)
                    return d[i] if (i is not None and i < len(d)) else None

                name  = gc("name") or ""
                sembol = name + ".IS"
                fiyat  = _sf(gc("close"))

                # ── Aylık Pivot (TradingView destekli) ──
                s1  = _sr(gc("Pivot.M.Classic.S1"), 2)
                s2  = _sr(gc("Pivot.M.Classic.S2"), 2)
                r1  = _sr(gc("Pivot.M.Classic.R1"), 2)
                r2  = _sr(gc("Pivot.M.Classic.R2"), 2)
                mid = _sr(gc("Pivot.M.Classic.Middle"), 2)

                # ── Günlük Pivot (Klasik formül: H+L+C /3) ──
                d_h = _sf(gc("high"))
                d_l = _sf(gc("low"))
                d_c = fiyat
                if d_h and d_l and d_c:
                    mid_d = round((d_h + d_l + d_c) / 3, 2)
                    s1_d  = round(2 * mid_d - d_h, 2)
                    r1_d  = round(2 * mid_d - d_l, 2)
                    s2_d  = round(mid_d - (d_h - d_l), 2)
                    r2_d  = round(mid_d + (d_h - d_l), 2)
                else:
                    mid_d = s1_d = s2_d = r1_d = r2_d = None

                # ── Aylık Pivot (High.1M/Low.1M ile hesap) ──
                m_h = _sf(gc("High.1M"))
                m_l = _sf(gc("Low.1M"))
                if m_h and m_l and d_c:
                    mid_w = round((m_h + m_l + d_c) / 3, 2)
                    s1_w  = round(2 * mid_w - m_h, 2)
                    r1_w  = round(2 * mid_w - m_l, 2)
                    s2_w  = round(mid_w - (m_h - m_l), 2)
                    r2_w  = round(mid_w + (m_h - m_l), 2)
                else:
                    mid_w = s1_w = s2_w = r1_w = r2_w = None

                destek_uzaklik = None
                direnc_getiri  = None
                destek_yakin   = False
                if fiyat and s1 and s1 > 0:
                    destek_uzaklik = round(((fiyat - s1) / s1) * 100, 2)
                    if 0 <= destek_uzaklik <= 5:
                        destek_yakin = True
                if fiyat and r1 and fiyat > 0:
                    direnc_getiri = round(((r1 - fiyat) / fiyat) * 100, 2)


                macd_v = _sf(gc("MACD.macd"))
                macd_s = _sf(gc("MACD.signal"))
                macd_bullish = (macd_v > macd_s) if (macd_v is not None and macd_s is not None) else None

                div     = _sf(gc("dividend_yield_recent"))
                rec     = _sf(gc("Recommend.All"))
                rsi     = _sf(gc("RSI"))
                net_kar = _sf(gc("net_income_ttm"))
                kara_gecti = (net_kar is not None and net_kar > 0)

                # ── Çeyrek Kâr Karşılaştırması ──
                net_kar_fq      = _sf(gc("net_income_fq"))
                net_kar_fq_prev = _sf(gc("net_income_fq_prev"))
                ceyrek_buyume = None
                if net_kar_fq is not None and net_kar_fq_prev is not None and net_kar_fq_prev != 0:
                    ceyrek_buyume = round(((net_kar_fq - net_kar_fq_prev) / abs(net_kar_fq_prev)) * 100, 1)
                ceyrek_karda = (net_kar_fq is not None and net_kar_fq_prev is not None
                                and net_kar_fq > net_kar_fq_prev)

                # ── EV/FAVÖK ──
                ev_favok = _sr(gc("enterprise_value_ebitda_ttm"), 2)

                # ── Sektör (sector + industry ile doğru eşleştirme) ──
                sektor_raw    = gc("sector") or ""
                industry_raw  = gc("industry") or ""
                sektor = _map_sector(str(sektor_raw), str(industry_raw))

                # ── BIST100 + XKTUM ──
                is_bist100 = sembol in BIST100_SET
                is_xktum   = sembol in XKTUM_SET

                # ── EMA Hesapları ──
                ema20  = _sf(gc("EMA20"))
                ema25  = _sf(gc("EMA25"))
                ema50  = _sf(gc("EMA50"))
                ema100 = _sf(gc("EMA100"))
                ema200 = _sf(gc("EMA200"))
                ema20_ustu  = (fiyat > ema20)  if (fiyat and ema20)  else None
                ema25_ustu  = (fiyat > ema25)  if (fiyat and ema25)  else None
                ema50_ustu  = (fiyat > ema50)  if (fiyat and ema50)  else None
                ema100_ustu = (fiyat > ema100) if (fiyat and ema100) else None
                ema200_ustu = (fiyat > ema200) if (fiyat and ema200) else None

                # ── Bollinger Bantları ──
                bb_upper = _sf(gc("BB.upper"))
                bb_lower = _sf(gc("BB.lower"))
                bb_basis = _sf(gc("BB.basis"))
                bb_pozisyon = None  # 0=alt bant, 100=üst bant
                bb_genislik = None  # bant genişliği % (sıkışma tespiti)
                if bb_upper and bb_lower and fiyat:
                    bant_araligi = bb_upper - bb_lower
                    if bant_araligi > 0:
                        bb_pozisyon = round((fiyat - bb_lower) / bant_araligi * 100, 1)
                if bb_upper and bb_lower and bb_basis and bb_basis > 0:
                    bb_genislik = round((bb_upper - bb_lower) / bb_basis * 100, 1)

                # ── Beta ──
                beta = _sr(gc("beta_1_year"), 2)

                # ── FAVÖK/Gelir (EBITDA Marjı) ──
                # TradingView TR piyasası için öz kaynak (equity) verisi gelmiyor.
                # Bunun yerine FAVÖK/Toplam Gelir = EBITDA Marjı kullanıyoruz.
                toplam_gelir    = _sf(gc("total_revenue_ttm"))
                brut_kar_marji  = _sr(gc("gross_margin"), 1)
                favok_val       = _sf(gc("ebitda_ttm"))
                favok_oz_kaynak = None  # Sütun adı korunuyor (frontend değişmesin)
                if favok_val and toplam_gelir and toplam_gelir != 0:
                    favok_oz_kaynak = round(favok_val / toplam_gelir, 2)  # EBITDA marjı

                # ── Hacim Hesapları ──
                hacim     = _sf(gc("volume"))
                hacim_7d  = _sf(gc("average_volume_5d_calc"))   # 5 günlük ≈ haftalık
                hacim_10d = _sf(gc("average_volume_10d_calc"))
                hacim_30d = _sf(gc("average_volume_30d_calc"))
                hacim_sig = _hacim_sinyal(hacim, hacim_7d, hacim_10d)
                hacim_oran = None
                ref_vol = hacim_7d or hacim_10d
                if hacim and ref_vol and ref_vol > 0:
                    hacim_oran = round(hacim / ref_vol, 2)

                # ── Döviz Geliri/Gideri (Placeholder) ──
                doviz_gelir_fazlasi = None  # KAP entegrasyonu bekliyor

                results.append({
                    "sembol":        sembol,
                    "ad":            str(gc("description") or name),
                    "fiyat":         _sr(gc("close"), 2),
                    "degisim":       _sr(gc("change"), 2),
                    "fk":            _sr(gc("price_earnings_ttm"), 2),
                    "pd_dd":         _sr(gc("price_book_ratio"), 2),
                    "ps":            _sr(gc("price_sales_ratio"), 2),
                    "piyasa_degeri": _sf(gc("market_cap_basic")),
                    "toplam_gelir":  toplam_gelir,
                    "brut_kar_marji": brut_kar_marji,
                    "favok":         favok_val,
                    "ev_favok":      ev_favok,
                    "favok_oz_kaynak": favok_oz_kaynak,  # Gerçekte FAVÖK/Gelir (EBITDA marjı)
                    "net_kar":       net_kar,
                    "net_kar_fq":    net_kar_fq,
                    "net_kar_fq_prev": net_kar_fq_prev,
                    "ceyrek_buyume": ceyrek_buyume,
                    "ceyrek_karda":  ceyrek_karda,
                    "net_kar_marj":  _sr(gc("after_tax_margin"), 1),
                    "temettu":       _sr(div, 2),
                    "kara_gecti":    kara_gecti,
                    "sektor":        sektor,
                    "sektor_raw":    sektor_raw,
                    "industry_raw":  industry_raw,
                    "is_bist100":    is_bist100,
                    "is_xktum":      is_xktum,
                    "perf_1m":       _sr(gc("Perf.1M"), 1),
                    "perf_6m":       _sr(gc("Perf.6M"), 1),
                    "perf_1y":       _sr(gc("Perf.1Y"), 1),
                    "perf_3y":       _sr(gc("Perf.3Y"), 1),
                    "perf_5y":       _sr(gc("Perf.5Y"), 1),
                    # Hacim
                    "hacim":         hacim,
                    "hacim_7d":      hacim_7d,
                    "hacim_10d":     hacim_10d,
                    "hacim_30d":     hacim_30d,
                    "hacim_oran":    hacim_oran,
                    "hacim_sinyal":  hacim_sig,
                    "rel_hacim":     _sr(gc("relative_volume_10d_calc"), 2),
                    # RSI & MACD
                    "rsi":           _sr(rsi, 1),
                    "rsi_signal":    _rsi_signal(rsi),
                    "macd":          _sr(macd_v, 4),
                    "macd_sig":      _sr(macd_s, 4),
                    "macd_bullish":  macd_bullish,
                    # Sinyaller
                    "recommend":     _sr(rec, 3),
                    "recommend_ma":  _sr(gc("Recommend.MA"), 3),
                    "recommend_osc": _sr(gc("Recommend.Other"), 3),
                    "signal":        _signal(rec),
                    # EMA
                    "ema20": _sr(ema20, 2), "ema25": _sr(ema25, 2),
                    "ema50": _sr(ema50, 2), "ema100": _sr(ema100, 2), "ema200": _sr(ema200, 2),
                    "ema20_ustu": ema20_ustu, "ema25_ustu": ema25_ustu,
                    "ema50_ustu": ema50_ustu, "ema100_ustu": ema100_ustu,
                    "ema200_ustu": ema200_ustu,
                    # Bollinger
                    "bb_upper": _sr(bb_upper, 2), "bb_lower": _sr(bb_lower, 2),
                    "bb_basis": _sr(bb_basis, 2), "bb_pozisyon": bb_pozisyon,
                    "bb_genislik": bb_genislik,
                    # Beta
                    "beta": beta,
                    # FAVÖK/ÖzKaynak
                    "favok_oz_kaynak": favok_oz_kaynak,
                    # Döviz (placeholder)
                    "doviz_gelir_fazlasi": doviz_gelir_fazlasi,
                    # Pivot — Aylık
                    "s1": s1, "s2": s2, "r1": r1, "r2": r2, "pivot_mid": mid,
                    # Pivot — Günlük
                    "s1_d": s1_d, "s2_d": s2_d, "r1_d": r1_d, "r2_d": r2_d, "pivot_mid_d": mid_d,
                    # Pivot — Aylık OHLC bazlı
                    "s1_w": s1_w, "s2_w": s2_w, "r1_w": r1_w, "r2_w": r2_w, "pivot_mid_w": mid_w,
                    "destek_uzaklik": destek_uzaklik,
                    "direnc_getiri":  direnc_getiri,
                    "destek_yakin":   destek_yakin,
                })

            offset += len(rows)
            with _lock:
                _progress["done"] = len(results)

            if offset >= total_cnt or len(rows) < batch:
                break

            time.sleep(0.5)

        print(f"=== Fetch complete: {len(results)} stocks ===", flush=True)

    except Exception as exc:
        import traceback
        print("=== _fetch() ERROR ===", flush=True)
        print(traceback.format_exc(), flush=True)
        with _lock:
            _cache["error"] = str(exc)

    with _lock:
        _cache["stocks"]     = results
        _cache["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        _cache["loading"]    = False

# ==============================================================================
#  MEVSİMSEL (Sadece lokal)
# ==============================================================================

TOP_50 = [
    "THYAO.IS","GARAN.IS","ASELS.IS","EREGL.IS","KCHOL.IS",
    "SAHOL.IS","AKBNK.IS","ISCTR.IS","SISE.IS","TOASO.IS",
    "FROTO.IS","PGSUS.IS","BIMAS.IS","MGROS.IS","TCELL.IS",
    "TURSG.IS","ENKAI.IS","PETKM.IS","ARCLK.IS","TUPRS.IS",
    "SOKM.IS","HALKB.IS","VAKBN.IS","YKBNK.IS","SASA.IS",
    "TAVHL.IS","CIMSA.IS","LOGO.IS","AEFES.IS","EKGYO.IS",
    "ALARK.IS","TTKOM.IS","DOHOL.IS","VESTL.IS","OTKAR.IS",
    "TKFEN.IS","TSKB.IS","KRDMD.IS","ENJSA.IS","AKENR.IS",
    "SELEC.IS","AKSEN.IS","KONTR.IS","GUBRF.IS","OYAKC.IS",
    "BRSAN.IS","ISFIN.IS","AKFGY.IS","AGHOL.IS","TTRAK.IS",
]
MONTHS_TR = ["Oca","Sub","Mar","Nis","May","Haz","Tem","Agu","Eyl","Eki","Kas","Ara"]
SEASONAL_CACHE_FILE = os.path.join(os.path.dirname(__file__), "seasonal_cache.json")

def _fetch_seasonal():
    """
    Mevsimsel veri çekme — HIZLI versiyon.
    yf.download() ile tüm hisseler TEK seferde çekilir.
    Disk cache ile restart sonrası anında yüklenir.
    """
    # Render / bulut ortamında atla
    is_cloud = (
        os.environ.get("RENDER") or
        os.environ.get("RAILWAY_ENVIRONMENT") or
        os.environ.get("HEROKU_APP_NAME") or
        os.environ.get("FLY_APP_NAME")
    )
    if is_cloud:
        print("Bulut ortami: mevsimsel analiz atlaniyor.", flush=True)
        with _lock:
            _seasonal_cache["data"]    = {}
            _seasonal_cache["loading"] = False
        return

    # ── 1. Disk cache'den oku (varsa anında yükle) ──
    if os.path.exists(SEASONAL_CACHE_FILE):
        try:
            with open(SEASONAL_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            # 24 saatten tazeyse kullan
            import datetime as dt2
            saved_at = cached.get("saved_at", "")
            if saved_at:
                age_h = (dt2.datetime.now() - dt2.datetime.fromisoformat(saved_at)).total_seconds() / 3600
                if age_h < 24:
                    with _lock:
                        _seasonal_cache["data"]       = cached["data"]
                        _seasonal_cache["updated_at"] = cached.get("updated_at", "")
                        _seasonal_cache["loading"]    = False
                    print(f"Seasonal: disk cache'den yuklendi ({len(cached['data'])} hisse, {age_h:.1f}s once)", flush=True)
                    return
        except Exception as e:
            print(f"Seasonal cache okuma hatasi: {e}", flush=True)

    with _lock:
        _seasonal_cache["loading"] = True

    try:
        import yfinance as yf
        from collections import defaultdict

        print("Seasonal: toplu indirme basliyor...", flush=True)

        # ── 2. Tüm hisseleri TEK seferde indir (çok daha hızlı) ──
        tickers_str = " ".join(TOP_50)
        hist = yf.download(
            tickers_str,
            period="5y",
            interval="1mo",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )

        monthly_data = defaultdict(lambda: defaultdict(list))

        if len(TOP_50) == 1:
            # Tek sembol farklı yapı döndürür
            closes  = hist["Close"].squeeze()
            returns = closes.pct_change().dropna() * 100
            for dt_idx, ret in returns.items():
                if ret == ret and ret is not None:
                    monthly_data[TOP_50[0]][dt_idx.month].append(float(ret))
        else:
            for ticker in TOP_50:
                try:
                    closes = hist[ticker]["Close"].squeeze()
                    returns = closes.pct_change().dropna() * 100
                    for dt_idx, ret in returns.items():
                        if ret == ret and ret is not None:
                            monthly_data[ticker][dt_idx.month].append(float(ret))
                except Exception:
                    continue

        seasonal = {}
        for ticker, months in monthly_data.items():
            seasonal[ticker] = {
                MONTHS_TR[m - 1]: round(sum(v) / len(v), 2)
                for m, v in months.items() if v
            }

        updated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

        with _lock:
            _seasonal_cache["data"]       = seasonal
            _seasonal_cache["updated_at"] = updated_at
        print(f"Seasonal: tamamlandi ({len(seasonal)} hisse)", flush=True)

        # ── 3. Disk'e kaydet (sonraki restart için) ──
        try:
            with open(SEASONAL_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "data":       seasonal,
                    "updated_at": updated_at,
                    "saved_at":   datetime.now().isoformat(),
                }, f, ensure_ascii=False)
            print("Seasonal: disk cache guncellendi.", flush=True)
        except Exception as e:
            print(f"Seasonal disk kayit hatasi: {e}", flush=True)

    except Exception as e:
        print("Seasonal error:", e, flush=True)
    finally:
        with _lock:
            _seasonal_cache["loading"] = False

# ==============================================================================
#  API ENDPOİNTLERİ
# ==============================================================================

@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "index.html"))

@app.route("/api/stocks")
def api_stocks():
    with _lock:
        return jsonify({
            "stocks":     _cache["stocks"] or [],
            "updated_at": _cache["updated_at"],
            "loading":    _cache["loading"],
            "error":      _cache["error"],
            "progress":   {"done": _progress["done"], "total": _progress["total"]},
        })

@app.route("/api/seasonal")
def api_seasonal():
    with _lock:
        return jsonify({
            "data":       _seasonal_cache["data"] or {},
            "updated_at": _seasonal_cache["updated_at"],
            "loading":    _seasonal_cache["loading"],
        })

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    with _lock:
        if _cache["loading"]:
            return jsonify({"status": "already_loading"})
        _cache["stocks"]     = None
        _cache["updated_at"] = None
    threading.Thread(target=_fetch, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/api/test")
def api_test():
    """Render'da TradingView bağlantısını test et"""
    try:
        r = requests.post(
            SCAN_URL,
            json={"columns": ["name", "close"], "range": [0, 3],
                  "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
                  "markets": ["turkey"]},
            headers=SCAN_HEADERS,
            timeout=(10, 20),
        )
        return jsonify({"status": r.status_code, "bytes": len(r.content),
                        "sample": r.json().get("data", [])[:2]})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/news/<ticker>")
def api_news(ticker):
    """
    KAP.org.tr haber sentiment endpoint.
    Şu an placeholder — ileride gerçek KAP scraping eklenecek.
    Dönüş: {"ticker": "GARAN", "sentiment": "iyi|notr|kotu", "news": [...]}
    """
    # Placeholder: rastgele ama tutarlı sentiment (ticker hash'e göre)
    import hashlib
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16) % 3
    sentiments = ["iyi", "notr", "kotu"]
    sentiment  = sentiments[h]
    badge_map  = {"iyi": "🟢 İyi", "notr": "🟡 Nötr", "kotu": "🔴 Kötü"}
    placeholder_news = [
        {"tarih": "2026-08-15", "baslik": "KAP haberleri yakında entegre edilecek", "sentiment": "notr"},
        {"tarih": "2026-08-01", "baslik": "Otomatik KAP tarama aktif değil", "sentiment": "notr"},
    ]
    return jsonify({
        "ticker":    ticker.upper().replace(".IS",""),
        "sentiment": sentiment,
        "badge":     badge_map.get(sentiment, "—"),
        "news":      placeholder_news,
        "kaynak":    "placeholder — KAP entegrasyonu yapılacak",
    })

# ==============================================================================
#  BAŞLANGIÇ
# ==============================================================================
# NOT: Gunicorn için thread'ler gunicorn.conf.py'deki post_fork hook'ta baslatilir.
# Lokalde dogrudan calistirilirsa asagidaki __main__ blogu devreye girer.

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # Lokal gelistirme: thread'leri buradan baslat
    threading.Thread(target=_fetch,          daemon=True).start()
    threading.Thread(target=_fetch_seasonal, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
