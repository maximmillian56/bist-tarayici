"""
AI Demo Trading — yapay zekanın sanal bakiye ile otomatik alım-satım simülasyonu.

- Bakiye USD cinsindendir (varsayılan 1000 USD). Hisseler TL ile işlem görür,
  USD/TRY kuru ile çevrilir (kur farkı da kâr/zarara yansır).
- Yalnızca long (alım → satım), kaldıraç yok, tam adet hisse.
- İşlem komisyonu simüle edilir (alış ve satışta binde 1).
- Borsa saatlerinde (Pzt-Cum 10:00-18:00, TSİ) her 30 dakikada bir AI analiz eder.
- Kullanıcı yalnızca izler ve başlangıç bakiyesini ayarlayabilir (ayarlayınca portföy sıfırlanır).
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

TZ_TR = timezone(timedelta(hours=3))        # Türkiye'de yaz/kış saati yok (UTC+3)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_FILE = os.path.join(BASE_DIR, "demo_portfolio.json")

DEFAULT_BALANCE = 1000.0
MIN_BALANCE, MAX_BALANCE = 10.0, 1_000_000.0
COMMISSION = 0.001                          # %0.1 her işlemde
CYCLE_MINUTES = 30
MARKET_OPEN, MARKET_CLOSE = (10, 0), (18, 0)
MAX_SNAPSHOTS = 4000
MAX_TRADES = 500
MODEL_NAME = "gemini-3-flash-preview"

_dlock = threading.RLock()
_state = {"running": False, "last_error": None}
_fx = {"rate": None, "ts": 0.0}
_get_stocks = lambda: []
_get_key = lambda: ""


# ──────────────────────────────────────────────────────────────────────────────
#  YARDIMCILAR
# ──────────────────────────────────────────────────────────────────────────────
def _now():
    return datetime.now(TZ_TR)


def _iso(dt=None):
    return (dt or _now()).isoformat(timespec="seconds")


def is_market_open(dt=None):
    dt = dt or _now()
    if dt.weekday() >= 5:
        return False
    cur = (dt.hour, dt.minute)
    return MARKET_OPEN <= cur < MARKET_CLOSE


def _usdtry():
    """USD/TRY kuru (10 dk önbellekli). Alınamazsa son bilinen kur; hiç yoksa None."""
    if _fx["rate"] and time.time() - _fx["ts"] < 600:
        return _fx["rate"]
    try:
        import yfinance as yf
        hist = yf.Ticker("USDTRY=X").history(period="5d")
        if not hist.empty:
            rate = float(hist["Close"].dropna().iloc[-1])
            if rate > 1:
                _fx["rate"], _fx["ts"] = rate, time.time()
                return rate
    except Exception as exc:
        print("[demo] kur alinamadi:", exc, flush=True)
    if _fx["rate"]:
        return _fx["rate"]
    p = _load()
    return p.get("son_kur") if p else None


def _new_portfolio(balance):
    now = _iso()
    return {
        "baslangic_bakiye": round(balance, 2),
        "nakit": round(balance, 2),
        "pozisyonlar": [],
        "islemler": [],
        "snapshots": [{"t": now, "deger": round(balance, 2)}],
        "baslangic_tarih": now,
        "son_analiz": None,
        "son_ozet": "Henüz analiz yapılmadı. Borsa açıldığında AI ilk kararını verecek.",
        "son_kur": None,
        "analiz_sayisi": 0,
    }


def _load():
    with _dlock:
        if not os.path.exists(PORTFOLIO_FILE):
            return None
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            print("[demo] portfoy okunamadi:", exc, flush=True)
            return None


def _save(p):
    with _dlock:
        tmp = PORTFOLIO_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PORTFOLIO_FILE)


def _get_or_create():
    with _dlock:
        p = _load()
        if p is None:
            p = _new_portfolio(DEFAULT_BALANCE)
            _save(p)
        return p


def _price_map():
    out = {}
    for s in _get_stocks() or []:
        fiyat = s.get("fiyat")
        if fiyat:
            out[s["sembol"].replace(".IS", "")] = (float(fiyat), s)
    return out


def _valuate(p, prices, kur):
    """Portföy değerleri (USD). Fiyatı bulunamayan pozisyon maliyetle değerlenir."""
    rows, pos_total = [], 0.0
    for pos in p["pozisyonlar"]:
        sym = pos["sembol"]
        cur_tl = prices.get(sym, (None,))[0]
        avg_tl = pos["maliyet_tl"] / pos["adet"]
        if cur_tl and kur:
            deger = pos["adet"] * cur_tl / kur
        else:
            deger = pos["maliyet_usd"]
        kar = deger - pos["maliyet_usd"]
        pos_total += deger
        rows.append({
            "sembol": sym,
            "adet": pos["adet"],
            "alis_fiyat": round(avg_tl, 2),
            "guncel_fiyat": round(cur_tl, 2) if cur_tl else None,
            "maliyet_usd": round(pos["maliyet_usd"], 2),
            "deger_usd": round(deger, 2),
            "kar_usd": round(kar, 2),
            "kar_yuzde": round(kar / pos["maliyet_usd"] * 100, 2) if pos["maliyet_usd"] else 0,
            "alis_tarih": pos.get("ilk_alis"),
        })
    return rows, pos_total


def _performance(p, toplam_deger):
    """1 hafta / 1 ay / 3 ay getirisi — snapshot geçmişinden hesaplanır."""
    snaps = p.get("snapshots", [])
    start = datetime.fromisoformat(p["baslangic_tarih"])
    now = _now()
    out = {}
    for label, days in (("1h", 7), ("1a", 30), ("3a", 90)):
        cutoff = now - timedelta(days=days)
        ref = None
        for sn in snaps:
            if datetime.fromisoformat(sn["t"]) <= cutoff:
                ref = sn["deger"]
            else:
                break
        elapsed = (now - start).total_seconds() / 86400
        tam = ref is not None
        if ref is None:
            ref = p["baslangic_bakiye"]
        kar = toplam_deger - ref
        out[label] = {
            "kar_usd": round(kar, 2),
            "kar_yuzde": round(kar / ref * 100, 2) if ref else 0,
            "tamamlandi": tam,
            "gecen_gun": round(min(elapsed, days), 1),
            "hedef_gun": days,
        }
    return out


def _trade_stats(p):
    sells = [t for t in p["islemler"] if t["tip"] == "SAT"]
    wins = [t for t in sells if t.get("kar_usd", 0) > 0]
    return {
        "toplam_islem": len(p["islemler"]),
        "kapanan_islem": len(sells),
        "kazanan": len(wins),
        "kazanma_orani": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "gerceklesen_kar": round(sum(t.get("kar_usd", 0) for t in sells), 2),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  AI KARAR MEKANİZMASI
# ──────────────────────────────────────────────────────────────────────────────
def _stock_line(s):
    sym = s["sembol"].replace(".IS", "")
    sig = s["signal"]["label"] if s.get("signal") else "-"
    emas = "".join(
        f"{e}{'+' if s.get(f'ema{e}_ustu') else '-'} "
        for e in (20, 50, 200) if s.get(f"ema{e}_ustu") is not None
    )
    return (
        f"{sym}|fiyat:{s.get('fiyat')}|gun:%{s.get('degisim')}|RSI:{s.get('rsi')}"
        f"|MACD_boga:{s.get('macd_bullish')}|TV:{sig}|EMA:{emas.strip()}"
        f"|BB_poz:%{s.get('bb_pozisyon')}|hacim_x:{s.get('hacim_oran')}"
        f"|D_destek:{s.get('s1_d')}|D_direnc:{s.get('r1_d')}|H_destek:{s.get('s1_w')}|H_direnc:{s.get('r1_w')}"
    )


def _build_prompt(p, rows, prices, kur):
    universe = [s for _, s in prices.values() if s.get("is_bist100")]
    if len(universe) < 20:
        universe = sorted((s for _, s in prices.values()),
                          key=lambda x: x.get("piyasa_degeri") or 0, reverse=True)[:100]
    stock_lines = "\n".join(_stock_line(s) for s in universe)

    pos_lines = "\n".join(
        f"{r['sembol']}: {r['adet']} adet, alış {r['alis_fiyat']} TL, güncel {r['guncel_fiyat']} TL, "
        f"K/Z {r['kar_usd']} USD (%{r['kar_yuzde']})"
        for r in rows
    ) or "Açık pozisyon yok."

    toplam = p["nakit"] + sum(r["deger_usd"] for r in rows)
    recent = "\n".join(
        f"{t['tarih'][:16]} {t['tip']} {t['sembol']} x{t['adet']} @{t['fiyat']} "
        f"{('K/Z ' + str(t['kar_usd']) + ' USD') if t['tip']=='SAT' else ''}"
        for t in p["islemler"][-8:]
    ) or "Henüz işlem yok."

    return f"""Sen Borsa İstanbul'da (BIST) çalışan, kısa vadeli (gün içi / birkaç günlük) işlem yapan bir yapay zeka trader'sın.
Bu bir DEMO simülasyonudur; sanal portföyünü yönetiyorsun. Amacın portföy değerini her gün artırmak, sermayeyi korumak ve
mümkün olduğunca günlük kâr elde etmektir. Alım-satım kararlarını tamamen kendin verirsin.

KURALLAR:
- Sadece long işlem: hisse AL, sonra SAT. Açığa satış ve kaldıraç yok.
- Sadece aşağıdaki listedeki hisseleri işlem yapabilirsin. Fiyat TL, bakiye USD (1 USD = {kur:.2f} TL).
- Adet tam sayı olmalı. Elindeki nakitten fazlasına alım yapamazsın. Elinde olmayan hisseyi satamazsın.
- Her işlemde binde 1 komisyon var; çok sık ve anlamsız alım-satımdan kaçın.
- Riski dağıt (tek hisseye tüm nakdi bağlama), zarar eden pozisyonlarda stop disiplini uygula, kâr hedefe gelince realize et.
- Fırsat yoksa hiç işlem yapma (islemler boş liste olabilir). Her işlem için kısa, veriye dayalı bir sebep yaz.
- Sebepleri Türkçe yaz.

PORTFÖY DURUMU:
Nakit: {p['nakit']:.2f} USD | Toplam değer: {toplam:.2f} USD | Başlangıç: {p['baslangic_bakiye']:.2f} USD
Açık pozisyonlar:
{pos_lines}
Son işlemler:
{recent}

PİYASA VERİSİ (BIST100, şu anki):
{stock_lines}

Yalnızca şu JSON biçiminde cevap ver:
{{"ozet": "piyasa ve kararların 1-2 cümlelik özeti", "islemler": [{{"aksiyon": "AL" veya "SAT", "sembol": "XXXX", "adet": 10, "sebep": "..."}}]}}"""


def _ask_gemini(prompt):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=_get_key())
    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=4096,
            response_mime_type="application/json",
        ),
    )
    text = (resp.text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)


def _execute(p, decision, prices, kur):
    """AI kararlarını güvenlik kurallarıyla uygular. Önce satışlar, sonra alışlar."""
    orders = decision.get("islemler") or []
    if not isinstance(orders, list):
        orders = []
    sells = [o for o in orders if str(o.get("aksiyon", "")).upper() == "SAT"]
    buys = [o for o in orders if str(o.get("aksiyon", "")).upper() == "AL"]
    done = []
    now = _iso()

    for o in sells:
        sym = str(o.get("sembol", "")).upper().replace(".IS", "")
        pos = next((x for x in p["pozisyonlar"] if x["sembol"] == sym), None)
        if not pos or sym not in prices:
            continue
        try:
            adet = int(o.get("adet", 0))
        except (TypeError, ValueError):
            continue
        adet = min(adet, pos["adet"])
        if adet < 1:
            continue
        fiyat = prices[sym][0]
        gelir = adet * fiyat / kur * (1 - COMMISSION)
        oran = adet / pos["adet"]
        maliyet = pos["maliyet_usd"] * oran
        kar = gelir - maliyet
        p["nakit"] += gelir
        pos["maliyet_usd"] -= maliyet
        pos["maliyet_tl"] -= pos["maliyet_tl"] * oran
        pos["adet"] -= adet
        if pos["adet"] <= 0:
            p["pozisyonlar"].remove(pos)
        rec = {"tarih": now, "tip": "SAT", "sembol": sym, "adet": adet, "fiyat": round(fiyat, 2),
               "toplam_usd": round(gelir, 2), "kar_usd": round(kar, 2),
               "kar_yuzde": round(kar / maliyet * 100, 2) if maliyet else 0,
               "sebep": str(o.get("sebep", ""))[:300]}
        p["islemler"].append(rec)
        done.append(rec)

    for o in buys:
        sym = str(o.get("sembol", "")).upper().replace(".IS", "")
        if sym not in prices:
            continue
        try:
            adet = int(o.get("adet", 0))
        except (TypeError, ValueError):
            continue
        fiyat = prices[sym][0]
        unit_usd = fiyat / kur * (1 + COMMISSION)
        adet = min(adet, int(p["nakit"] // unit_usd))
        if adet < 1:
            continue
        maliyet = adet * unit_usd
        p["nakit"] -= maliyet
        pos = next((x for x in p["pozisyonlar"] if x["sembol"] == sym), None)
        if pos:
            pos["adet"] += adet
            pos["maliyet_usd"] += maliyet
            pos["maliyet_tl"] += adet * fiyat
        else:
            p["pozisyonlar"].append({"sembol": sym, "adet": adet, "maliyet_usd": maliyet,
                                     "maliyet_tl": adet * fiyat, "ilk_alis": now})
        rec = {"tarih": now, "tip": "AL", "sembol": sym, "adet": adet, "fiyat": round(fiyat, 2),
               "toplam_usd": round(maliyet, 2), "sebep": str(o.get("sebep", ""))[:300]}
        p["islemler"].append(rec)
        done.append(rec)

    p["nakit"] = round(p["nakit"], 4)
    p["islemler"] = p["islemler"][-MAX_TRADES:]
    return done


def run_cycle(force=False):
    """Bir AI analiz turu. Borsa kapalıysa force=True olmadan çalışmaz."""
    if not force and not is_market_open():
        return {"ok": False, "neden": "Borsa kapalı"}
    if not _get_key():
        return {"ok": False, "neden": "GEMINI_API_KEY ayarlı değil"}
    if _state["running"]:
        return {"ok": False, "neden": "Analiz zaten çalışıyor"}
    _state["running"] = True
    try:
        prices = _price_map()
        if len(prices) < 20:
            return {"ok": False, "neden": "Piyasa verisi henüz yüklenmedi"}
        kur = _usdtry()
        if not kur:
            return {"ok": False, "neden": "USD/TRY kuru alınamadı"}

        with _dlock:
            p = _get_or_create()
            p["son_kur"] = kur
            rows, _ = _valuate(p, prices, kur)
            prompt = _build_prompt(p, rows, prices, kur)

        decision = _ask_gemini(prompt)          # ağ çağrısı kilit dışında

        with _dlock:
            p = _get_or_create()                # kullanıcı bu sırada sıfırlamış olabilir
            kur = _usdtry() or kur
            done = _execute(p, decision, prices, kur)
            rows, pos_total = _valuate(p, prices, kur)
            toplam = p["nakit"] + pos_total
            p["snapshots"].append({"t": _iso(), "deger": round(toplam, 2)})
            p["snapshots"] = p["snapshots"][-MAX_SNAPSHOTS:]
            p["son_analiz"] = _iso()
            p["son_ozet"] = str(decision.get("ozet", ""))[:600] or "Analiz tamamlandı."
            p["son_kur"] = kur
            p["analiz_sayisi"] = p.get("analiz_sayisi", 0) + 1
            _save(p)
        _state["last_error"] = None
        return {"ok": True, "islem_sayisi": len(done), "islemler": done}
    except Exception as exc:
        import traceback
        _state["last_error"] = str(exc)
        print("[demo] analiz hatasi:", traceback.format_exc(), flush=True)
        return {"ok": False, "neden": f"Hata: {exc}"}
    finally:
        _state["running"] = False


def _loop():
    """Arka plan döngüsü: borsa açıkken her CYCLE_MINUTES dakikada bir analiz."""
    time.sleep(45)      # ilk hisse verisinin yüklenmesini bekle
    while True:
        try:
            if is_market_open() and _get_key():
                p = _get_or_create()
                last = p.get("son_analiz")
                due = True
                if last:
                    due = _now() - datetime.fromisoformat(last) >= timedelta(minutes=CYCLE_MINUTES)
                if due:
                    res = run_cycle()
                    if not res.get("ok"):
                        print("[demo] atlandi:", res.get("neden"), flush=True)
                        time.sleep(120)     # veri/kur henüz hazır değilse biraz sonra tekrar dene
        except Exception as exc:
            print("[demo] dongu hatasi:", exc, flush=True)
        time.sleep(60)


_loop_started = False


def start_loop():
    global _loop_started
    if _loop_started:
        return
    _loop_started = True
    threading.Thread(target=_loop, daemon=True, name="demo-loop").start()


# ──────────────────────────────────────────────────────────────────────────────
#  ENDPOINTLER
# ──────────────────────────────────────────────────────────────────────────────
def init(app, get_stocks, get_key):
    """Flask uygulamasına demo endpointlerini bağlar."""
    global _get_stocks, _get_key
    _get_stocks, _get_key = get_stocks, get_key

    @app.route("/api/demo/status")
    def demo_status():
        with _dlock:
            p = _get_or_create()
            prices = _price_map()
            kur = _usdtry() or p.get("son_kur") or 0
            rows, pos_total = _valuate(p, prices, kur) if kur else ([], 0.0)
            toplam = p["nakit"] + pos_total
            kar = toplam - p["baslangic_bakiye"]
            return jsonify({
                "baslangic_bakiye": p["baslangic_bakiye"],
                "nakit": round(p["nakit"], 2),
                "pozisyon_degeri": round(pos_total, 2),
                "toplam_deger": round(toplam, 2),
                "kar_usd": round(kar, 2),
                "kar_yuzde": round(kar / p["baslangic_bakiye"] * 100, 2) if p["baslangic_bakiye"] else 0,
                "pozisyonlar": rows,
                "performans": _performance(p, toplam),
                "istatistik": _trade_stats(p),
                "kur": round(kur, 4) if kur else None,
                "baslangic_tarih": p["baslangic_tarih"],
                "son_analiz": p.get("son_analiz"),
                "son_ozet": p.get("son_ozet"),
                "analiz_sayisi": p.get("analiz_sayisi", 0),
                "borsa_acik": is_market_open(),
                "analiz_suruyor": _state["running"],
                "son_hata": _state["last_error"],
                "ai_aktif": bool(_get_key()),
                "periyot_dk": CYCLE_MINUTES,
                "komisyon": COMMISSION,
            })

    @app.route("/api/demo/history")
    def demo_history():
        with _dlock:
            p = _get_or_create()
            return jsonify({
                "islemler": list(reversed(p["islemler"]))[:100],
                "snapshots": p["snapshots"],
                "baslangic_bakiye": p["baslangic_bakiye"],
            })

    @app.route("/api/demo/reset", methods=["POST"])
    def demo_reset():
        body = request.get_json(silent=True) or {}
        try:
            bakiye = float(body.get("bakiye", DEFAULT_BALANCE))
        except (TypeError, ValueError):
            return jsonify({"error": "Geçersiz bakiye"}), 400
        if not (MIN_BALANCE <= bakiye <= MAX_BALANCE):
            return jsonify({"error": f"Bakiye {MIN_BALANCE:.0f} - {MAX_BALANCE:,.0f} USD arasında olmalı"}), 400
        with _dlock:
            _save(_new_portfolio(bakiye))
        return jsonify({"status": "ok", "bakiye": bakiye})

    @app.route("/api/demo/run", methods=["POST"])
    def demo_run():
        """Test amaçlı manuel tetikleme (arayüzde buton yok). 60 sn'de bir sınırlı."""
        now = time.time()
        if now - _state.get("manual_ts", 0) < 60:
            return jsonify({"ok": False, "neden": "60 saniyede bir çalıştırılabilir"}), 429
        _state["manual_ts"] = now
        force = request.args.get("force") == "1"
        return jsonify(run_cycle(force=force))
