#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.local/share/emudeck-favorites-sync"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/Desktop"
STATE_DIR="${HOME}/.local/state/emudeck-favorites-sync"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SOURCE_DIR/emudeck_favorites_sync/__init__.py")"

command -v python3 >/dev/null 2>&1 || {
  echo "Feil: Python 3 ble ikke funnet." >&2
  exit 1
}

# Versions before 1.0 could run a background sync service. It is gone now.
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now emudeck-favorites-sync.timer >/dev/null 2>&1 || true
  systemctl --user disable --now emudeck-favorites-sync.service >/dev/null 2>&1 || true
  rm -f "${HOME}/.config/systemd/user/emudeck-favorites-sync.service" \
        "${HOME}/.config/systemd/user/emudeck-favorites-sync.timer"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
fi
rm -f "$STATE_DIR/autosync.json" "$STATE_DIR/autosync.log" "$STATE_DIR/last-srm-entries.json"

# Build the new installation next to the old one and swap it in at the end, so
# an older program window that is still running is not disturbed half-way.
mkdir -p "$(dirname "$INSTALL_DIR")" "$BIN_DIR"
STAGING="$(mktemp -d "${INSTALL_DIR}.new.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$SOURCE_DIR/emudeck_favorites_sync" "$STAGING/"
rm -rf "$STAGING/emudeck_favorites_sync/__pycache__"
install -m 0644 "$SOURCE_DIR/pyproject.toml" "$STAGING/pyproject.toml"
install -m 0644 "$SOURCE_DIR/assets/emudeck-favorites-sync.svg" "$STAGING/emudeck-favorites-sync.svg"
install -m 0755 "$SOURCE_DIR/EmuDeck Favorites Sync.sh" "$STAGING/EmuDeck Favorites Sync.sh"
install -m 0755 "$SOURCE_DIR/update.sh" "$STAGING/update.sh"
install -m 0755 "$SOURCE_DIR/uninstall.sh" "$STAGING/uninstall.sh"
if [[ -d "$SOURCE_DIR/.git" ]]; then
  # A git checkout: «Oppdater program» can then use git pull.
  printf '%s\n' "$SOURCE_DIR" > "$STAGING/source-dir.txt"
fi

cat > "$STAGING/EmuDeck Favorites Sync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=EmuDeck Favorites Sync
Comment=Legg spill fra rom-mappa inn i Steam via Steam ROM Manager
Exec=bash "${INSTALL_DIR}/EmuDeck Favorites Sync.sh"
Icon=${INSTALL_DIR}/emudeck-favorites-sync.svg
Terminal=false
Categories=Game;Utility;
EOF
chmod 0644 "$STAGING/EmuDeck Favorites Sync.desktop"

if [[ -e "$INSTALL_DIR" ]]; then
  OLD="${INSTALL_DIR}.old.$$"
  mv "$INSTALL_DIR" "$OLD"
  mv "$STAGING" "$INSTALL_DIR"
  rm -rf "$OLD"
else
  mv "$STAGING" "$INSTALL_DIR"
fi
trap - EXIT

LAUNCHER_TMP="$(mktemp "${BIN_DIR}/.emudeck-favorites-sync.XXXXXX")"
cat > "$LAUNCHER_TMP" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${HOME}/.local/share/emudeck-favorites-sync${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m emudeck_favorites_sync.cli "$@"
EOF
chmod 0755 "$LAUNCHER_TMP"
mv -f "$LAUNCHER_TMP" "$BIN_DIR/emudeck-favorites-sync"

if [[ -d "$DESKTOP_DIR" ]]; then
  install -m 0755 "$INSTALL_DIR/EmuDeck Favorites Sync.desktop" "$DESKTOP_DIR/EmuDeck Favorites Sync.desktop"
fi

echo "EmuDeck Favorites Sync ${VERSION} er installert."
echo
echo "VIKTIG: Lukk dette vinduet og programmet, og start «EmuDeck Favorites Sync»"
echo "på nytt fra skrivebordet for å få den nye versjonen."
