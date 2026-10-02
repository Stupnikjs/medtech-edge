"""Client HTTP (bibliothèque standard) avec retries, backoff et contrôle d'intégrité."""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import USER_AGENT, Settings

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """Échec définitif d'un téléchargement (après retries éventuels)."""


class _Retryable(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


def _retry_after(headers: Any) -> float | None:
    try:
        value = headers.get("Retry-After") if headers else None
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class FdaClient:
    def __init__(
        self,
        settings: Settings,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.s = settings
        self._open = opener or urllib.request.build_opener().open
        self._sleep = sleep
        self._last = 0.0

    # -- internes ---------------------------------------------------------
    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )

    def _throttle(self) -> None:
        wait = self.s.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def _with_retries(self, url: str, action: Callable[[], Any]) -> Any:
        last: Exception | None = None
        for attempt in range(1, self.s.max_retries + 1):
            self._throttle()
            hint: float | None = None
            try:
                return action()
            except _Retryable as e:
                last, hint = e, e.retry_after
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_STATUS:
                    raise FetchError(f"HTTP {e.code} (non réessayable) sur {url}") from e
                last, hint = e, _retry_after(e.headers)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
                last = e
            if attempt == self.s.max_retries:
                break
            delay = min(self.s.backoff_max, self.s.backoff_base**attempt) * random.uniform(0.5, 1.0)
            if hint is not None:
                delay = max(delay, min(hint, self.s.backoff_max))
            log.warning("Tentative %d/%d échouée (%s), nouvel essai dans %.1fs : %s",
                        attempt, self.s.max_retries, last, delay, url)
            self._sleep(delay)
        raise FetchError(f"Échec après {self.s.max_retries} tentatives : {url} ({last})") from last

    # -- API publique -----------------------------------------------------
    def get_json(self, url: str) -> Any:
        def action() -> Any:
            with self._open(self._request(url), timeout=self.s.timeout) as r:
                raw = r.read()
            try:
                return json.loads(raw)
            except ValueError as e:
                raise _Retryable(f"JSON invalide : {e}") from e

        return self._with_retries(url, action)

    def download(self, url: str, dest: Path) -> tuple[str, int]:
        """Télécharge vers `dest` (écrasé à chaque tentative). Retourne (sha256, taille)."""
        dest.parent.mkdir(parents=True, exist_ok=True)

        def action() -> tuple[str, int]:
            digest, size = hashlib.sha256(), 0
            with self._open(self._request(url), timeout=self.s.timeout) as r, dest.open("wb") as f:
                while chunk := r.read(self.s.chunk_size):
                    f.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                expected = r.headers.get("Content-Length") if r.headers else None
                if expected is not None and int(expected) != size:
                    raise _Retryable(f"Téléchargement tronqué : {size} octets reçus sur {expected}")
                f.flush()
                os.fsync(f.fileno())
            return digest.hexdigest(), size

        return self._with_retries(url, action)
