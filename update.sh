#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ZIP_URL="https://github.com/actualpope/esdefavs/archive/refs/heads/main.zip"

update_from_zip() {
  command -v python3 >/dev/null 2>&1 || {
    echo "Feil: Python 3 trengs for å oppdatere, men ble ikke funnet."
    exit 2
  }
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  python3 - "$ZIP_URL" "$tmpdir" <<'PY'
import sys
import urllib.request
import zipfile
from pathlib import Path

url = sys.argv[1]
tmpdir = Path(sys.argv[2])
archive = tmpdir / "esdefavs-main.zip"
print(f"Laster ned {url}")
urllib.request.urlretrieve(url, archive)
with zipfile.ZipFile(archive) as zf:
    zf.extractall(tmpdir)
PY
  local extracted=""
  while IFS= read -r candidate; do
    extracted="$(dirname "$candidate")"
    break
  done < <(find "$tmpdir" -mindepth 2 -maxdepth 2 -type f -name install.sh | sort)
  if [[ -z "$extracted" || ! -f "$extracted/install.sh" ]]; then
    echo "Fant ikke install.sh i den nedlastede ZIP-fila. Mapper i arkivet:"
    find "$tmpdir" -mindepth 1 -maxdepth 1 -type d -print | sort | sed 's/^/  /'
    exit 2
  fi
  echo "Installerer ny versjon …"
  bash "$extracted/install.sh"
}

SOURCE_DIR=""
if [[ -f "$SCRIPT_DIR/source-dir.txt" ]]; then
  SOURCE_DIR="$(<"$SCRIPT_DIR/source-dir.txt")"
elif [[ -d "$SCRIPT_DIR/.git" ]]; then
  SOURCE_DIR="$SCRIPT_DIR"
fi

if [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
  cd "$SOURCE_DIR"
  git pull --ff-only
  bash install.sh
else
  update_from_zip
fi
