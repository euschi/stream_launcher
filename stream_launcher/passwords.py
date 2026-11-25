"""Password dal browser predefinito (famiglia Chromium) verso i profili dei servizi.

Chrome su Linux cifra le password con AES-128-CBC; la chiave deriva (PBKDF2) dalla
password "Chrome Safe Storage" custodita nel portachiavi GNOME (libsecret).
Su Windows (Chrome, Edge, Brave) la chiave AES-256-GCM sta in "Local State",
protetta con DPAPI dell'utente.
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


def default_browser() -> Source | None:
    """Browser da cui leggere le password: quello predefinito, o quello forzato
    con STREAM_LAUNCHER_PASSWORD_BROWSER (es. google-chrome, chromium, brave-browser,
    microsoft-edge)."""
    desktop_id = os.environ.get("STREAM_LAUNCHER_PASSWORD_BROWSER", "").strip()
    if sys.platform == "win32":
        name = desktop_id or _windows_default()
        if name not in WIN_BROWSERS:
            return None
        rel, label = WIN_BROWSERS[name]
        user_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / rel
        return Source(name, label, user_data / "Default", "")
    if not desktop_id:
        try:
            desktop_id = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
    desktop_id = desktop_id.removesuffix(".desktop")
    if desktop_id not in BROWSERS:
        return None
    base, app, name = BROWSERS[desktop_id]
    return Source(desktop_id, name, Path(base).expanduser() / "Default", app)


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


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise PasswordError("DPAPI: decifratura non riuscita")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class WindowsDecryptor:
    def __init__(self, source: Source):
        self.key, self.problem = None, ""
        try:
            state = json.loads((source.profile_dir.parent / "Local State").read_text(encoding="utf-8"))
            enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
            if enc[:5] != b"DPAPI":
                raise ValueError("formato della chiave sconosciuto")
            self.key = _dpapi_unprotect(enc[5:])
        except (OSError, KeyError, ValueError, PasswordError) as e:
            self.problem = f"chiave di {source.name} non leggibile ({e})"

    def decrypt(self, blob: bytes) -> str:
        prefix = blob[:3]
        if prefix == b"v20":
            raise PasswordError("cifratura legata all'app (v20) non supportata")
        if prefix not in (b"v10", b"v11"):
            return _dpapi_unprotect(blob).decode("utf-8")  # vecchi Chrome: DPAPI diretto
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            return AESGCM(self.key).decrypt(blob[3:15], blob[15:], None).decode("utf-8")
        except InvalidTag as e:
            raise PasswordError("chiave errata o dato corrotto") from e


def _origin(url: str) -> str:
    u = urllib.parse.urlsplit(url or "")
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}"


def _matches(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def read_logins(source: Source, domains: list[str]) -> list[dict]:
    """Login del browser per i domini indicati, già decifrati e deduplicati."""
    if not domains:
        return []
    decryptor = WindowsDecryptor(source) if sys.platform == "win32" else ChromeDecryptor(source)
    if decryptor.problem:
        raise PasswordError(decryptor.problem)
    found: dict[tuple[str, str], dict] = {}
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
            if not origin or not _matches(host, domains) or (origin, user) in found:
                continue  # a parità di sito e utente vince l'archivio dell'account Google
            try:
                password = decryptor.decrypt(blob)
            except (PasswordError, UnicodeDecodeError):
                continue
            found[(origin, user)] = {
                "origin": origin,
                "formActionOrigin": _origin(action_url) or origin,
                "username": user,
                "password": password,
                "usernameField": user_el or "",
                "passwordField": pass_el or "",
            }
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
