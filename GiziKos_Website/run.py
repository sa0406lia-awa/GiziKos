from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser

import uvicorn


def port_is_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def choose_port(host: str, preferred: int) -> int:
    if port_is_available(host, preferred):
        return preferred
    for port in range(preferred + 1, preferred + 21):
        if port_is_available(host, port):
            print(f"Port {preferred} sedang digunakan. GiziKos otomatis memakai port {port}.")
            return port
    raise RuntimeError("Tidak menemukan port kosong pada rentang yang tersedia.")


def open_browser_later(url: str) -> None:
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    preferred_port = int(os.getenv("PORT", "8000"))
    port = choose_port(host, preferred_port)
    url = f"http://{host}:{port}"
    print("=" * 64)
    print(" GiziKos siap dijalankan")
    print(f" Buka di browser: {url}")
    print(" Tekan CTRL+C untuk menghentikan server")
    print("=" * 64)
    if os.getenv("OPEN_BROWSER", "true").lower() == "true":
        threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
    )
