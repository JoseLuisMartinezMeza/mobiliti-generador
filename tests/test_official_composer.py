from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import shutil
import sys
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.mobiliti_layout import (  # noqa: E402
    SectionNeed,
    plan_mobiliti_layout,
)
from mobiliti_saas.quote_engine import generate_quote  # noqa: E402
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
