"""
===============================================================================
  BIST Hisse Senedi Temel Veri Çekme & Filtreleme Otomasyonu
  Yazar    : Antigravity
  Açıklama : yfinance aracılığıyla BIST hisselerinin temel finansal
             verilerini günlük olarak çekip CSV'ye kaydeder ve
             belirlenen kriterlere göre hisseleri filtreler.
  Modlar   :
    - Daemon  : python bist_fetcher.py          (schedule ile sürekli çalışır)
    - Tek sef.: python bist_fetcher.py --once   (bir kez çalışıp çıkar)
===============================================================================
"""

import io
import os
import sys
import time
import logging
import argparse
from datetime import datetime

import yfinance as yf
import pandas as pd
import schedule

# ==============================================================================
#  ⚙️  YAPILANDIRMA (CONFIG) BÖLÜMİ
#  Tüm ayarları buradan düzenleyebilirsiniz — başka dosyaya dokunmaya gerek yok.
# ==============================================================================

# ------------------------------------------------------------------------------
# 📋  TAKİP EDİLECEK BIST HİSSE LİSTESİ
# Hisse eklemek/çıkarmak için bu listeyi düzenleyin.
# Format: "SEMBOL.IS"  (Yahoo Finance BIST formatı)
# ------------------------------------------------------------------------------
HISSE_LISTESI = [
    "THYAO.IS",   # Türk Hava Yolları
    "GARAN.IS",   # Garanti BBVA Bankası
    "ASELS.IS",   # Aselsan
    "EREGL.IS",   # Ereğli Demir Çelik
    "KCHOL.IS",   # Koç Holding
    "SAHOL.IS",   # Sabancı Holding
    "AKBNK.IS",   # Akbank
    "ISCTR.IS",   # İş Bankası C
    "SISE.IS",    # Şişecam
    "TOASO.IS",   # Tofaş Oto Fab.
    "FROTO.IS",   # Ford Otosan
    "PGSUS.IS",   # Pegasus Hava Taşımacılığı
    "BIMAS.IS",   # BİM Birleşik Mağazalar
    "MGROS.IS",   # Migros Ticaret  (doğru sembol: MGROS)
    "TCELL.IS",   # Turkcell
    "TURSG.IS",   # Türkiye Sigorta
    "ENKAI.IS",   # Enka İnşaat
    "KOZAL.IS",   # Koza Altın (Yahoo Finance'te mevcut değilse "Veri Yok" olarak kaydedilir)
    "PETKM.IS",   # Petkim Petrokimya
    "ARCLK.IS",   # Arçelik
]

# ------------------------------------------------------------------------------
# 🔍  FİLTRE EŞİK DEĞERLERİ
# Her değeri ihtiyacınıza göre değiştirebilirsiniz.
# None yaparsanız o filtre devre dışı kalır.
# ------------------------------------------------------------------------------
FILTRE_ESIKLERI = {
    # F/K oranı aralığı: 0 < F/K < FK_MAX → potansiyel "ucuz" hisse işareti
    "fk_min":            0.0,        # F/K minimum değeri (0 altı → zarar var)
    "fk_max":            15.0,       # F/K maksimum değeri

    # PD/DD oranı: bu değerin altındaki hisseler defter değerinin altında işlem görüyor
    "pd_dd_max":         1.5,        # PD/DD üst sınırı

    # Temettü verimi minimum (yüzde değil, oran: 0.03 → %3)
    "temettu_min":       0.03,       # Minimum temettü verimi

    # Piyasa değeri minimum (TL cinsinden) — çok küçük hisseleri elemek için
    "piyasa_degeri_min": 1_000_000_000,   # 1 milyar TL

    # Günlük işlem hacmi minimum (adet) — likidite kontrolü
    "hacim_min":         1_000_000,       # 1 milyon adet
}

# ------------------------------------------------------------------------------
# 📂  DOSYA YOLLARI
# ------------------------------------------------------------------------------
BASE_DIR              = os.path.dirname(os.path.abspath(__file__))
CSV_TUM_HISSELER      = os.path.join(BASE_DIR, "tum_hisseler.csv")
CSV_FILTRELENMIS      = os.path.join(BASE_DIR, "filtrelenmis_hisseler.csv")
LOG_DOSYASI           = os.path.join(BASE_DIR, "automation.log")

# ------------------------------------------------------------------------------
# ⏰  ZAMANLAMA
# ------------------------------------------------------------------------------
# Her gün BIST kapanışından sonra hangi saatte çalışacak ("HH:MM")
CALISMA_SAATI = "18:30"

# ------------------------------------------------------------------------------
# 🌐  VERİ ÇEKME AYARLARI
# ------------------------------------------------------------------------------
ISTEK_ZAMAN_ASIMI   = 30          # Saniye cinsinden zaman aşımı
TEKRAR_BEKLEMELER   = [5, 15, 30] # Hata durumunda bekleme süreleri (saniye)
HISSELER_ARASI_BEKLEME = 0.5      # Hisseler arasında bekleme (rate limit önlemi)

# ==============================================================================
#  📋  LOG YAPILANDIRMASI
# ==============================================================================

# Windows konsolunda UTF-8 / emoji desteği için özel StreamHandler
class _SafeStreamHandler(logging.StreamHandler):
    """
    Windows PowerShell/CMD konsolunda UTF-8 karakter ve emoji içeren
    log mesajlarını güvenle yazar. Kodlanamayan karakterler '?' ile değiştirilir.
    Log *dosyasına* yazım etkilenmez — dosyaya tam UTF-8 gider.
    """
    def emit(self, record):
        try:
            msg  = self.format(record)
            # Konsola yazarken kodlanamayan karakterleri '?' ile değiştir
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                safe_msg = msg.encode(stream.encoding or "utf-8", errors="replace").decode(
                    stream.encoding or "utf-8", errors="replace"
                )
                stream.write(safe_msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


_console_handler = _SafeStreamHandler(sys.stdout)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)

_file_handler = logging.FileHandler(LOG_DOSYASI, mode="a", encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
logger = logging.getLogger(__name__)


# ==============================================================================
#  🏦  VERİ ÇEKME FONKSİYONU
# ==============================================================================

def _hisse_verisini_cek_tek(sembol: str) -> dict | None:
    """
    Tek bir hisse için yfinance üzerinden temel finansal verileri çeker.
    Eksik/None alanları 'Veri Yok' string'i ile işaretler.

    Args:
        sembol : Yahoo Finance formatında hisse sembolü (örn. "THYAO.IS")

    Returns:
        Hisse verilerini içeren dict ya da kritik hata varsa None
    """
    try:
        ticker = yf.Ticker(sembol)
        info   = ticker.info   # API çağrısı burada gerçekleşir

        # ── Bilgi mevcut mu? Geçerli bir ticker mi? ────────────────────────
        # Yahoo Finance bilinmeyen semboller için minimal bir dict döndürür.
        # "quoteType" alanının varlığını kontrol ederek gerçek veri olduğunu doğrula.
        if not info or info.get("quoteType") is None:
            logger.warning(f"⚠️  [{sembol}] Geçerli veri alınamadı (sembol bulunamıyor olabilir).")
            return None

        def guveli_al(anahtar, varsayilan="Veri Yok"):
            """
            info sözlüğünden güvenli şekilde değer çeker.
            None veya eksik alan gelirse varsayilan değerini döndürür.
            """
            deger = info.get(anahtar)
            if deger is None or (isinstance(deger, float) and pd.isna(deger)):
                return varsayilan
            return deger

        # ── Temettü Verimi: Normalizasyon ─────────────────────────────────
        # Yahoo Finance bazı BIST hisselerinde dividendYield'i ham yüzde
        # olarak döndürür (örn: 2.26 = %2.26).
        # Filtreleme tutarlılığı için oran biçimine (0.0226) dönüştürüyoruz.
        ham_temettu = guveli_al("dividendYield")
        if ham_temettu != "Veri Yok" and ham_temettu is not None:
            try:
                ham_temettu_float = float(ham_temettu)
                # Değer 1'den büyükse ham yüzde formatında gelmiştir
                if ham_temettu_float > 1.0:
                    temettu_normalize = round(ham_temettu_float / 100.0, 6)
                else:
                    temettu_normalize = ham_temettu_float
            except (ValueError, TypeError):
                temettu_normalize = "Veri Yok"
        else:
            temettu_normalize = "Veri Yok"

        # ── Temel Finansal Veriler ─────────────────────────────────────────
        hisse_verisi = {
            "timestamp":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sembol":            sembol,
            "sirket_adi":        guveli_al("longName", guveli_al("shortName")),
            "guncel_fiyat":      guveli_al("currentPrice", guveli_al("regularMarketPrice")),
            "fk_orani":          guveli_al("trailingPE"),
            "pd_dd_orani":       guveli_al("priceToBook"),
            "temettu_verimi":    temettu_normalize,   # 0.0226 = %2.26 formatında
            "piyasa_degeri":     guveli_al("marketCap"),
            "hacim":             guveli_al("volume", guveli_al("regularMarketVolume")),
            "para_birimi":       guveli_al("currency"),
        }

        logger.info(f"  ✅ [{sembol}] Veri çekildi → Fiyat: {hisse_verisi['guncel_fiyat']}")
        return hisse_verisi

    except Exception as e:
        logger.error(f"  ❌ [{sembol}] Beklenmeyen hata: {type(e).__name__}: {e}")
        return None


def fetch_stock_data(sembol_listesi: list[str]) -> list[dict]:
    """
    Verilen hisse listesi için tüm verileri toplar.
    Her hisse için 3 kez tekrar dener (artan bekleme süresi ile).
    Hata durumunda o hisseyi atlar, diğerlerine devam eder.

    Args:
        sembol_listesi : Takip edilecek semboller listesi

    Returns:
        Başarıyla çekilen hisse verilerini içeren dict listesi
    """
    tum_veriler   = []
    hata_sayisi   = 0
    basari_sayisi = 0

    logger.info(f"📊  {len(sembol_listesi)} hisse için veri çekme başlıyor...")

    for i, sembol in enumerate(sembol_listesi, start=1):
        logger.info(f"  [{i:02d}/{len(sembol_listesi)}] {sembol} işleniyor...")

        veri = None

        # 3 kez tekrar dene (artan bekleme süresiyle)
        for deneme, bekleme in enumerate(TEKRAR_BEKLEMELER, start=1):
            veri = _hisse_verisini_cek_tek(sembol)

            if veri is not None:
                break   # Başarılı, döngüden çık

            if deneme < len(TEKRAR_BEKLEMELER):
                logger.warning(f"       Deneme {deneme}/{len(TEKRAR_BEKLEMELER)} başarısız. "
                               f"{bekleme}sn sonra tekrar denenecek...")
                time.sleep(bekleme)

        if veri is not None:
            tum_veriler.append(veri)
            basari_sayisi += 1
        else:
            # Tüm denemeler başarısız → hisse için hata kaydı oluştur
            hata_kaydi = {
                "timestamp":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sembol":            sembol,
                "sirket_adi":        "Veri Yok",
                "guncel_fiyat":      "Veri Yok",
                "fk_orani":          "Veri Yok",
                "pd_dd_orani":       "Veri Yok",
                "temettu_verimi":    "Veri Yok",
                "piyasa_degeri":     "Veri Yok",
                "hacim":             "Veri Yok",
                "para_birimi":       "Veri Yok",
            }
            tum_veriler.append(hata_kaydi)
            hata_sayisi += 1
            logger.error(f"  🔴 [{sembol}] Tüm denemeler başarısız. Hata kaydı oluşturuldu.")

        # Hisseler arası bekleme (Yahoo Finance rate limit önlemi)
        if i < len(sembol_listesi):
            time.sleep(HISSELER_ARASI_BEKLEME)

    logger.info(f"📈  Veri çekme tamamlandı → Başarılı: {basari_sayisi}, "
                f"Hatalı: {hata_sayisi}, Toplam: {len(sembol_listesi)}")
    return tum_veriler


# ==============================================================================
#  🔍  FİLTRELEME FONKSİYONU
# ==============================================================================

def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    DataFrame üzerinde CONFIG'deki filtre eşiklerini uygular.
    Her hisse için:
      - Hangi kriterleri karşıladığını/karşılamadığını tespit eder
      - "filtre_durumu" sütununa detaylı açıklama yazar
      - "filtre_gecti" sütununa True/False yazar

    Filtre mantığı (AND bazlı — tüm kriterlerin sağlanması gerekir):
      1. F/K oranı → fk_min < F/K < fk_max
      2. PD/DD oranı → PD/DD < pd_dd_max
      3. Temettü verimi → temettu >= temettu_min
      4. Piyasa değeri → marketCap >= piyasa_degeri_min
      5. Hacim → volume >= hacim_min

    Args:
        df : fetch_stock_data çıktısından oluşturulan DataFrame

    Returns:
        Filtre sütunları eklenmiş DataFrame
    """
    filtre_durumu_listesi = []
    filtre_gecti_listesi  = []

    for _, satir in df.iterrows():

        sorunlar  = []   # Karşılanmayan kriter açıklamaları
        gecenler  = []   # Karşılanan kriter onayları

        # ── Yardımcı: sayısal dönüşüm ──────────────────────────────────────
        def sayiya_cevir(deger):
            """
            'Veri Yok' veya None olan değerleri None'a,
            diğerlerini float'a çevirir.
            """
            if deger == "Veri Yok" or deger is None:
                return None
            try:
                return float(deger)
            except (ValueError, TypeError):
                return None

        fk           = sayiya_cevir(satir.get("fk_orani"))
        pd_dd        = sayiya_cevir(satir.get("pd_dd_orani"))
        temettu      = sayiya_cevir(satir.get("temettu_verimi"))
        piyasa_deg   = sayiya_cevir(satir.get("piyasa_degeri"))
        hacim        = sayiya_cevir(satir.get("hacim"))

        # ── 1. F/K Oranı Kontrolü ──────────────────────────────────────────
        fk_min = FILTRE_ESIKLERI.get("fk_min")
        fk_max = FILTRE_ESIKLERI.get("fk_max")

        if fk is None:
            sorunlar.append("F/K verisi yok")
        elif fk_min is not None and fk <= fk_min:
            sorunlar.append(f"F/K negatif/sıfır ({fk:.2f})")
        elif fk_max is not None and fk >= fk_max:
            sorunlar.append(f"F/K yüksek ({fk:.2f} ≥ {fk_max})")
        else:
            gecenler.append(f"F/K uygun ({fk:.2f})")

        # ── 2. PD/DD Oranı Kontrolü ────────────────────────────────────────
        pd_dd_max = FILTRE_ESIKLERI.get("pd_dd_max")

        if pd_dd is None:
            sorunlar.append("PD/DD verisi yok")
        elif pd_dd_max is not None and pd_dd >= pd_dd_max:
            sorunlar.append(f"PD/DD yüksek ({pd_dd:.2f} ≥ {pd_dd_max})")
        else:
            gecenler.append(f"PD/DD uygun ({pd_dd:.2f})")

        # ── 3. Temettü Verimi Kontrolü ─────────────────────────────────────
        temettu_min = FILTRE_ESIKLERI.get("temettu_min")

        if temettu is None:
            sorunlar.append("Temettü verisi yok")
        elif temettu_min is not None and temettu < temettu_min:
            sorunlar.append(f"Düşük temettü (%{temettu*100:.2f} < %{temettu_min*100:.0f})")
        else:
            gecenler.append(f"Temettü uygun (%{temettu*100:.2f})")

        # ── 4. Piyasa Değeri Kontrolü ──────────────────────────────────────
        pd_min = FILTRE_ESIKLERI.get("piyasa_degeri_min")

        if piyasa_deg is None:
            sorunlar.append("Piyasa değeri verisi yok")
        elif pd_min is not None and piyasa_deg < pd_min:
            sorunlar.append(f"Piyasa değeri düşük ({piyasa_deg:,.0f} < {pd_min:,.0f})")
        else:
            gecenler.append(f"Piyasa değeri yeterli ({piyasa_deg:,.0f})")

        # ── 5. Hacim Kontrolü ──────────────────────────────────────────────
        hacim_min = FILTRE_ESIKLERI.get("hacim_min")

        if hacim is None:
            sorunlar.append("Hacim verisi yok")
        elif hacim_min is not None and hacim < hacim_min:
            sorunlar.append(f"Düşük hacim ({hacim:,.0f} < {hacim_min:,.0f})")
        else:
            gecenler.append(f"Hacim yeterli ({hacim:,.0f})")

        # ── Sonuç Değerlendirmesi ──────────────────────────────────────────
        if not sorunlar:
            durum    = "✅ Tüm kriterleri karşılıyor"
            gecti    = True
        else:
            durum    = "❌ " + " | ".join(sorunlar)
            gecti    = False

        filtre_durumu_listesi.append(durum)
        filtre_gecti_listesi.append(gecti)

    # Sütunları DataFrame'e ekle
    df = df.copy()
    df["filtre_durumu"] = filtre_durumu_listesi
    df["filtre_gecti"]  = filtre_gecti_listesi

    gecen_sayi = sum(filtre_gecti_listesi)
    logger.info(f"🔍  Filtreleme tamamlandı → {gecen_sayi}/{len(df)} hisse tüm kriterleri karşıladı.")

    return df


# ==============================================================================
#  💾  CSV KAYIT FONKSİYONU
# ==============================================================================

def save_to_csv(df_filtrelenmis: pd.DataFrame) -> bool:
    """
    İki ayrı CSV dosyasına kayıt yapar:

    1. tum_hisseler.csv (APPEND modu):
       Her çalıştırmada yeni satırlar eklenir. Tarihsel veri birikmesi için.
       Dosya yoksa header ile birlikte oluşturulur.

    2. filtrelenmis_hisseler.csv (OVERWRITE modu):
       Her çalıştırmada o günün filtreden geçen hisseleriyle sıfırdan yazılır.
       Sadece "filtre_gecti = True" olan satırlar kaydedilir.

    Args:
        df_filtrelenmis : apply_filters() çıktısı (filtre sütunları ekli)

    Returns:
        Her iki kayıt da başarılıysa True, hata varsa False
    """
    basari = True

    # ── 1. Ana CSV (tüm hisseler, append) ────────────────────────────────
    try:
        dosya_mevcut = os.path.exists(CSV_TUM_HISSELER)

        # Filtre sütunlarını da dahil ederek yaz
        df_filtrelenmis.to_csv(
            CSV_TUM_HISSELER,
            mode="a",
            header=not dosya_mevcut,
            index=False,
            encoding="utf-8-sig",  # Excel'de Türkçe desteği
        )

        if dosya_mevcut:
            logger.info(f"📄  {len(df_filtrelenmis)} satır mevcut dosyaya eklendi: {CSV_TUM_HISSELER}")
        else:
            logger.info(f"📄  Yeni dosya oluşturuldu ve {len(df_filtrelenmis)} satır yazıldı: {CSV_TUM_HISSELER}")

    except PermissionError:
        logger.error(f"❌  Yazma izni yok: {CSV_TUM_HISSELER} — Dosya Excel'de açık olabilir.")
        basari = False
    except Exception as e:
        logger.error(f"❌  Tüm hisseler CSV hatası: {type(e).__name__}: {e}")
        basari = False

    # ── 2. Filtrelenmiş CSV (sadece geçenler, overwrite) ─────────────────
    try:
        df_gecenler = df_filtrelenmis[df_filtrelenmis["filtre_gecti"] == True].copy()

        if df_gecenler.empty:
            logger.warning("⚠️  Hiçbir hisse filtreyi geçemedi. Filtrelenmiş CSV boş olarak kaydedildi.")
        
        # Her çalıştırmada sıfırdan yaz (mode="w")
        df_gecenler.to_csv(
            CSV_FILTRELENMIS,
            mode="w",
            header=True,
            index=False,
            encoding="utf-8-sig",
        )

        logger.info(f"📄  {len(df_gecenler)} filtrelenmiş hisse kaydedildi: {CSV_FILTRELENMIS}")

    except PermissionError:
        logger.error(f"❌  Yazma izni yok: {CSV_FILTRELENMIS} — Dosya Excel'de açık olabilir.")
        basari = False
    except Exception as e:
        logger.error(f"❌  Filtrelenmiş CSV hatası: {type(e).__name__}: {e}")
        basari = False

    return basari


# ==============================================================================
#  🔄  ANA İŞ FONKSİYONU
# ==============================================================================

def run_job():
    """
    Tam bir çalışma döngüsünü yönetir:
      1. Tüm hisseler için temel verileri çek
      2. Filtreleme uygula
      3. CSV'lere kaydet
      4. Özet raporu logla

    Hem schedule (daemon) hem de --once modunda çağrılır.
    """
    baslangic_zamani = datetime.now()

    logger.info("=" * 65)
    logger.info(f"🚀  BIST Veri Çekme İşlemi Başladı")
    logger.info(f"    Tarih/Saat   : {baslangic_zamani.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"    Hisse sayısı : {len(HISSE_LISTESI)}")
    logger.info("=" * 65)

    # ── ADIM 1: Veri Çek ─────────────────────────────────────────────────
    ham_veriler = fetch_stock_data(HISSE_LISTESI)

    if not ham_veriler:
        logger.error("🔴  Hiçbir hisse için veri çekilemedi. İşlem durduruluyor.")
        return

    # ── ADIM 2: DataFrame'e Dönüştür ─────────────────────────────────────
    df = pd.DataFrame(ham_veriler)

    # Sütun sırası (okunabilirlik için standart hale getir)
    sutun_sirasi = [
        "timestamp", "sembol", "sirket_adi",
        "guncel_fiyat", "fk_orani", "pd_dd_orani",
        "temettu_verimi", "piyasa_degeri", "hacim", "para_birimi",
    ]
    # Sütunların hepsinin mevcut olduğundan emin ol
    mevcut_sutunlar = [s for s in sutun_sirasi if s in df.columns]
    df = df[mevcut_sutunlar]

    # ── ADIM 3: Filtrele ──────────────────────────────────────────────────
    df_filtrelenmis = apply_filters(df)

    # ── ADIM 4: CSV'ye Kaydet ─────────────────────────────────────────────
    basarili = save_to_csv(df_filtrelenmis)

    # ── ADIM 5: Özet Rapor ────────────────────────────────────────────────
    bitis_zamani = datetime.now()
    sure_saniye  = (bitis_zamani - baslangic_zamani).total_seconds()
    gecenler     = df_filtrelenmis[df_filtrelenmis["filtre_gecti"] == True]["sembol"].tolist()

    logger.info("─" * 65)
    logger.info(f"📊  ÖZET RAPOR")
    logger.info(f"    Toplam hisse           : {len(ham_veriler)}")
    logger.info(f"    Filtreyi geçen hisseler: {len(gecenler)}")

    if gecenler:
        logger.info(f"    Geçen hisseler         : {', '.join(gecenler)}")

    logger.info(f"    Tamamlanma süresi      : {sure_saniye:.1f} saniye")
    logger.info(f"    CSV (tüm)              : {CSV_TUM_HISSELER}")
    logger.info(f"    CSV (filtrelenmiş)     : {CSV_FILTRELENMIS}")

    if basarili:
        logger.info("✅  İşlem başarıyla tamamlandı.")
    else:
        logger.warning("⚠️  İşlem tamamlandı ancak bazı CSV yazma hataları oluştu.")

    logger.info("=" * 65)


# ==============================================================================
#  🎯  ANA PROGRAM GİRİŞ NOKTASI
# ==============================================================================

def main():
    """
    Programın giriş noktası.
    İki modda çalışır:

    --once   : Tek seferlik çalışır ve çıkar.
               Windows Görev Zamanlayıcı veya Linux cron ile kullanıma uygundur.

    (varsayılan): Daemon modu — schedule ile her gün CALISMA_SAATI'nde çalışır.
                  İlk çalıştırmada hemen bir kez veri çeker, sonra zamanlamaya geçer.
    """
    parser = argparse.ArgumentParser(
        description="BIST Hisse Senedi Temel Veri Çekme & Filtreleme Otomasyonu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Kullanım Örnekleri:
  python bist_fetcher.py             # Daemon modu (her gün {CALISMA_SAATI}'de çalışır)
  python bist_fetcher.py --once      # Tek seferlik çalıştır ve çık
  python bist_fetcher.py --help      # Bu yardım mesajını göster

Linux/Mac Cron (her gün 18:30'da):
  30 18 * * 1-5 /usr/bin/python3 /path/to/bist_fetcher.py --once

Windows Görev Zamanlayıcı:
  Program : python
  Argüman : "C:\\...\\bist_fetcher.py" --once
        """,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Tek seferlik çalıştır (cron/zamanlayıcı ile kullanım için)",
    )
    args = parser.parse_args()

    # ── Banner ────────────────────────────────────────────────────────────
    logger.info("╔═══════════════════════════════════════════════════════════════╗")
    logger.info("║       BIST Hisse Veri Çekme & Filtreleme Otomasyonu          ║")
    logger.info("╚═══════════════════════════════════════════════════════════════╝")
    logger.info(f"  Hisse Sayısı    : {len(HISSE_LISTESI)}")
    logger.info(f"  Ana CSV         : {CSV_TUM_HISSELER}")
    logger.info(f"  Filtre CSV      : {CSV_FILTRELENMIS}")
    logger.info(f"  Log Dosyası     : {LOG_DOSYASI}")
    logger.info(f"  Filtre Eşikleri : F/K < {FILTRE_ESIKLERI['fk_max']} | "
                f"PD/DD < {FILTRE_ESIKLERI['pd_dd_max']} | "
                f"Temettü > %{FILTRE_ESIKLERI['temettu_min']*100:.0f}")

    # ── TEK SEFERLİK MOD ──────────────────────────────────────────────────
    if args.once:
        logger.info(f"  Mod             : Tek Seferlik (--once)")
        logger.info("─" * 65)
        run_job()
        logger.info("🏁  Tek seferlik çalışma tamamlandı. Program kapanıyor.")
        sys.exit(0)

    # ── DAEMON MODU ───────────────────────────────────────────────────────
    logger.info(f"  Mod             : Daemon (Her iş günü saat {CALISMA_SAATI})")
    logger.info("─" * 65)

    # İlk çalıştırmayı anında yap (kullanıcı beklemesin)
    logger.info("▶️  İlk veri çekme başlatılıyor...")
    run_job()

    # Sonraki çalıştırmalar için zamanlama kur
    schedule.every().day.at(CALISMA_SAATI).do(run_job)
    logger.info(f"⏰  Zamanlama aktif: Her gün saat {CALISMA_SAATI}'de çalışacak.")
    logger.info("   Durdurmak için Ctrl+C basın.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)   # Her dakika zamanlama kontrolü
    except KeyboardInterrupt:
        logger.info("\n🛑  Kullanıcı tarafından durduruldu (Ctrl+C).")
        logger.info("👋  Program kapatılıyor...")
        sys.exit(0)


if __name__ == "__main__":
    main()
