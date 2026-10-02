# medtech-edge

Outil perso de recherche : patterns entre données réglementaires medtech (FDA) et cours.

## Ingestion (étape 1)

Package `medtech_edge.ingestion` : télécharge les exports complets openFDA (`download.json`)
vers `data/raw/`, sans dépendance externe (Python ≥ 3.10).

```bash
export PYTHONPATH=src            # ou: pip install -e .
python -m medtech_edge.ingestion list                      # exports disponibles côté FDA
python -m medtech_edge.ingestion fetch --dry-run           # planifier sans écrire
python -m medtech_edge.ingestion fetch                     # 510k, pma, recall, enforcement, classification
python -m medtech_edge.ingestion fetch --endpoints event   # MAUDE (volumineux)
python -m medtech_edge.ingestion verify                    # recontrôle sha256 + zip de tout data/raw
python -m unittest discover -s tests -v
```

## Garanties « sanctuarisées »

- **Atomique** : téléchargement dans `data/raw/.tmp/`, vérification, puis publication sans écrasement.
- **Vérifié avant publication** : taille (Content-Length), CRC du zip, structure `meta`/`results`.
  Un fichier invalide n'atteint jamais `data/raw/`.
- **Immuable** : fichiers en lecture seule, un snapshot par date d'export
  (`openfda/device/<endpoint>/<export_date>/`), jamais retéléchargé ni modifié.
- **Traçable** : `data/raw/_manifest.jsonl` (append-only) : url, sha256, taille, date, nb d'enregistrements annoncé.
- **Robuste** : retries avec backoff exponentiel sur 429/5xx/timeouts, respect de `Retry-After`,
  hôtes de téléchargement en liste blanche, un échec n'arrête pas les autres fichiers (code retour 1).
- **Idempotent** : relancer est sans risque ; un fichier présent mais absent du manifeste est « adopté ».

## Limites connues

- Le nombre d'enregistrements annoncé par la FDA est stocké mais pas encore recompté dans le JSON.
- Structure de `download.json` supposée d'après la doc openFDA : lancer `list` en premier pour la confirmer.
