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


def test_spanish_description_translates_zoomlion_storage_and_reception_terms():
    storage_text = build_product_description(
        "DG2B-2.040053 Mobile Pedestal Files B/F",
        "Two-drawer mobile cabinet. Cabinet: E0 grade particleboard. "
        "Casters: castor wheels, made of nylon. Slide Rail: high-quality slide rail.",
        "Archiveros Moviles y Fijos",
        "es",
    )
    reception_text = build_product_description(
        "DMA81-2.150100 Reception Desk",
        "Reception desk. Top Panel: paper impregnated thermosetting resins surface. "
        "Stand panel: impregnated melamine paper. Back panel: high quality PVC edge banding strips.",
        "Escritorios-WorkStation",
        "es",
    )

    assert "cajonera movil de dos cajones" in storage_text.lower()
    assert "ruedas giratorias" in storage_text.lower()
    assert "corredera de alta calidad" in storage_text.lower()
    assert "mostrador de recepcion" in reception_text.lower()
    assert "panel superior" in reception_text.lower()
    assert "panel de soporte" in reception_text.lower()
    assert "panel posterior" in reception_text.lower()
    translated_bodies = "\n".join(
        text.split("\n", 1)[1] for text in (storage_text, reception_text)
    )
    for fragment in [
        "Two-drawer",
        "mobile cabinet",
        "castor wheels",
        "Slide Rail",
        "Reception desk",
        "Top Panel",
        "Stand panel",
        "Back panel",
    ]:
        assert fragment.lower() not in translated_bodies.lower()


def test_spanish_description_translates_zoomlion_chair_and_plywood_terms():
    chair_text = build_product_description(
        "CCP63SQ-TP Coupe II Task chair",
        "Leather: full-grain Nappa cowhid, abrasion resistance 500r without significant damage or peeling. "
        "Chair Back: ABS integrated back frame. Armrests: fixed, padded armrests. "
        "Seat Cushion: optional PVC synthetic leather.",
        "Silla",
        "es",
    )
    table_text = build_product_description(
        "SH56.042026 Flower 6 Occasional Tables",
        "Tabletop: 19mm, E0 grade plywood. Tabletop Edge Banding: natural wood veneer edge banding strips. "
        "Function Description: Writing Board Adjustment: Writing board can rotate 360 degrees.",
        "Mesas de Apoyo",
        "es",
    )

    assert "piel de vaca napa de flor entera" in chair_text.lower()
    assert "sin danos significativos ni desprendimiento" in chair_text.lower()
    assert "estructura integrada de respaldo en abs" in chair_text.lower()
    assert "descansabrazos fijos y acolchados" in chair_text.lower()
    assert "piel sintetica de pvc opcional" in chair_text.lower()
    assert "contrachapado grado e0" in table_text.lower()
    assert "canto de chapa de madera natural" in table_text.lower()
    assert "pizarron de escritura puede girar 360 grados" in table_text.lower()
    for fragment in [
        "Leather",
        "cowhid",
        "without significant damage",
        "ABS integrated back frame",
        "fixed, padded armrests",
        "synthetic leather",
        "plywood",
        "Writing Board Adjustment",
        "Writing board can rotate",
    ]:
        assert fragment.lower() not in f"{chair_text}\n{table_text}".lower()


def test_spanish_description_translates_sales_del_valle_chair_terms():
    leather_text = build_product_description(
        "CAL61KC Aulenti Task Chair",
        "Leather: contact surface uses full-grain lychee texture cowhide, "
        "non-contact surface PVC artificial leather, genuine leather dry rubbing fastness >= 4. "
        "Back Cushion: molded foam, load capacity 1001N. "
        "5-Star Bases: 700mm diameter aluminum alloy 5-star base.",
        "Silla",
        "es",
    )
    frame_text = build_product_description(
        "CDK18MS Ducky Guest",
        "Chair Back: PP+25% fiberglass integrated back frame. "
        "Seat: PP + 25% fiberglass integrated. Chair Frame: solid wood painted frame. "
        "Backrest: injection molded. Seat: 16mm poplar plywood. "
        "Adjustable Feet: Nickel-plated carbon steel.",
        "Silla",
        "es",
    )

    assert "superficie de contacto en piel de vaca de flor entera con textura lichi" in leather_text.lower()
    assert "superficie sin contacto en piel sintetica de pvc" in leather_text.lower()
    assert "cojin del respaldo" in leather_text.lower()
    assert "capacidad de carga 1001n" in leather_text.lower()
    assert "estructura integrada de respaldo en pp con 25% de fibra de vidrio" in frame_text.lower()
    assert "pp integrado con 25% de fibra de vidrio" in frame_text.lower()
    assert "estructura de madera solida pintada" in frame_text.lower()
    assert "moldeado por inyeccion" in frame_text.lower()
    assert "contrachapado de alamo" in frame_text.lower()
    assert "regatones ajustables" in frame_text.lower()
    assert "acero al carbono niquelado" in frame_text.lower()
    for fragment in [
        "contact surface",
        "cowhide",
        "non-contact",
        "genuine leather",
        "Back Cushion",
        "load capacity",
        "integrated back frame",
        "solid wood painted",
        "injection molded",
        "poplar plywood",
        "Adjustable Feet",
        "Nickel-plated",
        "Pinturaed",
    ]:
        assert fragment.lower() not in f"{leather_text}\n{frame_text}".lower()


def test_spanish_description_translates_sales_del_valle_workstation_terms():
    text = build_product_description(
        "Desk Lido Pro PB - L",
        "Edge banding: the table top uses PVC edge banding strips, thickness 2.5mm; "
        "the cabinet body uses PVC edge banding strips, thickness 1mm. "
        "Edge Banding: the cabinet uses high quality PVC edge banding strips, "
        "thickness 2.5mm for cabinet side panels. "
        "Modesty Panel: impregnated melamine paper. "
        "Lock: mechanical combination locks. "
        "Cable Box: standard flip-top cable box, made of aluminum alloy + ABS.",
        "Escritorios-WorkStation",
        "es",
    )

    assert "la cubierta usa cantos de pvc" in text.lower()
    assert "el cuerpo del gabinete usa cantos de pvc" in text.lower()
    assert "paneles laterales del gabinete" in text.lower()
    assert "faldon" in text.lower()
    assert "cerraduras mecanicas de combinacion" in text.lower()
    assert "caja pasacables estandar con tapa abatible" in text.lower()
    for fragment in [
        "table top",
        "cabinet body",
        "cabinet side panels",
        "Modesty Panel",
        "mechanical combination locks",
        "flip-top cable box",
    ]:
        assert fragment.lower() not in text.lower()


def test_spanish_description_translates_sales_del_valle_remaining_table_terms():
    occasional = build_product_description(
        "DMC71.160055 MixCube Occasional Table",
        "Fixed High-leg desk, steel Legs in two sides. "
        "Desk Legs: V-shaped steel legs, Q195 steel 40mm round tube, "
        "1.5mm wall thickness, electrostatic powder coating in surface. "
        "Adjustable feet: Adjustable, made of ABS + premium steel.",
        "Mesas de Apoyo",
        "es",
    )
    square = build_product_description(
        "T167.110110 Square 4 Post Leg Occasional Table",
        "Square Tea Table with Square Steel tube base. "
        "Veneer: Wood-color configuration, Main components: natural wood veneer, "
        "Underside of the tabletop: scientific veneer; "
        "Non-wood Color Configuration: No wood veneer used. "
        "Table Legs: Goal post leg in steel.",
        "Mesas de Apoyo",
        "es",
    )

    joined = f"{occasional}\n{square}".lower()
    assert "mesa alta fija con patas de acero a ambos lados" in joined
    assert "patas de acero en forma de v" in joined
    assert "espesor de pared" in joined
    assert "mesa de te cuadrada con base de tubo de acero cuadrado" in joined
    assert "cara inferior de la cubierta" in joined
    assert "chapa de madera reconstituida" in joined
    for fragment in [
        "fixed high-leg",
        "desk legs",
        "v-shaped",
        "wall thickness",
        "adjustable",
        "square tea table",
        "wood-color configuration",
        "main components",
        "underside",
        "scientific veneer",
        "no wood veneer used",
        "goal post leg",
    ]:
        assert fragment not in joined


def test_spanish_description_translates_sales_del_valle_remaining_storage_and_podium_terms():
    storage = build_product_description(
        "MG.GC07-2.090045 3-drawer lateral file",
        "Lock: ordinary front lock. Fixed three-drawer cabinet.",
        "Archiveros Moviles y Fijos",
        "es",
    )
    podium = build_product_description(
        "DM21.114 Welss Training system Fixed Height podium",
        "Fixed podium. Podium Support: Q195 steel, 2.0mm thicknesses. "
        "Podium Outside Panel: Q195 steel. Podium Steel Foot: Q195 steel.",
        "Terminados",
        "es",
    )

    joined = f"{storage}\n{podium}".lower()
    assert "cerradura frontal estandar" in joined
    assert "archivero fijo de tres cajones" in joined
    assert "podio fijo" in joined
    assert "soporte del podio" in joined
    assert "panel exterior del podio" in joined
    for fragment in [
        "ordinary front lock",
        "fixed three-drawer",
        "fixed podium",
        "podium support",
        "podium outside panel",
        "podium steel foot",
        "thicknesses",
    ]:
        assert fragment not in joined


def test_spanish_description_translates_sales_del_valle_remaining_lounge_and_phonebooth_terms():
    lounge = build_product_description(
        "SU38.1.MR UoE Lounge Chairs",
        "Spring Bag: seat cushion with individually wrapped springs, good breathability. "
        "Leg Frame: main materials use aluminum alloy + ABS. "
        "Sofa Support Parts: steel pivot, 360 degree rotation. "
        "Sofa Legs: steel base legs.",
        "Sillones",
        "es",
    )
    phonebooth = build_product_description(
        "DNA20-2 DNA II Phone booth",
        "Frame: AL6063-T5 aluminum alloy, profile 2.0mm thickness. "
        "Steel Frame: 60x40mm rectangular steel tube. "
        "Side Panel/Top Panel: made of high-quality cold-rolled steelin surface. "
        "Internal sound-absorbing cotton: 2500g/m2 grammage. "
        "Uses 4mm polyester fiber board + 1mm flocked felt, combined into a 5mm panel in inner panel. "
        "Fixed Glass: tempering glass, black screen printing on all four sides.",
        "Phonebooths",
        "es",
    )

    joined = f"{lounge}\n{phonebooth}".lower()
    assert "cojin del asiento con resortes embolsados individualmente" in joined
    assert "buena ventilacion" in joined
    assert "componentes de soporte del sofa" in joined
    assert "estructura de acero" in joined
    assert "algodon absorbente acustico interior" in joined
    assert "tablero de fibra de poliester" in joined
    assert "vidrio templado" in joined
    assert "serigrafia negra en los cuatro lados" in joined
    for fragment in [
        "spring bag",
        "individually wrapped",
        "good breathability",
        "leg frame",
        "main materials use",
        "sofa support parts",
        "steel pivot",
        "steel frame",
        "rectangular steel tube",
        "sound-absorbing cotton",
        "grammage",
        "polyester fiber board",
        "flocked felt",
        "inner panel",
        "fixed glass",
        "tempering glass",
        "screen printing",
    ]:
        assert fragment not in joined


@pytest.mark.parametrize(
    ("name", "source", "category", "expected", "forbidden"),
    [
        pytest.param(
            "BAGEL",
            "BAGEL Rectangular Coffee Table Base: Alu in teaklook Ceramic Top: JK-4",
            "Mesas de Apoyo",
            ("mesa de apoyo rectangular", "aluminio con apariencia de teca", "cubierta de ceramica"),
            ("coffee", "teaklook", "ceramic top"),
            id="bagel",
        ),
        pytest.param(
            "CAYENNE",
            "CAYENNE 2-seat left arm module with rope",
            "Sillones",
            ("modulo izquierdo de dos plazas", "descansabrazos", "cuerda"),
            ("2-seat", "left arm", "rope"),
            id="cayenne",
        ),
        pytest.param(
            "Chiengmai",
            "Chiengmai Meridienne Left short arm with straps full aluminium legs",
            "Sillones",
            ("meridiana", "descansabrazos corto izquierdo", "correas", "patas de aluminio"),
            ("meridienne", "straps", "aluminium"),
            id="chiengmai",
        ),
        pytest.param(
            "Dinosaur egg",
            "Dinosaur egg sling dining armchair with loose seat cushion, full aluminium legs",
            "Silla",
            ("silla de comedor con brazos", "cojin de asiento suelto", "patas de aluminio"),
            ("dining armchair", "loose seat", "aluminium"),
            id="dinosaur-egg",
        ),
        pytest.param(
            "Dublin",
            "Dublin barstool with back Seat and back with rope",
            "Bancos",
            ("banco alto", "asiento y respaldo", "cuerda"),
            ("barstool", "with back", "rope"),
            id="dublin",
        ),
        pytest.param(
            "ENZO",
            "ENZO Coffee Table (L) Top: Ceramic",
            "Mesas de Apoyo",
            ("mesa de apoyo", "cubierta: ceramica"),
            ("coffee table", "top: ceramic"),
            id="enzo",
        ),
        pytest.param(
            "IBIZA",
            "IBIZA dining armchair with rope",
            "Silla",
            ("silla de comedor con brazos", "cuerda"),
            ("dining armchair", "rope"),
            id="ibiza",
        ),
        pytest.param(
            "Lille",
            "Lille coffee table (S) with ceramic top - Travertino romano",
            "Mesas de Apoyo",
            ("mesa de apoyo", "cubierta de ceramica", "travertino romano"),
            ("coffee table", "ceramic top"),
            id="lille",
        ),
        pytest.param(
            "LOFT",
            "LOFT EXTENSION MODULE SINGLE, W/SPRING, VERSION C",
            "Sillones",
            ("modulo individual de extension", "con resorte", "version c"),
            ("extension module", "w/spring"),
            id="loft",
        ),
        pytest.param(
            "LOIRE",
            "LOIRE LOUNGE ARMCHAIR WITH STRAP 45mm",
            "Sillones",
            ("sillon de descanso", "con correa", "45 mm"),
            ("lounge armchair", "strap"),
            id="loire",
        ),
    ],
)
def test_spanish_description_translates_real_alma_catalog_phrases(
    name,
    source,
    category,
    expected,
    forbidden,
):
    description = build_product_description(name, source, category, "es")
    body = description.split("\n", 1)[1].casefold()

    for fragment in expected:
        assert fragment.casefold() in body
    for fragment in forbidden:
        assert fragment.casefold() not in body


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


def test_spanish_finish_uses_official_freight_concept_instead_of_generic_product():
    text = build_product_description(
        "Desso Grain B867 9506 B1 50x50",
        "Alfombra modular Desso",
        "Terminados",
        "es",
    )

    assert text.startswith("Terminado modelo Desso Grain B867 9506 B1 50x50.")
    assert not text.startswith("Producto terminado")


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
