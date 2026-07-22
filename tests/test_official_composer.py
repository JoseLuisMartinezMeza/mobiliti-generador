from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
import shutil
import sys
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as OpenpyxlImage
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.mobiliti_layout import (  # noqa: E402
    SectionNeed,
    plan_mobiliti_layout,
)
from mobiliti_saas.quote_engine import generate_quote  # noqa: E402
import mobiliti_saas.quote_engine.official_composer as official_composer_module  # noqa: E402
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage  # noqa: E402
from mobiliti_saas.quote_engine.ooxml_package import (  # noqa: E402
    relationship_part_name,
    resolve_internal_target,
)
from mobiliti_saas.quote_engine.ooxml_worksheet import (  # noqa: E402
    MobilitiCellWrite,
    MobilitiSheetMutation,
    WorksheetEditor,
    build_mobiliti_sheet,
)
from mobiliti_saas.quote_engine.mobiliti_pricing import (  # noqa: E402
    PricingRowBinding,
    build_mobiliti_pricing_writes,
    lumbro_frozen_cost,
    write_official_currency_selector,
)
from mobiliti_saas.quote_engine.official_composer import (  # noqa: E402
    ComposeRequest,
    CotizacionMetadata,
    CotizacionProduct,
    CotizacionProductImage,
    CotizacionSection,
    CotizacionSheetEditor,
    _translate_estrategia,
    compose_official_quote,
)
from mobiliti_saas.quote_engine.official_template import (  # noqa: E402
    load_template_contract,
)
from mobiliti_saas.quote_engine.quotation_sheets import (  # noqa: E402
    QuotationDataRow,
    _with_canonical_hash,
    build_quotation_data_sheet,
)


OFFICIAL_TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
CONTRACT_PATH = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "formato-cotizacion-2026-oficial.contract.json"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"


def _write_engine_source(
    path: Path,
    products: tuple[dict[str, object], ...],
    *,
    category: str = "Sillas",
    image_path: Path | None = None,
) -> None:
    workbook = Workbook()
    quotation = workbook.active
    quotation.title = "Quotation"
    headers = {
        1: "No.",
        2: "Item",
        4: "Description",
        5: "Dimension",
        7: "Qty",
        10: "List Price",
        12: "Supplier",
        13: "Discount Percent",
        14: "Original Currency",
        15: "Original Unit Price",
        16: "Frozen Exchange Rate",
        17: "Source Reference",
        18: "Price Mode",
        19: "Auto Electrification",
    }
    for column, value in headers.items():
        quotation.cell(7, column).value = value
    quotation["A8"] = f"- {category}"
    for offset, product in enumerate(products, start=9):
        quotation.cell(offset, 1).value = offset - 8
        quotation.cell(offset, 2).value = product["name"]
        quotation.cell(offset, 4).value = product.get("description", "")
        quotation.cell(offset, 5).value = product.get("dimension", "")
        quotation.cell(offset, 7).value = product.get("quantity", 1)
        quotation.cell(offset, 10).value = product.get("price", 0)
        quotation.cell(offset, 12).value = product.get("provider", "")
        quotation.cell(offset, 13).value = product.get("discount")
        quotation.cell(offset, 14).value = product.get("currency", "")
        quotation.cell(offset, 15).value = product.get("original_price")
        quotation.cell(offset, 16).value = product.get("rate")
        quotation.cell(offset, 17).value = product.get("reference", "")
        quotation.cell(offset, 18).value = product.get("mode", "")
        quotation.cell(offset, 19).value = product.get("auto")
    if image_path is not None:
        quotation.add_image(OpenpyxlImage(image_path), "B9")
    workbook.save(path)
    workbook.close()


def _canonical_row(
    *,
    item_key: str,
    section_id: str,
    section_title: str,
    origin: str,
    source_row: int | None,
    original_cost: str,
    frozen_rate: str,
    converted_cost: str,
    quantity: str,
    provider: str,
    region: str,
) -> QuotationDataRow:
    return _with_canonical_hash(
        QuotationDataRow(
            item_key=item_key,
            section_id=section_id,
            section_title=section_title,
            position=1,
            origin=origin,
            source_row=source_row,
            original_currency="MXN",
            original_cost=Decimal(original_cost),
            frozen_rate=Decimal(frozen_rate),
            converted_cost=Decimal(converted_cost),
            quantity=Decimal(quantity),
            provider=provider,
            region=region,
            source_hash="a" * 64,
            upstream_row_hash="b" * 64 if origin == "imported" else "",
            row_hash="",
        )
    )


def _mixed_metadata(*, imported_only: bool) -> dict[str, object]:
    return {
        "catalog_price_mode": "mixed_catalog_converted",
        "quote_currency": "MXN",
        "descuento": 30,
        "auto_electrification_rate": None,
        "rate_summary": []
        if imported_only
        else [
            {
                "catalog": "tarkett",
                "base_currency": "MXN",
                "quote_currency": "MXN",
                "exchange_rate": "1.000000",
                "rate_source": "identity",
                "rate_effective_date": "2026-07-22",
                "rate_retrieved_at": "",
            }
        ],
    }


def _cell_formula(package: XlsxPackage, sheet_name: str, coordinate: str) -> str | None:
    root = ET.fromstring(package.parts[package.sheet_part(sheet_name)])
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    if cell is None:
        return None
    formula = cell.find(f"{{{MAIN}}}f")
    return None if formula is None else f"={formula.text or ''}"


def _cell(root: ET.Element, coordinate: str) -> ET.Element:
    result = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    assert result is not None
    return result


def _terms_signature(root: ET.Element, *, start: int, end: int) -> tuple:
    signature = []
    for row_number in range(start, end + 1):
        row = root.find(f"{{{MAIN}}}sheetData/{{{MAIN}}}row[@r='{row_number}']")
        assert row is not None
        row_attributes = tuple(
            sorted((key, value) for key, value in row.attrib.items() if key != "r")
        )
        cells = []
        for cell in row.findall(f"{{{MAIN}}}c"):
            coordinate = cell.attrib["r"]
            column = "".join(character for character in coordinate if character.isalpha())
            if column > "J" or len(column) > 1:
                continue
            formula = cell.find(f"{{{MAIN}}}f")
            value = cell.find(f"{{{MAIN}}}v")
            text = cell.find(f"{{{MAIN}}}is/{{{MAIN}}}t")
            cells.append(
                (
                    column,
                    tuple(sorted((key, val) for key, val in cell.attrib.items() if key != "r")),
                    None if formula is None else (formula.text, tuple(sorted(formula.attrib.items()))),
                    None if value is None else value.text,
                    None if text is None else text.text,
                )
            )
        signature.append((row_attributes, tuple(cells)))
    return tuple(signature)


def _cotizacion_drawing_parts(package: XlsxPackage) -> tuple[str, str]:
    sheet_part = package.sheet_part("Cotizacion")
    sheet = ET.fromstring(package.parts[sheet_part])
    drawing = sheet.find(f"{{{MAIN}}}drawing")
    assert drawing is not None
    relationship_id = drawing.attrib[f"{{{OFFICE_REL}}}id"]
    sheet_relationships = ET.fromstring(
        package.parts[relationship_part_name(sheet_part)]
    )
    relationship = next(
        item
        for item in sheet_relationships.findall(f"{{{PACKAGE_REL}}}Relationship")
        if item.attrib["Id"] == relationship_id
    )
    drawing_part = resolve_internal_target(sheet_part, relationship.attrib["Target"])
    return drawing_part, relationship_part_name(drawing_part)


def _minimal_request(output: Path) -> ComposeRequest:
    return _request_for_sections(output, (1,))


def _request_for_sections(output: Path, section_sizes: tuple[int, ...]) -> ComposeRequest:
    contract = load_template_contract(CONTRACT_PATH)
    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    needs = [
        SectionNeed(f"section-{index}", f"Sección {index}", size)
        for index, size in enumerate(section_sizes, start=1)
    ]
    planned = plan_mobiliti_layout(needs)
    writes: list[MobilitiCellWrite] = []
    cotizacion_sections: list[CotizacionSection] = []
    global_position = 0
    for need, layout in zip(needs, planned.sections, strict=False):
        products: list[CotizacionProduct] = []
        for offset in range(need.item_count):
            global_position += 1
            target_row = layout.product_start + offset
            name = f"Producto {global_position}"
            writes.extend(
                (
                    MobilitiCellWrite(f"D{target_row}", "text", name),
                    MobilitiCellWrite(f"E{target_row}", "text", "Silla"),
                    MobilitiCellWrite(f"F{target_row}", "text", "Proveedor"),
                    MobilitiCellWrite(f"H{target_row}", "number", Decimal("1")),
                    MobilitiCellWrite(
                        f"J{target_row}",
                        "number",
                        Decimal(global_position).quantize(Decimal("0.01")),
                    ),
                    MobilitiCellWrite(f"P{target_row}", "text", "Centro"),
                )
            )
            products.append(
                CotizacionProduct(
                    item_key=f"item-{global_position}",
                    name=name,
                    description=f"Descripción {global_position}",
                    dimensions="60 x 60 cm",
                    quantity=Decimal("1"),
                    mobiliti_row=target_row,
                    discount=Decimal("0.30"),
                )
            )
        cotizacion_sections.append(
            CotizacionSection(title=need.title, products=tuple(products))
        )
    mobiliti = build_mobiliti_sheet(
        base.parts[base.sheet_part("Mobiliti")],
        needs,
        writes,
    )
    selector = WorksheetEditor.from_xml(mobiliti.xml)
    write_official_currency_selector(selector, "MXN", "Guadalajara")
    mobiliti = MobilitiSheetMutation(selector.to_xml(), mobiliti.row_map)

    cotizacion = CotizacionSheetEditor.from_xml(
        base.parts[base.sheet_part("Cotizacion")]
    ).compose(
        metadata=CotizacionMetadata(
            quotation_number="100-00001",
            project="Proyecto compositor",
            client="Cliente de prueba",
            email="cliente@example.test",
            phone="33 0000 0000",
            address="Guadalajara, Jalisco",
            business_name="Cliente SA de CV",
        ),
        sections=tuple(cotizacion_sections),
    )
    return ComposeRequest(
        template=OFFICIAL_TEMPLATE.resolve(),
        output=output.resolve(),
        mobiliti=mobiliti,
        cotizacion=cotizacion,
        quotation=None,
        quotation_data=build_quotation_data_sheet(()),
        contract=contract,
    )


def test_composer_preserves_protected_official_package_and_updates_dependents(
    tmp_path: Path,
) -> None:
    output = tmp_path / "quote.xlsx"
    request = _minimal_request(output)

    audit = compose_official_quote(request)

    assert audit.unexpected_changed_parts == frozenset()
    result = XlsxPackage.read(output)
    assert result.sheet_state("Fletes") == "hidden"
    assert result.sheet_state("Quotation_Data") == "veryHidden"
    assert "sheep" not in {name.casefold() for name, *_rest in result._sheet_rows()}
    assert sum(name.startswith("xl/externalLinks/") for name in result.parts) == 12
    assert sum(
        len(
            ET.fromstring(result.parts[part]).findall(f".//{{{MAIN}}}f")
        )
        for name, _state, _index, part in result._sheet_rows()
        if name.strip().casefold().startswith("spec")
    ) == 1314
    workbook = ET.fromstring(result.parts["xl/workbook.xml"])
    assert len(
        workbook.findall(f"{{{MAIN}}}definedNames/{{{MAIN}}}definedName")
    ) == 29
    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    for prefix in request.contract.protected_prefixes:
        for name, payload in base.parts.items():
            if name.startswith(prefix):
                assert result.parts[name] == payload
    assert _cell_formula(result, "Fletes", "D19") == (
        f"=Mobiliti!H{request.mobiliti.row_map.total_row}"
    )
    assert _cell_formula(result, "Estrategia Comercial ", "D59") == (
        f"=Cotizacion!H{request.cotizacion.total_row}"
    )


def test_cotizacion_clears_contamination_uses_master_discount_and_keeps_terms(
    tmp_path: Path,
) -> None:
    request = _request_for_sections(tmp_path / "two-products.xlsx", (2,))
    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    official = ET.fromstring(base.parts[base.sheet_part("Cotizacion")])
    candidate = ET.fromstring(request.cotizacion.xml)
    first_row, second_row = request.cotizacion.product_rows

    first_discount = _cell(candidate, f"G{first_row}")
    second_discount = _cell(candidate, f"G{second_row}")
    assert first_discount.find(f"{{{MAIN}}}f") is None
    assert Decimal(first_discount.findtext(f"{{{MAIN}}}v")) == Decimal("0.30")
    assert second_discount.findtext(f"{{{MAIN}}}f") == f"$G${first_row}"
    for row in (first_row, second_row):
        assert _cell(candidate, f"A{row}").attrib["t"] == "inlineStr"
        assert _cell(candidate, f"C{row}").attrib["t"] == "inlineStr"
        assert _cell(candidate, f"D{row}").attrib["t"] == "inlineStr"
        assert "#REF!" not in ET.tostring(
            candidate.find(f"{{{MAIN}}}sheetData/{{{MAIN}}}row[@r='{row}']"),
            encoding="unicode",
        )

    delta = request.cotizacion.terms_row_delta
    assert _terms_signature(official, start=28, end=76) == _terms_signature(
        candidate,
        start=28 + delta,
        end=76 + delta,
    )


def test_composer_handles_twenty_sections_and_one_hundred_product_section(
    tmp_path: Path,
) -> None:
    sizes = (100, *(1 for _ in range(19)))
    output = tmp_path / "large-dynamic.xlsx"
    request = _request_for_sections(output, sizes)

    compose_official_quote(request)

    package = XlsxPackage.read(output)
    assert len(request.mobiliti.row_map.sections) == 20
    assert request.mobiliti.row_map.sections[0].capacity == 100
    assert _cell_formula(package, "Fletes", "D19") == (
        f"=Mobiliti!H{request.mobiliti.row_map.total_row}"
    )
    estrategia = ET.fromstring(
        package.parts[package.sheet_part("Estrategia Comercial ")]
    )
    assert f"${request.mobiliti.row_map.last_product_row}" in (
        _cell(estrategia, "B7").findtext(f"{{{MAIN}}}f") or ""
    )
    assert _cell_formula(package, "Estrategia Comercial ", "D59") == (
        f"=Cotizacion!H{request.cotizacion.total_row}"
    )


def test_composer_preserves_static_drawing_and_adds_safe_product_png(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "product.png"
    Image.new("RGB", (80, 40), (25, 120, 210)).save(image_path)
    output = tmp_path / "with-image.xlsx"
    request = _minimal_request(output)
    cotizacion = replace(
        request.cotizacion,
        images=(
            CotizacionProductImage(
                image_path.resolve(),
                request.cotizacion.product_rows[0],
            ),
        ),
    )

    compose_official_quote(replace(request, cotizacion=cotizacion))

    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    result = XlsxPackage.read(output)
    base_drawing, base_rels = _cotizacion_drawing_parts(base)
    result_drawing, result_rels = _cotizacion_drawing_parts(result)
    assert result_drawing == base_drawing
    base_root = ET.fromstring(base.parts[base_drawing])
    result_root = ET.fromstring(result.parts[result_drawing])
    assert len(list(base_root)) == 8
    assert len(list(result_root)) == 6  # 5 estáticas + 1 producto nuevo.
    product_anchor = result_root.findall(f"{{{XDR}}}oneCellAnchor")[-1]
    assert product_anchor.findtext(f"{{{XDR}}}from/{{{XDR}}}row") == str(
        request.cotizacion.product_rows[0] - 1
    )

    before_relationships = {
        item.attrib["Id"]: dict(item.attrib)
        for item in ET.fromstring(base.parts[base_rels]).findall(
            f"{{{PACKAGE_REL}}}Relationship"
        )
    }
    after_relationships = {
        item.attrib["Id"]: dict(item.attrib)
        for item in ET.fromstring(result.parts[result_rels]).findall(
            f"{{{PACKAGE_REL}}}Relationship"
        )
    }
    assert all(after_relationships[key] == value for key, value in before_relationships.items())
    new_relationships = set(after_relationships) - set(before_relationships)
    assert len(new_relationships) == 1
    new_target = after_relationships[new_relationships.pop()]["Target"]
    new_media = resolve_internal_target(result_drawing, new_target)
    assert result.parts[new_media].startswith(b"\x89PNG\r\n\x1a\n")


def test_composer_uses_task7_numeric_price_once_for_import_catalog_and_lumbro(
    tmp_path: Path,
) -> None:
    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    need = SectionNeed("section-prices", "Precios", 3)
    row_map = plan_mobiliti_layout((need,))
    definitions = (
        ("catalog", None, "", Decimal("100"), Decimal("1"), Decimal("100.00")),
        ("imported", 9, "b" * 64, Decimal("10"), Decimal("18.5"), Decimal("185.00")),
        (
            "lumbro",
            None,
            "",
            Decimal("100"),
            Decimal("0.054054"),
            lumbro_frozen_cost(Decimal("100"), Decimal("0.054054")),
        ),
    )
    canonical_rows = tuple(
        _with_canonical_hash(
            QuotationDataRow(
                item_key=f"item-{position}",
                section_id=need.id,
                section_title=need.title,
                position=position,
                origin=origin,
                source_row=source_row,
                original_currency="USD" if origin != "lumbro" else "MXN",
                original_cost=original,
                frozen_rate=rate,
                converted_cost=converted,
                quantity=Decimal("1"),
                provider="Proveedor",
                region="Centro",
                source_hash="a" * 64,
                upstream_row_hash=upstream,
                row_hash="",
            )
        )
        for position, (origin, source_row, upstream, original, rate, converted) in enumerate(
            definitions,
            start=1,
        )
    )
    bindings = tuple(
        PricingRowBinding(
            item_key=row.item_key,
            section_id=row.section_id,
            position=row.position,
            target_row=target_row,
        )
        for row, target_row in zip(canonical_rows, row_map.item_rows, strict=True)
    )
    input_writes: list[MobilitiCellWrite] = []
    for row, target_row in zip(canonical_rows, row_map.item_rows, strict=True):
        input_writes.extend(
            (
                MobilitiCellWrite(f"D{target_row}", "text", row.item_key),
                MobilitiCellWrite(f"E{target_row}", "text", "Silla"),
                MobilitiCellWrite(f"F{target_row}", "text", row.provider),
                MobilitiCellWrite(f"H{target_row}", "number", row.quantity),
                MobilitiCellWrite(f"P{target_row}", "text", row.region),
            )
        )
    input_writes.extend(
        build_mobiliti_pricing_writes(
            canonical_rows,
            row_map,
            bindings=bindings,
        )
    )
    mobiliti = build_mobiliti_sheet(
        base.parts[base.sheet_part("Mobiliti")],
        [need],
        input_writes,
    )
    selector = WorksheetEditor.from_xml(mobiliti.xml)
    write_official_currency_selector(selector, "USD", "Monterrey")
    mobiliti = MobilitiSheetMutation(selector.to_xml(), mobiliti.row_map)
    cotizacion = CotizacionSheetEditor.from_xml(
        base.parts[base.sheet_part("Cotizacion")]
    ).compose(
        metadata=CotizacionMetadata(quotation_number="PRICE-001"),
        sections=(
            CotizacionSection(
                title=need.title,
                products=tuple(
                    CotizacionProduct(
                        item_key=row.item_key,
                        name=row.item_key,
                        description=row.origin,
                        dimensions="",
                        quantity=row.quantity,
                        mobiliti_row=target_row,
                    )
                    for row, target_row in zip(
                        canonical_rows,
                        row_map.item_rows,
                        strict=True,
                    )
                ),
            ),
        ),
    )
    output = tmp_path / "all-price-origins.xlsx"
    compose_official_quote(
        ComposeRequest(
            template=OFFICIAL_TEMPLATE.resolve(),
            output=output.resolve(),
            mobiliti=mobiliti,
            cotizacion=cotizacion,
            quotation=None,
            quotation_data=build_quotation_data_sheet(canonical_rows),
            contract=load_template_contract(CONTRACT_PATH),
        )
    )

    result = XlsxPackage.read(output)
    official_mobiliti = ET.fromstring(base.parts[base.sheet_part("Mobiliti")])
    result_mobiliti = ET.fromstring(result.parts[result.sheet_part("Mobiliti")])
    for row, target_row in zip(canonical_rows, row_map.item_rows, strict=True):
        price = _cell(result_mobiliti, f"J{target_row}")
        assert price.find(f"{{{MAIN}}}f") is None
        assert Decimal(price.findtext(f"{{{MAIN}}}v")) == row.converted_cost
        assert _cell(result_mobiliti, f"W{target_row}").find(f"{{{MAIN}}}f") is not None
        assert _cell(result_mobiliti, f"X{target_row}").find(f"{{{MAIN}}}f") is not None
    assert ET.tostring(_cell(result_mobiliti, "K6")) == ET.tostring(
        _cell(official_mobiliti, "K6")
    )


def test_composer_rejects_a_mobiliti_write_outside_the_declared_surface(
    tmp_path: Path,
) -> None:
    output = tmp_path / "tampered.xlsx"
    request = _minimal_request(output)
    root = ET.fromstring(request.mobiliti.xml)
    cell = root.find(f".//{{{MAIN}}}c[@r='A1']")
    if cell is None:
        sheet_data = root.find(f"{{{MAIN}}}sheetData")
        assert sheet_data is not None
        row = sheet_data.find(f"{{{MAIN}}}row[@r='1']")
        if row is None:
            row = ET.SubElement(sheet_data, f"{{{MAIN}}}row", {"r": "1"})
        cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": "A1"})
    for child in list(cell):
        cell.remove(child)
    cell.attrib["t"] = "inlineStr"
    inline = ET.SubElement(cell, f"{{{MAIN}}}is")
    ET.SubElement(inline, f"{{{MAIN}}}t").text = "alteración fuera de contrato"
    tampered = MobilitiSheetMutation(
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        request.mobiliti.row_map,
    )

    with pytest.raises(ValueError, match="fuera de la superficie mutable"):
        compose_official_quote(replace(request, mobiliti=tampered))

    assert not output.exists()


def test_composer_rejects_nonofficial_template_before_creating_output(
    tmp_path: Path,
) -> None:
    template = tmp_path / "tampered-template.xlsx"
    shutil.copyfile(OFFICIAL_TEMPLATE, template)
    with template.open("ab") as stream:
        stream.write(b"tamper")
    output = tmp_path / "must-not-exist.xlsx"
    request = replace(_minimal_request(output), template=template.resolve())

    with pytest.raises(ValueError, match="Plantilla oficial incompatible: sha256"):
        compose_official_quote(request)

    assert not output.exists()


def test_composer_rejects_existing_or_relative_output_without_overwrite(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "reserved.xlsx"
    existing.write_bytes(b"contenido del usuario")

    with pytest.raises(FileExistsError, match="salida ya existe"):
        compose_official_quote(_minimal_request(existing))
    assert existing.read_bytes() == b"contenido del usuario"

    relative_request = replace(
        _minimal_request(tmp_path / "absolute.xlsx"),
        output=Path("relative.xlsx"),
    )
    with pytest.raises(ValueError, match="ruta de salida debe ser absoluta"):
        compose_official_quote(relative_request)
    assert not (tmp_path / "absolute.xlsx").exists()


def test_composer_rejects_unsafe_product_image_before_creating_output(
    tmp_path: Path,
) -> None:
    image = tmp_path / "product.svg"
    image.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    output = tmp_path / "unsafe-image.xlsx"
    request = _minimal_request(output)
    cotizacion = replace(
        request.cotizacion,
        images=(CotizacionProductImage(image.resolve(), 17),),
    )

    with pytest.raises(ValueError, match="Formato de imagen.*no permitido"):
        compose_official_quote(replace(request, cotizacion=cotizacion))

    assert not output.exists()


def test_cotizacion_product_requires_nonempty_identity_fields() -> None:
    with pytest.raises(ValueError, match="Texto de Cotizacion vacío"):
        CotizacionProduct(
            item_key="",
            name="Silla",
            description="",
            dimensions="",
            quantity=Decimal("1"),
            mobiliti_row=14,
        )


def test_active_engine_routes_through_official_composer_without_legacy_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.xlsx"
    workbook = Workbook()
    quotation = workbook.active
    quotation.title = "Quotation"
    for column, value in {
        1: "No.",
        2: "Item",
        4: "Description",
        5: "Dimension",
        7: "Qty",
        10: "List Price",
    }.items():
        quotation.cell(7, column).value = value
    quotation["A8"] = "- Sillas"
    quotation["A9"] = 1
    quotation["B9"] = "Silla de prueba"
    quotation["D9"] = "Silla operativa"
    quotation["E9"] = "60 x 60 cm"
    quotation["G9"] = 2
    quotation["J9"] = 125.50
    workbook.save(source)
    workbook.close()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("el writer legacy no debe tener un caller activo")

    for name in (
        "_ensure_mobiliti_formula_layout",
        "_write_mobiliti_row_formulas",
        "_normalize_mobiliti_row_formulas",
        "_set_mobiliti_subtotal_formulas",
    ):
        monkeypatch.setattr(f"mobiliti_saas.quote_engine.engine.{name}", forbidden)

    output = tmp_path / "engine.xlsx"
    result = generate_quote(
        source,
        output,
        {"cotizacion": "100-ENGINE", "proyecto": "Ruta oficial"},
        OFFICIAL_TEMPLATE,
    )

    assert result == output
    package = XlsxPackage.read(output)
    assert package.sheet_state("Quotation_Data") == "veryHidden"
    assert package.sheet_part("Quotation")


def test_active_engine_renders_each_lumbro_accessory_once_and_includes_its_cost(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workstation.xlsx"
    _write_engine_source(
        source,
        (
            {
                "name": "Workstation 2 pax",
                "description": "Estación para dos usuarios",
                "dimension": "240 x 120 cm",
                "quantity": 1,
                "price": 1000,
            },
        ),
        category="Workstations",
    )
    output = tmp_path / "workstation-official.xlsx"

    generate_quote(source, output, {"cotizacion": "LUMBRO-E2E"}, OFFICIAL_TEMPLATE)

    workbook = load_workbook(output, data_only=False, keep_links=False)
    try:
        cotizacion = workbook["Cotizacion"]
        expected_names = (
            "Workstation 2 pax",
            "LIDO.OP-INT",
            "JUMP-1.5M",
            "CAJA-FUS",
        )
        rows_by_name = {
            name: [
                row
                for row in range(16, cotizacion.max_row + 1)
                if cotizacion.cell(row, 1).value == name
            ]
            for name in expected_names
        }
        assert all(len(rows) == 1 for rows in rows_by_name.values())

        visible_rows = tuple(rows_by_name[name][0] for name in expected_names)
        mobiliti_rows = []
        for row in visible_rows:
            formula = cotizacion.cell(row, 6).value
            assert isinstance(formula, str) and formula.startswith("=Mobiliti!X")
            mobiliti_rows.append(int(formula.removeprefix("=Mobiliti!X")))
            assert cotizacion.cell(row, 10).value == f"=E{row}*I{row}"
        assert len(set(mobiliti_rows)) == len(expected_names)

        total_row = next(
            row
            for row in range(max(visible_rows) + 1, cotizacion.max_row + 1)
            if cotizacion.cell(row, 4).value == "TOTAL:"
        )
        subtotal_value = cotizacion.cell(total_row - 5, 8).value
        subtotal_formula = getattr(subtotal_value, "text", subtotal_value)
        assert subtotal_formula == (
            f"=SUM(IFERROR(J{min(visible_rows)}:J{max(visible_rows)},0))"
        )

        audit = workbook["Quotation_Data"]
        canonical = [
            tuple(audit.cell(row, column).value for column in range(1, 14))
            for row in range(2, audit.max_row + 1)
        ]
        assert len(canonical) == len(expected_names)
        parent_key = str(canonical[0][0])
        for code, row, mobiliti_row in zip(
            expected_names[1:],
            canonical[1:],
            mobiliti_rows[1:],
            strict=True,
        ):
            assert parent_key in str(row[0])
            assert code in str(row[0])
            assert row[1] == canonical[0][1]
            assert workbook["Mobiliti"].cell(mobiliti_row, 10).value == pytest.approx(
                float(row[9])
            )
    finally:
        workbook.close()


def test_active_engine_uses_authoritative_catalog_canonical_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "catalog-source.xlsx"
    _write_engine_source(
        source,
        (
            {
                "name": "Silla catalogada",
                "description": "Silla de catálogo",
                "dimension": "60 x 60 cm",
                "quantity": 2,
                "price": 125.50,
                "provider": "Tarkett",
                "discount": 0,
                "currency": "MXN",
                "original_price": 125.50,
                "rate": 1,
                "reference": "catalog:tarkett:silla-1",
                "mode": "net",
                "auto": False,
            },
        ),
    )
    canonical = _canonical_row(
        item_key="catalog:tarkett:silla-1",
        section_id="catalog-section:chairs",
        section_title="Sillas canónicas",
        origin="catalog",
        source_row=None,
        original_cost="125.50",
        frozen_rate="1",
        converted_cost="125.50",
        quantity="2",
        provider="Tarkett",
        region="tarkett",
    )
    output = tmp_path / "catalog-official.xlsx"

    def legacy_validator_forbidden(*_args, **_kwargs):
        raise AssertionError("el handoff canónico no depende del validador legacy")

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.engine._validate_mixed_catalog_metadata",
        legacy_validator_forbidden,
    )

    generate_quote(
        source,
        output,
        _mixed_metadata(imported_only=False),
        OFFICIAL_TEMPLATE,
        quotation_data_rows=(canonical,),
    )

    workbook = load_workbook(output, data_only=False, keep_links=False)
    try:
        audit = workbook["Quotation_Data"]
        assert audit["A2"].value == canonical.item_key
        assert audit["B2"].value == canonical.section_id
        assert audit["C2"].value == canonical.section_title
        assert audit["P2"].value == canonical.row_hash
        cotizacion = workbook["Cotizacion"]
        assert any(
            cotizacion.cell(row, 1).value == canonical.section_title
            for row in range(16, cotizacion.max_row + 1)
        )
    finally:
        workbook.close()


def test_active_engine_uses_authoritative_imported_canonical_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "imported-source.xlsx"
    _write_engine_source(
        source,
        (
            {
                "name": "Producto importado",
                "description": "Línea importada",
                "dimension": "pieza",
                "quantity": 3,
                "price": 200.25,
                "provider": "Proveedor externo",
                "discount": 0,
                "currency": "MXN",
                "original_price": 200.25,
                "rate": 1,
                "reference": "import:quote-77:9",
                "mode": "imported",
                "auto": False,
            },
        ),
        category="Importados",
    )
    canonical = _canonical_row(
        item_key="import:quote-77:9",
        section_id="import-section:quote-77",
        section_title="Importación auditada",
        origin="imported",
        source_row=9,
        original_cost="200.25",
        frozen_rate="1",
        converted_cost="200.25",
        quantity="3",
        provider="Proveedor externo",
        region="imported",
    )
    output = tmp_path / "imported-official.xlsx"

    def legacy_validator_forbidden(*_args, **_kwargs):
        raise AssertionError("el handoff imported no depende del validador legacy")

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.engine._validate_mixed_catalog_metadata",
        legacy_validator_forbidden,
    )

    generate_quote(
        source,
        output,
        _mixed_metadata(imported_only=True),
        OFFICIAL_TEMPLATE,
        quotation_data_rows=(canonical,),
    )

    workbook = load_workbook(output, data_only=False, keep_links=False)
    try:
        audit = workbook["Quotation_Data"]
        assert tuple(audit.cell(2, column).value for column in (1, 2, 3, 6, 16)) == (
            canonical.item_key,
            canonical.section_id,
            canonical.section_title,
            canonical.source_row,
            canonical.row_hash,
        )
    finally:
        workbook.close()


def test_active_engine_rejects_nonempty_canonical_mismatch_without_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mismatch-source.xlsx"
    _write_engine_source(
        source,
        ({"name": "Silla", "quantity": 1, "price": 100},),
    )
    canonical = _canonical_row(
        item_key="catalog:item-1",
        section_id="catalog-section:1",
        section_title="Sillas",
        origin="catalog",
        source_row=None,
        original_cost="100",
        frozen_rate="1",
        converted_cost="100.00",
        quantity="99",
        provider="Sunon Inc",
        region="sunon",
    )
    output = tmp_path / "must-not-exist.xlsx"

    with pytest.raises(ValueError, match="canónic.*no coincide|canónica.*inconsistente"):
        generate_quote(
            source,
            output,
            {},
            OFFICIAL_TEMPLATE,
            quotation_data_rows=(canonical,),
        )

    assert not output.exists()


def test_active_engine_explicit_empty_canonical_rows_use_compatible_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty-canonical-source.xlsx"
    _write_engine_source(
        source,
        ({"name": "Silla fallback", "quantity": 1, "price": 100},),
    )
    output = tmp_path / "empty-canonical-output.xlsx"

    generate_quote(
        source,
        output,
        {},
        OFFICIAL_TEMPLATE,
        quotation_data_rows=(),
    )

    workbook = load_workbook(output, data_only=False, keep_links=False)
    try:
        assert workbook["Quotation_Data"]["A2"].value == "quotation:9"
    finally:
        workbook.close()


def test_active_engine_omitted_original_keeps_legacy_visible_quotation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "omitted-original.xlsx"
    _write_engine_source(
        source,
        ({"name": "Silla visible", "quantity": 1, "price": 100},),
    )
    output = tmp_path / "omitted-original-output.xlsx"

    generate_quote(source, output, {}, OFFICIAL_TEMPLATE)

    package = XlsxPackage.read(output)
    assert package.sheet_part("Quotation")


def test_active_engine_explicit_none_omits_visible_quotation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "none-original.xlsx"
    _write_engine_source(
        source,
        ({"name": "Silla sin original", "quantity": 1, "price": 100},),
    )
    output = tmp_path / "none-original-output.xlsx"

    generate_quote(
        source,
        output,
        {},
        OFFICIAL_TEMPLATE,
        original_quotation_path=None,
    )

    package = XlsxPackage.read(output)
    with pytest.raises(KeyError):
        package.sheet_part("Quotation")


def test_active_engine_explicit_original_transplants_that_workbook(
    tmp_path: Path,
) -> None:
    source = tmp_path / "generated-source.xlsx"
    original = tmp_path / "customer-original.xlsx"
    _write_engine_source(
        source,
        ({"name": "Producto generado", "quantity": 1, "price": 100},),
    )
    _write_engine_source(
        original,
        ({"name": "Producto original visible", "quantity": 7, "price": 999},),
        category="Original cliente",
    )
    output = tmp_path / "explicit-original-output.xlsx"

    generate_quote(
        source,
        output,
        {},
        OFFICIAL_TEMPLATE,
        original_quotation_path=original,
    )

    workbook = load_workbook(output, data_only=False, keep_links=False)
    try:
        assert workbook["Quotation"]["B9"].value == "Producto original visible"
        assert any(
            workbook["Cotizacion"].cell(row, 1).value == "Producto generado"
            for row in range(16, workbook["Cotizacion"].max_row + 1)
        )
    finally:
        workbook.close()


def test_active_engine_embedded_source_image_reaches_cotizacion_anchor(
    tmp_path: Path,
) -> None:
    image = tmp_path / "source-product.png"
    Image.new("RGBA", (32, 24), (20, 80, 160, 255)).save(image)
    source = tmp_path / "source-with-image.xlsx"
    _write_engine_source(
        source,
        ({"name": "Silla con imagen", "quantity": 1, "price": 100},),
        image_path=image,
    )
    output = tmp_path / "output-with-product-image.xlsx"

    generate_quote(
        source,
        output,
        {},
        OFFICIAL_TEMPLATE,
        original_quotation_path=None,
    )

    package = XlsxPackage.read(output)
    cotizacion = ET.fromstring(package.parts[package.sheet_part("Cotizacion")])
    product_row = next(
        int(row.attrib["r"])
        for row in cotizacion.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row")
        if (
            (cell := row.find(f"{{{MAIN}}}c[@r='A{row.attrib['r']}']"))
            is not None
            and cell.findtext(f"{{{MAIN}}}is/{{{MAIN}}}t")
            == "Silla con imagen"
        )
    )
    drawing_part, _drawing_rels_part = _cotizacion_drawing_parts(package)
    drawing = ET.fromstring(package.parts[drawing_part])
    product_anchors = [
        anchor
        for anchor in drawing.findall(f"{{{XDR}}}oneCellAnchor")
        if anchor.findtext(f"{{{XDR}}}from/{{{XDR}}}row") == str(product_row - 1)
        and anchor.findtext(f"{{{XDR}}}from/{{{XDR}}}col") == "1"
    ]
    assert len(product_anchors) == 1
    assert any(
        name.startswith("xl/media/quote_product_")
        and package.parts[name].startswith(b"\x89PNG\r\n\x1a\n")
        for name in package.parts
    )


@pytest.mark.parametrize(
    "surface",
    ("mobiliti_formula", "cotizacion_terms", "cotizacion_style", "row_height", "merge"),
)
def test_composer_rejects_any_change_outside_the_exact_sheet_allowlist(
    tmp_path: Path,
    surface: str,
) -> None:
    output = tmp_path / f"exact-{surface}.xlsx"
    request = _minimal_request(output)
    if surface == "mobiliti_formula":
        root = ET.fromstring(request.mobiliti.xml)
        coordinate = f"W{request.mobiliti.row_map.item_rows[0]}"
        formula = _cell(root, coordinate).find(f"{{{MAIN}}}f")
        assert formula is not None
        formula.text = "1+1"
        request = replace(
            request,
            mobiliti=replace(
                request.mobiliti,
                xml=ET.tostring(root, encoding="utf-8", xml_declaration=True),
            ),
        )
    else:
        root = ET.fromstring(request.cotizacion.xml)
        product_row = request.cotizacion.product_rows[0]
        if surface == "cotizacion_terms":
            terms_row = 28 + request.cotizacion.terms_row_delta
            term = _cell(root, f"A{terms_row}")
            value = term.find(f"{{{MAIN}}}v")
            assert value is not None
            value.text = "999999"
        elif surface == "cotizacion_style":
            _cell(root, f"A{product_row}").attrib["s"] = "0"
        elif surface == "row_height":
            row = root.find(f"{{{MAIN}}}sheetData/{{{MAIN}}}row[@r='{product_row}']")
            assert row is not None
            row.attrib["ht"] = "999"
        else:
            merge = root.find(f"{{{MAIN}}}mergeCells/{{{MAIN}}}mergeCell[@ref='A16:J16']")
            assert merge is not None
            merge.attrib["ref"] = "A16:I16"
        request = replace(
            request,
            cotizacion=replace(
                request.cotizacion,
                xml=ET.tostring(root, encoding="utf-8", xml_declaration=True),
            ),
        )

    with pytest.raises(ValueError, match="contrato exacto"):
        compose_official_quote(request)

    assert not output.exists()


@pytest.mark.parametrize("targets", ((999,), (17, 17)))
def test_composer_rejects_product_images_outside_unique_product_rows(
    tmp_path: Path,
    targets: tuple[int, ...],
) -> None:
    image = tmp_path / "safe.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(image)
    output = tmp_path / f"bad-image-{'-'.join(map(str, targets))}.xlsx"
    request = _minimal_request(output)
    cotizacion = replace(
        request.cotizacion,
        images=tuple(CotizacionProductImage(image.resolve(), row) for row in targets),
    )

    with pytest.raises(ValueError, match="filas de producto"):
        compose_official_quote(replace(request, cotizacion=cotizacion))

    assert not output.exists()


def test_product_picture_ids_are_unique_and_names_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "customer-name.png"
    second = tmp_path / "another-customer-name.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(first)
    Image.new("RGB", (16, 16), (40, 50, 60)).save(second)
    output = tmp_path / "deterministic-pictures.xlsx"
    request = _request_for_sections(output, (2,))
    product_rows = request.cotizacion.product_rows
    cotizacion = replace(
        request.cotizacion,
        images=(
            CotizacionProductImage(first.resolve(), product_rows[0]),
            CotizacionProductImage(second.resolve(), product_rows[1]),
        ),
    )

    compose_official_quote(replace(request, cotizacion=cotizacion))

    package = XlsxPackage.read(output)
    drawing_part, _drawing_rels = _cotizacion_drawing_parts(package)
    drawing = ET.fromstring(package.parts[drawing_part])
    ids = [node.attrib["id"] for node in drawing.findall(f".//{{{XDR}}}cNvPr")]
    assert len(ids) == len(set(ids))
    product_names = []
    for anchor in drawing.findall(f"{{{XDR}}}oneCellAnchor"):
        row = anchor.findtext(f"{{{XDR}}}from/{{{XDR}}}row")
        if row in {str(number - 1) for number in product_rows}:
            node = anchor.find(f".//{{{XDR}}}cNvPr")
            assert node is not None
            product_names.append(node.attrib["name"])
    assert product_names == ["Imagen de producto 0001", "Imagen de producto 0002"]


def test_composer_rejects_inline_string_value_smuggling(tmp_path: Path) -> None:
    output = tmp_path / "inline-smuggling.xlsx"
    request = _minimal_request(output)
    root = ET.fromstring(request.cotizacion.xml)
    header = _cell(root, "B3")
    ET.SubElement(header, f"{{{MAIN}}}v").text = "contenido oculto"
    cotizacion = replace(
        request.cotizacion,
        xml=ET.tostring(root, encoding="utf-8", xml_declaration=True),
    )

    with pytest.raises(ValueError, match="inlineStr exacto"):
        compose_official_quote(replace(request, cotizacion=cotizacion))

    assert not output.exists()


def test_audit_failure_never_leaves_output_or_compose_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "audit-failure.xlsx"

    def fail_contract(*_args, **_kwargs) -> None:
        raise ValueError("fallo de auditoría inyectado")

    monkeypatch.setattr(official_composer_module, "verify_output_contract", fail_contract)

    with pytest.raises(ValueError, match="fallo de auditoría"):
        compose_official_quote(_minimal_request(output))

    assert not output.exists()
    assert not tuple(tmp_path.glob(".*.compose-*.tmp"))


def test_publication_race_preserves_existing_output_and_recovers_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "race.xlsx"
    recovered = tmp_path / "recovered"
    recovered.mkdir()
    real_rename = official_composer_module.os.rename

    def race_publish(source: Path, destination: Path) -> None:
        Path(destination).write_bytes(b"archivo del usuario")
        raise FileExistsError("carrera EEXIST simulada")

    def recover_candidate(candidate: Path) -> None:
        real_rename(candidate, recovered / candidate.name)

    monkeypatch.setattr(
        official_composer_module,
        "_atomic_publish_no_replace",
        race_publish,
        raising=False,
    )
    monkeypatch.setattr(
        official_composer_module,
        "_recycle_candidate",
        recover_candidate,
        raising=False,
    )

    with pytest.raises(FileExistsError):
        compose_official_quote(_minimal_request(output))

    assert output.read_bytes() == b"archivo del usuario"
    assert not tuple(tmp_path.glob(".*.compose-*.tmp"))
    assert len(tuple(recovered.iterdir())) == 1


def test_compose_paths_reject_lexical_parent_segments_before_resolution(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "existing"
    nested.mkdir()
    output = nested / ".." / "lexical.xlsx"
    request = replace(_minimal_request(tmp_path / "safe.xlsx"), output=output)

    with pytest.raises(ValueError, match="segmentos léxicos"):
        compose_official_quote(request)

    assert not (tmp_path / "lexical.xlsx").exists()


def test_compose_paths_reject_windows_reparse_points(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "reparse.xlsx"
    monkeypatch.setattr(
        official_composer_module,
        "_path_is_reparse_point",
        lambda path: Path(path) == tmp_path,
        raising=False,
    )

    with pytest.raises(ValueError, match="reparse point"):
        compose_official_quote(_minimal_request(output))

    assert not output.exists()


def test_estrategia_translation_never_rewrites_reference_like_string_literals() -> None:
    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    part = base.sheet_part("Estrategia Comercial ")
    root = ET.fromstring(base.parts[part])
    formula = _cell(root, "B7").find(f"{{{MAIN}}}f")
    assert formula is not None
    formula.text = (
        'IF("Mobiliti!$G$14:$G$571"="literal",0,'
        'SUMIF(Mobiliti!$G$14:$G$571,"P00500",Mobiliti!$AD$14:$AD$571))'
    )
    row_map = plan_mobiliti_layout([SectionNeed("large", "Large", 100)])

    translated = ET.fromstring(
        _translate_estrategia(
            ET.tostring(root, encoding="utf-8", xml_declaration=True),
            row_map,
            30,
        )
    )
    result = _cell(translated, "B7").findtext(f"{{{MAIN}}}f")
    assert '"Mobiliti!$G$14:$G$571"' in result
    assert f"Mobiliti!$G$14:$G${row_map.last_product_row}" in result
    assert f"Mobiliti!$AD$14:$AD${row_map.last_product_row}" in result


def test_estrategia_subtotal_translation_only_changes_range_tokens() -> None:
    base = XlsxPackage.read(OFFICIAL_TEMPLATE)
    part = base.sheet_part("Estrategia Comercial ")
    root = ET.fromstring(base.parts[part])
    formula = _cell(root, "B63").find(f"{{{MAIN}}}f")
    assert formula is not None
    formula.text = (
        'IF("Cotizacion!H19"="literal",0,'
        'IF(B61=0,C61,B61*Cotizacion!H19))'
    )
    row_map = plan_mobiliti_layout([SectionNeed("large", "Large", 100)])

    translated = ET.fromstring(
        _translate_estrategia(
            ET.tostring(root, encoding="utf-8", xml_declaration=True),
            row_map,
            30,
        )
    )
    result = _cell(translated, "B63").findtext(f"{{{MAIN}}}f")
    assert '"Cotizacion!H19"' in result
    assert "B61*Cotizacion!H25" in result


def test_composed_zip_bytes_ignore_output_name_image_name_and_image_mtime(
    tmp_path: Path,
) -> None:
    first_image = tmp_path / "first-customer-name.png"
    second_image = tmp_path / "second-customer-name.png"
    Image.new("RGB", (24, 16), (90, 40, 10)).save(first_image)
    second_image.write_bytes(first_image.read_bytes())
    os.utime(first_image, (1_000_000_000, 1_000_000_000))
    os.utime(second_image, (1_700_000_000, 1_700_000_000))
    first_output = tmp_path / "first-output.xlsx"
    second_output = tmp_path / "different-output-name.xlsx"

    first_request = _minimal_request(first_output)
    second_request = _minimal_request(second_output)
    first_cotizacion = replace(
        first_request.cotizacion,
        images=(
            CotizacionProductImage(
                first_image.resolve(),
                first_request.cotizacion.product_rows[0],
            ),
        ),
    )
    second_cotizacion = replace(
        second_request.cotizacion,
        images=(
            CotizacionProductImage(
                second_image.resolve(),
                second_request.cotizacion.product_rows[0],
            ),
        ),
    )

    compose_official_quote(replace(first_request, cotizacion=first_cotizacion))
    compose_official_quote(replace(second_request, cotizacion=second_cotizacion))

    assert first_output.read_bytes() == second_output.read_bytes()
