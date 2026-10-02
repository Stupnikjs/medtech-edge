#!/usr/bin/env python3
"""Concatène tous les fichiers texte du projet dans un seul fichier forllm.txt.

Usage (depuis la racine du projet) :
    python concat_for_llm.py                 # écrit forllm.txt à la racine
    python concat_for_llm.py -o autre.txt    # autre fichier de sortie
    python concat_for_llm.py --max-kb 500    # ignore les fichiers > 500 Ko

Le fichier généré contient un arbre des fichiers retenus, puis le contenu de
chacun précédé d'un en-tête avec son chemin relatif.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUTPUT_NAME = "forllm.txt"

# Dossiers entièrement ignorés (par nom, à n'importe quelle profondeur).
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
    "dist", "build", ".tox", ".eggs",
}
# Dossiers ignorés par chemin relatif (données volumineuses, non pertinentes).
EXCLUDED_PATHS = {"data"}
# Suffixes de dossiers ignorés (ex. medtech_edge.egg-info).
EXCLUDED_DIR_SUFFIXES = (".egg-info",)

# Fichiers ignorés par nom ou extension.
EXCLUDED_FILES = {".DS_Store", ".gitkeep", "uv.lock", "poetry.lock", "package-lock.json"}
EXCLUDED_EXTS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".db", ".sqlite", ".sqlite3",
    ".zip", ".gz", ".tar", ".7z", ".png", ".jpg", ".jpeg", ".gif", ".pdf",
    ".parquet", ".csv", ".json.gz",
}


def is_excluded_dir(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if path.name in EXCLUDED_DIRS or path.name.endswith(EXCLUDED_DIR_SUFFIXES):
        return True
    return rel.as_posix() in EXCLUDED_PATHS


def collect_files(root: Path, skip: set[Path]) -> list[Path]:
    files: list[Path] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if not is_excluded_dir(entry, root):
                    walk(entry)
            elif entry.is_file():
                if (
                    entry.name in EXCLUDED_FILES
                    or entry.suffix.lower() in EXCLUDED_EXTS
                    or entry.resolve() in skip
                ):
                    continue
                files.append(entry)

    walk(root)
    return files


def read_text(path: Path, max_bytes: int) -> str | None:
    """Retourne le texte du fichier, ou None s'il est binaire / trop gros."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def build_tree(root: Path, files: list[Path]) -> str:
    lines = [f"{root.name}/"]
    for f in files:
        lines.append(f"  {f.relative_to(root).as_posix()}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-r", "--root", default=None, help="racine du projet (défaut : dossier de ce script)")
    parser.add_argument("-o", "--output", default=OUTPUT_NAME, help=f"fichier de sortie (défaut : {OUTPUT_NAME})")
    parser.add_argument("--max-kb", type=int, default=300, help="taille max d'un fichier en Ko (défaut : 300)")
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output

    skip = {output.resolve(), Path(__file__).resolve()}
    max_bytes = args.max_kb * 1024

    contents: list[tuple[Path, str]] = []
    skipped: list[Path] = []
    for f in collect_files(root, skip):
        text = read_text(f, max_bytes)
        if text is None:
            skipped.append(f)
        else:
            contents.append((f, text))

    parts = [
        f"# Projet : {root.name}",
        f"# {len(contents)} fichiers concaténés",
        "",
        "## Arborescence",
        build_tree(root, [f for f, _ in contents]),
        "",
    ]
    for f, text in contents:
        rel = f.relative_to(root).as_posix()
        parts += [
            "=" * 80,
            f"FILE: {rel}",
            "=" * 80,
            text.rstrip("\n"),
            "",
        ]

    output.write_text("\n".join(parts), encoding="utf-8")

    total_kb = output.stat().st_size / 1024
    print(f"{len(contents)} fichiers -> {output} ({total_kb:.1f} Ko)")
    for f in skipped:
        print(f"  ignoré (binaire ou > {args.max_kb} Ko) : {f.relative_to(root).as_posix()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())