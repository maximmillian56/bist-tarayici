import os

port = int(os.environ.get("PORT", 10000))
bind = f"0.0.0.0:{port}"

worker_class = "gthread"
workers = 1
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
# preload_app OLMAMALI — ama thread'leri de module-level'da başlatma!

def post_fork(server, worker):
    """
    Worker fork edildikten SONRA background thread'leri başlat.
    Bu sayede thread'ler doğru process'te (worker'da) çalışır.
    """
    import threading
    import app as myapp

    # Worker'ın cache'ini sıfırla (master'dan kirli state gelebilir)
    with myapp._lock:
        myapp._cache["loading"]    = False
        myapp._cache["stocks"]     = None
        myapp._cache["error"]      = None
        myapp._cache["updated_at"] = None

    # Thread'leri worker'ın kendi process'inde başlat
    threading.Thread(target=myapp._fetch,          daemon=True).start()
    threading.Thread(target=myapp._fetch_seasonal, daemon=True).start()

    server.log.info(f"Worker {worker.pid}: _fetch thread'leri baslatildi")
