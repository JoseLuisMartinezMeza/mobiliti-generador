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
