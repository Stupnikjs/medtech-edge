"""Configuration de l'ingestion openFDA (dispositifs médicaux)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOURCE = "openfda"
DOWNLOAD_INDEX_URL = "https://api.fda.gov/download.json"
# Seuls ces hôtes sont autorisés pour les fichiers (protège contre un index altéré).
ALLOWED_DOWNLOAD_HOSTS = frozenset({"download.open.fda.gov"})
USER_AGENT = "medtech-edge/0.1 (recherche personnelle)"


@dataclass(frozen=True)
class Endpoint:
    name: str         # nom local, utilisé dans l'arborescence
    category: str     # catégorie openFDA (ici "device")
    key: str          # clé correspondante dans download.json
    description: str


ENDPOINTS: dict[str, Endpoint] = {
    e.name: e
    for e in (
        Endpoint("510k", "device", "510k", "Autorisations 510(k)"),
        Endpoint("pma", "device", "pma", "Approbations PMA"),
        Endpoint("recall", "device", "recall", "Rappels de dispositifs"),
        Endpoint("enforcement", "device", "enforcement", "Rappels classés (enforcement)"),
        Endpoint("classification", "device", "classification", "Classification des produits"),
        Endpoint("registrationlisting", "device", "registrationlisting", "Registrations et listings"),
        Endpoint("event", "device", "event", "MAUDE : incidents déclarés (volumineux)"),
        Endpoint("udi", "device", "udi", "GUDID / UDI (volumineux)"),
    )
}

# Les jeux volumineux (event, udi, registrationlisting) sont à demander explicitement.
DEFAULT_ENDPOINTS = ("510k", "pma", "recall", "enforcement", "classification")


@dataclass(frozen=True)
class Settings:
    raw_dir: Path = Path("data/raw")
    max_retries: int = 6
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    timeout: float = 60.0
    chunk_size: int = 1 << 20
    min_interval: float = 0.5  # secondes minimum entre deux requêtes

    @property
    def manifest_path(self) -> Path:
        return self.raw_dir / "_manifest.jsonl"

    @property
    def tmp_dir(self) -> Path:
        return self.raw_dir / ".tmp"
