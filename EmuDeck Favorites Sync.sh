#!/usr/bin/env bash
# Starts the EmuDeck Favorites Sync window.
set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${HOME}/.local/state/emudeck-favorites-sync/logs"
mkdir -p "$LOG_DIR"
export PYTHONPATH="${HERE}${PYTHONPATH:+:${PYTHONPATH}}"

message() {
  if command -v kdialog >/dev/null 2>&1; then
    kdialog --title "EmuDeck Favorites Sync" --error "$1"
  elif command -v zenity >/dev/null 2>&1; then
    zenity --title="EmuDeck Favorites Sync" --error --text="$1"
  else
    printf '%s\n' "$1" >&2
  fi
}

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  message "Python mangler tkinter, som programvinduet trenger."
  exit 1
fi

python3 -m emudeck_favorites_sync.gui 2>"$LOG_DIR/gui-errors.txt"
status=$?
if [[ "$status" -ne 0 ]]; then
  message "Programmet stoppet med en feil:

$(tail -n 15 "$LOG_DIR/gui-errors.txt")

Hele feilen ligger i ${LOG_DIR}/gui-errors.txt"
fi
exit "$status"
