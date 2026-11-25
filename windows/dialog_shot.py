"""CI: fotografa un dialogo di Firefox con un campo da compilare, col tema scuro e chiaro.

Verifica visiva del tema applicato all'interfaccia di Firefox (userChrome.css);
le immagini finiscono tra gli artifact della action.
"""
import http.server
import os
import subprocess
import threading
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stream_launcher import core  # noqa: E402

OUT = Path("dist/screens")
OUT.mkdir(parents=True, exist_ok=True)
PAGE = ("<body style='background:#2b2b2b;color:#ddd;font:16px sans-serif'><h1>Pagina del servizio</h1>"
        "<select><option>Italiano</option><option>English</option></select>"
        "<script>setTimeout(() => prompt('Inserisci il codice di verifica inviato via SMS', ''), 1500)</script>")
SHOT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
    "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($i);$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
    "$i.Save($env:SHOT_PATH)"
)

class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = http.server.HTTPServer(("127.0.0.1", 0), Page)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = core.load_service("primevideo")
svc = replace(base, url=f"http://127.0.0.1:{server.server_port}/", proxy=core.Proxy(), addons=[])
for theme in ("dark", "light"):
    core.set_pref("theme", theme)
    core.launch(svc)
    time.sleep(15)
    path = OUT / f"dialogo-{theme}.png"
    subprocess.run(["powershell", "-NoProfile", "-Command", SHOT], env={**os.environ, "SHOT_PATH": str(path.resolve())}, check=True)
    print(f"{path}: {path.stat().st_size} byte", flush=True)
    subprocess.run(["taskkill", "/F", "/T", "/IM", "firefox.exe"], capture_output=True)
    time.sleep(3)
core.set_pref("theme", "auto")
