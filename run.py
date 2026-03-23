#!/usr/bin/env python3
"""
Person Intel Agent — Starter Script
Startet die Webapp automatisch im Browser.

Usage:
    python run.py
    python run.py --port 8080
    python run.py --no-browser
"""

import argparse
import os
import socket
import sys
import subprocess
import time
import webbrowser
from pathlib import Path

from app.login_manager import ensure_playwright_browser

def check_venv():
    """Prüft ob venv existiert und aktiviert ist."""
    venv_path = Path(__file__).parent / ".venv"
    if not venv_path.exists():
        print("📦 Erstelle Virtual Environment...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        print("✅ Virtual Environment erstellt!")
    
    # Check if we're in venv
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        return True
    
    # Re-run in venv
    python = str(venv_path / "bin" / "python")
    if not Path(python).exists():
        python = str(venv_path / "Scripts" / "python.exe")  # Windows
    
    print("🔄 Starte in Virtual Environment...")
    os.execv(python, [python] + sys.argv)

def install_deps():
    """Installiert Dependencies."""
    req_file = Path(__file__).parent / "requirements.txt"
    if req_file.exists():
        print("📦 Installiere Dependencies...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
            check=True
        )
        print("✅ Dependencies installiert!")


def ensure_browser_binaries():
    """Install Playwright Chromium automatically if needed."""
    print("🌐 Prüfe Playwright Browser...")
    result = ensure_playwright_browser("chromium")
    if not result.get("success"):
        print("⚠️ Playwright Chromium konnte nicht automatisch installiert werden.")
        print(result.get("error", "Unknown error"))
        return False
    if result.get("installed"):
        print("✅ Playwright Chromium installiert!")
    else:
        print("✅ Playwright Chromium bereit.")
    return True


def find_port_process(port: int) -> str | None:
    """Return a short description of the process listening on a port."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[1]
    return None


def port_is_available(port: int) -> bool:
    """Check whether a TCP port is free on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", port)) != 0

def start_webapp(port: int, open_browser: bool):
    """Startet die Webapp."""
    app_dir = Path(__file__).parent
    os.chdir(app_dir)

    if not port_is_available(port):
        print(f"\n⚠️ Port {port} ist bereits belegt.")
        proc = find_port_process(port)
        if proc:
            print(f"   Listener: {proc}")
        print(f"   Starte z.B. mit: python run.py --port {port + 1}")
        print(f"   Oder beende den Prozess auf Port {port} und versuche es erneut.")
        return
    
    print(f"\n{'='*50}")
    print(f"🚀 Person Intel Agent Webapp")
    print(f"{'='*50}")
    print(f"📡 URL: http://localhost:{port}")
    print(f"🛑 Stoppen: Ctrl+C")
    print(f"{'='*50}\n")
    
    if open_browser:
        # Open browser after short delay
        import threading
        def open_browser_delayed():
            time.sleep(2)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser_delayed, daemon=True).start()
    
    # Start uvicorn
    try:
        subprocess.run([
            sys.executable, "-m", "uvicorn",
            "app.web:app",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--reload"
        ], check=True)
    except KeyboardInterrupt:
        print("\n👋 Webapp gestoppt!")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Webapp konnte nicht gestartet werden (exit {e.returncode}).")

def main():
    parser = argparse.ArgumentParser(description="Person Intel Agent Webapp Starter")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    parser.add_argument("--install", action="store_true", help="Nur Dependencies installieren")
    args = parser.parse_args()
    
    check_venv()
    
    if args.install:
        install_deps()
        ensure_browser_binaries()
        print("✅ Fertig! Starte mit: python run.py")
        return

    install_deps()
    ensure_browser_binaries()
    start_webapp(args.port, not args.no_browser)

if __name__ == "__main__":
    main()
