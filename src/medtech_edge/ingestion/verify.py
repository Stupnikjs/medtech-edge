"""Contrôles d'intégrité des fichiers bruts."""
from __future__ import annotations

import zipfile
import zlib
from pathlib import Path
from typing import Any


class IntegrityError(RuntimeError):
    pass


def verify_zip(path: Path) -> dict[str, Any]:
    """Vérifie le zip (CRC de tout le contenu) et la forme openFDA du JSON interne."""
    try:
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if i.filename.endswith(".json")]
            if len(members) != 1:
                raise IntegrityError(f"{path.name} : 1 fichier .json attendu, {len(members)} trouvé(s)")
            bad = z.testzip()
            if bad is not None:
                raise IntegrityError(f"{path.name} : CRC invalide pour {bad}")
            with z.open(members[0]) as f:
                head = f.read(65536).decode("utf-8", "replace")
            if '"meta"' not in head or '"results"' not in head:
                raise IntegrityError(f"{path.name} : structure openFDA (meta/results) absente")
            return {"member": members[0].filename, "uncompressed_bytes": members[0].file_size}
    except (zipfile.BadZipFile, zlib.error, EOFError) as e:
        raise IntegrityError(f"{path.name} : archive illisible ({e})") from e
