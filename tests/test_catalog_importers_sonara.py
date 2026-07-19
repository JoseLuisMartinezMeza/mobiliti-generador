import hashlib
import io
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import fitz
import pytest
from PIL import Image

from mobiliti_saas.quote_engine.supplier_catalog import (
    PUBLIC_ITEM_FIELDS,
    build_supplier_cart_payload,
    load_supplier_catalog_data,
)
from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError
from mobiliti_saas.worker.catalog_sync.importers import sonara
from mobiliti_saas.worker.catalog_sync.importers.sonara import (
    build_sonara_snapshot,
    build_sonara_snapshot_with_assets,
)


@dataclass(frozen=True)
class AdapterFile:
    path: str
    kind: str
    brand: str | None
    sha256: str
    mime_type: str
    local_path: Path | None


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(output, "PNG")
    return output.getvalue()


def _write_price_pdf(path, rows, *, currency="Moneda: MXN", notice="Precios mas IVA"):
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    for x, label in ((120, "PRODUCTO"), (300, "DESCRIPCION"), (500, "PRECIO DE LISTA")):
        page.insert_text((x, 65), label, fontsize=9)
    page.insert_text((120, 85), currency, fontsize=8)
    page.insert_text((300, 85), notice, fontsize=8)

    # Insert by column, with descriptions reversed, so flattened text order is unusable.
    for index, row in enumerate(rows):
        y = 125 + index * 55
        page.insert_text((120, y), row[0], fontsize=9)
    for index in reversed(range(len(rows))):
        y = 125 + index * 55
        page.insert_text((300, y), rows[index][1], fontsize=9)
    for index, row in enumerate(rows):
        y = 125 + index * 55
        page.insert_text((500, y), f"$ {row[2]}", fontsize=9)
    document.save(path)
    document.close()


def _write_catalog_pdf(path, records):
    document = fitz.open()
    for record in records:
        page = document.new_page(width=612, height=792)
        page.insert_text((60, 80), record["label"], fontsize=16)
        page.insert_textbox(fitz.Rect(60, 100, 550, 190), record["description"], fontsize=10)
        page.insert_image(fitz.Rect(80, 220, 380, 445), stream=_png_bytes(record["color"]))
    document.save(path)
    document.close()


def _bundle(price_list, catalog):
    return (
        AdapterFile(
            "SONARA/Catalogo-Sonara.pdf",
            "catalog",
            None,
            _sha256(catalog),
            "application/pdf",
            catalog,
        ),
        AdapterFile(
            "SONARA/Lista de precios Sonara 2026.pdf",
            "price_list",
            None,
            _sha256(price_list),
            "application/pdf",
            price_list,
        ),
    )


@pytest.fixture
def source_bundle(tmp_path):
    price_list = tmp_path / "Lista de precios Sonara 2026.pdf"
    catalog = tmp_path / "Catalogo-Sonara.pdf"
    _write_price_pdf(
        price_list,
        (
            ("SCC018 Celosia", "Panel acustico 12 mm Unidad: M2", "1,880.00"),
            ("Panel Alpha", "Panel decorativo 9 mm", "2,570.00"),
        ),
    )
    _write_catalog_pdf(
        catalog,
        (
            {
                "label": "Panel Tipo Celosia Modelo SCC018",
                "description": "Ficha oficial: panel acustico de 12 mm para interiores.",
                "color": "red",
            },
            {
                "label": "Panel Alpha 9 mm",
                "description": "Ficha oficial: panel decorativo Alpha de 9 mm.",
                "color": "blue",
            },
        ),
    )
    return _bundle(price_list, catalog)


def _without_generated_at(snapshot):
    return {key: value for key, value in snapshot.items() if key != "generated_at"}


def test_snapshot_contract_coordinates_authority_tax_and_determinism(source_bundle):
    first = build_sonara_snapshot(source_bundle)
    second = build_sonara_snapshot(tuple(reversed(source_bundle)))
    loaded = load_supplier_catalog_data(first, expected_supplier="sonara")

    expected_hash = hashlib.sha256(
        "\n".join(
            f"{row.path}\0{row.sha256}" for row in sorted(source_bundle, key=lambda row: row.path)
        ).encode()
    ).hexdigest()
    assert set(first) == {"supplier", "source_hash", "generated_at", "items"}
    assert first["source_hash"] == expected_hash
    assert _without_generated_at(first) == _without_generated_at(second)
    assert len(loaded["items"]) == 2
    assert all(set(item) == set(PUBLIC_ITEM_FIELDS) for item in first["items"])

    by_name = {item["name"]: item for item in first["items"]}
    celosia = by_name["SCC018 Celosia"]
    assert celosia["price_net"] == "1880.000000"
    assert celosia["tax_rate"] == "0.160000"
    assert celosia["attributes"]["source_currency_status"] == "verified"
    assert "source_currency_rule" not in celosia["attributes"]
    assert celosia["unit"] == "M2"
    assert celosia["description"].startswith("Ficha oficial:")
    assert celosia["attributes"]["row_description"] == "Panel acustico 12 mm Unidad: M2"
    assert celosia["attributes"]["image_sha256"]
    assert celosia["attributes"]["image_width"] > 0
    assert celosia["attributes"]["image_bbox"] == [80.0, 220.0, 380.0, 445.0]
    assert celosia["image_url"] == "" and celosia["image_kind"] == "placeholder"
    evidence = json.loads(celosia["source_reference"])
    assert evidence[0]["sheet_or_page"] == 1
    assert evidence[0]["cell_or_bbox"] == [120.0, 115.3, 542.5, 127.7]
    assert all(reference["file_id"] in {row.sha256 for row in source_bundle} for reference in evidence)


def test_missing_code_uses_stable_review_identity_and_unique_name_enrichment(source_bundle):
    first = build_sonara_snapshot(source_bundle)
    second = build_sonara_snapshot(tuple(reversed(source_bundle)))
    item = next(item for item in first["items"] if item["name"] == "Panel Alpha")
    same = next(row for row in second["items"] if row["name"] == "Panel Alpha")

    assert item["sku"] == ""
    assert item["code_status"] == "needs_review"
    assert item["internal_id"] == same["internal_id"]
    assert item["attributes"]["image_sha256"]
    assert any("codigo" in warning.lower() for warning in item["warnings"])


def test_local_review_item_crosses_supplier_cart_with_one_canonical_warning(source_bundle):
    snapshot = build_sonara_snapshot(source_bundle)
    item = next(row for row in snapshot["items"] if row["code_status"] == "needs_review")

    line = build_supplier_cart_payload(
        [{"internal_id": item["internal_id"], "quantity": "1", "add_on_option_ids": []}],
        snapshot,
        "MXN",
        [],
    )["items"][0]

    def normalized(warning):
        return " ".join(
            "".join(
                character for character in unicodedata.normalize("NFKD", warning.casefold())
                if not unicodedata.combining(character)
            ).split()
        )

    assert line["unit_price"] == "2570.00"
    assert line["sku"] == ""
    assert line["code_status"] == "needs_review"
    assert any("faltante" in warning.lower() for warning in line["warnings"])
    assert [warning for warning in line["warnings"] if normalized(warning) == "codigo por verificar"] == ["Codigo por verificar"]


def test_duplicate_normalized_catalog_name_stays_placeholder_without_image(tmp_path):
    price_list = tmp_path / "prices.pdf"
    catalog = tmp_path / "catalog.pdf"
    _write_price_pdf(price_list, (("Panel Alpha", "Panel decorativo 9 mm", "2,570.00"),))
    _write_catalog_pdf(
        catalog,
        (
            {"label": "Panel Alpha 9 mm", "description": "Ficha Alpha A", "color": "blue"},
            {"label": "Panel Alpha 9 mm", "description": "Ficha Alpha B", "color": "green"},
        ),
    )

    item = build_sonara_snapshot(_bundle(price_list, catalog))["items"][0]

    assert "image_sha256" not in item["attributes"]
    assert any("ambigu" in warning.lower() for warning in item["warnings"])


def test_conflicting_prices_are_blocked(source_bundle, tmp_path):
    catalog = source_bundle[0].local_path
    conflict = tmp_path / "conflict.pdf"
    _write_price_pdf(
        conflict,
        (
            ("SCC018 Celosia", "Panel acustico 12 mm", "1,880.00"),
            ("SCC018 Celosia", "Panel acustico 12 mm", "9,999.00"),
        ),
    )
    conflict_item = build_sonara_snapshot(_bundle(conflict, catalog))["items"][0]
    assert conflict_item["price_net"] == "0.000000"
    assert any("conflic" in warning.lower() for warning in conflict_item["warnings"])


def test_missing_currency_uses_auditable_mxn_business_override(source_bundle, tmp_path, monkeypatch):
    price_list = tmp_path / "missing-currency.pdf"
    _write_price_pdf(
        price_list,
        [("PANEL 01", "Panel acustico 60 x 120 cm", "1880.00")],
        currency="Lista vigente marzo 2026",
    )
    monkeypatch.setattr(
        sonara,
        "_SONARA_PRICE_SHA256",
        hashlib.sha256(price_list.read_bytes()).hexdigest(),
    )
    item = build_sonara_snapshot(_bundle(price_list, source_bundle[0].local_path))["items"][0]
    assert item["base_currency"] == "MXN"
    assert item["price_net"] == "1880.000000"
    assert item["tax_rate"] == "0.160000"
    assert item["attributes"]["source_currency_status"] == "business_override"
    assert item["attributes"]["source_currency_rule"] == "sonara_mxn_confirmed_2026-07-19"


@pytest.mark.parametrize(
    "declaration",
    (
        "Moneda: USD", "Moneda: EUR", "Moneda: MXN / Moneda: USD",
        "Precios USD", "US$ 1,880.00", "Precios en €",
    ),
)
def test_foreign_or_contradictory_currency_fails_closed(source_bundle, tmp_path, declaration):
    price_list = tmp_path / "rejected-currency.pdf"
    _write_price_pdf(
        price_list,
        [("PANEL 01", "Panel acustico 60 x 120 cm", "1880.00")],
        currency=declaration,
    )
    item = build_sonara_snapshot(_bundle(price_list, source_bundle[0].local_path))["items"][0]
    assert item["base_currency"] == "XXX"
    assert item["price_net"] == "0.000000"
    assert item["attributes"]["source_currency_status"] == "rejected"
    assert any("moneda" in warning.lower() for warning in item["warnings"])


def test_unrecognized_missing_currency_file_fails_closed(source_bundle, tmp_path):
    price_list = tmp_path / "untrusted-missing-currency.pdf"
    _write_price_pdf(
        price_list,
        [("PANEL 01", "Panel acustico 60 x 120 cm", "1880.00")],
        currency="Lista sin moneda",
    )
    item = build_sonara_snapshot(_bundle(price_list, source_bundle[0].local_path))["items"][0]
    assert item["base_currency"] == "XXX"
    assert item["price_net"] == "0.000000"
    assert item["attributes"]["source_currency_status"] == "rejected"


def test_currency_and_iva_require_contextual_explicit_declarations(source_bundle, tmp_path, monkeypatch):
    catalog = source_bundle[0].local_path
    price_list = tmp_path / "ambiguous-commercial-terms.pdf"
    _write_price_pdf(
        price_list,
        (("SCC018 Celosia", "Panel acustico 12 mm", "1,880.00"),),
        currency="Referencia interna MXN",
        notice="Precios vigentes",
    )
    monkeypatch.setattr(sonara, "_SONARA_PRICE_SHA256", _sha256(price_list))
    item = build_sonara_snapshot(_bundle(price_list, catalog))["items"][0]

    assert item["base_currency"] == "MXN"
    assert item["attributes"]["source_currency_status"] == "business_override"
    assert item["price_net"] == "0.000000"
    assert item["tax_rate"] == "0.000000"
    assert not any("moneda" in warning.lower() for warning in item["warnings"])
    assert any("iva" in warning.lower() for warning in item["warnings"])


def test_descriptive_number_is_not_synthesized_as_supplier_code(tmp_path):
    price_list = tmp_path / "prices.pdf"
    catalog = tmp_path / "catalog.pdf"
    _write_price_pdf(price_list, (("Panel acustico 1200", "Panel decorativo 9 mm", "2,570.00"),))
    _write_catalog_pdf(
        catalog,
        ({"label": "Panel acustico 1200", "description": "Ficha oficial sin codigo.", "color": "blue"},),
    )

    item = build_sonara_snapshot(_bundle(price_list, catalog))["items"][0]

    assert item["sku"] == ""
    assert item["code_status"] == "needs_review"


def test_words_are_classified_by_their_own_horizontal_position(tmp_path):
    words = [
        (120.0, 55.0, 165.0, 65.0, "PRODUCTO", 0, 0, 0),
        (300.0, 55.0, 360.0, 65.0, "DESCRIPCION", 1, 0, 0),
        (500.0, 55.0, 535.0, 65.0, "PRECIO", 2, 0, 0),
        (120.0, 115.0, 175.0, 126.0, "SCC018", 3, 0, 0),
        (300.0, 115.0, 340.0, 126.0, "Panel", 3, 0, 1),
    ]
    column, *_ = sonara._column_headers(words)
    assert column(words[-1]) == "description"

    price_list = tmp_path / "prices.pdf"
    catalog = tmp_path / "catalog.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    for x, label in ((120, "PRODUCTO"), (300, "DESCRIPCION"), (500, "PRECIO DE LISTA")):
        page.insert_text((x, 65), label, fontsize=9)
    page.insert_text((120, 85), "Moneda: MXN", fontsize=8)
    page.insert_text((300, 85), "Precios mas IVA", fontsize=8)
    page.insert_text(
        (120, 125),
        "SCC018 Celosia" + " " * 50 + "Panel acustico 12 mm Unidad: M2",
        fontsize=9,
    )
    page.insert_text((500, 125), "$ 1,880.00", fontsize=9)
    document.save(price_list)
    document.close()
    _write_catalog_pdf(
        catalog,
        ({"label": "Modelo SCC018", "description": "Ficha oficial del panel.", "color": "red"},),
    )

    item = build_sonara_snapshot(_bundle(price_list, catalog))["items"][0]

    assert item["name"] == "SCC018 Celosia"
    assert item["attributes"]["row_description"] == "Panel acustico 12 mm Unidad: M2"


def test_drawn_table_borders_override_centered_header_starts():
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.draw_line((273, 50), (273, 720))
    page.draw_line((490, 50), (490, 720))
    words = [
        (176.0, 55.0, 238.0, 65.0, "PRODUCTO", 0, 0, 0),
        (348.0, 55.0, 422.0, 65.0, "DESCRIPCION", 1, 0, 0),
        (502.0, 55.0, 542.0, 65.0, "PRECIO", 2, 0, 0),
        (267.0, 115.0, 310.0, 126.0, "Descripcion", 3, 0, 0),
    ]

    try:
        column, *_ = sonara._column_headers(words, page)
        assert column(words[-1]) == "description"
    finally:
        document.close()


def test_ungrouped_four_digit_price_is_not_dropped(tmp_path):
    price_list = tmp_path / "prices.pdf"
    catalog = tmp_path / "catalog.pdf"
    _write_price_pdf(price_list, (("Panel Alpha", "Panel decorativo", "1880.00"),))
    _write_catalog_pdf(
        catalog,
        ({"label": "Panel Alpha", "description": "Ficha oficial.", "color": "blue"},),
    )

    item = build_sonara_snapshot(_bundle(price_list, catalog))["items"][0]

    assert item["price_net"] == "1880.000000"


def test_pdf_geometry_parser_is_isolated_and_consumes_validated_bytes(source_bundle, monkeypatch):
    real_isolated = sonara._parse_sonara_documents_isolated
    calls = []

    def audited_isolated(price_data, catalog_data, include_assets, confirmed_price_sha256):
        calls.append((price_data, catalog_data, include_assets, confirmed_price_sha256))
        return real_isolated(price_data, catalog_data, include_assets, confirmed_price_sha256)

    monkeypatch.setattr(sonara, "_parse_sonara_documents_isolated", audited_isolated)
    monkeypatch.setattr(
        sonara,
        "_price_rows",
        lambda *_args, **_kwargs: pytest.fail("geometry parser ran in the worker process"),
    )

    build_sonara_snapshot(source_bundle)

    assert len(calls) == 1
    price_data, catalog_data, include_assets, confirmed_price_sha256 = calls[0]
    assert hashlib.sha256(price_data).hexdigest() == source_bundle[1].sha256
    assert hashlib.sha256(catalog_data).hexdigest() == source_bundle[0].sha256
    assert include_assets is False
    assert confirmed_price_sha256 == sonara._SONARA_PRICE_SHA256


def test_duplicate_logical_source_paths_are_rejected(source_bundle):
    duplicate_path = (
        source_bundle[0],
        AdapterFile(**{**source_bundle[1].__dict__, "path": source_bundle[0].path}),
    )

    with pytest.raises(ValueError, match="SONARA_BUNDLE"):
        build_sonara_snapshot(duplicate_path)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda rows: rows[:-1],
        lambda rows: rows + (rows[0],),
        lambda rows: tuple(
            AdapterFile(row.path, row.kind, row.brand, row.sha256, "text/plain", row.local_path)
            if row.kind == "catalog"
            else row
            for row in rows
        ),
        lambda rows: tuple(
            AdapterFile(row.path, row.kind, row.brand, row.sha256, row.mime_type, None)
            if row.kind == "price_list"
            else row
            for row in rows
        ),
    ),
)
def test_bundle_shape_and_mime_fail_closed(source_bundle, mutation):
    with pytest.raises((ValueError, SourceSafetyError)):
        build_sonara_snapshot(mutation(source_bundle))


def test_hash_mismatch_and_corrupt_pdf_fail_closed(source_bundle, tmp_path):
    mismatched = (
        AdapterFile(**{**source_bundle[0].__dict__, "sha256": "0" * 64}),
        source_bundle[1],
    )
    with pytest.raises((ValueError, SourceSafetyError)):
        build_sonara_snapshot(mismatched)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    with pytest.raises(SourceSafetyError):
        build_sonara_snapshot(_bundle(corrupt, source_bundle[0].local_path))


def test_ignored_real_sources_pass_contract_and_report_metrics():
    root = Path(".cache/catalog-sources/sonara")
    catalog = root / "Catalogo-Sonara.pdf"
    price_list = root / "Lista de precios Sonara 2026.pdf"
    if not catalog.exists() or not price_list.exists():
        pytest.skip("ignored Sonara source PDFs are not available")
    assert _sha256(catalog) == "35c4abd3c4b3fef5c11cb8b7b22509f9913343b9ee79bf4cc6ae9c6aac3f0099"
    assert _sha256(price_list) == "c497314221f5e700d6722deb92a3dbb02c4686e7b39e17766332bee6a6e05128"

    snapshot = build_sonara_snapshot(_bundle(price_list, catalog))
    items = load_supplier_catalog_data(snapshot, expected_supplier="sonara")["items"]
    metrics = {
        "rows": len(items),
        "images": sum("image_sha256" in item["attributes"] for item in items),
        "nonzero_prices": sum(item["price_net"] != "0.000000" for item in items),
        "blocked_prices": sum(item["price_net"] == "0.000000" for item in items),
        "verified_codes": sum(item["code_status"] == "verified" for item in items),
        "needs_review": sum(item["code_status"] == "needs_review" for item in items),
        "warnings": sum(len(item["warnings"]) for item in items),
        "currency_warnings": sum(
            any("moneda" in warning.lower() for warning in item["warnings"]) for item in items
        ),
        "ambiguity_warnings": sum(
            any("ambigua" in warning.lower() for warning in item["warnings"]) for item in items
        ),
        "conflict_warnings": sum(
            any("conflictivo" in warning.lower() for warning in item["warnings"]) for item in items
        ),
    }
    print("SONARA_REAL_METRICS=" + json.dumps(metrics, sort_keys=True))
    assert metrics["rows"] == 39
    assert metrics["nonzero_prices"] == 39
    assert metrics["blocked_prices"] == 0
    assert metrics["verified_codes"] == 7
    assert metrics["needs_review"] == 32
    assert metrics["currency_warnings"] == 0


def test_ignored_real_sources_reconcile_only_exact_sacc_variants_and_assets():
    root = Path(".cache/catalog-sources/sonara")
    catalog = root / "Catalogo-Sonara.pdf"
    price_list = root / "Lista de precios Sonara 2026.pdf"
    if not catalog.exists() or not price_list.exists():
        pytest.skip("ignored Sonara source PDFs are not available")

    build = build_sonara_snapshot_with_assets(_bundle(price_list, catalog))
    items = load_supplier_catalog_data(build.snapshot, expected_supplier="sonara")["items"]
    exact = {item["sku"]: item for item in items if item["code_status"] == "verified"}

    assert set(exact) == {
        "SACC003-01", "SACC003-02", "SACC004-01", "SACC004-02",
        "SACC005-01", "SACC005-02", "SACC006-01",
    }
    assert exact["SACC003-01"]["product_key"] == exact["SACC003-02"]["product_key"]
    assert exact["SACC003-01"]["internal_id"] != exact["SACC003-02"]["internal_id"]
    assert all(
        item["product_url"] == "https://sonara.mx/soluciones-sonara/paneles-suspendidos/"
        for item in exact.values()
    )
    assert all(
        item["attributes"]["product_url_match"]["status"] == "collection_index"
        for item in exact.values()
    )
    assert len(items) == 39
    assert all(item["base_currency"] == "MXN" for item in items)
    assert all(item["price_net"] != "0.000000" for item in items)
    assert all(
        item["attributes"]["source_currency_status"] == "business_override"
        for item in items
    )
    assert all(
        item["attributes"]["source_currency_rule"] == "sonara_mxn_confirmed_2026-07-19"
        for item in items
    )
    assert all(item["attributes"]["source_price_printed"].startswith("$") for item in items)
    assert sum(item["code_status"] == "needs_review" for item in items) == 32
    assert sum(bool(item["attributes"].get("dimensions")) for item in items) == 32
    assert len(build.bindings) == 5
    assert len(build.assets_by_sha256) == 3
    assert all(binding.match_status == "exact_pdf" for binding in build.bindings)
    assert exact["SACC004-01"]["image_kind"] == "placeholder"
    assert exact["SACC004-02"]["image_kind"] == "placeholder"
    sacc004_ids = {exact["SACC004-01"]["internal_id"], exact["SACC004-02"]["internal_id"]}
    assert not any(binding.internal_id in sacc004_ids for binding in build.bindings)
