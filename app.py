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
    "total_equity_mrq",           # Öz Sermaye
    "ebitda_ttm",                # FAVÖK
    "net_income_ttm",            # Net Kar
    "after_tax_margin",           # Net Kar Marjı
    "dividend_yield_recent",      # Temettü
    "Perf.1Y", "Perf.3Y", "Perf.5Y", "Perf.6M", "Perf.1M",
    "volume",
    "average_volume_10d_calc",
    "average_volume_5d_calc",
    "relative_volume_10d_calc",
    "RSI",
    "MACD.macd", "MACD.signal",
    "Recommend.All", "Recommend.MA", "Recommend.Other",
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
        return {"label": "—", "cls": "neutral", "icon": "->"}
    r = float(recommend)
    if r >= 0.5:
        return {"label": "Guclu Al",  "cls": "strong-buy",  "icon": "^^"}
    elif r >= 0.1:
        return {"label": "Al",        "cls": "buy",         "icon": "^"}
    elif r > -0.1:
        return {"label": "Notr",      "cls": "neutral",     "icon": "->"}
    elif r > -0.5:
        return {"label": "Sat",       "cls": "sell",        "icon": "v"}
    else:
        return {"label": "Guclu Sat", "cls": "strong-sell", "icon": "vv"}

def _rsi_signal(rsi):
    if rsi is None:
        return {"label": "—", "cls": "neutral"}
    r = float(rsi)
    if r < 30:
        return {"label": f"Asiri Satim ({r:.0f})", "cls": "oversold"}
    elif r > 70:
        return {"label": f"Asiri Alim ({r:.0f})",  "cls": "overbought"}
    elif r <= 45:
        return {"label": f"Dusuk ({r:.0f})",        "cls": "low-rsi"}
    elif r >= 55:
        return {"label": f"Yuksek ({r:.0f})",       "cls": "high-rsi"}
    else:
        return {"label": f"Normal ({r:.0f})",        "cls": "neutral"}

# ==============================================================================
#  VERİ ÇEKME — DİREKT API
# ==============================================================================

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

                s1  = _sr(gc("Pivot.M.Classic.S1"), 2)
                s2  = _sr(gc("Pivot.M.Classic.S2"), 2)
                r1  = _sr(gc("Pivot.M.Classic.R1"), 2)
                r2  = _sr(gc("Pivot.M.Classic.R2"), 2)
                mid = _sr(gc("Pivot.M.Classic.Middle"), 2)

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

                results.append({
                    "sembol":        sembol,
                    "ad":            str(gc("description") or name),
                    "fiyat":         _sr(gc("close"), 2),
                    "degisim":       _sr(gc("change"), 2),
                    "fk":            _sr(gc("price_earnings_ttm"), 2),
                    "pd_dd":         _sr(gc("price_book_ratio"), 2),
                    "ps":            _sr(gc("price_sales_ratio"), 2),
                    "piyasa_degeri": _sf(gc("market_cap_basic")),
                    "oz_sermaye":    _sf(gc("total_equity_mrq")),
                    "favok":         _sf(gc("ebitda_ttm")),
                    "net_kar":       net_kar,
                    "net_kar_marj":  _sr(gc("after_tax_margin"), 1),
                    "temettu":       _sr(div, 2),
                    "kara_gecti":    kara_gecti,
                    "perf_1m":  _sr(gc("Perf.1M"), 1),
                    "perf_6m":  _sr(gc("Perf.6M"), 1),
                    "perf_1y":  _sr(gc("Perf.1Y"), 1),
                    "perf_3y":  _sr(gc("Perf.3Y"), 1),
                    "perf_5y":  _sr(gc("Perf.5Y"), 1),
                    "hacim":    _sf(gc("volume")),
                    "hacim_21d": _sf(gc("average_volume_10d_calc")),
                    "hacim_5d":  _sf(gc("average_volume_5d_calc")),
                    "rel_hacim": _sr(gc("relative_volume_10d_calc"), 2),
                    "rsi":        _sr(rsi, 1),
                    "rsi_signal": _rsi_signal(rsi),
                    "macd":       _sr(macd_v, 4),
                    "macd_sig":   _sr(macd_s, 4),
                    "macd_bullish": macd_bullish,
                    "recommend":    _sr(rec, 3),
                    "recommend_ma": _sr(gc("Recommend.MA"), 3),
                    "recommend_osc":_sr(gc("Recommend.Other"), 3),
                    "signal":       _signal(rec),
                    "s1": s1, "s2": s2, "r1": r1, "r2": r2,
                    "pivot_mid":      mid,
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

def _fetch_seasonal():
    # Render / bulut ortamında atla (Yahoo Finance engelliyor)
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

    with _lock:
        _seasonal_cache["loading"] = True
    try:
        import yfinance as yf
        from collections import defaultdict
        monthly_data = defaultdict(lambda: defaultdict(list))
        for ticker in TOP_50:
            try:
                hist = yf.download(ticker, period="5y", interval="1mo",
                                   progress=False, auto_adjust=True)
                if hist.empty:
                    continue
                closes  = hist["Close"].squeeze()
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
        print(f"Seasonal complete: {len(seasonal)} stocks", flush=True)
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
