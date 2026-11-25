"""Test su Windows reale (CI): runtime, collegamenti, avvio, password, GUI.

Gira con APPDATA/LOCALAPPDATA puntati a cartelle temporanee.
"""
import base64
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stream_launcher import core, passwords  # noqa: E402


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        sys.exit(1)


# --- runtime e servizi (dopo `init`)
check(core.firefox_binary().is_file(), f"firefox.exe in {core.RUNTIME_DIR} (versione {core.runtime_version()})")
check((core.RUNTIME_DIR / "distribution" / "policies.json").is_file(), "policies.json installate")
services = core.list_services()
check(len(services) >= 15, f"{len(services)} servizi predefiniti")
links = sorted(p.name for p in core.APPS_DIR.glob("*.lnk"))
check("Stream Launcher.lnk" in links and len(links) == len(services) + 1, f"{len(links)} collegamenti nel menu Start")
check(any(core.ICONS_DIR.glob("*.ico")), "icone .ico dei servizi")

# --- rinomina ed eliminazione aggiornano i collegamenti
svc = core.load_service("netflix")
svc.name = "Netflix: prova"
core.save_service(svc)
core.refresh_desktop_entry(svc, icon=False)
check((core.APPS_DIR / "Netflix prova.lnk").is_file() and not (core.APPS_DIR / "Netflix.lnk").exists(), "rinomina → collegamento ricreato")
dup = core.duplicate_service("netflix", "Netflix 2")
core.remove_service(dup.id)
check(not (core.APPS_DIR / "Netflix 2.lnk").exists(), "eliminazione → collegamento tolto")

# --- avvio reale di Firefox (headless) e rilevamento del profilo in uso
os.environ["MOZ_HEADLESS"] = "1"
svc.addons = ["amo:ublock-origin"]
svc.proxy = core.Proxy("socks5", "127.0.0.1", 1080, "utente", "segreto")  # usa l'estensione proxy
core.save_service(svc)
core.launch(svc)
for _ in range(60):
    if core.profile_running(svc.profile_dir):
        break
    time.sleep(1)
check(core.profile_running(svc.profile_dir), "Firefox avviato: profilo in uso rilevato")
ext = {p.name for p in (svc.profile_dir / "extensions").glob("*.xpi")}
check(f"{core.PROXY_ADDON_ID}.xpi" in ext and len(ext) == 2, f"addon nel profilo: {sorted(ext)}")
check('"network.proxy.socks_port", 1080' in (svc.profile_dir / "user.js").read_text(encoding="utf-8"), "user.js con il proxy")
subprocess.run(["taskkill", "/F", "/T", "/IM", "firefox.exe"], capture_output=True)
time.sleep(3)
check(not core.profile_running(svc.profile_dir), "Firefox chiuso: profilo libero")

# --- password: chiave DPAPI in Local State + AES-GCM, come Chrome/Edge
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402


class Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_protect(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    assert ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
    return ctypes.string_at(out.pbData, out.cbData)


key, nonce = os.urandom(32), os.urandom(12)
user_data = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Edge/User Data"
(user_data / "Default").mkdir(parents=True, exist_ok=True)
(user_data / "Local State").write_text(json.dumps(
    {"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPI" + dpapi_protect(key)).decode()}}))
import sqlite3  # noqa: E402

con = sqlite3.connect(user_data / "Default" / "Login Data")
con.execute("CREATE TABLE logins (origin_url, action_url, username_element, username_value, password_element, "
            "password_value, signon_realm, blacklisted_by_user)")
blob = b"v10" + nonce + AESGCM(key).encrypt(nonce, "pàssword".encode(), None)
con.execute("INSERT INTO logins VALUES (?,?,?,?,?,?,?,0)",
            ("https://www.netflix.com/login", "https://www.netflix.com/login", "email", "io@example.com", "pw", blob,
             "https://www.netflix.com/"))
con.commit()
con.close()
os.environ["STREAM_LAUNCHER_PASSWORD_BROWSER"] = "microsoft-edge"
src = passwords.default_browser()
logins = passwords.read_logins(src, ["netflix.com"])
check(src.name == "Microsoft Edge" and logins and logins[0]["password"] == "pàssword", "password Edge decifrate (DPAPI + AES-GCM)")

# --- GUI e tray (offscreen)
from PySide6.QtWidgets import QApplication  # noqa: E402

from stream_launcher import app  # noqa: E402

qa = QApplication([])
app._bridge = app._Bridge()
win = app.Window()
win.open_editor("netflix")
check(win.editor._collect().proxy == svc.proxy, "editor: valori del servizio")
ctl = app.Controller(qa)
check(len(ctl.menu.actions()) == len(core.list_services()) + 3, "menu della tray")
print("Tutti i test superati.")
