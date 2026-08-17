import os

# Render'in verdiği PORT'u kullan
port = int(os.environ.get("PORT", 5000))
bind = f"0.0.0.0:{port}"

# Threading worker — background thread'lerin düzgün çalışması için
worker_class = "gthread"
workers = 1
threads = 4
timeout = 120          # TradingView sorgusu için yeterli süre
graceful_timeout = 30
keepalive = 5
preload_app = True     # App başlamadan önce kod yüklensin
