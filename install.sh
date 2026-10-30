#!/usr/bin/env bash
# Scarica il runtime Firefox portatile, prepara il profilo dedicato
# e registra il client Netflix nel menu applicazioni.
#
#   ./install.sh            installa / aggiorna la configurazione
#   ./install.sh --update   riscarica anche il runtime Firefox
#   ./install.sh --uninstall
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/netflix-client"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
FIREFOX_URL="https://download.mozilla.org/?product=firefox-latest-ssl&os=linux64&lang=it"
NETFLIX_ICON_URL="https://assets.nflxext.com/us/ffe/siteui/common/icons/nficon2016.png"

uninstall() {
  rm -rf "$DATA_DIR"
  rm -f "$BIN_DIR/netflix-client" "$APPS_DIR/netflix-client.desktop" "$ICONS_DIR/netflix-client.svg"
  echo "Client Netflix rimosso."
}

install_runtime() {
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  echo "Scarico il runtime Firefox..."
  curl -fL --progress-bar -o "$tmp/firefox.tar" "$FIREFOX_URL"
  tar -xf "$tmp/firefox.tar" -C "$tmp"
  rm -rf "$DATA_DIR/firefox"
  mv "$tmp/firefox" "$DATA_DIR/firefox"
}

case "${1:-}" in
  --uninstall) uninstall; exit 0 ;;
esac

mkdir -p "$DATA_DIR/profile/chrome" "$BIN_DIR" "$APPS_DIR" "$ICONS_DIR"

if [[ "${1:-}" == "--update" || ! -x "$DATA_DIR/firefox/firefox" ]]; then
  install_runtime
fi

# Policy aziendali del runtime (telemetria off, DRM sempre attivo, niente onboarding).
mkdir -p "$DATA_DIR/firefox/distribution"
cp "$SRC_DIR/config/policies.json" "$DATA_DIR/firefox/distribution/policies.json"

# Profilo dedicato.
cp "$SRC_DIR/config/user.js" "$DATA_DIR/profile/user.js"
cp "$SRC_DIR/config/chrome/userChrome.css" "$DATA_DIR/profile/chrome/userChrome.css"

# Launcher e icona: il logo Netflix (marchio registrato) non è nel repo,
# viene scaricato dai server Netflix; se non è raggiungibile resta l'icona generica.
install -m 755 "$SRC_DIR/netflix-client" "$BIN_DIR/netflix-client"
cp "$SRC_DIR/config/icon.svg" "$ICONS_DIR/netflix-client.svg"
ICON="$ICONS_DIR/netflix-client.svg"
if curl -fsSL -o "$DATA_DIR/icon.png" "$NETFLIX_ICON_URL"; then
  ICON="$DATA_DIR/icon.png"
fi

cat > "$APPS_DIR/netflix-client.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Netflix
Comment=Client Netflix su motore Firefox
Exec=$BIN_DIR/netflix-client
Icon=$ICON
Terminal=false
Categories=AudioVideo;Video;Player;
StartupWMClass=NetflixClient
EOF

update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
gtk-update-icon-cache -q "${ICONS_DIR%/scalable/apps}" >/dev/null 2>&1 || true

echo "Installato. Avvia \"Netflix\" dal menu applicazioni oppure con: netflix-client"
