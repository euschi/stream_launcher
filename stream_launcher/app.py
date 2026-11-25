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
    QToolButton,
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
# Tema scuro coerente con l'icona: fondo viola-nero, accento arancio -> magenta.
BG, SURFACE, SURFACE_HI, BORDER = "#141219", "#1e1b25", "#27232f", "#322d3c"
TEXT, MUTED, ACCENT, DANGER = "#efeaf5", "#9d95ab", "#ec4899", "#f87171"
ASSETS = core.ASSETS_DIR.as_posix()
GRADIENT = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9f1c, stop:1 #ec4899)"
STYLE = f"""
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget, QDialog {{ background: {BG}; }}
QLabel[role="title"] {{ font-size: 20px; font-weight: 700; }}
QLabel[role="name"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QFrame#row {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#row:hover {{ background: {SURFACE_HI}; border-color: #4a4257; }}
QFrame#banner {{ background: #2a1d17; border: 1px solid #ff9f1c; border-radius: 12px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{ background: transparent; border: none; }}
QListWidget#addons {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; }}
QListWidget#addons::item {{ padding: 4px 6px; border-radius: 6px; }}
QListWidget#addons::item:selected {{ background: {SURFACE_HI}; }}
QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px;
            margin-top: 30px; padding: 16px 14px 12px; font-size: 14px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 6px; top: 6px; color: {TEXT}; }}
QLineEdit, QComboBox {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
            padding: 7px 9px; font-weight: 400; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: #5d5669; border-color: #26222e; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{ASSETS}/chevron.svg"); width: 12px; height: 12px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid #4a4257; border-radius: 5px; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{ASSETS}/check.svg"); }}
QCheckBox::indicator:disabled {{ border-color: #2b2733; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_HI}; }}
QCheckBox {{ font-weight: 400; spacing: 8px; }}
QPushButton, QToolButton {{ background: {SURFACE_HI}; border: 1px solid {BORDER}; border-radius: 9px;
            padding: 7px 14px; font-weight: 600; }}
QPushButton:hover, QToolButton:hover {{ background: #312c3b; border-color: #4a4257; }}
QPushButton:disabled {{ color: #5d5669; background: {SURFACE}; }}
QPushButton[kind="accent"], QToolButton[kind="accent"] {{ background: {GRADIENT}; border: none; color: white; }}
QPushButton[kind="accent"]:hover, QToolButton[kind="accent"]:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffb347, stop:1 #f472b6); }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {MUTED}; }}
QPushButton[kind="ghost"]:hover {{ color: {TEXT}; background: {SURFACE_HI}; }}
QPushButton[kind="danger"] {{ background: transparent; border: 1px solid #5b2a33; color: {DANGER}; }}
QPushButton[kind="danger"]:hover {{ background: #3a1d24; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_HI}; }}
QStatusBar {{ background: {BG}; color: {MUTED}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def _apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")  # identico su Linux e Windows, e rispetta la palette
    pal = QPalette()
    for role, color in (
        (QPalette.Window, BG), (QPalette.Base, BG), (QPalette.AlternateBase, SURFACE),
        (QPalette.Button, SURFACE_HI), (QPalette.WindowText, TEXT), (QPalette.Text, TEXT),
        (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, SURFACE), (QPalette.ToolTipText, TEXT),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, "#6f6880"),
    ):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)


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
    def __init__(self, win: "Window"):
        super().__init__()
        self.win = win
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 8)
        layout.setSpacing(16)

        header, actions = _header("Stream Launcher", "Ogni servizio ha profilo, login, proxy e addon separati", app_icon())
        add = QToolButton(text="+  Aggiungi", popupMode=QToolButton.InstantPopup)
        add.setProperty("kind", "accent")
        add.setCursor(Qt.PointingHandCursor)
        menu = QMenu(add)
        for t in core.list_templates():
            menu.addAction(t.name, lambda tid=t.id: win.add_template(tid))
        add.setMenu(menu)
        actions.addWidget(add)
        layout.addLayout(header)

        self.banner = QFrame(objectName="banner")
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(14, 10, 10, 10)
        self.banner_label = _label("Runtime Firefox non installato: serve per avviare i servizi")
        self.banner_btn = _button("Installa", self._install_runtime, "accent")
        row.addWidget(self.banner_label, 1)
        row.addWidget(self.banner_btn)
        layout.addWidget(self.banner)

        self.list = QListWidget()
        self.list.setSpacing(5)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.itemClicked.connect(lambda it: win.open_editor(it.data(Qt.UserRole)))
        layout.addWidget(self.list, 1)
        self.refresh()

    def refresh(self) -> None:
        self.banner.setVisible(not core.runtime_installed())
        self.list.clear()
        services = core.list_services()
        if not services:
            self.list.addItem("Nessun servizio: aggiungine uno con «Aggiungi»")
        for svc in services:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, svc.id)
            self.list.addItem(item)
            row = QFrame(objectName="row")
            row.setAttribute(Qt.WA_Hover)
            row.setCursor(Qt.PointingHandCursor)
            row.setToolTip("Modifica")
            h = QHBoxLayout(row)
            h.setContentsMargins(14, 10, 12, 10)
            h.setSpacing(14)
            icon = QLabel()
            icon.setPixmap(service_icon(svc).pixmap(40, 40))
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(_label(svc.name, "name"))
            host = svc.url.split("://", 1)[-1].split("/", 1)[0]
            text.addWidget(_label(f"{host}  ·  {svc.proxy.describe()}", "muted"))
            h.addWidget(icon)
            h.addLayout(text, 1)
            h.addWidget(_button("▶  Avvia", lambda sid=svc.id: self.win.launch(sid), "accent"))
            h.addWidget(_button("Elimina", lambda s=svc: self.win.confirm_delete(s), "danger"))
            item.setSizeHint(row.sizeHint())
            self.list.setItemWidget(item, row)

    def _install_runtime(self) -> None:
        self.banner_btn.setEnabled(False)
        self.banner_label.setText("Scarico il runtime Firefox…")

        def done(msg: str) -> None:
            self.banner_btn.setEnabled(True)
            self.banner_label.setText("Runtime Firefox non installato: serve per avviare i servizi")
            self.win.toast(msg)
            self.refresh()

        run_async(
            lambda: f"Runtime Firefox {core.install_runtime()} installato",
            done,
            lambda e: done(f"Installazione non riuscita: {e}"),
        )


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

        for group in (self._service_group(), self._proxy_group(), self._password_group(), self._addon_group()):
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

    def _password_group(self) -> QGroupBox:
        from .passwords import default_browser, domains_for

        source = default_browser()
        browser = source.name if source else "browser predefinito"
        g = QGroupBox("Password")
        form = self._form(g)
        form.addRow(_note(f"A ogni avvio i login di questo servizio salvati in {browser} vengono "
                          "importati nel profilo (nuovi e password cambiate). Solo i domini indicati."))
        self.pw_switch = QCheckBox(f"Importa password da {browser}")
        self.pw_switch.setChecked(self.svc.passwords == "auto" and source is not None)
        self.pw_switch.setEnabled(source is not None)
        self.pw_switch.toggled.connect(self._sync_sensitivity)
        self.pw_domains = QLineEdit(", ".join(self.svc.login_domains))
        self.pw_domains.setPlaceholderText(f"vuoto = {', '.join(domains_for(self.svc.url))}")
        check = _button("Controlla login", self._check_passwords)
        check.setEnabled(source is not None)
        self.pw_result = _note("" if source else "Il browser predefinito non è Chrome, Edge, Chromium o Brave.")
        form.addRow(self.pw_switch)
        form.addRow("Domini", self.pw_domains)
        form.addRow(self._side_by_side(check, self.pw_result))
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
        if hasattr(self, "pw_switch"):
            self.pw_domains.setEnabled(self.pw_switch.isChecked())

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

    def _check_passwords(self) -> None:
        from .passwords import PasswordError, default_browser, read_logins

        svc = self._collect()
        self.pw_result.setText("Controllo…")

        def work():
            try:
                logins = read_logins(default_browser(), core.login_domains(svc))
            except PasswordError as e:
                return f"Errore: {e}"
            if not logins:
                return "Nessun login salvato per questi domini"
            # Mai le password: solo siti e utenti.
            people = sorted({f"{lg['username']} ({lg['origin'].split('://', 1)[1]})" for lg in logins})
            return f"{len(logins)} login: " + ", ".join(people)

        run_async(work, self.pw_result.setText, self.pw_result.setText)

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
            passwords=("auto" if self.pw_switch.isChecked() else "off") if self.pw_switch.isEnabled() else self.svc.passwords,
            login_domains=core.split_domains(self.pw_domains.text()),
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
        self.editor: Editor | None = None

    def toast(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

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

    def launch(self, service_id: str) -> None:
        if not core.runtime_installed():
            self.toast("Installa prima il runtime Firefox")
            return
        core.launch_detached(service_id)
        self.toast("Avvio in corso…")

    def add_template(self, template_id: str) -> None:
        self.toast("Aggiungo…")
        # Scarica l'icona: in background.
        run_async(lambda: core.add_from_template(template_id), lambda svc: self.open_editor(svc.id), self.toast)

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
        self.menu.addAction("Impostazioni…", self.show_settings)
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
        if not core.runtime_installed():
            self.show_settings()
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
