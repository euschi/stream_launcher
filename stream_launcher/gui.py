"""Finestra «Stream Launcher»: elenco servizi e impostazioni (GTK4 + libadwaita)."""
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import core  # noqa: E402
from .core import LauncherError  # noqa: E402

PROXY_CHOICES = [
    ("none", "Nessuno"),
    ("http", "HTTP"),
    ("https", "HTTPS (proxy in TLS)"),
    ("socks5", "SOCKS5"),
    ("socks4", "SOCKS4"),
]
UA_CHOICES = [("", "Predefinito (Linux)"), ("windows", "Firefox su Windows"), ("custom", "Personalizzato")]
ADDON_HINT = (
    "amo:NOME da addons.mozilla.org, chrome:ID dal Chrome Web Store, "
    "oppure un file .xpi / .crx o una cartella. Gli addon Chrome e non firmati "
    "funzionano solo con il runtime ESR."
)


def _combo(title: str, choices: list[tuple[str, str]], current: str) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _, label in choices]))
    keys = [k for k, _ in choices]
    row.set_selected(keys.index(current) if current in keys else len(keys) - 1)
    return row


def _selected_key(row: Adw.ComboRow, choices: list[tuple[str, str]]) -> str:
    return choices[row.get_selected()][0]


def _icon_widget(svc: core.Service) -> Gtk.Widget:
    if svc.icon_path.is_file():
        img = Gtk.Image.new_from_file(str(svc.icon_path))
    else:
        img = Gtk.Image.new_from_icon_name("video-display-symbolic")
    img.set_pixel_size(32)
    return img


class ServiceListPage(Adw.NavigationPage):
    def __init__(self, win: "Window"):
        super().__init__(title="Stream Launcher", tag="list")
        self.win = win

        header = Adw.HeaderBar()
        add_btn = Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text="Aggiungi servizio")
        menu = Gio.Menu()
        for t in core.list_templates():
            item = Gio.MenuItem.new(t.name, None)
            item.set_action_and_target_value("win.add-template", GLib.Variant("s", t.id))
            menu.append_item(item)
        add_btn.set_menu_model(menu)
        header.pack_start(add_btn)

        self.banner = Adw.Banner(title="Runtime Firefox non installato", button_label="Installa")
        self.banner.connect("button-clicked", self._install_runtime)

        self.page = Adw.PreferencesPage()
        self.group: Adw.PreferencesGroup | None = None

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        toolbar.set_content(self.page)
        self.set_child(toolbar)
        self.refresh()

    def refresh(self) -> None:
        self.banner.set_revealed(not core.runtime_installed())
        if self.group:
            self.page.remove(self.group)
        self.group = Adw.PreferencesGroup(
            title="Servizi",
            description="Ogni servizio ha profilo, login, proxy e addon separati.",
        )
        services = core.list_services()
        if not services:
            self.group.add(Adw.ActionRow(title="Nessun servizio: aggiungine uno con +"))
        for svc in services:
            row = Adw.ActionRow(title=svc.name, subtitle=f"{svc.url} · {svc.proxy.describe()}", activatable=True)
            row.set_use_markup(False)
            row.add_prefix(_icon_widget(svc))
            play = Gtk.Button(
                icon_name="media-playback-start-symbolic",
                tooltip_text="Avvia",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
            )
            play.connect("clicked", lambda _b, sid=svc.id: self.win.launch(sid))
            row.add_suffix(play)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, sid=svc.id: self.win.open_editor(sid))
            self.group.add(row)
        self.page.add(self.group)

    def _install_runtime(self, banner: Adw.Banner) -> None:
        banner.set_button_label("")
        banner.set_title("Scarico il runtime Firefox…")

        def work():
            try:
                version = core.install_runtime()
                msg = f"Runtime Firefox {version} installato"
            except (LauncherError, OSError) as e:
                msg = f"Installazione non riuscita: {e}"
            GLib.idle_add(done, msg)

        def done(msg):
            banner.set_title("Runtime Firefox non installato")
            banner.set_button_label("Installa")
            self.win.toast(msg)
            self.refresh()

        threading.Thread(target=work, daemon=True).start()


class ServiceEditor(Adw.NavigationPage):
    def __init__(self, win: "Window", svc: core.Service):
        super().__init__(title=svc.name, tag=f"edit-{svc.id}")
        self.win = win
        self.svc = svc
        self.addons = list(svc.addons)
        self.addon_rows: list[Adw.ActionRow] = []

        header = Adw.HeaderBar()
        save = Gtk.Button(label="Salva", css_classes=["suggested-action"])
        save.connect("clicked", lambda _b: self.save())
        header.pack_end(save)

        page = Adw.PreferencesPage()
        page.add(self._service_group())
        page.add(self._proxy_group())
        page.add(self._addon_group())
        page.add(self._actions_group())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)
        self._sync_sensitivity()

    # ------------------------------------------------------------------ sezioni

    def _service_group(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title="Servizio", description=f"ID: {self.svc.id}")
        self.name_row = Adw.EntryRow(title="Nome")
        self.name_row.set_text(self.svc.name)
        self.url_row = Adw.EntryRow(title="Indirizzo iniziale")
        self.url_row.set_text(self.svc.url)

        ua = self.svc.user_agent
        ua_key = ua if ua in ("", "windows") else "custom"
        self.ua_row = _combo("User agent", UA_CHOICES, ua_key)
        self.ua_row.set_subtitle("Alcuni servizi danno più qualità se il sistema dichiarato è Windows")
        self.ua_custom = Adw.EntryRow(title="User agent personalizzato")
        self.ua_custom.set_text(ua if ua_key == "custom" else "")
        self.ua_row.connect("notify::selected", lambda *_: self._sync_sensitivity())

        for row in (self.name_row, self.url_row, self.ua_row, self.ua_custom):
            g.add(row)
        return g

    def _proxy_group(self) -> Adw.PreferencesGroup:
        p = self.svc.proxy
        g = Adw.PreferencesGroup(
            title="Proxy",
            description="Tutto il traffico del servizio passa dal proxy; se il proxy cade "
            "la connessione si blocca invece di uscire in chiaro.",
        )
        self.proxy_type = _combo("Tipo", PROXY_CHOICES, p.type)
        self.proxy_type.connect("notify::selected", lambda *_: self._sync_sensitivity())
        self.proxy_host = Adw.EntryRow(title="Host")
        self.proxy_host.set_text(p.host)
        self.proxy_port = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.proxy_port.set_title("Porta")
        self.proxy_port.set_value(p.port or 1080)
        self.proxy_user = Adw.EntryRow(title="Utente (facoltativo)")
        self.proxy_user.set_text(p.username)
        self.proxy_pass = Adw.PasswordEntryRow(title="Password")
        self.proxy_pass.set_text(p.password)
        self.proxy_rows = (self.proxy_host, self.proxy_port, self.proxy_user, self.proxy_pass)
        g.add(self.proxy_type)
        for row in self.proxy_rows:
            g.add(row)
        return g

    def _addon_group(self) -> Adw.PreferencesGroup:
        self.addon_group = Adw.PreferencesGroup(title="Addon", description=ADDON_HINT)
        pick = Gtk.Button(
            icon_name="document-open-symbolic",
            tooltip_text="Scegli un file .xpi o .crx",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        pick.connect("clicked", lambda _b: self._pick_addon_file())
        self.addon_group.set_header_suffix(pick)

        self.addon_entry = Adw.EntryRow(title="Aggiungi (es. amo:ublock-origin)", show_apply_button=True)
        self.addon_entry.connect("apply", lambda row: self._add_addon(row.get_text()))
        self.addon_group.add(self.addon_entry)
        self._render_addons()
        return self.addon_group

    def _actions_group(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        launch = Adw.ButtonRow(title="Salva e avvia", start_icon_name="media-playback-start-symbolic")
        launch.connect("activated", lambda _r: self.save() and self.win.launch(self.svc.id))
        dup = Adw.ButtonRow(title="Duplica…", start_icon_name="edit-copy-symbolic")
        dup.connect("activated", lambda _r: self._ask_duplicate())
        delete = Adw.ButtonRow(title="Elimina…", start_icon_name="user-trash-symbolic", css_classes=["destructive-action"])
        delete.connect("activated", lambda _r: self._ask_delete())
        for row in (launch, dup, delete):
            g.add(row)
        return g

    # ------------------------------------------------------------------ logica

    def _sync_sensitivity(self) -> None:
        self.ua_custom.set_visible(_selected_key(self.ua_row, UA_CHOICES) == "custom")
        active = _selected_key(self.proxy_type, PROXY_CHOICES) != "none"
        for row in self.proxy_rows:
            row.set_sensitive(active)

    def _render_addons(self) -> None:
        for row in self.addon_rows:
            self.addon_group.remove(row)
        self.addon_rows = []
        for spec in self.addons:
            row = Adw.ActionRow(title=spec)
            row.set_use_markup(False)
            rm = Gtk.Button(
                icon_name="list-remove-symbolic",
                tooltip_text="Rimuovi",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
            )
            rm.connect("clicked", lambda _b, s=spec: self._remove_addon(s))
            row.add_suffix(rm)
            self.addon_group.add(row)
            self.addon_rows.append(row)

    def _add_addon(self, spec: str) -> None:
        spec = spec.strip()
        if not spec or spec in self.addons:
            return
        self.addon_entry.set_sensitive(False)

        def work():
            try:
                core.resolve_addon(spec)  # scarica e verifica subito
                err = None
            except (LauncherError, OSError) as e:
                err = str(e)
            GLib.idle_add(done, err)

        def done(err):
            self.addon_entry.set_sensitive(True)
            if err:
                self.win.toast(f"Addon non valido: {err}")
                return
            self.addons.append(spec)
            self.addon_entry.set_text("")
            self._render_addons()
            self.win.toast("Addon aggiunto: salva per confermare")

        threading.Thread(target=work, daemon=True).start()

    def _remove_addon(self, spec: str) -> None:
        self.addons.remove(spec)
        self._render_addons()

    def _pick_addon_file(self) -> None:
        flt = Gtk.FileFilter(name="Addon (.xpi, .crx, .zip)")
        for pattern in ("*.xpi", "*.crx", "*.zip"):
            flt.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog = Gtk.FileDialog(title="Scegli un addon", filters=filters)

        def on_done(dlg, result):
            try:
                f = dlg.open_finish(result)
            except GLib.Error:
                return
            if f and f.get_path():
                self._add_addon(f.get_path())

        dialog.open(self.win, None, on_done)

    def _collect(self) -> core.Service:
        ua_key = _selected_key(self.ua_row, UA_CHOICES)
        ua = self.ua_custom.get_text().strip() if ua_key == "custom" else ua_key
        ptype = _selected_key(self.proxy_type, PROXY_CHOICES)
        proxy = core.Proxy()
        if ptype != "none":
            proxy = core.Proxy(
                type=ptype,
                host=self.proxy_host.get_text().strip(),
                port=int(self.proxy_port.get_value()),
                username=self.proxy_user.get_text().strip(),
                password=self.proxy_pass.get_text(),
            )
        return core.Service(
            id=self.svc.id,
            name=self.name_row.get_text().strip(),
            url=self.url_row.get_text().strip(),
            icon_url=self.svc.icon_url,
            user_agent=ua,
            addons=list(self.addons),
            proxy=proxy,
        )

    def save(self) -> bool:
        try:
            svc = self._collect()
            core.save_service(svc)
            core.refresh_desktop_entry(svc, icon=False)
        except LauncherError as e:
            self.win.toast(str(e))
            return False
        self.svc = svc
        self.set_title(svc.name)
        self.win.list_page.refresh()
        running = core.profile_running(svc.profile_dir)
        self.win.toast("Salvato" + (": riavvia il servizio per applicare" if running else ""))
        return True

    def _ask_duplicate(self) -> None:
        if not self.save():
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        entry = Gtk.Entry(text=f"{self.svc.name} (copia)", activates_default=True)
        copy_profile = Gtk.CheckButton(label="Copia anche il profilo (login e cookie)")
        box.append(entry)
        box.append(copy_profile)

        dialog = Adw.AlertDialog(
            heading="Duplica servizio",
            body="La copia avrà lo stesso indirizzo, proxy e addon. Senza copiare il profilo "
            "dovrai rifare il login.",
            extra_child=box,
        )
        dialog.add_response("cancel", "Annulla")
        dialog.add_response("duplicate", "Duplica")
        dialog.set_response_appearance("duplicate", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("duplicate")

        def on_response(_d, response):
            if response != "duplicate":
                return
            name, copy = entry.get_text(), copy_profile.get_active()

            def done(new):
                self.win.list_page.refresh()
                self.win.nav.pop()
                self.win.open_editor(new.id)
                self.win.toast(f"Creato «{new.name}»")

            self.win.toast("Duplico…")
            # La copia del profilo può durare a lungo: mai sul thread della GUI.
            self.win.run_async(lambda: core.duplicate_service(self.svc.id, name, copy_profile=copy), done)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _ask_delete(self) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Eliminare «{self.svc.name}»?",
            body="Verranno rimossi configurazione, voce di menu e profilo (login compresi).",
        )
        dialog.add_response("cancel", "Annulla")
        dialog.add_response("delete", "Elimina")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response != "delete":
                return

            def done(_result):
                self.win.list_page.refresh()
                self.win.nav.pop()
                self.win.toast("Servizio eliminato")

            self.win.run_async(lambda: core.remove_service(self.svc.id), done)

        dialog.connect("response", on_response)
        dialog.present(self.win)


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Stream Launcher", default_width=600, default_height=760)
        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)
        self.list_page = ServiceListPage(self)
        self.nav.add(self.list_page)

        action = Gio.SimpleAction.new("add-template", GLib.VariantType.new("s"))
        action.connect("activate", self._add_template)
        self.add_action(action)

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=4))

    def open_editor(self, service_id: str) -> None:
        try:
            svc = core.load_service(service_id)
        except LauncherError as e:
            self.toast(str(e))
            return
        self.nav.push(ServiceEditor(self, svc))

    def launch(self, service_id: str) -> None:
        if not core.runtime_installed():
            self.toast("Installa prima il runtime Firefox")
            return
        core.launch_detached(service_id)
        self.toast("Avvio in corso…")

    def run_async(self, work, on_done) -> None:
        """Esegue `work` in un thread (disco/rete) e `on_done(risultato)` sul thread GTK.

        Tenere libero il thread della GUI evita il "non risponde" di GNOME.
        """

        def runner():
            try:
                result, error = work(), None
            except (LauncherError, OSError) as e:
                result, error = None, str(e)
            GLib.idle_add(finish, result, error)

        def finish(result, error):
            if error:
                self.toast(error)
            else:
                on_done(result)
            return False

        threading.Thread(target=runner, daemon=True).start()

    def _add_template(self, _action, param: GLib.Variant) -> None:
        def done(svc):
            self.list_page.refresh()
            self.open_editor(svc.id)

        # Scarica l'icona: in background.
        self.run_async(lambda: core.add_from_template(param.get_string()), done)


def main(service_id: str | None = None) -> int:
    # Istanza unica: riaprire le impostazioni (es. dalla tray) porta in primo piano quella aperta.
    app = Adw.Application(application_id=core.SETTINGS_APP_ID)

    def on_activate(application):
        if application.get_active_window():
            application.get_active_window().present()
            return
        win = Window(application)
        win.present()
        if service_id:
            win.open_editor(service_id)

    app.connect("activate", on_activate)
    return app.run([])
