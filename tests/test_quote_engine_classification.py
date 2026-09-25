from pathlib import Path
import sys

import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine import (  # noqa: E402
    build_product_description,
    classify_product_name,
    generate_quote,
    load_category_dictionary,
)
from mobiliti_saas.quote_engine.catalog_cart import catalog_quotation_item_text  # noqa: E402


DOWNLOADS = Path(r"C:\Users\pepem\Downloads")
TEMPLATE_DIR = ROOT / "versiones historial" / "HISTORIAL DE VERSIONES" / "Mobiliti_Generador_Windows"
TEMPLATE = next(TEMPLATE_DIR.glob("Formato*.xlsx"), TEMPLATE_DIR / "Formato Cotizacion 2026 GDL (1).xlsx")


def test_batch_alias_learning_keeps_task_chair_as_silla():
    dictionary = load_category_dictionary(["CLG65SW Locke Task Chair"])

    assert classify_product_name("CLG65SW Locke Task Chair", dictionary) == "Silla"
    assert classify_product_name("CLG65SW Locke", dictionary) == "Silla"


def test_batch_alias_learning_does_not_mix_products_from_the_same_series():
    dictionary = load_category_dictionary(
        [
            "I-Varna II Conference Table",
            "DV88R-2.220154 I-Varna II Private Offices Desk",
        ]
    )

    assert (
        classify_product_name(
            "DV88R-2.220154 I-Varna II Private Offices Desk",
            dictionary,
        )
        == "Escritorios-WorkStation"
    )


def test_meeting_name_classifies_as_meeting_table_without_breaking_meeting_chair():
    dictionary = load_category_dictionary(["Lido meeting ch 6px", "CLG65SW Meeting Chair"])

    assert classify_product_name("Lido meeting ch 6px", dictionary) == "Mesas de Juntas"
    assert classify_product_name("CLG65SW Meeting Chair", dictionary) == "Silla"


def test_lounge_and_modular_names_classify_as_sillones():
    dictionary = load_category_dictionary([
        "SH31.2.MR Flower 6 Lounge Seating",
        "MR Tetris Modular Seating",
        "Modular meeting table",
    ])

    assert classify_product_name("SH31.2.MR Flower 6 Lounge Seating", dictionary) == "Sillones"
    assert classify_product_name("MR Tetris Modular Seating", dictionary) == "Sillones"
    assert classify_product_name("Modular meeting table", dictionary) == "Mesas de Juntas"


@pytest.mark.parametrize(
    (
        "product_name",
        "description",
        "source_category",
        "supplier",
        "expected",
    ),
    [
        pytest.param(
            "JUMP-1.5M",
            "Jumper de interconexion",
            "Interconexion",
            "Lumbro",
            "Multicontactos",
            id="lumbro-jumper",
        ),
        pytest.param(
            "CAJA-FUS",
            "Caja de fusibles para electrificacion",
            "Accesorios",
            "Lumbro",
            "Multicontactos",
            id="lumbro-fuse-box",
        ),
        pytest.param(
            "CAYENNE MERIDIENNE right t arm with rope",
            "CAYENNE MERIDIENNE right t arm with rope",
            "CAYENNE",
            "ALMA",
            "Sillones",
            id="alma-meridienne",
        ),
        pytest.param(
            "STRING Dining Side Chair Frame",
            "Dining side chair with aluminum frame",
            "STRING",
            "ALMA",
            "Silla",
            id="alma-chair",
        ),
        pytest.param(
            "PILLOW Coffee Table Top",
            "Coffee table with aluminum top",
            "PILLOW",
            "ALMA",
            "Mesas de Apoyo",
            id="alma-side-table",
        ),
        pytest.param(
            "A4 2P",
            "Sofa 2 plazas con brazos y respaldo bajo",
            "Sofas",
            "Lauco",
            "Sillones",
            id="lauco-sofa-from-description",
        ),
        pytest.param(
            "REPLAY ACCESORIOS",
            "Base de asiento y silla de polipropileno",
            "Sillas",
            "Labenze",
            "Silla",
            id="labenze-chair-from-collection",
        ),
        pytest.param(
            "TEO RE-1200",
            "Sillon gerencial con cabecera",
            "",
            "Requiez",
            "Silla",
            id="requiez-executive-chair-synonym",
        ),
        pytest.param(
            "Lido 7px PA",
            "Straight employee desk, steel legs on both sides",
            "PA - SISTEMAS",
            "Sunon",
            "Escritorios-WorkStation",
            id="sales-del-valle-description-context",
        ),
        pytest.param(
            "Welss Training system Fixed Height podium",
            "Fixed podium",
            "PA - SALON USOS MULTIPLES",
            "Sunon",
            "Terminados",
            id="sales-del-valle-podium-official-fallback",
        ),
        pytest.param(
            "MODELO VISITA",
            "Silla de visita tapizada",
            "PB - SALA DE JUNTAS 8PX",
            "Requiez",
            "Silla",
            id="description-outranks-room-section",
        ),
        pytest.param(
            "NICO RA-29",
            "Banco operativo con asiento tapizado",
            "Sillas y bancos de trabajo",
            "Requiez",
            "Bancos",
            id="description-disambiguates-mixed-collection",
        ),
    ],
)
def test_product_classification_uses_supplier_and_source_context(
    product_name,
    description,
    source_category,
    supplier,
    expected,
):
    dictionary = load_category_dictionary()

    assert (
        classify_product_name(
            product_name,
            dictionary,
            description=description,
            source_category=source_category,
            supplier=supplier,
        )
        == expected
    )


def test_native_catalog_collection_reaches_classification_but_stays_out_of_visible_copy():
    source_description, _, _ = catalog_quotation_item_text(
        {
            "code": "A4-PANEL",
            "name": "A4 PANEL",
            "description": "Panel divisorio tapizado",
            "collection": "Sofas",
            "quantity": 1,
            "unit": "PZA",
            "unit_price": "100",
        },
        index=1,
        source_type="supplier_cart",
    )
    dictionary = load_category_dictionary()

    category = classify_product_name(
        "A4 PANEL",
        dictionary,
        description=source_description,
        supplier="Lauco",
    )
    visible_description = build_product_description(
        "A4 PANEL",
        source_description,
        category,
        "es",
    )

    assert "Coleccion: Sofas" in source_description
    assert category == "Sillones"
    assert "Coleccion:" not in visible_description


def test_specific_product_term_wins_over_generic_finish_term_in_same_description():
    dictionary = load_category_dictionary()

    assert (
        classify_product_name(
            "A4 2P",
            dictionary,
            description=(
                "Sofa 2 plazas con brazos y respaldo bajo; "
                "patas con acabado pintura horneada | Coleccion: Sofas"
            ),
            supplier="Lauco",
        )
        == "Sillones"
    )


@pytest.mark.parametrize(
    ("product_name", "description", "supplier", "expected"),
    [
        pytest.param(
            "Rollo de 1 x 20mts de Aislante Ecomute",
            "Codigo de proveedor faltante. Verificar antes de cotizar.",
            "Tarkett",
            "Terminados",
            id="aislante-no-coincide-con-tiza-dentro-de-cotizar",
        ),
        pytest.param(
            "CAYENNE 2-seat left arm module with rope",
            "CAYENNE 2-seat left arm module with rope",
            "ALMA",
            "Sillones",
            id="alma-modulo-de-sofa-no-es-workstation",
        ),
        pytest.param(
            "Desso Grain B867 9506 B1 50x50",
            "Alfombra modular Desso",
            "Tarkett",
            "Terminados",
            id="desso-no-es-workstation",
        ),
        pytest.param(
            "Estacion Lido 8PAX Tarkett",
            "Estacion de trabajo para ocho usuarios",
            "Tarkett",
            "Escritorios-WorkStation",
            id="tarkett-no-fuerza-todo-a-terminados",
        ),
        pytest.param(
            "Cuerpo en 28mm frenes en 16mm",
            "Cuerpo en 28mm frenes en 16mm | Coleccion: cm alto Credenzas",
            "JOME",
            "Librero - Locker - Gabinete",
            id="jome-usa-el-sistema-credenzas",
        ),
        pytest.param(
            "CUB HIVE",
            "Paneles en bastidor de pino con cubierta de trabajo | Coleccion: Sofas",
            "Lauco",
            "Phonebooths",
            id="lauco-cub-hive-es-phonebooth",
        ),
        pytest.param(
            "F PIGRECO",
            "Una sola pieza de polipropileno, nylon y fibra de vidrio",
            "Offiho",
            "Bancos",
            id="offiho-pigreco",
        ),
        pytest.param(
            "JUN RE-1062",
            "Banca italiana de 2 plazas tapizada",
            "Requiez",
            "Bancos",
            id="requiez-banca",
        ),
        pytest.param(
            "Pintarron White star",
            "Pintarron blanco",
            "Lauco",
            "Pizarrones",
            id="pintarron",
        ),
        pytest.param(
            "DROP",
            "Mesa de polipropileno para exterior",
            "Lauco",
            "Mesas de Apoyo",
            id="lauco-drop-mesa",
        ),
        pytest.param(
            "Mesa de capacitacion",
            "Mesa plegable para capacitacion",
            "Lauco",
            "Mesas de Juntas",
            id="mesa-de-capacitacion",
        ),
        pytest.param(
            "ALUFSEN",
            "Asiento y respaldo con piston para silla operativa",
            "Offiho",
            "Silla",
            id="offiho-alufsen-es-silla",
        ),
        pytest.param(
            "VANTO",
            "Producto Offiho VANTO. Variante: NEGRO. Unidad: PZA.",
            "Offiho",
            "Silla",
            id="offiho-vanto-es-silla",
        ),
        pytest.param(
            "06",
            "Producto Offiho 06 | URL: https://www.offiho.com/econosillas/brazos-fijos-operativas/06e",
            "Offiho",
            "Silla",
            id="offiho-brazos-operativas",
        ),
        pytest.param(
            "RA-01",
            "Juego de brazos ajustables para asiento",
            "Requiez",
            "Silla",
            id="requiez-ra-01-accesorio-de-silla",
        ),
        pytest.param(
            "RA-23",
            "Kit de base de tapiz asiento Rewind",
            "Requiez",
            "Silla",
            id="requiez-ra-23-accesorio-de-silla",
        ),
        pytest.param(
            "Tequila base alta",
            "Base de mesa alta para cubierta circular",
            "Idelika",
            "Mesas de Apoyo",
            id="idelika-tequila-base-alta",
        ),
        pytest.param(
            "Estructura Elevable",
            "Estructura de altura ajustable electrica | Coleccion: Estructuras 1 Leg",
            "CR Global",
            "Escritorios-WorkStation",
            id="cr-global-estructura-elevable-escritorio",
        ),
        pytest.param(
            "Estructura Elevable",
            "Estructura plegable | Coleccion: Mesas de Capacitacion",
            "CR Global",
            "Mesas de Juntas",
            id="cr-global-estructura-mesa-capacitacion",
        ),
        pytest.param(
            "ROOT 160-05535",
            "Base ROOT para mesa | Coleccion: Mesas",
            "Requiez",
            "Mesas de Apoyo",
            id="requiez-root-es-mesa",
        ),
        pytest.param(
            "LOFT EXTENSION MODULE SINGLE",
            "Modulo individual de extension con resorte",
            "ALMA",
            "Sillones",
            id="alma-loft-es-modulo-de-sillon",
        ),
        pytest.param(
            "Base central combiada",
            "Base central combinada para mesa",
            "JOME",
            "Mesas de Apoyo",
            id="jome-base-central-combinada",
        ),
        pytest.param(
            "Base central corta",
            "Base central corta para mesa",
            "JOME",
            "Mesas de Apoyo",
            id="jome-base-central-corta",
        ),
    ],
)
def test_real_e2e_products_use_official_freight_concepts(
    product_name,
    description,
    supplier,
    expected,
):
    dictionary = load_category_dictionary([product_name])

    assert (
        classify_product_name(
            product_name,
            dictionary,
            description=description,
            supplier=supplier,
        )
        == expected
    )


def test_python_engine_writes_product_category_from_product_name(tmp_path):
    source = DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx"
    if not source.exists() or not TEMPLATE.exists():
        pytest.skip("Golden input/template not available on this machine")

    output = tmp_path / "iza_python_categories.xlsx"
    generate_quote(
        source,
        output,
        {"cotizacion": "GOLDEN", "proyecto": "Golden", "cliente": "Cliente"},
        TEMPLATE,
    )

    wb = load_workbook(output, data_only=False)
    mob = wb["Mobiliti"]
    assert mob["E14"].value == "Silla"
    assert mob["E49"].value == "Archiveros Moviles y Fijos"
    wb.close()
