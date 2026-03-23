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
import sys
import subprocess
import time
import webbrowser
from pathlib import Path

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

def start_webapp(port: int, open_browser: bool):
    """Startet die Webapp."""
    app_dir = Path(__file__).parent
    os.chdir(app_dir)
    
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

def main():
    parser = argparse.ArgumentParser(description="Person Intel Agent Webapp Starter")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    parser.add_argument("--install", action="store_true", help="Nur Dependencies installieren")
    args = parser.parse_args()
    
    check_venv()
    
    if args.install:
        install_deps()
        print("✅ Fertig! Starte mit: python run.py")
        return
    
    install_deps()
    start_webapp(args.port, not args.no_browser)

if __name__ == "__main__":
    main()
