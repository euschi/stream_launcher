"""Icona nella tray (StatusNotifierItem via AyatanaAppIndicator, GTK3).

Gira in un processo separato dalla finestra impostazioni (GTK4): le due
versioni di GTK non possono convivere nello stesso processo.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import core  # noqa: E402

INDICATOR_ID = "stream-launcher"


class Tray:
    def __init__(self) -> None:
        self.indicator = AppIndicator.Indicator.new(
            INDICATOR_ID,
            str(core.ASSETS_DIR / "icon.svg"),
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title("Stream Launcher")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._rebuild_pending = False
        self._monitors = []
        self._watch()
        self.rebuild()

    def _watch(self) -> None:
        """Ricostruisce il menu quando si aggiunge, duplica o rinomina un servizio."""
        core.SERVICES_DIR.mkdir(parents=True, exist_ok=True)
        for path in (core.SERVICES_DIR, core.ICONS_DIR):
            path.mkdir(parents=True, exist_ok=True)
            mon = Gio.File.new_for_path(str(path)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            mon.connect("changed", lambda *_: self._schedule_rebuild())
            self._monitors.append(mon)

    def _schedule_rebuild(self) -> None:
        # Più eventi ravvicinati (scrittura .tmp + rename) diventano una sola ricostruzione.
        if not self._rebuild_pending:
            self._rebuild_pending = True
            GLib.timeout_add(300, self.rebuild)

    @staticmethod
    def _item(label: str, callback, icon_path=None) -> Gtk.MenuItem:
        if icon_path and icon_path.is_file():
            item = Gtk.ImageMenuItem(label=label)
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 16, 16)
                item.set_image(Gtk.Image.new_from_pixbuf(pix))
                item.set_always_show_image(True)
            except GLib.Error:
                pass
        else:
            item = Gtk.MenuItem(label=label)
        item.set_use_underline(False)
        item.connect("activate", lambda _i: callback())
        return item

    def rebuild(self) -> bool:
        self._rebuild_pending = False
        menu = Gtk.Menu()
        services = core.list_services()
        if not services:
            empty = Gtk.MenuItem(label="Nessun servizio")
            empty.set_sensitive(False)
            menu.append(empty)
        for svc in services:
            label = svc.name if not svc.proxy.enabled else f"{svc.name}  ·  proxy"
            menu.append(self._item(label, lambda sid=svc.id: self.launch(sid), svc.icon_path))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._item("Impostazioni…", self.open_settings))
        menu.append(self._item("Esci dalla tray", Gtk.main_quit))
        menu.show_all()
        self.indicator.set_menu(menu)
        self.menu = menu  # riferimento vivo: altrimenti il GC lo distrugge
        return False

    @staticmethod
    def launch(service_id: str) -> None:
        if not core.runtime_installed():
            Tray.open_settings()
            return
        core.launch_detached(service_id)

    @staticmethod
    def open_settings() -> None:
        subprocess.Popen(
            [sys.executable, str(core.CLI_PATH), "settings"],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _single_instance():
    """Una sola icona per sessione: il lock resta finché il processo vive."""
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(core.DATA_DIR / "tray.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def main() -> int:
    lock = _single_instance()
    if lock is None:
        print("La tray di Stream Launcher è già attiva.", file=sys.stderr)
        return 0
    Tray()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, Gtk.main_quit)  # SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, Gtk.main_quit)  # SIGINT
    Gtk.main()
    return 0
