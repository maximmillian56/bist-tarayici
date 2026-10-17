"""
===============================================================================
  Kripto Para Veri Çekme Otomasyonu
  Yazar: Antigravity
  Açıklama: CoinGecko (veya CoinMarketCap) API'sinden kripto para verilerini
             günlük olarak çekip CSV dosyasına kaydeder.
  Desteklenen Modlar:
    - Daemon modu : python crypto_fetcher.py           (sürekli çalışır, schedule ile)
    - Tek seferlik: python crypto_fetcher.py --once    (bir kez çalışıp çıkar)
===============================================================================
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime

import requests
import pandas as pd
import schedule

# ==============================================================================
#  ⚙️  YAPILANDIRMA (CONFIG) BÖLÜMİ
#  Tüm ayarları buradan kolayca değiştirebilirsiniz.
# ==============================================================================

# --- API Seçimi ---
# "coingecko"  : Ücretsiz, API key gerektirmez (varsayılan)
# "coinmarketcap" : API key gerektirir, CMC_API_KEY değişkenine key girin
API_PROVIDER = "coingecko"

# --- CoinMarketCap API Key (sadece API_PROVIDER = "coinmarketcap" ise gerekli) ---
CMC_API_KEY = "BURAYA_COINMARKETCAP_API_KEY_GIRIN"

# --- Takip Edilecek Coin Listesi ---
# CoinGecko için coin ID kullanılır (küçük harf, tire ile ayrılmış)
# CoinMarketCap için sembol kullanılır
COIN_IDS_COINGECKO = [
    "bitcoin",
    "ethereum",
    "solana",
    "binancecoin",
    "ripple",
    "cardano",
    "dogecoin",
    "avalanche-2",
    "polkadot",
    "chainlink",
]

COIN_SYMBOLS_CMC = [
    "BTC", "ETH", "SOL", "BNB", "XRP",
    "ADA", "DOGE", "AVAX", "DOT", "LINK",
]

# --- Dosya Yolları ---
# CSV ve log dosyalarının kaydedileceği klasör (scriptin çalıştığı dizin)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.join(BASE_DIR, "crypto_data.csv")
LOG_FILE_PATH = os.path.join(BASE_DIR, "automation.log")

# --- Zamanlama ---
# Her gün saat kaçta çalışacağı (24 saat formatı, "HH:MM")
SCHEDULE_TIME = "09:00"

# --- API İstek Ayarları ---
REQUEST_TIMEOUT = 30            # Saniye cinsinden zaman aşımı
RETRY_DELAYS = [5, 15, 30]     # Hata durumunda bekleme süreleri (saniye)

# --- CoinGecko Ücretsiz API Ayarları ---
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_RATE_LIMIT_DELAY = 1.5  # İstekler arası bekleme süresi (saniye)

# --- CoinMarketCap API Ayarları ---
CMC_BASE_URL = "https://pro-api.coinmarketcap.com/v1"

# ==============================================================================
#  📋  LOG YAPILANDIRMASI
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        # Log dosyasına yaz (append modunda, encoding UTF-8)
        logging.FileHandler(LOG_FILE_PATH, mode="a", encoding="utf-8"),
        # Aynı zamanda terminale de yaz
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

# ==============================================================================
#  🌐  API ÇAĞRI FONKSİYONLARI
# ==============================================================================

def _make_request_with_retry(url: str, params: dict, headers: dict = None) -> dict | list | None:
    """
    Verilen URL'ye HTTP GET isteği gönderir.
    Hata durumunda artan bekleme süresiyle 3 kez tekrar dener.

    Args:
        url     : İstek yapılacak tam URL
        params  : URL query parametreleri
        headers : HTTP başlıkları (CoinMarketCap için API key gerekli)

    Returns:
        Başarılı ise JSON yanıtı (dict veya list), başarısız ise None
    """
    headers = headers or {}

    for deneme_no, bekleme_suresi in enumerate(RETRY_DELAYS, start=1):
        try:
            logger.info(f"API isteği gönderiliyor (Deneme {deneme_no}/{len(RETRY_DELAYS)}): {url}")
            yanit = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            # HTTP hata kodlarını yakala (4xx, 5xx)
            yanit.raise_for_status()

            logger.info(f"API isteği başarılı. HTTP {yanit.status_code}")
            return yanit.json()

        except requests.exceptions.Timeout:
            logger.warning(f"⏱  Zaman aşımı (Deneme {deneme_no}). {bekleme_suresi}sn bekleniyor...")

        except requests.exceptions.HTTPError as e:
            durum_kodu = e.response.status_code if e.response is not None else "?"
            logger.warning(f"🚫  HTTP Hatası {durum_kodu}: {e} (Deneme {deneme_no}). {bekleme_suresi}sn bekleniyor...")

            # 429 Too Many Requests → daha uzun bekle
            if durum_kodu == 429:
                logger.warning("⚠️  Rate limit aşıldı! Ekstra bekleme uygulanıyor...")
                time.sleep(bekleme_suresi * 2)
                continue

        except requests.exceptions.ConnectionError:
            logger.warning(f"🔌  Bağlantı hatası (Deneme {deneme_no}). {bekleme_suresi}sn bekleniyor...")

        except requests.exceptions.RequestException as e:
            logger.error(f"❌  Beklenmeyen istek hatası: {e}")

        # Son denemede bile başarısız olunursa None dön
        if deneme_no < len(RETRY_DELAYS):
            time.sleep(bekleme_suresi)

    logger.error(f"🔴  Tüm denemeler başarısız oldu! URL: {url}")
    return None


def fetch_data_coingecko() -> list[dict] | None:
    """
    CoinGecko /coins/markets endpoint'inden coin verilerini çeker.

    Endpoint: GET https://api.coingecko.com/api/v3/coins/markets
    Rate Limit: 30 istek/dakika (ücretsiz Demo plan)
    Response alanları kullanılanlar:
        id, symbol, name, current_price, price_change_percentage_24h,
        market_cap, total_volume

    Returns:
        Coin verilerini içeren dict listesi veya None
    """
    url = f"{COINGECKO_BASE_URL}/coins/markets"

    # CoinGecko birden fazla coin ID'sini virgülle alır
    coin_ids_str = ",".join(COIN_IDS_COINGECKO)

    params = {
        "vs_currency": "usd",           # USD cinsinden fiyat
        "ids": coin_ids_str,             # Takip edilecek coin'ler
        "order": "market_cap_desc",      # Piyasa değerine göre sırala
        "per_page": 250,                 # Sayfa başına sonuç sayısı (maks 250)
        "page": 1,
        "sparkline": "false",            # Mini grafik verisini dahil etme
        "price_change_percentage": "24h",
    }

    yanit_verisi = _make_request_with_retry(url, params)

    if yanit_verisi is None:
        return None

    # Sadece ihtiyacımız olan alanları çıkar
    zaman_damgasi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    islenmiş_veriler = []

    for coin in yanit_verisi:
        islenmiş_veriler.append({
            "timestamp":             zaman_damgasi,
            "coin_id":               coin.get("id", ""),
            "name":                  coin.get("name", ""),
            "symbol":                coin.get("symbol", "").upper(),
            "price_usd":             coin.get("current_price"),
            "price_change_24h_pct":  coin.get("price_change_percentage_24h"),
            "market_cap_usd":        coin.get("market_cap"),
            "volume_24h_usd":        coin.get("total_volume"),
        })

    logger.info(f"✅  CoinGecko'dan {len(islenmiş_veriler)} coin verisi başarıyla çekildi.")
    return islenmiş_veriler


def fetch_data_coinmarketcap() -> list[dict] | None:
    """
    CoinMarketCap /cryptocurrency/quotes/latest endpoint'inden coin verilerini çeker.

    Endpoint: GET https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest
    Rate Limit: Plan bazlı (Basic plan: 333 istek/gün)
    Dokümantasyon: https://coinmarketcap.com/api/documentation/v1/

    Returns:
        Coin verilerini içeren dict listesi veya None
    """
    if CMC_API_KEY == "BURAYA_COINMARKETCAP_API_KEY_GIRIN" or not CMC_API_KEY:
        logger.error("❌  CoinMarketCap API key ayarlanmamış! CMC_API_KEY değişkenini doldurun.")
        return None

    url = f"{CMC_BASE_URL}/cryptocurrency/quotes/latest"

    # CoinMarketCap sembol listesini virgülle alır
    semboller_str = ",".join(COIN_SYMBOLS_CMC)

    params = {
        "symbol": semboller_str,
        "convert": "USD",
    }

    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
        "Accept": "application/json",
    }

    yanit_verisi = _make_request_with_retry(url, params, headers)

    if yanit_verisi is None:
        return None

    # CMC yanıt yapısı: {"data": {"BTC": {...}, "ETH": {...}}, "status": {...}}
    if "data" not in yanit_verisi:
        logger.error(f"❌  CoinMarketCap yanıtında 'data' alanı bulunamadı: {yanit_verisi}")
        return None

    zaman_damgasi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    islenmiş_veriler = []

    for sembol, coin_verisi in yanit_verisi["data"].items():
        # Bazı semboller birden fazla sonuç döndürebilir (liste olarak gelir)
        if isinstance(coin_verisi, list):
            coin_verisi = coin_verisi[0]

        usd_verisi = coin_verisi.get("quote", {}).get("USD", {})

        islenmiş_veriler.append({
            "timestamp":             zaman_damgasi,
            "coin_id":               str(coin_verisi.get("id", "")),
            "name":                  coin_verisi.get("name", ""),
            "symbol":                coin_verisi.get("symbol", "").upper(),
            "price_usd":             usd_verisi.get("price"),
            "price_change_24h_pct":  usd_verisi.get("percent_change_24h"),
            "market_cap_usd":        usd_verisi.get("market_cap"),
            "volume_24h_usd":        usd_verisi.get("volume_24h"),
        })

    logger.info(f"✅  CoinMarketCap'ten {len(islenmiş_veriler)} coin verisi başarıyla çekildi.")
    return islenmiş_veriler


def fetch_data() -> list[dict] | None:
    """
    CONFIG'e göre uygun API sağlayıcısından veri çeker.
    API_PROVIDER değişkenini değiştirerek kaynağı kolayca değiştirebilirsiniz.

    Returns:
        Coin verilerini içeren dict listesi veya None (hata durumunda)
    """
    if API_PROVIDER.lower() == "coingecko":
        # CoinGecko ücretsiz API'ye aşırı yük bindirmemek için kısa bekleme
        time.sleep(COINGECKO_RATE_LIMIT_DELAY)
        return fetch_data_coingecko()

    elif API_PROVIDER.lower() == "coinmarketcap":
        return fetch_data_coinmarketcap()

    else:
        logger.error(f"❌  Geçersiz API sağlayıcısı: '{API_PROVIDER}'. 'coingecko' veya 'coinmarketcap' kullanın.")
        return None


# ==============================================================================
#  💾  CSV KAYIT FONKSİYONU
# ==============================================================================

def save_to_csv(veriler: list[dict]) -> bool:
    """
    Coin verilerini CSV dosyasına kaydeder.

    - Dosya yoksa: Header satırıyla birlikte yeni dosya oluşturur.
    - Dosya varsa: Mevcut verinin üzerine yazmadan sona satır ekler (append).
    - pandas DataFrame kullanılarak veri işlenir.

    CSV Sütunları:
        timestamp, coin_id, name, symbol, price_usd,
        price_change_24h_pct, market_cap_usd, volume_24h_usd

    Args:
        veriler : fetch_data() fonksiyonundan gelen dict listesi

    Returns:
        Başarılıysa True, hata varsa False
    """
    try:
        # Dict listesini pandas DataFrame'e çevir
        df_yeni = pd.DataFrame(veriler)

        # Sütun sırasını standart hale getir
        sutun_sirasi = [
            "timestamp",
            "coin_id",
            "name",
            "symbol",
            "price_usd",
            "price_change_24h_pct",
            "market_cap_usd",
            "volume_24h_usd",
        ]
        df_yeni = df_yeni[sutun_sirasi]

        # Sayısal sütunlarda yuvarlama (okunabilirlik için)
        df_yeni["price_usd"]            = df_yeni["price_usd"].round(4)
        df_yeni["price_change_24h_pct"] = df_yeni["price_change_24h_pct"].round(2)
        df_yeni["market_cap_usd"]       = df_yeni["market_cap_usd"].astype("Int64", errors="ignore")
        df_yeni["volume_24h_usd"]       = df_yeni["volume_24h_usd"].astype("Int64", errors="ignore")

        dosya_mevcut = os.path.exists(CSV_FILE_PATH)

        # Dosya varsa append, yoksa yeni oluştur (header ile)
        df_yeni.to_csv(
            CSV_FILE_PATH,
            mode="a",               # Append modu
            header=not dosya_mevcut,# Dosya yoksa header yaz
            index=False,            # Satır indeksini kaydetme
            encoding="utf-8-sig",   # Excel'de Türkçe karakter desteği
        )

        if dosya_mevcut:
            logger.info(f"📄  {len(veriler)} satır mevcut CSV'ye eklendi: {CSV_FILE_PATH}")
        else:
            logger.info(f"📄  Yeni CSV dosyası oluşturuldu ve {len(veriler)} satır yazıldı: {CSV_FILE_PATH}")

        return True

    except PermissionError:
        logger.error(f"❌  Dosyaya yazma izni yok: {CSV_FILE_PATH} — Dosya başka bir programda açık olabilir.")
        return False
    except Exception as e:
        logger.error(f"❌  CSV yazma hatası: {type(e).__name__}: {e}")
        return False


# ==============================================================================
#  🔄  ANA İŞ FONKSİYONU
# ==============================================================================

def run_job():
    """
    Tek bir çalışma döngüsünü gerçekleştirir:
      1. API'den veri çek
      2. Veriyi CSV'ye kaydet
      3. Sonucu logla

    Bu fonksiyon hem schedule (daemon modu) hem de --once (tek seferlik)
    modunda çağrılır.
    """
    logger.info("=" * 60)
    logger.info(f"🚀  Veri çekme işlemi başlıyor. Sağlayıcı: {API_PROVIDER.upper()}")
    logger.info("=" * 60)

    # 1. Veri çek
    veriler = fetch_data()

    if veriler is None:
        logger.error("🔴  Veri çekilemedi. Bu döngü atlanıyor.")
        logger.info("=" * 60)
        return

    if len(veriler) == 0:
        logger.warning("⚠️  API yanıtı boş veri döndürdü.")
        logger.info("=" * 60)
        return

    # 2. CSV'ye kaydet
    basarili = save_to_csv(veriler)

    if basarili:
        logger.info(f"✅  İşlem tamamlandı. Toplam {len(veriler)} coin kaydedildi.")
    else:
        logger.error("🔴  CSV kaydetme başarısız oldu.")

    logger.info("=" * 60)


# ==============================================================================
#  ⏰  ANA PROGRAM GİRİŞ NOKTASI
# ==============================================================================

def main():
    """
    Programın giriş noktası.

    İki modda çalışır:
    --once : Tek seferlik çalışır ve çıkar (cron ile kullanım için ideal)
    (varsayılan): Daemon modu — schedule ile her gün SCHEDULE_TIME saatinde çalışır
    """
    # Komut satırı argümanlarını ayrıştır
    parser = argparse.ArgumentParser(
        description="Kripto para verisi çekme ve CSV kayıt otomasyonu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Kullanım Örnekleri:
  python crypto_fetcher.py             # Daemon modu (sürekli çalışır)
  python crypto_fetcher.py --once      # Tek seferlik çalıştır ve çık
  python crypto_fetcher.py --help      # Bu yardım mesajını göster

Cron ile Günlük Zamanlama (Linux/Mac):
  0 9 * * * /usr/bin/python3 /path/to/crypto_fetcher.py --once
        """,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Tek seferlik çalıştır (cron ile kullanım için)",
    )
    args = parser.parse_args()

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║       Kripto Para Veri Çekme Otomasyonu Başlatıldı      ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")
    logger.info(f"  API Sağlayıcısı : {API_PROVIDER.upper()}")
    logger.info(f"  CSV Dosyası     : {CSV_FILE_PATH}")
    logger.info(f"  Log Dosyası     : {LOG_FILE_PATH}")
    logger.info(f"  Takip Edilen    : {len(COIN_IDS_COINGECKO if API_PROVIDER == 'coingecko' else COIN_SYMBOLS_CMC)} coin")

    # ── TEK SEFERLİK MOD (--once) ──────────────────────────────────────────
    if args.once:
        logger.info("  Çalışma Modu    : Tek Seferlik (--once)")
        logger.info("─" * 60)
        run_job()
        logger.info("🏁  Tek seferlik çalışma tamamlandı. Program sonlandırılıyor.")
        sys.exit(0)

    # ── DAEMON MODU (schedule) ─────────────────────────────────────────────
    logger.info(f"  Çalışma Modu    : Daemon (Her gün saat {SCHEDULE_TIME})")
    logger.info("─" * 60)
    logger.info(f"⏰  Zamanlama ayarlandı: Her gün saat {SCHEDULE_TIME}'de çalışacak.")
    logger.info("   Durdurmak için Ctrl+C tuşlayın.")

    # İlk çalıştırmayı hemen yap (beklemeye gerek kalmadan)
    logger.info("▶️  İlk veri çekme işlemi hemen başlatılıyor...")
    run_job()

    # Sonraki çalıştırmalar için zamanlama kur
    schedule.every().day.at(SCHEDULE_TIME).do(run_job)

    # Sürekli çalışma döngüsü
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Her dakika zamanlama kontrolü yap
    except KeyboardInterrupt:
        logger.info("\n🛑  Kullanıcı tarafından durduruldu (Ctrl+C).")
        logger.info("👋  Program sonlandırılıyor...")
        sys.exit(0)


if __name__ == "__main__":
    main()
