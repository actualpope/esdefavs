#!/usr/bin/env bash
set -euo pipefail

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now emudeck-favorites-sync.timer >/dev/null 2>&1 || true
  systemctl --user disable --now emudeck-favorites-sync.service >/dev/null 2>&1 || true
  rm -f "${HOME}/.config/systemd/user/emudeck-favorites-sync.service" \
        "${HOME}/.config/systemd/user/emudeck-favorites-sync.timer"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

rm -rf "${HOME}/.local/share/emudeck-favorites-sync"
rm -f "${HOME}/.local/bin/emudeck-favorites-sync" "${HOME}/Desktop/EmuDeck Favorites Sync.desktop"
echo "Programfilene er fjernet. Spillene i Steam og SRM er ikke rørt."
echo "Lagret tilstand og sikkerhetskopier ligger fortsatt i ~/.local/state/emudeck-favorites-sync."
