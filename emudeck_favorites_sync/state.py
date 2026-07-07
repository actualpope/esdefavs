from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import Manifest


def load_manifest(path: Path) -> Manifest | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = Manifest.from_dict(data)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(f"Invalid state file {path}: {error}") from error
    if manifest.schema_version != 1:
        raise ValueError(f"Unsupported schema version in {path}: {manifest.schema_version}")
    return manifest


def save_manifest_atomic(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest.to_dict(), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

