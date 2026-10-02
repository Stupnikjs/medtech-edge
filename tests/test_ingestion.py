import io
import json
import stat
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path

from medtech_edge.ingestion import bulk
from medtech_edge.ingestion.client import FdaClient, FetchError
from medtech_edge.ingestion.config import DOWNLOAD_INDEX_URL, ENDPOINTS, Settings
from medtech_edge.ingestion.storage import Manifest, sha256_file

FILE_URL = "https://download.open.fda.gov/device/510k/device-510k-0001-of-0001.json.zip"


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("device-510k-0001-of-0001.json",
                   json.dumps({"meta": {"last_updated": "2026-09-30"}, "results": [{"k_number": "K000001"}] * 50}))
    return buf.getvalue()


def index_json(file_url: str = FILE_URL) -> bytes:
    return json.dumps({"meta": {}, "results": {"device": {"510k": {
        "export_date": "2026-09-30",
        "partitions": [{"display_name": "x", "file": file_url, "size_mb": "0.01", "records": 50}]}}}}).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(FILE_URL, code, "err", {"Retry-After": "0"}, None)


class FakeResp:
    def __init__(self, body: bytes, headers=None):
        self._b, self.headers = io.BytesIO(body), headers or {}

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """Réponses par URL ; le dernier élément de chaque file est 'collant'."""

    def __init__(self, routes):
        self.routes = {u: list(v) for u, v in routes.items()}
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        q = self.routes[url]
        item = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(item, Exception):
            raise item
        body, headers = item if isinstance(item, tuple) else (item, {})
        return FakeResp(body, headers)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.s = Settings(raw_dir=Path(self._tmp.name) / "raw", max_retries=3,
                          backoff_base=1.0, backoff_max=0.0, min_interval=0.0, timeout=1)

    def client(self, net):
        return FdaClient(self.s, opener=net, sleep=lambda s: None)

    def run_fetch(self, net):
        return bulk.run(self.client(net), self.s, [ENDPOINTS["510k"]])

    def dest(self):
        return self.s.raw_dir / "openfda/device/510k/2026-09-30/device-510k-0001-of-0001.json.zip"

    def test_telechargement_ok_lecture_seule_et_manifeste(self):
        z = make_zip()
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [(z, {"Content-Length": str(len(z))})]})
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "downloaded")
        self.assertEqual(stat.S_IMODE(self.dest().stat().st_mode) & 0o222, 0)
        (rec,) = Manifest(self.s.manifest_path).records()
        self.assertEqual(rec["sha256"], sha256_file(self.dest()))
        self.assertEqual(rec["expected_records"], 50)
        self.assertEqual(list(self.s.tmp_dir.glob("*")), [])
        self.assertEqual(bulk.verify_all(self.s), [])

    def test_idempotence_aucun_retelechargement(self):
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [make_zip()]})
        self.run_fetch(net)
        net.calls.clear()
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "skipped")
        self.assertNotIn(FILE_URL, net.calls)
        self.assertEqual(len(Manifest(self.s.manifest_path).records()), 1)

    def test_retry_sur_503_puis_succes(self):
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [http_error(503), make_zip()]})
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "downloaded")
        self.assertEqual(net.calls.count(FILE_URL), 2)

    def test_404_non_reessaye(self):
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [http_error(404)]})
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "failed")
        self.assertEqual(net.calls.count(FILE_URL), 1)

    def test_archive_tronquee_rejetee_rien_ecrit(self):
        z = make_zip()
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [z[: len(z) // 2]]})
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "failed")
        self.assertFalse(self.dest().exists())
        self.assertEqual(Manifest(self.s.manifest_path).records(), [])
        self.assertEqual(list(self.s.tmp_dir.glob("*")), [])

    def test_archive_corrompue_crc_rejetee(self):
        z = bytearray(make_zip())
        z[40] ^= 0xFF  # altère les données compressées
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [bytes(z)]})
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "failed")
        self.assertFalse(self.dest().exists())

    def test_content_length_incoherent_reessaye_puis_echec(self):
        z = make_zip()
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [(z, {"Content-Length": str(len(z) + 10)})]})
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "failed")
        self.assertEqual(net.calls.count(FILE_URL), 3)
        self.assertFalse(self.dest().exists())

    def test_hote_non_autorise_refuse(self):
        evil = "https://evil.example.com/x/a.json.zip"
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json(evil)]})
        with self.assertRaises(FetchError):
            self.run_fetch(net)

    def test_verify_detecte_alteration(self):
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [make_zip()]})
        self.run_fetch(net)
        self.dest().chmod(0o644)
        with self.dest().open("ab") as f:
            f.write(b"x")
        self.assertTrue(any("TAILLE" in p or "SHA256" in p for p in bulk.verify_all(self.s)))

    def test_adoption_si_manifeste_perdu(self):
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()], FILE_URL: [make_zip()]})
        self.run_fetch(net)
        self.s.manifest_path.unlink()
        (r,) = self.run_fetch(net)
        self.assertEqual(r.status, "adopted")
        self.assertEqual(len(Manifest(self.s.manifest_path).records()), 1)

    def test_dry_run_n_ecrit_rien(self):
        net = FakeNet({DOWNLOAD_INDEX_URL: [index_json()]})
        (r,) = bulk.run(self.client(net), self.s, [ENDPOINTS["510k"]], dry_run=True)
        self.assertEqual(r.status, "planned")
        self.assertFalse(self.s.raw_dir.exists())


if __name__ == "__main__":
    unittest.main()
