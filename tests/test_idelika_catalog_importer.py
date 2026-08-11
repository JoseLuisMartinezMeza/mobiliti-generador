from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from mobiliti_saas.worker.catalog_sync.importers import (
    IdelikaEvidenceRow,
    extract_idelika_rows,
)
from mobiliti_saas.worker.catalog_sync.importers.common import PdfPage


@dataclass(frozen=True)
class TextDocument:
    """Fixture de texto equivalente a un PDF ya extraído, sin depender de red."""

    kind: str
    path: str
    source_url: str
    pages: tuple[PdfPage, ...]


def _document(kind: str, filename: str, *pages: str) -> TextDocument:
    return TextDocument(
        kind=kind,
        path=filename,
        source_url=f"https://sharepoint.example.test/idelika/{kind}",
        pages=tuple(
            PdfPage(page_number=index, text=text)
            for index, text in enumerate(pages, start=1)
        ),
    )


def _document_at_page(
    kind: str,
    filename: str,
    page_number: int,
    text: str,
) -> TextDocument:
    return TextDocument(
        kind=kind,
        path=filename,
        source_url=f"https://sharepoint.example.test/idelika/{kind}",
        pages=(PdfPage(page_number=page_number, text=text),),
    )


def _official_style_documents() -> tuple[TextDocument, ...]:
    return (
        _document(
            "fabricacion",
            "1 CATALOGO FABRICACION 2026B.pdf",
            """
            IDÉLIKA | CATÁLOGO FABRICACIÓN 2026B
            PÁGINA 1

            PRODUCTO: Escritorio Nube
            SKU: ID-FAB-100
            FAMILIA: Escritorios
            VARIANTE: Roble / 120 cm
            MATERIAL: Melamina y acero
            MEDIDAS: 120 x 60 x 75 cm
            DESCRIPCIÓN: Escritorio rectangular con estructura metálica.
            UNIDAD: pieza
            PRECIO: $3,999 - $3,499 MXN
            PEDIDO MÍNIMO: 2

            www.idelika.mx | ventas@idelika.mx
            """,
        ),
        _document(
            "stock",
            "2 CATALOGO STOCK 2026.pdf",
            """
            IDÉLIKA | CATÁLOGO STOCK 2026
            PÁGINA 1

            PRODUCTO: Silla Loop
            FAMILIA: Sillas
            VARIANTE: Gris
            MATERIAL: Polipropileno
            DESCRIPCIÓN: Silla apilable para uso interior.
            UNIDAD: pza
            PRECIOS: $1,850 / $1,250

            IDÉLIKA | CATÁLOGO STOCK 2026
            www.idelika.mx | PÁGINA 1
            """,
        ),
        _document(
            "school-series",
            "4 SCHOOL SERIES 2026.pdf",
            """
            IDÉLIKA SCHOOL SERIES 2026
            PÁGINA 1

            PRODUCTO: Pupitre Delta
            FAMILIA: School Series
            VARIANTE: Cubierta azul
            MATERIAL: Polipropileno y acero
            MEDIDAS: 60 x 45 x 75 cm
            DESCRIPCIÓN: Pupitre individual escolar.
            UNIDAD: pieza

            www.idelika.mx | School Series
            """,
        ),
    )


def test_fabricacion_y_stock_asignan_el_menor_como_costo_y_el_mayor_como_referencia():
    rows = extract_idelika_rows(_official_style_documents())

    fabricacion = next(row for row in rows if row.subcatalog == "Fabricacion")
    stock = next(row for row in rows if row.subcatalog == "Stock")

    assert fabricacion.cost_mxn == Decimal("3499")
    assert fabricacion.reference_price_mxn == Decimal("3999")
    assert fabricacion.original_price_text == "$3,999 - $3,499 MXN"
    assert fabricacion.price_status == "confirmado"
    assert stock.cost_mxn == Decimal("1250")
    assert stock.reference_price_mxn == Decimal("1850")
    assert stock.original_price_text == "$1,850 / $1,250"


def test_school_sin_precio_sigue_cotizable_y_no_fabrica_cero():
    rows = extract_idelika_rows(_official_style_documents())

    school = next(row for row in rows if row.subcatalog == "School Series")

    assert school.cost_mxn is None
    assert school.reference_price_mxn is None
    assert school.original_price_text is None
    assert school.price_status == "precio_por_confirmar"
    assert school.quotable is True


def test_conserva_sku_publicado_y_deja_sku_ausente_en_blanco():
    rows = extract_idelika_rows(_official_style_documents())

    fabricacion = next(row for row in rows if row.subcatalog == "Fabricacion")
    stock = next(row for row in rows if row.subcatalog == "Stock")

    assert fabricacion.sku == "ID-FAB-100"
    assert stock.sku is None
    assert stock.stable_key
    assert stock.identity_hash
    assert "invent" not in stock.stable_key.casefold()


def test_claves_hashes_orden_y_filas_son_deterministas():
    documents = _official_style_documents()

    first = extract_idelika_rows(documents)
    second = extract_idelika_rows(documents)

    assert first == second
    assert tuple(row.stable_key for row in first) == tuple(
        row.stable_key for row in second
    )
    assert tuple(row.identity_hash for row in first) == tuple(
        row.identity_hash for row in second
    )
    assert all(isinstance(row, IdelikaEvidenceRow) for row in first)


def test_ignora_encabezados_pies_y_conserva_archivo_url_y_pagina_uno_based():
    document = _document(
        "stock",
        "2 CATALOGO STOCK 2026.pdf",
        """
        IDÉLIKA | CATÁLOGO STOCK 2026
        www.idelika.mx | PÁGINA 1
        """,
        """
        IDÉLIKA | CATÁLOGO STOCK 2026
        PÁGINA 2

        PRODUCTO: Banco Tori
        SKU: ID-ST-220
        DESCRIPCIÓN: Banco alto con respaldo.
        UNIDAD: pieza
        PRECIO: $2,100 – $2,800

        ventas@idelika.mx | www.idelika.mx
        """,
    )

    rows = extract_idelika_rows((document,))

    assert len(rows) == 1
    assert rows[0].product == "Banco Tori"
    assert rows[0].source_file == "2 CATALOGO STOCK 2026.pdf"
    assert rows[0].source_page == 2
    assert rows[0].source_url == "https://sharepoint.example.test/idelika/stock"


def test_omite_fila_monetaria_ambigua_en_vez_de_adivinar():
    document = _document(
        "fabricacion",
        "1 CATALOGO FABRICACION 2026B.pdf",
        """
        PRODUCTO: Mesa Ambigua
        SKU: ID-AMB-1
        DESCRIPCIÓN: La página no vincula los tres importes con variantes.
        UNIDAD: pieza
        PRECIO: $3,100 / $3,500 / $4,200

        PRODUCTO: Mesa Clara
        SKU: ID-OK-2
        DESCRIPCIÓN: Mesa con pareja inequívoca.
        UNIDAD: pieza
        PRECIO: $2,500 / $3,000
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [row.sku for row in rows] == ["ID-OK-2"]


def test_no_fusiona_nombres_parecidos_sin_identidad_compartida_demostrada():
    document = _document(
        "stock",
        "2 CATALOGO STOCK 2026.pdf",
        """
        PRODUCTO: Silla Nova
        VARIANTE: Negra
        DESCRIPCIÓN: Silla visitante con cuatro patas.
        UNIDAD: pieza
        PRECIO: $1,000 / $1,300

        PRODUCTO: Silla Nova Plus
        VARIANTE: Negra
        DESCRIPCIÓN: Silla visitante con brazos.
        UNIDAD: pieza
        PRECIO: $1,200 / $1,500
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [row.product for row in rows] == ["Silla Nova", "Silla Nova Plus"]
    assert rows[0].stable_key != rows[1].stable_key
    assert rows[0].identity_hash != rows[1].identity_hash


def test_conserva_identidad_publicada_y_separa_variantes_explicitas():
    document = _document(
        "stock",
        "2 CATALOGO STOCK 2026.pdf",
        """
        PRODUCTO: Silla Aura
        SKU: ID-AURA-10
        FAMILIA: Aura
        VARIANTE: Negra
        DESCRIPCIÓN: Silla Aura, acabado negro.
        UNIDAD: pieza
        PRECIO: $1,100 / $1,400

        PRODUCTO: Silla Aura
        SKU: ID-AURA-10
        FAMILIA: Aura
        VARIANTE: Blanca
        DESCRIPCIÓN: Silla Aura, acabado blanco.
        UNIDAD: pieza
        PRECIO: $1,200 / $1,500
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [row.variant for row in rows] == ["Negra", "Blanca"]
    assert {row.sku for row in rows} == {"ID-AURA-10"}
    assert len({row.stable_key for row in rows}) == 2


def test_omite_multiples_pares_si_el_bloque_no_demuestra_variantes():
    document = _document(
        "fabricacion",
        "1 CATALOGO FABRICACION 2026B.pdf",
        """
        PRODUCTO: Mesa Sin Asociación
        SKU: ID-AMB-2
        DESCRIPCIÓN: Dos pares aparecen sin rótulo que los vincule a opciones.
        UNIDAD: pieza
        PRECIO: $2,000 / $2,500
        PRECIO: $3,000 / $3,500
        """,
    )

    assert extract_idelika_rows((document,)) == ()


def test_fabricacion_pagina_5_separa_productos_solo_por_evidencia_del_layout():
    document = _document_at_page(
        "fabricacion",
        "1 CATALOGO FABRICACION 2026B.pdf",
        5,
        """
        Mesa comedor Irune
        Silla Veracruz
        Fabricación
        120*120*75 alto
        Madera de teka
        $20,990 – $19,990 Mesa
        $7,990 – $5,990 Silla
        Tulum sofá cama
        Color a elegir
        Fabricación
        230*115*80
        Precios disponibles en www.idelika.com
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [(row.product, row.cost_mxn, row.reference_price_mxn) for row in rows] == [
        ("Mesa comedor Irune", Decimal("19990"), Decimal("20990")),
        ("Silla Veracruz", Decimal("5990"), Decimal("7990")),
        ("Tulum sofá cama", None, None),
    ]
    assert rows[-1].original_price_text == "Precios disponibles en www.idelika.com"
    assert all(row.source_page == 5 for row in rows)


def test_stock_pagina_13_no_arrastra_productos_sin_limite_demostrado():
    document = _document_at_page(
        "stock",
        "2 CATALOGO STOCK 2026.pdf",
        13,
        """
        Dafne mesa
        Madera de acacia
        Importación
        120*75
        $16,990 – $11,990
        Thianna silla
        Madera de acacia
        56*52*85 alto
        Importación
        $3,990 – $3,499
        Comedor Marieta c/ 6 sillas tequila
        Aluminio y cristal templado
        160*90*75 Negro
        $34,990 – $29,990
        Stearn perchero
        42*50*183 alto
        Acero con pintura electroestática
        Uso exterior techado
        $1,990 – $1,899
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [row.product for row in rows] == ["Dafne mesa", "Thianna silla"]
    assert [row.cost_mxn for row in rows] == [Decimal("11990"), Decimal("3499")]
    assert [row.dimensions for row in rows] == ["120*75", "56*52*85 alto"]
    assert all("Marieta" not in row.description for row in rows)
    assert all("Stearn" not in row.description for row in rows)


def test_fabricacion_pagina_51_rechaza_pie_y_conserva_precio_pendiente():
    document = _document_at_page(
        "fabricacion",
        "1 CATALOGO FABRICACION 2026B.pdf",
        51,
        """
        Zapopan Showroom &
        Warehouse
        Av. Justo Sierra 1028 Col.
        Agua Blanca Industrial
        45235 Zapopan.
        Playa del Carmen Showroom
        & Warehouse
        Carretera federal #10455,
        Plaza Recubre, Playa del
        Carmen, Q. Roo. MX CP77725
        Colchón memory foam
        Fabricación
        Precios disponibles en
        www.idelika.com
        """,
    )

    rows = extract_idelika_rows((document,))

    assert len(rows) == 1
    assert rows[0].product == "Colchón memory foam"
    assert rows[0].description == "Precios disponibles en"
    assert rows[0].original_price_text == "Precios disponibles en www.idelika.com"
    assert rows[0].price_status == "precio_por_confirmar"
    assert rows[0].source_page == 51


def test_school_pagina_12_no_trata_importacion_en_descripcion_como_producto():
    document = _document_at_page(
        "school-series",
        "4 SCHOOL SERIES 2026.pdf",
        12,
        """
        Legión set Pic-nic
        Importación
        Cubierta 120*60
        Medida total 120*160*82
        Fabricado en acero tubular
        Calibre 18 con pintura
        Electroestática y asiento
        En concha de polipropileno
        Cubierta de melamina
        Encapsulada apta para
        Exteriores.
        Pedido exclusivo de
        Importación.
        Consultar tiempos de
        Entrega.
        Producto exclusivo de importación mínimo 10 sets
        """,
    )

    rows = extract_idelika_rows((document,))

    assert len(rows) == 1
    assert rows[0].product == "Legión set Pic-nic"
    assert rows[0].minimum_order == Decimal("10")
    assert rows[0].price_status == "precio_por_confirmar"


def test_school_pagina_13_usa_solo_anclas_positivas_y_no_fusiona_bloques():
    document = _document_at_page(
        "school-series",
        "4 SCHOOL SERIES 2026.pdf",
        13,
        """
        Silla Jous primaria
        Importación
        Fabricado en acero tubular
        Calibre 18 con pintura
        Electroestática y asiento
        En concha de polipropileno
        Mesa architect
        Fabricación
        Medida: 64*48*71
        Fabricado en acero tubular
        Calibre 18 con pintura
        Electroestática y cubierta
        En laminado plástico
        Eco pupitre
        Fabricación
        Medida: 58*50*78
        Fabricado en acero tubular
        Calibre 18 con pintura
        Electroestática y cubierta
        En laminado plástico
        Concha de polipropileno
        Pintarrón interactivo
        Importación 177*122
        Metálico porcelanizado, interface USB compatible con windows 7 a 10
        Pintarrón White star
        Importación 240*120
        Material: Pintarrón blanco con borde de aluminio anodizado antióxido
        Pintarrón pedestal giratorio
        Importación 180*120
        Material: Pintarrón blanco con borde de aluminio anodizado antióxido
        Touch all in one 65” o 86”
        Importación 142*80 ó 190*106
        Tecnología touch, resolución 4k, compatible con diversos sistemas operativos
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [row.product for row in rows] == [
        "Silla Jous primaria",
        "Mesa architect",
        "Eco pupitre",
        "Pintarrón interactivo",
        "Pintarrón White star",
        "Pintarrón pedestal giratorio",
        "Touch all in one 65” o 86”",
    ]
    assert all(row.price_status == "precio_por_confirmar" for row in rows)
    assert len({row.stable_key for row in rows}) == len(rows)
    assert len({row.identity_hash for row in rows}) == len(rows)


def test_majahuitas_set_liga_cada_medida_a_su_precio_y_su_identidad():
    document = _document_at_page(
        "fabricacion",
        "1 CATALOGO FABRICACION 2026B.pdf",
        33,
        """
        Majahuitas set
        Fabricación
        Parota y cuerda
        66*58*70 sillón
        $9,990 – $7,990
        66*170*70 love
        $31,990 – $28,990
        66*200*70 sofá
        $39,990 – $34,990
        100*60*32 mesa
        $11,990 – $9,990
        Cojín curri incluido
        Colección
        Majahuitas
        """,
    )

    rows = extract_idelika_rows((document,))

    assert [row.product for row in rows] == ["Majahuitas set"] * 4
    assert [row.dimensions for row in rows] == [
        "66*58*70 sillón",
        "66*170*70 love",
        "66*200*70 sofá",
        "100*60*32 mesa",
    ]
    assert [row.cost_mxn for row in rows] == [
        Decimal("7990"),
        Decimal("28990"),
        Decimal("34990"),
        Decimal("9990"),
    ]
    assert len({row.stable_key for row in rows}) == 4
    assert len({row.identity_hash for row in rows}) == 4


@pytest.mark.parametrize(
    "price_text",
    (
        "Producto $3,000 + Flete $500",
        "$3,000 + IVA $480",
        "MXN $3,000 / USD $175",
    ),
)
def test_rechaza_par_de_importes_con_conceptos_o_monedas_distintas(price_text):
    document = _document(
        "stock",
        "2 CATALOGO STOCK 2026.pdf",
        f"""
        PRODUCTO: Silla con precio ambiguo
        UNIDAD: pieza
        PRECIO: {price_text}
        """,
    )

    assert extract_idelika_rows((document,)) == ()


@pytest.mark.parametrize(
    ("evidence", "expected_original"),
    (
        ("PRECIO: Por confirmar", "Por confirmar"),
        ("Consultar precio", "Consultar precio"),
        ("Precios disponibles en www.idelika.com", "Precios disponibles en www.idelika.com"),
    ),
)
def test_school_con_evidencia_textual_de_precio_pendiente_sigue_cotizable(
    evidence,
    expected_original,
):
    document = _document(
        "school-series",
        "4 SCHOOL SERIES 2026.pdf",
        f"""
        PRODUCTO: Pupitre pendiente
        DESCRIPCIÓN: Pupitre escolar.
        UNIDAD: pieza
        {evidence}
        """,
    )

    rows = extract_idelika_rows((document,))

    assert len(rows) == 1
    assert rows[0].cost_mxn is None
    assert rows[0].reference_price_mxn is None
    assert rows[0].original_price_text == expected_original
    assert rows[0].price_status == "precio_por_confirmar"
    assert rows[0].quotable is True
