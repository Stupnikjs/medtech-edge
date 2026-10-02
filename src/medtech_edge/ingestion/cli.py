"""CLI : python -m medtech_edge.ingestion {list,fetch,verify}"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from . import bulk
from .client import FdaClient, FetchError
from .config import DEFAULT_ENDPOINTS, ENDPOINTS, Settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="medtech-edge-ingest", description=__doc__)
    p.add_argument("--raw-dir", type=Path, default=Settings().raw_dir)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("list", "Voir les exports disponibles côté FDA"),
                        ("fetch", "Télécharger vers data/raw")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--endpoints", nargs="+", choices=sorted(ENDPOINTS), default=list(DEFAULT_ENDPOINTS))
        if name == "fetch":
            s.add_argument("--dry-run", action="store_true", help="Planifier sans écrire")
    sub.add_parser("verify", help="Recontrôler l'intégrité de data/raw")
    return p


def main(argv: list[str] | None = None, client: FdaClient | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings(raw_dir=args.raw_dir)

    if args.cmd == "verify":
        problems = bulk.verify_all(settings)
        for line in problems:
            print(line)
        print("OK : intégrité confirmée" if not problems else f"{len(problems)} problème(s)")
        return 1 if problems else 0

    client = client or FdaClient(settings)
    endpoints = [ENDPOINTS[n] for n in args.endpoints]
    try:
        results = bulk.run(client, settings, endpoints, dry_run=(args.cmd == "list" or args.dry_run))
    except FetchError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 2

    if args.cmd == "list":
        seen = {}
        for r in results:
            seen.setdefault(r.partition.endpoint.name, [r.partition.export_date, 0])[1] += 1
        for name, (date, n) in seen.items():
            print(f"{name:20s} export {date}  {n} fichier(s)")
        return 0

    counts = Counter(r.status for r in results)
    print(", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "rien à faire")
    for r in results:
        if r.status == "failed":
            print(f"ÉCHEC {r.partition.url} : {r.error}", file=sys.stderr)
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
