import inspect
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from mobiliti_saas.api import index as api
from mobiliti_saas.quote_engine.template_profiles import (
    DEFAULT_TEMPLATE_PROFILE_ID,
)
from mobiliti_saas.worker import quote_worker


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "mobiliti_saas" / "web" / "src" / "main.jsx"
CANONICAL_API = ROOT / "mobiliti_saas" / "api" / "index.py"
WEB_API = ROOT / "mobiliti_saas" / "web" / "api" / "index.py"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, "official_2026_gdl"),
        ("", "official_2026_gdl"),
        ("Formato Cotizacion 2026 GDL (1).xlsx", "official_2026_gdl"),
        ("Plantilla Corporativa Mobiliti 2025", "official_2026_gdl"),
        ("official_2026_gdl", "official_2026_gdl"),
        ("sunon_cdmx_v1c", "sunon_cdmx_v1c"),
    ],
)
def test_api_canonicalizes_supported_template_values(raw_value, expected):
    assert api._canonical_template_id(raw_value) == expected


@pytest.mark.parametrize(
    "raw_value",
    [
        "desconocida",
        "otra-plantilla.xlsx",
        "../plantilla.xlsx",
        r"C:\temp\plantilla.xlsx",
    ],
)
def test_api_rejects_unknown_template_ids_as_bad_request(raw_value):
    with pytest.raises(HTTPException) as caught:
        api._canonical_template_id(raw_value)

    assert caught.value.status_code == 400
    assert "Plantilla no permitida" in str(caught.value.detail)


def test_ui_uses_stable_template_ids_and_exposes_three_template_options():
    source = UI_SOURCE.read_text(encoding="utf-8")

    assert source.count('template: "official_2026_gdl"') >= 2
    assert (
        '<option value="official_2026_gdl">'
        "Formato Cotización 2026 GDL (1)</option>"
    ) in source
    assert (
        '<option value="sunon_cdmx_v1c">'
        "Formato Cotización Único - Sunon CDMX V1C</option>"
    ) in source
    assert (
        '<option value="official_2026_gdl">'
        "Plantilla Corporativa Mobiliti 2025</option>"
    ) in source


def test_project_quote_request_carries_the_selected_template_id():
    source = UI_SOURCE.read_text(encoding="utf-8")

    assert (
        "? {expected_revision: projectQuote.revision, "
        "template: getForm().template}"
    ) in source


def test_unknown_init_upload_template_fails_before_job_creation(monkeypatch):
    created = []
    monkeypatch.setattr(api, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(api, "_enforce_active_quote_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        api,
        "db_create_quote_job",
        lambda *_args, **_kwargs: created.append((_args, _kwargs)),
    )

    with pytest.raises(HTTPException) as caught:
        api.cotizaciones_init_upload(
            {
                "filename": "quotation.xlsx",
                "size": 1,
                "template": "desconocida.xlsx",
            },
            {"id": 7},
        )

    assert caught.value.status_code == 400
    assert created == []


def test_retry_canonicalizes_historical_template_before_update(monkeypatch):
    captured = {}
    job = {
        "id": "job-1",
        "status": "failed",
        "input_path": "users/7/jobs/job-1/input.xlsx",
        "template": "Formato Cotizacion 2026 GDL (1).xlsx",
        "metadata": {},
        "error_message": "fallo",
    }
    monkeypatch.setattr(api, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(api, "_quote_job_for_user", lambda *_args: job)
    monkeypatch.setattr(api, "_enforce_active_quote_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "_wake_worker", lambda: None)

    def update_job(_job_id, updates, **_kwargs):
        captured.update(updates)
        return {**job, **updates}

    monkeypatch.setattr(api, "db_update_quote_job", update_job)

    api.cotizaciones_retry("job-1", {"id": 7})

    assert captured["template"] == "official_2026_gdl"


def test_retry_rejects_unknown_template_before_update(monkeypatch):
    updated = []
    job = {
        "id": "job-2",
        "status": "failed",
        "input_path": "users/7/jobs/job-2/input.xlsx",
        "template": "desconocida.xlsx",
        "metadata": {},
        "error_message": "fallo",
    }
    monkeypatch.setattr(api, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(api, "_quote_job_for_user", lambda *_args: job)
    monkeypatch.setattr(api, "_enforce_active_quote_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        api,
        "db_update_quote_job",
        lambda *_args, **_kwargs: updated.append((_args, _kwargs)),
    )

    with pytest.raises(HTTPException) as caught:
        api.cotizaciones_retry("job-2", {"id": 7})

    assert caught.value.status_code == 400
    assert updated == []


def test_import_preview_persists_canonical_template(monkeypatch):
    captured = {}
    job = {
        "id": "job-preview",
        "status": "draft",
        "input_path": "users/7/jobs/job-preview/input.xlsx",
        "template": "Formato Cotizacion 2026 Oficial.xlsx",
        "metadata": {"original_filename": "quotation.xlsx"},
    }
    manifest = {
        "import_id": "job-preview",
        "original_filename": "quotation.xlsx",
        "source_hash": "a" * 64,
        "source_currency": "USD",
        "provider": "Sunon",
        "sections": [],
        "items": [],
    }
    monkeypatch.setattr(api, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(api, "_quote_job_for_user", lambda *_args: job)
    monkeypatch.setattr(api, "_storage_download_bytes", lambda _path: b"xlsx")
    monkeypatch.setattr(
        api,
        "build_import_manifest",
        lambda *_args: (manifest, {}),
    )
    monkeypatch.setattr(
        api,
        "_store_import_preview",
        lambda *_args: ("users/7/jobs/job-preview/manifest.json", {}),
    )

    def update_job(_job_id, updates, **_kwargs):
        captured.update(updates)
        return {**job, **updates}

    monkeypatch.setattr(api, "db_update_quote_job", update_job)

    result = api.quotation_import_preview("job-preview", {"id": 7})

    assert captured["template"] == "official_2026_gdl"
    assert result["import_id"] == "job-preview"


def test_project_quote_accepts_and_persists_template_id():
    assert "template" in api._PROJECT_QUOTE_FIELDS
    source = inspect.getsource(api.projects_quote)

    assert 'template = _canonical_template_id(body.get("template"))' in source
    assert "template=template" in source


def test_worker_uses_legacy_zero_argument_helper_for_default_profile(monkeypatch):
    profile = SimpleNamespace(
        id=DEFAULT_TEMPLATE_PROFILE_ID,
        template_path=Path("ignored.xlsx"),
    )
    monkeypatch.setattr(
        quote_worker,
        "resolve_template_profile",
        lambda raw, require_files: profile,
    )
    monkeypatch.setattr(quote_worker, "_template_path", lambda: "legacy-default.xlsx")

    assert quote_worker._template_path_for_job({}) == "legacy-default.xlsx"


def test_worker_selects_profile_path_from_job_and_requires_files(
    monkeypatch,
    tmp_path,
):
    selected = tmp_path / "sunon-cdmx.xlsx"
    seen = {}

    def resolve(raw, *, require_files):
        seen.update(raw=raw, require_files=require_files)
        return SimpleNamespace(id="sunon_cdmx_v1c", template_path=selected)

    monkeypatch.setattr(quote_worker, "resolve_template_profile", resolve)

    assert (
        quote_worker._template_path_for_job({"template": "sunon_cdmx_v1c"})
        == str(selected)
    )
    assert seen == {"raw": "sunon_cdmx_v1c", "require_files": True}


def test_worker_passes_selected_template_to_pdf_conversion(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4")
    selected = str(tmp_path / "sunon-cdmx.xlsx")
    seen = {}
    monkeypatch.setattr(quote_worker, "_input_extension_for_job", lambda _job: ".pdf")
    monkeypatch.setattr(quote_worker, "_template_path_for_job", lambda _job: selected)

    def convert_pdf(input_path, output_path, template_path):
        seen.update(
            input_path=input_path,
            output_path=output_path,
            template_path=template_path,
        )
        output_path.write_bytes(b"xlsx")

    monkeypatch.setattr(quote_worker, "_convert_pdf_to_quotation", convert_pdf)

    prepared = quote_worker._prepare_generator_input(
        {"metadata": {}},
        source,
        tmp_path,
    )

    assert seen["template_path"] == selected
    assert prepared.parser_source == seen["output_path"]


def test_worker_passes_selected_template_to_generator(
    monkeypatch,
    tmp_path,
):
    selected = str(tmp_path / "sunon-cdmx.xlsx")
    seen = {}
    monkeypatch.setattr(quote_worker, "QUOTE_ENGINE", "python")
    monkeypatch.setattr(quote_worker, "_template_path_for_job", lambda _job: selected)

    online_quote_generator = ModuleType("online_quote_generator")
    online_quote_generator.generate_online_quote = lambda **kwargs: seen.update(kwargs)
    monkeypatch.setitem(sys.modules, "online_quote_generator", online_quote_generator)
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    prepared = quote_worker.PreparedGeneratorInput(
        parser_source=source,
        original_quotation=source,
        quotation_data=(),
    )

    quote_worker._run_generator(
        {"template": "sunon_cdmx_v1c", "metadata": {}},
        prepared,
        output,
    )

    assert seen["template_path"] == selected


def test_api_mirrors_remain_byte_for_byte_identical():
    assert CANONICAL_API.read_bytes() == WEB_API.read_bytes()
