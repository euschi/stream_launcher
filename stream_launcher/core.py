"""Logica condivisa: servizi, runtime Firefox, profili, addon, proxy e voci di menu.

Ogni servizio è un file INI in ~/.config/stream-launcher/services/<id>.ini e ha
un profilo Firefox tutto suo in ~/.local/share/stream-launcher/profiles/<id>.
Il runtime Firefox portatile è unico e condiviso da tutti i servizi.
"""
from __future__ import annotations

import configparser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

APP = "stream-launcher"


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


XDG_DATA = _xdg("XDG_DATA_HOME", ".local/share")
DATA_DIR = XDG_DATA / APP
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / APP
SERVICES_DIR = CONFIG_DIR / "services"
SETTINGS_FILE = CONFIG_DIR / "settings.ini"
APPS_DIR = XDG_DATA / "applications"
RUNTIME_DIR = DATA_DIR / "firefox"
PROFILES_DIR = DATA_DIR / "profiles"
ICONS_DIR = DATA_DIR / "icons"
ADDON_CACHE = DATA_DIR / "addons-cache"
LOG_FILE = DATA_DIR / "launch.log"

# Radice dell'applicazione (repo o copia installata): contiene config/, templates/, bin/.
APP_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_ROOT / "config"
TEMPLATES_DIR = APP_ROOT / "templates"
CLI_PATH = APP_ROOT / "bin" / APP

SETTINGS_APP_ID = "dev.streamlauncher.Settings"

RUNTIME_URLS = {
    # ESR permette addon non firmati (addon Chrome convertiti, estensione proxy).
    "esr": "https://download.mozilla.org/?product=firefox-esr-latest-ssl&os=linux64&lang=it",
    "release": "https://download.mozilla.org/?product=firefox-latest-ssl&os=linux64&lang=it",
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
    save_service(svc)
    refresh_desktop_entry(svc)
    return svc


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
            ignore=shutil.ignore_patterns("lock", ".parentlock", "cache2", "startupCache"),
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
    svc.desktop_path.unlink(missing_ok=True)
    svc.icon_path.unlink(missing_ok=True)
    if not keep_profile:
        shutil.rmtree(svc.profile_dir, ignore_errors=True)
    update_desktop_database()


def seed_default_services() -> list[Service]:
    """Crea un servizio per ogni modello mai proposto prima.

    I modelli già proposti sono registrati in settings.ini: così un aggiornamento
    aggiunge i servizi nuovi, ma quelli che l'utente ha eliminato non ricompaiono.
    """
    cp = _settings()
    if not cp.has_section("templates"):
        cp["templates"] = {}
    seeded = {t for t in cp["templates"].get("seeded", "").split(",") if t}
    added = []
    for t in list_templates():
        if t.id in seeded:
            continue
        seeded.add(t.id)
        if (SERVICES_DIR / f"{t.id}.ini").exists():
            continue
        added.append(add_from_template(t.id, new_id=t.id))
    cp["templates"]["seeded"] = ",".join(sorted(seeded))
    _write_settings(cp)
    return added


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


# --------------------------------------------------------------------------- runtime


def firefox_binary() -> Path:
    return RUNTIME_DIR / "firefox"


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


def install_runtime(channel: str | None = None) -> str:
    channel = channel or runtime_channel()
    if channel not in RUNTIME_URLS:
        raise LauncherError(f"Canale «{channel}» non valido (esr o release).")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as tmp:
        archive = Path(tmp) / "firefox.tar"
        print(f"Scarico il runtime Firefox ({channel})...", file=sys.stderr)
        _download(RUNTIME_URLS[channel], archive)
        subprocess.run(["tar", "-xf", str(archive), "-C", tmp], check=True)
        shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
        shutil.move(str(Path(tmp) / "firefox"), RUNTIME_DIR)
    _set_runtime_channel(channel)
    install_policies()
    return runtime_version()


def install_policies() -> None:
    """Policy del runtime (telemetria off, DRM sempre attivo): valgono per tutti i profili."""
    dist = RUNTIME_DIR / "distribution"
    dist.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS_DIR / "policies.json", dist / "policies.json")


# --------------------------------------------------------------------------- profilo


def profile_running(profile: Path) -> bool:
    """Firefox su Linux crea il symlink `lock` -> "host:+PID" finché è aperto."""
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
    with tempfile.NamedTemporaryFile() as tmp:
        _download(file_url, Path(tmp.name))
        data = Path(tmp.name).read_bytes()
    gid, content, signed = _to_firefox_xpi(data, slug)
    return info.get("guid") or gid, content, signed


def _fetch_chrome(chrome_id: str) -> tuple[str, bytes, bool]:
    url = (
        "https://clients2.google.com/service/update2/crx?response=redirect"
        "&prodversion=131.0&acceptformat=crx2,crx3"
        f"&x=id%3D{chrome_id}%26installsource%3Dondemand%26uc"
    )
    with tempfile.NamedTemporaryFile() as tmp:
        _download(url, Path(tmp.name))
        data = Path(tmp.name).read_bytes()
    return _to_firefox_xpi(data, chrome_id)


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


def fetch_icon(svc: Service) -> None:
    """Scarica l'icona (marchi registrati: non stanno nel repo)."""
    host = urllib.parse.urlparse(svc.url).hostname or ""
    url = svc.icon_url or f"https://www.google.com/s2/favicons?domain={host}&sz=256"
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _download(url, svc.icon_path)
    except LauncherError:
        svc.icon_path.unlink(missing_ok=True)


def _desktop_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", " ")


def write_desktop_entry(svc: Service) -> None:
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    icon = svc.icon_path if svc.icon_path.is_file() else ASSETS_DIR / "icon.svg"
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
        f"Icon={ASSETS_DIR / 'icon.svg'}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;Settings;\n"
        f"StartupWMClass={SETTINGS_APP_ID}\n",
        encoding="utf-8",
    )


def update_desktop_database() -> None:
    try:
        subprocess.run(["update-desktop-database", str(APPS_DIR)], capture_output=True, check=False)
    except FileNotFoundError:
        pass


def refresh_desktop_entry(svc: Service, icon: bool = True) -> None:
    if icon and not svc.icon_path.is_file():
        fetch_icon(svc)
    write_desktop_entry(svc)
    update_desktop_database()


def sync_desktop_entries() -> None:
    """Ricrea le voci di menu e toglie quelle di servizi non più esistenti."""
    services = list_services()
    ids = {s.app_id for s in services}
    if APPS_DIR.is_dir():
        for path in APPS_DIR.glob(f"{APP}-*.desktop"):
            if path.stem not in ids:
                path.unlink()
    for svc in services:
        if not svc.icon_path.is_file():
            fetch_icon(svc)
        write_desktop_entry(svc)
    write_settings_entry()
    update_desktop_database()


# --------------------------------------------------------------------------- avvio


def launch(svc: Service) -> None:
    """Sostituisce il processo corrente con Firefox sul profilo del servizio."""
    if not runtime_installed():
        raise LauncherError("Runtime Firefox non installato: esegui «stream-launcher runtime install».")
    env = os.environ.copy()
    if env.get("XDG_SESSION_TYPE") == "wayland":
        env["MOZ_ENABLE_WAYLAND"] = "1"
    args = [str(firefox_binary()), "--name", svc.app_id, "--class", svc.app_id, "--profile", str(svc.profile_dir)]
    if profile_running(svc.profile_dir):
        # Già aperto: Firefox inoltra la richiesta all'istanza esistente (nuova finestra).
        os.execve(args[0], args, env)
    prepare_profile(svc, log=lambda m: print(m, file=sys.stderr))
    os.execve(args[0], [*args, svc.url], env)


def launch_detached(service_id: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "ab") as log:
        subprocess.Popen(
            [sys.executable, str(CLI_PATH), "run", service_id],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
