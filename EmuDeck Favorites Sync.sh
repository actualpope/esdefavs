#!/usr/bin/env bash
set -euo pipefail

APP="${HOME}/.local/bin/emudeck-favorites-sync"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

have() {
  command -v "$1" >/dev/null 2>&1
}

message() {
  local title="$1"
  local text="$2"
  if have kdialog; then
    kdialog --title "$title" --msgbox "$text"
  elif have zenity; then
    zenity --title="$title" --info --text="$text"
  else
    printf '%s\n\n%s\n' "$title" "$text"
  fi
}

show_file() {
  local title="$1"
  local file="$2"
  if have kdialog; then
    kdialog --title "$title" --textbox "$file" 900 650
  elif have zenity; then
    zenity --title="$title" --text-info --filename="$file" --width=900 --height=650
  else
    cat "$file"
  fi
}

choose() {
  local title="EmuDeck Favorites Sync"
  local prompt="Hva vil du gjøre?"
  local version=""
  version="$("$APP" --version 2>/dev/null | sed 's/^emudeck-favorites-sync //')" || version=""
  if [[ -n "$version" ]]; then
    title="EmuDeck Favorites Sync ${version}"
    prompt="Versjon: ${version}

Hva vil du gjøre?"
  fi
  if have kdialog; then
    kdialog --title "$title" --menu "$prompt" \
      now "Oppdater ES-DE favoritter" \
      list "Se ES-DE Favoritter" \
      update "Oppdater program" \
      quit "Lukk"
  elif have zenity; then
    zenity --title="$title" --text="$prompt" --list --column=valg --column=handling --hide-column=1 \
      now "Oppdater ES-DE favoritter" \
      list "Se ES-DE Favoritter" \
      update "Oppdater program" \
      quit "Lukk"
  else
    echo "GUI-verktøy mangler. Installer kdialog/zenity eller bruk terminalkommandoene."
    return 1
  fi
}

run_and_show() {
  local title="$1"
  shift
  local output
  output="$(mktemp)"
  if "$@" >"$output" 2>&1; then
    show_file "$title" "$output"
  else
    show_file "$title - trenger sjekk" "$output"
  fi
  rm -f "$output"
}

if [[ ! -x "$APP" ]]; then
  if [[ -x "$HERE/install.sh" ]]; then
    if have kdialog; then
      kdialog --title "EmuDeck Favorites Sync" --yesno "Programmet er ikke installert ennå. Vil du installere det nå?"
      answer=$?
    elif have zenity; then
      zenity --title="EmuDeck Favorites Sync" --question --text="Programmet er ikke installert ennå. Vil du installere det nå?"
      answer=$?
    else
      answer=1
    fi
    if [[ "$answer" -eq 0 ]]; then
      run_and_show "Installerer" bash "$HERE/install.sh"
    fi
  fi
fi

if [[ ! -x "$APP" ]]; then
  message "EmuDeck Favorites Sync" "Programmet er ikke installert. Kjør bash install.sh fra programmappen."
  exit 1
fi

while true; do
  choice="$(choose || true)"
  case "$choice" in
    now)
      run_and_show "Oppdater ES-DE favoritter" "$APP" autosync-now --summary
      ;;
    list)
      run_and_show "ES-DE Favoritter" "$APP" list-favorites
      ;;
    update)
      run_and_show "Oppdater program" bash "$HERE/update.sh"
      ;;
    quit|"")
      exit 0
      ;;
  esac
done
