"""Test su Windows reale (CI): catalogo, runtime, collegamenti, icona nella barra
delle applicazioni, archivio password, GUI.

Gira con APPDATA/LOCALAPPDATA puntati a cartelle temporanee, dopo `init`.
"""
import base64
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import time
from ctypes import POINTER, WINFUNCTYPE, byref, c_int, c_void_p, c_wchar_p
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stream_launcher import core, passwords  # noqa: E402
from stream_launcher import winshell as ws  # noqa: E402


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        sys.exit(1)


def links():
    return sorted(p.name for p in core.APPS_DIR.glob("*.lnk"))


# --- prima installazione: niente servizi, niente runtime
check(core.list_services() == [] and not core.runtime_installed(), "init: nessun servizio e nessun runtime")
check(links() == ["Stream Launcher.lnk"], f"init: solo la voce del launcher ({links()})")

# --- installare dal catalogo scarica il runtime
core.add_from_template("netflix")
core.add_from_template("primevideo")
check(core.runtime_installed(), f"primo servizio: runtime Firefox {core.runtime_version()} scaricato")
check(len(links()) == 3 and any(core.ICONS_DIR.glob("*.ico")), f"voci e icone dei servizi ({links()})")
check(core.fetch_catalog_icons() >= 0 and core.catalog_icon_path("youtube").is_file(), "icone del catalogo")

# --- rinomina ed eliminazione aggiornano i collegamenti
svc = core.load_service("netflix")
svc.name = "Netflix: prova"
core.save_service(svc)
core.refresh_desktop_entry(svc, icon=False)
check((core.APPS_DIR / "Netflix prova.lnk").is_file() and not (core.APPS_DIR / "Netflix.lnk").exists(), "rinomina: collegamento ricreato")
dup = core.duplicate_service("netflix", "Netflix 2")
core.remove_service(dup.id)
check(not (core.APPS_DIR / "Netflix 2.lnk").exists(), "eliminazione: collegamento tolto")


# --- AppUserModelID: collegamento e finestra di Firefox devono coincidere
shell32, user32 = ctypes.windll.shell32, ctypes.windll.user32
shell32.SHGetPropertyStoreFromParsingName.argtypes = (c_wchar_p, c_void_p, c_int, POINTER(ws.GUID), POINTER(c_void_p))
shell32.SHGetPropertyStoreForWindow.argtypes = (c_void_p, POINTER(ws.GUID), POINTER(c_void_p))


def read_aumid(store) -> str:
    pv = ws.PROPVARIANT()
    ws.call(store, 5, (POINTER(ws.PROPERTYKEY), POINTER(ws.PROPVARIANT)), byref(ws.aumid_key()), byref(pv))
    return ctypes.wstring_at(pv.value) if pv.vt == ws.VT_LPWSTR else ""


def shortcut_aumid(path: Path) -> str:
    ctypes.windll.ole32.CoInitializeEx(None, 2)
    store = c_void_p()
    hr = shell32.SHGetPropertyStoreFromParsingName(str(path), None, 0, byref(ws.GUID.of(ws.IID_PROPERTY_STORE)), byref(store))
    return read_aumid(store) if hr >= 0 else f"errore 0x{hr & 0xFFFFFFFF:08X}"


def firefox_window_aumids() -> list:
    found = []

    @WINFUNCTYPE(c_int, c_void_p, c_void_p)
    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(c_void_p(hwnd), name, 64)
        if name.value == "MozillaWindowClass" and user32.IsWindowVisible(c_void_p(hwnd)):
            store = c_void_p()
            if shell32.SHGetPropertyStoreForWindow(c_void_p(hwnd), byref(ws.GUID.of(ws.IID_PROPERTY_STORE)), byref(store)) >= 0:
                found.append(read_aumid(store))
        return 1

    user32.EnumWindows(visit, 0)
    return found


expected = ws.firefox_aumid(str(svc.profile_dir))
check(shortcut_aumid(core.APPS_DIR / "Netflix prova.lnk") == expected, f"collegamento con AppUserModelID {expected}")

svc.addons = ["amo:ublock-origin"]
svc.proxy = core.Proxy("socks5", "127.0.0.1", 1080, "utente", "segreto")  # usa l'estensione proxy
core.save_service(svc)
core.launch(svc)  # finestra vera, non headless: serve per leggere l'AppUserModelID
windows = []
for _ in range(90):
    windows = [a for a in firefox_window_aumids() if a]
    if windows:
        break
    time.sleep(1)
print(f"     finestre Firefox: {windows}, atteso: {expected}")
check(expected in windows, "finestra di Firefox raggruppata sotto il collegamento del servizio (icona e nome)")
check(core.profile_running(svc.profile_dir), "profilo in uso rilevato")
ext = {p.name for p in (svc.profile_dir / "extensions").glob("*.xpi")}
check(f"{core.PROXY_ADDON_ID}.xpi" in ext and len(ext) == 2, f"addon nel profilo: {sorted(ext)}")
subprocess.run(["taskkill", "/F", "/T", "/IM", "firefox.exe"], capture_output=True)
time.sleep(3)
check(not core.profile_running(svc.profile_dir), "Firefox chiuso: profilo libero")

# --- password: Edge finto (chiave DPAPI + AES-GCM) -> archivio del launcher -> profilo
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

key, nonce = os.urandom(32), os.urandom(12)
user_data = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Edge/User Data"
(user_data / "Default").mkdir(parents=True, exist_ok=True)
(user_data / "Local State").write_text(json.dumps(
    {"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPI" + passwords._dpapi(key, protect=True)).decode()}}))
con = sqlite3.connect(user_data / "Default" / "Login Data")
con.execute("CREATE TABLE logins (origin_url, action_url, username_element, username_value, password_element, "
            "password_value, signon_realm, blacklisted_by_user)")
rows = [("https://www.netflix.com/login", "io@example.com", "pàssword"), ("https://www.banca.it/", "io", "segreta")]
for origin, user, pw in rows:
    blob = b"v10" + nonce + AESGCM(key).encrypt(nonce, pw.encode(), None)
    con.execute("INSERT INTO logins VALUES (?,?,?,?,?,?,?,0)", (origin, origin, "u", user, "p", blob, origin))
con.commit()
con.close()
edge = next((b for b in passwords.installed_browsers() if b.desktop_id == "microsoft-edge"), None)
check(edge is not None, f"browser installati: {[b.name for b in passwords.installed_browsers()]}")
store = passwords.import_logins(edge, core.all_login_domains())
check([lg["origin"] for lg in store["logins"]] == ["https://www.netflix.com"], "import: solo i login dei servizi (niente banca)")
check(b"netflix" not in core.CONFIG_DIR.joinpath("passwords.bin").read_bytes(), "archivio cifrato su disco (DPAPI)")
check(passwords.load_store()["logins"][0]["password"] == "pàssword", "archivio riletto e decifrato")
check(core.sync_passwords(svc) == 1 and (svc.profile_dir / passwords.IMPORT_FILE).is_file(), "avvio: login passato al profilo")
passwords.clear_store()
check(passwords.load_store() == {}, "archivio svuotato")

# --- GUI e tray (offscreen)
from PySide6.QtWidgets import QApplication  # noqa: E402

from stream_launcher import app  # noqa: E402

qa = QApplication([])
app._bridge = app._Bridge()
win = app.Window()
check(win.list_page.list.count() == 2 + 2 + len(core.list_templates()) - 2, "elenco: sezioni, servizi e catalogo")
win.open_editor("netflix")
check(win.editor._collect().proxy == svc.proxy, "editor: valori del servizio")
win.open_passwords()
check(win.editor.browser.count() >= 1, "pagina password")
ctl = app.Controller(qa)
check(len(ctl.menu.actions()) == len(core.list_services()) + 3, "menu della tray")
print("Tutti i test superati.")
