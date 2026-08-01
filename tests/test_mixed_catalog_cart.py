from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from mobiliti_saas.quote_engine import catalog_cart
from mobiliti_saas.quote_engine.mixed_catalog import (
    MIXED_CATALOG_ORDER,
    build_mixed_catalog_cart_payload,
    build_mixed_reservation_groups,
    mixed_cart_key,
    preflight_mixed_catalog_items,
    validate_mixed_catalog_payload,
)
from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem
from mobiliti_saas.quote_engine.project_quote import project_context
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem
from project_fixtures import valid_project_payload
from quotation_import_fixtures import write_import_fixture


MIXED_CART_MODULE = Path("mobiliti_saas/web/src/mixedCart.js")
MIXED_CART_EXPORTS = (
    "createImportedCartBundle",
    "replaceImportedCartBundle",
    "toMixedQuoteItem",
    "updateImportedCartLine",
)
IMPORT_ID = "7b1d6d42-236a-4bc1-9aa8-8d9db793c30b"
FIRST_LINE_ID = "11111111-1111-4111-8111-111111111111"
SECOND_LINE_ID = "22222222-2222-4222-8222-222222222222"


def run_mixed_cart_js(function_name, *args):
    module_url = MIXED_CART_MODULE.resolve().as_uri()
    script = (
        f'import {{ {", ".join(MIXED_CART_EXPORTS)} }} from {json.dumps(module_url)};\n'
        f"console.log(JSON.stringify({function_name}(...{json.dumps(args)})));"
    )
    return _run_mixed_cart_script(script)


def _run_mixed_cart_script(script):
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def imported_preview(import_id=IMPORT_ID):
    items = [
        {
            "key": f"import:{import_id}:{row}",
            "source_row": row,
            "name": f"Producto {row}",
            "description": f"Descripcion {row}",
            "dimension": "600 x 600 mm",
            "quantity": "1",
            "unit_price": "688.50" if row == 9 else "10",
            "image_url": f"https://storage.invalid/preview/{row}.png",
        }
        for row in range(9, 16)
    ]
    return {
        "import_id": import_id,
        "original_filename": "CET PRUEBAS.xlsx",
        "provider": "Proveedor detectado",
        "source_currency": None,
        "sections": [
            {"id": "import-section-1", "title": "Sala de juntas", "item_keys": [item["key"] for item in items[:3]]},
            {"id": "import-section-2", "title": "Muestras", "item_keys": [item["key"] for item in items[3:5]]},
            {"id": "import-section-3", "title": "Consejo", "item_keys": [item["key"] for item in items[5:]]},
        ],
        "items": items,
    }


def test_imported_preview_becomes_editable_global_cart_lines():
    result = run_mixed_cart_js(
        "createImportedCartBundle",
        imported_preview(),
        "USD",
        "Sunon",
        [{"id": "section-1", "concept": "Recepcion"}],
    )

    assert len(result["lines"]) == 7
    assert result["lines"][0]["kind"] == "imported"
    assert result["lines"][0]["key"] == f"import:{IMPORT_ID}:9"
    assert result["lines"][0]["sectionId"] == result["sections"][0]["id"]
    assert result["lines"][0]["sourceCurrency"] == "USD"
    assert result["lines"][0]["edits"]["unitPrice"] == "688.50"
    assert result["lines"][0]["edits"]["provider"] == "Sunon"
    assert result["sections"] == [
        {"id": "section-1", "concept": "Sala de juntas"},
        {"id": "section-2", "concept": "Muestras"},
        {"id": "section-3", "concept": "Consejo"},
    ]


def test_imported_preview_creates_mapped_sections_from_an_empty_cart():
    preview = imported_preview()

    result = run_mixed_cart_js(
        "createImportedCartBundle", preview, "USD", "Sunon", []
    )

    assert result["sections"] == [
        {"id": "section-1", "concept": "Sala de juntas"},
        {"id": "section-2", "concept": "Muestras"},
        {"id": "section-3", "concept": "Consejo"},
    ]
    assert [line["key"] for line in result["lines"]] == [
        item["key"] for item in preview["items"]
    ]
    assert [line["sectionId"] for line in result["lines"]] == [
        "section-1", "section-1", "section-1", "section-2", "section-2", "section-3", "section-3",
    ]
    assert {line["sectionId"] for line in result["lines"]} == {
        section["id"] for section in result["sections"]
    }


def test_same_canonical_product_can_appear_twice_with_distinct_line_ids(
    mixed_catalogs,
    rate_rows,
):
    rows = [
        {
            "line_id": FIRST_LINE_ID,
            "catalog": "sunon",
            "internal_id": "sunon:desk-1",
            "quantity": "1",
        },
        {
            "line_id": SECOND_LINE_ID,
            "catalog": "sunon",
            "internal_id": "sunon:desk-1",
            "quantity": "2",
        },
    ]
    project = valid_project_payload()
    principal = project["lines"][0]
    principal["line_id"] = FIRST_LINE_ID
    principal["identity"]["internal_id"] = "sunon:desk-1"
    second = deepcopy(principal)
    second["line_id"] = SECOND_LINE_ID
    second["position"] = 1
    second["quantity"] = "2"
    project["lines"] = [principal, second]

    payload = build_mixed_catalog_cart_payload(
        rows,
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": "section-1",
            "title": "Recepción",
            "line_ids": [row["line_id"] for row in rows],
        }],
        project_context=project_context(project, "project-7", 3),
        today=date(2026, 7, 19),
    )

    items = payload["groups"][0]["items"]
    assert [item["line_id"] for item in items] == [FIRST_LINE_ID, SECOND_LINE_ID]
    assert items[0]["canonical_key"] == items[1]["canonical_key"]
    assert payload["sections"][0]["line_ids"] == [FIRST_LINE_ID, SECOND_LINE_ID]
    assert payload["project_context"]["project_revision"] == 3


def test_project_context_preserves_template_and_language_through_mixed_validation(
    mixed_catalogs,
    rate_rows,
):
    project = valid_project_payload()
    project["quote_fields"].update({
        "template": "sunon_cdmx_v1c",
        "description_language": "en",
    })
    principal = project["lines"][0]
    principal["line_id"] = FIRST_LINE_ID
    principal["identity"]["internal_id"] = "sunon:desk-1"
    project["lines"] = [principal]

    payload = build_mixed_catalog_cart_payload(
        [{
            "line_id": FIRST_LINE_ID,
            "catalog": "sunon",
            "internal_id": "sunon:desk-1",
            "quantity": principal["quantity"],
        }],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": project["sections"][0]["section_id"],
            "title": project["sections"][0]["concept"],
            "line_ids": [FIRST_LINE_ID],
        }],
        project_context=project_context(project, "project-cdmx", 4),
        today=date(2026, 7, 19),
    )

    validated = validate_mixed_catalog_payload(payload)

    assert validated["project_context"]["project_id"] == "project-cdmx"
    assert validated["project_context"]["project_revision"] == 4
    assert validated["project_context"]["normalized_project_payload"][
        "quote_fields"
    ]["template"] == "sunon_cdmx_v1c"
    assert validated["project_context"]["normalized_project_payload"][
        "quote_fields"
    ]["description_language"] == "en"


def test_legacy_request_without_line_ids_still_builds_payload(
    mixed_catalogs,
    rate_rows,
):
    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "tarkett", "code": "25731726", "quantity": "1"}],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )

    assert payload["groups"][0]["items"][0]["line_id"] == "legacy-1"
    assert payload["sections"] == [{
        "id": "section-1",
        "title": "Recepción",
        "line_ids": ["legacy-1"],
    }]
    assert payload["project_context"] is None


@pytest.mark.parametrize(
    "row",
    [
        {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
        {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"},
    ],
)
def test_direct_catalog_duplicates_also_keep_independent_occurrences(
    row,
    mixed_catalogs,
    rate_rows,
):
    payload = build_mixed_catalog_cart_payload(
        [row, {**row, "quantity": "2"}],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )

    lines = payload["groups"][0]["items"]
    assert [line["line_id"] for line in lines] == ["legacy-1", "legacy-2"]
    assert lines[0]["canonical_key"] == lines[1]["canonical_key"]


def test_legacy_item_keys_are_rejected_when_duplicate_catalog_identity_is_ambiguous(
    mixed_catalogs,
    rate_rows,
):
    rows = [
        {
            "line_id": FIRST_LINE_ID,
            "catalog": "sunon",
            "internal_id": "sunon:desk-1",
            "quantity": "1",
        },
        {
            "line_id": SECOND_LINE_ID,
            "catalog": "sunon",
            "internal_id": "sunon:desk-1",
            "quantity": "2",
        },
    ]

    with pytest.raises(ValueError, match="ambigu"):
        build_mixed_catalog_cart_payload(
            rows,
            catalogs=mixed_catalogs,
            rate_rows=rate_rows,
            quote_currency="MXN",
            commercial_discount_percent="40",
            presentation_sections=[{
                "id": "section-1",
                "title": "Recepción",
                "item_keys": [mixed_cart_key(rows[0]), mixed_cart_key(rows[1])],
            }],
            today=date(2026, 7, 19),
        )


def test_line_id_must_be_unique_across_catalog_and_imported_occurrences(
    tmp_path,
    mixed_catalogs,
    rate_rows,
):
    source = write_import_fixture(tmp_path / "cross-occurrence.xlsx")
    manifest, _images = build_import_manifest(
        source.read_bytes(), IMPORT_ID, source.name
    )

    with pytest.raises(ValueError, match="line_id mixto duplicado"):
        build_mixed_catalog_cart_payload(
            [{
                "line_id": FIRST_LINE_ID,
                "catalog": "tarkett",
                "code": "25731726",
                "quantity": "1",
            }],
            catalogs=mixed_catalogs,
            rate_rows=rate_rows,
            quote_currency="MXN",
            commercial_discount_percent="40",
            imported_source={
                "manifest": manifest,
                "source_currency": "USD",
                "items": [{
                    "line_id": FIRST_LINE_ID,
                    "kind": "imported",
                    "import_id": IMPORT_ID,
                    "source_row": 11,
                    "source_currency": "USD",
                    "quantity": "1",
                    "overrides": {
                        "name": "Alien Task Chair",
                        "description": "Silla operativa",
                        "dimension": "630 x 565 x 1000 mm",
                        "unit_price": "82",
                        "provider": "Sunon",
                    },
                }],
            },
            today=date(2026, 7, 19),
        )


@pytest.mark.parametrize("with_project_context", (False, True))
def test_explicit_imported_occurrences_can_share_the_same_source_row(
    with_project_context,
    tmp_path,
    mixed_catalogs,
    rate_rows,
):
    source = write_import_fixture(tmp_path / "duplicate-project-import.xlsx")
    manifest, _images = build_import_manifest(
        source.read_bytes(), IMPORT_ID, source.name
    )
    project = valid_project_payload()
    imported_template = deepcopy(project["lines"][1])
    imported_template.update({
        "role": "principal",
        "section_id": "section-1",
        "parent_line_id": None,
        "source_row": 11,
        "import_id": IMPORT_ID,
        "quantity": "1",
        "unit_price": "82.00",
    })
    imported_template.pop("quantity_mode")
    first_project_line = {
        **imported_template,
        "line_id": FIRST_LINE_ID,
        "position": 0,
    }
    second_project_line = {
        **imported_template,
        "line_id": SECOND_LINE_ID,
        "position": 1,
        "quantity": "2",
    }
    project["lines"] = [first_project_line, second_project_line]
    raw_template = {
        "kind": "imported",
        "import_id": IMPORT_ID,
        "source_row": 11,
        "source_currency": "USD",
        "quantity": "1",
        "overrides": {
            "name": "Alien Task Chair",
            "description": "Silla operativa",
            "dimension": "630 x 565 x 1000 mm",
            "unit_price": "82.00",
            "provider": "Sunon",
        },
    }

    payload = build_mixed_catalog_cart_payload(
        [],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": "section-1",
            "title": "Recepción",
            "line_ids": [FIRST_LINE_ID, SECOND_LINE_ID],
        }],
        imported_source={
            "manifest": manifest,
            "source_currency": "USD",
            "items": [
                {**raw_template, "line_id": FIRST_LINE_ID},
                {
                    **raw_template,
                    "line_id": SECOND_LINE_ID,
                    "quantity": "2",
                },
            ],
        },
        project_context=(
            project_context(project, "project-imported", 1)
            if with_project_context
            else None
        ),
        today=date(2026, 7, 19),
    )

    items = payload["imported_source"]["items"]
    assert [item["line_id"] for item in items] == [
        FIRST_LINE_ID,
        SECOND_LINE_ID,
    ]
    assert items[0]["canonical_key"] == items[1]["canonical_key"]
    assert payload["sections"][0]["line_ids"] == [
        FIRST_LINE_ID,
        SECOND_LINE_ID,
    ]


def test_legacy_imported_duplicate_source_row_remains_rejected(
    tmp_path,
    mixed_catalogs,
    rate_rows,
):
    source = write_import_fixture(tmp_path / "duplicate-legacy-import.xlsx")
    manifest, _images = build_import_manifest(
        source.read_bytes(), IMPORT_ID, source.name
    )
    imported = {
        "kind": "imported",
        "import_id": IMPORT_ID,
        "source_row": 11,
        "source_currency": "USD",
        "quantity": "1",
        "overrides": {
            "name": "Alien Task Chair",
            "description": "Silla operativa",
            "dimension": "630 x 565 x 1000 mm",
            "unit_price": "82.00",
            "provider": "Sunon",
        },
    }

    with pytest.raises(ValueError, match="Fila importada invalida"):
        build_mixed_catalog_cart_payload(
            [],
            catalogs=mixed_catalogs,
            rate_rows=rate_rows,
            quote_currency="MXN",
            commercial_discount_percent="40",
            imported_source={
                "manifest": manifest,
                "source_currency": "USD",
                "items": [imported, deepcopy(imported)],
            },
            today=date(2026, 7, 19),
        )


def test_project_context_must_be_exact_and_reference_every_occurrence(
    mixed_catalogs,
    rate_rows,
):
    project = valid_project_payload()
    project["lines"] = [project["lines"][0]]
    context = project_context(project, "project-7", 3)
    context["compositions"][0]["price_terms"][0]["line_id"] = SECOND_LINE_ID

    with pytest.raises(ValueError, match="Contexto de Proyecto invalido"):
        build_mixed_catalog_cart_payload(
            [{
                "line_id": FIRST_LINE_ID,
                "catalog": "sunon",
                "internal_id": "sunon:desk-1",
                "quantity": "10",
            }],
            catalogs=mixed_catalogs,
            rate_rows=rate_rows,
            quote_currency="MXN",
            commercial_discount_percent="40",
            presentation_sections=[{
                "id": "section-1",
                "title": "Recepción",
                "line_ids": [FIRST_LINE_ID],
            }],
            project_context=context,
            today=date(2026, 7, 19),
        )


def test_project_context_rejects_occurrence_presented_in_the_wrong_section(
    mixed_catalogs,
    rate_rows,
):
    project = valid_project_payload()
    principal = project["lines"][0]
    principal["line_id"] = FIRST_LINE_ID
    principal["identity"]["internal_id"] = "sunon:desk-1"
    second = deepcopy(principal)
    second.update({
        "line_id": SECOND_LINE_ID,
        "section_id": "section-5",
        "position": 0,
        "quantity": "2",
    })
    project["sections"].append({
        "section_id": "section-5",
        "concept": "Privados",
        "position": 1,
    })
    project["lines"] = [principal, second]

    with pytest.raises(ValueError, match="Contexto de Proyecto invalido"):
        build_mixed_catalog_cart_payload(
            [
                {
                    "line_id": FIRST_LINE_ID,
                    "catalog": "sunon",
                    "internal_id": "sunon:desk-1",
                    "quantity": "10",
                },
                {
                    "line_id": SECOND_LINE_ID,
                    "catalog": "sunon",
                    "internal_id": "sunon:desk-1",
                    "quantity": "2",
                },
            ],
            catalogs=mixed_catalogs,
            rate_rows=rate_rows,
            quote_currency="MXN",
            commercial_discount_percent="40",
            presentation_sections=[
                {
                    "id": "section-1",
                    "title": "Recepción",
                    "line_ids": [SECOND_LINE_ID],
                },
                {
                    "id": "section-5",
                    "title": "Privados",
                    "line_ids": [FIRST_LINE_ID],
                },
            ],
            project_context=project_context(project, "project-7", 3),
            today=date(2026, 7, 19),
        )


def test_imported_line_checkout_contains_only_reference_and_allowed_overrides():
    bundle = run_mixed_cart_js(
        "createImportedCartBundle",
        imported_preview(),
        "EUR",
        "Sunon",
        [{"id": "section-1", "concept": "Recepcion"}],
    )
    item = run_mixed_cart_js("toMixedQuoteItem", bundle["lines"][0])

    assert set(item) == {
        "kind", "import_id", "source_row", "source_currency", "quantity", "overrides",
    }
    assert item["kind"] == "imported"
    assert item["import_id"] == IMPORT_ID
    assert item["source_row"] == 9
    assert item["source_currency"] == "EUR"
    assert item["overrides"] == {
        "name": "Producto 9",
        "description": "Descripcion 9",
        "dimension": "600 x 600 mm",
        "unit_price": "688.50",
        "provider": "Sunon",
    }


def test_imported_edits_are_immutable_and_reject_unsafe_text_or_invalid_money():
    preview = imported_preview()
    module_url = MIXED_CART_MODULE.resolve().as_uri()
    result = _run_mixed_cart_script(
        f'import {{ {", ".join(MIXED_CART_EXPORTS)} }} from {json.dumps(module_url)};\n'
        f"const preview = {json.dumps(preview)};\n"
        "const bundle = createImportedCartBundle(preview, 'USD', 'Sunon', [{id: 'section-1', concept: 'Recepcion'}]);\n"
        "const original = bundle.lines;\n"
        "const updated = updateImportedCartLine(original, original[0].key, {name: 'Nombre revisado'});\n"
        "const errors = [];\n"
        "for (const edits of [{name: '=SUM(A1:A2)'}, {unitPrice: '-1'}, {provider: ''}, {unknown: 'x'}]) {\n"
        "  try { updateImportedCartLine(original, original[0].key, edits); errors.push('accepted'); }\n"
        "  catch (error) { errors.push(error.message); }\n"
        "}\n"
        "console.log(JSON.stringify({originalName: original[0].edits.name, updatedName: updated[0].edits.name, "
        "unrelatedPreserved: original[1] === updated[1], errors}));"
    )

    assert result["originalName"] == "Producto 9"
    assert result["updatedName"] == "Nombre revisado"
    assert result["unrelatedPreserved"] is True
    assert all(error != "accepted" for error in result["errors"])


def test_replacing_import_keeps_catalog_lines_and_removes_previous_import():
    previous = run_mixed_cart_js(
        "createImportedCartBundle",
        imported_preview(),
        "USD",
        "Sunon",
        [{"id": "section-1", "concept": "Recepcion"}],
    )
    catalog_line = {
        "key": "alma:[\\\"alma:desk\\\",\\\"\\\",[]]",
        "catalog": "alma",
        "sectionId": "section-1",
    }
    replacement = run_mixed_cart_js(
        "createImportedCartBundle",
        imported_preview("0e061c8a-0085-446f-a4d3-b4d4b43f0f2a"),
        "MXN",
        "Otro proveedor",
        previous["sections"],
    )

    result = run_mixed_cart_js(
        "replaceImportedCartBundle",
        [catalog_line, *previous["lines"]],
        previous["sections"],
        replacement,
    )

    assert [line["key"] for line in result["lines"]] == [
        catalog_line["key"],
        *[line["key"] for line in replacement["lines"]],
    ]
    assert all(line.get("kind") != "imported" or line["importId"] == replacement["lines"][0]["importId"] for line in result["lines"])
    assert {line["sectionId"] for line in result["lines"]} <= {section["id"] for section in result["sections"]}


def test_replacing_import_from_empty_lines_and_sections_keeps_only_imported_sections():
    bundle = run_mixed_cart_js(
        "createImportedCartBundle", imported_preview(), "USD", "Sunon", []
    )

    result = run_mixed_cart_js("replaceImportedCartBundle", [], [], bundle)

    assert result == bundle


def test_first_import_appends_after_the_initial_section_when_catalog_lines_exist():
    sections = [{"id": "section-1", "concept": "Recepcion"}]
    catalog_line = {
        "key": "tarkett:25731726",
        "catalog": "tarkett",
        "sectionId": "section-1",
    }
    bundle = run_mixed_cart_js(
        "createImportedCartBundle", imported_preview(), "USD", "Sunon", sections
    )

    result = run_mixed_cart_js(
        "replaceImportedCartBundle", [catalog_line], sections, bundle
    )

    assert result["sections"][0] == sections[0]
    assert result["lines"][0] == catalog_line
    assert result["lines"][1]["sectionId"] == "section-2"
    assert [section["concept"] for section in result["sections"]] == [
        "Recepcion", "Sala de juntas", "Muestras", "Consejo",
    ]


def _supplier_item(catalog, *, internal_id, price="100.000000", stock="5.000000", availability="stocked"):
    return {
        "internal_id": internal_id, "supplier": catalog, "product_key": internal_id,
        "sku": internal_id.upper(), "code_status": "verified", "brand": catalog,
        "collection": catalog, "name": f"Producto {catalog}", "description": "",
        "unit": "pieza", "availability_type": availability,
        "stock": stock if availability == "stocked" else None,
        "lead_time": "Sobre pedido" if availability != "stocked" else "",
        "base_price_options": [], "add_on_options": [],
        "base_currency": "USD" if catalog in {"sunon", "alma"} else "MXN",
        "price_net": price, "tax_rate": "0.160000", "attributes": {},
        "image_url": "", "image_kind": "placeholder", "product_url": "",
        "warnings": [], "source_reference": f"{catalog}:source",
    }


@pytest.fixture
def mixed_catalogs():
    tarkett = TarkettCatalogItem("25731726", "Piso Tarkett", "m2", Decimal("10"), unit_price=Decimal("100"), price_source="catalog")
    offiho = OffihoCatalogItem("offiho:desk-1", "OHE-1", "Escritorio", "Negro", "PZA", Decimal("1"), Decimal("8"), Decimal("200"), price_source="catalog")
    catalogs = {
        "tarkett": {"source_hash": "a" * 64, "items": [tarkett], "by_code": {tarkett.code: tarkett}},
        "offiho": {"source_hash": "b" * 64, "items": [offiho], "by_inventory_key": {offiho.inventory_key: offiho}},
    }
    for index, catalog in enumerate(MIXED_CATALOG_ORDER[2:], start=2):
        item = _supplier_item(catalog, internal_id=f"{catalog}:desk-1")
        catalogs[catalog] = {
            "supplier": catalog, "source_hash": f"{index:x}" * 64,
            "generated_at": "2026-07-19T00:00:00+00:00", "items": [item],
        }
    return catalogs


@pytest.fixture
def rate_rows():
    return [
        {"currency": "USD", "effective_date": "2026-07-19", "mxn_per_unit": "18.500000", "retrieved_at": "2026-07-19T12:00:00+00:00"},
        {"currency": "EUR", "effective_date": "2026-07-19", "mxn_per_unit": "20.500000", "retrieved_at": "2026-07-19T12:00:00+00:00"},
    ]


def browser_rows_for_all_catalogs():
    return [
        {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
        {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"},
        *[
            {"catalog": catalog, "internal_id": f"{catalog}:desk-1", "quantity": "1"}
            for catalog in MIXED_CATALOG_ORDER[2:]
        ],
    ]


def test_mixed_payload_accepts_700_lines_across_35_sections(rate_rows):
    supplier_names = MIXED_CATALOG_ORDER[2:]
    catalogs = {
        catalog: {
            "supplier": catalog,
            "source_hash": f"{position:x}" * 64,
            "generated_at": "2026-07-19T00:00:00+00:00",
            "items": [],
        }
        for position, catalog in enumerate(supplier_names, start=2)
    }
    rows = []
    for index in range(700):
        catalog = supplier_names[index % len(supplier_names)]
        internal_id = f"{catalog}:large-{index + 1}"
        catalogs[catalog]["items"].append(
            _supplier_item(catalog, internal_id=internal_id)
        )
        rows.append(
            {"catalog": catalog, "internal_id": internal_id, "quantity": "1"}
        )
    keys = [mixed_cart_key(row) for row in rows]
    sections = [
        {
            "id": f"section-{position + 1}",
            "title": f"Area {position + 1}",
            "item_keys": keys[offset:offset + 20],
        }
        for position, offset in enumerate(range(0, len(keys), 20))
    ]

    payload = build_mixed_catalog_cart_payload(
        rows,
        catalogs=catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=sections,
        today=date(2026, 7, 19),
    )

    assert payload["item_count"] == 700
    assert len(payload["sections"]) == 35


def test_mixed_payload_keeps_catalog_groups_and_imported_source_separate(
    tmp_path, mixed_catalogs, rate_rows
):
    source = write_import_fixture(tmp_path / "mixed-import.xlsx")
    manifest, _images = build_import_manifest(source.read_bytes(), IMPORT_ID, source.name)
    catalog_item = {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"}
    imported_item = {
        "kind": "imported",
        "import_id": IMPORT_ID,
        "source_row": 11,
        "source_currency": "USD",
        "quantity": "2",
        "overrides": {
            "name": "Alien Task Chair revisada",
            "description": "Silla operativa revisada",
            "dimension": "630 x 565 x 1000 mm",
            "unit_price": "82.00",
            "provider": "Sunon",
        },
    }
    catalog_key = mixed_cart_key(catalog_item)
    imported_key = f"import:{IMPORT_ID}:11"
    sections = [{
        "id": "section-1",
        "title": "Recepcion",
        "item_keys": [catalog_key, imported_key],
    }]

    payload = build_mixed_catalog_cart_payload(
        [catalog_item],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=sections,
        imported_source={
            "manifest": manifest,
            "items": [imported_item],
            "source_currency": "USD",
        },
        today=date(2026, 7, 19),
    )

    assert payload["groups"][0]["catalog"] == "offiho"
    assert payload["imported_source"]["items"][0]["canonical_key"] == imported_key
    assert payload["sections"][0]["line_ids"] == ["legacy-1", "legacy-import-1"]
    assert build_mixed_reservation_groups(payload) == [{
        "catalog": "offiho",
        "items": [{
            "identity": "offiho:desk-1",
            "sku": "OHE-1",
            "quantity": "1.000000",
            "stock": "8.000000",
        }],
    }]


def test_mixed_cart_groups_nine_catalogs_in_canonical_order(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload(browser_rows_for_all_catalogs(), catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert payload["source_type"] == "mixed_catalog_cart"
    assert [group["catalog"] for group in payload["groups"]] == list(MIXED_CATALOG_ORDER)
    assert sum(len(group["items"]) for group in payload["groups"]) == len(
        MIXED_CATALOG_ORDER
    )


def test_mixed_cart_preserves_manual_sections_independent_from_catalog_groups(
    mixed_catalogs, rate_rows
):
    rows = [
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"},
        {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"},
        {"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "1"},
    ]
    keys = [mixed_cart_key(row) for row in rows]
    sections = [
        {"id": "section-1", "title": "Recepción", "item_keys": keys[:2]},
        {"id": "section-2", "title": "Privados", "item_keys": keys[2:]},
    ]

    payload = build_mixed_catalog_cart_payload(
        rows,
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=sections,
        today=date(2026, 7, 19),
    )

    assert payload["sections"] == [
        {"id": "section-1", "title": "Recepción", "line_ids": ["legacy-1", "legacy-2"]},
        {"id": "section-2", "title": "Privados", "line_ids": ["legacy-3"]},
    ]
    assert [group["catalog"] for group in payload["groups"]] == [
        "offiho",
        "sonara",
        "alma",
    ]


def test_mixed_cart_without_sections_uses_one_reception_section_in_request_order(
    mixed_catalogs, rate_rows
):
    rows = [
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"},
        {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
    ]
    payload = build_mixed_catalog_cart_payload(
        rows,
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )
    assert payload["sections"] == [{
        "id": "section-1",
        "title": "Recepción",
        "line_ids": ["legacy-1", "legacy-2"],
    }]


@pytest.mark.parametrize(
    "sections",
    (
        [{"id": "section-1", "title": "Recepción", "item_keys": []}],
        [{"id": "section-1", "title": "", "item_keys": ["alma:[\"alma:desk-1\",\"\",[]]"]}],
        [{"id": "section-1", "title": "Recepción", "item_keys": ["alma:unknown"]}],
        [{"id": "section-1", "title": "Recepción", "item_keys": ["alma:[\"alma:desk-1\",\"\",[]]"], "extra": True}],
    ),
)
def test_mixed_cart_rejects_invalid_manual_sections(mixed_catalogs, rate_rows, sections):
    with pytest.raises((TypeError, ValueError), match="(?i)section|seccion|presentation_sections"):
        build_mixed_catalog_cart_payload(
            [{"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"}],
            catalogs=mixed_catalogs,
            rate_rows=rate_rows,
            quote_currency="MXN",
            commercial_discount_percent="40",
            presentation_sections=sections,
            today=date(2026, 7, 19),
        )


@pytest.mark.parametrize("field", ("unit_price", "base_currency", "exchange_rate", "stock", "image_url", "product_url", "supplier", "warnings"))
def test_mixed_cart_rejects_browser_owned_commercial_fields(mixed_catalogs, rate_rows, field):
    row = {"catalog": "tarkett", "code": "25731726", "quantity": "1", field: "tampered"}
    with pytest.raises(ValueError, match="Campo mixto no permitido"):
        build_mixed_catalog_cart_payload([row], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))


def test_mixed_cart_distinguishes_supplier_configurations(mixed_catalogs, rate_rows):
    first = {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "base_option_id": "base-a", "add_on_option_ids": ["addon-a"]}
    second = {**first, "add_on_option_ids": ["addon-b"]}
    item = mixed_catalogs["alma"]["items"][0]
    item["base_price_options"] = [
        {"id": "base-a", "name": "Base A", "price_net": "100.000000", "available": True},
        {"id": "base-b", "name": "Base B", "price_net": "100.000000", "available": True},
    ]
    item["add_on_options"] = [
        {"id": "addon-a", "name": "A", "family": "a", "price_net": "1.000000", "available": True},
        {"id": "addon-b", "name": "B", "family": "b", "price_net": "2.000000", "available": True},
    ]
    payload = build_mixed_catalog_cart_payload([first, second], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert [item["canonical_key"] for item in payload["groups"][0]["items"]] == [
        'alma:["alma:desk-1","base-a",["addon-a"]]', 'alma:["alma:desk-1","base-a",["addon-b"]]',
    ]


@pytest.mark.parametrize("row", [
    {"catalog": "tarkett", "quantity": "1"}, {"catalog": "offiho", "quantity": "1"},
    {"catalog": "alma", "quantity": "1"}, {"catalog": "alma", "internal_id": "alma:desk-1"},
])
def test_mixed_cart_requires_canonical_identity_fields(row):
    with pytest.raises(ValueError, match="Campo mixto requerido"):
        preflight_mixed_catalog_items([row])


def test_mixed_cart_normalizes_missing_supplier_configuration():
    normalized = preflight_mixed_catalog_items([{"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "1"}])[0]
    assert normalized["base_option_id"] == ""
    assert normalized["add_on_option_ids"] == []


@pytest.mark.parametrize("quantity", (1, 1.5))
def test_mixed_cart_accepts_json_numeric_quantity_before_builder_rules(quantity):
    normalized = preflight_mixed_catalog_items([{"catalog": "tarkett", "code": "25731726", "quantity": quantity}])
    assert normalized[0]["quantity"] == quantity


@pytest.mark.parametrize("quantity", (True, False, float("nan"), float("inf"), [], {}, None))
def test_mixed_cart_rejects_non_finite_or_non_json_quantity_types(quantity):
    with pytest.raises(ValueError, match="quantity invalida"):
        preflight_mixed_catalog_items([{"catalog": "tarkett", "code": "25731726", "quantity": quantity}])


def test_mixed_cart_uses_json_tuple_keys_to_avoid_delimiter_collisions():
    left = mixed_cart_key({"catalog": "alma", "internal_id": "a|b", "base_option_id": "c", "add_on_option_ids": []})
    right = mixed_cart_key({"catalog": "alma", "internal_id": "a", "base_option_id": "b|c", "add_on_option_ids": []})
    assert left != right


@pytest.mark.parametrize("identity", ("x" * 1001, "bad\x00", "bad\x7f", "bad\u200b", "bad\ud800"))
def test_mixed_cart_rejects_oversized_and_control_identities_before_builders(identity):
    with pytest.raises(ValueError, match="code invalido"):
        preflight_mixed_catalog_items([{"catalog": "tarkett", "code": identity, "quantity": "1"}])


@pytest.mark.parametrize(
    "row",
    [
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "x" * 65},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "add_on_option_ids": [f"a{i}" for i in range(201)]},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "add_on_option_ids": ["x" * 501]},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "add_on_option_ids": ["bad\ud800"]},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "add_on_option_ids": ["dup", "dup"]},
    ],
)
def test_mixed_cart_rejects_bounded_configuration_before_builders(row):
    with pytest.raises(ValueError):
        preflight_mixed_catalog_items([row])


def test_mixed_cart_allows_duplicate_canonical_rows_with_legacy_occurrence_ids(
    mixed_catalogs,
    rate_rows,
):
    row = {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"}
    payload = build_mixed_catalog_cart_payload(
        [row, dict(row)],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )

    assert [line["line_id"] for line in payload["groups"][0]["items"]] == [
        "legacy-1", "legacy-2",
    ]
    assert len({line["canonical_key"] for line in payload["groups"][0]["items"]}) == 1


def test_mixed_cart_sorts_add_ons_before_supplier_builder(monkeypatch, mixed_catalogs, rate_rows):
    import mobiliti_saas.quote_engine.mixed_catalog as mixed_module

    item = mixed_catalogs["alma"]["items"][0]
    item["add_on_options"] = [
        {"id": "a", "name": "A", "family": "a", "price_net": "1.000000", "available": True},
        {"id": "b", "name": "B", "family": "b", "price_net": "1.000000", "available": True},
    ]
    original = mixed_module.build_supplier_cart_payload
    seen = []
    def capture(rows, *args, **kwargs):
        seen.append(rows[0]["add_on_option_ids"])
        return original(rows, *args, **kwargs)
    monkeypatch.setattr(mixed_module, "build_supplier_cart_payload", capture)
    build_mixed_catalog_cart_payload([{"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "add_on_option_ids": ["b", "a"]}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert seen == [["a", "b"]]


@pytest.mark.parametrize("discount", ("-0.01", "100.01", "NaN", "Infinity", "texto"))
def test_mixed_cart_rejects_invalid_commercial_discount(mixed_catalogs, rate_rows, discount):
    with pytest.raises(ValueError, match="Descuento comercial"):
        build_mixed_catalog_cart_payload([{"catalog": "tarkett", "code": "25731726", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent=discount, today=date(2026, 7, 19))


def test_mixed_reservations_aggregate_configurations(mixed_catalogs, rate_rows):
    item = mixed_catalogs["alma"]["items"][0]
    item["base_price_options"] = [
        {"id": "base-a", "name": "Base A", "price_net": "100.000000", "available": True},
        {"id": "base-b", "name": "Base B", "price_net": "100.000000", "available": True},
    ]
    payload = build_mixed_catalog_cart_payload([
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "base_option_id": "base-a", "add_on_option_ids": []},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "2", "base_option_id": "base-b", "add_on_option_ids": []},
    ], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert len(payload["groups"][0]["items"]) == 2
    assert build_mixed_reservation_groups(payload) == [{"catalog": "alma", "items": [{"identity": "alma:desk-1", "sku": "ALMA:DESK-1", "quantity": "3.000000", "stock": "5.000000"}]}]


@pytest.mark.parametrize(
    ("quote_currency", "alma_price", "sonara_price", "automatic_rate"),
    (("MXN", "1850.00", "100.00", "1.000000"), ("USD", "100.00", "5.41", "0.054054"), ("EUR", "90.24", "4.88", "0.048780")),
)
def test_mixed_cart_freezes_conversion_without_double_conversion(mixed_catalogs, rate_rows, quote_currency, alma_price, sonara_price, automatic_rate):
    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "tarkett", "code": "25731726", "quantity": "1"}, {"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "1"}, {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"}],
        catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency=quote_currency, commercial_discount_percent="40", today=date(2026, 7, 19),
    )
    lines = {group["catalog"]: group["items"][0] for group in payload["groups"]}
    assert (lines["alma"]["unit_price"], lines["sonara"]["unit_price"]) == (alma_price, sonara_price)
    assert lines["alma"]["original_currency"] == "USD"
    assert lines["sonara"]["original_currency"] == "MXN"
    assert payload["auto_electrification_rate"]["exchange_rate"] == automatic_rate


def test_mixed_cart_preserves_per_line_discount_audit_by_price_mode(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload(browser_rows_for_all_catalogs(), catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    discounts = {group["catalog"]: group["items"][0]["discount_percent"] for group in payload["groups"]}
    assert set(discounts) == set(MIXED_CATALOG_ORDER)
    assert discounts == {
        "tarkett": "40.000000",
        "offiho": "40.000000",
        "cr-global": "0.000000",
        "sonara": "0.000000",
        "sunon": "0.000000",
        "alma": "0.000000",
        "lumbro": "0.000000",
        "jome": "0.000000",
        "lauco": "0.000000",
    }


def test_mixed_cart_rejects_non_sixteen_percent_tax(mixed_catalogs, rate_rows):
    mixed_catalogs["alma"]["items"][0]["tax_rate"] = "0.080000"
    with pytest.raises(ValueError, match="IVA 16"):
        build_mixed_catalog_cart_payload([{"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))


@pytest.mark.parametrize("catalog", MIXED_CATALOG_ORDER)
def test_mixed_payload_rejects_wrong_base_currency_for_every_catalog(frozen_mixed_payload, catalog):
    payload = deepcopy(frozen_mixed_payload)
    group = next(group for group in payload["groups"] if group["catalog"] == catalog)
    group["base_currency"] = "USD" if group["base_currency"] == "MXN" else "MXN"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("catalog", MIXED_CATALOG_ORDER)
def test_mixed_catalog_reservations_have_authoritative_identities(frozen_mixed_payload, catalog):
    line = next(group for group in frozen_mixed_payload["groups"] if group["catalog"] == catalog)["items"][0]
    reservation = line["reservation"]
    assert set(reservation) == {"identity", "sku", "quantity", "stock"}
    if catalog == "tarkett":
        assert reservation["identity"] == line["code"]
    elif catalog == "offiho":
        assert reservation["identity"] == line["canonical_key"].split(":", 1)[1]
    else:
        assert reservation["identity"] == json.loads(line["canonical_key"].split(":", 1)[1])[0]


@pytest.mark.parametrize("catalog", MIXED_CATALOG_ORDER)
def test_mixed_line_projection_has_exact_contract_for_each_family(frozen_mixed_payload, catalog):
    expected_by_catalog = {
        "tarkett": {"canonical_key":"tarkett:25731726","catalog":"tarkett","supplier":"Tarkett","code":"25731726","name":"Piso Tarkett","description":"","unit":"m2","quantity":"1.000000","unit_price":"100.00","discount_percent":"40.000000","original_currency":"MXN","original_unit_price":"100.000000","frozen_exchange_rate":"1.000000","source_reference":"tarkett:" + "a" * 64 + ":25731726","price_mode":"list","auto_electrification":True,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"","attributes":{},"variant":"","availability_type":"stocked","available_quantity":"10.000000","stock":"10.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"25731726","sku":"25731726","quantity":"1.000000","stock":"10.000000"}},
        "offiho": {"canonical_key":"offiho:offiho:desk-1","catalog":"offiho","supplier":"Offiho","code":"OHE-1","name":"Escritorio","description":"","unit":"PZA","quantity":"1.000000","unit_price":"200.00","discount_percent":"40.000000","original_currency":"MXN","original_unit_price":"200.000000","frozen_exchange_rate":"1.000000","source_reference":"offiho:" + "b" * 64 + ":offiho:desk-1","price_mode":"list","auto_electrification":True,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"","attributes":{},"variant":"Negro","availability_type":"stocked","available_quantity":"8.000000","stock":"8.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"offiho:desk-1","sku":"OHE-1","quantity":"1.000000","stock":"8.000000"}},
        "cr-global": {"canonical_key":"cr-global:[\"cr-global:desk-1\",\"\",[]]","catalog":"cr-global","supplier":"CR Global","code":"CR-GLOBAL:DESK-1","name":"Producto cr-global","description":"","unit":"pieza","quantity":"1.000000","unit_price":"100.00","discount_percent":"0.000000","original_currency":"MXN","original_unit_price":"100.000000","frozen_exchange_rate":"1.000000","source_reference":"cr-global:source","price_mode":"net","auto_electrification":False,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"Standard","attributes":{},"variant":"","availability_type":"stocked","available_quantity":"5.000000","stock":"5.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"cr-global:desk-1","sku":"CR-GLOBAL:DESK-1","quantity":"1.000000","stock":"5.000000"}},
        "sonara": {"canonical_key":"sonara:[\"sonara:desk-1\",\"\",[]]","catalog":"sonara","supplier":"Sonara","code":"SONARA:DESK-1","name":"Producto sonara","description":"","unit":"pieza","quantity":"1.000000","unit_price":"100.00","discount_percent":"0.000000","original_currency":"MXN","original_unit_price":"100.000000","frozen_exchange_rate":"1.000000","source_reference":"sonara:source","price_mode":"net","auto_electrification":False,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"Standard","attributes":{},"variant":"","availability_type":"stocked","available_quantity":"5.000000","stock":"5.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"sonara:desk-1","sku":"SONARA:DESK-1","quantity":"1.000000","stock":"5.000000"}},
        "sunon": {"canonical_key":"sunon:[\"sunon:desk-1\",\"\",[]]","catalog":"sunon","supplier":"Sunon","code":"SUNON:DESK-1","name":"Producto sunon","description":"","unit":"pieza","quantity":"1.000000","unit_price":"1850.00","discount_percent":"0.000000","original_currency":"USD","original_unit_price":"100.000000","frozen_exchange_rate":"18.500000","source_reference":"sunon:source","price_mode":"net","auto_electrification":False,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"Standard","attributes":{},"variant":"","availability_type":"stocked","available_quantity":"5.000000","stock":"5.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"sunon:desk-1","sku":"SUNON:DESK-1","quantity":"1.000000","stock":"5.000000"}},
        "alma": {"canonical_key":"alma:[\"alma:desk-1\",\"\",[]]","catalog":"alma","supplier":"ALMA","code":"ALMA:DESK-1","name":"Producto alma","description":"","unit":"pieza","quantity":"1.000000","unit_price":"1850.00","discount_percent":"0.000000","original_currency":"USD","original_unit_price":"100.000000","frozen_exchange_rate":"18.500000","source_reference":"alma:source","price_mode":"net","auto_electrification":False,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"Standard","attributes":{},"variant":"","availability_type":"stocked","available_quantity":"5.000000","stock":"5.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"alma:desk-1","sku":"ALMA:DESK-1","quantity":"1.000000","stock":"5.000000"}},
        "lumbro": {"canonical_key":"lumbro:[\"lumbro:desk-1\",\"\",[]]","catalog":"lumbro","supplier":"Lumbro","code":"LUMBRO:DESK-1","name":"Producto lumbro","description":"","unit":"pieza","quantity":"1.000000","unit_price":"100.00","discount_percent":"0.000000","original_currency":"MXN","original_unit_price":"100.000000","frozen_exchange_rate":"1.000000","source_reference":"lumbro:source","price_mode":"net","auto_electrification":False,"tax_rate":"0.160000","image_url":"","product_url":"","warnings":[],"code_status":"verified","configuration":"Standard","attributes":{},"variant":"","availability_type":"stocked","available_quantity":"5.000000","stock":"5.000000","lead_time":"","price_source":"catalog","stock_status":"available","image_kind":"placeholder","reservation":{"identity":"lumbro:desk-1","sku":"LUMBRO:DESK-1","quantity":"1.000000","stock":"5.000000"}},
    }
    for additional_catalog, supplier_label in (
        ("jome", "JOME"),
        ("lauco", "Lauco"),
    ):
        expected = deepcopy(expected_by_catalog["lumbro"])
        expected.update({
            "canonical_key": (
                f'{additional_catalog}:["{additional_catalog}:desk-1","",[]]'
            ),
            "catalog": additional_catalog,
            "supplier": supplier_label,
            "code": f"{additional_catalog.upper()}:DESK-1",
            "name": f"Producto {additional_catalog}",
            "source_reference": f"{additional_catalog}:source",
            "reservation": {
                "identity": f"{additional_catalog}:desk-1",
                "sku": f"{additional_catalog.upper()}:DESK-1",
                "quantity": "1.000000",
                "stock": "5.000000",
            },
        })
        expected_by_catalog[additional_catalog] = expected
    line = next(group for group in frozen_mixed_payload["groups"] if group["catalog"] == catalog)["items"][0]
    expected_by_catalog[catalog]["line_id"] = (
        f"legacy-{MIXED_CATALOG_ORDER.index(catalog) + 1}"
    )
    assert line == expected_by_catalog[catalog]


def test_mixed_cart_offiho_missing_price_insufficient_stock_preserves_all_authoritative_fields(mixed_catalogs, rate_rows):
    item = OffihoCatalogItem("offiho:short", "OHE-S", "Escritorio", "Nogal", "PZA", Decimal("1"), Decimal("2"), Decimal("200"), price_source="missing")
    mixed_catalogs["offiho"] = {"source_hash": "b" * 64, "items": [item], "by_inventory_key": {item.inventory_key: item}}
    payload = build_mixed_catalog_cart_payload([{"catalog": "offiho", "inventory_key": "offiho:short", "quantity": "3"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    line = payload["groups"][0]["items"][0]
    assert line["variant"] == "Nogal"
    assert line["warnings"].count("Precio por confirmar") == 1
    assert line["stock_status"] == "insufficient_stock"
    assert line["quantity"] == line["reservation"]["quantity"] == "3.000000"
    assert line["available_quantity"] == line["stock"] == line["reservation"]["stock"] == "2.000000"
    assert line["reservation"] == {"identity": "offiho:short", "sku": "OHE-S", "quantity": "3.000000", "stock": "2.000000"}


def test_mixed_cart_rejects_divergent_eligible_snapshots_during_constructor(monkeypatch, mixed_catalogs, rate_rows):
    import mobiliti_saas.quote_engine.mixed_catalog as module
    from mobiliti_saas.quote_engine.supplier_catalog import RateSnapshot
    snapshots = iter((
        RateSnapshot("MXN", "USD", Decimal("0.054054"), "saas_exchange_rates", date(2026, 7, 19), "2026-07-19T12:00:00+00:00"),
        RateSnapshot("MXN", "USD", Decimal("0.050000"), "saas_exchange_rates", date(2026, 7, 19), "2026-07-19T12:00:00+00:00"),
    ))
    monkeypatch.setattr(module, "resolve_conversion_rate", lambda *args: next(snapshots))
    with pytest.raises(ValueError, match="Tasa de electrificacion mixta inconsistente"):
        build_mixed_catalog_cart_payload([{"catalog": "tarkett", "code": "25731726", "quantity": "1"}, {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="USD", commercial_discount_percent="40", today=date(2026, 7, 19))


@pytest.mark.parametrize("catalog", MIXED_CATALOG_ORDER[2:])
def test_mixed_constructor_rejects_wrong_base_currency_in_source_fixture(mixed_catalogs, rate_rows, catalog):
    item = mixed_catalogs[catalog]["items"][0]
    item["base_currency"] = "USD" if item["base_currency"] == "MXN" else "MXN"
    with pytest.raises(ValueError, match=catalog):
        build_mixed_catalog_cart_payload([{"catalog": catalog, "internal_id": f"{catalog}:desk-1", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))


@pytest.mark.parametrize(("catalog", "row"), (("tarkett", {"catalog":"tarkett","code":"25731726","quantity":"1"}), ("offiho", {"catalog":"offiho","inventory_key":"offiho:desk-1","quantity":"1"})))
def test_mixed_constructor_rejects_wrong_direct_rate_base_currency(monkeypatch, mixed_catalogs, rate_rows, catalog, row):
    import mobiliti_saas.quote_engine.mixed_catalog as module
    from mobiliti_saas.quote_engine.supplier_catalog import RateSnapshot
    monkeypatch.setattr(module, "resolve_conversion_rate", lambda *args: RateSnapshot("USD", "MXN", Decimal("1.000000"), "saas_exchange_rates", date(2026, 7, 19), "2026-07-19T12:00:00+00:00"))
    with pytest.raises(ValueError, match=catalog):
        build_mixed_catalog_cart_payload([row], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))


def test_quote_engine_module_copies_are_byte_identical():
    for module in ("catalog_cart.py", "mixed_catalog.py", "quotation_import.py"):
        paths = [
            Path("mobiliti_saas/quote_engine") / module,
            Path("mobiliti_saas/web/mobiliti_saas/quote_engine") / module,
        ]
        assert len({hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}) == 1


@pytest.fixture
def frozen_mixed_payload(mixed_catalogs, rate_rows):
    return build_mixed_catalog_cart_payload(
        browser_rows_for_all_catalogs(), catalogs=mixed_catalogs, rate_rows=rate_rows,
        quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19),
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(item_count=999), "Conteo mixto inconsistente"),
        (lambda payload: payload.update(rate_summary=[]), "Resumen de tasas mixtas inconsistente"),
        (lambda payload: payload["groups"].reverse(), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0].update(catalog_source_hash="A" * 64), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0].update(rate_retrieved_at="x"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0]["items"][0].update(supplier="spoof"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][2]["items"][0].update(price_mode="list"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][2]["items"][0].update(auto_electrification="true"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0]["items"][0].update(product_url="http://sonara.mx/panel"), "Grupos mixtos invalidos"),
    ],
)
def test_mixed_payload_rejects_tampering(frozen_mixed_payload, mutate, message):
    payload = deepcopy(frozen_mixed_payload)
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_reservation_results_without_reservation(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][4]["items"][0]
    line["availability_type"] = "made_to_order"
    line["available_quantity"] = None
    line["stock"] = None
    line["stock_status"] = ""
    line["reservation"] = None
    line.update(reserved_quantity="0.000000", available_after_reservations="0.000000", reserved_by_others=False)
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda line: line.update(code_status="needs_review", warnings=["Codigo por verificar"]), "Grupos mixtos invalidos"),
        (lambda line: line.update(stock_status="insufficient_stock"), "Grupos mixtos invalidos"),
    ],
)
def test_mixed_payload_preserves_tarkett_authoritative_semantics(frozen_mixed_payload, mutate, message):
    payload = deepcopy(frozen_mixed_payload)
    mutate(payload["groups"][0]["items"][0])
    with pytest.raises(ValueError, match=message):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_structurally_coherent_tarkett_insufficient_reservation(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][0]["items"][0]
    line.update(
        quantity="11.000000", stock_status="insufficient_stock",
        reserved_quantity="0.000000", available_after_reservations="10.000000", reserved_by_others=False,
    )
    line["reservation"]["quantity"] = "11.000000"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_tarkett_insufficient_without_reservation_snapshot(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][0]["items"][0]
    line.update(quantity="11.000000", stock_status="insufficient_stock")
    line["reservation"]["quantity"] = "11.000000"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("catalog", ("tarkett", "offiho"))
def test_mixed_payload_rejects_review_status_for_legacy_catalogs(frozen_mixed_payload, catalog):
    payload = deepcopy(frozen_mixed_payload)
    line = next(group for group in payload["groups"] if group["catalog"] == catalog)["items"][0]
    line.update(code_status="needs_review", warnings=["Codigo por verificar"])
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("catalog", ("cr-global", "sunon", "alma"))
def test_mixed_payload_accepts_review_status_for_supplier_catalogs(frozen_mixed_payload, catalog):
    payload = deepcopy(frozen_mixed_payload)
    line = next(group for group in payload["groups"] if group["catalog"] == catalog)["items"][0]
    line.update(code_status="needs_review", warnings=["Codigo por verificar"])
    validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("catalog", ("sonara", "lumbro"))
def test_mixed_payload_allows_review_status_only_for_review_families(frozen_mixed_payload, catalog):
    payload = deepcopy(frozen_mixed_payload)
    line = next(group for group in payload["groups"] if group["catalog"] == catalog)["items"][0]
    line.update(code="", code_status="needs_review", warnings=["Codigo por verificar"])
    line["reservation"]["sku"] = ""
    assert validate_mixed_catalog_payload(payload) is payload


@pytest.mark.parametrize("currency", ([], {}, 1, None))
def test_mixed_payload_rejects_non_string_quote_currency_stably(frozen_mixed_payload, currency):
    payload = deepcopy(frozen_mixed_payload)
    payload["quote_currency"] = currency
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        build_mixed_catalog_cart_payload(
            [{"catalog": "tarkett", "code": "25731726", "quantity": "1"}], catalogs={},
            rate_rows=[], quote_currency=currency, commercial_discount_percent="40", today=date(2026, 7, 19),
        )


@pytest.mark.parametrize("field", ("image_url", "product_url"))
@pytest.mark.parametrize("url", ("http://sonara.mx/panel", "https://user:pass@sonara.mx/panel", "https://sonara.mx:444/panel"))
def test_mixed_payload_rejects_noncommercial_url_shapes(frozen_mixed_payload, field, url):
    payload = deepcopy(frozen_mixed_payload)
    payload["groups"][2]["items"][0][field] = url
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_allows_only_exact_published_dev_asset_url(
    frozen_mixed_payload, monkeypatch, tmp_path
):
    object_name = f"{'a' * 64}.png"
    project_root = tmp_path / "repo"
    (project_root / ".git").mkdir(parents=True)
    module_dir = project_root / "mobiliti_saas" / "quote_engine"
    module_dir.mkdir(parents=True)
    monkeypatch.setattr(catalog_cart, "__file__", str(module_dir / "catalog_cart.py"))
    asset_dir = project_root / ".mobiliti_dev_store" / "catalog-assets"
    asset_dir.mkdir(parents=True)
    (asset_dir / object_name).write_bytes(b"published-dev-asset")
    monkeypatch.setenv("MOBILITI_DEV_MODE", "1")
    monkeypatch.setenv("MOBILITI_DEV_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.delenv("MOBILITI_DEV_STORE_DIR", raising=False)

    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][2]["items"][0]
    line["image_url"] = f"http://127.0.0.1:8000/dev/catalog-assets/{object_name}"
    assert validate_mixed_catalog_payload(payload) is payload

    for invalid_url in (
        f"http://localhost:8000/dev/catalog-assets/{object_name}",
        f"http://127.0.0.1:8000/other/{object_name}",
        f"http://127.0.0.1:8000/dev/catalog-assets/{object_name}?raw=1",
    ):
        line["image_url"] = invalid_url
        with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
            validate_mixed_catalog_payload(payload)

    line["image_url"] = f"http://127.0.0.1:8000/dev/catalog-assets/{object_name}"
    monkeypatch.delenv("MOBILITI_DEV_MODE")
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_deep_attributes(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    attributes = {"leaf": "value"}
    for _ in range(9):
        attributes = {"nested": attributes}
    payload["groups"][2]["items"][0]["attributes"] = attributes
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_cart_preserves_review_missing_price_and_generated_reference_warnings(mixed_catalogs, rate_rows):
    sonara = mixed_catalogs["sonara"]["items"][0]
    sonara.update(code_status="needs_review", sku="", warnings=["código por verificar"], image_kind="generated_reference", image_url="https://images.example.test/sonara.png")
    offiho = mixed_catalogs["offiho"]["items"][0]
    mixed_catalogs["offiho"]["items"] = [OffihoCatalogItem(offiho.inventory_key, offiho.code, offiho.name, offiho.variant, offiho.unit, offiho.pieces_per_box, offiho.available_quantity, offiho.unit_price, price_source="missing")]
    mixed_catalogs["offiho"]["by_inventory_key"] = {offiho.inventory_key: mixed_catalogs["offiho"]["items"][0]}
    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "1"}, {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"}],
        catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19),
    )
    lines = {group["catalog"]: group["items"][0] for group in payload["groups"]}
    assert "Codigo por verificar" in lines["sonara"]["warnings"]
    assert "Imagen de referencia" in lines["sonara"]["warnings"]
    assert "Precio por confirmar" in lines["offiho"]["warnings"]


def test_mixed_cart_accepts_alma_review_code_with_pending_price(mixed_catalogs, rate_rows):
    alma = mixed_catalogs["alma"]["items"][0]
    alma.update(
        code_status="needs_review",
        sku="",
        price_net="0.000000",
        base_price_options=[],
        add_on_options=[],
        warnings=[],
    )

    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"}],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="USD",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )

    line = payload["groups"][0]["items"][0]
    assert line["code_status"] == "needs_review"
    assert line["code"] == ""
    assert line["price_source"] == "missing"
    assert line["original_unit_price"] == "0.000000"
    assert line["unit_price"] == "0.00"
    assert "Codigo por verificar" in line["warnings"]
    assert "Precio por confirmar" in line["warnings"]
    validate_mixed_catalog_payload(payload)


def test_mixed_cart_preserves_supplier_warning_alongside_review_warning(
    mixed_catalogs, rate_rows
):
    sonara = mixed_catalogs["sonara"]["items"][0]
    sonara.update(
        code_status="needs_review",
        sku="",
        warnings=["Revision documental local"],
    )

    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "1"}],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )

    assert payload["groups"][0]["items"][0]["warnings"] == [
        "Revision documental local",
        "Codigo por verificar",
    ]


@pytest.mark.parametrize(("catalog", "availability"), (("alma", "made_to_order"), ("sunon", "made_to_order"), ("sonara", "unknown")))
def test_mixed_cart_keeps_nonstocked_supplier_lines_without_reservation(mixed_catalogs, rate_rows, catalog, availability):
    item = mixed_catalogs[catalog]["items"][0]
    item.update(availability_type=availability, stock=None)
    payload = build_mixed_catalog_cart_payload([{"catalog": catalog, "internal_id": f"{catalog}:desk-1", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    line = payload["groups"][0]["items"][0]
    assert (line["availability_type"], line["reservation"], line["stock_status"]) == (availability, None, "")


def test_mixed_payload_keeps_commercial_product_url_inert(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][2]["items"][0]
    line["product_url"] = "https://sonara.mx/producto/panel"
    assert validate_mixed_catalog_payload(payload) is payload
    line["image_url"] = "https://sonara.mx/producto/panel"
    assert validate_mixed_catalog_payload(payload) is payload


def test_mixed_payload_identity_rate_requires_empty_retrieval_timestamp(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    for group in payload["groups"]:
        if group["base_currency"] == "MXN":
            group.update(exchange_rate="1.000000", rate_source="identity", rate_retrieved_at="")
            for line in group["items"]:
                line.update(frozen_exchange_rate="1.000000")
    payload["rate_summary"] = [{key: group[key] for key in ("catalog", "base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at")} for group in payload["groups"]]
    payload["auto_electrification_rate"] = {key: payload["groups"][0][key] for key in ("base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at")}
    assert validate_mixed_catalog_payload(payload) is payload
    payload["groups"][0]["rate_retrieved_at"] = "2026-07-19T12:00:00+00:00"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("field", ("base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at"))
def test_mixed_payload_rejects_each_tampered_auto_electrification_rate(frozen_mixed_payload, field):
    payload = deepcopy(frozen_mixed_payload)
    payload["auto_electrification_rate"][field] = "tampered"
    with pytest.raises(ValueError, match="Tasa de electrificacion mixta invalida"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_missing_or_extra_automatic_snapshot_fields(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    payload["auto_electrification_rate"].pop("rate_source")
    with pytest.raises(ValueError, match="Tasa de electrificacion mixta invalida"):
        validate_mixed_catalog_payload(payload)
    payload = deepcopy(frozen_mixed_payload)
    payload["auto_electrification_rate"]["unexpected"] = "x"
    with pytest.raises(ValueError, match="Tasa de electrificacion mixta invalida"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("field", ("unit_price", "frozen_exchange_rate"))
def test_mixed_payload_rejects_tampered_converted_price_or_rate(frozen_mixed_payload, field):
    payload = deepcopy(frozen_mixed_payload)
    payload["groups"][5]["items"][0][field] = "9.990000" if field == "frozen_exchange_rate" else "9.99"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_empty_nonidentity_timestamp(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    group = payload["groups"][5]
    group["rate_retrieved_at"] = ""
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_oversized_serialized_payload(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    payload["groups"][2]["items"][0]["description"] = "x" * 5_000_001
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_cart_tarkett_only_copies_exact_automatic_snapshot(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload([{"catalog": "tarkett", "code": "25731726", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="USD", commercial_discount_percent="40", today=date(2026, 7, 19))
    fields = ("base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at")
    assert payload["auto_electrification_rate"] == {field: payload["groups"][0][field] for field in fields}


def test_mixed_cart_tarkett_and_offiho_share_exact_automatic_snapshot(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload([{"catalog": "tarkett", "code": "25731726", "quantity": "1"}, {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="EUR", commercial_discount_percent="40", today=date(2026, 7, 19))
    fields = ("base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at")
    snapshots = [{field: group[field] for field in fields} for group in payload["groups"]]
    assert snapshots[0] == snapshots[1] == payload["auto_electrification_rate"]


def test_mixed_cart_alma_only_does_not_lookup_mxn_rate(monkeypatch, mixed_catalogs, rate_rows):
    import mobiliti_saas.quote_engine.mixed_catalog as module
    monkeypatch.setattr(module, "resolve_conversion_rate", lambda *args: (_ for _ in ()).throw(AssertionError("no MXN lookup")))
    payload = build_mixed_catalog_cart_payload([{"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"}], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="USD", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert payload["auto_electrification_rate"] is None


@pytest.mark.parametrize("field", ("original_currency", "quote_currency"))
def test_mixed_payload_rejects_individually_valid_but_inconsistent_currencies(frozen_mixed_payload, field):
    payload = deepcopy(frozen_mixed_payload)
    if field == "original_currency":
        payload["groups"][5]["items"][0][field] = "MXN"
    else:
        payload["groups"][5][field] = "EUR"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_nonzero_line_discount_for_net_price_mode(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    payload["groups"][2]["items"][0]["discount_percent"] = "1.000000"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_boolean_item_count(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    payload["groups"] = payload["groups"][:1]
    payload["rate_summary"] = payload["rate_summary"][:1]
    payload["auto_electrification_rate"] = {
        key: payload["groups"][0][key]
        for key in (
            "base_currency", "quote_currency", "exchange_rate", "rate_source",
            "rate_effective_date", "rate_retrieved_at",
        )
    }
    payload["item_count"] = True

    with pytest.raises(ValueError, match="Conteo mixto inconsistente"):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_rejects_missing_insufficient_warning_after_valid_reservation_result(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][2]["items"][0]
    line.update(quantity="6.000000", stock_status="insufficient_stock", reserved_quantity="0.000000", available_after_reservations="5.000000", reserved_by_others=False, warnings=[])
    line["reservation"]["quantity"] = "6.000000"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)
