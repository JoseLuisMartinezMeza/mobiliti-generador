import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.project_model import normalize_project_payload


MODULE = Path("mobiliti_saas/web/src/mixedCart.js").resolve().as_uri()
IMPORT_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SOURCE_HASH = "a" * 64


def promotion_preview():
    item = {
        "key": f"import:{IMPORT_ID}:9",
        "source_row": 9,
        "category": "Sala",
        "name": "Silla importada",
        "description": "",
        "dimension": "",
        "provider": "Offiho México",
        "official_code": "OHE-405",
        "quantity": "1",
        "unit_price": "80.00",
        "source_currency": "USD",
        "source_reference": "quotation.xlsx#Quotation!9",
        "row_hash": "b" * 64,
    }
    manifest = {
        "schema_version": 1,
        "import_id": IMPORT_ID,
        "source_hash": SOURCE_HASH,
        "original_filename": "quotation.xlsx",
        "provider": "Proveedor general",
        "source_currency": "USD",
        "currency_status": "detected",
        "columns": {
            "m3": "H", "unit_price": "J", "descripcion": "D", "dimension": "E",
        },
        "sections": [{
            "id": "import-section-1",
            "title": "Sala",
            "item_keys": [item["key"]],
        }],
        "items": [item],
    }
    preview = deepcopy(manifest)
    preview["items"][0]["image_url"] = "/cotizaciones/preview/transient.png"
    return preview, manifest


def valid_promotion_response():
    _preview, manifest = promotion_preview()
    prefix = f"projects/7/{PROJECT_ID}"
    return {
        "source_asset_key": f"{prefix}/sources/{SOURCE_HASH}.xlsx",
        "image_asset_keys": {
            "9": f"{prefix}/images/{SOURCE_HASH[:16]}-row-9.png",
        },
        "manifest": manifest,
    }


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


def test_section_reordering_persists_the_complete_product_tree():
    result = run_js(r"""
      const firstId = "11111111-1111-4111-8111-111111111111";
      const secondId = "22222222-2222-4222-8222-222222222222";
      const complementId = "33333333-3333-4333-8333-333333333333";
      const sections = [
        {id: "section-1", concept: "Recepcion"},
        {id: "section-2", concept: "Sala de juntas"},
      ];
      const base = {
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1",
        provider: "Sunon",
        quantity: "2",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
      };
      let lines = [
        model.createMixedCartLine({...base, lineId: firstId, sectionId: "section-1"}),
        model.createMixedCartLine({...base, lineId: secondId, sectionId: "section-2"}),
      ];
      lines = model.addProjectComplement(lines, firstId, {
        ...base,
        lineId: complementId,
        officialCode: "POWER-1",
        snapshot: {...base.snapshot, name: "Multicontacto", code: "POWER-1"},
      }, "fixed_project");

      const movedSections = model.moveMixedCartSection(sections, lines, "section-1", "down");
      const saved = model.serializeProject({
        quoteFields: {
          proyecto: "Proyecto", cliente: "Cliente", correo: "", telefono: "",
          direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40",
          template: "official_2026_gdl", description_language: "es",
        },
        sections: movedSections,
        lines,
      });
      const reopened = model.hydrateProject(saved);
      console.log(JSON.stringify({
        originalOrder: sections.map((section) => section.id),
        savedOrder: saved.sections.map((section) => `${section.section_id}:${section.position}`),
        reopenedOrder: reopened.sections.map((section) => section.id),
        relationships: reopened.lines.map((line) => ({
          lineId: line.lineId,
          role: line.role,
          sectionId: line.sectionId,
          parentLineId: line.parentLineId,
        })),
      }));
    """)
    assert result == {
        "originalOrder": ["section-1", "section-2"],
        "savedOrder": ["section-2:0", "section-1:1"],
        "reopenedOrder": ["section-2", "section-1"],
        "relationships": [
            {
                "lineId": "11111111-1111-4111-8111-111111111111",
                "role": "principal",
                "sectionId": "section-1",
                "parentLineId": None,
            },
            {
                "lineId": "22222222-2222-4222-8222-222222222222",
                "role": "principal",
                "sectionId": "section-2",
                "parentLineId": None,
            },
            {
                "lineId": "33333333-3333-4333-8333-333333333333",
                "role": "complement",
                "sectionId": None,
                "parentLineId": "11111111-1111-4111-8111-111111111111",
            },
        ],
    }


def test_section_reordering_keeps_the_empty_tail_section_anchored():
    result = run_js(r"""
      const sections = [
        {id: "section-1", concept: "Recepcion"},
        {id: "section-2", concept: "Sala de juntas"},
        {id: "section-3", concept: "Espacio abierto"},
      ];
      const lines = [
        {sectionId: "section-1"},
        {sectionId: "section-2"},
      ];
      const emptyMovedUp = model.moveMixedCartSection(
        sections, lines, "section-3", "up"
      );
      const occupiedMovedDown = model.moveMixedCartSection(
        sections, lines, "section-2", "down"
      );
      console.log(JSON.stringify({
        emptyMovedUp: emptyMovedUp.map((section) => section.id),
        occupiedMovedDown: occupiedMovedDown.map((section) => section.id),
      }));
    """)
    assert result == {
        "emptyMovedUp": ["section-1", "section-2", "section-3"],
        "occupiedMovedDown": ["section-1", "section-2", "section-3"],
    }


def test_copy_paste_inserts_an_independent_snapshot_before_target_with_complements():
    result = run_js(r"""
      const sourceId = "11111111-1111-4111-8111-111111111111";
      const targetId = "22222222-2222-4222-8222-222222222222";
      const childId = "33333333-3333-4333-8333-333333333333";
      const base = model.hydrateProject({
        schema_version: 1,
        quote_fields: {
          proyecto: "Proyecto", cliente: "Cliente", correo: "", telefono: "",
          direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40",
          template: "official_2026_gdl", description_language: "es",
        },
        sections: [
          {section_id: "section-1", concept: "Origen", position: 0},
          {section_id: "section-2", concept: "Destino", position: 1},
        ],
        lines: [{
          line_id: sourceId,
          role: "principal",
          section_id: "section-1",
          parent_line_id: null,
          position: 0,
          quantity: "3",
          source: "imported",
          official_code: "IMP-1",
          display_cache: {
            name: "Mesa importada", code: "IMP-1", image_url: "",
            configuration: "Nogal con electrificacion",
          },
          import_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          source_row: 9,
          source_currency: "USD",
          provider: "Proveedor original",
          name: "Mesa configurada",
          description: "Descripcion editada",
          dimension: "240 x 120 cm",
          unit_price: "125.50",
          image_asset_key: "projects/1/project/images/source-row-9.png",
          source_asset_key: "projects/1/project/sources/source.xlsx",
        }, {
          line_id: targetId,
          role: "principal",
          section_id: "section-2",
          parent_line_id: null,
          position: 0,
          quantity: "1",
          source: "catalog",
          official_code: "TARGET-1",
          display_cache: {name: "Destino", code: "TARGET-1", image_url: ""},
          catalog: "sunon",
          identity: {
            internal_id: "sunon:target", base_option_id: "", add_on_option_ids: [],
          },
          quantity_rules_cache: {
            min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true,
          },
        }],
      });
      let lines = model.addProjectComplement(base.lines, sourceId, {
        lineId: childId,
        catalog: "alma",
        identity: {
          internal_id: "alma:power", base_option_id: "base-nogal",
          add_on_option_ids: ["usb", "contacto"],
        },
        officialCode: "POWER-1",
        provider: "ALMA",
        quantity: "2",
        quantityRules: {
          min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true,
        },
        snapshot: {
          name: "Modulo electrico", code: "POWER-1", image_url: "", unit: "PZA",
          availability: "Sobre pedido", configuration: "USB + contacto", warnings: [],
        },
      }, "fixed_project");

      const clipboard = model.copyProjectLineTree(lines, sourceId);
      lines = model.updateImportedCartLine(lines, lines[0].key, {
        name: "Fuente modificada despues de copiar",
        unitPrice: "999.00",
      });
      const pastedLines = model.pasteProjectLineTree(lines, clipboard, targetId);
      const pasted = pastedLines.find((line) => (
        line.role === "principal"
        && line.sectionId === "section-2"
        && line.officialCode === "IMP-1"
      ));
      const pastedChild = model.projectComplements(pastedLines, pasted.lineId)[0];
      const saved = model.serializeProject({...base, lines: pastedLines});
      const reopened = model.hydrateProject(saved);
      const reopenedCopy = reopened.lines.find((line) => line.lineId === pasted.lineId);
      const edited = model.updateImportedCartLine(reopened.lines, reopenedCopy.key, {
        name: "Solo la copia",
      });
      console.log(JSON.stringify({
        section2: pastedLines
          .filter((line) => line.role === "principal" && line.sectionId === "section-2")
          .sort((left, right) => left.position - right.position)
          .map((line) => `${line.officialCode}:${line.position}`),
        sourceName: lines.find((line) => line.lineId === sourceId).edits.name,
        copiedName: pasted.edits.name,
        copiedPrice: pasted.edits.unitPrice,
        copiedQuantity: pasted.quantity,
        copiedConfiguration: pasted.snapshot.configuration,
        newPrincipalId: pasted.lineId !== sourceId && pasted.lineId !== targetId,
        newComplementId: pastedChild.lineId !== childId,
        complementParent: pastedChild.parentLineId === pasted.lineId,
        complementMode: pastedChild.quantityMode,
        complementQuantity: pastedChild.quantity,
        complementOptions: pastedChild.identity.add_on_option_ids,
        reopenedUniqueKeys: new Set(reopened.lines.map((line) => line.key)).size,
        reopenedLineCount: reopened.lines.length,
        editedImportedNames: edited
          .filter((line) => line.kind === "imported")
          .sort((left, right) => left.sectionId.localeCompare(right.sectionId))
          .map((line) => line.edits.name),
      }));
    """)
    assert result == {
        "section2": ["IMP-1:0", "TARGET-1:1"],
        "sourceName": "Fuente modificada despues de copiar",
        "copiedName": "Mesa configurada",
        "copiedPrice": "125.50",
        "copiedQuantity": "3",
        "copiedConfiguration": "Nogal con electrificacion",
        "newPrincipalId": True,
        "newComplementId": True,
        "complementParent": True,
        "complementMode": "fixed_project",
        "complementQuantity": "2",
        "complementOptions": ["contacto", "usb"],
        "reopenedUniqueKeys": 5,
        "reopenedLineCount": 5,
        "editedImportedNames": [
            "Fuente modificada despues de copiar",
            "Solo la copia",
        ],
    }


def test_imported_line_matches_provider_and_official_code():
    result = run_js(r"""
      const imported = {
        kind: "imported", role: "principal",
        lineId: "11111111-1111-4111-8111-111111111111",
        officialCode: " OHE   405 ", provider: "Offiho   México",
      };
      console.log(JSON.stringify({
        matches: model.projectLineMatches(imported, {
          provider: " offiho méxico ", officialCode: "ohe 405",
        }),
        missing: model.projectLineMatches(
          {...imported, officialCode: ""},
          {provider: "offiho méxico", officialCode: "OHE 405"},
        ),
      }));
    """)
    assert result == {"matches": True, "missing": False}


def test_replace_all_imported_without_official_code_uses_original_import_identity():
    result = run_js(r"""
      const imported = (lineId, importId, sourceRow, sectionId, name = "E904-2.200040 File Cabinet") => ({
        kind: "imported",
        role: "principal",
        lineId,
        importId,
        sourceRow,
        sourceCurrency: "USD",
        officialCode: "",
        provider: "SUNON TECHNOLOGY CO.,LTD.",
        quantity: "1",
        sectionId,
        position: 0,
        snapshot: {
          name,
          code: "",
          description: "File Cabinet",
          dimension: "2,003*400*845 mm",
          image_url: "",
          unit: "PZA",
          availability: "",
          configuration: "",
          warnings: [],
        },
        edits: {
          name,
          description: "File Cabinet",
          dimension: "2,003*400*845 mm",
          unitPrice: "164.53",
          provider: "SUNON TECHNOLOGY CO.,LTD.",
        },
      });
      const sourceImport = "18a91f71-f06a-4775-9ae7-5fa72b5c2d06";
      const lines = [
        imported("11111111-1111-4111-8111-111111111111", sourceImport, 11, "section-1"),
        imported("22222222-2222-4222-8222-222222222222", sourceImport, 15, "section-2"),
        imported("33333333-3333-4333-8333-333333333333", sourceImport, 19, "section-3"),
        imported("44444444-4444-4444-8444-444444444444", "different-import", 11, "section-4"),
        model.createMixedCartLine({
          lineId: "55555555-5555-4555-8555-555555555555",
          catalog: "sunon",
          identity: {internal_id: "sunon:file-cabinet", base_option_id: "", add_on_option_ids: []},
          officialCode: "",
          provider: "SUNON TECHNOLOGY CO.,LTD.",
          quantity: "1",
          quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
          snapshot: {name: "E904-2.200040 File Cabinet", code: "", image_url: "", unit: "PZA",
            availability: "", configuration: "", warnings: []},
          sectionId: "section-4",
        }),
      ];
      const target = {
        catalog: "sunon",
        identity: {internal_id: "sunon:replacement", base_option_id: "", add_on_option_ids: []},
        officialCode: "REPLACEMENT-1",
        provider: "Sunon Inc",
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Replacement", code: "REPLACEMENT-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
      };
      const selector = model.projectLineSelector(lines[0]);
      const replaced = model.replaceAllProjectLines(lines, selector, target);
      console.log(JSON.stringify({
        affected: replaced.summary.affected,
        imported: replaced.summary.imported,
        sections: replaced.summary.sections,
        codes: replaced.lines.map((line) => line.officialCode),
        names: replaced.lines.map((line) => line.snapshot.name),
      }));
    """)
    assert result == {
        "affected": 3,
        "imported": 3,
        "sections": 3,
        "codes": ["REPLACEMENT-1", "REPLACEMENT-1", "REPLACEMENT-1", "", ""],
        "names": ["Replacement", "Replacement", "Replacement",
                  "E904-2.200040 File Cabinet", "E904-2.200040 File Cabinet"],
    }


def test_imported_bundle_copies_row_code_and_provider_and_requires_durable_assets():
    preview, _manifest = promotion_preview()
    promotion = valid_promotion_response()
    result = run_js(f"""
      const preview = {json.dumps(preview, ensure_ascii=False)};
      const promotion = {json.dumps(promotion, ensure_ascii=False)};
      const expected = {{
        userId: 7,
        projectId: {json.dumps(PROJECT_ID)},
        importId: preview.import_id,
      }};
      let missingAssetError = "";
      try {{
        model.withDurableImportedAssets(
          preview,
          {{...promotion, image_asset_keys: {{}}}},
          expected,
        );
      }} catch (error) {{
        missingAssetError = error.message;
      }}
      const durable = model.withDurableImportedAssets(preview, promotion, expected);
      const bundle = model.createImportedCartBundle(
        durable, "USD", "Proveedor elegido", [{{id: "section-1", concept: "Recepción"}}],
      );
      console.log(JSON.stringify({{
        officialCode: bundle.lines[0].officialCode,
        provider: bundle.lines[0].provider,
        sourceAssetKey: bundle.lines[0].sourceAssetKey,
        imageAssetKey: bundle.lines[0].imageAssetKey,
        imageUrl: bundle.lines[0].snapshot.image_url,
        originalImageUrl: preview.items[0].image_url,
        missingAssetError,
      }}));
    """)
    assert result == {
        "officialCode": "OHE-405",
        "provider": "Offiho México",
        "sourceAssetKey": (
            f"projects/7/{PROJECT_ID}/sources/{SOURCE_HASH}.xlsx"
        ),
        "imageAssetKey": (
            f"projects/7/{PROJECT_ID}/images/{SOURCE_HASH[:16]}-row-9.png"
        ),
        "imageUrl": "",
        "originalImageUrl": "/cotizaciones/preview/transient.png",
        "missingAssetError": "Falta imagen durable para la fila importada 9",
    }


def test_durable_import_accepts_supplier_descriptions_between_1000_and_2000_characters():
    preview, manifest = promotion_preview()
    long_description = "Descripcion tecnica " + ("x" * 1_180)
    preview["items"][0]["description"] = long_description
    manifest["items"][0]["description"] = long_description
    promotion = valid_promotion_response()
    promotion["manifest"] = manifest

    result = run_js(f"""
      const preview = {json.dumps(preview, ensure_ascii=False)};
      const durable = model.withDurableImportedAssets(
        preview,
        {json.dumps(promotion, ensure_ascii=False)},
        {{
          userId: 7,
          projectId: {json.dumps(PROJECT_ID)},
          importId: preview.import_id,
        }},
      );
      const bundle = model.createImportedCartBundle(
        durable, "USD", "Proveedor elegido", [{{id: "section-1", concept: "Recepcion"}}],
      );
      console.log(JSON.stringify({{
        imported: bundle.lines.length,
        descriptionLength: bundle.lines[0].edits.description.length,
      }}));
    """)

    assert result == {
        "imported": 1,
        "descriptionLength": len(long_description),
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda response: response.__setitem__("extra", True),
        lambda response: response["manifest"].__setitem__("extra", True),
        lambda response: response.__setitem__(
            "source_asset_key",
            response["source_asset_key"].replace("projects/7/", "projects/8/"),
        ),
        lambda response: response.__setitem__(
            "source_asset_key",
            response["source_asset_key"].replace(PROJECT_ID, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ),
        lambda response: response.__setitem__(
            "source_asset_key",
            f"projects/7/{PROJECT_ID}/sources/../source.xlsx",
        ),
        lambda response: response["manifest"].__setitem__(
            "import_id", "22222222-2222-4222-8222-222222222222",
        ),
        lambda response: response["manifest"].__setitem__("source_hash", "c" * 64),
        lambda response: response["manifest"]["items"][0].__setitem__(
            "official_code", "STALE",
        ),
        lambda response: response["manifest"]["items"][0].__setitem__(
            "provider", "Proveedor stale",
        ),
        lambda response: response["manifest"]["items"][0].__setitem__("source_row", 10),
        lambda response: response["image_asset_keys"].__setitem__(
            "10", f"projects/7/{PROJECT_ID}/images/{SOURCE_HASH[:16]}-row-10.png",
        ),
        lambda response: response["image_asset_keys"].__setitem__(
            "9", f"projects/7/{PROJECT_ID}/images/not-the-canonical-row-name.png",
        ),
    ],
    ids=[
        "response-extra",
        "manifest-extra",
        "wrong-user",
        "wrong-project",
        "path-traversal",
        "wrong-import",
        "stale-source-hash",
        "stale-official-code",
        "stale-provider",
        "stale-row",
        "extra-image-row",
        "malformed-image-name",
    ],
)
def test_durable_import_response_rejects_malformed_or_stale_contract(mutation):
    preview, _manifest = promotion_preview()
    response = valid_promotion_response()
    mutation(response)

    result = run_js(f"""
      const preview = {json.dumps(preview, ensure_ascii=False)};
      const original = JSON.stringify(preview);
      let message = "";
      try {{
        model.withDurableImportedAssets(
          preview,
          {json.dumps(response, ensure_ascii=False)},
          {{userId: 7, projectId: {json.dumps(PROJECT_ID)}, importId: preview.import_id}},
        );
      }} catch (error) {{
        message = error.message;
      }}
      console.log(JSON.stringify({{
        rejected: Boolean(message),
        originalUnchanged: JSON.stringify(preview) === original,
      }}));
    """)

    assert result == {"rejected": True, "originalUnchanged": True}


def test_imported_official_code_allows_empty_rejects_unsafe_text_and_unlinks_immediately():
    result = run_js(r"""
      const edits = {
        officialCode: "",
        name: "Importada",
        description: "",
        dimension: "",
        unitPrice: "80.00",
        provider: "Offiho",
      };
      const empty = model.validateImportedCartEdits(edits);
      const failures = ["=CMD()", "+SUM(A1)", "-1+1", "@A1", "ABC\u0007"].map((officialCode) => {
        try {
          model.validateImportedCartEdits({...edits, officialCode});
          return "";
        } catch (error) {
          return error.message;
        }
      });
      const line = model.createImportedCartBundle({
        import_id: "11111111-1111-4111-8111-111111111111",
        source_currency: "USD",
        sections: [{
          id: "source", title: "Sala",
          item_keys: ["import:11111111-1111-4111-8111-111111111111:9"],
        }],
        items: [{
          key: "import:11111111-1111-4111-8111-111111111111:9",
          source_row: 9,
          official_code: "OHE-405",
          provider: "Offiho",
          name: "Importada",
          description: "",
          dimension: "",
          quantity: "1",
          unit_price: "80.00",
          source_currency: "USD",
          image_url: "",
        }],
      }, "USD", "Offiho", [{id: "section-1", concept: "Sala"}]).lines[0];
      const unlinked = model.updateImportedCartLine([line], line.key, {officialCode: ""})[0];
      console.log(JSON.stringify({
        empty: empty.officialCode,
        failures,
        visibleCode: unlinked.officialCode,
        matches: model.projectLineMatches(
          unlinked, {provider: "Offiho", officialCode: "OHE-405"},
        ),
      }));
    """)
    assert result == {
        "empty": "",
        "failures": [
            "Codigo oficial invalido",
            "Codigo oficial invalido",
            "Codigo oficial invalido",
            "Codigo oficial invalido",
            "Codigo oficial requerido",
        ],
        "visibleCode": "",
        "matches": False,
    }


def test_project_serialization_allows_pending_supplier_code_but_rejects_empty_legacy_code():
    result = run_js(r"""
      const imported = model.createImportedCartBundle({
        import_id: "11111111-1111-4111-8111-111111111111",
        source_currency: "USD",
        sections: [{
          id: "source", title: "Sala",
          item_keys: ["import:11111111-1111-4111-8111-111111111111:9"],
        }],
        items: [{
          key: "import:11111111-1111-4111-8111-111111111111:9",
          source_row: 9,
          official_code: "",
          provider: "Offiho",
          name: "Importada",
          description: "",
          dimension: "",
          quantity: "1",
          unit_price: "80.00",
          source_currency: "USD",
          image_url: "",
        }],
      }, "USD", "Offiho", [{id: "section-1", concept: "Sala"}]).lines[0];
      const base = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "",
          direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: [{id: "section-1", concept: "Sala"}],
      };
      const importedPayload = model.serializeProject({...base, lines: [imported]});
      let catalogError = "";
      try {
        model.serializeProject({...base, lines: [model.createMixedCartLine({
          catalog: "tarkett",
          identity: {code: "TARK-1"},
          officialCode: "",
          provider: "Tarkett",
          quantity: "1",
          quantityRules: {min: "1", step: "1", maxDecimals: 0,
            max: "1000000", integer: true},
          snapshot: {name: "Chair", code: "", image_url: "", unit: "PZA",
            availability: "", configuration: "", warnings: []},
          sectionId: "section-1",
        })]});
      } catch (error) {
        catalogError = error.message;
      }
      console.log(JSON.stringify({
        importedCode: importedPayload.lines[0].official_code,
        catalogError,
      }));
    """)
    assert result == {
        "importedCode": "",
        "catalogError": "Codigo oficial requerido",
    }


def test_catalog_upsert_assigns_consecutive_positions_within_the_same_section():
    result = run_js(r"""
      const makeLine = (code) => model.createMixedCartLine({
        catalog: "sunon",
        identity: {
          internal_id: `sunon:${code}`,
          base_option_id: "",
          add_on_option_ids: [],
        },
        officialCode: code,
        provider: "Sunon",
        quantity: "1",
        quantityRules: {
          min: "1", step: "1", maxDecimals: 0,
          max: "1000000", integer: true,
        },
        snapshot: {
          name: code, code, image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: [],
        },
        sectionId: "section-1",
      });
      let lines = model.upsertMixedCartLine([], makeLine("CHAIR-1"));
      lines = model.upsertMixedCartLine(lines, makeLine("CHAIR-2"));
      console.log(JSON.stringify(lines.map((line) => line.position)));
    """)
    assert result == [0, 1]


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


def test_project_quote_preferences_round_trip_and_legacy_defaults():
    result = run_js(r"""
      const makeState = (quoteFields) => ({
        quoteFields,
        sections: [{id: "section-1", concept: "Reception"}],
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
      });
      const legacyFields = {
        proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
        razon_social: "", quote_currency: "MXN", descuento: "40",
      };
      const selected = model.serializeProject(makeState({
        ...legacyFields,
        template: "sunon_cdmx_v1c",
        description_language: "en",
      }));
      const reopened = model.hydrateProject(selected);
      const legacy = model.serializeProject(makeState(legacyFields));
      const legacyReopened = model.hydrateProject(legacy);
      console.log(JSON.stringify({
        selectedPayload: selected.quote_fields,
        selectedReopened: reopened.quoteFields,
        legacyPayload: legacy.quote_fields,
        legacyReopened: legacyReopened.quoteFields,
      }));
    """)
    for fields in (result["selectedPayload"], result["selectedReopened"]):
        assert fields["template"] == "sunon_cdmx_v1c"
        assert fields["description_language"] == "en"
    for fields in (result["legacyPayload"], result["legacyReopened"]):
        assert fields["template"] == "official_2026_gdl"
        assert fields["description_language"] == "es"


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


def catalog_project_payload(quantity="1.0000001"):
    return {
        "schema_version": 1,
        "quote_fields": {
            "proyecto": "", "cliente": "", "correo": "", "telefono": "",
            "direccion": "", "razon_social": "", "quote_currency": "MXN", "descuento": "40",
        },
        "sections": [{"section_id": "section-1", "concept": "RecepciÃ³n", "position": 0}],
        "lines": [{
            "line_id": "11111111-1111-4111-8111-111111111111",
            "role": "principal", "section_id": "section-1", "parent_line_id": None,
            "position": 0, "quantity": quantity, "source": "catalog", "catalog": "sunon",
            "official_code": "CHAIR-1", "display_cache": {
                "name": "Silla", "code": "CHAIR-1", "image_url": "",
            },
            "identity": {
                "internal_id": "sunon:chair", "base_option_id": "", "add_on_option_ids": [],
            },
            "quantity_rules_cache": {
                "min": "1", "step": "1", "maxDecimals": 0, "max": "1000000", "integer": True,
            },
        }],
    }


def test_cached_catalog_persisted_quantity_uses_backend_decimal_contract():
    payload = normalize_project_payload(catalog_project_payload())
    assert payload["lines"][0]["quantity"] == "1.0000001"

    result = run_js(f"""
      const payload = {json.dumps(payload)};
      const reopened = model.hydrateProject(payload);
      const saved = model.serializeProject(reopened);
      console.log(JSON.stringify({{
        quantity: reopened.lines[0].quantity,
        persistedFallback: reopened.lines[0].projectQuantityFallback,
        savedQuantity: saved.lines[0].quantity,
        savedRules: saved.lines[0].quantity_rules_cache,
      }}));
    """)
    assert result == {
        "quantity": "1.0000001",
        "persistedFallback": True,
        "savedQuantity": "1.0000001",
        "savedRules": {
            "min": "1", "step": "1", "maxDecimals": 0, "max": "1000000", "integer": True,
        },
    }


@pytest.mark.parametrize("quantity", ["0", "-1", "1000000.0000001"])
def test_cached_catalog_persisted_quantity_rejects_backend_invalid_bounds(quantity):
    payload = catalog_project_payload(quantity)
    with pytest.raises(ValueError):
        normalize_project_payload(payload)

    result = run_js(f"""
      const payload = {json.dumps(payload)};
      try {{ model.hydrateProject(payload); console.log(JSON.stringify("accepted")); }}
      catch {{ console.log(JSON.stringify("rejected")); }}
    """)
    assert result == "rejected"


def test_backend_normalized_long_fixed_decimals_round_trip_for_catalog_and_imported():
    catalog_payload = normalize_project_payload(catalog_project_payload("1e-31"))
    imported_payload = normalize_project_payload(imported_project_payload("1e-31", "1e-31"))
    catalog_quantity = catalog_payload["lines"][0]["quantity"]
    imported_quantity = imported_payload["lines"][0]["quantity"]
    imported_price = imported_payload["lines"][0]["unit_price"]
    assert len(catalog_quantity) > 32
    assert catalog_quantity == imported_quantity == imported_price

    result = run_js(f"""
      const catalog = {json.dumps(catalog_payload)};
      const imported = {json.dumps(imported_payload)};
      const catalogSaved = model.serializeProject(model.hydrateProject(catalog));
      const importedSaved = model.serializeProject(model.hydrateProject(imported));
      console.log(JSON.stringify({{
        catalog: catalogSaved.lines[0].quantity,
        importedQuantity: importedSaved.lines[0].quantity,
        importedPrice: importedSaved.lines[0].unit_price,
      }}));
    """)
    assert result == {
        "catalog": catalog_quantity,
        "importedQuantity": imported_quantity,
        "importedPrice": imported_price,
    }


def imported_project_payload(quantity="1.0000001", unit_price="0.0000001"):
    return {
        "schema_version": 1,
        "quote_fields": {
            "proyecto": "", "cliente": "", "correo": "", "telefono": "",
            "direccion": "", "razon_social": "", "quote_currency": "MXN", "descuento": "40",
        },
        "sections": [{"section_id": "section-1", "concept": "RecepciÃ³n", "position": 0}],
        "lines": [{
            "line_id": "11111111-1111-4111-8111-111111111111",
            "role": "principal", "section_id": "section-1", "parent_line_id": None,
            "position": 0, "quantity": quantity, "source": "imported",
            "official_code": "OHE-405", "display_cache": {
                "name": "Silla importada", "code": "OHE-405", "image_url": "",
            },
            "import_id": "22222222-2222-4222-8222-222222222222", "source_row": 9,
            "source_currency": "USD", "provider": "Offiho", "name": "Silla importada",
            "description": "", "dimension": "", "unit_price": unit_price,
            "image_asset_key": "", "source_asset_key": "",
        }],
    }


def test_imported_persisted_decimals_match_backend_and_round_trip_unchanged():
    payload = normalize_project_payload(imported_project_payload())
    assert payload["lines"][0]["quantity"] == "1.0000001"
    assert payload["lines"][0]["unit_price"] == "0.0000001"

    result = run_js(f"""
      const payload = {json.dumps(payload)};
      const reopened = model.hydrateProject(payload);
      const saved = model.serializeProject(reopened);
      console.log(JSON.stringify({{
        quantity: reopened.lines[0].quantity,
        unitPrice: reopened.lines[0].edits.unitPrice,
        savedQuantity: saved.lines[0].quantity,
        savedUnitPrice: saved.lines[0].unit_price,
      }}));
    """)
    assert result == {
        "quantity": "1.0000001",
        "unitPrice": "0.0000001",
        "savedQuantity": "1.0000001",
        "savedUnitPrice": "0.0000001",
    }


def test_imported_project_without_official_code_reopens_and_round_trips():
    payload = imported_project_payload()
    payload["lines"][0]["official_code"] = ""
    payload["lines"][0]["display_cache"]["code"] = ""
    payload["lines"][0]["image_asset_key"] = (
        "projects/7/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/images/row-9.png"
    )
    payload["lines"][0]["display_cache"]["image_url"] = (
        "https://storage.example/row-9.png?signed=1"
    )
    payload = normalize_project_payload(payload)

    result = run_js(f"""
      const reopened = model.hydrateProject({json.dumps(payload)});
      const saved = model.serializeProject(reopened);
      console.log(JSON.stringify({{
        reopenedCode: reopened.lines[0].officialCode,
        reopenedImage: reopened.lines[0].snapshot.image_url,
        savedCode: saved.lines[0].official_code,
        savedImage: saved.lines[0].display_cache.image_url,
        source: saved.lines[0].source,
      }}));
    """)

    assert result == {
        "reopenedCode": "",
        "reopenedImage": "https://storage.example/row-9.png?signed=1",
        "savedCode": "",
        "savedImage": "",
        "source": "imported",
    }


@pytest.mark.parametrize(
    ("quantity", "unit_price"),
    [
        ("0", "0"),
        ("-1", "0"),
        ("1000000.0000001", "0"),
        ("1", "-0.0000001"),
        ("1", "NaN"),
        ("1", "Infinity"),
    ],
)
def test_imported_persisted_decimals_reject_backend_invalid_values(quantity, unit_price):
    payload = imported_project_payload(quantity, unit_price)
    with pytest.raises(ValueError):
        normalize_project_payload(payload)

    result = run_js(f"""
      const payload = {json.dumps(payload)};
      try {{ model.hydrateProject(payload); console.log(JSON.stringify("accepted")); }}
      catch {{ console.log(JSON.stringify("rejected")); }}
    """)
    assert result == "rejected"


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


def test_name_only_imported_edit_preserves_persisted_long_decimals():
    payload = normalize_project_payload(imported_project_payload("1e-31", "1e-31"))
    persisted_decimal = payload["lines"][0]["quantity"]

    result = run_js(f"""
      const reopened = model.hydrateProject({json.dumps(payload)});
      const edited = model.updateImportedCartLine(
        reopened.lines, reopened.lines[0].key, {{name: "Silla renombrada"}},
      );
      const saved = model.serializeProject({{...reopened, lines: edited}});
      console.log(JSON.stringify({{
        name: edited[0].edits.name,
        quantity: edited[0].quantity,
        unitPrice: edited[0].edits.unitPrice,
        savedQuantity: saved.lines[0].quantity,
        savedUnitPrice: saved.lines[0].unit_price,
      }}));
    """)

    assert result == {
        "name": "Silla renombrada",
        "quantity": persisted_decimal,
        "unitPrice": persisted_decimal,
        "savedQuantity": persisted_decimal,
        "savedUnitPrice": persisted_decimal,
    }


def test_replace_catalog_line_preserves_persisted_long_quantity_until_edit():
    payload = normalize_project_payload(catalog_project_payload("1e-31"))
    persisted_decimal = payload["lines"][0]["quantity"]

    result = run_js(f"""
      const reopened = model.hydrateProject({json.dumps(payload)});
      const target = {{
        catalog: "alma",
        identity: {{internal_id: "alma:replacement", base_option_id: "", add_on_option_ids: []}},
        officialCode: "NEW-1", provider: "ALMA", quantity: "1",
        quantityRules: {{min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true}},
        snapshot: {{name: "Reemplazo", code: "NEW-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []}},
      }};
      const replaced = model.replaceProjectLine(reopened.lines, reopened.lines[0].lineId, target);
      const saved = model.serializeProject({{...reopened, lines: replaced.lines}});
      console.log(JSON.stringify({{
        quantity: replaced.lines[0].quantity,
        savedQuantity: saved.lines[0].quantity,
        code: replaced.lines[0].officialCode,
      }}));
    """)

    assert result == {
        "quantity": persisted_decimal,
        "savedQuantity": persisted_decimal,
        "code": "NEW-1",
    }


def test_explicit_edits_of_persisted_quantities_remain_interactively_strict():
    catalog_payload = normalize_project_payload(catalog_project_payload("1e-31"))
    imported_payload = normalize_project_payload(imported_project_payload("1e-31", "1e-31"))

    result = run_js(f"""
      const catalog = model.hydrateProject({json.dumps(catalog_payload)}).lines[0];
      const imported = model.hydrateProject({json.dumps(imported_payload)}).lines[0];
      const attempts = [catalog, imported].map((line) => {{
        try {{
          model.updateMixedCartQuantity([line], line.key, "1.0000001");
          return "accepted";
        }} catch {{
          return "rejected";
        }}
      }});
      console.log(JSON.stringify(attempts));
    """)

    assert result == ["rejected", "rejected"]
