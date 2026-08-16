"""
BIST Hisse Senedi Tarayıcısı — Web Uygulaması Backend
Flask
"""
import os, time, threading
from datetime import datetime
from flask import Flask, jsonify, send_file
from tradingview_screener import Query

# ==============================================================================
#  ⚙️  CONFIG
# ==============================================================================

PORT = int(os.environ.get("PORT", 5000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================================================================
#  FLASK KURULUMU
# ==============================================================================

app      = Flask(__name__)

# Veri cache'i
_cache = {
    "stocks"    : None,
    "updated_at": None,
    "loading"   : False,
    "error"     : None,
}
# İlerleme takibi (yükleme sırasında kaç hisse tamamlandı)
_progress = {"done": 0, "total": 0}
_lock     = threading.Lock()

# ==============================================================================
#  VERİ ÇEKME FONKSİYONU
# ==============================================================================

def _fetch():
    global HISSE_LISTESI
    try:
        from tradingview_screener import Query
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "tradingview_screener"])
        from tradingview_screener import Query

    with _lock:
        _cache["loading"] = True
        _cache["error"]   = None
        # We don't have a reliable progress bar since it's 1 request, just say 0 -> 100%
        _progress["done"] = 0
        _progress["total"] = 613

    results = []
    
    try:
        q = Query().set_markets('turkey').select(
            'name', 'description', 'close', 'price_earnings_ttm', 
            'price_book_ratio', 'dividend_yield_recent', 
            'market_cap_basic', 'volume'
        ).limit(1000)
        
        _, df = q.get_scanner_data()
        
        for _, row in df.iterrows():
            # Tradingview returns ticker as 'BIST:GARAN', name as 'GARAN'
            # We construct sembol as 'GARAN.IS'
            name = str(row.get('name', ''))
            sembol = name + ".IS"
            
            def safe_float(val):
                if val is None or str(val).lower() == 'nan': return None
                try:
                    f = float(val)
                    return None if f != f else f
                except Exception:
                    return None
            
            div = safe_float(row.get('dividend_yield_recent'))
            if div is not None:
                # Tradingview returns dividend as percentage e.g., 4.02 for 4.02%
                div = round(div / 100.0, 6)
                
            results.append({
                "sembol"       : sembol,
                "ad"           : str(row.get('description', '')) or SIRKET_ADLARI.get(sembol, sembol),
                "fiyat"        : safe_float(row.get('close')),
                "fk"           : safe_float(row.get('price_earnings_ttm')),
                "pd_dd"        : safe_float(row.get('price_book_ratio')),
                "temettu"      : div,
                "piyasa_degeri": safe_float(row.get('market_cap_basic')),
                "hacim"        : safe_float(row.get('volume')),
                "doviz"        : "TRY",
                "hata"         : False,
            })
            
        with _lock:
            _progress["done"] = len(results)
            _progress["total"] = len(results)

    except Exception as exc:
        print("TradingView Fetch Error:", exc)
        with _lock:
            _cache["error"] = str(exc)

    with _lock:
        _cache["stocks"]     = results
        _cache["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        _cache["loading"]    = False

# ==============================================================================
#  API ENDPOİNTLERİ
# ==============================================================================

@app.route("/")
def index():
    """Ana HTML sayfasını serve eder."""
    return send_file(os.path.join(BASE_DIR, "index.html"))


@app.route("/api/stocks")
def api_stocks():
    """
    Hisse verilerini JSON olarak döndürür.
    loading=True ise veriler henüz yükleniyor demektir.
    progress ile kaç hisse tamamlandığı görülebilir.
    """
    with _lock:
        return jsonify({
            "stocks"    : _cache["stocks"] or [],
            "updated_at": _cache["updated_at"],
            "loading"   : _cache["loading"],
            "progress"  : {
                "done" : _progress["done"],
                "total": _progress["total"],
            },
        })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """
    Kullanıcı 'Yenile' butonuna basınca çağrılır.
    Cache'i temizler ve yeni bir veri çekme thread'i başlatır.
    """
    with _lock:
        if _cache["loading"]:
            return jsonify({"status": "already_loading"})
        _cache["stocks"]     = None
        _cache["updated_at"] = None

    threading.Thread(target=_fetch, daemon=True).start()
    return jsonify({"status": "started"})


# ==============================================================================
#  BAŞLANGIÇ (WSGI & Lokal)
# ==============================================================================

# Gunicorn veya PythonAnywhere gibi WSGI sunucuları uygulamayı içe aktardığında
# arka plan thread'inin başlaması için bunu global alanda tetikliyoruz.
threading.Thread(target=_fetch, daemon=True).start()

if __name__ == "__main__":
    import sys, webbrowser

    # Windows konsolunda UTF-8 (emoji ve Türkçe karakterler için)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("\n" + "=" * 58)
    print("  BIST Hisse Senedi Tarayicisi")
    print(f"  http://localhost:{PORT}  adresinde calisiyor")
    print("  Tüm hisseler yükleniyor...")
    print("=" * 58 + "\n")

    # Tarayıcıyı otomatik aç
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
