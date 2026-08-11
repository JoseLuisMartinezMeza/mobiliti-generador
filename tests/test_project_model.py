from copy import deepcopy

import pytest

from project_fixtures import valid_project_payload
from mobiliti_saas.quote_engine.project_model import (
    normalize_project_payload,
    normalized_match_key,
    project_physical_line_count,
    project_summary,
)


def test_project_payload_accepts_one_level_and_counts_physical_rows():
    normalized = normalize_project_payload(valid_project_payload())

    assert normalized["lines"][1]["parent_line_id"] == normalized["lines"][0]["line_id"]
    assert project_summary(normalized) == {"sections": 1, "principals": 1, "complements": 1}
    assert project_physical_line_count(normalized) == 2


def test_project_display_cache_preserves_configuration_and_accepts_legacy_rows():
    configured = valid_project_payload()
    configured["lines"][0]["display_cache"]["configuration"] = (
        "Aluminio + Tela A + Cojín"
    )

    normalized = normalize_project_payload(configured)
    legacy = normalize_project_payload(valid_project_payload())

    assert normalized["lines"][0]["display_cache"]["configuration"] == (
        "Aluminio + Tela A + Cojín"
    )
    assert legacy["lines"][0]["display_cache"] == {
        "name": "Silla",
        "code": "CHAIR-1",
        "image_url": "",
    }


@pytest.mark.parametrize("mutation", ["duplicate", "orphan", "nested", "cycle"])
def test_project_payload_rejects_invalid_graph(mutation):
    payload = valid_project_payload()
    if mutation == "duplicate":
        payload["lines"][1]["line_id"] = payload["lines"][0]["line_id"]
    elif mutation == "orphan":
        payload["lines"][1]["parent_line_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif mutation == "nested":
        payload["lines"].append({
            **deepcopy(payload["lines"][1]),
            "line_id": "44444444-4444-4444-8444-444444444444",
            "parent_line_id": payload["lines"][1]["line_id"],
        })
    else:
        payload["lines"][0]["role"] = "complement"
        payload["lines"][0]["section_id"] = None
        payload["lines"][0]["parent_line_id"] = payload["lines"][1]["line_id"]

    with pytest.raises(ValueError):
        normalize_project_payload(payload)


def test_match_key_requires_both_provider_and_code():
    assert normalized_match_key("  CR Global ", " ab-12 ") == ("cr global", "AB-12")
    assert normalized_match_key("", "AB-12") is None
    assert normalized_match_key("CR Global", "") is None


def test_pending_supplier_code_is_allowed_but_legacy_catalog_code_remains_required():
    imported = valid_project_payload()
    imported["lines"][1]["official_code"] = ""

    normalized = normalize_project_payload(imported)

    assert normalized["lines"][1]["official_code"] == ""

    supplier = valid_project_payload()
    supplier["lines"][0]["official_code"] = ""

    normalized_supplier = normalize_project_payload(supplier)

    assert normalized_supplier["lines"][0]["official_code"] == ""

    catalog = valid_project_payload()
    catalog["lines"][0].update({
        "catalog": "tarkett",
        "official_code": "",
        "identity": {"code": "TARK-1"},
    })
    with pytest.raises(ValueError):
        normalize_project_payload(catalog)


@pytest.mark.parametrize("official_code", ["=A1", "+SUM(A1)", "-1+1", "@A1", "ABC\x07"])
def test_imported_optional_official_code_still_rejects_unsafe_text(official_code):
    payload = valid_project_payload()
    payload["lines"][1]["official_code"] = official_code

    with pytest.raises(ValueError):
        normalize_project_payload(payload)


def _duplicate_complement_position(payload):
    payload["lines"].append({
        **deepcopy(payload["lines"][1]),
        "line_id": "44444444-4444-4444-8444-444444444444",
        "position": 0,
    })


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda payload: payload["lines"][0].__setitem__("unexpected", "no"), "unexpected field"),
        (lambda payload: payload["lines"][1].__setitem__("source_asset_key", "projects/7/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/uploads/source.xlsx"), "foreign asset key"),
        (_duplicate_complement_position, "duplicate position"),
        (lambda payload: payload["lines"][0]["identity"].pop("internal_id"), "missing catalog identity"),
        (lambda payload: payload["lines"][1].__setitem__("unit_price", "NaN"), "non-finite imported price"),
        (lambda payload: payload["lines"][1].__setitem__("name", "=SUM(A1:A2)"), "formula-prefixed imported text"),
        (lambda payload: payload["lines"][1].__setitem__("official_code", "=SUM(A1:A2)"), "formula-prefixed imported code"),
    ],
)
def test_project_payload_rejects_invalid_source_contract(mutation, expected):
    payload = valid_project_payload()
    mutation(payload)

    with pytest.raises(ValueError):
        normalize_project_payload(payload)
