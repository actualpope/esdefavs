#!/usr/bin/env bash
set -euo pipefail

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now emudeck-favorites-sync.service >/dev/null 2>&1 || true
  rm -f "${HOME}/.config/systemd/user/emudeck-favorites-sync.service"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

rm -rf "${HOME}/.local/share/emudeck-favorites-sync"
rm -f "${HOME}/.local/bin/emudeck-favorites-sync"
echo "Program files removed. Scan state was kept in ~/.local/state/emudeck-favorites-sync."
