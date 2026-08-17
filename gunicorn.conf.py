import os

# Render'in verdiği PORT'u kullan
port = int(os.environ.get("PORT", 10000))
bind = f"0.0.0.0:{port}"

# gthread worker — background thread'ler için gerekli
worker_class = "gthread"
workers = 1
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5

# KRITIK: preload_app OLMAMALI — thread'ler fork sonrası ölür!
# preload_app = False  (default zaten False)
