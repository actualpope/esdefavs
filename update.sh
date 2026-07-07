#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR"
ZIP_URL="https://github.com/actualpope/esdefavs/archive/refs/heads/main.zip"

update_from_zip() {
  command -v python3 >/dev/null 2>&1 || {
    echo "Error: Python 3 is required for ZIP-based updates but was not found."
    exit 2
  }
  local tmpdir
  tmpdir="$(mktemp -d)"
  python3 - "$ZIP_URL" "$tmpdir" <<'PY'
from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

url = sys.argv[1]
tmpdir = Path(sys.argv[2])
archive = tmpdir / "esdefavs-main.zip"
print(f"Downloading {url}")
urllib.request.urlretrieve(url, archive)
with zipfile.ZipFile(archive) as zf:
    zf.extractall(tmpdir)
PY
  local extracted="$tmpdir/esdefavs-main"
  if [[ ! -x "$extracted/install.sh" ]]; then
    echo "Downloaded ZIP did not contain install.sh where expected:"
    echo "  $extracted/install.sh"
    exit 2
  fi
  echo "Installing downloaded GitHub ZIP..."
  bash "$extracted/install.sh"
}

if [[ -f "$SCRIPT_DIR/source-dir.txt" ]]; then
  RECORDED_SOURCE="$(<"$SCRIPT_DIR/source-dir.txt")"
  if [[ -n "$RECORDED_SOURCE" ]]; then
    SOURCE_DIR="$RECORDED_SOURCE"
  fi
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git was not found; using ZIP-based GitHub update instead."
  update_from_zip
  exit 0
fi

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  echo "Could not find the Git checkout used for updates; using ZIP-based GitHub update instead."
  echo "  $SOURCE_DIR"
  update_from_zip
  exit 0
fi

cd "$SOURCE_DIR"

git pull --ff-only
bash install.sh
