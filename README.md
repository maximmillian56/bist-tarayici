# 🚀 BIST Pro — Hisse Senedi Tarayıcısı & AI Analiz Platformu

Borsa İstanbul (BIST) hisse senetleri için geliştirilmiş, gerçek zamanlı TradingView verileriyle çalışan, teknik & temel analiz süzgeçleri, otomatik formasyon taraması, TradingView Lightweight Charts grafikleri ve **Google Gemini AI** destekli asistan/demo trading özelliklerini içeren kapsamlı finansal analiz web uygulaması.

---

## 🌟 Öne Çıkan Özellikler

1. **Doğrudan TradingView Scanner Entegrasyonu**
   - 600'den fazla BIST hissesi için anlık fiyat, değişim, piyasa değeri ve hacim verileri.
   - 4H, 1H, Günlük ve Haftalık pivot seviyeleri (S1, S2, R1, R2, Pivot).
   - Temel analiz metrikleri: F/K, PD/DD, P/S, EV/FAVÖK, Brüt Kâr Marjı, FAVÖK/Gelir (EBITDA), Temettü Verimi (%).
   - Teknik analiz göstergeleri: RSI, MACD, EMA (20/25/50/100/200), Bollinger Bantları (pozisyon & genişlik), Beta.
   - BIST 100 ve XKTUM (Katılım Tüm) endeks filtreleri.

2. **Yükseliş Formasyonları Tarayıcısı (`/api/formations`)**
   - BIST100 hisseleri üzerinde otomatik teknik formasyon tespiti: Çekiç (Hammer), Yutan Boğa (Engulfing), Sabah Yıldızı (Morning Star), Delici Çizgi (Piercing), Üç Beyaz Asker.
   - Çoklu zaman dilimi desteği (1H, 4H, Günlük).
   - Arka plan taraması, rate limiting ve anlık durum ilerlemesi.

3. **TradingView Lightweight Charts ile İnteraktif Grafikler**
   - Hisseye tıklandığında açılan profesyonel interaktif mum ve hacim grafiği.
   - Farklı periyot (1H, 4H, 1D, 1W) ve aralık (5D, 1M, 3M, 6M, 1Y, 5Y) seçenekleri.

4. **Yapay Zeka Destekli Analiz & Chat (`/api/chat`, `/api/daily-report`)**
   - **Google Gemini** ile hisse hakkında temel ve teknik analiz bağlamı ile zenginleştirilmiş sohbet.
   - Favori hisseler için tek tıkla **Günlük AI Bülteni/Raporu** oluşturma.
   - Güvenli XSS korumalı Markdown formatlama ve rate limit koruması.

5. **AI Otonom Demo Trading Simülasyonu (`demo_trading.py`)**
   - Sanal bakiye ile AI'ın BIST piyasasında otomatik alım-satım yaptığı canlı simülasyon.
   - Kâr/zarar, kazanma oranı, işlem geçmişi ve portföy grafiği takibi.
   - İsteğe bağlı kalıcı veri saklama desteği: PostgreSQL (`DATABASE_URL`) veya Upstash Redis (`UPSTASH_REDIS_REST_URL`).

6. **Mevsimsellik Analizi**
   - Son 5 yılın aylık getiri ortalamalarını gösteren mevsimsel döngü tablosu.

---

## 📁 Proje Mimarisi

```
Crypto Earn/
├── app.py                 # Flask web sunucusu, TradingView scanner ve API uçları
├── demo_trading.py        # Otonom AI demo trading simülasyon motoru
├── index.html             # Tek sayfa (SPA) kullanıcı arayüzü ve görselleştirme
├── gunicorn.conf.py       # Production WSGI sunucu ayarları ve post_fork thread yönetimi
├── requirements.txt       # Python paket bağımlılıkları
├── Dockerfile             # Docker & Hugging Face Spaces konteyner yapılandırması
├── .dockerignore          # Konteyner imajı optimizasyonu
├── bist_fetcher.py        # Günlük CSV veri çekme scripti (isteğe bağlı araç)
└── crypto_fetcher.py      # Kripto veri çekme scripti (isteğe bağlı araç)
```

---

## ⚙️ Kurulum ve Çalıştırma

### 1. Gereksinimler
- Python 3.10+
- (İsteğe bağlı) Google Gemini API Key

### 2. Bağımlılıkları Yükleme
```bash
pip install -r requirements.txt
```

### 3. Çevre Değişkenleri (.env)
Proje kök dizininde bir `.env` dosyası oluşturabilirsiniz:
```env
# Zorunlu değil ama AI özellikleri için tavsiye edilir:
GEMINI_API_KEY=your_gemini_api_key_here

# İsteğe bağlı Demo Trading kalıcılık ayarları (PostgreSQL):
DATABASE_URL=postgres://user:password@host:port/dbname

# İsteğe bağlı Demo Trading admin şifresi:
DEMO_ADMIN_PASSWORD=guclu_bir_sifre

# Çalışma portu (varsayılan: 5000):
PORT=5000
```

### 4. Yerel Olarak Başlatma
```bash
python app.py
```
Tarayıcınızdan `http://localhost:5000` adresine giderek uygulamayı kullanabilirsiniz.

---

## 🐳 Docker ile Çalıştırma

```bash
# Docker imajı oluşturma
docker build -t bist-pro .

# Konteyneri çalıştırma (5000 portunda)
docker run -d -p 5000:5000 --env-file .env bist-pro
```

Hugging Face Spaces üzerinde doğrudan Dockerfile ile dağıtılabilir (varsayılan `PORT=7860` otomatik desteklenir).

---

## 🌐 Production Dağıtımı (Gunicorn / Render)

Render veya benzeri Linux PaaS ortamlarında:
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn -c gunicorn.conf.py app:app`

`gunicorn.conf.py` konfigürasyonu, veri çekme thread'lerini `post_fork` hook'unda başlatarak bellek ve eşzamanlılık tutarlılığı sağlar.

---

## 🔒 Güvenlik & Rate Limiting

- **API Koruma**: `/api/refresh`, `/api/formations/refresh`, `/api/chat` ve `/api/daily-report` endpoint'leri IP bazlı ve global kayan pencereli (sliding window) rate limit ile korunur.
- **XSS Koruması**: Arayüzdeki tüm kullanıcı ve hisse verileri HTML kaçış (`escHtml`) işleminden geçirilir.
- **Güvenlik Başlıkları**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN` ve `Referrer-Policy: same-origin` yanıt başlıkları otomatik eklenir.

---

## ⚖️ Yasal Uyarı

Bu uygulamada sunulan tüm veriler, grafikler, formasyon analizleri ve yapay zeka yorumları yalnızca eğitim ve bilgilendirme amaçlıdır; **kesinlikle yatırım tavsiyesi niteliğinde değildir**.
