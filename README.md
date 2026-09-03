# 📈 BIST Hisse Senedi Veri Çekme & Filtreleme Otomasyonu

BIST (Borsa İstanbul) hisse senetlerinin temel finansal verilerini `yfinance` üzerinden günlük çekip `tum_hisseler.csv`'ye kaydeden, belirlenen kriterleri karşılayan hisseleri `filtrelenmis_hisseler.csv`'ye yazan Python otomasyonu.

---

## 📁 Dosya Yapısı

```
Crypto Earn/
├── bist_fetcher.py           ← Ana otomasyon scripti
├── requirements.txt          ← Gerekli kütüphaneler
├── README.md                 ← Bu dosya
├── tum_hisseler.csv          ← Tüm hisse verileri (tarihsel birikir)
├── filtrelenmis_hisseler.csv ← Filtreyi geçen hisseler (her gün güncellenir)
└── automation.log            ← İşlem ve hata logları
```

---

## ⚙️ Kurulum

### 1. Gereksinimler
Python **3.10+** gereklidir.

### 2. Bağımlılıkları Yükle

```bash
pip install -r requirements.txt
```

| Kütüphane  | Kullanım Amacı                                  |
|------------|-------------------------------------------------|
| `yfinance` | Yahoo Finance üzerinden BIST hisse verisi çekme |
| `pandas`   | Veri işleme ve CSV yazma                        |
| `schedule` | Günlük zamanlama (daemon modu)                  |

---

## 🚀 Kullanım

### Daemon Modu (Sürekli Çalışır)
Script her gün saat `18:30`'da (BIST kapanışı sonrası) çalışır. İlk açılışta anında bir kez çalışır:

```bash
python bist_fetcher.py
```

Durdurmak için → `Ctrl+C`

### Tek Seferlik Mod
Bir kez çalışıp çıkar. Sistem zamanlaması için idealdir:

```bash
python bist_fetcher.py --once
```

---

## 🔧 Yapılandırma

`bist_fetcher.py` içindeki **CONFIG bölümünü** düzenleyin:

### Hisse Listesi
```python
HISSE_LISTESI = [
    "THYAO.IS",   # Türk Hava Yolları
    "GARAN.IS",   # Garanti BBVA Bankası
    # ... kolayca ekleyip çıkarabilirsiniz
]
```
> Hisse sembolünün sonuna `.IS` eklemeyi unutmayın (Yahoo Finance BIST formatı).

### Filtre Eşiklerini Değiştirme
```python
FILTRE_ESIKLERI = {
    "fk_min":            0.0,          # F/K minimum (0 altı = zarar eden şirket)
    "fk_max":            15.0,         # F/K maksimum  ← buradan değiştirin
    "pd_dd_max":         1.5,          # PD/DD üst sınırı
    "temettu_min":       0.03,         # Min temettü (0.03 = %3)
    "piyasa_degeri_min": 1_000_000_000, # Min piyasa değeri (TL)
    "hacim_min":         1_000_000,    # Min günlük hacim (adet)
}
```

Bir filtreyi devre dışı bırakmak için değerini `None` yapın:
```python
"temettu_min": None,   # Temettü filtresi devre dışı
```

### Zamanlama
```python
CALISMA_SAATI = "18:30"   # HH:MM formatı
```

---

## 📅 Sistem Zamanlaması

### Windows — Görev Zamanlayıcı (PowerShell ile)

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument '"C:\Users\islam\Desktop\Crypto Earn\bist_fetcher.py" --once'

# Hafta içi (Pazartesi-Cuma) 18:30'da çalıştır
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "18:30"

Register-ScheduledTask `
    -TaskName "BIST_Hisse_Fetcher" `
    -Action $action `
    -Trigger $trigger
```

### Linux / Mac — Cron

```bash
crontab -e
```

Hafta içi her gün 18:30'da:
```cron
30 18 * * 1-5 /usr/bin/python3 /path/to/bist_fetcher.py --once >> /path/to/cron.log 2>&1
```

---

## 📊 CSV Dosyaları

### `tum_hisseler.csv` — Tarihsel Birikim
Her çalıştırmada yeni satırlar **eklenir** (üzerine yazılmaz):

| Sütun              | Açıklama                        |
|--------------------|---------------------------------|
| `timestamp`        | Veri çekilme tarihi/saati       |
| `sembol`           | Hisse sembolü (örn. THYAO.IS)   |
| `sirket_adi`       | Şirket tam adı                  |
| `guncel_fiyat`     | Güncel kapanış fiyatı (TL)      |
| `fk_orani`         | F/K (Fiyat/Kazanç) oranı        |
| `pd_dd_orani`      | PD/DD (Piyasa/Defter) oranı     |
| `temettu_verimi`   | Temettü verimi (0.05 = %5)      |
| `piyasa_degeri`    | Piyasa değeri (TL)              |
| `hacim`            | Günlük işlem hacmi (adet)       |
| `para_birimi`      | Para birimi (TRY)               |
| `filtre_durumu`    | Kriterlerin detaylı açıklaması  |
| `filtre_gecti`     | True / False                    |

### `filtrelenmis_hisseler.csv` — Günlük Güncelleme
Her çalıştırmada **sıfırdan yazılır** — o günün tüm kriterleri karşılayan hisseleri içerir.

---

## 📋 Log Örneği

```
2026-08-16 18:30:00 | INFO     | 🚀  BIST Veri Çekme İşlemi Başladı
2026-08-16 18:30:00 | INFO     | 📊  20 hisse için veri çekme başlıyor...
2026-08-16 18:30:01 | INFO     |   ✅ [THYAO.IS] Veri çekildi → Fiyat: 287.5
2026-08-16 18:30:03 | INFO     |   ✅ [GARAN.IS] Veri çekildi → Fiyat: 112.3
...
2026-08-16 18:30:25 | INFO     | 🔍  Filtreleme tamamlandı → 4/20 hisse geçti
2026-08-16 18:30:25 | INFO     | ✅  İşlem başarıyla tamamlandı.
```

---

## ⚠️ Bilinen Limitler

| Durum                          | Açıklama                                               |
|--------------------------------|--------------------------------------------------------|
| Bazı F/K verileri "Veri Yok"   | yfinance BIST fundamental datasında boşluklar olabilir |
| Piyasa değeri TL cinsinden     | Yahoo Finance bazı hisseler için USD gösterebilir      |
| Temettü verimi güncelmeyebilir | Temettü ödeme dönemlerinde gecikme yaşanabilir         |

> Veri eksikliği durumunda script çökmez — ilgili alan `"Veri Yok"` olarak işaretlenir.

---

## 🛠️ Sorun Giderme

| Hata                              | Çözüm                                                  |
|-----------------------------------|--------------------------------------------------------|
| `ModuleNotFoundError: yfinance`   | `pip install -r requirements.txt` çalıştırın          |
| Sembol bulunamadı uyarısı         | Hisse sembolünün `.IS` ile bittiğini kontrol edin      |
| CSV dosyası açıkken hata          | Excel'i kapatın, sonra tekrar çalıştırın              |
| Tüm veriler "Veri Yok"            | İnternet bağlantısını ve Yahoo Finance erişimini kontrol edin |
