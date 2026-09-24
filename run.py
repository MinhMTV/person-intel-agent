#!/usr/bin/env python3
"""Person Intel Agent — local starter.

Usage:
    python run.py                 # start on http://127.0.0.1:8000 and open the browser
    python run.py --port 8080
    python run.py --no-browser
    python run.py --install       # create .venv (if needed) and install requirements
    python run.py --dev           # auto-reload for development (never use in production)

Production: set APP_ENV=production and run `python -m app serve` behind a
reverse proxy with TLS and APP_API_TOKEN configured.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def venv_python() -> Path:
    venv = ROOT / ".venv"
    candidate = venv / "bin" / "python"
    return candidate if candidate.exists() else venv / "Scripts" / "python.exe"


def ensure_venv() -> None:
    """Re-exec inside ./.venv (created on first use)."""
    if in_venv():
        return
    if not (ROOT / ".venv").exists():
        print("Creating virtual environment in .venv …")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    python = venv_python()
    os.execv(str(python), [str(python), *sys.argv])  # noqa: S606


def install() -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port)) != 0  # noqa: S104


def main() -> None:
    parser = argparse.ArgumentParser(description="Person Intel Agent starter")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--install", action="store_true", help="Install requirements and exit")
    parser.add_argument("--dev", action="store_true", help="Enable auto-reload (development only)")
    args = parser.parse_args()

    ensure_venv()
    if args.install:
        install()
        print("Done. Start with: python run.py")
        return
    try:
        import fastapi  # noqa: F401
        import uvicorn
    except ImportError:
        print("Dependencies missing — installing requirements …")
        install()
        import uvicorn

    if not port_free(args.host, args.port):
        print(f"Port {args.port} is already in use. Try: python run.py --port {args.port + 1}")
        sys.exit(1)

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"  # noqa: S104
    print(f"Person Intel Agent → {url}   (Ctrl+C to stop)")
    if not args.no_browser:
        def open_browser() -> None:
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()
    os.chdir(ROOT)
    uvicorn.run("app.api.app:app", host=args.host, port=args.port, reload=args.dev, server_header=False)


if __name__ == "__main__":
    main()
