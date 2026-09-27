from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup(path: Path, backup_dir: Path) -> str | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / path.name
    index = 2
    while destination.exists():
        destination = backup_dir / f"{path.name}.{index}"
        index += 1
    if path.is_dir():
        shutil.copytree(path, destination)
    else:
        shutil.copy2(path, destination)
    return str(destination)
