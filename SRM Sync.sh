#!/usr/bin/env bash
# Starts the SRM Sync window.
set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${HOME}/.local/state/srm-sync"
OLD_STATE_DIR="${HOME}/.local/state/emudeck-favorites-sync"
if [[ -d "$OLD_STATE_DIR" && ! -L "$OLD_STATE_DIR" && ! -e "$STATE_DIR" ]]; then
  mkdir -p "$(dirname "$STATE_DIR")"
  mv "$OLD_STATE_DIR" "$STATE_DIR"
fi
LOG_DIR="${STATE_DIR}/logs"
mkdir -p "$LOG_DIR"
export PYTHONPATH="${HERE}${PYTHONPATH:+:${PYTHONPATH}}"

message() {
  if command -v kdialog >/dev/null 2>&1; then
    kdialog --title "SRM Sync" --error "$1"
  elif command -v zenity >/dev/null 2>&1; then
    zenity --title="SRM Sync" --error --text="$1"
  else
    printf '%s\n' "$1" >&2
  fi
}

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  message "Python mangler tkinter, som programvinduet trenger."
  exit 1
fi

python3 -m srm_sync.gui 2>"$LOG_DIR/gui-errors.txt"
status=$?
if [[ "$status" -ne 0 ]]; then
  message "Programmet stoppet med en feil:

$(tail -n 15 "$LOG_DIR/gui-errors.txt")

Hele feilen ligger i ${LOG_DIR}/gui-errors.txt"
fi
exit "$status"
