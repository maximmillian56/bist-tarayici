"""
BIST Hisse Senedi Tarayıcısı v2.0
TradingView Screener (temel + teknik + destek/direnç)
yfinance (mevsimsel analiz, arka plan)
"""
import os, threading
from datetime import datetime
from flask import Flask, jsonify, send_file
from tradingview_screener import Query

PORT     = int(os.environ.get("PORT", 5000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app      = Flask(__name__)

# ==============================================================================
#  ÖNBELLEKLER
# ==============================================================================

_cache = {"stocks": None, "updated_at": None, "loading": False, "error": None}
_seasonal_cache = {"data": None, "updated_at": None, "loading": False}
_progress = {"done": 0, "total": 613}
_lock = threading.Lock()

# ==============================================================================
#  YARDIMCI FONKSİYONLAR
# ==============================================================================

def _sf(val):
    """safe_float — None, NaN veya boş değerleri None döndürür."""
    if val is None:
        return None
    try:
        s = str(val).strip().lower()
        if s in ("nan", "none", "null", "inf", "-inf", ""):
            return None
        f = float(val)
        return None if f != f else f   # NaN guard
    except Exception:
        return None

def _sr(val, n=2):
    """safe_round"""
    f = _sf(val)
    return round(f, n) if f is not None else None

def _signal(recommend):
    """Recommend.All → Türkçe etiket + renk sınıfı"""
    if recommend is None:
        return {"label": "—", "cls": "neutral", "icon": "➡️"}
    r = float(recommend)
    if r >= 0.5:
        return {"label": "Güçlü Al",  "cls": "strong-buy",  "icon": "🚀"}
    elif r >= 0.1:
        return {"label": "Al",        "cls": "buy",         "icon": "📈"}
    elif r > -0.1:
        return {"label": "Nötr",      "cls": "neutral",     "icon": "➡️"}
    elif r > -0.5:
        return {"label": "Sat",       "cls": "sell",        "icon": "📉"}
    else:
        return {"label": "Güçlü Sat", "cls": "strong-sell", "icon": "🔻"}

def _rsi_signal(rsi):
    """RSI → Türkçe etiket"""
    if rsi is None:
        return {"label": "—", "cls": "neutral"}
    r = float(rsi)
    if r < 30:
        return {"label": f"Aşırı Satım ({r:.0f})", "cls": "oversold"}
    elif r > 70:
        return {"label": f"Aşırı Alım ({r:.0f})",  "cls": "overbought"}
    elif r <= 45:
        return {"label": f"Düşük ({r:.0f})",        "cls": "low-rsi"}
    elif r >= 55:
        return {"label": f"Yüksek ({r:.0f})",       "cls": "high-rsi"}
    else:
        return {"label": f"Normal ({r:.0f})",        "cls": "neutral"}

# ==============================================================================
#  TRADINGVIEW VERİ ÇEKME
# ==============================================================================

def _fetch():
    with _lock:
        _cache["loading"] = True
        _cache["error"]   = None
        _progress["done"] = 0
        _progress["total"] = 613

    results = []

    # Önce tam sorguyu dene, başarısız olursa minimal sorguya geç
    # Tam sorgu - tüm alanlar
    FULL_FIELDS = [
        "name", "description", "close", "change",
        "price_earnings_ttm",        # F/K
        "price_book_ratio",           # PD/DD
        "price_sales_ratio",          # P/S
        "market_cap_basic",           # Piyasa Değeri
        "total_equity_mrq",           # Öz Sermaye
        "ebitda_ttm",                # FAVÖK
        "net_income_ttm",            # Net Kar
        "after_tax_margin",           # Net Kar Marjı
        "dividend_yield_recent",      # Temettü
        "Perf.1Y", "Perf.3Y", "Perf.5Y", "Perf.6M", "Perf.1M",
        "volume",
        "average_volume_10d_calc",    # 10 günlük (21 yerine, daha stabil)
        "average_volume_5d_calc",
        "relative_volume_10d_calc",
        "RSI", "MACD.macd", "MACD.signal",
        "Recommend.All", "Recommend.MA", "Recommend.Other",
        "Pivot.M.Classic.S1", "Pivot.M.Classic.S2",
        "Pivot.M.Classic.R1", "Pivot.M.Classic.R2",
        "Pivot.M.Classic.Middle",
    ]
    # Minimal fallback - yalnızca kesin çalışan alanlar
    FALLBACK_FIELDS = [
        "name", "description", "close", "change",
        "price_earnings_ttm", "price_book_ratio",
        "market_cap_basic", "dividend_yield_recent",
        "Perf.1Y", "volume", "RSI",
        "Recommend.All",
        "Pivot.M.Classic.S1", "Pivot.M.Classic.R1",
    ]

    df = None
    for fields in [FULL_FIELDS, FALLBACK_FIELDS]:
        try:
            _, df = (
                Query()
                .set_markets("turkey")
                .select(*fields)
                .limit(1000)
                .get_scanner_data()
            )
            print(f"TradingView query OK with {len(fields)} fields -> {len(df)} rows")
            break
        except Exception as exc:
            print(f"TradingView query failed with {len(fields)} fields: {exc}")
            df = None

    if df is None or len(df) == 0:
        print("Both queries failed, returning empty results")
        with _lock:
            _cache["stocks"]     = []
            _cache["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            _cache["loading"]    = False
            _cache["error"]      = "TradingView veri çekme başarısız"
        return

    try:
        for _, row in df.iterrows():
            name   = str(row.get("name", ""))
            sembol = name + ".IS"
            fiyat  = _sf(row.get("close"))

            s1     = _sr(row.get("Pivot.M.Classic.S1"), 2)
            s2     = _sr(row.get("Pivot.M.Classic.S2"), 2)
            r1     = _sr(row.get("Pivot.M.Classic.R1"), 2)
            r2     = _sr(row.get("Pivot.M.Classic.R2"), 2)
            mid    = _sr(row.get("Pivot.M.Classic.Middle"), 2)

            destek_uzaklik = None
            direnc_getiri  = None
            destek_yakin   = False
            if fiyat and s1 and s1 > 0:
                destek_uzaklik = round(((fiyat - s1) / s1) * 100, 2)
                if 0 <= destek_uzaklik <= 5:
                    destek_yakin = True
            if fiyat and r1 and fiyat > 0:
                direnc_getiri = round(((r1 - fiyat) / fiyat) * 100, 2)

            macd_v = _sf(row.get("MACD.macd"))
            macd_s = _sf(row.get("MACD.signal"))
            macd_bullish = (macd_v > macd_s) if (macd_v is not None and macd_s is not None) else None

            div     = _sf(row.get("dividend_yield_recent"))
            rec     = _sf(row.get("Recommend.All"))
            rsi     = _sf(row.get("RSI"))
            net_kar = _sf(row.get("net_income_ttm"))
            kara_gecti = (net_kar is not None and net_kar > 0)

            results.append({
                "sembol":         sembol,
                "ad":             str(row.get("description", name)),
                "fiyat":          _sr(row.get("close"), 2),
                "degisim":        _sr(row.get("change"), 2),
                "fk":             _sr(row.get("price_earnings_ttm"), 2),
                "pd_dd":          _sr(row.get("price_book_ratio"), 2),
                "ps":             _sr(row.get("price_sales_ratio"), 2),
                "piyasa_degeri":  _sf(row.get("market_cap_basic")),
                "oz_sermaye":     _sf(row.get("total_equity_mrq")),
                "favok":          _sf(row.get("ebitda_ttm")),
                "net_kar":        net_kar,
                "net_kar_marj":   _sr(row.get("after_tax_margin"), 1),
                "temettu":        _sr(div, 2),
                "kara_gecti":     kara_gecti,
                "perf_1m":  _sr(row.get("Perf.1M"), 1),
                "perf_6m":  _sr(row.get("Perf.6M"), 1),
                "perf_1y":  _sr(row.get("Perf.1Y"), 1),
                "perf_3y":  _sr(row.get("Perf.3Y"), 1),
                "perf_5y":  _sr(row.get("Perf.5Y"), 1),
                "hacim":         _sf(row.get("volume")),
                "hacim_21d":     _sf(row.get("average_volume_10d_calc")),
                "hacim_5d":      _sf(row.get("average_volume_5d_calc")),
                "rel_hacim":     _sr(row.get("relative_volume_10d_calc"), 2),
                "rsi":           _sr(rsi, 1),
                "rsi_signal":    _rsi_signal(rsi),
                "macd":          _sr(macd_v, 4),
                "macd_sig":      _sr(macd_s, 4),
                "macd_bullish":  macd_bullish,
                "recommend":     _sr(rec, 3),
                "recommend_ma":  _sr(row.get("Recommend.MA"), 3),
                "recommend_osc": _sr(row.get("Recommend.Other"), 3),
                "signal":        _signal(rec),
                "s1": s1, "s2": s2,
                "r1": r1, "r2": r2,
                "pivot_mid":      mid,
                "destek_uzaklik": destek_uzaklik,
                "direnc_getiri":  direnc_getiri,
                "destek_yakin":   destek_yakin,
            })

        with _lock:
            _progress["done"]  = len(results)
            _progress["total"] = len(results)
        print(f"Fetch complete: {len(results)} stocks")

    except Exception as exc:
        import traceback
        print("=== Row processing ERROR ===")
        print(traceback.format_exc())
        with _lock:
            _cache["error"] = str(exc)

    with _lock:
        _cache["stocks"]     = results
        _cache["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        _cache["loading"]    = False


# ==============================================================================
#  MEVSİMSEL ANALİZ (arka plan, yfinance)
# ==============================================================================

TOP_100 = [
    "THYAO.IS","GARAN.IS","ASELS.IS","EREGL.IS","KCHOL.IS",
    "SAHOL.IS","AKBNK.IS","ISCTR.IS","SISE.IS","TOASO.IS",
    "FROTO.IS","PGSUS.IS","BIMAS.IS","MGROS.IS","TCELL.IS",
    "TURSG.IS","ENKAI.IS","KOZAL.IS","PETKM.IS","ARCLK.IS",
    "TUPRS.IS","SOKM.IS","HALKB.IS","VAKBN.IS","YKBNK.IS",
    "SASA.IS","TAVHL.IS","CIMSA.IS","LOGO.IS","AEFES.IS",
    "EKGYO.IS","ALARK.IS","TTKOM.IS","DOHOL.IS","VESTL.IS",
    "AGHOL.IS","GUBRF.IS","OTKAR.IS","OYAKC.IS","TKFEN.IS",
    "TSKB.IS","KRDMD.IS","BRSAN.IS","ENJSA.IS","AKENR.IS",
    "SELEC.IS","ISFIN.IS","AKSEN.IS","AKFGY.IS","KONTR.IS",
]

MONTHS_TR = ["Oca","Şub","Mar","Nis","May","Haz","Tem","Ağu","Eyl","Eki","Kas","Ara"]

def _fetch_seasonal():
    """
    Mevsimsel analiz: yalnızca lokal ortamda çalışır.
    Render/bulut ortamında Yahoo Finance tüm istekleri engelliyor.
    Render otomatik RENDER=true env var set eder — bunu kullanıyoruz.
    """
    # Bulut ortamı tespiti — Render, Railway, Heroku vb. kendi env var'larını set eder
    is_cloud = (
        os.environ.get("RENDER") or
        os.environ.get("RAILWAY_ENVIRONMENT") or
        os.environ.get("HEROKU_APP_NAME") or
        os.environ.get("FLY_APP_NAME") or
        os.environ.get("KOYEB_APP")
    )

    if is_cloud:
        print("Bulut ortami tespit edildi (RENDER env var), mevsimsel analiz atlanıyor.")
        with _lock:
            _seasonal_cache["data"]    = {}
            _seasonal_cache["loading"] = False
        return

    with _lock:
        _seasonal_cache["loading"] = True
    try:
        import yfinance as yf
        from collections import defaultdict

        monthly_data = defaultdict(lambda: defaultdict(list))

        for ticker in TOP_100:
            try:
                hist = yf.download(ticker, period="5y", interval="1mo",
                                   progress=False, auto_adjust=True)
                if hist.empty:
                    continue
                closes = hist["Close"].squeeze()
                returns = closes.pct_change().dropna() * 100
                for dt, ret in returns.items():
                    if ret == ret:
                        monthly_data[ticker][dt.month].append(float(ret))
            except Exception:
                continue

        seasonal = {}
        for ticker, months in monthly_data.items():
            seasonal[ticker] = {
                MONTHS_TR[m - 1]: round(sum(v) / len(v), 2)
                for m, v in months.items() if v
            }

        with _lock:
            _seasonal_cache["data"]       = seasonal
            _seasonal_cache["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        print(f"Seasonal fetch complete: {len(seasonal)} stocks")
    except Exception as e:
        print("Seasonal fetch error:", e)
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

# ==============================================================================
#  BAŞLANGIÇ
# ==============================================================================

threading.Thread(target=_fetch,          daemon=True).start()
threading.Thread(target=_fetch_seasonal, daemon=True).start()

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
