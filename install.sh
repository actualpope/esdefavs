#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.local/share/srm-sync"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/Desktop"
STATE_DIR="${HOME}/.local/state/srm-sync"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SOURCE_DIR/srm_sync/__init__.py")"

# The program was called "EmuDeck Favorites Sync" before 1.4.
OLD_INSTALL_DIR="${HOME}/.local/share/emudeck-favorites-sync"
OLD_STATE_DIR="${HOME}/.local/state/emudeck-favorites-sync"

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

# Keep the saved game list, settings and backups under the new name.
if [[ -d "$OLD_STATE_DIR" && ! -L "$OLD_STATE_DIR" && ! -e "$STATE_DIR" ]]; then
  mkdir -p "$(dirname "$STATE_DIR")"
  mv "$OLD_STATE_DIR" "$STATE_DIR"
fi
rm -f "$STATE_DIR/autosync.json" "$STATE_DIR/autosync.log" "$STATE_DIR/last-srm-entries.json"

# Build the new installation next to the old one and swap it in at the end, so
# a program window that is still running is not disturbed half-way.
mkdir -p "$(dirname "$INSTALL_DIR")" "$BIN_DIR"
STAGING="$(mktemp -d "${INSTALL_DIR}.new.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$SOURCE_DIR/srm_sync" "$STAGING/"
rm -rf "$STAGING/srm_sync/__pycache__"
install -m 0644 "$SOURCE_DIR/pyproject.toml" "$STAGING/pyproject.toml"
install -m 0644 "$SOURCE_DIR/assets/srm-sync.svg" "$STAGING/srm-sync.svg"
install -m 0755 "$SOURCE_DIR/SRM Sync.sh" "$STAGING/SRM Sync.sh"
install -m 0755 "$SOURCE_DIR/update.sh" "$STAGING/update.sh"
install -m 0755 "$SOURCE_DIR/uninstall.sh" "$STAGING/uninstall.sh"

cat > "$STAGING/SRM Sync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SRM Sync
Comment=Legg spill fra rom-mappa inn i Steam via Steam ROM Manager
Exec=bash "${INSTALL_DIR}/SRM Sync.sh"
Icon=${INSTALL_DIR}/srm-sync.svg
Terminal=false
Categories=Game;Utility;
EOF
chmod 0644 "$STAGING/SRM Sync.desktop"

if [[ -e "$INSTALL_DIR" ]]; then
  OLD="${INSTALL_DIR}.old.$$"
  mv "$INSTALL_DIR" "$OLD"
  mv "$STAGING" "$INSTALL_DIR"
  rm -rf "$OLD"
else
  mv "$STAGING" "$INSTALL_DIR"
fi
trap - EXIT

LAUNCHER_TMP="$(mktemp "${BIN_DIR}/.srm-sync.XXXXXX")"
cat > "$LAUNCHER_TMP" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${HOME}/.local/share/srm-sync${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m srm_sync.cli "$@"
EOF
chmod 0755 "$LAUNCHER_TMP"
mv -f "$LAUNCHER_TMP" "$BIN_DIR/srm-sync"

if [[ -d "$DESKTOP_DIR" ]]; then
  install -m 0755 "$INSTALL_DIR/SRM Sync.desktop" "$DESKTOP_DIR/SRM Sync.desktop"
fi

# Remove the old name.
rm -rf "$OLD_INSTALL_DIR"
rm -f "$BIN_DIR/emudeck-favorites-sync" "$DESKTOP_DIR/EmuDeck Favorites Sync.desktop"

echo "SRM Sync ${VERSION} er installert."
