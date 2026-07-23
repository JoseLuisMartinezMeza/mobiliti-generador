import json
import subprocess
from pathlib import Path


MODULE = Path("mobiliti_saas/web/src/mixedCart.js").resolve().as_uri()


def run_js(source):
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f'import * as model from {json.dumps(MODULE)};\n{source}',
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_occurrences_replace_one_all_and_remove_principal_complements():
    result = run_js(r"""
      const base = {
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1",
        provider: "Sunon",
        quantity: "2",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1",
      };
      let lines = [
        model.createMixedCartLine({...base, lineId: "11111111-1111-4111-8111-111111111111"}),
        model.createMixedCartLine({...base, lineId: "22222222-2222-4222-8222-222222222222"}),
      ];
      lines = model.addProjectComplement(lines, lines[0].lineId, {
        ...base,
        lineId: "33333333-3333-4333-8333-333333333333",
        officialCode: "HEAD-1",
        quantity: "1",
      }, "per_parent_unit");
      const target = {
        ...base,
        catalog: "alma",
        identity: {internal_id: "alma:new", base_option_id: "", add_on_option_ids: []},
        officialCode: "NEW-1",
        provider: "ALMA",
        snapshot: {...base.snapshot, name: "New", code: "NEW-1"},
      };
      const one = model.replaceProjectLine(lines, lines[0].lineId, target);
      const all = model.replaceAllProjectLines(lines, {provider: "Sunon", officialCode: "CHAIR-1"}, target);
      console.log(JSON.stringify({
        unique: new Set(lines.map((line) => line.lineId)).size,
        oneCodes: one.lines.filter((line) => line.role === "principal").map((line) => line.officialCode),
        oneRemoved: one.removedComplementIds,
        allCodes: all.lines.filter((line) => line.role === "principal").map((line) => line.officialCode),
        allAffected: all.summary.affected,
      }));
    """)
    assert result == {
        "unique": 3,
        "oneCodes": ["NEW-1", "CHAIR-1"],
        "oneRemoved": ["33333333-3333-4333-8333-333333333333"],
        "allCodes": ["NEW-1", "NEW-1"],
        "allAffected": 2,
    }


def test_imported_line_matches_provider_and_official_code():
    result = run_js(r"""
      const imported = {
        kind: "imported", role: "principal",
        lineId: "11111111-1111-4111-8111-111111111111",
        officialCode: " OHE-405 ", provider: "Offiho",
      };
      console.log(JSON.stringify({
        matches: model.projectLineMatches(imported, {provider: " offiho ", officialCode: "OHE-405"}),
        missing: model.projectLineMatches({...imported, officialCode: ""}, {provider: "offiho", officialCode: "OHE-405"}),
      }));
    """)
    assert result == {"matches": True, "missing": False}


def test_project_serialization_round_trips_occurrence_graph():
    result = run_js(r"""
      const state = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "",
          direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: [{id: "section-1", concept: "RecepciÃ³n"}],
        lines: [model.createMixedCartLine({
          catalog: "sunon",
          identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
          officialCode: "CHAIR-1",
          provider: "Sunon",
          quantity: "1",
          quantityRules: {min: "1", step: "1", maxDecimals: 0,
            max: "1000000", integer: true},
          snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
            availability: "", configuration: "", warnings: []},
          sectionId: "section-1",
          lineId: "11111111-1111-4111-8111-111111111111",
        })],
      };
      const payload = model.serializeProject(state);
      const reopened = model.hydrateProject(payload);
      console.log(JSON.stringify({
        payloadLine: payload.lines[0].line_id,
        reopenedLine: reopened.lines[0].lineId,
        section: reopened.sections[0].concept,
      }));
    """)
    assert result == {
        "payloadLine": "11111111-1111-4111-8111-111111111111",
        "reopenedLine": "11111111-1111-4111-8111-111111111111",
        "section": "RecepciÃ³n",
    }


def test_imported_serialization_uses_editable_code_and_provider():
    result = run_js(r"""
      const payload = model.serializeProject({
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: [{id: "section-1", concept: "RecepciÃ³n"}],
        lines: [{
          kind: "imported", key: "import:11111111-1111-4111-8111-111111111111:9",
          lineId: "22222222-2222-4222-8222-222222222222", role: "principal",
          sectionId: "section-1", parentLineId: null, quantityMode: null, position: 0,
          importId: "11111111-1111-4111-8111-111111111111", sourceRow: 9,
          sourceCurrency: "USD", quantity: "1",
          quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "1000000"},
          officialCode: "STALE", provider: "Stale", imageAssetKey: "", sourceAssetKey: "",
          snapshot: {name: "Importada", code: "STALE", image_url: "", unit: "PZA",
            availability: "No serializar", configuration: "", warnings: []},
          edits: {officialCode: "OHE-405", name: "Importada", description: "", dimension: "",
            unitPrice: "20.00", provider: "Offiho"},
        }],
      });
      console.log(JSON.stringify({
        code: payload.lines[0].official_code,
        provider: payload.lines[0].provider,
        hasAvailability: Object.hasOwn(payload.lines[0].display_cache, "availability"),
      }));
    """)
    assert result == {"code": "OHE-405", "provider": "Offiho", "hasAvailability": False}


def test_hydrate_catalog_line_without_optional_quantity_rules_cache():
    result = run_js(r"""
      const line = model.createMixedCartLine({
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1", provider: "Sunon", quantity: "1.5",
        quantityRules: {min: "0.5", step: "0.5", maxDecimals: 1, max: "100", integer: false},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1", lineId: "11111111-1111-4111-8111-111111111111",
      });
      const payload = model.serializeProject({
        quoteFields: {proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40"},
        sections: [{id: "section-1", concept: "RecepciÃ³n"}], lines: [line],
      });
      delete payload.lines[0].quantity_rules_cache;
      const reopened = model.hydrateProject(payload);
      console.log(JSON.stringify({
        quantity: reopened.lines[0].quantity,
        backendFallback: reopened.lines[0].projectQuantityFallback,
      }));
    """)
    assert result == {
        "quantity": "1.5",
        "backendFallback": True,
    }


def test_no_cache_catalog_fallback_matches_backend_decimal_boundaries():
    result = run_js(r"""
      const line = model.createMixedCartLine({
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1", provider: "Sunon", quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "100", integer: true},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1", lineId: "11111111-1111-4111-8111-111111111111",
      });
      const base = model.serializeProject({
        quoteFields: {proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40"},
        sections: [{id: "section-1", concept: "RecepciÃ³n"}], lines: [line],
      });
      delete base.lines[0].quantity_rules_cache;
      const outcomes = ["1.0000001", "1e-7", "1000000", "0", "-1", "1000000.0000001"]
        .map((quantity) => {
          const payload = JSON.parse(JSON.stringify(base));
          payload.lines[0].quantity = quantity;
          try { return model.hydrateProject(payload).lines[0].quantity; }
          catch { return "rejected"; }
        });
      console.log(JSON.stringify(outcomes));
    """)
    assert result == [
        "1.0000001",
        "0.0000001",
        "1000000",
        "rejected",
        "rejected",
        "rejected",
    ]


def test_hydrate_project_rejects_unknown_nested_persisted_keys():
    result = run_js(r"""
      const line = model.createMixedCartLine({
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1", provider: "Sunon", quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "100", integer: true},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1", lineId: "11111111-1111-4111-8111-111111111111",
      });
      const state = {
        quoteFields: {proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40"},
        sections: [{id: "section-1", concept: "RecepciÃ³n"}], lines: [line],
      };
      const payload = model.serializeProject(state);
      const attempts = [
        ["display", (copy) => { copy.lines[0].display_cache.availability = "forged"; }],
        ["rules", (copy) => { copy.lines[0].quantity_rules_cache.forged = true; }],
        ["identity", (copy) => { copy.lines[0].identity.forged = true; }],
      ].map(([name, mutate]) => {
        const copy = JSON.parse(JSON.stringify(payload)); mutate(copy);
        try { model.hydrateProject(copy); return name + ":accepted"; }
        catch { return name + ":rejected"; }
      });
      console.log(JSON.stringify(attempts));
    """)
    assert result == ["display:rejected", "rules:rejected", "identity:rejected"]


def test_serialize_project_normalizes_empty_section_concept_without_mutating_state():
    result = run_js(r"""
      const line = model.createMixedCartLine({
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1", provider: "Sunon", quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "100", integer: true},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1", lineId: "11111111-1111-4111-8111-111111111111",
      });
      const sections = [{id: "section-1", concept: ""}];
      const payload = model.serializeProject({
        quoteFields: {proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40"},
        sections, lines: [line],
      });
      console.log(JSON.stringify({saved: payload.sections[0].concept, editable: sections[0].concept}));
    """)
    assert result == {"saved": "Recepci\u00f3n", "editable": ""}
