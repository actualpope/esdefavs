#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR"

command -v git >/dev/null 2>&1 || {
  echo "Error: git is required for GitHub updates but was not found."
  echo "Install git or update by downloading the repository manually."
  exit 2
}

if [[ -f "$SCRIPT_DIR/source-dir.txt" ]]; then
  RECORDED_SOURCE="$(<"$SCRIPT_DIR/source-dir.txt")"
  if [[ -n "$RECORDED_SOURCE" ]]; then
    SOURCE_DIR="$RECORDED_SOURCE"
  fi
fi

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  echo "Could not find the Git checkout used for updates:"
  echo "  $SOURCE_DIR"
  echo
  echo "Install from a Git clone first, for example:"
  echo "  git clone https://github.com/actualpope/esdefavs.git"
  echo "  cd esdefavs"
  echo "  bash install.sh"
  exit 2
fi

cd "$SOURCE_DIR"

git pull --ff-only
bash install.sh
