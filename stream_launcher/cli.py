"""Interfaccia a riga di comando: `stream-launcher <comando>`."""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import core
from .core import LauncherError


def cmd_list(_args) -> None:
    services = core.list_services()
    if not services:
        print("Nessun servizio. Aggiungine uno con: stream-launcher add <modello>")
        return
    w_id = max(len(s.id) for s in services) + 2
    w_name = max(len(s.name) for s in services) + 2
    for svc in services:
        print(f"{svc.id:<{w_id}}{svc.name:<{w_name}}{svc.proxy.describe()}")


def cmd_templates(_args) -> None:
    for t in core.list_templates():
        print(f"{t.id:<22} {t.name:<24} {t.url}")


def cmd_run(args) -> None:
    core.launch(core.load_service(args.id))


def _gui(mode: str, service_id: str | None = None) -> None:
    try:
        from . import app  # Qt si carica solo quando serve
    except ImportError as e:
        raise LauncherError(f"Serve PySide6 per finestra e tray (Arch: sudo pacman -S pyside6): {e}") from e
    sys.exit(app.main(mode, service_id))


def cmd_settings(args) -> None:
    _gui("settings", args.id)


def cmd_tray(_args) -> None:
    _gui("tray")


def cmd_add(args) -> None:
    svc = core.add_from_template(args.template, name=args.name, new_id=args.id)
    print(f"Installato «{svc.name}» (id {svc.id}).")


def cmd_duplicate(args) -> None:
    svc = core.duplicate_service(args.source, args.name, new_id=args.id, copy_profile=args.copy_profile)
    print(f"Creato «{svc.name}» (id {svc.id}) da «{args.source}».")


def cmd_rename(args) -> None:
    svc = core.load_service(args.id)
    svc.name = args.name
    core.save_service(svc)
    core.refresh_desktop_entry(svc, icon=False)
    print(f"Rinominato in «{svc.name}».")


def cmd_remove(args) -> None:
    core.remove_service(args.id, keep_profile=args.keep_profile)
    print(f"Rimosso «{args.id}».")


def cmd_proxy(args) -> None:
    svc = core.load_service(args.id)
    if args.off:
        svc.proxy = core.Proxy()
    else:
        if not (args.type and args.host and args.port):
            raise LauncherError("Servono --type, --host e --port (oppure --off).")
        password = args.password
        if args.user and password is None:
            password = getpass.getpass(f"Password per {args.user}@{args.host}: ")
        svc.proxy = core.Proxy(args.type, args.host, args.port, args.user or "", password or "")
    core.save_service(svc)
    core.refresh_desktop_entry(svc, icon=False)
    print(f"{svc.name}: {svc.proxy.describe()}. Effettivo dal prossimo avvio.")


def cmd_proxy_test(args) -> None:
    from .proxytest import ProxyTestError, test_proxy

    svc = core.load_service(args.id)
    label = svc.proxy.describe() if svc.proxy.enabled else "connessione diretta (nessun proxy)"
    print(f"{svc.name}: provo {label}...")
    try:
        result = test_proxy(svc.proxy, svc.url)
    except ProxyTestError as e:
        raise LauncherError(f"proxy non funzionante: {e}") from e
    print(result.summary())


def cmd_passwords(args) -> None:
    from .passwords import PasswordError, clear_store, default_browser, import_logins, installed_browsers, load_store

    if args.action == "import":
        source = default_browser() if not args.browser else next(
            (b for b in installed_browsers() if b.desktop_id == args.browser), None)
        if not source:
            found = ", ".join(b.desktop_id for b in installed_browsers()) or "nessuno"
            raise LauncherError(f"Browser non trovato. Installati: {found} (scegli con --browser).")
        try:
            store = import_logins(source, core.all_login_domains())
        except PasswordError as e:
            raise LauncherError(str(e)) from e
        print(f"Importati {len(store['logins'])} login da {source.name}.")
    elif args.action == "clear":
        clear_store()
        print("Archivio delle password svuotato.")
    else:
        store = load_store()
        if not store:
            print("Nessuna password importata: stream-launcher passwords import [--browser ...]")
            return
        # Solo siti e utenti, mai le password.
        print(f"{len(store['logins'])} login da {store['browser']} (aggiornati {store['updated']}):")
        for login in sorted(store["logins"], key=lambda x: (x["origin"], x["username"])):
            print(f"  {login['origin']}  {login['username']}")


def cmd_useragent(args) -> None:
    svc = core.load_service(args.id)
    svc.user_agent = "" if args.value == "default" else args.value
    core.save_service(svc)
    print(f"{svc.name}: user agent «{svc.user_agent or 'predefinito'}».")


def cmd_addon(args) -> None:
    svc = core.load_service(args.id)
    if args.action == "list":
        for spec in svc.addons:
            print(spec)
        return
    if args.action == "update":
        warnings = core.sync_addons(svc, refresh=True)
        print("Addon aggiornati." if not warnings else "Aggiornati, con avvisi.")
        return
    if not args.spec:
        raise LauncherError("Indica l'addon: amo:<nome>, chrome:<id> o un percorso.")
    if args.action == "add":
        core.resolve_addon(args.spec)  # verifica subito che esista e sia valido
        if args.spec not in svc.addons:
            svc.addons.append(args.spec)
    elif args.spec in svc.addons:
        svc.addons.remove(args.spec)
    else:
        raise LauncherError(f"«{args.spec}» non è tra gli addon di {svc.name}.")
    core.save_service(svc)
    print(f"{svc.name}: {len(svc.addons)} addon. Effettivo dal prossimo avvio.")


def cmd_runtime(args) -> None:
    if args.action == "install":
        version = core.install_runtime(args.channel)
        print(f"Runtime Firefox {version} ({core.runtime_channel()}) installato.")
    else:
        state = core.runtime_version() or "non installato"
        print(f"Runtime: {state} — canale {core.runtime_channel()} — {core.RUNTIME_DIR}")


def cmd_storage(args) -> None:
    if args.action == "move":
        if not args.dir:
            raise LauncherError("Indica la cartella di destinazione.")
        dest = core.move_storage(args.dir)
        print(f"Runtime e profili ora in {dest}.")
    else:
        print(f"Runtime, profili e cache addon: {core.STORAGE_DIR}")


def cmd_init(args) -> None:
    """Usato dagli installer: voci di menu. I servizi si scelgono dal catalogo e
    il runtime Firefox si scarica col primo servizio installato."""
    if args.update_runtime:
        version = core.install_runtime(args.channel)
        print(f"Runtime Firefox {version} ({core.runtime_channel()}) installato.")
    elif core.runtime_installed():
        core.install_policies()
    core.sync_desktop_entries()
    core.set_autostart(core.get_pref("autostart", "on") == "on")  # rispetta la scelta fatta nelle impostazioni


def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    p = argparse.ArgumentParser(prog="stream-launcher", description="Servizi di streaming su runtime Firefox portatile.")
    p.add_argument("--version", action="version", version=f"stream-launcher {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="elenca i servizi").set_defaults(func=cmd_list)
    sub.add_parser("templates", help="elenca i modelli disponibili").set_defaults(func=cmd_templates)

    s = sub.add_parser("run", help="avvia un servizio")
    s.add_argument("id")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("settings", help="apre la finestra delle impostazioni")
    s.add_argument("id", nargs="?")
    s.set_defaults(func=cmd_settings)

    sub.add_parser("tray", help="icona nella tray con i servizi").set_defaults(func=cmd_tray)

    s = sub.add_parser("add", help="installa un servizio dal catalogo (vedi templates)")
    s.add_argument("template")
    s.add_argument("--name")
    s.add_argument("--id")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("duplicate", help="duplica un servizio con un nuovo nome")
    s.add_argument("source")
    s.add_argument("--name", required=True)
    s.add_argument("--id", help="predefinito: ricavato dal nome")
    s.add_argument("--copy-profile", action="store_true", help="copia anche login e cookie")
    s.set_defaults(func=cmd_duplicate)

    s = sub.add_parser("rename", help="cambia il nome mostrato")
    s.add_argument("id")
    s.add_argument("name")
    s.set_defaults(func=cmd_rename)

    s = sub.add_parser("remove", help="elimina un servizio")
    s.add_argument("id")
    s.add_argument("--keep-profile", action="store_true")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("proxy", help="imposta o toglie il proxy di un servizio")
    s.add_argument("id")
    s.add_argument("--off", action="store_true")
    s.add_argument("--type", choices=[t for t in core.PROXY_TYPES if t != "none"])
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--user")
    s.add_argument("--password", help="se omessa con --user viene chiesta")
    s.set_defaults(func=cmd_proxy)

    s = sub.add_parser("proxy-test", help="prova il proxy: IP e paese di uscita, raggiungibilità del servizio")
    s.add_argument("id")
    s.set_defaults(func=cmd_proxy_test)

    s = sub.add_parser("passwords", help="password dei servizi: import da un browser, list o clear")
    s.add_argument("action", choices=["import", "list", "clear"])
    s.add_argument("--browser", help="google-chrome, microsoft-edge, chromium o brave-browser (predefinito: quello di sistema)")
    s.set_defaults(func=cmd_passwords)

    s = sub.add_parser("useragent", help="user agent: default, windows o stringa personalizzata")
    s.add_argument("id")
    s.add_argument("value")
    s.set_defaults(func=cmd_useragent)

    s = sub.add_parser("addon", help="gestisce gli addon (amo:<nome>, chrome:<id>, percorso)")
    s.add_argument("id")
    s.add_argument("action", choices=["list", "add", "remove", "update"])
    s.add_argument("spec", nargs="?")
    s.set_defaults(func=cmd_addon)

    s = sub.add_parser("runtime", help="runtime Firefox condiviso")
    s.add_argument("action", choices=["install", "info"])
    s.add_argument("--channel", choices=sorted(core.RUNTIME_URLS))
    s.set_defaults(func=cmd_runtime)

    s = sub.add_parser("storage", help="dove stanno runtime e profili (spostali su un SSD)")
    s.add_argument("action", choices=["info", "move"])
    s.add_argument("dir", nargs="?")
    s.set_defaults(func=cmd_storage)

    s = sub.add_parser("init", help="prima installazione (usato da install.sh)")
    s.add_argument("--update-runtime", action="store_true")
    s.add_argument("--channel", choices=sorted(core.RUNTIME_URLS))
    s.set_defaults(func=cmd_init)
    return p


def _windows_console() -> None:
    """L'eseguibile Windows non ha console: si aggancia a quella di chi lo lancia
    (prompt, PowerShell), altrimenti scrive nel log."""
    if sys.platform != "win32":
        return
    if sys.stdout is not None:
        # Console o pipe in codifica Windows (cp1252): un carattere fuori tabella non deve far fallire.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        return
    import ctypes

    if ctypes.windll.kernel32.AttachConsole(-1):
        sys.stdout = sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")
    else:
        core.DATA_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(core.LOG_FILE, "a", encoding="utf-8", errors="replace")
        sys.stdin = open(os.devnull, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _windows_console()
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv or ["settings"])  # senza comando: finestra impostazioni
    try:
        args.func(args)
    except LauncherError as e:
        print(f"Errore: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
