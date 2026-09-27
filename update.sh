#!/usr/bin/env bash
# Downloads the latest version from GitHub and installs it.
set -euo pipefail

ZIP_URL="https://github.com/actualpope/esdefavs/archive/refs/heads/main.zip"

command -v python3 >/dev/null 2>&1 || {
  echo "Feil: Python 3 trengs for å oppdatere, men ble ikke funnet."
  exit 2
}

TMPDIR_UPDATE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_UPDATE"' EXIT

python3 - "$ZIP_URL" "$TMPDIR_UPDATE" <<'PY'
import sys
import urllib.request
import zipfile
from pathlib import Path

url = sys.argv[1]
tmpdir = Path(sys.argv[2])
archive = tmpdir / "main.zip"
print(f"Laster ned {url}")
urllib.request.urlretrieve(url, archive)
with zipfile.ZipFile(archive) as zf:
    zf.extractall(tmpdir)
PY

EXTRACTED="$(find "$TMPDIR_UPDATE" -mindepth 2 -maxdepth 2 -type f -name install.sh -printf '%h\n' | sort | head -n 1)"
if [[ -z "$EXTRACTED" ]]; then
  echo "Fant ikke install.sh i den nedlastede ZIP-fila."
  exit 2
fi
echo "Installerer ny versjon …"
bash "$EXTRACTED/install.sh"
