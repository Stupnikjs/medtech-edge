"""Stockage immuable : écriture atomique, lecture seule, manifeste append-only."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


def safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def publish_readonly(tmp: Path, final: Path) -> None:
    """Déplace `tmp` vers `final` sans jamais écraser, puis passe en lecture seule."""
    final.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(tmp, 0o444)
    try:
        os.link(tmp, final)  # atomique ; échoue si `final` existe déjà
    except FileExistsError:
        raise
    except OSError:  # système de fichiers sans liens physiques
        if final.exists():
            raise FileExistsError(final)
        os.replace(tmp, final)
    else:
        os.unlink(tmp)
    _fsync_dir(final.parent)


class Manifest:
    """Journal JSONL append-only : une ligne par fichier brut référencé."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError as e:
                    raise ValueError(f"Manifeste corrompu ligne {n} : {e}") from e
        return out

    def by_path(self) -> dict[str, dict[str, Any]]:
        return {r["path"]: r for r in self.records()}
