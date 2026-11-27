# Stream Launcher

Launcher per servizi di streaming su Linux e Windows (Netflix, Disney+, Prime Video,
Paramount+, …) basato sul motore di Firefox (Gecko), senza bisogno di avere
Firefox installato.

Su Chrome/PWA con GPU NVIDIA e Wayland i video DRM restano neri (o, togliendo
l'accelerazione, la qualità crolla); Firefox invece li riproduce correttamente.
Il progetto usa un **Firefox ESR portatile e isolato** come runtime condiviso e
dà a ogni servizio:

- un **profilo separato** (login, cookie, impostazioni indipendenti);
- un **proxy** facoltativo (HTTP, HTTPS, SOCKS5, SOCKS4, con o senza credenziali);
- **addon** da addons.mozilla.org, dal Chrome Web Store o da file locali;
- uno **user agent** a scelta (predefinito, Firefox su Windows, personalizzato);
- la sua voce nel menu applicazioni e nella **tray**.

I servizi si possono **duplicare con un nome a scelta**, ad esempio un Paramount+
italiano e uno «Paramount+ USA» con proxy USA, o due account dello stesso servizio.

### Servizi inclusi

| Categoria | Servizi |
|---|---|
| Abbonamento | Netflix, Prime Video, Disney+, NOW, Sky Go, HBO Max, Apple TV, Paramount+, TIMVISION, MUBI, Crunchyroll |
| Sport | DAZN |
| Gratuiti / TV | RaiPlay, Mediaset Infinity, La7, Pluto TV, YouTube |
| Noleggio | Chili, Rakuten TV |

Sono un **catalogo**: l'installazione non crea nessun servizio. Nella finestra,
in cima ci sono «I tuoi servizi» e sotto il catalogo con **Installa**; il primo
servizio installato scarica anche il motore Firefox. Da terminale:
`stream-launcher templates` e `stream-launcher add <modello>`.

Se un servizio dice che il browser o il sistema non è supportato, imposta lo
user agent «Firefox su Windows».

## Installazione

```bash
./install.sh              # installa l'app (servizi e runtime si scelgono poi dal catalogo)
./install.sh --update     # riscarica anche il runtime Firefox
./install.sh --uninstall  # rimuove app, runtime, profili, voci di menu e tray
```

Su **Arch Linux** conviene il pacchetto, così pacman lo conosce e porta da solo
le dipendenze (si costruisce dal ramo `main`):

```bash
cd packaging/arch && makepkg -si     # installa o aggiorna
sudo pacman -R stream-launcher       # rimuove (dati e configurazione restano nella home)
```

Le altre distribuzioni usano `install.sh`. Requisiti: `python3` e `PySide6` (finestra e tray; Arch: `sudo pacman -S pyside6`)
e, su GNOME, l'estensione AppIndicator per la tray. Senza PySide6 `install.sh` si
ferma senza toccare l'installazione esistente.

### Windows

Scarica `StreamLauncher-Setup-<versione>.exe` dagli artifact della action
**Windows** (o dalle release, per le versioni taggate) e installalo: non servono
Python né permessi di amministratore. L'installer è leggero: mette il launcher
nel menu Start (cartella *Stream Launcher*) e avvia la tray anche al login; i
servizi si installano dal catalogo e ognuno ha la sua voce nel menu Start. Le
finestre di un servizio compaiono nella barra delle applicazioni con il suo nome
e la sua icona, raggruppate a parte, e si possono aggiungere alla barra. La disinstallazione toglie app, runtime e profili;
la configurazione in `%APPDATA%\stream-launcher` resta.

Da un prompt (cmd o PowerShell) si usa `stream-launcher-cli`, che accetta gli
stessi comandi della versione Linux, ad esempio `stream-launcher-cli list` o il
menu interattivo `stream-launcher-cli menu`. L'installer mette la sua cartella nel
PATH dell'utente (vale per i prompt aperti dopo l'installazione) e la toglie alla
disinstallazione. `StreamLauncher.exe` è la versione con finestra: il prompt non
aspetta che finisca, quindi non va bene per il menu. I login che il browser ha cifrato con la
cifratura legata all'app (prefisso `v20`) vengono saltati.

## Uso

- **Tray**: il menu dell'icona elenca i servizi; «Impostazioni…» apre la gestione.
  Su Windows basta il clic sinistro, su Linux il clic sinistro apre le impostazioni
  e il menu è sul tasto destro. La tray parte da sola al login.
- **Menu applicazioni**: ogni servizio ha la sua voce; tasto destro → *Impostazioni*.
- **Stream Launcher** (menu) apre la finestra: installa dal catalogo, avvia,
  modifica nome, indirizzo, user agent, proxy e addon, duplica o elimina.
  **Impostazioni**: tema (scuro, chiaro o come il sistema, applicato anche ai
  dialoghi di Firefox: prompt, login, salvataggio password, tendine), avvio con
  il sistema, cosa fare chiudendo la finestra o avviando un servizio, canale e
  aggiornamento del motore Firefox, password (vedi sotto).

**Menu nel terminale**: `stream-launcher menu` (o `stream-launcher m`; su Windows
`stream-launcher-cli menu`) elenca i servizi con un numero. Scrivi il numero o
parte del nome (es. `2` o `para`) e il servizio parte in background; `a` installa
dal catalogo, `q` esce.

```text
Stream Launcher 1.03.00

  1  Netflix
  2  Paramount+
  3  Prime Video

  a  installa dal catalogo
  q  esci

Avvia (numero o nome):
```

Da terminale:

```bash
stream-launcher menu
stream-launcher list
stream-launcher run paramountplus
stream-launcher duplicate paramountplus --name "Paramount+ USA" [--copy-profile]
stream-launcher rename netflix "Netflix famiglia"
stream-launcher proxy paramountplus --type socks5 --host 1.2.3.4 --port 1080 --user io
stream-launcher proxy paramountplus --off
stream-launcher proxy-test paramountplus            # IP/paese di uscita, il servizio risponde?
stream-launcher storage move /var/lib/stream-launcher  # runtime e profili su un disco veloce
stream-launcher useragent primevideo windows        # default | windows | "stringa"
stream-launcher addon netflix add amo:ublock-origin
stream-launcher addon netflix add chrome:<id-di-32-lettere>
stream-launcher addon netflix add ~/addon/mio.crx    # anche .xpi o cartella
stream-launcher addon netflix update                 # riscarica gli addon
stream-launcher runtime info | install [--channel esr|release]
stream-launcher passwords import [--browser microsoft-edge]  # login dei servizi nell'archivio
stream-launcher passwords list | clear
```

Le modifiche valgono dal prossimo avvio del servizio.

## Dove stanno i file

| Percorso | Contenuto |
|---|---|
| `~/.config/stream-launcher/services/<id>.ini` | configurazione del servizio (permessi 600: contiene la password del proxy) |
| `~/.local/share/stream-launcher/firefox/` | runtime Firefox ESR condiviso |
| `~/.local/share/stream-launcher/profiles/<id>/` | profilo Firefox del servizio |
| `~/.local/share/stream-launcher/app/` | copia installata dell'applicazione |
| `~/.local/share/stream-launcher/launch.log` | log degli avvii fatti da tray/impostazioni |

Su Windows: configurazione in `%APPDATA%\stream-launcher`, runtime, profili e
log in `%LOCALAPPDATA%\stream-launcher`, app in `%LOCALAPPDATA%\Programs\Stream Launcher`.

## Struttura del repo

| File | Ruolo |
|---|---|
| `install.sh` | copia l'app, symlink in `~/.local/bin`, `init`, autostart della tray |
| `bin/stream-launcher` | punto d'ingresso (CLI) |
| `stream_launcher/core.py` | servizi, runtime, profili, proxy, addon, voci di menu |
| `stream_launcher/cli.py` | comandi da terminale |
| `stream_launcher/app.py` | finestra impostazioni e tray (Qt/PySide6, un solo processo per utente) |
| `windows/` | installer Inno Setup, icona `.ico`, test su Windows reale |
| `.github/workflows/windows.yml` | test, eseguibile (PyInstaller) e installer per Windows |
| `templates/*.ini` | modelli dei servizi predefiniti |
| `config/user.js` | preferenze comuni dei profili (DRM, WebRender, addon) |
| `config/policies.json` | policy del runtime: telemetria off, DRM sempre attivo |
| `config/chrome/userChrome.css` | nasconde schede e barre |
| `config/proxy-extension/` | estensione generata per proxy con credenziali / HTTPS |
| `config/autoconfig/` | script di avvio del runtime: importa le password nel profilo |
| `stream_launcher/passwords.py` | lettura e decifratura dei login di Chrome/Chromium/Brave |
| `stream_launcher/proxytest.py` | test di connessione del proxy |

Le icone dei servizi (marchi registrati) non sono nel repo: vengono scaricate
all'installazione dai siti dei servizi.

## Note

- **Qualità**: su Linux c'è solo Widevine L3, quindi niente 4K/HDR; il tetto
  (di solito 720p–1080p) lo decide il servizio. Lo user agent «Firefox su
  Windows» a volte alza la risoluzione concessa: prova servizio per servizio.
- **Proxy**: se il proxy non risponde il traffico si blocca, non esce in chiaro.
  DNS e WebRTC passano dal proxy. Le credenziali e il tipo HTTPS usano
  un'estensione interna e richiedono il runtime ESR. **Prova connessione**
  (finestra impostazioni) o `proxy-test` verificano con lo stesso protocollo di
  Firefox IP e paese di uscita (via ipinfo.io) e che il sito del servizio risponda.
- **Password**: si importano una volta, per tutto il launcher, dalla pagina
  **Password** (o `passwords import`) scegliendo il browser: Chrome, Edge,
  Chromium o Brave. Vengono letti solo i login dei siti dei servizi (installati
  e del catalogo, con gli alias noti: Prime Video usa anche amazon.it e
  amazon.com) e salvati in un archivio del launcher, nel portachiavi GNOME su
  Linux o in un file cifrato con DPAPI su Windows. A ogni avvio, ogni servizio
  riceve i login del suo sito: Firefox li legge da un file con permessi 600 che
  viene cancellato appena importato. Ogni voce dell'archivio si può modificare,
  eliminare o aggiungere a mano; ripetere l'import aggiorna dal browser senza
  annullare queste correzioni. `passwords list` mostra siti e utenti, mai le password.
- **"L'applicazione non risponde"**: succede se runtime e profili stanno su un
  disco lento o saturo (HDD, trim in corso). Spostali su SSD con `storage move`.
- **Addon Chrome**: vengono convertiti al volo (id Gecko, service worker →
  background script). Le API Chrome più esotiche possono non funzionare in Firefox.
  Gli addon non firmati funzionano solo con il runtime ESR (predefinito).
- **Duplicati**: senza `--copy-profile` (o la casella nella finestra) il
  duplicato parte senza login.
- Al primo avvio di un servizio Firefox scarica Widevine in background: se il
  video non parte subito, attendi un minuto e riapri.
- Indietro: **Alt+←**. Schermo intero: **F11**.
