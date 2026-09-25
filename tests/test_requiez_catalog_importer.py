from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import fitz
import pytest
from PIL import Image

from mobiliti_saas.quote_engine.supplier_catalog import load_supplier_catalog_data


MODULE = "mobiliti_saas.worker.catalog_sync.importers.requiez"
MIME = "application/pdf"
OFFICIAL_SHA256 = "7f3281d1965c67a234bac55112800067019ad471f835de59ff758e759eca56ba"
OFFICIAL_FALLBACK = Path(
    r"C:\Users\pepem\AppData\Local\Temp\mobiliti-catalog-discovery-20260818"
    r"\Lista de precios A-26.pdf"
)

EXPECTED_ACCESSORY_LABELS = {
    (110, "RA-01", 1253): "Juego de brazos ajustables de poliuretano.",
    (110, "RA-02", 1253): "Juego de brazos fijos de poliuretano.",
    (110, "RA-04", 1132): "Juego de brazos ajustables de polipropileno.",
    (110, "RA-05", 595): "Juego de brazos fijos poliuretano.",
    (110, "RA-09G", 751): "Juego de brazos fijos Quadra poliuretano.",
    (110, "RA-10", 1157): "Juego de brazos opcionales ajustables con pad de poliuretano.",
    (110, "RA-11", 408): "Juego de brazos opcionales fijos de polipropileno.",
    (110, "RA-14", 414): "Juego de brazos fijos 1D de polipropileno.",
    (110, "RA-20", 398): "Juego de brazos fijo Rewind de poliuretano.",
    (110, "RA-12", 280): "Juego de grapas unión Rewind / Sin brazos.",
    (110, "RA-13", 280): "Juego de grapas unión Rewind / Con brazos.",
    (111, "RA-06", 1607): (
        "Juego de brazos fijos de poliuretano con vistas en aluminio pulido."
    ),
    (111, "RA-07", 1253): "Juego de brazos ajustables de polipropileno.",
    (111, "RA-08", 1387): "Juego de brazos giratorios de polipropileno.",
    (111, "RA-09N", 751): "Juego de brazos fijos Quadra de polipropileno.",
    (111, "RA-15", 414): "Juego de brazos fijos de polipropileno.",
    (111, "RA-16", 990): "Juego de brazos ajustables 2D de poliuretano.",
    (111, "RA-17", 1155): "Juego de brazos ajustables 3D de poliuretano.",
    (111, "RA-18", 534): "Juego de brazos en color negro de polipropileno.",
    (112, "RA-30", 1221): "GR",
    (112, "RA-30", 1054): "NG",
    (112, "RA-31", 1174): "Cabecera ajustable en tela.",
    (112, "RA-35N", 1132): "Cabecera ajustable en piel. Negra o gris.",
    (112, "RA-36", 1094): "Cabecera ajustableen color negro, azul, verde o tórtora.",
    (112, "RA-40", 767): "Kit de extensión para banco de Acero cromado.",
    (112, "RA-45GR", 845): "Kit de extensión para banco de Nylon color gris.",
    (112, "RA-45N", 744): "Kit de extensión para banco alto. Pistón extra largo de Nylon.",
    (112, "RA-49", 1285): "Kit de extensión para banco. Pistón extra largo de Nylon negro.",
    (112, "RA-41", 673): "Descansapies de Nylon.",
    (112, "RA-88", 169): "Modificación",
    (112, "RA-88", 277): "Accesorio",
    (112, "RA-90", 260): "Juego de regatones 50mm (5 piezas).",
    (112, "RA-91", 260): "Juego de regatones altos 60mm. (5 piezas).",
    (113, "RA-95", 979): "Cabecera en mesh.",
    (113, "RA-23", 563): "Kit de base de tapiz asiento Rewind.",
    (113, "RA-27", 884): "Kit de base de tapiz asiento Outline.",
    (113, "RA-28", 1034): "Kit de base de tapiz asiento Antonella.",
    (113, "RA-29", 1198): "Kit de base de tapiz asiento Nico.",
    (113, "RA-24", 1645): "Base aluminio 24” elaborada en aluminio pulido.",
    (113, "RA-25", 1914): "Base de aluminio pulido largo de Nylon.",
    (113, "RA-26", 1766): "Base aluminio 26” Elaborada en aluminio pulido.",
}

EXPECTED_ACCESSORY_DESCRIPTORS = {
    code: label
    for (_page, code, _price), label in EXPECTED_ACCESSORY_LABELS.items()
    if code not in {"RA-30", "RA-88"}
}
EXPECTED_ACCESSORY_DESCRIPTORS.update(
    {
        "RA-30": "Cabecera en Mesh. Negro o gris cálido.",
        "RA-88": "Juego de rodajas para piso delicado en poliuretano (5 piezas).",
    }
)

EXACT_ACCESSORY_DESCRIPTIONS = {
    "RA-01": (
        "Juego de brazos ajustables de poliuretano. Modelos compatibles "
        "430, 460, 470, 490, 650, 680, 100."
    ),
    "RA-95": "Cabecera en mesh. Modelos compatibles 9101.",
    "RA-24": (
        "Base aluminio 24” elaborada en aluminio pulido. Modelos compatibles "
        "430, 460, 470, 492, 650, 680."
    ),
    "RA-25": (
        "Base de aluminio pulido largo de Nylon. Modelos compatibles Aria."
    ),
    "RA-26": (
        "Base aluminio 26” Elaborada en aluminio pulido. Modelos compatibles "
        "1500, 1510, 1950, 1951, 4500, 4501."
    ),
    "RA-88": (
        "Juego de rodajas para piso delicado en poliuretano (5 piezas). "
        "Modelos compatibles 430, 460, 470, 490, 650, 680, 1500, 1510."
    ),
}


@dataclass(frozen=True)
class SourceDocument:
    path: str
    kind: str
    brand: str | None
    sha256: str
    mime_type: str
    local_path: Path


def _module():
    try:
        return __import__(MODULE, fromlist=["*"])
    except ModuleNotFoundError as error:
        pytest.fail(f"Falta el importador Requiez: {error.name}")


def _source(path: Path) -> SourceDocument:
    return SourceDocument(
        path="REQUIEZ/Lista de precios A-26.pdf",
        kind="price_list",
        brand=None,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        mime_type=MIME,
        local_path=path,
    )


def _fixture_pdf(path: Path) -> None:
    image_path = path.with_name("aria.png")
    Image.new("RGB", (120, 180), "#c8b7a6").save(image_path)
    document = fitz.open()

    page = document.new_page(width=612, height=792)
    page.insert_text((36, 55), "ARIA", fontsize=32)
    page.insert_text((36, 75), "SILLAS Y BANCOS DE TRABAJO", fontsize=9)
    page.insert_text((443, 196), "RP-1400/GC", fontsize=12)
    page.insert_text((443, 211), "RP-1400/NG", fontsize=12)
    page.insert_text((443, 226), "$11,345.00", fontsize=12)
    page.insert_text((443, 330), "$999.00", fontsize=12)  # sin SKU: no se arrastra
    page.insert_image(fitz.Rect(280, 500, 418, 730), filename=os.fspath(image_path))
    page.insert_text((310, 746), "RP-1400/GC", fontsize=9)

    page = document.new_page(width=612, height=792)
    page.insert_text((36, 55), "CHAP", fontsize=32)
    page.insert_text((36, 75), "MESAS", fontsize=9)
    page.insert_text((422, 175), "CUBIERTA CIRCULAR", fontsize=8)
    page.insert_text((422, 195), "ESTRUCTURA NG", fontsize=8)
    page.insert_text((422, 230), "RM-40/D40", fontsize=10)
    page.insert_text((422, 243), "$1,838.00", fontsize=10)
    page.insert_text((496, 195), "ESTRUCTURA BCO/ANT/DGY/", fontsize=7)
    page.insert_text((496, 205), "MELAMINA", fontsize=7)
    page.insert_text((496, 230), "RM-40/D40", fontsize=10)
    page.insert_text((496, 243), "$2,106.00", fontsize=10)

    page = document.new_page(width=612, height=792)
    page.insert_text((36, 55), "STELLA", fontsize=32)
    page.insert_text((36, 75), "SILLONES", fontsize=9)
    page.insert_text((443, 196), "STE-01", fontsize=12)
    page.insert_text((443, 211), "1 Plaza", fontsize=10)
    page.insert_text((443, 226), "$8,898.00", fontsize=12)
    page.insert_text((443, 250), "Estructura color especial", fontsize=10)
    page.insert_text((443, 265), "$9,200.00", fontsize=12)

    # ROOT publica el SKU solo en el encabezado y diez acabados en el panel.
    # Es un contrato explicito de esa pagina, no un arrastre entre paginas.
    page = document.new_page(width=612, height=792)
    page.insert_text((36, 55), "ROOT", fontsize=32)
    page.insert_text((36, 75), "MESAS", fontsize=9)
    page.insert_text((36, 95), "160-O5535 - Mesa Root", fontsize=12)
    page.insert_text((422, 185), "RESINA FENOLICA NG/GR", fontsize=8)
    page.insert_text((422, 199), "$4,348.00", fontsize=10)
    page.insert_text((422, 210), "Colores especiales", fontsize=8)
    page.insert_text((422, 223), "$4,584.00", fontsize=10)
    page.insert_text((215, 748), "160-05535", fontsize=9)

    page = document.new_page(width=612, height=792)
    page.insert_text((36, 55), "ACCESORIOS", fontsize=32)
    page.insert_text((179, 135), "RA-88", fontsize=10)
    page.insert_text((179, 160), "Modificacion", fontsize=10)
    page.insert_text((230, 160), "$169.00", fontsize=10)

    page = document.new_page(width=612, height=792)
    page.insert_text((36, 55), "ARIA", fontsize=32)
    page.insert_text((36, 75), "SILLAS Y BANCOS DE TRABAJO", fontsize=9)
    authoritative = ("RP-1405/NG", "RE-1455/NG", "RP-1406/GC", "RE-1456/NG")
    corrupt_panel = ("RP-1405/NG", "RP-1455/NG", "RE-1406/GC", "RP-1456/NG")
    prices = (11408, 8648, 14371, 10250)
    for index, code in enumerate(authoritative):
        page.insert_text((36, 95 + index * 18), code, fontsize=12)
    for index, (code, price) in enumerate(zip(corrupt_panel, prices, strict=True)):
        page.insert_text((443, 196 + index * 45), code, fontsize=12)
        page.insert_text((443, 211 + index * 45), f"${price:,.2f}", fontsize=12)

    document.save(path)
    document.close()


@pytest.fixture
def requiez_files(tmp_path, monkeypatch):
    path = tmp_path / "Lista de precios A-26.pdf"
    _fixture_pdf(path)
    source = _source(path)
    monkeypatch.setattr(_module(), "SUPPORTED_SHA256", source.sha256)
    return (source,)


def test_parsea_solo_skus_anclados_al_precio_sin_fuzzy_ni_arrastre(requiez_files):
    rows = _module().parse_requiez_rows(requiez_files)

    assert {(row["code"], row["price_net"]) for row in rows} == {
        ("RP-1400/GC", Decimal("11345")),
        ("RP-1400/NG", Decimal("11345")),
        ("RM-40/D40", Decimal("1838")),
        ("RM-40/D40", Decimal("2106")),
        ("STE-01", Decimal("8898")),
        ("STE-01", Decimal("9200")),
        ("160-05535", Decimal("4348")),
        ("160-05535", Decimal("4584")),
        ("RA-88", Decimal("169")),
        ("RP-1405/NG", Decimal("11408")),
        ("RE-1455/NG", Decimal("8648")),
        ("RP-1406/GC", Decimal("14371")),
        ("RE-1456/NG", Decimal("10250")),
    }
    assert all(row["currency"] == "MXN" for row in rows)
    assert all(row["page"] in {1, 2, 3, 4, 5, 6} for row in rows)
    assert not {"RP-1455/NG", "RE-1406/GC", "RP-1456/NG"} & {
        row["code"] for row in rows if row["page"] == 6
    }
    assert all(row["price_bbox"][2] > row["price_bbox"][0] for row in rows)


def test_etiqueta_conserva_acabado_del_mismo_span_y_encabezado_compartido():
    module = _module()
    price_left = module._PriceOccurrence(
        Decimal("1882"), (443.0, 211.0, 492.0, 226.0), "$1,882.00"
    )
    price_right = module._PriceOccurrence(
        Decimal("1693"), (500.0, 211.0, 549.0, 226.0), "$1,693.00"
    )
    shared_spans = (
        module._Span("NG/GR", (443.0, 178.0, 485.0, 190.0), 9),
        module._Span("RE-614", (443.0, 197.0, 486.0, 212.0), 9),
        module._Span("RE-615", (500.0, 197.0, 539.0, 212.0), 9),
        module._Span("$1,882.00", price_left.bbox, 9),
        module._Span("$1,693.00", price_right.bbox, 9),
    )

    assert module._price_label(
        price_left, (price_left, price_right), shared_spans, "Visitantes y colectividad"
    ) == "NG/GR"
    assert module._price_label(
        price_right, (price_left, price_right), shared_spans, "Visitantes y colectividad"
    ) == "NG/GR"

    own_price = module._PriceOccurrence(
        Decimal("2701"), (443.0, 189.0, 491.0, 204.0), "$2,701.00"
    )
    own_spans = (
        module._Span("RE-570 Cromo", (443.0, 175.0, 524.0, 190.0), 9),
        module._Span("$2,701.00", own_price.bbox, 9),
    )
    assert module._price_label(
        own_price, (own_price,), own_spans, "Visitantes y colectividad"
    ) == "Cromo"

    accessory_price = module._PriceOccurrence(
        Decimal("563"), (178.5, 159.6, 216.7, 174.6), "$563.00"
    )
    accessory_spans = (
        module._Span("RA-23", (178.0, 134.7, 206.4, 147.2), 9),
        module._Span("$563.00", accessory_price.bbox, 9),
        module._Span("Kit de base de tapiz", (177.7, 172.6, 270.0, 182.6), 9),
        module._Span("asiento Rewind.", (177.7, 182.6, 260.0, 192.6), 9),
        module._Span("Modelos compatibles", (177.7, 202.7, 270.0, 212.7), 9),
    )
    assert module._price_label(
        accessory_price, (accessory_price,), accessory_spans, "Accesorios"
    ) == "Kit de base de tapiz asiento Rewind."

    standard_price = module._PriceOccurrence(
        Decimal("16121"), (443.4, 189.8, 488.6, 204.8), "$16,121.00"
    )
    standard_spans = (
        module._Span("RE-810", (443.4, 175.4, 483.4, 190.4), 9),
        module._Span("$16,121.00", standard_price.bbox, 9),
        module._Span("Tapiz personalizable", (259.8, 175.3, 360.0, 185.3), 9),
        module._Span("*Consulta muestrario", (259.8, 185.3, 370.0, 195.3), 9),
    )
    assert module._price_label(
        standard_price, (standard_price,), standard_spans, "Sillones"
    ) == "Tapiz personalizable · Consulta muestrario"


def test_descripcion_superior_elige_solo_el_renglon_mas_cercano():
    module = _module()
    spans = (
        module._Span("RE-780", (36.0, 83.874, 89.886, 103.874), 12),
        module._Span(
            "- Silla Visitante Respaldo y Asiento Tapizado en Tela.",
            (114.572, 84.374, 452.351, 101.874),
            10,
        ),
        module._Span("RE-780/P", (36.0, 100.866, 107.55, 120.866), 12),
        module._Span(
            "- Silla Visitante Respaldo y Asiento Tapizado en Piel.",
            (114.572, 102.378, 449.596, 119.878),
            10,
        ),
        module._Span("RE-781", (36.0, 117.858, 86.11, 137.858), 12),
        module._Span(
            "- Silla Visitante 4 Puntos Respaldo y Asiento Tapizado en Tela.",
            (114.572, 120.382, 512.173, 137.882),
            10,
        ),
    )
    descriptions = module._descriptions(spans, module._codes(spans))

    assert descriptions == {
        "RE-780": "Silla Visitante Respaldo y Asiento Tapizado en Tela.",
        "RE-780/P": "Silla Visitante Respaldo y Asiento Tapizado en Piel.",
        "RE-781": "Silla Visitante 4 Puntos Respaldo y Asiento Tapizado en Tela.",
    }


def test_codigo_del_panel_usa_grafia_publicada_equivalente_del_encabezado():
    module = _module()
    codes = (
        module._CodeOccurrence("RE-570", "RE-570", (36.0, 84.0, 90.0, 104.0)),
        module._CodeOccurrence("RE-570R", "RE-570R", (36.0, 103.0, 100.0, 123.0)),
        module._CodeOccurrence(
            "RE-570/R", "RE-570/R Cromo", (443.0, 259.0, 537.0, 274.0)
        ),
    )

    authoritative = module._authoritative_published_code("RE-570/R", codes)

    assert authoritative is not None
    assert authoritative.code == "RE-570R"
    assert authoritative.bbox[0] < 160


def test_imagen_exacta_prioriza_raster_encima_de_su_rotulo():
    module = _module()
    correct = (368.117, 348.754, 489.13, 521.302)
    following = (431.622, 535.977, 567.449, 742.25)

    class Page:
        @staticmethod
        def get_image_info(xrefs=True):
            assert xrefs is True
            return (
                {"xref": 10, "bbox": correct},
                {"xref": 11, "bbox": following},
            )

    codes = (
        module._CodeOccurrence(
            "RE-1755N/NG",
            "RE-1755N/NG",
            (434.839, 524.096, 490.464, 535.346),
        ),
    )

    match = module._image_match(Page(), 30, "RE-1755N/NG", codes, "a" * 64)

    assert match is not None
    assert match.bbox == correct


def test_imagen_exacta_prioriza_rotulo_superpuesto_sobre_mencion_lejana():
    module = _module()
    correct = (280.847, 508.664, 417.114, 745.513)
    misleading = (361.38, 322.483, 486.849, 499.078)

    class Page:
        @staticmethod
        def get_image_info(xrefs=True):
            assert xrefs is True
            return (
                {"xref": 20, "bbox": correct},
                {"xref": 21, "bbox": misleading},
            )

    codes = (
        module._CodeOccurrence(
            "RP-8000", "RP-8000", (367.443, 743.119, 404.818, 754.369)
        ),
        module._CodeOccurrence(
            "RP-8000",
            "RP-8000, RP-8001 Y RP-8005 EN",
            (450.64, 522.439, 561.793, 531.189),
        ),
    )

    match = module._image_match(Page(), 28, "RP-8000", codes, "a" * 64)

    assert match is not None
    assert match.bbox == correct


def test_snapshot_agrupa_opciones_y_publica_contrato_mxn_made_to_order(requiez_files):
    module = _module()
    first = module.build_requiez_snapshot_with_assets(
        requiez_files, synced_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    )
    second = module.build_requiez_snapshot_with_assets(
        requiez_files, synced_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    )

    assert first.snapshot == second.snapshot
    assert first.snapshot["supplier"] == "requiez"
    assert first.snapshot["generated_at"] == "2026-08-18T12:00:00Z"
    assert len(first.snapshot["items"]) == 10
    assert len({item["internal_id"] for item in first.snapshot["items"]}) == 10
    assert all(item["brand"] == "Requiez" for item in first.snapshot["items"])
    assert all(item["base_currency"] == "MXN" for item in first.snapshot["items"])
    assert all(item["tax_rate"] == "0.160000" for item in first.snapshot["items"])
    assert all(item["availability_type"] == "made_to_order" for item in first.snapshot["items"])
    assert all(item["unit"] == "PZA" for item in first.snapshot["items"])
    assert all(item["product_url"].endswith(f"#page={item['attributes']['source_page']}") for item in first.snapshot["items"])

    chap = next(item for item in first.snapshot["items"] if item["sku"] == "RM-40/D40")
    assert [option["price_net"] for option in chap["base_price_options"]] == [
        "1838.000000",
        "2106.000000",
    ]
    assert chap["price_net"] == "1838.000000"
    assert [option["name"] for option in chap["base_price_options"]] == [
        "CUBIERTA CIRCULAR · ESTRUCTURA NG",
        "CUBIERTA CIRCULAR · ESTRUCTURA BCO/ANT/DGY/MELAMINA",
    ]

    aria_gc = next(item for item in first.snapshot["items"] if item["sku"] == "RP-1400/GC")
    aria_ng = next(item for item in first.snapshot["items"] if item["sku"] == "RP-1400/NG")
    assert aria_gc["base_price_options"] == []
    assert aria_gc["price_net"] == "11345.000000"
    assert aria_gc["image_kind"] == "official"
    assert aria_ng["image_kind"] == "official"
    assert len(first.assets_by_sha256) == 1
    assert len(first.bindings) == 2
    matches = {binding.internal_id: binding for binding in first.bindings}
    assert matches[aria_gc["internal_id"]].match_status == "exact_pdf"
    assert matches[aria_ng["internal_id"]].match_status == "family_pdf"
    assert matches[aria_gc["internal_id"]].source_references[0]["sheet_or_page"] == 1

    root = next(item for item in first.snapshot["items"] if item["sku"] == "160-05535")
    assert root["code_status"] == "verified"
    assert len(root["base_price_options"]) == 2
    assert [option["name"] for option in root["base_price_options"]] == [
        "RESINA FENOLICA NG/GR", "RESINA FENOLICA · Colores especiales"
    ]
    assert any("pagina 4" in warning.casefold() for warning in root["warnings"])
    stella = next(item for item in first.snapshot["items"] if item["sku"] == "STE-01")
    assert [option["price_net"] for option in stella["base_price_options"]] == [
        "8898.000000", "9200.000000"
    ]


def test_builder_ligero_no_carga_activos_binarios(requiez_files):
    snapshot = _module().build_requiez_snapshot(
        requiez_files, synced_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    )

    assert snapshot["supplier"] == "requiez"
    assert all(item["image_kind"] == "placeholder" for item in snapshot["items"])

    with pytest.raises(ValueError, match="REQUIEZ_SYNCED_AT"):
        _module().build_requiez_snapshot(
            requiez_files, synced_at=datetime(2026, 8, 18, 12)
        )


def test_rechaza_hash_o_contrato_de_fuente_no_revisado(requiez_files):
    document = requiez_files[0]
    bad_hash = SourceDocument(
        document.path,
        document.kind,
        document.brand,
        "0" * 64,
        document.mime_type,
        document.local_path,
    )
    with pytest.raises(ValueError, match="REQUIEZ_HASH"):
        _module().parse_requiez_rows((bad_hash,))


@pytest.mark.skipif(not OFFICIAL_FALLBACK.is_file(), reason="PDF oficial no descargado")
def test_pdf_oficial_a26_conserva_version_y_cobertura_basica():
    module = _module()
    assert hashlib.sha256(OFFICIAL_FALLBACK.read_bytes()).hexdigest() == OFFICIAL_SHA256
    source = _source(OFFICIAL_FALLBACK)
    rows = module.parse_requiez_rows((source,))
    codes = {row["code"] for row in rows}

    assert len(rows) == 367
    assert {
        "RP-1400/GC", "RE-1450/NG", "RS-200NG/10", "RE-1074M", "RA-01",
        "STE-01", "STE-06", "160-05535",
    } <= codes
    prices = {(row["code"], row["page"]): row["price_net"] for row in rows}
    assert prices[("RE-1450/GC", 3)] == 7995
    assert prices[("RP-1400/GC", 3)] == 11345
    root = [row for row in rows if row["code"] == "160-05535" and row["page"] == 108]
    assert len(root) == 10
    assert sorted(row["price_net"] for row in rows if row["code"] == "RA-88") == [169, 277]
    assert {
        (row["code"], row["price_net"])
        for row in rows if row["page"] == 7
    } == {
        ("RP-1405/NG", Decimal("11408")),
        ("RE-1455/NG", Decimal("8648")),
        ("RP-1406/GC", Decimal("14371")),
        ("RE-1456/NG", Decimal("10250")),
    }
    assert {row["code"] for row in rows if row["page"] == 65} >= {
        "RE-570",
        "RE-570R",
        "RA-23",
    }
    assert not any(
        row["code"] == "RE-570/R" and row["page"] == 65 for row in rows
    )
    accessory_labels = {
        row["code"]: row["option_label"]
        for row in rows
        if row["page"] == 113
    }
    assert accessory_labels == {
        "RA-95": "Cabecera en mesh.",
        "RA-23": "Kit de base de tapiz asiento Rewind.",
        "RA-27": "Kit de base de tapiz asiento Outline.",
        "RA-28": "Kit de base de tapiz asiento Antonella.",
        "RA-29": "Kit de base de tapiz asiento Nico.",
        "RA-24": "Base aluminio 24” elaborada en aluminio pulido.",
        "RA-25": "Base de aluminio pulido largo de Nylon.",
        "RA-26": "Base aluminio 26” Elaborada en aluminio pulido.",
    }
    accessory_rows = [row for row in rows if 110 <= row["page"] <= 113]
    assert {
        (row["page"], row["code"], row["price_net"]): row["option_label"]
        for row in accessory_rows
    } == {
        (page, code, Decimal(price)): label
        for (page, code, price), label in EXPECTED_ACCESSORY_LABELS.items()
    }
    assert all(
        row["description"].startswith(EXPECTED_ACCESSORY_DESCRIPTORS[row["code"]])
        and "Modelos compatibles" in row["description"]
        and row["description"] != f"Accesorios {row['code']}"
        for row in accessory_rows
    )
    for code, expected_description in EXACT_ACCESSORY_DESCRIPTIONS.items():
        assert {
            row["description"]
            for row in accessory_rows
            if row["code"] == code
        } == {expected_description}
    assert [
        (row["option_label"], row["price_net"])
        for row in accessory_rows
        if row["code"] == "RA-88"
    ] == [
        ("Modificación", Decimal("169")),
        ("Accesorio", Decimal("277")),
    ]
    expected_sequence_pages = {
        10: {("RM-9100/NG", 21709), ("RM-9100/GR", 22198), ("RM-9105/NG", 9466)},
        13: {("RP-5051/AL", 18700), ("RP-5051/NL", 16593), ("RE-5061/AL", 15630), ("RE-5061/NL", 13524)},
        37: {("RE-1600/NG", 10681), ("RE-1600/BL", 12872)},
        43: {("RS-680GR/40", 5524), ("RS-680GR/45", 5602)},
        44: {("RS-680N/NG", 4451), ("RS-680N/40", 5218), ("RS-680N/45", 5195)},
        85: {("RS-151/R/N/NG", 6795), ("RS-151/R/G/GR", 6795), ("RS-161/R/NG", 4711), ("RS-161/R/GR", 4711)},
        86: {("RS-152/N/NG", 6655), ("RS-152/G/GR", 6655), ("RS-152/NG", 3880), ("RS-152/GR", 3880)},
    }
    for page, expected in expected_sequence_pages.items():
        page_rows = [row for row in rows if row["page"] == page]
        assert {(row["code"], row["price_net"]) for row in page_rows} == expected
        assert all(row["code_bbox"][0] < 160 for row in page_rows)
    snapshot = module.build_requiez_snapshot(
        (source,), synced_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
    )
    normalized = load_supplier_catalog_data(snapshot, expected_supplier="requiez")
    assert len(normalized["items"]) == 314
    assert all(
        len({option["name"] for option in item["base_price_options"]})
        == len(item["base_price_options"])
        for item in normalized["items"]
    )
    assert all(row["page"] in range(3, 114) for row in rows)
    assert not any(
        option["name"].startswith("Evidencia pagina")
        for item in normalized["items"]
        for option in item["base_price_options"]
    )
    by_sku = {item["sku"]: item for item in normalized["items"]}
    for code in ("RA-23", "RA-25", "RA-27", "RA-29"):
        item = by_sku[code]
        assert item["base_price_options"] == []
        assert len(item["attributes"]["prices"]) >= 2
        assert len(json.loads(item["source_reference"])) >= 2
        assert item["description"].startswith(EXPECTED_ACCESSORY_DESCRIPTORS[code])
    assert [
        (option["name"], option["price_net"])
        for option in by_sku["RA-88"]["base_price_options"]
    ] == [
        ("Modificación", "169.000000"),
        ("Accesorio", "277.000000"),
    ]
    assert [
        option["name"] for option in by_sku["RE-810"]["base_price_options"]
    ] == [
        "Tapiz personalizable · Consulta muestrario",
        "TELA GRADO A APOLO, MILÁN Y FRED",
    ]
