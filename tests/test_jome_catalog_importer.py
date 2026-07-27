import hashlib
import importlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorksheetImage
from PIL import Image

from mobiliti_saas.quote_engine.supplier_catalog import PUBLIC_ITEM_FIELDS, load_supplier_catalog_data
from mobiliti_saas.worker.catalog_sync.importers.common import CatalogSnapshotBuild


MODULE = "mobiliti_saas.worker.catalog_sync.importers.jome"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class SourceDocument:
    """Documento mínimo que el importador recibe desde el sincronizador."""

    path: str
    kind: str
    sha256: str
    mime_type: str
    local_path: Path


def _jome_module():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        pytest.fail(f"Falta el importador JOME: {error.name}")


def _image(color: str) -> WorksheetImage:
    data = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(data, "PNG")
    data.seek(0)
    return WorksheetImage(data)


def _write_catalog(path: Path, sheet_name: str, rows: tuple[tuple, ...], *, image: bool = False) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet["A1"] = "Sistema: Operativo"
    sheet["A2"] = "Bloque: Mesas"
    sheet.append(("", "Código", "Descripción", "Medidas", "Costo", "", "", "Moneda", "Precio comercial"))
    for row in rows:
        sheet.append(row)
    if image:
        sheet.add_image(_image("red"), "A4")
    workbook.save(path)
    workbook.close()


def _document(path: Path, kind: str) -> SourceDocument:
    return SourceDocument(
        path=path.name,
        kind=kind,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        mime_type=MIME,
        local_path=path,
    )


@pytest.fixture
def jome_documents(tmp_path):
    estructuras = tmp_path / "Spec guide-Estructuras Jome-2026.xlsx"
    laminado = tmp_path / "Spec guide-Laminado-2026.xlsx"
    _write_catalog(
        estructuras,
        "COSTO ESTRUCTURAS 2026",
        (
            ("", "MA02", "Mesa operativa", "120 x 60 cm", 135, "", "", "USD", 999),
            ("", "MA03", "Mesa ejecutiva", "160 x 70 cm", 210, "", "", "USD", 998),
            ("", "", "Mesa ejecutiva ampliada", "180 x 70 cm", 230, "", "", "USD", 997),
            ("", "DUP-1", "Mesa compacta", "100 x 50 cm", 99, "", "", "MXN", 777),
        ),
        image=True,
    )
    _write_catalog(
        laminado,
        "COSTO LAMINADO 2026",
        (("", "DUP-1", "Mesa laminada", "100 x 50 cm", 125, "", "", "MXN", 888),),
    )
    return (_document(estructuras, "estructuras"), _document(laminado, "laminado"))


def _item(snapshot: dict, code: str, subcatalog: str) -> dict:
    return next(
        row
        for row in snapshot["items"]
        if row["code"] == code and row["subcatalog"] == subcatalog
    )


def test_importa_costos_jome_de_columna_e_normaliza_moneda_y_conserva_procedencia(jome_documents):
    """Falla si se usa I, se convierte USD o se pierde la procedencia de MA02/MA03."""
    snapshot = _jome_module().import_jome_catalog(
        jome_documents,
        synced_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    ma02 = _item(snapshot, "MA02", "estructuras")
    ma03 = _item(snapshot, "MA03", "estructuras")
    assert ma02["raw_cost"] == Decimal("135")
    assert ma02["base_currency"] == "MXN"
    assert ma02["provenance"]["declared_currency"] == "USD"
    assert ma02["provenance"]["currency_normalization"] == "human_source_error_to_mxn"
    assert ma03["raw_cost"] == Decimal("210")
    assert ma03["raw_cost"] != Decimal("998")
    assert ma02["provenance"]["cost_cell"] == "E4"
    assert ma02["provenance"]["sheet"] == "COSTO ESTRUCTURAS 2026"


def test_jome_preserva_codigos_repetidos_y_comparte_imagen_del_bloque(jome_documents):
    """Falla si se colapsan códigos por catálogo o se pierde el ancla OOXML compartida."""
    snapshot = _jome_module().import_jome_catalog(
        jome_documents,
        synced_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    estructuras = _item(snapshot, "DUP-1", "estructuras")
    laminado = _item(snapshot, "DUP-1", "laminado")
    ma02 = _item(snapshot, "MA02", "estructuras")
    ma03 = _item(snapshot, "MA03", "estructuras")
    assert estructuras["identity"] != laminado["identity"]
    assert ma02["identity"] == "estructuras:operativo:mesas:ma02:120-x-60-cm:4"
    assert ma02["image"]["sha256"] == ma03["image"]["sha256"]
    assert ma02["image"]["source_reference"]["cell_or_bbox"] == "A4"


def test_jome_hereda_codigo_solo_para_variante_dentro_del_bloque_explicito(jome_documents):
    """Falla si una variante de bloque se descarta en vez de conservar la familia explícita."""
    snapshot = _jome_module().import_jome_catalog(
        jome_documents,
        synced_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    variant = next(row for row in snapshot["items"] if row["name"] == "Mesa ejecutiva ampliada")
    assert variant["code"] == "MA03"
    assert variant["identity"] == "estructuras:operativo:mesas:ma03:180-x-70-cm:6"


def test_builder_jome_produce_snapshot_publico_y_assets_aprobados(jome_documents):
    """Falla si el adaptador deja registros crudos fuera del contrato del servicio."""
    build = _jome_module().build_jome_snapshot_with_assets(
        jome_documents,
        synced_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    assert isinstance(build, CatalogSnapshotBuild)
    assert all(set(item) == set(PUBLIC_ITEM_FIELDS) for item in build.snapshot["items"])
    loaded = load_supplier_catalog_data(build.snapshot, expected_supplier="jome")
    ma02 = next(item for item in loaded["items"] if item["attributes"]["source_code"] == "MA02")
    assert ma02["price_net"] == "135.000000"
    assert ma02["base_currency"] == "MXN"
    assert ma02["attributes"]["provenance"]["declared_currency"] == "USD"
    assert build.assets_by_sha256
    assert build.bindings[0].image_kind == "official"


def test_builder_jome_se_expone_desde_el_paquete_de_importadores(jome_documents):
    """Falla si el sincronizador no puede resolver el adaptador JOME por su paquete normal."""
    from mobiliti_saas.worker.catalog_sync.importers import build_jome_snapshot_with_assets

    build = build_jome_snapshot_with_assets(
        jome_documents,
        synced_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert isinstance(build, CatalogSnapshotBuild)


OFFICIAL_ROOT = Path("tmp/jome-lauco-source")
OFFICIAL_JOME = (
    OFFICIAL_ROOT / "Spec guide-Estructuras Jome-2026.xlsx",
    OFFICIAL_ROOT / "Spec guide-Laminado-2026.xlsx",
)


@pytest.mark.skipif(not all(path.is_file() for path in OFFICIAL_JOME), reason="copias oficiales JOME no disponibles")
def test_jome_oficial_lee_costos_e_y_sanea_wdp_sin_perder_procedencia():
    """Falla si WDP bloquea el libro o si E/I y las etiquetas USD se confunden."""
    snapshot = _jome_module().import_jome_catalog(
        (_document(OFFICIAL_JOME[0], "estructuras"), _document(OFFICIAL_JOME[1], "laminado")),
        synced_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    measi = _item(snapshot, "MEASI-2009", "estructuras")
    af = _item(snapshot, "AF-2C1G", "laminado")
    ma02 = _item(snapshot, "MA02", "estructuras")
    ma03 = _item(snapshot, "MA03", "estructuras")
    assert measi["raw_cost"] == Decimal("9500")
    assert af["raw_cost"] == Decimal("2975")
    assert ma02["raw_cost"] == Decimal("135")
    assert ma03["raw_cost"] == Decimal("75")
    assert {ma02["provenance"]["declared_currency"], ma03["provenance"]["declared_currency"]} == {"USD"}
    assert all(row["base_currency"] == "MXN" for row in (ma02, ma03))
    assert all(
        row["provenance"]["currency_normalization"] == "human_source_error_to_mxn"
        for row in (ma02, ma03)
    )
    assert sum(row["subcatalog"] == "estructuras" for row in snapshot["items"]) == 219
    # Las 355 filas laminadas con código se complementan con MJ-4015 (fila 525),
    # variante sin código que hereda la familia explícita del bloque.
    assert sum(row["subcatalog"] == "laminado" for row in snapshot["items"]) == 356
