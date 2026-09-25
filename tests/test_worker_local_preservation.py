"""Regresión local sin pytest ni borrado de fixtures: ejecutar este archivo con Python."""

from copy import deepcopy
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mobiliti_saas" / "worker"))

import quote_worker
from mobiliti_saas.quote_engine.catalog_cart import create_catalog_quotation_workbook
from openpyxl import load_workbook
from PIL import Image


def block_deletion(event, args):
    if event in {"os.remove", "os.rmdir", "shutil.rmtree"}:
        raise PermissionError(f"Borrado bloqueado en la prueba: {event} {args[0]}")


class LocalPreservationTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output" / "tests-worker-preservation"
        output.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=output))
        self.temp = self.root / "temp"
        self.temp.mkdir()
        self.enterContext(patch.object(tempfile, "tempdir", str(self.temp)))
        self.enterContext(patch.dict(os.environ, {
            "MOBILITI_DEV_MODE": "1",
            "MOBILITI_DEV_STORE_DIR": str(self.root),
            "MOBILITI_DEV_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
        }))
        self.enterContext(patch.object(quote_worker, "DEV_MODE", True))
        self.enterContext(patch.object(quote_worker, "DEV_STORE_DIR", self.root))
        self.job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.import_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.client = quote_worker.LocalDevClient()
        self.input_key = f"users/1/jobs/{self.job_id}/input.xlsx"
        self.import_key = f"users/1/jobs/{self.import_id}/input.xlsx"
        self.job = {
            "id": self.job_id, "usuario_id": 1, "status": "queued",
            "input_path": self.input_key, "output_path": None,
            "created_at": "2026-08-31T00:00:00+00:00",
            "metadata": {"import_source": {"import_id": self.import_id}},
        }
        self.import_job = {
            "id": self.import_id, "usuario_id": 1, "status": "failed",
            "input_path": self.import_key,
            "metadata": {"import_consumed_by_job_id": self.job_id},
        }
        self.client._save({"quote_jobs": [deepcopy(self.job), deepcopy(self.import_job)],
                           "projects": [{"id": "preservar-proyecto"}]})
        for key, data in [(self.input_key, b"entrada"), (self.import_key, b"importada")]:
            target = self.client._storage_file(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def test_completion_preserves_inputs_import_metadata_and_work_files(self):
        def generate(_job, source, output):
            self.assertEqual(source.parser_source.read_bytes(), b"entrada")
            output.write_bytes(b"resultado")

        with patch.object(quote_worker, "_run_generator", generate):
            try:
                quote_worker.process_job(self.client, self.job)
            except PermissionError as exc:
                self.fail(f"La finalización intentó borrar archivos: {exc}")
        store = self.client._load()
        completed = store["quote_jobs"][0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["input_path"], self.input_key)
        self.assertEqual(store["quote_jobs"][1], self.import_job)
        self.assertEqual(store["projects"], [{"id": "preservar-proyecto"}])
        self.assertEqual(self.client._storage_file(self.input_key).read_bytes(), b"entrada")
        self.assertEqual(self.client._storage_file(self.import_key).read_bytes(), b"importada")
        self.assertEqual(self.client._storage_file(completed["output_path"]).read_bytes(), b"resultado")
        work = Path(completed["metadata"]["local_work_dir"])
        self.assertEqual((work / "input.xlsx").read_bytes(), b"entrada")
        self.assertEqual((work / "output.xlsx").read_bytes(), b"resultado")

    def test_failure_preserves_partial_output_and_releases_lease(self):
        def fail(_job, _source, output):
            output.write_bytes(b"salida parcial")
            raise RuntimeError("fallo simulado del generador")

        with patch.object(quote_worker, "_run_generator", fail):
            try:
                with self.assertRaisesRegex(RuntimeError, "fallo simulado"):
                    quote_worker.process_job(self.client, self.job)
            except PermissionError as exc:
                self.fail(f"La gestión del fallo intentó borrar archivos: {exc}")
        failed = self.client._load()["quote_jobs"][0]
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["lease_expires_at"])
        self.assertIsNone(failed["output_path"])
        self.assertEqual(failed["input_path"], self.input_key)
        work = Path(failed["metadata"]["local_work_dir"])
        self.assertEqual((work / "output.xlsx").read_bytes(), b"salida parcial")
        self.assertEqual((work / "input.xlsx").read_bytes(), b"entrada")

    def test_local_storage_refuses_deletion_before_filesystem_call(self):
        with self.assertRaisesRegex(PermissionError, "preservaci[oó]n local"):
            self.client.storage_delete(self.input_key)
        self.assertEqual(self.client._storage_file(self.input_key).read_bytes(), b"entrada")

    def test_selected_job_does_not_consume_another_queued_job(self):
        store = self.client._load()
        other = {**deepcopy(self.job), "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                 "created_at": "2026-08-30T00:00:00+00:00"}
        store["quote_jobs"].append(other)
        self.client._save(store)
        with patch.object(quote_worker, "_run_generator",
                          lambda _job, _source, output: output.write_bytes(b"resultado")):
            try:
                self.assertTrue(quote_worker.run_once(job_id=self.job_id))
            except TypeError as exc:
                self.fail(f"El worker no permite seleccionar la prueba local: {exc}")
        rows = self.client._load()["quote_jobs"]
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[2], other)

    def test_candidate_is_preserved_without_recycle_bin_or_quarantine(self):
        from mobiliti_saas.quote_engine import official_composer

        candidate = self.root / "candidato.xlsx"
        candidate.write_bytes(b"candidato conservado")
        with patch.object(official_composer.subprocess, "run",
                          side_effect=AssertionError("No invocar Papelera en preservación local")), \
             patch.object(official_composer, "_quarantine_candidate",
                          side_effect=AssertionError("No mover el candidato local")):
            self.assertEqual(official_composer._recycle_candidate(candidate), candidate)
        self.assertEqual(candidate.read_bytes(), b"candidato conservado")

    def test_catalog_roundtrip_keeps_image_and_values_without_temp_deletion(self):
        image_bytes = BytesIO()
        Image.new("RGB", (8, 8), "red").save(image_bytes, format="PNG")
        data = image_bytes.getvalue()
        name = hashlib.sha256(data).hexdigest() + ".png"
        asset_root = self.root / "catalog-assets"
        asset_root.mkdir()
        (asset_root / name).write_bytes(data)
        payload = {"source_type": "supplier_cart", "items": [{
            "code": "P-01", "name": "Silla de prueba", "description": "Descripción",
            "quantity": 2, "unit_price": 10, "unit": "PZA",
            "attributes": {"dimensions": "1x2x3"},
            "image_url": f"http://127.0.0.1:8000/dev/catalog-assets/{name}",
        }]}
        target = self.root / "cotizacion.xlsx"
        try:
            create_catalog_quotation_workbook(payload, target,
                                              source_type="supplier_cart", category_label="Prueba")
        except PermissionError as exc:
            self.fail(f"La serialización intentó borrar un temporal: {exc}")
        workbook = load_workbook(BytesIO(target.read_bytes()))
        self.addCleanup(workbook.close)
        sheet = workbook["Quotation"]
        self.assertEqual(sheet["B9"].value, "Silla de prueba")
        self.assertEqual(sheet["G9"].value, 2)
        self.assertEqual(sheet["J9"].value, 10)
        self.assertTrue(sheet["A8"].font.bold)
        self.assertEqual(len(sheet._images), 1)
        self.assertEqual((asset_root / name).read_bytes(), data)


if __name__ == "__main__":
    # La fase roja no puede borrar ni siquiera fixtures o temporales de librerías.
    sys.addaudithook(block_deletion)
    unittest.main(verbosity=2)
