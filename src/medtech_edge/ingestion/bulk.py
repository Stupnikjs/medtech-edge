"""Ingestion par fichiers complets openFDA (download.json) vers data/raw, de façon idempotente."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .client import FdaClient, FetchError
from .config import ALLOWED_DOWNLOAD_HOSTS, DOWNLOAD_INDEX_URL, SOURCE, Endpoint, Settings
from .storage import Manifest, publish_readonly, safe_segment, sha256_file
from .verify import IntegrityError, verify_zip

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Partition:
    endpoint: Endpoint
    export_date: str
    url: str
    expected_records: int | None


@dataclass
class Result:
    partition: Partition
    status: str  # planned | downloaded | skipped | adopted | failed
    path: Path | None = None
    error: str | None = None


def _check_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise FetchError(f"URL refusée (hôte non autorisé) : {url}")
    name = safe_segment(Path(parsed.path).name)
    if not name.endswith(".json.zip"):
        raise FetchError(f"Nom de fichier inattendu : {url}")
    return name


def plan(client: FdaClient, endpoints: list[Endpoint], index_url: str = DOWNLOAD_INDEX_URL) -> list[Partition]:
    index = client.get_json(index_url)
    parts: list[Partition] = []
    for ep in endpoints:
        try:
            node = index["results"][ep.category][ep.key]
            export_date = str(node["export_date"])
            partitions = node["partitions"]
        except (KeyError, TypeError) as e:
            available = sorted(index.get("results", {}).get(ep.category, {})) if isinstance(index, dict) else []
            raise FetchError(f"Endpoint {ep.category}/{ep.key} absent de l'index (disponibles : {available})") from e
        for p in partitions:
            _check_url(p["file"])
            try:
                n = int(p.get("records"))
            except (TypeError, ValueError):
                n = None
            parts.append(Partition(ep, export_date, p["file"], n))
    return parts


def dest_for(settings: Settings, p: Partition) -> Path:
    return (settings.raw_dir / SOURCE / p.endpoint.category / p.endpoint.name
            / safe_segment(p.export_date) / _check_url(p.url))


def _record(settings: Settings, p: Partition, dest: Path, sha: str, info: dict, **extra) -> dict:
    return {
        "source": SOURCE,
        "endpoint": f"{p.endpoint.category}/{p.endpoint.name}",
        "export_date": p.export_date,
        "url": p.url,
        "path": dest.relative_to(settings.raw_dir).as_posix(),
        "sha256": sha,
        "bytes": dest.stat().st_size,
        "member": info["member"],
        "uncompressed_bytes": info["uncompressed_bytes"],
        "expected_records": p.expected_records,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    }


def fetch_partition(client: FdaClient, settings: Settings, manifest: Manifest,
                    known: dict[str, dict], p: Partition) -> Result:
    dest = dest_for(settings, p)
    rel = dest.relative_to(settings.raw_dir).as_posix()

    if dest.exists():  # snapshot déjà présent : on ne retélécharge ni n'écrase jamais
        info = verify_zip(dest)
        if rel in known:
            return Result(p, "skipped", dest)
        rec = _record(settings, p, dest, sha256_file(dest), info, adopted=True)
        manifest.append(rec)
        known[rel] = rec
        return Result(p, "adopted", dest)

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = settings.tmp_dir / f"{uuid.uuid4().hex}.part"
    try:
        sha, _ = client.download(p.url, tmp)
        info = verify_zip(tmp)
        publish_readonly(tmp, dest)
        rec = _record(settings, p, dest, sha, info)
        manifest.append(rec)
        known[rel] = rec
        return Result(p, "downloaded", dest)
    finally:
        tmp.unlink(missing_ok=True)


def run(client: FdaClient, settings: Settings, endpoints: list[Endpoint], dry_run: bool = False) -> list[Result]:
    parts = plan(client, endpoints)
    if dry_run:
        return [Result(p, "planned", dest_for(settings, p)) for p in parts]
    manifest = Manifest(settings.manifest_path)
    known = manifest.by_path()
    results: list[Result] = []
    for p in parts:
        try:
            r = fetch_partition(client, settings, manifest, known, p)
        except (FetchError, IntegrityError, OSError) as e:
            log.error("ÉCHEC %s : %s", p.url, e)
            r = Result(p, "failed", None, str(e))
        results.append(r)
    return results


def verify_all(settings: Settings) -> list[str]:
    """Recontrôle tous les fichiers du manifeste (présence, taille, sha256, zip) + orphelins."""
    problems: list[str] = []
    records = Manifest(settings.manifest_path).records()
    listed = set()
    for rec in records:
        listed.add(rec["path"])
        f = settings.raw_dir / rec["path"]
        if not f.exists():
            problems.append(f"MANQUANT : {rec['path']}")
        elif f.stat().st_size != rec["bytes"]:
            problems.append(f"TAILLE DIFFÉRENTE : {rec['path']}")
        elif sha256_file(f) != rec["sha256"]:
            problems.append(f"SHA256 DIFFÉRENT : {rec['path']}")
        else:
            try:
                verify_zip(f)
            except IntegrityError as e:
                problems.append(f"CORROMPU : {e}")
    for f in (settings.raw_dir / SOURCE).rglob("*.json.zip") if (settings.raw_dir / SOURCE).exists() else []:
        if f.relative_to(settings.raw_dir).as_posix() not in listed:
            problems.append(f"NON RÉFÉRENCÉ : {f.relative_to(settings.raw_dir).as_posix()}")
    return problems
