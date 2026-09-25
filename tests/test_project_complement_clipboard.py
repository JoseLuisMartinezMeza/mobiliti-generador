"""Copiar complementos conserva la configuracion y crea ocurrencias independientes."""

from test_project_model_ui import run_js
from project_fixtures import valid_project_payload
import json


def test_pegar_complementos_conserva_destino_y_modos_al_guardar():
    resultado = run_js(r'''
      const principal = (lineId, quantity, sectionId) => model.createMixedCartLine({
        lineId, catalog: "sunon", identity: {internal_id: "sunon:mesa"}, quantity,
        sectionId, snapshot: {name: "Mesa", code: "MESA", warnings: []},
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
      });
      const origen = principal("11111111-1111-4111-8111-111111111111", "2", "section-1");
      const destino = principal("22222222-2222-4222-8222-222222222222", "5", "section-2");
      const accesorio = {...principal("33333333-3333-4333-8333-333333333333", "3", "section-1"),
        identity: {internal_id: "sunon:usb", base_option_id: "negro", add_on_option_ids: ["cable"]}};
      let lines = model.addProjectComplement([origen, destino], origen.lineId, accesorio, "per_parent_unit");
      lines = model.addProjectComplement(lines, origen.lineId, {...accesorio, quantity: "4"}, "fixed_project");
      lines = model.addProjectComplement(lines, destino.lineId, {...accesorio, quantity: "1"}, "fixed_project");
      const antes = JSON.stringify(lines);
      const copia = model.copiar_complementos_proyecto(lines, origen.lineId);
      const una = model.copiar_complementos_proyecto(lines, lines[2].lineId);
      const pegadas = model.pegar_complementos_proyecto(lines, copia, destino.lineId);
      const repetidas = model.pegar_complementos_proyecto(pegadas, una, destino.lineId);
      copia.complements[0].identity.add_on_option_ids.push("solo-portapapeles");
      const proyecto = {
        quoteFields: {proyecto: "Oficina", cliente: "Cliente", correo: "", telefono: "",
          direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40",
          template: "official_2026_gdl", description_language: "es"},
        sections: [{id: "section-1", concept: "Origen"}, {id: "section-2", concept: "Destino"}],
        lines: repetidas,
      };
      const reabierto = model.hydrateProject(model.serializeProject(proyecto));
      console.log(JSON.stringify({
        originalIntacto: antes === JSON.stringify(lines),
        principales: reabierto.lines.filter(l => l.role === "principal").map(l => [l.lineId, l.quantity, l.sectionId]),
        complementos: model.projectComplements(reabierto.lines, destino.lineId).map(l => [l.quantity, l.quantityMode, l.position]),
        independientes: new Set(repetidas.map(l => l.lineId)).size === repetidas.length,
        opciones: model.projectComplements(reabierto.lines, destino.lineId)[1].identity.add_on_option_ids,
        origen: model.projectComplements(reabierto.lines, origen.lineId).length,
      }));
    ''')
    assert resultado == {
        "originalIntacto": True,
        "principales": [
            ["11111111-1111-4111-8111-111111111111", "2", "section-1"],
            ["22222222-2222-4222-8222-222222222222", "5", "section-2"],
        ],
        "complementos": [["1", "fixed_project", 0], ["3", "per_parent_unit", 1],
                          ["4", "fixed_project", 2], ["3", "per_parent_unit", 3]],
        "independientes": True, "opciones": ["cable"], "origen": 2,
    }


def test_portapapeles_rechaza_destinos_y_contenido_invalidos():
    resultado = run_js(r'''
      const id = "11111111-1111-4111-8111-111111111111";
      const hijo = "22222222-2222-4222-8222-222222222222";
      const lines = [{lineId: id, role: "principal"},
        {lineId: hijo, role: "complement", parentLineId: id, position: 0, quantityMode: "fixed_project"}];
      const copia = model.copiar_complementos_proyecto(lines, hijo);
      const casos = [
        () => model.pegar_complementos_proyecto(lines, copia, hijo),
        () => model.pegar_complementos_proyecto(lines, null, id),
        () => model.pegar_complementos_proyecto(lines, {...copia, complements: [lines[0]]}, id),
        () => model.pegar_complementos_proyecto(lines, {...copia, complements: []}, id),
        () => model.copiar_complementos_proyecto([lines[0]], id),
      ];
      console.log(JSON.stringify(casos.map(caso => {try {caso(); return false;} catch {return true;}})));
    ''')
    assert resultado == [True] * 5


def test_copia_importada_conserva_precio_fuente_y_ediciones_independientes():
    proyecto = valid_project_payload()
    resultado = run_js(f"const payload = {json.dumps(proyecto)};" + r'''
      const base = model.hydrateProject(payload);
      const origen = base.lines[1];
      const copia = model.copiar_complementos_proyecto(base.lines, origen.lineId);
      const lines = model.pegar_complementos_proyecto(base.lines, copia, base.lines[0].lineId);
      const nueva = lines[lines.length - 1];
      const editadas = model.updateImportedCartLine(lines, nueva.key, {name: "Copia editada", unitPrice: "35"});
      const guardado = model.serializeProject({...base, lines: editadas});
      console.log(JSON.stringify(guardado.lines.filter(l => l.role === "complement").map(l => ({
        name: l.name, price: l.unit_price, row: l.source_row, source: l.source_asset_key,
        mode: l.quantity_mode, parent: l.parent_line_id,
      }))));
    ''')
    assert resultado == [
        {"name": nombre, "price": precio, "row": 14,
         "source": proyecto["lines"][1]["source_asset_key"],
         "mode": "per_parent_unit", "parent": proyecto["lines"][0]["line_id"]}
        for nombre, precio in [("Cabecera", "20.00"), ("Copia editada", "35")]
    ]
