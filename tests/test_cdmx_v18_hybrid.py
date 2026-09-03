from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import column_index_from_string, range_boundaries

from mobiliti_saas.quote_engine.engine import _official_delivery_place
from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed
from mobiliti_saas.quote_engine.mobiliti_pricing import (
    write_official_currency_selector,
)
from mobiliti_saas.quote_engine.official_composer import (
    CDMX_COTIZACION_LAYOUT,
    ComposeRequest,
    CotizacionMetadata,
    CotizacionProduct,
    CotizacionSection,
    CotizacionSheetEditor,
    compose_official_quote,
)
from mobiliti_saas.quote_engine.official_template import load_template_contract
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage
from mobiliti_saas.quote_engine.ooxml_worksheet import (
    MobilitiCellWrite,
    MobilitiSheetMutation,
    WorksheetEditor,
    build_mobiliti_sheet,
)
from mobiliti_saas.quote_engine.quotation_sheets import (
    build_quotation_data_sheet,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "mobiliti_saas" / "worker" / "templates"
CDMX_TEMPLATE = TEMPLATES / "Formato Cotizacion Sunon CDMX V1C.xlsx"
CDMX_CONTRACT = (
    TEMPLATES / "formato-cotizacion-sunon-cdmx-v1c.contract.json"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _formula(package: XlsxPackage, sheet: str, coordinate: str) -> str | None:
    root = ET.fromstring(package.parts[package.sheet_part(sheet)])
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    if cell is None:
        return None
    formula = cell.find(f"{{{MAIN}}}f")
    return None if formula is None else f"={formula.text or ''}"


def _text(package: XlsxPackage, sheet: str, coordinate: str) -> str | None:
    root = ET.fromstring(package.parts[package.sheet_part(sheet)])
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    if cell is None or cell.attrib.get("t") != "inlineStr":
        return None
    return "".join(node.text or "" for node in cell.iter(f"{{{MAIN}}}t"))


def _scalar(package: XlsxPackage, sheet: str, coordinate: str) -> str | None:
    root = ET.fromstring(package.parts[package.sheet_part(sheet)])
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    if cell is None:
        return None
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN}}}t"))
    value = cell.findtext(f"{{{MAIN}}}v")
    if cell.attrib.get("t") != "s" or value is None:
        return value
    shared = ET.fromstring(package.parts["xl/sharedStrings.xml"])
    strings = shared.findall(f"{{{MAIN}}}si")
    return "".join(node.text or "" for node in strings[int(value)].iter(f"{{{MAIN}}}t"))


def _has_cell_payload(
    package: XlsxPackage,
    sheet: str,
    coordinate: str,
) -> bool:
    root = ET.fromstring(package.parts[package.sheet_part(sheet)])
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    if cell is None:
        return False
    return cell.attrib.get("t") is not None or any(
        child.tag in {f"{{{MAIN}}}f", f"{{{MAIN}}}v", f"{{{MAIN}}}is"}
        for child in cell
    )


def _fresh_cdmx_request(output: Path, section_count: int = 17) -> ComposeRequest:
    base = XlsxPackage.read(CDMX_TEMPLATE)
    needs = tuple(
        SectionNeed(f"section-{index}", f"Sección {index}", 1)
        for index in range(1, section_count + 1)
    )
    planned = build_mobiliti_sheet(
        base.parts[base.sheet_part("Mobiliti")],
        needs,
        (),
    ).row_map
    writes: list[MobilitiCellWrite] = []
    sections: list[CotizacionSection] = []
    for index, target_row in enumerate(planned.item_rows, start=1):
        writes.extend(
            (
                MobilitiCellWrite(f"D{target_row}", "text", f"Producto {index}"),
                MobilitiCellWrite(f"E{target_row}", "text", "Silla"),
                MobilitiCellWrite(f"F{target_row}", "text", "Proveedor"),
                MobilitiCellWrite(f"H{target_row}", "number", Decimal("1")),
                MobilitiCellWrite(f"J{target_row}", "number", Decimal("100")),
                MobilitiCellWrite(f"P{target_row}", "number", Decimal("0")),
                MobilitiCellWrite(f"S{target_row}", "text", "Centro"),
            )
        )
        sections.append(
            CotizacionSection(
                title=f"Sección {index}",
                products=(
                    CotizacionProduct(
                        item_key=f"item-{index}",
                        name=f"Producto {index}",
                        description=f"Descripción {index}",
                        dimensions="60 x 60 cm",
                        quantity=Decimal("1"),
                        mobiliti_row=target_row,
                        discount=Decimal("0.40"),
                    ),
                ),
            )
        )

    mobiliti = build_mobiliti_sheet(
        base.parts[base.sheet_part("Mobiliti")],
        needs,
        writes,
    )
    selector = WorksheetEditor.from_xml(mobiliti.xml)
    write_official_currency_selector(
        selector,
        "MXN",
        "ZMG",
        Decimal("0.40"),
        composer_variant="sunon_cdmx_v1c",
    )
    mobiliti = MobilitiSheetMutation(selector.to_xml(), mobiliti.row_map)
    cotizacion = CotizacionSheetEditor.from_xml(
        base.parts[base.sheet_part("Cotizacion")]
    ).compose(
        metadata=CotizacionMetadata(
            quotation_number="CDMX-V18-E2E",
            project="Contrato híbrido",
            quote_currency="MXN",
            delivery_place="ZMG",
        ),
        sections=tuple(sections),
        mobiliti_row_map=mobiliti.row_map,
        composer_variant="sunon_cdmx_v1c",
    )
    return ComposeRequest(
        template=CDMX_TEMPLATE,
        output=output,
        mobiliti=mobiliti,
        cotizacion=cotizacion,
        quotation=None,
        quotation_data=build_quotation_data_sheet(()),
        contract=load_template_contract(CDMX_CONTRACT),
    )


def test_cdmx_v18_composes_fresh_seventeen_section_financial_contract(
    tmp_path: Path,
) -> None:
    request = _fresh_cdmx_request(tmp_path / "cdmx-v18-17-sections.xlsx")

    compose_official_quote(request)

    package = XlsxPackage.read(request.output)
    row_map = request.mobiliti.row_map
    subtotal_rows = request.cotizacion.section_subtotal_rows
    subtotal_row = request.cotizacion.total_row - 4
    assert len(row_map.sections) == 17
    assert len(subtotal_rows) == 17
    assert _text(package, "Mobiliti", "P8") == "ZMG"
    assert _formula(package, "Cotizacion", "F17") == "=Mobiliti!AA15"
    assert _formula(package, "Cotizacion", "G17") == "=ROUND(Mobiliti!$AD$14,2)"
    assert _formula(package, "Cotizacion", f"H{subtotal_row}") == (
        "=SUM(" + ",".join(f"J{row}" for row in subtotal_rows) + ")"
    )
    assert _formula(package, "Cotizacion", f"H{subtotal_row + 1}") == (
        f"=H{subtotal_row}*$N${subtotal_row + 2}"
    )
    assert _formula(package, "Cotizacion", f"H{subtotal_row + 2}") == (
        f"=H{subtotal_row}+H{subtotal_row + 1}"
    )
    assert _formula(package, "Cotizacion", f"H{subtotal_row + 3}") == (
        f"=H{subtotal_row + 2}*16%"
    )
    assert _formula(package, "Cotizacion", f"H{subtotal_row + 4}") == (
        f"=H{subtotal_row + 2}+H{subtotal_row + 3}"
    )
    assert tuple(
        _scalar(package, "Cotizacion", f"D{subtotal_row + offset}")
        for offset in range(5)
    ) == (
        "SUBTOTAL:",
        "COSTO DE FLETE E INSTALACIÓN:",
        "SUBTOTAL:",
        "IVA:",
        "TOTAL:",
    )
    for offset in range(5):
        for column in "EFG":
            assert not _has_cell_payload(
                package,
                "Cotizacion",
                f"{column}{subtotal_row + offset}",
            )
    first_terms_row = (
        CDMX_COTIZACION_LAYOUT.terms_start
        + request.cotizacion.terms_row_delta
    )
    for row in range(request.cotizacion.total_row + 1, first_terms_row):
        for column in "ABCDEFGHIJ":
            assert not _has_cell_payload(
                package,
                "Cotizacion",
                f"{column}{row}",
            )
    print_end_row = (
        CDMX_COTIZACION_LAYOUT.print_end
        + request.cotizacion.terms_row_delta
    )
    cotizacion = ET.fromstring(package.parts[package.sheet_part("Cotizacion")])
    for cell in cotizacion.findall(f".//{{{MAIN}}}c"):
        coordinate = cell.attrib["r"]
        column = "".join(character for character in coordinate if character.isalpha())
        row = int("".join(character for character in coordinate if character.isdigit()))
        if column_index_from_string(column) <= 10 and row > print_end_row:
            assert not (
                cell.attrib.get("t") is not None
                or any(
                    child.tag
                    in {f"{{{MAIN}}}f", f"{{{MAIN}}}v", f"{{{MAIN}}}is"}
                    for child in cell
                )
            )
    for merge in cotizacion.findall(
        f"{{{MAIN}}}mergeCells/{{{MAIN}}}mergeCell"
    ):
        min_column, _min_row, max_column, max_row = range_boundaries(
            merge.attrib["ref"]
        )
        if min_column <= 10 and max_column >= 1:
            assert max_row <= print_end_row
    assert _formula(package, "Cotizacion", f"M{subtotal_row}") == (
        "=VLOOKUP(Mobiliti!$P$8,Tabla_Regiones,2,0)"
    )
    assert _formula(package, "Cotizacion", f"N{subtotal_row}") == (
        f'=IF(Mobiliti!$P$4=TRUE,Cotizacion!H{subtotal_row}*17.5,'
        f"Cotizacion!H{subtotal_row})"
    )
    assert _formula(package, "Cotizacion", f"O{subtotal_row}") == (
        f"=VLOOKUP(N{subtotal_row},Fletes!$D$45:$F$50,3,TRUE)"
    )
    assert _formula(package, "Cotizacion", f"N{subtotal_row + 2}") == (
        f"=M{subtotal_row}*O{subtotal_row}"
    )
    for coordinate in (
        f"M{subtotal_row}",
        f"N{subtotal_row}",
        f"O{subtotal_row}",
        f"N{subtotal_row + 2}",
    ):
        cell = cotizacion.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
        assert cell is not None
        assert cell.attrib.get("t") != "e"
        assert cell.find(f"{{{MAIN}}}v") is None
    assert _scalar(package, "Fletes", "A47") == "ZMG"
    assert _scalar(package, "Fletes", "B47") == "0.06"
    assert _scalar(package, "Fletes", "A48") == "CDMX"
    assert _scalar(package, "Fletes", "B48") == "0.15"
    assert _formula(package, "Fletes", "D19") == (
        f"=Mobiliti!H{row_map.total_row}"
    )
    assert f"${row_map.last_product_row}" in (
        _formula(package, "Fletes", "B61") or ""
    )
    assert _formula(package, "Estrategia Comercial ", "B70") == (
        f"=Cotizacion!H{request.cotizacion.total_row}"
    )
    assert _formula(package, "Control Administrativo", "E3") == (
        f"=Cotizacion!H{request.cotizacion.total_row - 2}"
    )
    assert _formula(package, "Control Administrativo", "E4") == (
        f"=Cotizacion!$H${request.cotizacion.total_row}"
    )

    workbook = ET.fromstring(package.parts["xl/workbook.xml"])
    defined_names = {
        node.attrib.get("name"): node.text
        for node in workbook.findall(f".//{{{MAIN}}}definedName")
    }
    assert defined_names["Tabla_Instalacion"] == "Fletes!$M$6:$N$21"
    assert defined_names["Tabla_Factor"] == "Fletes!$Q$6:$R$21"


def test_cdmx_v18_normalizes_delivery_but_official_selector_keeps_p8_formula() -> None:
    assert _official_delivery_place({}, variant="sunon_cdmx_v1c") == "ZMG"
    assert _official_delivery_place(
        {"delivery_place": "CDMX"},
        variant="sunon_cdmx_v1c",
    ) == "CDMX"

    base = XlsxPackage.read(TEMPLATES / "Formato Cotizacion 2026 Oficial.xlsx")
    editor = WorksheetEditor.from_xml(base.parts[base.sheet_part("Mobiliti")])
    before = editor.to_xml()
    write_official_currency_selector(
        editor,
        "MXN",
        "ZMG",
        Decimal("0.40"),
        composer_variant="official_v17",
    )
    before_root = ET.fromstring(before)
    after_root = ET.fromstring(editor.to_xml())
    before_p8 = before_root.find(f".//{{{MAIN}}}c[@r='P8']")
    after_p8 = after_root.find(f".//{{{MAIN}}}c[@r='P8']")
    assert before_p8 is not None and after_p8 is not None
    assert ET.tostring(after_p8) == ET.tostring(before_p8)
