#!/usr/bin/env bash
# Installa Stream Launcher: copia l'applicazione, scarica il runtime Firefox
# portatile, crea i servizi predefiniti, le voci nel menu applicazioni e
# l'icona nella tray (avviata anche al login).
#
#   ./install.sh                 installa / aggiorna l'applicazione
#   ./install.sh --update        riscarica anche il runtime Firefox
#   ./install.sh --uninstall     rimuove applicazione, runtime, profili, voci di menu e tray
#                                (la configurazione in ~/.config/stream-launcher resta)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_DIR="$DATA_HOME/stream-launcher"
APP_DIR="$DATA_DIR/app"
APPS_DIR="$DATA_HOME/applications"
AUTOSTART="$CONFIG_HOME/autostart/stream-launcher-tray.desktop"
BIN_LINK="$HOME/.local/bin/stream-launcher"

remove_legacy() {
  # Vecchio client solo-Netflix: tolgo launcher e voce di menu, i dati restano.
  local found=0
  for f in "$HOME/.local/bin/netflix-client" "$APPS_DIR/netflix-client.desktop" \
           "$DATA_HOME/icons/hicolor/scalable/apps/netflix-client.svg"; do
    [[ -e "$f" ]] && { rm -f "$f"; found=1; }
  done
  if [[ $found == 1 && -d "$DATA_HOME/netflix-client" ]]; then
    echo "Rimosso il vecchio launcher Netflix. I suoi dati sono ancora in $DATA_HOME/netflix-client:"
    echo "  cancellali con «rm -rf $DATA_HOME/netflix-client» quando hai verificato il nuovo."
  fi
}

stop_tray() {
  pkill -f "$APP_DIR/bin/stream-launcher tray" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  stop_tray
  # Runtime e profili possono essere stati spostati (stream-launcher storage move):
  # lì tolgo solo le cartelle create dal launcher, non la cartella scelta dall'utente.
  if [[ -x "$BIN_LINK" ]]; then
    storage="$("$BIN_LINK" storage info 2>/dev/null | sed 's/^[^:]*: //')"
    if [[ -n "$storage" && "$storage" != "$DATA_DIR" ]]; then
      rm -rf "$storage/firefox" "$storage/profiles" "$storage/addons-cache"
    fi
  fi
  rm -rf "$DATA_DIR"
  rm -f "$BIN_LINK" "$AUTOSTART" "$APPS_DIR"/stream-launcher.desktop "$APPS_DIR"/stream-launcher-*.desktop
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
  echo "Stream Launcher rimosso (configurazione conservata in $CONFIG_HOME/stream-launcher)."
  exit 0
fi

command -v python3 >/dev/null || { echo "Serve python3." >&2; exit 1; }
if ! python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null; then
  echo "Attenzione: GTK4/libadwaita per Python non trovati: niente finestra Impostazioni"
  echo "  (Arch: sudo pacman -S python-gobject libadwaita). La riga di comando funziona comunque."
fi
if ! python3 -c 'import gi; gi.require_version("AyatanaAppIndicator3","0.1")' 2>/dev/null; then
  echo "Attenzione: libayatana-appindicator non trovata: niente icona nella tray"
  echo "  (Arch: sudo pacman -S libayatana-appindicator; su GNOME serve l'estensione AppIndicator)."
fi

# Copia dell'applicazione: le voci di menu puntano qui, non al repo.
stop_tray
mkdir -p "$DATA_DIR" "$(dirname "$BIN_LINK")" "$(dirname "$AUTOSTART")"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/stream_launcher" "$SRC_DIR/config" "$SRC_DIR/templates" "$SRC_DIR/bin" "$APP_DIR/"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$APP_DIR/bin/stream-launcher"
ln -sfn "$APP_DIR/bin/stream-launcher" "$BIN_LINK"

remove_legacy

if [[ "${1:-}" == "--update" ]]; then
  "$BIN_LINK" init --update-runtime
else
  "$BIN_LINK" init
fi

cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Stream Launcher (tray)
Comment=Icona nella tray con i servizi di streaming
Exec=$APP_DIR/bin/stream-launcher tray
Icon=$APP_DIR/config/icon.svg
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

# Avvia subito la tray, staccata dal terminale.
setsid -f "$APP_DIR/bin/stream-launcher" tray >/dev/null 2>&1 < /dev/null || true

echo
echo "Installato. Trovi i servizi nell'icona della tray e nel menu applicazioni;"
echo "«Stream Launcher» apre le impostazioni. Da terminale:"
echo "  stream-launcher list | run <id> | duplicate <id> --name \"Nome\" | proxy <id> ..."
