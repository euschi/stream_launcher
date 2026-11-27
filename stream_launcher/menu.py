"""Menu interattivo nel terminale: `stream-launcher menu`.

Elenca i servizi installati con un numero: si sceglie scrivendo il numero o parte
del nome e il servizio parte in background, così il terminale resta libero.
Dallo stesso menu si installano i servizi del catalogo. Solo input()/print(),
quindi funziona uguale su Linux e Windows senza dipendenze.
"""
from __future__ import annotations

from typing import Callable

from . import core
from .core import LauncherError, Service

Ask = Callable[[str], str]


def pick(items: list[Service], answer: str) -> Service | None:
    """Voce scelta dal numero (1…n) o da una parte univoca del nome o dell'id."""
    answer = answer.strip().lower()
    if not answer:
        return None
    if answer.isdigit():
        n = int(answer)
        return items[n - 1] if 1 <= n <= len(items) else None
    exact = [s for s in items if answer in (s.id.lower(), s.name.lower())]
    if exact:
        return exact[0]
    matches = [s for s in items if answer in s.name.lower() or answer in s.id.lower()]
    return matches[0] if len(matches) == 1 else None


def _print_list(items: list[Service], details: Callable[[Service], str]) -> None:
    width = max(len(s.name) for s in items) + 2
    digits = len(str(len(items)))
    for i, svc in enumerate(items, 1):
        print(f"  {i:>{digits}}  {svc.name:<{width}}{details(svc)}".rstrip())


def _catalog(ask: Ask) -> None:
    installed = {s.id for s in core.list_services()}
    catalog = [t for t in core.list_templates() if t.id not in installed]
    if not catalog:
        print("\nTutti i servizi del catalogo sono già installati.")
        return
    while True:
        print("\nCatalogo: scegli il servizio da installare (invio per tornare indietro)\n")
        _print_list(catalog, lambda t: t.url)
        answer = ask("\nInstalla: ").strip()
        if not answer:
            return
        template = pick(catalog, answer)
        if not template:
            print(f"Nessun servizio corrisponde a «{answer}».")
            continue
        if not core.runtime_installed():
            print("Primo servizio: scarico il motore Firefox, può volerci qualche minuto...")
        svc = core.add_from_template(template.id)
        print(f"Installato «{svc.name}».")
        return


def run(ask: Ask = input, launch: Callable[[str], None] = core.launch_detached) -> None:
    from . import __version__

    while True:
        services = core.list_services()
        print(f"\nStream Launcher {__version__}\n")
        if services:
            _print_list(services, lambda s: s.proxy.describe() if s.proxy.enabled else "")
        else:
            print("  Nessun servizio installato.")
        print("\n  a  installa dal catalogo\n  q  esci")
        try:
            answer = ask("\nAvvia (numero o nome): ").strip()
        except EOFError:
            print()
            return
        key = answer.lower()
        if key in ("q", "esci", "exit", "quit"):
            return
        if not key:
            continue
        if key == "a":
            try:
                _catalog(ask)
            except LauncherError as e:
                print(f"Errore: {e}")
            except EOFError:
                print()
                return
            continue
        svc = pick(services, answer)
        if not svc:
            print(f"Nessun servizio corrisponde a «{answer}».")
            continue
        launch(svc.id)
        print(f"Avvio «{svc.name}»...")
        return
