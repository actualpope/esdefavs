#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.local/share/emudeck-favorites-sync"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/Desktop"
ICON_PATH="${INSTALL_DIR}/emudeck-favorites-sync.svg"

command -v python3 >/dev/null 2>&1 || {
  echo "Error: Python 3 is required but was not found." >&2
  exit 1
}

SERVICE_WAS_ACTIVE=0
TIMER_WAS_ACTIVE=0
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user is-active --quiet emudeck-favorites-sync.service >/dev/null 2>&1; then
    SERVICE_WAS_ACTIVE=1
  fi
  if systemctl --user is-active --quiet emudeck-favorites-sync.timer >/dev/null 2>&1; then
    TIMER_WAS_ACTIVE=1
  fi
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR"
rm -rf "$INSTALL_DIR/emudeck_favorites_sync"
cp -R "$SOURCE_DIR/emudeck_favorites_sync" "$INSTALL_DIR/"
install -m 0644 "$SOURCE_DIR/pyproject.toml" "$INSTALL_DIR/pyproject.toml"
install -m 0644 "$SOURCE_DIR/assets/emudeck-favorites-sync.svg" "$ICON_PATH"
install -m 0755 "$SOURCE_DIR/EmuDeck Favorites Sync.sh" "$INSTALL_DIR/EmuDeck Favorites Sync.sh"
install -m 0644 "$SOURCE_DIR/EmuDeck Favorites Sync.desktop" "$INSTALL_DIR/EmuDeck Favorites Sync.desktop"
install -m 0755 "$SOURCE_DIR/sync-on.sh" "$INSTALL_DIR/sync-on.sh"
install -m 0755 "$SOURCE_DIR/sync-off.sh" "$INSTALL_DIR/sync-off.sh"
install -m 0755 "$SOURCE_DIR/sync-status.sh" "$INSTALL_DIR/sync-status.sh"
install -m 0755 "$SOURCE_DIR/sync-now.sh" "$INSTALL_DIR/sync-now.sh"
install -m 0755 "$SOURCE_DIR/esde-closed.sh" "$INSTALL_DIR/esde-closed.sh"
install -m 0755 "$SOURCE_DIR/update.sh" "$INSTALL_DIR/update.sh"
printf '%s\n' "$SOURCE_DIR" > "$INSTALL_DIR/source-dir.txt"

cat > "$BIN_DIR/emudeck-favorites-sync" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${HOME}/.local/share/emudeck-favorites-sync${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m emudeck_favorites_sync.cli "$@"
EOF
chmod 0755 "$BIN_DIR/emudeck-favorites-sync"

cat > "$INSTALL_DIR/EmuDeck Favorites Sync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=EmuDeck Favorites Sync
Comment=Skru ES-DE til Steam-favorittsync på og av
Exec=bash "${INSTALL_DIR}/EmuDeck Favorites Sync.sh"
Icon=${ICON_PATH}
Terminal=false
Categories=Game;Utility;
EOF
chmod 0644 "$INSTALL_DIR/EmuDeck Favorites Sync.desktop"
install -m 0755 "$INSTALL_DIR/EmuDeck Favorites Sync.desktop" "$DESKTOP_DIR/EmuDeck Favorites Sync.desktop"

systemctl --user disable --now emudeck-favorites-sync.timer >/dev/null 2>&1 || true
systemctl --user disable --now emudeck-favorites-sync.service >/dev/null 2>&1 || true
"${BIN_DIR}/emudeck-favorites-sync" autosync-off >/dev/null 2>&1 || true
if [[ "$SERVICE_WAS_ACTIVE" -eq 1 || "$TIMER_WAS_ACTIVE" -eq 1 ]]; then
  echo "Background autosync was running and has been turned off; use 'Oppdater ES-DE favoritter' in the control panel instead."
fi

echo "Installed EmuDeck Favorites Sync."
echo
echo "Graphical control panel:"
echo "  ${INSTALL_DIR}/EmuDeck Favorites Sync.desktop"
echo "  ${DESKTOP_DIR}/EmuDeck Favorites Sync.desktop"
echo
echo "Usage:"
echo "  ${BIN_DIR}/emudeck-favorites-sync list-favorites"
echo "  ${BIN_DIR}/emudeck-favorites-sync autosync-now --summary"
echo
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo "Note: ${BIN_DIR} is not currently on PATH. Use the full command above,"
  echo "or restart your terminal and try: emudeck-favorites-sync list-favorites"
fi
