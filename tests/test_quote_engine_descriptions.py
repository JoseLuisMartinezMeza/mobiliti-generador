from pathlib import Path
import sys

import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine import build_product_description, generate_quote  # noqa: E402


DOWNLOADS = Path(r"C:\Users\pepem\Downloads")
TEMPLATE_DIR = ROOT / "versiones historial" / "HISTORIAL DE VERSIONES" / "Mobiliti_Generador_Windows"
TEMPLATE = next(TEMPLATE_DIR.glob("Formato*.xlsx"), TEMPLATE_DIR / "Formato Cotizacion 2026 GDL (1).xlsx")


def test_build_product_description_in_spanish_is_natural_summary():
    text = build_product_description(
        "CLG65SW Locke Task Chair",
        "Material Description: 1)Finishing: high-quality mesh, dry rubbing fastness >= 4. "
        "2)Chair Back: nylon + 30% fiberglass integrated back frame "
        "3)Seat Cushion: molded foam, density >= 55kg/m3.",
        "Silla",
        "es",
    )

    assert text.startswith("Silla modelo Locke Task Chair.")
    assert "malla de alta calidad" in text
    assert "respaldo" in text
    assert "Material Description" not in text


def test_description_title_keeps_model_when_code_is_glued_to_name():
    text = build_product_description(
        "CLG65SWLocke Task Chair",
        "Finishing: high-quality mesh.",
        "Silla",
        "es",
    )

    assert text.startswith("Silla modelo Locke Task Chair.")


def test_description_title_does_not_strip_capacity_as_code():
    text = build_product_description(
        "8PAX Conference Table",
        "Long conference table.",
        "Mesas de Juntas",
        "es",
    )

    assert text.startswith("Mesa de juntas modelo 8PAX Conference Table.")


def test_spanish_description_removes_common_english_fragments():
    text = build_product_description(
        "CHT85SW H2 Task Chair",
        "Finishing: backrest uses mesh, dry rubbing fastness >=4, abrasion resistance >=6000 cycles. "
        "Seat cushion uses high-quality linen velvet, cut foam, density 25kg/m3. "
        "Chair Frame: steel frame, electrostatic powder coating. Leg nails: PP fixed nails.",
        "Silla",
        "es",
    )

    assert text.startswith("Silla modelo H2 Task Chair.")
    assert "Acabado: respaldo en malla" in text
    assert "cojin del asiento con terciopelo de lino de alta calidad" in text.lower()
    assert "espuma cortada" in text
    assert "ciclos" in text
    for fragment in ["Finishing", "backrest uses", "uses high-quality", "cut foam", " cycles"]:
        assert fragment not in text


def test_spanish_description_translates_file_cabinet_terms():
    text = build_product_description(
        "E904-2.200040 File Cabinet",
        "Material Description: four-door file cabinet with display area (shelves). "
        "Cabinet: E0 grade MDF/particle board, natural wood veneer edge banding. "
        "Veneer: natural wood veneer >=0.4mm on wood grain parts. "
        "Hinges: concealed hinge. Lock: double-door lock.",
        "Archiveros Moviles y Fijos",
        "es",
    )

    assert text.startswith("Archivero movil o fijo modelo File Cabinet.")
    lower = text.lower()
    assert "archivero de cuatro puertas con area de exhibicion" in lower
    assert "tablero MDF/aglomerado grado E0" in text
    assert "chapa de madera natural" in lower
    for fragment in [" with ", "E0 grade", "natural wood veneer", "concealed hinge", "double-door lock"]:
        assert fragment not in text
    for fragment in ["Hinges", "Lock"]:
        assert fragment not in text


def test_spanish_description_translates_workstation_terms():
    text = build_product_description(
        "Lido Estacion 4 pax",
        "Straight employee desk, steel legs on both sides. "
        "Tabletop: 25mm, surface uses impregnated melamine paper for excellent stability, superior layering, "
        "strong resistance to dirt and wear. E0 grade particle board. "
        "Edge banding: the tabletop uses PVC edge banding strips, thickness 2.5mm. "
        "Beam: high-quality cold-rolled steel, with an electrostatic powder coating on the surface.",
        "Escritorios-WorkStation",
        "es",
    )

    assert text.startswith("Escritorio workstation modelo Lido Estacion 4 pax.")
    lower = text.lower()
    assert "estacion de trabajo recta" in lower
    assert "patas de acero en ambos lados" in text
    assert "papel melaminico impregnado" in text
    assert "tablero aglomerado grado E0" in text
    assert "cantos de PVC" in text
    for fragment in ["Straight employee", "steel legs", "impregnated melamine", "E0 grade", "edge banding"]:
        assert fragment not in text
    assert "Beam" not in text


def test_spanish_description_translates_frel_workstation_terms():
    text = build_product_description(
        "Escirotio 7 pax",
        "Straight Desk for Staff. Tabletop: 25mm, surface uses impregnated melamine paper. "
        "Pull Rod: made of high-quality cold-rolled steel, with an electrostatic powder coating on the surface.",
        "Escritorios-WorkStation",
        "es",
    )

    lower = text.lower()
    assert "escritorio recto para personal" in lower
    assert "tirante" in lower
    assert "fabricado en acero laminado en frio de alta calidad" in lower
    for fragment in ["Straight Desk", "Staff", "Pull Rod", "made of"]:
        assert fragment not in text


def test_spanish_description_translates_frel_chair_and_sofa_terms():
    chair_text = build_product_description(
        "CSD61TW Staff Chair",
        "Finishing: high-quality linen fabric, dry rubbing color fastness grade 3-4. "
        "Armrests: PP height-adjustable armrest.",
        "Silla",
        "es",
    )
    sofa_text = build_product_description(
        "SH31.2.MR Flower 6 Lounge Modular Seating",
        "Finishing: three-proof fabric, dry rubbing color fastness grade 3-4, oil repellency >= grade 4. "
        "Foam: high resilience foam, seat density >=31kg/m3, other parts >=20kg/m3. "
        "Sofa Legs: steel Sofa Legs, electrostatic powder coating, 2.0mm hollow round tube.",
        "Sillones",
        "es",
    )

    assert "tela de lino de alta calidad" in chair_text.lower()
    assert "descansabrazos de PP ajustable en altura" in chair_text
    assert "tela repelente" in sofa_text.lower()
    assert "grado 4" in sofa_text.lower()
    assert "espuma:" in sofa_text.lower()
    assert "espuma de alta resiliencia" in sofa_text.lower()
    assert "otras partes" in sofa_text.lower()
    assert "patas de sofa de acero" in sofa_text.lower()
    assert "tubo redondo hueco" in sofa_text.lower()
    for text in [chair_text, sofa_text]:
        for fragment in ["high-quality linen fabric", "height-adjustable armrest", "three-proof fabric", "high resilience foam", "Sofa Legs", "Foam", "grade"]:
            assert fragment not in text


def test_spanish_description_translates_frel_meeting_and_storage_terms():
    meeting_text = build_product_description(
        "Sala de juntas 14 pax",
        "Square conference table. Table Legs: door-shaped steel legs, high-quality cold-rolled steel, "
        "surface electrostatic powder coating.",
        "Mesas de Juntas",
        "es",
    )
    storage_text = build_product_description(
        "DG64-2 Storage Cabinets",
        "four-door file cabinet with glass doors. Door Panel: glass panel door with embedded glass, "
        "made of E0 grade particle board, 5mm tempered glass. Hinges: damping buffer hinge. Handles: ABS.",
        "Librero - Locker - Gabinete",
        "es",
    )

    assert "mesa de juntas cuadrada" in meeting_text.lower()
    assert "patas de acero tipo marco" in meeting_text.lower()
    assert "superficie con recubrimiento electrostatico en polvo" in meeting_text.lower()
    assert "archivero de cuatro puertas con puertas de vidrio" in storage_text.lower()
    assert "puerta de panel de vidrio con vidrio embebido" in storage_text.lower()
    assert "vidrio templado" in storage_text.lower()
    assert "bisagra con cierre amortiguado" in storage_text.lower()
    assert "jaladeras" in storage_text.lower()
    for text in [meeting_text, storage_text]:
        for fragment in ["Square conference", "door-shaped steel", "glass doors", "Door Panel", "embedded glass", "tempered glass", "damping buffer hinge", "Handles"]:
            assert fragment not in text


def test_spanish_description_translates_frel_remaining_table_terms():
    side_table = build_product_description(
        "T207 Personal Table",
        "Steel edge side table with handle. Table Legs: T-shaped steel legs, electrostatic powder coating surface, 5mm. "
        "Paint: with water-based environmentally friendly paint.",
        "Mesas de Apoyo",
        "es",
    )
    coffee_table = build_product_description(
        "DT202 Wire base Occasional Table",
        "Irregular coffee table. Edge banding: the tabletop uses PVC edge strips, thickness 1mm. "
        "Support Frame: Q195 steel, electrostatic powder coating, 10mm diameter solid round bar.",
        "Mesas de Apoyo",
        "es",
    )

    assert "mesa auxiliar con borde de acero y jaladera" in side_table.lower()
    assert "patas de acero tipo t" in side_table.lower()
    assert "pintura ecologica base agua" in side_table.lower()
    assert "mesa de cafe irregular" in coffee_table.lower()
    assert "cantos de PVC" in coffee_table
    assert "acero Q195" in coffee_table
    assert "barra redonda solida" in coffee_table.lower()
    for text in [side_table, coffee_table]:
        for fragment in ["Steel edge", "side table", "handle", "T-shaped", "Paint", "water-based", "Irregular coffee", "Q195 steel", "solid round bar", "edge strips"]:
            assert fragment not in text


def test_spanish_description_translates_frel_remaining_lounge_and_chair_terms():
    lounge_text = build_product_description(
        "SF30.1.MR.G Flower 0 Lounge Chair",
        "Finishing: leather and fabric combination, main areas use linen velvet fabric, "
        "bottom part with ecological polyurethane (EPU) synthetic leather. "
        "Foam: molded, density >=50kg/m3, with built-in welded steel frame. "
        "Sofa Legs: aluminum alloy Sofa Legs, electrostatic powder coating.",
        "Sillones",
        "es",
    )
    chair_text = build_product_description(
        "CMJ14GH MJ Multipurpose",
        "Fabric Combination: Inner seat with premium linen, dry rub fastness >=4. "
        "Outer back with PVC leather, fold endurance >=30000 cycles, no cracks. "
        "Chair Body: Cut cotton, E1 grade bentwood seat, supports 102kg. "
        "Chair Frame: steel frame, includes a 14mm diameter reinforced round steel, powder-coated. "
        "Casters: PA casters.",
        "Silla",
        "es",
    )

    lower_lounge = lounge_text.lower()
    lower_chair = chair_text.lower()
    assert "combinacion de piel y tela" in lower_lounge
    assert "zonas principales con tela de lino aterciopelada" in lower_lounge
    assert "parte inferior con piel sintetica de poliuretano ecologico" in lower_lounge
    assert "espuma moldeada" in lower_lounge
    assert "estructura de acero soldada integrada" in lower_lounge
    assert "patas de sofa de aleacion de aluminio" in lower_lounge
    assert "combinacion de telas" in lower_chair
    assert "asiento interior con lino premium" in lower_chair
    assert "respaldo exterior con piel pvc" in lower_chair
    assert "sin grietas" in lower_chair
    assert "algodon cortado" in lower_chair
    assert "soporta 102kg" in lower_chair
    assert "rodajas de pa" in lower_chair
    for text in [lounge_text, chair_text]:
        for fragment in ["leather", "bottom part", "built-in", "aluminum alloy", "Fabric Combination", "Inner seat", "Outer back", "dry rub", "fold endurance", "no cracks", "Cut cotton", "supports", "includes", "reinforced round steel", "powder-coated", "PA casters"]:
            assert fragment not in text


def test_spanish_description_translates_frel_last_residual_terms():
    lounge_text = build_product_description(
        "SD34.1.MR D2 Lounge Seating",
        "Finishing: linen fabric, dry rubbing color fastness grade 3-4. "
        "Sofa Legs: MDF, eco-friendly paint finish.",
        "Sillones",
        "es",
    )
    mixed_text = build_product_description(
        "SF30.1.MR.G Flower 0 Lounge Chair",
        "Finishing: bottom part uses ecological polyurethane (EPU) synthetic leather, "
        "linen fabric dry rubbing color fastness grade 3-4.",
        "Sillones",
        "es",
    )
    chair_text = build_product_description(
        "CMJ14GH MJ Multipurpose",
        "Chair Body: E1 grade bentwood seat, supports 102kg. "
        "Chair Frame: includes a 463mm long reinforced round steel.",
        "Silla",
        "es",
    )
    table_text = build_product_description(
        "DT202 Wire base Occasional Table",
        "Edge banding: the tabletop uses PVC edge banding strios, thickness 1mm.",
        "Mesas de Apoyo",
        "es",
    )
    storage_text = build_product_description(
        "DG64-2 Storage Cabinets",
        "Cabinet: the main body surface covered with impregnated melamine paper.",
        "Librero - Locker - Gabinete",
        "es",
    )

    joined = "\n".join([lounge_text, mixed_text, chair_text, table_text, storage_text])
    lower = joined.lower()
    assert "tela de lino" in lower
    assert "acabado ecologico de pintura" in lower
    assert "parte inferior con piel sintetica de poliuretano ecologico" in lower
    assert "asiento de madera curvada grado E1".lower() in lower
    assert "463 mm de largo" in lower
    assert "cantos de PVC" in joined
    assert "la superficie del cuerpo principal" in lower
    for fragment in ["linen fabric", "bottom part", "bentwood seat", " long ", "PVC edge strios", "PVC canto strios", "paint finish", "el superficie"]:
        assert fragment.lower() not in lower


def test_spanish_description_translates_board_and_power_terms():
    table_text = build_product_description(
        "EN79-2.600180 Varna Conference Table",
        "Long meeting table with round aluminum base. Tabletop: 26mm, "
        "the majority of the board is made of E0 grade MDF. "
        "Veneer: natural wood veneer >=0.4mm on wood grain parts.",
        "Mesas de Juntas",
        "es",
    )
    power_text = build_product_description(
        "DMC28.100 MixCube Power Bank",
        "Portable Power with Round plate Base. Cover Plate: 8mm thickness, "
        "surface material is impregnated film paper,E0 grade particleboard. "
        "Edge banding: Cover Plate adopt PVC edge banding strips.",
        "Multicontactos",
        "es",
    )

    assert "mesa de juntas alargada" in table_text.lower()
    assert "MDF grado E0" in table_text
    assert "modulo electrico portatil" in power_text.lower()
    assert "tablero aglomerado grado E0" in power_text
    for text in [table_text, power_text]:
        for fragment in ["E0 grade", "Portable Power", "Round plate", "particleboard"]:
            assert fragment not in text


def test_processed_description_removes_only_technical_audit_segments():
    text = build_product_description(
        "Mesa ALMA",
        (
            "Descripcion Mesa ALMA"
            " | Fuente: alma:e2e:configurable"
            " | Hash fuente: " + "a" * 64
            + " | Clave: ALMA-E2E"
            " | Base operativa; Electrificacion A"
            " | Entrega: Entrega inmediata"
            " | Revision documental local"
        ),
        "Mesas de Apoyo",
        "es",
    )

    assert "Base operativa" in text
    assert "Electrificacion A" in text
    assert "Entrega inmediata" in text
    assert "Revision documental local" in text
    assert "Fuente:" not in text
    assert "Hash fuente:" not in text
    assert "Clave:" not in text


def test_python_engine_writes_spanish_description_in_cotizacion(tmp_path):
    source = DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx"
    if not source.exists() or not TEMPLATE.exists():
        pytest.skip("Golden input/template not available on this machine")

    output = tmp_path / "iza_python_descriptions.xlsx"
    generate_quote(
        source,
        output,
        {
            "cotizacion": "GOLDEN",
            "proyecto": "Golden",
            "cliente": "Cliente",
            "description_language": "es",
            "tipo_cambio": 20,
        },
        TEMPLATE,
    )

    wb = load_workbook(output, data_only=False)
    assert wb["Cotizacion"]["C17"].value == "=Quotation!D9"
    description = wb["Quotation"]["D9"].value
    assert description.startswith("Silla modelo Locke Task Chair.")
    assert "malla de alta calidad" in description
    assert wb["Quotation"]["E9"].value != description
    wb.close()
