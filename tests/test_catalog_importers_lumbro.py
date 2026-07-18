import hashlib
import json
from io import BytesIO
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import fitz
import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XlsxImage
from PIL import Image

from mobiliti_saas.worker.catalog_sync.importers import parse_lumbro_pdf_prices
from mobiliti_saas.worker.catalog_sync.importers import lumbro as lumbro_importer


FIXTURES = Path("tests/fixtures/catalog_graph/lumbro")
GENERAL_PATH = "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf"
NEW_PATH = "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf"
SPEC_PATH = "SPEC GUIDES 2026/LUMBRO/Spec guide-Lumbro-2026.xlsx"
INTERCONNECTION_PATH = "LUMBRO/LP/Precios Interconexión Sunón act.xlsx"
INTERCONNECTION_HEADER = "PRECIOS UNITARIOS MENOS EL 10% DESCUENTO MAS IVA"
MULT_LIDO_DESCRIPTION = (
    "Multicontacto Especial LINEA LIDO PARA INTERCONECTAR 4 Puertos AC , "
    "1 Puerto USB DE CARGA DOBLE TIPO A, CON ENTRADAS PARA ARNES DE AMBOS "
    "LADOS, MEDIDAS DE 42 X 16 CM"
)
LIDO_OP_DESCRIPTION = (
    "Multicontacto LIDO para canaleta COLOR GRIS OXFORD con 3 puertos AC No "
    "Regulados y 1 PUERTO USB CARGA DOBLE, para INTERCONECTAR"
)
JUMPER_DESCRIPTION = (
    "Cable de interconexión o JUMPER CON SALIDA PARA ARNES POR AMBOS LADOS "
    "de 1.5 metros para Carga No Regula"
)
FUSE_BOX_DESCRIPTION = (
    "Caja de Fusible, PARA CARGA NO REGULADA, con entrada para ARNES de un "
    "costado y del otro cable cal 14 con clavija de 2.5 m de longitud"
)


@dataclass(frozen=True)
class AdapterFile:
    path: str
    kind: str
    brand: str | None
    sha256: str
    mime_type: str
    local_path: Path | None


def _write_pdf(path: Path, fixture_name: str) -> None:
    pages = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    document = fitz.open()
    for expected_page, fixture in enumerate(pages, 1):
        assert fixture["page"] == expected_page
        page = document.new_page(width=612, height=792)
        y = 45
        for line in fixture["text"].splitlines():
            page.insert_text((45, y), line, fontsize=9)
            y += 15
    document.save(path)
    document.close()


def _adapter_file(logical_path: str, local_path: Path) -> AdapterFile:
    return AdapterFile(
        path=logical_path,
        kind="price_list",
        brand=None,
        sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
        mime_type="application/pdf",
        local_path=local_path,
    )


def _spec_file(local_path: Path) -> AdapterFile:
    return AdapterFile(
        path=SPEC_PATH,
        kind="spec_guide",
        brand=None,
        sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        local_path=local_path,
    )


def _interconnection_file(local_path: Path) -> AdapterFile:
    return AdapterFile(
        path=INTERCONNECTION_PATH,
        kind="price_list",
        brand=None,
        sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        local_path=local_path,
    )


def _png_bytes(color="#303030") -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 16), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def pdf_sources(tmp_path):
    general = tmp_path / "LISTA DE PRECIOS MULTICONTACTOS 2026.pdf"
    new = tmp_path / "LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf"
    _write_pdf(general, "price_general_pages.json")
    _write_pdf(new, "price_new_pages.json")
    return (
        _adapter_file(GENERAL_PATH, general),
        _adapter_file(NEW_PATH, new),
    )


@pytest.fixture
def spec_source(tmp_path):
    path = tmp_path / "Spec guide-Lumbro-2026.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SPEC-GUIDE-LUMBRO"
    sheet.append([])
    for column, value in enumerate(
        ("Cod.", "Imagen.", "Descripcion.", "Medida/Unidad.", "P. Unitario.", "Moneda"),
        1,
    ):
        sheet.cell(8, column, value)

    sheet.merge_cells("C9:F9")
    sheet["C9"] = "Barcelona"
    sheet["A10"] = "BARCELONA"
    sheet["C10"] = "Multicontacto, Modelo Barcelona. Incluye: 3 puertos de corriente"
    sheet["D10"] = "245 x 102 x 60 mm"
    sheet["E10"] = 5648
    sheet["F10"] = "MXN"
    sheet["C11"] = "  y 1 cable de metro con clavija  "
    sheet["C12"] = "Color: Gris Aluminio, Negro"
    sheet["C14"] = "Su montaje requiere orificio de 220 x 68 mm"
    sheet["C15"] = "NOTA: SE PUEDEN MODIFICAR LAS CONEXIONES"

    sheet.merge_cells("C19:F19")
    sheet["C19"] = "Barcelona Carga"
    sheet["A20"] = "BARCELONA"
    sheet["C20"] = "Multicontacto, Modelo Barcelona. Incluye: USB de carga"
    sheet["D20"] = "245 x 102 x 60 mm"
    sheet["E20"] = 5648
    sheet["F20"] = "MXN"
    sheet["C21"] = "y 1 cable de metro con clavija"
    sheet["C22"] = "Color: Blanco"
    sheet["C24"] = "Su montaje requiere orificio de 220 x 68 mm"

    sheet.merge_cells("C29:F29")
    sheet["C29"] = "Lisboa"
    sheet["A30"] = "LISBOA"
    sheet["C30"] = "Multicontacto, Modelo Lisboa"
    sheet["D30"] = "245 x 102 x 60 mm"
    sheet["E30"] = 4000
    sheet["F30"] = "MXN"
    sheet["C31"] = "Color: Negro"

    sheet.merge_cells("C39:F39")
    sheet["C39"] = "Ibiza Carga"
    sheet["A40"] = "IBIZA"
    sheet["C40"] = "Multicontacto, Modelo Ibiza con USB de carga"
    sheet["D40"] = "185 x 75 x 60 mm"
    sheet["E40"] = 1648
    sheet["F40"] = "MXN"
    sheet["C41"] = "Color: Negro"

    sheet.merge_cells("C49:F49")
    sheet["C49"] = "Monaco"
    sheet["A50"] = "MONACO-G"
    sheet["C50"] = "Pasacables, Modelo Monaco G"
    sheet["D50"] = "300 x 111 x 28 mm"
    sheet["E50"] = 1000
    sheet["F50"] = "MXN"
    sheet["C51"] = "Color: Negro"

    sheet.merge_cells("C59:F59")
    sheet["C59"] = "Barcelona box HDMI"
    sheet["A60"] = "BARCELONA BOX IN"
    sheet["C60"] = "Multicontacto Barcelona con HDMI inalambrico"
    sheet["E60"] = 5648
    sheet["F60"] = "MXN"
    sheet["C61"] = "Color: Negro"

    sheet.add_image(XlsxImage(BytesIO(_png_bytes("#303030"))), "B10")
    sheet.add_image(XlsxImage(BytesIO(_png_bytes("#0044AA"))), "B20")
    sheet.add_image(XlsxImage(BytesIO(_png_bytes("#AA0000"))), "A1")
    sheet.add_image(XlsxImage(BytesIO(_png_bytes("#00AA00"))), "B31")
    sheet.add_image(XlsxImage(BytesIO(_png_bytes("#AA00AA"))), "C31")
    workbook.save(path)
    workbook.close()
    return _spec_file(path)


@pytest.fixture
def lumbro_spec(spec_source):
    return lumbro_importer.parse_lumbro_spec_guide(spec_source)


def _write_interconnection_workbook(
    path: Path,
    *,
    active_sheet: str = "2026",
    header: str = INTERCONNECTION_HEADER,
) -> None:
    workbook = Workbook()
    old = workbook.active
    old.title = "2025"
    current = workbook.create_sheet("2026")

    old["H3"] = INTERCONNECTION_HEADER
    old["G4"] = MULT_LIDO_DESCRIPTION
    old["H4"] = 2587.5
    old.add_image(XlsxImage(BytesIO(_png_bytes("#AA0000"))), "C13")

    current["H3"] = header
    current["G4"] = MULT_LIDO_DESCRIPTION
    current["H4"] = 3003
    current["G7"] = LIDO_OP_DESCRIPTION
    current["H7"] = 1394.07
    current["G13"] = JUMPER_DESCRIPTION
    current["H13"] = 350
    current["G19"] = FUSE_BOX_DESCRIPTION
    current["H19"] = 772
    current["G26"] = "Multicontacto sin código oficial verificable"
    current["H26"] = 999
    current["O4"] = "Configuración alterna sin código verificable"
    current["P4"] = 3277
    current["O5"] = "Esta pareja no está autorizada"
    current["P5"] = 1234

    current.add_image(XlsxImage(BytesIO(_png_bytes("#303030"))), "C4")
    current.add_image(XlsxImage(BytesIO(_png_bytes("#008888"))), "C7")
    current.add_image(XlsxImage(BytesIO(_png_bytes("#0044AA"))), "C13")
    current.add_image(XlsxImage(BytesIO(_png_bytes("#00AA00"))), "C19")
    current.add_image(XlsxImage(BytesIO(_png_bytes("#AA00AA"))), "I19")
    current.add_image(XlsxImage(BytesIO(_png_bytes("#AAAA00"))), "C30")

    workbook.active = workbook.sheetnames.index(active_sheet)
    workbook.save(path)
    workbook.close()


@pytest.fixture
def interconnection_source(tmp_path):
    path = tmp_path / "Precios Interconexion Sunon act.xlsx"
    _write_interconnection_workbook(path)
    return _interconnection_file(path)


def test_general_pdf_keeps_published_net_price(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    barcelona = next(row for row in rows if row.identity == "barcelona")

    assert barcelona.model == "Barcelona"
    assert barcelona.configuration == ""
    assert barcelona.net_price == Decimal("2824")
    assert barcelona.currency == "MXN"
    assert barcelona.tax_rate == Decimal("0.16")
    assert barcelona.authority_rank == 3
    assert barcelona.parse_status == "parsed"
    assert barcelona.source.path == GENERAL_PATH
    assert barcelona.source.file_id == pdf_sources[0].sha256
    assert barcelona.source.page > 0
    assert not isinstance(barcelona.net_price, float)


def test_general_pdf_parses_explicit_ibiza_carga_a_c_configuration(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    ibiza = next(row for row in rows if row.identity == "ibiza carga a c")

    assert ibiza.model == "Ibiza"
    assert ibiza.configuration == "Carga A+C"
    assert ibiza.net_price == Decimal("824")
    assert ibiza.parse_status == "parsed"


def test_general_pdf_does_not_swallow_currency_separator_before_wrapped_model(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    box = next(row for row in rows if row.identity == "barcelona box hdmi inalambrico")

    assert box.model == "Barcelona"
    assert box.configuration == "Box/HDMI Inalámbrico"
    assert box.net_price == Decimal("2824")


def test_new_pdf_parses_venecia_inalambrico_with_higher_authority(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    venecia = [
        row
        for row in rows
        if row.identity == "venecia inalambrico" and row.source.path == NEW_PATH
    ]

    assert len(venecia) == 1
    assert venecia[0].model == "Venecia"
    assert venecia[0].configuration == "Inalámbrico"
    assert venecia[0].net_price == Decimal("1490")
    assert venecia[0].authority_rank == 2
    assert venecia[0].parse_status == "parsed"


def test_pdf_repeated_headings_are_not_product_rows(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)

    assert all(row.identity not in {"modelo", "precio"} for row in rows)
    assert sum(row.identity == "torre octa" for row in rows) == 2


def test_general_pdf_malformed_currency_is_retained_for_review(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    hamburgo = next(row for row in rows if row.identity == "hamburgo")

    assert hamburgo.net_price is None
    assert hamburgo.parse_status == "needs_review"
    assert "malformed_currency" in hamburgo.warnings
    assert hamburgo.source.path == GENERAL_PATH


def test_new_pdf_conflicting_duplicate_prices_remain_as_review_evidence(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    duplicates = [row for row in rows if row.identity == "torre octa"]

    assert [row.net_price for row in duplicates] == [Decimal("5480"), Decimal("5580")]
    assert all(row.parse_status == "needs_review" for row in duplicates)
    assert all("conflicting_price" in row.warnings for row in duplicates)
    assert [row.source.page for row in duplicates] == [1, 2]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: rows[:1],
        lambda rows: (rows[0], rows[0]),
        lambda rows: (replace(rows[0], path="LUMBRO/LP/otro.pdf"), rows[1]),
        lambda rows: (replace(rows[0], kind="catalog"), rows[1]),
        lambda rows: (replace(rows[0], mime_type="application/octet-stream"), rows[1]),
        lambda rows: (replace(rows[0], sha256="0" * 64), rows[1]),
    ],
)
def test_pdf_source_set_is_strictly_validated(pdf_sources, mutation):
    with pytest.raises(ValueError, match="LUMBRO_PDF_(?:BUNDLE|HASH)"):
        parse_lumbro_pdf_prices(mutation(pdf_sources))


def test_spec_guide_extracts_identity_dimensions_colors_and_exact_provenance(lumbro_spec):
    variants = [
        record
        for record in lumbro_spec.records
        if record.model == "Barcelona" and record.configuration == ""
    ]

    assert {record.color for record in variants} == {"Gris Aluminio", "Negro"}
    assert {record.code for record in variants} == {"BARCELONA"}
    assert {record.dimensions for record in variants} == {"245 x 102 x 60 mm"}
    assert all(
        record.description
        == "Multicontacto, Modelo Barcelona. Incluye: 3 puertos de corriente y 1 cable de metro con clavija"
        for record in variants
    )
    assert all(record.mounting == "Su montaje requiere orificio de 220 x 68 mm" for record in variants)
    assert all(record.source.row == 10 and record.source.heading_row == 9 for record in variants)
    assert all(record.provenance["code"][0]["cell_or_bbox"] == "A10" for record in variants)
    assert all(
        [reference["cell_or_bbox"] for reference in record.provenance["description"]]
        == ["C10", "C11"]
        for record in variants
    )
    assert all(record.provenance["dimensions"][0]["cell_or_bbox"] == "D10" for record in variants)
    assert all(record.provenance["color"][0]["cell_or_bbox"] == "C12" for record in variants)
    assert all(record.notes == ("NOTA: SE PUEDEN MODIFICAR LAS CONEXIONES",) for record in variants)
    assert all(record.provenance["notes"][0]["cell_or_bbox"] == "C15" for record in variants)
    assert all(record.spec_price_evidence == 5648 for record in variants)
    assert all(record.net_price is None for record in variants)


def test_spec_guide_emits_only_explicit_variants_and_safe_family_image_reuse(lumbro_spec):
    variants = {
        (record.model, record.configuration, record.color): record
        for record in lumbro_spec.records
    }

    assert set(variants) == {
        ("Barcelona", "", "Gris Aluminio"),
        ("Barcelona", "", "Negro"),
        ("Barcelona", "Carga", "Blanco"),
        ("Barcelona", "Box/HDMI Inalámbrico", "Negro"),
        ("Ibiza", "Carga", "Negro"),
        ("Lisboa", "", "Negro"),
        ("Monaco", "G", "Negro"),
    }
    bindings = {binding.internal_id: binding for binding in lumbro_spec.bindings}
    for identity, image_cell in (
        (("Barcelona", "", "Gris Aluminio"), "B10"),
        (("Barcelona", "", "Negro"), "B10"),
        (("Barcelona", "Carga", "Blanco"), "B20"),
    ):
        record = variants[identity]
        assert record.image_warning == "El color puede variar"
        assert bindings[record.internal_id].match_status == "family_xlsx"
        assert bindings[record.internal_id].source_references[0]["cell_or_bbox"] == image_cell

    lisboa = variants[("Lisboa", "", "Negro")]
    assert lisboa.image_sha256 is None
    assert lisboa.internal_id not in bindings
    assert len(lumbro_spec.assets_by_sha256) == 2


def test_spec_guide_excludes_non_candidate_anchors_and_ambiguous_family_reuse(lumbro_spec):
    variants = {
        (record.model, record.configuration, record.color): record
        for record in lumbro_spec.records
    }
    bindings = {binding.internal_id: binding for binding in lumbro_spec.bindings}
    box = variants[("Barcelona", "Box/HDMI Inalámbrico", "Negro")]

    assert box.image_sha256 is None
    assert box.internal_id not in bindings
    assert {
        reference["cell_or_bbox"]
        for binding in bindings.values()
        for reference in binding.source_references
    } == {"B10", "B20"}
    assert len(lumbro_spec.assets_by_sha256) == 2


def test_spec_guide_prefers_explicit_configuration_in_heading_or_code(lumbro_spec):
    identities = {
        (record.source.row, record.model, record.configuration)
        for record in lumbro_spec.records
    }

    assert (40, "Ibiza", "Carga") in identities
    assert (50, "Monaco", "G") in identities
    assert (60, "Barcelona", "Box/HDMI Inalámbrico") in identities

    monaco = next(record for record in lumbro_spec.records if record.source.row == 50)
    assert [ref["cell_or_bbox"] for ref in monaco.provenance["model"]] == ["C49", "A50"]
    assert [ref["cell_or_bbox"] for ref in monaco.provenance["configuration"]] == ["A50"]


def test_spec_price_is_never_commercial_authority(lumbro_spec, pdf_sources):
    prices = parse_lumbro_pdf_prices(pdf_sources)
    enriched = lumbro_importer.reconcile_lumbro_spec_prices(lumbro_spec.records, prices)
    barcelona = next(
        record
        for record in enriched
        if record.model == "Barcelona" and record.configuration == "" and record.color == "Negro"
    )

    assert barcelona.net_price == Decimal("2824")
    assert barcelona.spec_price_evidence == 5648
    assert barcelona.price_source is not None
    assert barcelona.price_source.path.endswith("LISTA DE PRECIOS MULTICONTACTOS 2026.pdf")
    assert barcelona.provenance["spec_price_evidence"][0]["cell_or_bbox"] == "E10"


def test_spec_price_collision_for_same_identity_fails_closed(lumbro_spec, pdf_sources):
    price = next(row for row in parse_lumbro_pdf_prices(pdf_sources) if row.identity == "barcelona")
    collision = replace(price, net_price=Decimal("9999"))

    enriched = lumbro_importer.reconcile_lumbro_spec_prices(
        lumbro_spec.records, (price, collision)
    )
    barcelona = next(
        record
        for record in enriched
        if record.model == "Barcelona" and record.configuration == ""
    )

    assert barcelona.net_price is None
    assert barcelona.price_source is None
    assert barcelona.spec_price_evidence == 5648


def test_spec_workbook_is_closed_when_parsing_raises(spec_source, monkeypatch):
    workbook = Workbook()
    workbook.active.title = "SPEC-GUIDE-LUMBRO"

    class TrackingWorkbook:
        closed = False

        @property
        def sheetnames(self):
            return workbook.sheetnames

        def __getitem__(self, name):
            return workbook[name]

        def close(self):
            self.closed = True
            workbook.close()

    tracking = TrackingWorkbook()
    monkeypatch.setattr(lumbro_importer, "open_xlsx_data_only", lambda _path: tracking)

    with pytest.raises(ValueError, match="LUMBRO_SPEC_HEADER"):
        lumbro_importer.parse_lumbro_spec_guide(spec_source)

    assert tracking.closed is True


def test_interconnection_uses_active_2026_cached_net_value(interconnection_source):
    build = lumbro_importer.parse_lumbro_interconnection(interconnection_source)
    item = next(record for record in build.records if record.code == "MULT-LIDO-INT")

    assert item.net_price == Decimal("3003")
    assert item.net_price != Decimal("2702.7")
    assert item.source.sheet == "2026"
    assert item.source.row == 4
    assert item.source.description_cell == "G4"
    assert item.source.price_cell == "H4"
    assert item.authority_rank == 4
    assert all(record.net_price != Decimal("2587.5") for record in build.records)


def test_interconnection_maps_only_exact_spec_descriptions(interconnection_source):
    build = lumbro_importer.parse_lumbro_interconnection(interconnection_source)
    by_description = {record.description: record for record in build.records}

    assert by_description[MULT_LIDO_DESCRIPTION].code == "MULT-LIDO-INT"
    assert by_description[LIDO_OP_DESCRIPTION].code == "LIDO.OP-INT"
    assert by_description[JUMPER_DESCRIPTION].code == "JUMP-1.5M"
    assert by_description[FUSE_BOX_DESCRIPTION].code == "CAJA-FUS"

    unknown = by_description["Multicontacto sin código oficial verificable"]
    assert unknown.code == ""
    assert unknown.parse_status == "needs_review"
    assert "missing_code" in unknown.warnings
    assert all(record.description != "Esta pareja no está autorizada" for record in build.records)


def test_interconnection_parses_only_the_o_p_pair_on_row_four(interconnection_source):
    build = lumbro_importer.parse_lumbro_interconnection(interconnection_source)
    alternate = next(
        record
        for record in build.records
        if record.description == "Configuración alterna sin código verificable"
    )

    assert alternate.source.description_cell == "O4"
    assert alternate.source.price_cell == "P4"
    assert alternate.net_price == Decimal("3277")
    assert alternate.code == ""
    assert alternate.parse_status == "needs_review"


def test_interconnection_images_are_active_sheet_only_and_fail_closed(
    interconnection_source,
):
    build = lumbro_importer.parse_lumbro_interconnection(interconnection_source)
    bindings = {binding.internal_id: binding for binding in build.bindings}
    records = {record.description: record for record in build.records}

    jumper = records[JUMPER_DESCRIPTION]
    assert bindings[jumper.internal_id].source_references[0]["sheet_or_page"] == "2026"
    assert bindings[jumper.internal_id].source_references[0]["cell_or_bbox"] == "C13"

    fuse_box = records[FUSE_BOX_DESCRIPTION]
    assert fuse_box.internal_id not in bindings
    assert "ambiguous_image" in fuse_box.warnings
    assert len(build.assets_by_sha256) == 2
    assert len(build.image_evidence) == 6
    assert {evidence.source.sheet for evidence in build.image_evidence} == {"2026"}
    assert {
        (evidence.source.cell, evidence.status, evidence.reason)
        for evidence in build.image_evidence
    } == {
        ("C4", "excluded", "ambiguous_product_row"),
        ("C7", "bound", None),
        ("C13", "bound", None),
        ("C19", "excluded", "ambiguous_images"),
        ("I19", "excluded", "ambiguous_images"),
        ("C30", "excluded", "no_product_row"),
    }


@pytest.mark.parametrize(
    ("active_sheet", "header", "error"),
    [
        ("2025", INTERCONNECTION_HEADER, "LUMBRO_INTERCONNECTION_ACTIVE_SHEET"),
        ("2026", "Precio neto aproximado", "LUMBRO_INTERCONNECTION_HEADER"),
    ],
)
def test_interconnection_rejects_wrong_active_sheet_or_header(
    tmp_path, active_sheet, header, error
):
    path = tmp_path / "Precios Interconexion Sunon act.xlsx"
    _write_interconnection_workbook(path, active_sheet=active_sheet, header=header)

    with pytest.raises(ValueError, match=error):
        lumbro_importer.parse_lumbro_interconnection(_interconnection_file(path))
