"""Logica condivisa: servizi, runtime Firefox, profili, addon, proxy e voci di menu.

Ogni servizio è un file INI in ~/.config/stream-launcher/services/<id>.ini e ha
un profilo Firefox tutto suo in ~/.local/share/stream-launcher/profiles/<id>
(su Windows: %APPDATA%\\stream-launcher e %LOCALAPPDATA%\\stream-launcher).
Il runtime Firefox portatile è unico e condiviso da tutti i servizi.
"""
from __future__ import annotations

import configparser
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

APP = "stream-launcher"
IS_WIN = sys.platform == "win32"
FROZEN = getattr(sys, "frozen", False)  # eseguibile PyInstaller (Windows)


def _env_dir(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


if IS_WIN:
    DATA_DIR = _env_dir("LOCALAPPDATA", "AppData/Local") / APP
    CONFIG_DIR = _env_dir("APPDATA", "AppData/Roaming") / APP
    # Cartella del menu Start: collegamenti .lnk, uno per servizio.
    APPS_DIR = _env_dir("APPDATA", "AppData/Roaming") / "Microsoft/Windows/Start Menu/Programs/Stream Launcher"
else:
    XDG_DATA = _env_dir("XDG_DATA_HOME", ".local/share")
    DATA_DIR = XDG_DATA / APP
    CONFIG_DIR = _env_dir("XDG_CONFIG_HOME", ".config") / APP
    APPS_DIR = XDG_DATA / "applications"
SERVICES_DIR = CONFIG_DIR / "services"
SETTINGS_FILE = CONFIG_DIR / "settings.ini"
ICONS_DIR = DATA_DIR / "icons"
LOG_FILE = DATA_DIR / "launch.log"


def _storage_root() -> Path:
    """Dove stanno runtime, profili e cache addon: spostabile su un disco veloce."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(SETTINGS_FILE, encoding="utf-8")
    custom = cp.get("paths", "storage", fallback="").strip()
    return Path(custom).expanduser() if custom else DATA_DIR


STORAGE_DIR = _storage_root()
STORAGE_ITEMS = ("firefox", "profiles", "addons-cache")
RUNTIME_DIR = STORAGE_DIR / "firefox"
PROFILES_DIR = STORAGE_DIR / "profiles"
ADDON_CACHE = STORAGE_DIR / "addons-cache"

# Radice dell'applicazione (repo, copia installata o eseguibile): contiene config/, templates/, bin/.
APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ASSETS_DIR = APP_ROOT / "config"
TEMPLATES_DIR = APP_ROOT / "templates"
CLI_PATH = APP_ROOT / "bin" / APP

_OS = "win64" if IS_WIN else "linux64"
RUNTIME_URLS = {
    # ESR permette addon non firmati (addon Chrome convertiti, estensione proxy).
    "esr": f"https://download.mozilla.org/?product=firefox-esr-latest-ssl&os={_OS}&lang=it",
    "release": f"https://download.mozilla.org/?product=firefox-latest-ssl&os={_OS}&lang=it",
}
DEFAULT_CHANNEL = "esr"

PROXY_TYPES = ("none", "http", "https", "socks5", "socks4")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
CHROME_ID_RE = re.compile(r"^[a-p]{32}$")
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) stream-launcher"}

PROXY_ADDON_ID = "proxy@stream-launcher.local"
ADDON_STATE_FILE = "stream-launcher-addons.json"

# Preferenze generate dal launcher: vengono tolte da prefs.js prima di ogni avvio,
# così togliere un'impostazione (es. il proxy) ha effetto davvero.
MANAGED_PREFS = (
    "browser.startup.homepage",
    "general.useragent.override",
    "general.platform.override",
    "general.oscpu.override",
    "general.appversion.override",
    "network.proxy.",
    "media.peerconnection.ice.proxy_only",
)


class LauncherError(Exception):
    """Errore mostrabile all'utente così com'è."""


def self_command(*args: str) -> list[str]:
    """Riga di comando per rilanciare il launcher (eseguibile o script) con `args`."""
    if FROZEN:
        return [sys.executable, *args]
    python = Path(sys.executable)
    if IS_WIN and (python.parent / "pythonw.exe").is_file():
        python = python.parent / "pythonw.exe"  # niente finestra di console
    return [str(python), str(CLI_PATH), *args]


def spawn_detached(cmd: list[str], **kwargs) -> subprocess.Popen:
    """Processo staccato dal chiamante: sopravvive alla sua chiusura."""
    if IS_WIN:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, **kwargs)


# --------------------------------------------------------------------------- servizi


@dataclass
class Proxy:
    type: str = "none"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    @property
    def enabled(self) -> bool:
        return self.type != "none" and bool(self.host) and self.port > 0

    @property
    def needs_extension(self) -> bool:
        """Firefox da solo non gestisce credenziali SOCKS né proxy HTTPS (TLS)."""
        return self.enabled and (self.type == "https" or bool(self.username))

    def describe(self) -> str:
        if not self.enabled:
            return "nessun proxy"
        auth = " con credenziali" if self.username else ""
        return f"{self.type.upper()} {self.host}:{self.port}{auth}"


@dataclass
class Service:
    id: str
    name: str
    url: str
    icon_url: str = ""
    # "" = predefinito, "windows" = finto Firefox su Windows, altro = stringa personalizzata.
    user_agent: str = ""
    addons: list[str] = field(default_factory=list)
    proxy: Proxy = field(default_factory=Proxy)
    # "auto" = importa a ogni avvio le password dal browser predefinito, "off" = no.
    passwords: str = "auto"
    # Domini dei login da importare; vuoto = dominio dell'indirizzo + alias noti.
    login_domains: list[str] = field(default_factory=list)

    @property
    def app_id(self) -> str:
        # Coincide con il nome del file .desktop: GNOME raggruppa le finestre giuste.
        return f"{APP}-{self.id}"

    @property
    def profile_dir(self) -> Path:
        return PROFILES_DIR / self.id

    @property
    def icon_path(self) -> Path:
        return ICONS_DIR / f"{self.id}.png"

    @property
    def desktop_path(self) -> Path:
        return APPS_DIR / f"{self.app_id}.desktop"

    @property
    def config_path(self) -> Path:
        return SERVICES_DIR / f"{self.id}.ini"


def validate_id(service_id: str) -> str:
    if not ID_RE.match(service_id):
        raise LauncherError(
            f"ID «{service_id}» non valido: solo minuscole, cifre e trattini (max 48)."
        )
    return service_id


def split_domains(text: str) -> list[str]:
    return [d.strip().lower().lstrip(".") for d in re.split(r"[,\s]+", text) if d.strip()]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "servizio"


def unique_id(base: str) -> str:
    base = slugify(base)
    candidate, n = base, 2
    while (SERVICES_DIR / f"{candidate}.ini").exists():
        candidate = f"{base[:44]}-{n}"
        n += 1
    return candidate


def _parse(path: Path, service_id: str) -> Service:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as e:
        raise LauncherError(f"{path}: file non valido ({e})") from e
    s = cp["service"] if cp.has_section("service") else {}
    p = cp["proxy"] if cp.has_section("proxy") else {}
    proxy_type = p.get("type", "none").strip().lower() or "none"
    if proxy_type not in PROXY_TYPES:
        raise LauncherError(f"{path}: tipo di proxy «{proxy_type}» non supportato")
    try:
        port = int(p.get("port", "0") or 0)
    except ValueError as e:
        raise LauncherError(f"{path}: porta del proxy non valida") from e
    url = s.get("url", "").strip()
    if not url:
        raise LauncherError(f"{path}: manca l'indirizzo (url)")
    return Service(
        id=service_id,
        name=s.get("name", service_id).strip() or service_id,
        url=url,
        icon_url=s.get("icon_url", "").strip(),
        user_agent=s.get("user_agent", "").strip(),
        addons=[a.strip() for a in s.get("addons", "").splitlines() if a.strip()],
        passwords="off" if s.get("passwords", "auto").strip().lower() == "off" else "auto",
        login_domains=split_domains(s.get("login_domains", "")),
        proxy=Proxy(
            type=proxy_type,
            host=p.get("host", "").strip(),
            port=port,
            username=p.get("username", "").strip(),
            password=p.get("password", ""),
        ),
    )


def load_service(service_id: str) -> Service:
    path = SERVICES_DIR / f"{validate_id(service_id)}.ini"
    if not path.is_file():
        raise LauncherError(f"Servizio «{service_id}» inesistente.")
    return _parse(path, service_id)


def list_services() -> list[Service]:
    if not SERVICES_DIR.is_dir():
        return []
    services = []
    for path in SERVICES_DIR.glob("*.ini"):
        try:
            services.append(_parse(path, path.stem))
        except LauncherError as e:
            print(f"Ignoro {path.name}: {e}", file=sys.stderr)
    return sorted(services, key=lambda s: s.name.lower())


def save_service(svc: Service) -> None:
    validate_id(svc.id)
    if not svc.name.strip():
        raise LauncherError("Il nome non può essere vuoto.")
    if not re.match(r"^https?://", svc.url):
        raise LauncherError("L'indirizzo deve iniziare con http:// o https://")
    if svc.proxy.type not in PROXY_TYPES:
        raise LauncherError(f"Tipo di proxy «{svc.proxy.type}» non supportato.")
    if svc.proxy.type != "none" and not (svc.proxy.host and 0 < svc.proxy.port < 65536):
        raise LauncherError("Per il proxy servono host e porta (1-65535).")

    cp = configparser.ConfigParser(interpolation=None)
    cp["service"] = {
        "name": svc.name.strip(),
        "url": svc.url.strip(),
        "icon_url": svc.icon_url,
        "user_agent": svc.user_agent,
        "addons": "\n".join(svc.addons),
        "passwords": svc.passwords,
        "login_domains": ", ".join(svc.login_domains),
    }
    cp["proxy"] = {
        "type": svc.proxy.type,
        "host": svc.proxy.host,
        "port": str(svc.proxy.port or ""),
        "username": svc.proxy.username,
        "password": svc.proxy.password,
    }
    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    SERVICES_DIR.chmod(0o700)
    # Il file può contenere la password del proxy: leggibile solo dall'utente.
    tmp = svc.config_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        cp.write(f)
    tmp.replace(svc.config_path)


def list_templates() -> list[Service]:
    return sorted(
        (_parse(p, p.stem) for p in TEMPLATES_DIR.glob("*.ini")),
        key=lambda s: s.name.lower(),
    )


def add_from_template(template_id: str, name: str | None = None, new_id: str | None = None) -> Service:
    path = TEMPLATES_DIR / f"{template_id}.ini"
    if not path.is_file():
        raise LauncherError(f"Modello «{template_id}» inesistente.")
    svc = _parse(path, template_id)
    svc = replace(svc, name=name or svc.name)
    svc.id = validate_id(new_id) if new_id else unique_id(template_id)
    if svc.config_path.exists():
        raise LauncherError(f"Esiste già un servizio con ID «{svc.id}».")
    ensure_runtime()
    save_service(svc)
    catalog = catalog_icon_path(template_id)
    if catalog.is_file() and not svc.icon_path.is_file():
        shutil.copyfile(catalog, svc.icon_path)
    refresh_desktop_entry(svc)
    return svc


def catalog_icon_path(template_id: str) -> Path:
    return ICONS_DIR / "catalog" / f"{template_id}.png"


def fetch_catalog_icons() -> int:
    """Scarica le icone dei modelli che non le hanno ancora; ritorna quante."""
    fetched = 0
    for t in list_templates():
        dest = catalog_icon_path(t.id)
        if not dest.is_file():
            fetch_icon(t, dest)
            fetched += dest.is_file()
    return fetched


def duplicate_service(
    src_id: str, name: str, new_id: str | None = None, copy_profile: bool = False
) -> Service:
    src = load_service(src_id)
    dst = replace(src, name=name.strip(), proxy=replace(src.proxy), addons=list(src.addons))
    dst.id = validate_id(new_id) if new_id else unique_id(name)
    if dst.config_path.exists():
        raise LauncherError(f"Esiste già un servizio con ID «{dst.id}».")
    if copy_profile and src.profile_dir.is_dir():
        if profile_running(src.profile_dir):
            raise LauncherError(f"Chiudi «{src.name}» prima di copiarne il profilo.")
        shutil.copytree(
            src.profile_dir,
            dst.profile_dir,
            symlinks=True,
            ignore=shutil.ignore_patterns("lock", ".parentlock", "parent.lock", "cache2", "startupCache"),
        )
    save_service(dst)
    if src.icon_path.is_file():
        ICONS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src.icon_path, dst.icon_path)
    refresh_desktop_entry(dst)
    return dst


def remove_service(service_id: str, keep_profile: bool = False) -> None:
    svc = load_service(service_id)
    if profile_running(svc.profile_dir):
        raise LauncherError(f"Chiudi «{svc.name}» prima di eliminarlo.")
    svc.config_path.unlink(missing_ok=True)
    svc.icon_path.unlink(missing_ok=True)
    svc.icon_path.with_suffix(".ico").unlink(missing_ok=True)
    if not keep_profile:
        shutil.rmtree(svc.profile_dir, ignore_errors=True)
    remove_desktop_entry(svc)


# --------------------------------------------------------------------------- impostazioni globali


def _settings() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(SETTINGS_FILE, encoding="utf-8")
    if not cp.has_section("runtime"):
        cp["runtime"] = {}
    return cp


def runtime_channel() -> str:
    channel = _settings()["runtime"].get("channel", DEFAULT_CHANNEL)
    return channel if channel in RUNTIME_URLS else DEFAULT_CHANNEL


def _write_settings(cp: configparser.ConfigParser) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        cp.write(f)


def _set_runtime_channel(channel: str) -> None:
    cp = _settings()
    cp["runtime"]["channel"] = channel
    _write_settings(cp)


def move_storage(target: str) -> Path:
    """Sposta runtime, profili e cache addon in `target` (es. su un SSD)."""
    dest = Path(target).expanduser().resolve()
    if dest == STORAGE_DIR.resolve():
        raise LauncherError(f"I dati sono già in {dest}.")
    if dest.is_relative_to(STORAGE_DIR.resolve()):
        raise LauncherError("La destinazione non può stare dentro la cartella attuale.")
    running = [s.name for s in list_services() if profile_running(s.profile_dir)]
    if running:
        raise LauncherError(f"Chiudi prima: {', '.join(running)}.")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        dest.chmod(0o700)  # profili con cookie e password
    except OSError as e:
        raise LauncherError(f"Non posso usare {dest}: {e}") from e
    clash = [n for n in STORAGE_ITEMS if (dest / n).exists() and (STORAGE_DIR / n).exists()]
    if clash:
        raise LauncherError(f"In {dest} esiste già: {', '.join(clash)}.")
    for name in STORAGE_ITEMS:
        src = STORAGE_DIR / name
        if src.exists():
            print(f"Sposto {name}...", file=sys.stderr)
            shutil.move(str(src), str(dest / name))
    cp = _settings()
    if not cp.has_section("paths"):
        cp["paths"] = {}
    cp["paths"]["storage"] = "" if dest == DATA_DIR.resolve() else str(dest)
    _write_settings(cp)
    return dest


# --------------------------------------------------------------------------- runtime


def firefox_binary() -> Path:
    return RUNTIME_DIR / ("firefox.exe" if IS_WIN else "firefox")


def runtime_installed() -> bool:
    return firefox_binary().is_file()


def runtime_version() -> str:
    try:
        for line in (RUNTIME_DIR / "application.ini").read_text().splitlines():
            if line.startswith("Version="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f, length=1 << 20)
    except OSError as e:
        raise LauncherError(f"Download fallito ({url}): {e}") from e


def _download_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except OSError as e:
        raise LauncherError(f"Download fallito ({url}): {e}") from e


def _extract_runtime(archive: Path, tmp: Path) -> Path:
    """Estrae il runtime scaricato e ritorna la cartella che contiene l'eseguibile."""
    out = tmp / "out"
    if IS_WIN:
        # Installer completo di Firefox: /ExtractDir scompatta senza installare né chiedere permessi.
        subprocess.run([str(archive), f"/ExtractDir={out}"], check=True, timeout=600)
    else:
        out.mkdir()
        subprocess.run(["tar", "-xf", str(archive), "-C", str(out)], check=True)
    # Solo file: su Linux anche la cartella che contiene l'eseguibile si chiama "firefox".
    exe = next((f for f in out.rglob(firefox_binary().name) if f.is_file()), None)
    if not exe:
        raise LauncherError("Archivio del runtime Firefox non riconosciuto.")
    return exe.parent


def ensure_runtime() -> None:
    """Scarica il runtime alla prima occasione in cui serve davvero."""
    if not runtime_installed():
        install_runtime()


def install_runtime(channel: str | None = None) -> str:
    channel = channel or runtime_channel()
    if channel not in RUNTIME_URLS:
        raise LauncherError(f"Canale «{channel}» non valido (esr o release).")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as tmp:
        archive = Path(tmp) / ("firefox-setup.exe" if IS_WIN else "firefox.tar")
        print(f"Scarico il runtime Firefox ({channel})...", file=sys.stderr)
        _download(RUNTIME_URLS[channel], archive)
        try:
            folder = _extract_runtime(archive, Path(tmp))
        except (OSError, subprocess.SubprocessError) as e:
            raise LauncherError(f"Estrazione del runtime Firefox fallita: {e}") from e
        shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
        RUNTIME_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), RUNTIME_DIR)
    _set_runtime_channel(channel)
    install_policies()
    return runtime_version()


def install_policies() -> None:
    """Policy e autoconfig del runtime: valgono per tutti i profili.

    L'autoconfig (stream-launcher.cfg) importa all'avvio le password preparate dal launcher.
    """
    dist = RUNTIME_DIR / "distribution"
    dist.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS_DIR / "policies.json", dist / "policies.json")
    prefs_dir = RUNTIME_DIR / "defaults" / "pref"
    prefs_dir.mkdir(parents=True, exist_ok=True)
    _copy_if_changed(ASSETS_DIR / "autoconfig" / "autoconfig.js", prefs_dir / "autoconfig.js")
    _copy_if_changed(ASSETS_DIR / "autoconfig" / "stream-launcher.cfg", RUNTIME_DIR / "stream-launcher.cfg")


def _copy_if_changed(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    if not dst.is_file() or dst.read_bytes() != data:
        dst.write_bytes(data)


# --------------------------------------------------------------------------- profilo


def profile_running(profile: Path) -> bool:
    """Firefox su Linux crea il symlink `lock` -> "host:+PID" finché è aperto;
    su Windows tiene aperto `parent.lock` in modo esclusivo."""
    if IS_WIN:
        try:
            with open(profile / "parent.lock", "rb"):
                return False
        except FileNotFoundError:
            return False
        except PermissionError:
            return True
    try:
        target = os.readlink(profile / "lock")
    except OSError:
        return False
    m = re.search(r"\+(\d+)$", target)
    if not m:
        return False
    try:
        os.kill(int(m.group(1)), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pref(name: str, value: object) -> str:
    return f"user_pref({json.dumps(name)}, {json.dumps(value)});"


def _user_agent_prefs(svc: Service) -> list[str]:
    if not svc.user_agent:
        return []
    if svc.user_agent == "windows":
        major = runtime_version().split(".")[0] or "140"
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{major}.0) Gecko/20100101 Firefox/{major}.0"
        return [
            _pref("general.useragent.override", ua),
            _pref("general.platform.override", "Win32"),
            _pref("general.oscpu.override", "Windows NT 10.0; Win64; x64"),
            _pref("general.appversion.override", "5.0 (Windows)"),
        ]
    return [_pref("general.useragent.override", svc.user_agent)]


def _proxy_prefs(proxy: Proxy) -> list[str]:
    if not proxy.enabled:
        return []
    prefs = [
        _pref("network.proxy.type", 1),
        _pref("network.proxy.no_proxies_on", ""),
        # Se il proxy cade non si passa alla connessione diretta (niente fughe di geolocalizzazione).
        _pref("network.proxy.failover_direct", False),
        _pref("media.peerconnection.ice.proxy_only_if_behind_proxy", True),
    ]
    if proxy.type.startswith("socks"):
        prefs += [
            _pref("network.proxy.socks", proxy.host),
            _pref("network.proxy.socks_port", proxy.port),
            _pref("network.proxy.socks_version", 5 if proxy.type == "socks5" else 4),
            _pref("network.proxy.socks_remote_dns", True),
        ]
    else:
        # Il tipo "https" (proxy raggiunto in TLS) lo gestisce l'estensione; qui la base HTTP.
        prefs += [
            _pref("network.proxy.http", proxy.host),
            _pref("network.proxy.http_port", proxy.port),
            _pref("network.proxy.ssl", proxy.host),
            _pref("network.proxy.ssl_port", proxy.port),
        ]
    return prefs


def _scrub_prefs_js(profile: Path) -> None:
    prefs_js = profile / "prefs.js"
    if not prefs_js.is_file():
        return
    lines = prefs_js.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [
        ln
        for ln in lines
        if not any(ln.startswith(f'user_pref("{key}') for key in MANAGED_PREFS)
    ]
    if len(kept) != len(lines):
        prefs_js.write_text("".join(kept), encoding="utf-8")


def prepare_profile(svc: Service, log=print) -> None:
    profile = svc.profile_dir
    (profile / "chrome").mkdir(parents=True, exist_ok=True)
    _scrub_prefs_js(profile)
    generated = [
        "",
        f"// --- Generato da stream-launcher per «{svc.name}» ---",
        _pref("browser.startup.homepage", svc.url),
        *_user_agent_prefs(svc),
        *_proxy_prefs(svc.proxy),
    ]
    base = (ASSETS_DIR / "user.js").read_text(encoding="utf-8")
    (profile / "user.js").write_text(base + "\n".join(generated) + "\n", encoding="utf-8")
    shutil.copyfile(ASSETS_DIR / "chrome" / "userChrome.css", profile / "chrome" / "userChrome.css")
    sync_addons(svc, log)
    sync_passwords(svc, log)


def login_domains(svc: Service) -> list[str]:
    from .passwords import domains_for

    return svc.login_domains or domains_for(svc.url)


def all_login_domains() -> list[str]:
    """Domini di tutti i servizi, installati e del catalogo: sono gli unici login importati."""
    domains: set[str] = set()
    for svc in [*list_services(), *list_templates()]:
        domains.update(login_domains(svc))
    return sorted(domains)


def sync_passwords(svc: Service, log=print) -> int:
    """Passa al profilo i login del servizio presi dall'archivio del launcher; ritorna quanti."""
    from .passwords import logins_for, stage_import

    logins = [] if svc.passwords == "off" else logins_for(login_domains(svc))
    stage_import(svc.profile_dir, logins)  # senza login toglie anche eventuali residui
    return len(logins)


# --------------------------------------------------------------------------- addon


@dataclass
class AddonFile:
    id: str
    path: Path
    signed: bool


def _gecko_id(manifest: dict) -> str:
    for key in ("browser_specific_settings", "applications"):
        gid = manifest.get(key, {}).get("gecko", {}).get("id")
        if gid:
            return gid
    return ""


def _zip_offset(data: bytes) -> bytes:
    """Un .crx è uno zip preceduto da un'intestazione con firma Chrome."""
    if data[:4] != b"Cr24":
        return data
    version = int.from_bytes(data[4:8], "little")
    if version == 3:
        start = 12 + int.from_bytes(data[8:12], "little")
    elif version == 2:
        start = 16 + int.from_bytes(data[8:12], "little") + int.from_bytes(data[12:16], "little")
    else:
        raise LauncherError(f"Formato crx v{version} non supportato")
    return data[start:]


def _to_firefox_xpi(data: bytes, fallback_id: str) -> tuple[str, bytes, bool]:
    """Ritorna (id, contenuto xpi, firmato). Gli addon firmati restano intatti."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(_zip_offset(data)))
    except zipfile.BadZipFile as e:
        raise LauncherError("archivio dell'addon non valido") from e
    with zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise LauncherError("manifest.json mancante nell'addon")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8-sig"))
        signed = bool({"META-INF/mozilla.rsa", "META-INF/cose.sig"} & names)
        gid = _gecko_id(manifest)
        if signed:
            if not gid:
                # Senza id nel manifest l'id è il CN del certificato di firma AMO.
                rsa = zf.read("META-INF/mozilla.rsa") if "META-INF/mozilla.rsa" in names else b""
                m = re.search(rb"\{[0-9a-fA-F-]{36}\}|[\w.+-]+@[\w.-]+", rsa)
                if not m:
                    raise LauncherError("impossibile ricavare l'id dell'addon: usa la forma amo:<nome>")
                gid = m.group(0).decode()
            return gid, data, True

        # Non firmato (es. addon Chrome): adattamenti minimi per Gecko e id obbligatorio.
        gid = gid or f"{slugify(fallback_id)}@stream-launcher.local"
        manifest.setdefault("browser_specific_settings", {}).setdefault("gecko", {})["id"] = gid
        manifest.pop("applications", None)
        for key in ("key", "update_url", "minimum_chrome_version"):
            manifest.pop(key, None)
        bg = manifest.get("background")
        if isinstance(bg, dict) and "service_worker" in bg and "scripts" not in bg:
            bg["scripts"] = [bg["service_worker"]]
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in zf.infolist():
                if info.filename == "manifest.json" or info.filename.startswith("_metadata/"):
                    continue
                dst.writestr(info, zf.read(info))
            dst.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        return gid, out.getvalue(), False


def _zip_dir(folder: Path) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder).as_posix())
    return out.getvalue()


def _cached(key: str, fetch, refresh: bool) -> AddonFile:
    """Cache su disco: <key>.xpi + <key>.json con id e firma."""
    ADDON_CACHE.mkdir(parents=True, exist_ok=True)
    xpi, meta = ADDON_CACHE / f"{key}.xpi", ADDON_CACHE / f"{key}.json"
    if not refresh and xpi.is_file() and meta.is_file():
        info = json.loads(meta.read_text())
        return AddonFile(info["id"], xpi, info["signed"])
    gid, content, signed = fetch()
    xpi.write_bytes(content)
    meta.write_text(json.dumps({"id": gid, "signed": signed}))
    return AddonFile(gid, xpi, signed)


def _fetch_amo(slug: str) -> tuple[str, bytes, bool]:
    api = f"https://addons.mozilla.org/api/v5/addons/addon/{urllib.parse.quote(slug)}/"
    req = urllib.request.Request(api, headers=HTTP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            info = json.load(resp)
        file_url = info["current_version"]["file"]["url"]
    except (OSError, KeyError, ValueError) as e:
        raise LauncherError(f"addon «{slug}» non trovato su addons.mozilla.org") from e
    gid, content, signed = _to_firefox_xpi(_download_bytes(file_url), slug)
    return info.get("guid") or gid, content, signed


def _fetch_chrome(chrome_id: str) -> tuple[str, bytes, bool]:
    url = (
        "https://clients2.google.com/service/update2/crx?response=redirect"
        "&prodversion=131.0&acceptformat=crx2,crx3"
        f"&x=id%3D{chrome_id}%26installsource%3Dondemand%26uc"
    )
    return _to_firefox_xpi(_download_bytes(url), chrome_id)


def resolve_addon(spec: str, refresh: bool = False) -> AddonFile:
    """amo:<nome>, chrome:<id>, oppure percorso a .xpi / .crx / cartella non compressa."""
    spec = spec.strip()
    if spec.startswith("amo:"):
        slug = spec[4:].strip()
        if not re.match(r"^[\w.-]+$", slug):
            raise LauncherError(f"nome AMO «{slug}» non valido")
        return _cached(f"amo-{slug}", lambda: _fetch_amo(slug), refresh)
    if spec.startswith("chrome:"):
        cid = spec[7:].strip()
        if not CHROME_ID_RE.match(cid):
            raise LauncherError(f"id Chrome «{cid}» non valido (32 lettere a-p)")
        return _cached(f"chrome-{cid}", lambda: _fetch_chrome(cid), refresh)

    path = Path(spec).expanduser()
    if not path.exists():
        raise LauncherError(f"file «{path}» inesistente")
    data = _zip_dir(path) if path.is_dir() else path.read_bytes()
    gid, content, signed = _to_firefox_xpi(data, path.stem)
    # I file locali si rileggono sempre: chi li modifica vuole vedere le modifiche.
    key = "local-" + slugify(str(path))
    ADDON_CACHE.mkdir(parents=True, exist_ok=True)
    out = ADDON_CACHE / f"{key}.xpi"
    out.write_bytes(content)
    return AddonFile(gid, out, signed)


def _proxy_extension(proxy: Proxy) -> bytes:
    src = ASSETS_DIR / "proxy-extension"
    cfg = {
        "type": proxy.type,
        "host": proxy.host,
        "port": proxy.port,
        "username": proxy.username,
        "password": proxy.password,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("manifest.json", "background.js"):
            zf.write(src / name, name)
        zf.writestr("config.js", f"const PROXY_CONFIG = {json.dumps(cfg)};\n")
    return out.getvalue()


def _write_if_changed(path: Path, content: bytes) -> None:
    if path.is_file() and path.read_bytes() == content:
        return
    path.write_bytes(content)


def sync_addons(svc: Service, log=print, refresh: bool = False) -> list[str]:
    """Allinea profile/extensions agli addon del servizio; ritorna gli avvisi."""
    ext_dir = svc.profile_dir / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    state_file = svc.profile_dir / ADDON_STATE_FILE
    try:
        previous = set(json.loads(state_file.read_text()))
    except (OSError, ValueError):
        previous = set()

    esr = runtime_channel() == "esr"
    warnings: list[str] = []
    current: set[str] = set()
    for spec in svc.addons:
        try:
            addon = resolve_addon(spec, refresh=refresh)
        except LauncherError as e:
            warnings.append(f"Addon «{spec}» saltato: {e}")
            continue
        if not addon.signed and not esr:
            warnings.append(f"Addon «{spec}» non firmato: serve il runtime ESR, Firefox lo ignorerà.")
        _write_if_changed(ext_dir / f"{addon.id}.xpi", addon.path.read_bytes())
        current.add(addon.id)

    if svc.proxy.needs_extension:
        if not esr:
            warnings.append(
                "Credenziali del proxy / proxy HTTPS: servono il runtime ESR (stream-launcher runtime install --channel esr)."
            )
        _write_if_changed(ext_dir / f"{PROXY_ADDON_ID}.xpi", _proxy_extension(svc.proxy))
        current.add(PROXY_ADDON_ID)

    # Rimuove solo gli addon messi dal launcher; quelli installati a mano restano.
    for old in previous - current:
        (ext_dir / f"{old}.xpi").unlink(missing_ok=True)
    state_file.write_text(json.dumps(sorted(current)))
    for w in warnings:
        log(w)
    return warnings


# --------------------------------------------------------------------------- menu applicazioni


def fetch_icon(svc: Service, dest: Path | None = None) -> None:
    """Scarica l'icona (marchi registrati: non stanno nel repo)."""
    dest = dest or svc.icon_path
    host = urllib.parse.urlparse(svc.url).hostname or ""
    url = svc.icon_url or f"https://www.google.com/s2/favicons?domain={host}&sz=256"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(url, dest)
    except LauncherError:
        dest.unlink(missing_ok=True)


def refresh_desktop_entry(svc: Service, icon: bool = True) -> None:
    if icon and not svc.icon_path.is_file():
        fetch_icon(svc)
    if IS_WIN:
        _sync_shortcuts()  # il nome del .lnk è il nome del servizio: rinomina = ricrea
        return
    write_desktop_entry(svc)
    update_desktop_database()


def remove_desktop_entry(svc: Service) -> None:
    if IS_WIN:
        _sync_shortcuts()
        return
    svc.desktop_path.unlink(missing_ok=True)
    update_desktop_database()


def sync_desktop_entries() -> None:
    """Ricrea le voci di menu e toglie quelle di servizi non più esistenti."""
    services = list_services()
    for svc in services:
        if not svc.icon_path.is_file():
            fetch_icon(svc)
    if IS_WIN:
        _sync_shortcuts(services)
        return
    ids = {s.app_id for s in services}
    if APPS_DIR.is_dir():
        for path in APPS_DIR.glob(f"{APP}-*.desktop"):
            if path.stem not in ids:
                path.unlink()
    for svc in services:
        write_desktop_entry(svc)
    write_settings_entry()
    update_desktop_database()


# --- Linux: file .desktop


def _desktop_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", " ")


def install_theme_icon() -> str:
    """Icona dell'app nel tema hicolor, richiamata per nome.

    Con un percorso fisso GNOME tiene in cache l'immagine vecchia anche dopo un
    aggiornamento; un'icona del tema invece viene ricaricata quando cambia.
    """
    target = XDG_DATA / "icons/hicolor/scalable/apps" / f"{APP}.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (ASSETS_DIR / "icon.svg").read_bytes()
    if not target.is_file() or target.read_bytes() != data:
        target.write_bytes(data)
        theme = XDG_DATA / "icons/hicolor"
        theme.touch()  # segnala il cambio a chi sorveglia il tema
        try:
            subprocess.run(["gtk-update-icon-cache", "-qtf", str(theme)], capture_output=True, check=False)
        except FileNotFoundError:
            pass
    return APP


def write_desktop_entry(svc: Service) -> None:
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    icon = svc.icon_path if svc.icon_path.is_file() else install_theme_icon()
    cli = CLI_PATH
    svc.desktop_path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_desktop_escape(svc.name)}\n"
        f"Comment={_desktop_escape(svc.name)} su motore Firefox ({svc.proxy.describe()})\n"
        f"Exec={cli} run {svc.id}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;Player;\n"
        f"StartupWMClass={svc.app_id}\n"
        "Actions=settings;\n"
        "\n"
        "[Desktop Action settings]\n"
        "Name=Impostazioni\n"
        f"Exec={cli} settings {svc.id}\n",
        encoding="utf-8",
    )


def write_settings_entry() -> None:
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    (APPS_DIR / f"{APP}.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Stream Launcher\n"
        "Comment=Gestisci i servizi di streaming: proxy, addon, duplicati\n"
        f"Exec={CLI_PATH} settings\n"
        f"Icon={install_theme_icon()}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;Settings;\n"
        f"StartupWMClass={APP}\n",
        encoding="utf-8",
    )


def update_desktop_database() -> None:
    try:
        subprocess.run(["update-desktop-database", str(APPS_DIR)], capture_output=True, check=False)
    except FileNotFoundError:
        pass


# --- Windows: collegamenti .lnk nel menu Start

def _png_to_ico(svc: Service) -> str:
    """Windows vuole icone .ico per i collegamenti: un .ico può contenere un PNG così com'è."""
    try:
        data = svc.icon_path.read_bytes()
    except OSError:
        return ""
    ico = svc.icon_path.with_suffix(".ico")
    if data[:4] == b"\0\0\1\0":
        ico.write_bytes(data)
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = (int.from_bytes(data[i:i + 4], "big") for i in (16, 20))
        header = struct.pack("<HHHBBBBHHII", 0, 1, 1, w % 256, h % 256, 0, 0, 1, 32, len(data), 22)
        ico.write_bytes(header + data)
    else:
        return ""
    return str(ico)


def _sync_shortcuts(services: list[Service] | None = None) -> None:
    """Ricrea le voci del menu Start. Ogni servizio porta l'AppUserModelID delle finestre
    Firefox del suo profilo: la barra delle applicazioni ne mostra icona e nome."""
    from .winshell import create_shortcut, firefox_aumid

    services = list_services() if services is None else services
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    for old in APPS_DIR.glob("*.lnk"):
        old.unlink(missing_ok=True)
    target, *base_args = self_command()
    prefix = subprocess.list2cmdline(base_args)
    entries = [("Stream Launcher", "settings", "Gestisci i servizi di streaming: proxy, addon, duplicati", "", "")]
    used = {"stream launcher"}
    for svc in services:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", svc.name).strip(" .") or svc.id
        if name.lower() in used:
            name = f"{name} ({svc.id})"
        used.add(name.lower())
        desc = f"{svc.name} su motore Firefox ({svc.proxy.describe()})"
        entries.append((name, f"run {svc.id}", desc, _png_to_ico(svc), firefox_aumid(str(svc.profile_dir))))
    for name, args, desc, icon, aumid in entries:
        try:
            create_shortcut(str(APPS_DIR / f"{name}.lnk"), target, f"{prefix} {args}".strip(),
                            str(Path(target).parent), desc, icon, aumid)
        except OSError as e:
            print(f"Collegamento «{name}» non creato: {e}", file=sys.stderr)


# --------------------------------------------------------------------------- avvio


def launch(svc: Service) -> None:
    """Avvia Firefox sul profilo del servizio (su Linux sostituisce il processo corrente)."""
    ensure_runtime()
    env = os.environ.copy()
    if IS_WIN:
        args = [str(firefox_binary()), "--profile", str(svc.profile_dir)]
    else:
        if env.get("XDG_SESSION_TYPE") == "wayland":
            env["MOZ_ENABLE_WAYLAND"] = "1"
        args = [str(firefox_binary()), "--name", svc.app_id, "--class", svc.app_id, "--profile", str(svc.profile_dir)]
    if not profile_running(svc.profile_dir):
        install_policies()  # aggiorna l'autoconfig anche sui runtime installati prima
        prepare_profile(svc, log=lambda m: print(m, file=sys.stderr))
        args.append(svc.url)
    # Già aperto: Firefox inoltra la richiesta all'istanza esistente (nuova finestra).
    if IS_WIN:
        spawn_detached(args, env=env)
    else:
        os.execve(args[0], args, env)


def launch_detached(service_id: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "ab") as log:
        spawn_detached(self_command("run", service_id), stdout=log, stderr=log)
