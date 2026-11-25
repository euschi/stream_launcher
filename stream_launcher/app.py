"""Finestra impostazioni e icona nella tray (Qt: Linux e Windows), in un solo processo.

Istanza unica per utente: rilanciare `settings` o `tray` inoltra la richiesta
all'istanza già attiva (QLocalServer), che apre la finestra o resta in tray.
Disco e rete vanno sempre in un thread: la GUI non si blocca mai.
"""
from __future__ import annotations

import getpass
import signal
import threading

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QIcon, QIntValidator, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import core
from .core import LauncherError

SERVER_NAME = f"{core.APP}-{getpass.getuser()}"
PROXY_CHOICES = [
    ("none", "Nessuno"),
    ("http", "HTTP"),
    ("https", "HTTPS (proxy in TLS)"),
    ("socks5", "SOCKS5"),
    ("socks4", "SOCKS4"),
]
UA_CHOICES = [
    ("", "Predefinito (Windows)" if core.IS_WIN else "Predefinito (Linux)"),
    ("windows", "Firefox su Windows"),
    ("custom", "Personalizzato"),
]
# Temi coerenti con l'icona: accento arancio -> magenta su fondo viola-nero (scuro) o lilla chiaro.
ASSETS = core.ASSETS_DIR.as_posix()
THEMES = core.THEMES
THEME_CHOICES = [("auto", "Automatico (come il sistema)"), ("dark", "Scuro"), ("light", "Chiaro")]


def _style(c: dict) -> str:
    return f"""
QWidget {{ color: {c['text']}; font-size: 13px; }}
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget, QDialog {{ background: {c['bg']}; }}
QLabel[role="title"] {{ font-size: 20px; font-weight: 700; }}
QLabel[role="name"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="section"] {{ font-size: 16px; font-weight: 700; }}
QFrame#catalog {{ background: transparent; border: 1px dashed {c['border']}; border-radius: 14px; }}
QFrame#catalog:hover {{ background: {c['surface']}; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QFrame#row {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#row:hover {{ background: {c['surface_hi']}; border-color: {c['hover_border']}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{ background: transparent; border: none; }}
QListWidget#addons {{ background: {c['bg']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 4px; }}
QListWidget#addons::item {{ padding: 4px 6px; border-radius: 6px; }}
QListWidget#addons::item:selected {{ background: {c['surface_hi']}; }}
QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px;
            margin-top: 30px; padding: 16px 14px 12px; font-size: 14px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 6px; top: 6px; color: {c['text']}; }}
QLineEdit, QComboBox {{ background: {c['bg']}; border: 1px solid {c['border']}; border-radius: 8px;
            padding: 7px 9px; font-weight: 400; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {c['disabled']}; border-color: {c['disabled_border']}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{ASSETS}/chevron.svg"); width: 12px; height: 12px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {c['hover_border']}; border-radius: 5px; background: {c['bg']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url("{ASSETS}/check.svg"); }}
QCheckBox::indicator:disabled {{ border-color: {c['disabled_border']}; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; selection-background-color: {c['surface_hi']}; }}
QCheckBox {{ font-weight: 400; spacing: 8px; }}
QPushButton, QToolButton {{ background: {c['surface_hi']}; border: 1px solid {c['border']}; border-radius: 9px;
            padding: 7px 14px; font-weight: 600; }}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; border-color: {c['hover_border']}; }}
QPushButton:disabled {{ color: {c['disabled']}; background: {c['surface']}; }}
QPushButton[kind="accent"], QToolButton[kind="accent"] {{ background: {c['gradient']}; border: none; color: white; }}
QPushButton[kind="accent"]:hover, QToolButton[kind="accent"]:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffb347, stop:1 #f472b6); }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {c['muted']}; }}
QPushButton[kind="ghost"]:hover {{ color: {c['text']}; background: {c['surface_hi']}; }}
QPushButton[kind="danger"] {{ background: transparent; border: 1px solid {c['danger_border']}; color: {c['danger']}; }}
QPushButton[kind="danger"]:hover {{ background: {c['danger_hover']}; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['surface_hi']}; }}
QStatusBar {{ background: {c['bg']}; color: {c['muted']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def _apply_theme(app: QApplication) -> None:
    """Applica il tema scelto nelle impostazioni; «auto» segue il sistema."""
    choice = core.get_pref("theme", "auto")
    if choice not in THEMES:
        choice = "dark" if app.styleHints().colorScheme() != Qt.ColorScheme.Light else "light"
    c = THEMES[choice]
    app.setStyle("Fusion")  # identico su Linux e Windows, e rispetta la palette
    pal = QPalette()
    for role, key in (
        (QPalette.Window, "bg"), (QPalette.Base, "bg"), (QPalette.AlternateBase, "surface"),
        (QPalette.Button, "surface_hi"), (QPalette.WindowText, "text"), (QPalette.Text, "text"),
        (QPalette.ButtonText, "text"), (QPalette.ToolTipBase, "surface"), (QPalette.ToolTipText, "text"),
        (QPalette.Highlight, "accent"), (QPalette.PlaceholderText, "placeholder"),
    ):
        pal.setColor(role, QColor(c[key]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)
    app.setStyleSheet(_style(c))


ADDON_HINT = (
    "amo:NOME da addons.mozilla.org, chrome:ID dal Chrome Web Store, "
    "oppure un file .xpi / .crx o una cartella. Gli addon Chrome e non firmati "
    "funzionano solo con il runtime ESR."
)


class _Bridge(QObject):
    """Riporta sul thread della GUI le funzioni chiamate dai thread di lavoro."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()


_bridge: _Bridge | None = None


def run_async(work, on_done, on_error) -> None:
    """`work()` in un thread (disco/rete), poi `on_done(risultato)` o `on_error(testo)` sulla GUI."""

    def runner():
        try:
            result = work()
            _bridge.call.emit(lambda: on_done(result))
        except (LauncherError, OSError) as e:
            msg = str(e)
            _bridge.call.emit(lambda: on_error(msg))

    threading.Thread(target=runner, daemon=True).start()


def app_icon() -> QIcon:
    return QIcon(str(core.ASSETS_DIR / "icon.svg"))


def service_icon(svc: core.Service) -> QIcon:
    return QIcon(str(svc.icon_path)) if svc.icon_path.is_file() else app_icon()


def _combo(choices: list[tuple[str, str]], current: str) -> QComboBox:
    box = QComboBox()
    for key, label in choices:
        box.addItem(label, key)
    keys = [k for k, _ in choices]
    box.setCurrentIndex(keys.index(current) if current in keys else len(keys) - 1)
    return box


def _label(text: str, role: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(wrap)
    if role:
        label.setProperty("role", role)
    return label


def _note(text: str) -> QLabel:
    return _label(text, "muted", wrap=True)


def _button(label: str, fn, kind: str = "") -> QPushButton:
    b = QPushButton(label)
    b.setCursor(Qt.PointingHandCursor)
    if kind:
        b.setProperty("kind", kind)
    b.clicked.connect(lambda _checked=False: fn())
    return b


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0]


def _header(title: str, subtitle: str = "", icon: QIcon | None = None) -> tuple[QHBoxLayout, QHBoxLayout]:
    """Intestazione di pagina: (riga completa, riga dei pulsanti a destra)."""
    row = QHBoxLayout()
    row.setSpacing(12)
    if icon:
        pic = QLabel()
        pic.setPixmap(icon.pixmap(44, 44))
        row.addWidget(pic)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(_label(title, "title"))
    if subtitle:
        text.addWidget(_label(subtitle, "muted"))
    row.addLayout(text, 1)
    actions = QHBoxLayout()
    actions.setSpacing(8)
    row.addLayout(actions)
    return row, actions


# --------------------------------------------------------------------------- elenco


class ServiceList(QWidget):
    """In cima i servizi installati, sotto il catalogo da cui installarne altri."""

    def __init__(self, win: "Window"):
        super().__init__()
        self.win = win
        self.busy: set[str] = set()  # modelli in installazione
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 8)
        layout.setSpacing(16)
        header, actions = _header("Stream Launcher", "Ogni servizio ha profilo, login, proxy e addon separati", app_icon())
        actions.addWidget(_button("⚙  Impostazioni", win.open_settings))
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setSpacing(5)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.itemClicked.connect(lambda it: win.open_editor(it.data(Qt.UserRole)))
        layout.addWidget(self.list, 1)
        self.refresh()
        # Icone del catalogo: si scaricano una volta, in background.
        run_async(core.fetch_catalog_icons, lambda n: n and self.refresh(), lambda _e: None)

    def _section(self, title: str, note: str) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        self.list.addItem(item)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(4, 12, 4, 2)
        v.setSpacing(0)
        v.addWidget(_label(title, "section"))
        v.addWidget(_label(note, "muted"))
        item.setSizeHint(box.sizeHint())
        self.list.setItemWidget(item, box)

    def _row(self, icon: QIcon, name: str, detail: str, buttons: list[QPushButton], service_id: str | None) -> None:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, service_id)
        self.list.addItem(item)
        row = QFrame(objectName="row" if service_id else "catalog")
        row.setAttribute(Qt.WA_Hover)
        if service_id:
            row.setCursor(Qt.PointingHandCursor)
            row.setToolTip("Modifica")
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 10, 12, 10)
        h.setSpacing(14)
        pic = QLabel()
        pic.setPixmap(icon.pixmap(40, 40))
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label(name, "name"))
        text.addWidget(_label(detail, "muted"))
        h.addWidget(pic)
        h.addLayout(text, 1)
        for b in buttons:
            h.addWidget(b)
        item.setSizeHint(row.sizeHint())
        self.list.setItemWidget(item, row)

    def refresh(self) -> None:
        self.list.clear()
        services = core.list_services()
        installed = {s.id for s in services}
        self._section("I tuoi servizi", "Clic su un servizio per proxy, addon e duplicati."
                      if services else "Ancora nessuno: installali dal catalogo qui sotto.")
        for svc in services:
            self._row(
                service_icon(svc), svc.name, f"{_host(svc.url)}  ·  {svc.proxy.describe()}",
                [_button("▶  Avvia", lambda sid=svc.id: self.win.launch(sid), "accent"),
                 _button("Elimina", lambda s=svc: self.win.confirm_delete(s), "danger")],
                svc.id,
            )
        catalog = [t for t in core.list_templates() if t.id not in installed]
        self._section("Catalogo", "Installa solo quello che usi. Il primo servizio scarica anche il motore Firefox."
                      if catalog else "Hai installato tutto il catalogo.")
        for t in catalog:
            icon_file = core.catalog_icon_path(t.id)
            busy = t.id in self.busy
            button = _button("Installo…" if busy else "Installa", lambda tid=t.id: self.install(tid), "" if busy else "accent")
            button.setEnabled(not busy)
            self._row(QIcon(str(icon_file)) if icon_file.is_file() else app_icon(), t.name, _host(t.url), [button], None)

    def install(self, template_id: str) -> None:
        self.busy.add(template_id)
        self.refresh()
        first = not core.runtime_installed()
        self.win.toast("Scarico il motore Firefox e installo…" if first else "Installo…")

        def finish(message: str) -> None:
            self.busy.discard(template_id)
            self.refresh()
            self.win.toast(message)

        run_async(lambda: core.add_from_template(template_id),
                  lambda svc: finish(f"«{svc.name}» installato"),
                  lambda e: finish(f"Installazione non riuscita: {e}"))


# --------------------------------------------------------------------------- modifica


class Editor(QScrollArea):
    def __init__(self, win: "Window", svc: core.Service):
        super().__init__()
        self.win = win
        self.svc = svc
        self.addons = list(svc.addons)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(22, 14, 22, 18)
        layout.setSpacing(4)

        layout.addWidget(_button("←  Tutti i servizi", win.show_list, "ghost"), 0, Qt.AlignLeft)
        header, top = _header(svc.name, f"ID {svc.id}", service_icon(svc))
        save = _button("Salva", self.save, "accent")
        save.setDefault(True)
        top.addWidget(save)
        layout.addLayout(header)

        for group in (self._service_group(), self._proxy_group(), self._addon_group()):
            form = group.layout()
            if isinstance(form, QFormLayout):  # etichette della stessa larghezza in tutti i riquadri
                for i in range(form.rowCount()):
                    item = form.itemAt(i, QFormLayout.LabelRole)
                    if item and isinstance(item.widget(), QLabel):
                        item.widget().setFixedWidth(118)
            layout.addWidget(group)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 14, 0, 0)
        actions.addWidget(_button("▶  Salva e avvia", lambda: self.save(then=lambda: win.launch(self.svc.id)), "accent"))
        actions.addWidget(_button("Duplica…", self._ask_duplicate))
        actions.addStretch(1)
        actions.addWidget(_button("Elimina…", lambda: win.confirm_delete(self.svc, after=win.show_list), "danger"))
        layout.addLayout(actions)
        layout.addStretch(1)
        self.setWidget(body)
        self._sync_sensitivity()

    # ------------------------------------------------------------------ sezioni

    @staticmethod
    def _form(group: QGroupBox) -> QFormLayout:
        form = QFormLayout(group)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return form

    @staticmethod
    def _side_by_side(button: QPushButton, note: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(button)
        row.addWidget(note, 1)
        return row

    def _service_group(self) -> QGroupBox:
        g = QGroupBox("Servizio")
        form = self._form(g)
        self.name = QLineEdit(self.svc.name)
        self.url = QLineEdit(self.svc.url)
        ua = self.svc.user_agent
        ua_key = ua if ua in ("", "windows") else "custom"
        self.ua = _combo(UA_CHOICES, ua_key)
        self.ua.currentIndexChanged.connect(self._sync_sensitivity)
        self.ua_custom = QLineEdit(ua if ua_key == "custom" else "")
        self.ua_custom.setPlaceholderText("User agent personalizzato")
        form.addRow("Nome", self.name)
        form.addRow("Indirizzo iniziale", self.url)
        form.addRow("User agent", self.ua)
        form.addRow("", self.ua_custom)
        form.addRow(_note("Alcuni servizi danno più qualità se il sistema dichiarato è Windows."))
        return g

    def _proxy_group(self) -> QGroupBox:
        p = self.svc.proxy
        g = QGroupBox("Proxy")
        form = self._form(g)
        form.addRow(_note("Tutto il traffico del servizio passa dal proxy; se il proxy cade "
                          "la connessione si blocca invece di uscire in chiaro."))
        self.proxy_type = _combo(PROXY_CHOICES, p.type)
        self.proxy_type.currentIndexChanged.connect(self._sync_sensitivity)
        self.proxy_host = QLineEdit(p.host)
        self.proxy_port = QLineEdit(str(p.port or 1080))
        self.proxy_port.setValidator(QIntValidator(1, 65535, self.proxy_port))
        self.proxy_user = QLineEdit(p.username, placeholderText="facoltativo")
        self.proxy_pass = QLineEdit(p.password, echoMode=QLineEdit.Password)
        form.addRow("Tipo", self.proxy_type)
        form.addRow("Host", self.proxy_host)
        form.addRow("Porta", self.proxy_port)
        form.addRow("Utente", self.proxy_user)
        form.addRow("Password", self.proxy_pass)
        self.test_btn = _button("Prova connessione", self._test_proxy)
        self.test_result = _note("Verifica IP e paese di uscita e che il servizio risponda.")
        form.addRow(self._side_by_side(self.test_btn, self.test_result))
        return g

    def _addon_group(self) -> QGroupBox:
        g = QGroupBox("Addon")
        v = QVBoxLayout(g)
        v.addWidget(_note(ADDON_HINT))
        self.addon_list = QListWidget(objectName="addons")
        self.addon_list.setMaximumHeight(110)
        v.addWidget(self.addon_list)
        row = QHBoxLayout()
        self.addon_entry = QLineEdit(placeholderText="es. amo:ublock-origin")
        self.addon_entry.returnPressed.connect(lambda: self._add_addon(self.addon_entry.text()))
        row.addWidget(self.addon_entry, 1)
        row.addWidget(_button("Aggiungi", lambda: self._add_addon(self.addon_entry.text())))
        row.addWidget(_button("File…", self._pick_addon_file))
        row.addWidget(_button("Rimuovi", self._remove_addon))
        v.addLayout(row)
        self._render_addons()
        return g

    # ------------------------------------------------------------------ logica

    def _sync_sensitivity(self) -> None:
        self.ua_custom.setVisible(self.ua.currentData() == "custom")
        active = self.proxy_type.currentData() != "none"
        for w in (self.proxy_host, self.proxy_port, self.proxy_user, self.proxy_pass):
            w.setEnabled(active)

    def _render_addons(self) -> None:
        self.addon_list.clear()
        self.addon_list.addItems(self.addons)

    def _add_addon(self, spec: str) -> None:
        spec = spec.strip()
        if not spec or spec in self.addons:
            return
        self.addon_entry.setEnabled(False)

        def done(_):
            self.addon_entry.setEnabled(True)
            self.addons.append(spec)
            self.addon_entry.clear()
            self._render_addons()
            self.win.toast("Addon aggiunto: salva per confermare")

        def failed(err):
            self.addon_entry.setEnabled(True)
            self.win.toast(f"Addon non valido: {err}")

        run_async(lambda: core.resolve_addon(spec), done, failed)  # scarica e verifica subito

    def _remove_addon(self) -> None:
        item = self.addon_list.currentItem()
        if item:
            self.addons.remove(item.text())
            self._render_addons()

    def _pick_addon_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Scegli un addon", "", "Addon (*.xpi *.crx *.zip)")
        if path:
            self._add_addon(path)

    def _test_proxy(self) -> None:
        from .proxytest import ProxyTestError, test_proxy

        svc = self._collect()  # valori della maschera, anche se non ancora salvati
        if svc.proxy.type != "none" and not svc.proxy.enabled:
            self.win.toast("Inserisci host e porta del proxy")
            return
        self.test_btn.setEnabled(False)
        self.test_result.setText("Prova in corso…" if svc.proxy.enabled else "Nessun proxy: provo la connessione diretta…")

        def work():
            try:
                return "✔ " + test_proxy(svc.proxy, svc.url).summary()
            except ProxyTestError as e:
                return f"✘ Non funziona: {e}"

        def done(text):
            self.test_btn.setEnabled(True)
            self.test_result.setText(text)

        run_async(work, done, lambda e: done(f"✘ Non funziona: {e}"))

    def _collect(self) -> core.Service:
        ua_key = self.ua.currentData()
        ptype = self.proxy_type.currentData()
        proxy = core.Proxy()
        if ptype != "none":
            proxy = core.Proxy(
                type=ptype,
                host=self.proxy_host.text().strip(),
                port=int(self.proxy_port.text() or 0),
                username=self.proxy_user.text().strip(),
                password=self.proxy_pass.text(),
            )
        return core.Service(
            id=self.svc.id,
            name=self.name.text().strip(),
            url=self.url.text().strip(),
            icon_url=self.svc.icon_url,
            user_agent=self.ua_custom.text().strip() if ua_key == "custom" else ua_key,
            addons=list(self.addons),
            proxy=proxy,
            # Senza browser compatibile la casella è spenta: non sovrascrivo la scelta salvata.
            passwords=self.svc.passwords,
            login_domains=self.svc.login_domains,
        )

    def save(self, then=None) -> None:
        try:
            svc = self._collect()
            core.save_service(svc)
        except LauncherError as e:
            self.win.toast(str(e))
            return
        self.svc = svc
        running = core.profile_running(svc.profile_dir)
        self.win.toast("Salvato" + (": riavvia il servizio per applicare" if running else ""))
        self.win.list_page.refresh()
        # Voci di menu (su Windows una chiamata a PowerShell): fuori dal thread della GUI.
        run_async(lambda: core.refresh_desktop_entry(svc, icon=False), lambda _: then and then(), self.win.toast)

    def _ask_duplicate(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Duplica servizio")
        v = QVBoxLayout(dialog)
        v.addWidget(_note("La copia avrà lo stesso indirizzo, proxy e addon. Senza copiare il profilo "
                          "dovrai rifare il login."))
        name = QLineEdit(f"{self.svc.name} (copia)")
        copy_profile = QCheckBox("Copia anche il profilo (login e cookie)")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Duplica")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        for w in (name, copy_profile, buttons):
            v.addWidget(w)
        if dialog.exec() != QDialog.Accepted:
            return
        new_name, copy, src_id = name.text(), copy_profile.isChecked(), self.svc.id

        def done(new):
            self.win.open_editor(new.id)
            self.win.toast(f"Creato «{new.name}»")

        self.win.toast("Duplico…")
        # La copia del profilo può durare a lungo: mai sul thread della GUI.
        run_async(lambda: core.duplicate_service(src_id, new_name, copy_profile=copy), done, self.win.toast)


# --------------------------------------------------------------------------- password


class SettingsPage(QScrollArea):
    """Impostazioni del launcher: aspetto, comportamento, motore Firefox e password.

    Le password si importano a livello globale: un browser, solo i login dei servizi,
    in un archivio protetto.
    """

    def __init__(self, win: "Window"):
        from .passwords import default_browser, installed_browsers

        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(22, 14, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(_button("←  Tutti i servizi", win.show_list, "ghost"), 0, Qt.AlignLeft)
        header, _ = _header("Impostazioni", "Aspetto, comportamento, motore Firefox e password", app_icon())
        layout.addLayout(header)
        for group in (self._look_group(), self._behaviour_group(), self._runtime_group()):
            layout.addWidget(group)

        g = QGroupBox("Password")
        v = QVBoxLayout(g)
        v.addWidget(_note(
            "Dal browser scelto vengono letti SOLO i login dei siti dei servizi (quelli installati e "
            "quelli del catalogo) e salvati in un archivio protetto del launcher: il portachiavi di "
            "sistema su Linux, un file cifrato con il tuo utente su Windows. A ogni avvio, ogni "
            "servizio riceve i login del suo sito. Ripeti l'import quando cambi una password: le voci "
            "che modifichi o elimini qui restano come le hai lasciate."))
        row = QHBoxLayout()
        self.browser = QComboBox()
        default = default_browser()
        for b in installed_browsers():
            suffix = " (predefinito)" if default and b.desktop_id == default.desktop_id else ""
            self.browser.addItem(b.name + suffix, b)
        if not self.browser.count():
            self.browser.addItem("Nessun browser compatibile trovato (Chrome, Edge, Chromium, Brave)", None)
        if default:
            self.browser.setCurrentIndex(max(0, self.browser.findText(default.name + " (predefinito)")))
        self.import_btn = _button("Importa", self._import, "accent")
        self.import_btn.setEnabled(self.browser.currentData() is not None)
        row.addWidget(self.browser, 1)
        row.addWidget(self.import_btn)
        v.addLayout(row)
        layout.addWidget(g)

        g = QGroupBox("Archivio password")
        v = QVBoxLayout(g)
        self.store: dict = {}
        self.status = _note("")
        self.logins = QListWidget(objectName="addons")
        self.logins.setMinimumHeight(260)
        self.logins.setSelectionMode(QListWidget.NoSelection)
        v.addWidget(self.status)
        v.addWidget(self.logins)
        bottom = QHBoxLayout()
        bottom.addWidget(_button("+  Aggiungi a mano", lambda: self._edit(None)))
        bottom.addStretch(1)
        bottom.addWidget(_button("Svuota l'archivio", self._clear, "danger"))
        v.addLayout(bottom)
        layout.addWidget(g)
        layout.addStretch(1)
        self.setWidget(body)
        self._load()

    # ------------------------------------------------------------------ aspetto e comportamento

    @staticmethod
    def _pref_combo(key: str, choices: list[tuple[str, str]], default: str, then=None) -> QComboBox:
        box = _combo(choices, core.get_pref(key, default))

        def changed():
            core.set_pref(key, box.currentData())
            if then:
                then()

        box.currentIndexChanged.connect(changed)
        return box

    def _look_group(self) -> QGroupBox:
        g = QGroupBox("Aspetto")
        form = QFormLayout(g)
        form.addRow("Tema", self._pref_combo("theme", THEME_CHOICES, "auto", lambda: _apply_theme(QApplication.instance())))
        return g

    def _behaviour_group(self) -> QGroupBox:
        g = QGroupBox("Comportamento")
        form = QFormLayout(g)
        autostart = QCheckBox("Avvia con il sistema (icona nella tray all'accesso)")
        autostart.setChecked(core.autostart_path().exists())
        autostart.toggled.connect(lambda on: run_async(lambda: core.set_autostart(on), lambda _: None, self.win.toast))
        form.addRow(autostart)
        form.addRow("Chiudendo la finestra", self._pref_combo(
            "close_action", [("tray", "Resta nella tray"), ("quit", "Esci da Stream Launcher")], "tray"))
        form.addRow("Avviando un servizio", self._pref_combo(
            "after_launch", [("stay", "Lascia aperta questa finestra"), ("close", "Chiudi questa finestra")], "stay"))
        return g

    def _runtime_group(self) -> QGroupBox:
        g = QGroupBox("Motore Firefox")
        form = QFormLayout(g)
        self.runtime_info = _note("")
        self.channel = _combo([("esr", "ESR (consigliato)"), ("release", "Release (più recente)")], core.runtime_channel())
        self.runtime_btn = _button("Scarica / aggiorna", self._update_runtime)
        bottom = QHBoxLayout()
        bottom.addWidget(_note(f"Profili e motore in {core.STORAGE_DIR}"), 1)
        bottom.addWidget(self.runtime_btn, 0, Qt.AlignBottom)
        form.addRow(self.runtime_info)
        form.addRow("Canale", self.channel)
        form.addRow(_note("ESR serve per gli addon Chrome e per i proxy con credenziali."))
        form.addRow(bottom)
        self._runtime_text()
        return g

    def _runtime_text(self) -> None:
        version = core.runtime_version()
        self.runtime_info.setText(f"Installato: Firefox {version} ({core.runtime_channel()})" if version
                                  else "Non ancora scaricato: arriva con il primo servizio installato.")

    def _update_runtime(self) -> None:
        running = [svc.name for svc in core.list_services() if core.profile_running(svc.profile_dir)]
        if running:
            self.win.toast(f"Chiudi prima: {', '.join(running)}")
            return
        channel = self.channel.currentData()
        self.runtime_btn.setEnabled(False)
        self.runtime_info.setText("Scarico Firefox…")

        def done(_):
            self.runtime_btn.setEnabled(True)
            self._runtime_text()
            self.win.toast("Motore Firefox aggiornato")

        def failed(err):
            self.runtime_btn.setEnabled(True)
            self._runtime_text()
            self.win.toast(f"Aggiornamento non riuscito: {err}")

        run_async(lambda: core.install_runtime(channel), done, failed)

    # ------------------------------------------------------------------ password

    def _show(self, store: dict) -> None:
        self.store = store
        self.logins.clear()
        logins = store.get("logins", [])
        if not logins:
            self.status.setText("Archivio vuoto: scegli un browser e premi Importa.")
        elif store.get("updated"):
            when = store["updated"].replace("T", " alle ")
            self.status.setText(f"{len(logins)} login, ultimo import da {store['browser']} il {when}.")
        else:
            self.status.setText(f"{len(logins)} login inseriti a mano.")
        # Nell'elenco solo siti e utenti: la password si vede solo aprendo la voce.
        for lg in sorted(logins, key=lambda x: (x["origin"], x["username"])):
            item = QListWidgetItem()
            self.logins.addItem(item)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 4, 4)
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(_label(lg["username"], "name"))
            text.addWidget(_label(_host(lg["origin"]) + ("  ·  modificato a mano" if lg.get("manual") else ""), "muted"))
            h.addLayout(text, 1)
            h.addWidget(_button("Modifica", lambda x=lg: self._edit(x)))
            h.addWidget(_button("Elimina", lambda x=lg: self._delete(x), "danger"))
            item.setSizeHint(row.sizeHint())
            self.logins.setItemWidget(item, row)

    def _edit(self, login: dict | None) -> None:
        from .passwords import PasswordError, edit_login

        login = login or {}
        dialog = QDialog(self)
        dialog.setWindowTitle("Modifica login" if login else "Nuovo login")
        form = QFormLayout(dialog)
        site = QLineEdit(_host(login.get("origin", "")), placeholderText="es. www.netflix.com")
        user = QLineEdit(login.get("username", ""))
        pw = QLineEdit(login.get("password", ""), echoMode=QLineEdit.Password)
        show = QCheckBox("Mostra password")
        show.toggled.connect(lambda on: pw.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Salva")
        buttons.button(QDialogButtonBox.Cancel).setText("Annulla")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow("Sito", site)
        form.addRow("Utente", user)
        form.addRow("Password", pw)
        form.addRow("", show)
        form.addRow(buttons)
        dialog.resize(440, dialog.sizeHint().height())
        if dialog.exec() != QDialog.Accepted:
            return
        old_key = [login["origin"], login["username"]] if login else None
        values = (site.text().strip(), user.text().strip(), pw.text())

        def work():
            try:
                return edit_login(self.store, old_key, *values)
            except PasswordError as e:
                raise LauncherError(str(e)) from e

        run_async(work, lambda store: (self._show(store), self.win.toast("Login salvato")),
                  lambda e: self.win.toast(f"Non salvato: {e}"))

    def _delete(self, login: dict) -> None:
        from .passwords import delete_login

        if QMessageBox.question(self, "Eliminare il login?",
                                f"{login['username']} su {_host(login['origin'])}: non verrà più passato al servizio "
                                "e un nuovo import non lo riporterà.") != QMessageBox.Yes:
            return
        run_async(lambda: delete_login(self.store, [login["origin"], login["username"]]),
                  lambda store: (self._show(store), self.win.toast("Login eliminato")), self.win.toast)

    def _load(self) -> None:
        from .passwords import load_store

        self.status.setText("Leggo l'archivio…")
        run_async(load_store, self._show, self.win.toast)

    def _import(self) -> None:
        from .passwords import PasswordError, import_logins

        source = self.browser.currentData()
        self.import_btn.setEnabled(False)
        self.status.setText(f"Importo da {source.name}…")

        def work():
            try:
                return import_logins(source, core.all_login_domains())
            except PasswordError as e:
                raise LauncherError(str(e)) from e

        def done(store):
            self.import_btn.setEnabled(True)
            self._show(store)
            self.win.toast(f"Archivio aggiornato da {source.name}: {len(store['logins'])} login")

        def failed(err):
            self.import_btn.setEnabled(True)
            self.status.setText(f"Import non riuscito: {err}")

        run_async(work, done, failed)

    def _clear(self) -> None:
        from .passwords import clear_store

        if QMessageBox.question(self, "Svuotare l'archivio?", "I servizi non riceveranno più i login importati.") \
                == QMessageBox.Yes:
            run_async(clear_store, lambda _: self._show({}), self.win.toast)


# --------------------------------------------------------------------------- finestra e tray


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stream Launcher")
        self.resize(700, 800)
        self.setMinimumWidth(560)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.list_page = ServiceList(self)
        self.stack.addWidget(self.list_page)
        self.editor: QWidget | None = None  # pagina aperta sopra l'elenco (servizio o password)

    def toast(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        if core.get_pref("close_action", "tray") == "quit":
            QApplication.instance().quit()

    def show_list(self) -> None:
        self.list_page.refresh()
        self.stack.setCurrentWidget(self.list_page)
        if self.editor:
            self.stack.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None

    def open_editor(self, service_id: str | None) -> None:
        if not service_id:
            return
        try:
            svc = core.load_service(service_id)
        except LauncherError as e:
            self.toast(str(e))
            return
        self.show_list()
        self.editor = Editor(self, svc)
        self.stack.addWidget(self.editor)
        self.stack.setCurrentWidget(self.editor)

    def open_settings(self) -> None:
        self.show_list()
        self.editor = SettingsPage(self)
        self.stack.addWidget(self.editor)
        self.stack.setCurrentWidget(self.editor)

    def launch(self, service_id: str) -> None:
        if core.runtime_installed():
            core.launch_detached(service_id)
            self.toast("Avvio in corso…")
            if core.get_pref("after_launch", "stay") == "close":
                self.close()
            return
        self.toast("Scarico il motore Firefox, poi avvio…")
        run_async(core.ensure_runtime, lambda _: self.launch(service_id), self.toast)

    def confirm_delete(self, svc: core.Service, after=None) -> None:
        answer = QMessageBox.question(
            self,
            f"Eliminare «{svc.name}»?",
            "Verranno rimossi configurazione, voce di menu e profilo (login compresi).",
        )
        if answer != QMessageBox.Yes:
            return

        def done(_):
            self.list_page.refresh()
            if after:
                after()
            self.toast(f"«{svc.name}» eliminato")

        run_async(lambda: core.remove_service(svc.id), done, self.toast)


class Controller(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.window: Window | None = None
        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("Stream Launcher")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

        # Ricostruisce il menu quando si aggiunge, duplica o rinomina un servizio.
        for path in (core.SERVICES_DIR, core.ICONS_DIR):
            path.mkdir(parents=True, exist_ok=True)
        self.watcher = QFileSystemWatcher([str(core.SERVICES_DIR), str(core.ICONS_DIR)])
        self.timer = QTimer(singleShot=True, interval=300)  # più eventi ravvicinati = un solo rebuild
        self.timer.timeout.connect(self.rebuild_menu)
        self.watcher.directoryChanged.connect(lambda _p: self.timer.start())
        self.rebuild_menu()

    def rebuild_menu(self) -> None:
        self.menu.clear()
        services = core.list_services()
        if not services:
            self.menu.addAction("Nessun servizio").setEnabled(False)
        for svc in services:
            label = svc.name if not svc.proxy.enabled else f"{svc.name}  ·  proxy"
            self.menu.addAction(service_icon(svc), label.replace("&", "&&"), lambda sid=svc.id: self.launch(sid))
        self.menu.addSeparator()
        self.menu.addAction("Apri Stream Launcher…", self.show_settings)
        self.menu.addAction("Esci", self.app.quit)

    def _activated(self, reason) -> None:
        if reason != QSystemTrayIcon.Trigger:
            return
        # Windows: clic sinistro = elenco servizi. Su Linux la posizione del cursore non è
        # affidabile (Wayland): il menu sta sul tasto destro, il clic apre le impostazioni.
        if core.IS_WIN:
            self.menu.popup(QCursor.pos())
        else:
            self.show_settings()

    def launch(self, service_id: str) -> None:
        if not core.runtime_installed():  # il download si vede nella finestra
            self.show_settings()
            self.window.launch(service_id)
            return
        core.launch_detached(service_id)

    def show_settings(self, service_id: str | None = None) -> None:
        if self.window is None:
            self.window = Window()
        else:
            self.window.list_page.refresh()
        self.window.show()
        self.window.setWindowState(self.window.windowState() & ~Qt.WindowMinimized)
        self.window.raise_()
        self.window.activateWindow()
        if service_id:
            self.window.open_editor(service_id)

    def handle(self, message: str) -> None:
        mode, _, service_id = message.partition(" ")
        if mode == "settings":
            self.show_settings(service_id or None)


def _forward(message: str) -> bool:
    """Passa la richiesta all'istanza già attiva, se c'è."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(500):
        return False
    sock.write(message.encode("utf-8"))
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main(mode: str = "settings", service_id: str | None = None) -> int:
    global _bridge
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication([core.APP])
    app.setApplicationName(core.APP)
    app.setApplicationDisplayName("Stream Launcher")
    app.setDesktopFileName(core.APP)  # Wayland: abbina le finestre a stream-launcher.desktop
    app.setWindowIcon(app_icon())
    _apply_theme(app)
    app.styleHints().colorSchemeChanged.connect(lambda _s: core.get_pref("theme", "auto") == "auto" and _apply_theme(app))
    app.setQuitOnLastWindowClosed(False)  # chiusa la finestra si resta nella tray

    message = f"{mode} {service_id or ''}".strip()
    if _forward(message):
        return 0
    _bridge = _Bridge()
    QLocalServer.removeServer(SERVER_NAME)  # socket rimasto da un'istanza terminata male
    server = QLocalServer()
    server.listen(SERVER_NAME)
    controller = Controller(app)

    def on_connection():
        conn = server.nextPendingConnection()
        conn.readyRead.connect(lambda: controller.handle(bytes(conn.readAll()).decode("utf-8", "replace").strip()))
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)
    controller.handle(message)
    return app.exec()
