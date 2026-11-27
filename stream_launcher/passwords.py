"""Password da un browser (famiglia Chromium) verso i profili dei servizi.

Il launcher importa una volta, a livello globale, SOLO i login dei domini dei servizi
in un archivio locale protetto (portachiavi GNOME su Linux, DPAPI su Windows).
A ogni avvio di un servizio i login del suo dominio passano dall'archivio a Firefox.

Chrome su Linux cifra le password con AES-128-CBC; la chiave deriva (PBKDF2) dalla
password "Chrome Safe Storage" custodita nel portachiavi GNOME (libsecret).
Su Windows (Chrome, Edge, Brave) le password vecchie (v10/v11) usano una chiave
AES-256-GCM in "Local State" protetta con DPAPI dell'utente; quelle nuove (v20,
Chrome 127+) usano lo schema "app-bound", la cui chiave la decifra solo il servizio
di elevazione del browser, chiamato via COM (IElevator).
A ogni avvio di un servizio si leggono SOLO i login dei suoi domini, si decifrano
e si consegnano a Firefox in un file temporaneo nel profilo (permessi 600): lo
script di autoconfig del runtime li importa all'avvio e cancella subito il file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

IMPORT_FILE = "stream-launcher-logins.json"

# desktop id del browser predefinito -> (cartella dati, nome nel portachiavi, nome leggibile)
BROWSERS = {
    "google-chrome": ("~/.config/google-chrome", "chrome", "Google Chrome"),
    "google-chrome-beta": ("~/.config/google-chrome-beta", "chrome", "Google Chrome Beta"),
    "chromium": ("~/.config/chromium", "chromium", "Chromium"),
    "brave-browser": ("~/.config/BraveSoftware/Brave-Browser", "brave", "Brave"),
}
# Windows: nome -> (cartella dati sotto %LOCALAPPDATA%, nome leggibile)
WIN_BROWSERS = {
    "google-chrome": ("Google/Chrome/User Data", "Google Chrome"),
    "chromium": ("Chromium/User Data", "Chromium"),
    "brave-browser": ("BraveSoftware/Brave-Browser/User Data", "Brave"),
    "microsoft-edge": ("Microsoft/Edge/User Data", "Microsoft Edge"),
}
WIN_PROGIDS = {"ChromeHTML": "google-chrome", "ChromiumHTM": "chromium", "BraveHTML": "brave-browser", "MSEdgeHTM": "microsoft-edge"}
LOGIN_DBS = ("Login Data For Account", "Login Data")  # account Google prima, poi locale

# Chrome 127+ (e Edge, Brave) cifra le password nuove con lo schema "v20" (app-bound):
# la chiave sta in Local State (os_crypt.app_bound_encrypted_key) protetta a livello SYSTEM,
# e la decifra solo il servizio di elevazione del browser, esposto come oggetto COM IElevator.
# Per ogni browser servono CLSID (la classe COM) e IID (l'interfaccia IElevator).
ELEVATORS = {
    "google-chrome": ("{708860E0-F641-4611-8895-7D867DD3675B}", "{463ABECF-410D-407F-8AF5-0DF35A005CC8}"),
    "microsoft-edge": ("{1FCBE96C-1697-43AF-9140-2897C7C69767}", "{C9C2B807-7731-4F34-81B7-44FF7779522B}"),
    "brave-browser": ("{576B31AF-6369-4B6B-8560-E4B203A97A8B}", "{F396861E-0C8E-4C71-8256-2FAE6D759CE9}"),
}


class PasswordError(Exception):
    pass


@dataclass
class Source:
    desktop_id: str
    name: str
    profile_dir: Path
    keyring_app: str


def _windows_default() -> str:
    import winreg

    base = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https"
    for sub_key in ("UserChoiceLatest\\ProgId", "UserChoiceLatest", "UserChoice"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{sub_key}") as k:
                progid = winreg.QueryValueEx(k, "ProgId")[0]
        except OSError:
            continue
        return WIN_PROGIDS.get(progid.split(".")[0], "")
    return ""


def _source(key: str) -> Source | None:
    """Browser per chiave (es. google-chrome, microsoft-edge), se lo conosciamo."""
    key = key.removesuffix(".desktop")
    if sys.platform == "win32":
        if key not in WIN_BROWSERS:
            return None
        rel, label = WIN_BROWSERS[key]
        user_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / rel
        return Source(key, label, user_data / "Default", "")
    if key not in BROWSERS:
        return None
    base, app, label = BROWSERS[key]
    return Source(key, label, Path(base).expanduser() / "Default", app)


def default_browser() -> Source | None:
    """Il browser predefinito del sistema, o quello forzato con
    STREAM_LAUNCHER_PASSWORD_BROWSER (es. google-chrome, chromium, brave-browser, microsoft-edge)."""
    key = os.environ.get("STREAM_LAUNCHER_PASSWORD_BROWSER", "").strip()
    if not key and sys.platform == "win32":
        key = _windows_default()
    elif not key:
        try:
            key = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
    return _source(key)


def installed_browsers() -> list[Source]:
    """Browser compatibili con un profilo sul disco, tra cui l'utente può scegliere."""
    keys = WIN_BROWSERS if sys.platform == "win32" else BROWSERS
    return [s for s in map(_source, keys) if s and s.profile_dir.is_dir()]


# --------------------------------------------------------------------------- AES-128-CBC (solo decifratura)
# Implementazione minima in Python puro: niente dipendenze e la chiave non passa
# mai a processi esterni (la riga di comando di openssl sarebbe leggibile in /proc).


def _build_sboxes() -> tuple[list[int], list[int]]:
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)  # p * 3
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09  # q / 3
        rot = lambda v, n: ((v << n) | (v >> (8 - n))) & 0xFF  # noqa: E731
        sbox[p] = q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


_SBOX, _INV_SBOX = _build_sboxes()


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


_M9, _M11, _M13, _M14 = ([_gmul(i, n) for i in range(256)] for n in (9, 11, 13, 14))


def _round_keys(key: bytes) -> list[list[int]]:
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [_SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[r * 4:(r + 1) * 4], []) for r in range(11)]


def _decrypt_block(block: bytes, rk: list[list[int]]) -> bytes:
    # Stato in ordine di colonna: s[c*4 + r].
    s = [b ^ k for b, k in zip(block, rk[10])]
    for rnd in range(9, -1, -1):
        s = [_INV_SBOX[s[((c - r) % 4) * 4 + r]] for c in range(4) for r in range(4)]  # InvShiftRows+InvSubBytes
        s = [b ^ k for b, k in zip(s, rk[rnd])]
        if rnd:  # InvMixColumns
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                out += [
                    _M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3],
                    _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                    _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3],
                    _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3],
                ]
            s = out
    return bytes(s)


def aes128_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise PasswordError("dato cifrato di lunghezza non valida")
    rk = _round_keys(key)
    out, prev = bytearray(), iv
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, rk), prev))
        prev = block
    pad = out[-1]
    if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
        raise PasswordError("chiave errata o dato corrotto")
    return bytes(out[:-pad])


# --------------------------------------------------------------------------- Chrome


def _keyring_secret(app: str) -> bytes | None:
    try:
        res = subprocess.run(["secret-tool", "lookup", "application", app], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    secret = res.stdout.rstrip(b"\n")
    return secret if res.returncode == 0 and secret else None


def _derive(secret: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1, 16)


class ChromeDecryptor:
    IV = b" " * 16

    def __init__(self, source: Source):
        self.key10 = _derive(b"peanuts")  # schema "v10": chiave fissa, senza portachiavi
        secret = _keyring_secret(source.keyring_app)
        self.key11 = _derive(secret) if secret else None
        self.problem = "" if self.key11 else f"chiave di {source.name} non trovata nel portachiavi"

    def decrypt(self, blob: bytes) -> str:
        prefix, data = blob[:3], blob[3:]
        if prefix == b"v10":
            key = self.key10
        elif prefix == b"v11":
            if not self.key11:
                raise PasswordError("chiave del portachiavi non disponibile (portachiavi bloccato?)")
            key = self.key11
        else:
            raise PasswordError("schema di cifratura non supportato")
        return aes128_cbc_decrypt(key, self.IV, data).decode("utf-8")


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Cifra o decifra con la chiave dell'utente Windows (DPAPI)."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise PasswordError("DPAPI: operazione non riuscita")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _elevator_decrypt(desktop_id: str, blob: bytes) -> bytes:
    """Chiave app-bound (schema v20) decifrata dal servizio di elevazione del browser.

    Il servizio è un oggetto COM (IElevator) che gira come SYSTEM; chiama DecryptData,
    che restituisce la chiave AES-256 usata per le password v20. Non serve essere
    amministratore, ma il browser deve avere il servizio registrato (installazione
    normale) e conviene chiuderlo prima. Solo Windows.
    """
    import ctypes
    from ctypes import wintypes

    guids = ELEVATORS.get(desktop_id)
    if not guids:
        raise PasswordError("questo browser non ha un servizio di elevazione per lo schema v20")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def guid(text: str) -> GUID:
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
        return g

    ole32, oleaut32 = ctypes.oledll.ole32, ctypes.windll.oleaut32
    oleaut32.SysAllocStringByteLen.restype = ctypes.c_void_p
    oleaut32.SysStringByteLen.restype = ctypes.c_uint
    oleaut32.SysStringByteLen.argtypes = [ctypes.c_void_p]

    # Apartment a thread singolo. Se COM è già inizializzato nel processo (la GUI Qt lo fa),
    # CoInitializeEx dà S_FALSE/RPC_E_CHANGED_MODE: va bene, non lo chiudiamo noi.
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    uninit = hr == 0  # S_OK: l'abbiamo inizializzato noi, tocca a noi chiuderlo
    clsid, iid = guid(guids[0]), guid(guids[1])
    pointer = ctypes.c_void_p()
    bstr_in = bstr_out = None
    try:
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x4,  # CLSCTX_LOCAL_SERVER
                               ctypes.byref(iid), ctypes.byref(pointer))
        # Impersonazione: senza di questa il servizio rifiuta la richiesta.
        ole32.CoSetProxyBlanket(pointer, 0xFFFFFFFF, 0xFFFFFFFF, None, 0, 3,  # RPC_C_IMP_LEVEL_IMPERSONATE
                                None, 0x40)  # EOAC_DYNAMIC_CLOAKING
        # Vtable: QueryInterface/AddRef/Release (0-2), RunRecoveryCRXElevated (3),
        # EncryptData (4), DecryptData (5). DecryptData(this, BSTR in, BSTR* out, DWORD* err).
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0]
        slot = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[5]
        decrypt_data = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD))(slot)
        bstr_in = ctypes.c_void_p(oleaut32.SysAllocStringByteLen(blob, len(blob)))
        out, last_error = ctypes.c_void_p(), wintypes.DWORD(0)
        hr = decrypt_data(pointer, bstr_in, ctypes.byref(out), ctypes.byref(last_error))
        bstr_out = out
        if hr != 0 or not out.value:
            raise PasswordError(f"servizio di elevazione: errore 0x{hr & 0xFFFFFFFF:08X} "
                                f"(last_error {last_error.value})")
        return ctypes.string_at(out.value, oleaut32.SysStringByteLen(out))
    except OSError as e:  # oledll solleva WindowsError sugli HRESULT falliti
        raise PasswordError(f"servizio di elevazione non raggiungibile ({e})") from e
    finally:
        if bstr_in:
            oleaut32.SysFreeString(bstr_in)
        if bstr_out:
            oleaut32.SysFreeString(bstr_out)
        if pointer:
            vt = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0]  # rilascio via Release (slot 2)
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                ctypes.cast(vt, ctypes.POINTER(ctypes.c_void_p))[2])
            release(pointer)
        if uninit:
            ctypes.windll.ole32.CoUninitialize()


class WindowsDecryptor:
    def __init__(self, source: Source):
        self.key, self.problem = None, ""
        self.app_key, self.app_problem = None, ""
        try:
            state = json.loads((source.profile_dir.parent / "Local State").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.problem = self.app_problem = f"Local State di {source.name} non leggibile ({e})"
            return
        try:
            enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
            if enc[:5] != b"DPAPI":
                raise ValueError("formato della chiave sconosciuto")
            self.key = _dpapi(enc[5:], protect=False)
        except (KeyError, ValueError, PasswordError) as e:
            self.problem = f"chiave di {source.name} non leggibile ({e})"
        # Chiave app-bound (v20): assente sui Chrome più vecchi, presente dal 127 in poi.
        app_enc = state.get("os_crypt", {}).get("app_bound_encrypted_key")
        if not app_enc:
            self.app_problem = "nessuna chiave app-bound (v20) in Local State"
            return
        try:
            data = base64.b64decode(app_enc)
            if data[:4] != b"APPB":
                raise ValueError("formato della chiave app-bound sconosciuto")
            self.app_key = _elevator_decrypt(source.desktop_id, data[4:])
        except (ValueError, PasswordError) as e:
            self.app_problem = f"chiave app-bound di {source.name} non ottenuta ({e})"

    def decrypt(self, blob: bytes) -> str:
        prefix = blob[:3]
        if prefix not in (b"v10", b"v11", b"v20"):
            return _dpapi(blob, protect=False).decode("utf-8")  # vecchi Chrome: DPAPI diretto
        key = self.app_key if prefix == b"v20" else self.key
        if key is None:
            raise PasswordError(self.app_problem if prefix == b"v20" else self.problem)
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            return AESGCM(key).decrypt(blob[3:15], blob[15:], None).decode("utf-8")
        except InvalidTag as e:
            raise PasswordError("chiave errata o dato corrotto") from e


def _origin(url: str) -> str:
    u = urllib.parse.urlsplit(url or "")
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}"


def matches_domain(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def read_logins(source: Source, domains: list[str]) -> list[dict]:
    """Login del browser per i domini indicati, già decifrati e deduplicati."""
    if not domains:
        return []
    decryptor = WindowsDecryptor(source) if sys.platform == "win32" else ChromeDecryptor(source)
    # Su Windows una delle due chiavi (classica v10/v11 o app-bound v20) può mancare senza
    # che l'altra sia inutile: si blocca solo se non se ne può usare nessuna.
    if decryptor.problem and (getattr(decryptor, "app_key", True) is None):
        raise PasswordError(decryptor.problem)
    found: dict[tuple[str, str], dict] = {}
    blocked = ""  # motivo per cui una voce che corrispondeva è stata saltata (per spiegare lo 0)
    for db_name in LOGIN_DBS:
        path = source.profile_dir / db_name
        if not path.is_file():
            continue
        # immutable=1: si legge anche mentre Chrome tiene il database aperto.
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            rows = con.execute(
                "SELECT origin_url, action_url, username_element, username_value, "
                "password_element, password_value, signon_realm, blacklisted_by_user FROM logins"
            ).fetchall()
            con.close()
        except sqlite3.Error as e:
            raise PasswordError(f"lettura di {db_name} fallita: {e}") from e
        for origin_url, action_url, user_el, user, pass_el, blob, realm, blacklisted in rows:
            if blacklisted or not blob or not user:
                continue
            origin = _origin(origin_url) or _origin(realm)
            host = urllib.parse.urlsplit(origin).hostname or ""
            if not origin or not matches_domain(host, domains) or (origin, user) in found:
                continue  # a parità di sito e utente vince l'archivio dell'account Google
            try:
                password = decryptor.decrypt(blob)
            except (PasswordError, UnicodeDecodeError) as e:
                blocked = blocked or str(e)
                continue
            found[(origin, user)] = {
                "origin": origin,
                "formActionOrigin": _origin(action_url) or origin,
                "username": user,
                "password": password,
                "usernameField": user_el or "",
                "passwordField": pass_el or "",
            }
    if not found and blocked:  # c'erano login del dominio ma nessuno decifrabile: dillo
        raise PasswordError(blocked)
    return list(found.values())


# Servizi che fanno il login su un dominio diverso da quello dell'indirizzo.
DOMAIN_ALIASES = {
    "primevideo.com": ["amazon.it", "amazon.com"],
    "nowtv.it": ["sky.it"],
    "sky.it": ["nowtv.it"],
    "hbomax.com": ["max.com"],
    "youtube.com": ["google.com"],
    "la7play.it": ["la7.it"],
}


def domains_for(url: str) -> list[str]:
    """Dominio registrabile dell'indirizzo (es. www.netflix.com -> netflix.com) + alias noti."""
    host = urllib.parse.urlsplit(url).hostname or ""
    parts = host.split(".")
    if len(parts) < 2:
        return []
    base = ".".join(parts[-2:])
    return [base, *DOMAIN_ALIASES.get(base, [])]


def stage_import(profile: Path, logins: list[dict]) -> None:
    """Scrive il file che l'autoconfig di Firefox importa e cancella all'avvio."""
    target = profile / IMPORT_FILE
    target.unlink(missing_ok=True)
    if not logins:
        return
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(logins, f)


# --------------------------------------------------------------------------- archivio locale

STORE_ATTRS = ["application", "stream-launcher", "kind", "logins"]


def _store_file() -> Path:
    from . import core

    return core.CONFIG_DIR / "passwords.bin"


def save_store(store: dict) -> None:
    data = json.dumps(store).encode("utf-8")
    if sys.platform == "win32":
        path = _store_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_dpapi(data, protect=True))
        return
    try:
        res = subprocess.run(["secret-tool", "store", "--label=Stream Launcher: login dei servizi", *STORE_ATTRS],
                             input=data, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PasswordError(f"portachiavi non disponibile (serve secret-tool): {e}") from e
    if res.returncode:
        raise PasswordError(f"portachiavi: {res.stderr.decode(errors='replace').strip() or 'scrittura non riuscita'}")


def load_store() -> dict:
    """{"browser": nome, "updated": data ISO, "logins": [...]}; vuoto se non c'è."""
    try:
        if sys.platform == "win32":
            data = _dpapi(_store_file().read_bytes(), protect=False)
        else:
            data = subprocess.run(["secret-tool", "lookup", *STORE_ATTRS], capture_output=True, timeout=15).stdout
        return json.loads(data) if data.strip() else {}
    except (OSError, ValueError, PasswordError, subprocess.TimeoutExpired):
        return {}


def clear_store() -> None:
    if sys.platform == "win32":
        _store_file().unlink(missing_ok=True)
        return
    try:
        subprocess.run(["secret-tool", "clear", *STORE_ATTRS], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _key(login: dict) -> list[str]:
    return [login["origin"], login["username"]]


def import_logins(source: Source, domains: list[str]) -> dict:
    """Aggiorna l'archivio con i login del browser per i domini indicati.

    Le voci modificate a mano restano come le hai lasciate e quelle eliminate
    a mano non ricompaiono: l'import non annulla le correzioni.
    """
    import datetime

    old = load_store()
    removed = old.get("removed", [])
    merged = {tuple(_key(lg)): lg for lg in read_logins(source, domains) if _key(lg) not in removed}
    merged.update({tuple(_key(lg)): lg for lg in old.get("logins", []) if lg.get("manual")})
    store = {
        "browser": source.name,
        "updated": datetime.datetime.now().isoformat(timespec="minutes"),
        "logins": list(merged.values()),
        "removed": removed,
    }
    save_store(store)
    return store


def edit_login(store: dict, old_key: list[str] | None, site: str, username: str, password: str) -> dict:
    """Sostituisce (o aggiunge, con old_key None) una voce; ritorna l'archivio salvato."""
    origin = _origin(site if "://" in site else f"https://{site.strip()}")
    if not origin or not username or not password:
        raise PasswordError("servono sito, utente e password")
    logins = [lg for lg in store.get("logins", []) if _key(lg) != old_key]
    previous = next((lg for lg in store.get("logins", []) if _key(lg) == old_key), {})
    new = {**previous, "origin": origin, "username": username, "password": password, "manual": True}
    if previous.get("origin") != origin:
        new["formActionOrigin"] = origin
    new.setdefault("usernameField", "")
    new.setdefault("passwordField", "")
    if any(_key(lg) == _key(new) for lg in logins):
        raise PasswordError("esiste già una voce con questo sito e utente")
    removed = store.get("removed", [])
    if old_key and old_key != _key(new) and old_key not in removed:
        removed = [*removed, old_key]  # la versione del browser non deve ricomparire
    store = {**store, "logins": [*logins, new], "removed": removed}
    store.setdefault("browser", "inserimento manuale")
    store.setdefault("updated", "")
    save_store(store)
    return store


def delete_logins(store: dict, keys: list[list[str]]) -> dict:
    """Elimina le voci indicate; un nuovo import non le riporterà."""
    removed = store.get("removed", [])
    store = {
        **store,
        "logins": [lg for lg in store.get("logins", []) if _key(lg) not in keys],
        "removed": removed + [k for k in keys if k not in removed],
    }
    save_store(store)
    return store


def logins_for(domains: list[str], store: dict | None = None) -> list[dict]:
    store = load_store() if store is None else store
    return [lg for lg in store.get("logins", [])
            if matches_domain(urllib.parse.urlsplit(lg["origin"]).hostname or "", domains)]
