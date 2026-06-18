import os
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from openpyxl import Workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vercel_deploy", "api"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "worker"))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import index
import quote_worker


def _sample_quotation(path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    for col, value in {1: "No.", 2: "Item", 4: "Description", 5: "Dimension", 7: "Qty", 10: "List Price"}.items():
        ws.cell(row=7, column=col, value=value)
    ws.cell(row=8, column=1, value="- Escritorios")
    ws.cell(row=9, column=1, value=1)
    ws.cell(row=9, column=2, value="Mesa Uno")
    ws.cell(row=9, column=4, value="Mesa operativa")
    ws.cell(row=9, column=5, value="1200 x 600")
    ws.cell(row=9, column=7, value=2)
    ws.cell(row=9, column=10, value=1000)
    wb.save(path)


def test_dev_mode_full_quote_flow(tmp_path, monkeypatch):
    store_dir = tmp_path / "dev-store"
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "DEV_STORE_DIR", store_dir)
    monkeypatch.setattr(index, "DEV_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setattr(quote_worker, "DEV_MODE", True)
    monkeypatch.setattr(quote_worker, "DEV_STORE_DIR", store_dir)
    monkeypatch.setattr(quote_worker, "QUOTE_ENGINE", "python")
    index._RATE_LIMIT_STORE.clear()

    client = TestClient(index.app)
    login = client.post("/login", json={"email": "dev@mobiliti.local", "password": "dev12345"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    source = tmp_path / "quotation.xlsx"
    _sample_quotation(source)

    init = client.post(
        "/cotizaciones/init-upload",
        headers=headers,
        json={"filename": "quotation.xlsx", "size": source.stat().st_size, "template": "online"},
    )
    assert init.status_code == 200
    init_data = init.json()
    assert init_data["upload_url"].endswith("/dev-upload")

    upload = client.post(
        init_data["upload_url"],
        headers=headers,
        files={"file": ("quotation.xlsx", source.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 200

    submit = client.post(
        f"/cotizaciones/{init_data['job_id']}/submit",
        headers=headers,
        json={
            "cotizacion": "COT-DEV",
            "proyecto": "Proyecto Dev",
            "cliente": "Cliente Dev",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa Dev",
            "template": "online",
            "image_provider": "pillow",
        },
    )
    assert submit.status_code == 200
    assert submit.json()["job"]["status"] == "queued"

    assert quote_worker.run_once() is True

    status = client.get(f"/cotizaciones/{init_data['job_id']}", headers=headers)
    assert status.status_code == 200
    job = status.json()["job"]
    assert job["status"] == "completed"
    assert job["output_path"].endswith("/output.xlsx")

    download = client.get(f"/cotizaciones/{init_data['job_id']}/download", headers=headers)
    assert download.status_code == 200
    assert "/dev/storage/" in download.json()["download_url"]

    encoded = quote(job["output_path"], safe="")
    file_resp = client.get(f"/dev/storage/{encoded}")
    assert file_resp.status_code == 200
    assert file_resp.content.startswith(b"PK")
