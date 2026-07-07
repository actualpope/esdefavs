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
  if have kdialog; then
    kdialog --title "EmuDeck Favorites Sync" --menu "Hva vil du gjøre?" \
      on "Skru på automatisk sync" \
      off "Skru av automatisk sync" \
      status "Status og favoritter" \
      now "Oppdater Steam nå" \
      choose_srm "Velg SRM AppImage" \
      import "Importer stagede favoritter til Steam nå" \
      report "Lag feilsøkingsrapport" \
      quit "Lukk"
  elif have zenity; then
    zenity --title="EmuDeck Favorites Sync" --list --column=valg --column=handling --hide-column=1 \
      on "Skru på automatisk sync" \
      off "Skru av automatisk sync" \
      status "Status og favoritter" \
      now "Oppdater Steam nå" \
      choose_srm "Velg SRM AppImage" \
      import "Importer stagede favoritter til Steam nå" \
      report "Lag feilsøkingsrapport" \
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
    on)
      run_and_show "Autosync på" "$APP" autosync-on
      ;;
    off)
      run_and_show "Autosync av" "$APP" autosync-off
      ;;
    status)
      run_and_show "Status" "$APP" autosync-status
      ;;
    now)
      run_and_show "Oppdater Steam nå" "$APP" autosync-now
      ;;
    choose_srm)
      if have kdialog; then
        picked="$(kdialog --title "Velg Steam ROM Manager" --getopenfilename "${HOME}" "*.AppImage|AppImage-filer" || true)"
      elif have zenity; then
        picked="$(zenity --title="Velg Steam ROM Manager AppImage" --file-selection --filename="${HOME}/" || true)"
      else
        picked=""
      fi
      if [[ -n "${picked:-}" ]]; then
        run_and_show "SRM AppImage lagret" "$APP" set-srm-path "$picked"
      fi
      ;;
    import)
      run_and_show "Steam-import nå" "$APP" steam-import-now
      ;;
    report)
      run_and_show "Feilsøkingsrapport" "$APP" compatibility-report
      ;;
    quit|"")
      exit 0
      ;;
  esac
done
