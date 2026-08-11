import json
import subprocess
from pathlib import Path


PICKER_PATH = Path("mobiliti_saas/web/src/productPicker.js")
WORKSPACE_PATH = Path("mobiliti_saas/web/src/projectWorkspace.js")
MIXED_CART_PATH = Path("mobiliti_saas/web/src/mixedCart.js")


def run_picker(source):
    script = (
        f'import * as picker from {json.dumps(PICKER_PATH.resolve().as_uri())};\n'
        f"{source}"
    )
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def run_project_selection_flow(source):
    script = (
        f'import * as picker from {json.dumps(PICKER_PATH.resolve().as_uri())};\n'
        f'import * as workspace from {json.dumps(WORKSPACE_PATH.resolve().as_uri())};\n'
        f'import * as cart from {json.dumps(MIXED_CART_PATH.resolve().as_uri())};\n'
        f"{source}"
    )
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_idelika_and_conceptos_are_exposed_after_lauco_with_exact_labels():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    options = run_picker("console.log(JSON.stringify(picker.CATALOG_OPTIONS));")

    assert options[-3:] == [
        {"value": "lauco", "label": "Lauco"},
        {"value": "idelika", "label": "IDÉLIKA"},
        {"value": "conceptos", "label": "Conceptos"},
    ]
    assert main.index('["lauco", "Lauco"') < main.index('["idelika", "IDÉLIKA"')
    assert main.index('["idelika", "IDÉLIKA"') < main.index('["conceptos", "Conceptos"')


def test_project_picker_renders_generic_idelika_and_conceptos_product_details():
    dialog = Path("mobiliti_saas/web/src/ProductPickerDialog.jsx").read_text(encoding="utf-8")
    result = run_picker(r"""
      const configurable = {
        catalog: "conceptos", official_code: "CON-17", base_currency: "MXN",
        identity: {internal_id: "conceptos:sofa", base_option_id: "", add_on_option_ids: []},
        snapshot: {name: "Sofá modular", image_url: "https://example.test/sofa.png", availability: "Sobre pedido"},
        base_options: [{id: "tela-a", name: "Tela A", price_net: "1234.5"}],
      };
      const pending = {
        catalog: "idelika", official_code: "", display_key: "idelika:mesa-01", base_currency: "MXN",
        quotable: true,
        identity: {internal_id: "idelika:mesa-01", base_option_id: "", add_on_option_ids: []},
        snapshot: {name: "Mesa", code: "", image_url: "", availability: "Por confirmar", warnings: ["Código por verificar", "Precio por confirmar"]},
        price_net: null,
      };
      const selection = picker.createCanonicalProductSelection(configurable, "tela-a");
      console.log(JSON.stringify({
        configuredPrice: picker.productPriceLabel(configurable, "tela-a"),
        pendingPrice: picker.productPriceLabel(pending),
        configurationLabel: picker.productBaseConfigurationLabel(configurable),
        selectedCatalog: selection.catalog,
        selectedConfiguration: selection.snapshot.configuration,
      }));
    """)

    assert result == {
        "configuredPrice": "MXN 1,234.50",
        "pendingPrice": "Precio por confirmar",
        "configurationLabel": "Configuración base",
        "selectedCatalog": "conceptos",
        "selectedConfiguration": "Tela A",
    }
    for marker in (
        "project-picker-result-image",
        "item.official_code",
        "project-picker-warnings",
        "productBaseConfigurationLabel",
        "Disponibilidad:",
        "productPriceLabel",
        "disabled={!canConfirm}",
    ):
        assert marker in dialog
    assert "const canConfirm = canConfirmProductSelection(selected, selectedBaseOptionId);" in dialog
    assert 'catalog === "idelika"' not in dialog
    assert 'catalog === "conceptos"' not in dialog


def test_project_picker_selection_reaches_cart_for_pending_idelika_and_configurable_conceptos():
    dialog = Path("mobiliti_saas/web/src/ProductPickerDialog.jsx").read_text(encoding="utf-8")
    result = run_project_selection_flow(r"""
      const pending = {
        catalog: "idelika", official_code: "", display_key: "idelika:mesa-01", base_currency: "MXN",
        quotable: true,
        identity: {internal_id: "idelika:mesa-01", base_option_id: "", add_on_option_ids: []},
        price_net: null,
        snapshot: {name: "Mesa IDÉLIKA", code: "", image_url: "", availability: "Por confirmar", warnings: ["Código por verificar", "Precio por confirmar"]},
      };
      const configurable = {
        catalog: "conceptos", official_code: "CON-SOFA-02", base_currency: "MXN",
        quotable: true,
        identity: {internal_id: "conceptos:sofa-02", base_option_id: "", add_on_option_ids: []},
        base_options: [{id: "tela-b", name: "Tela B", price_net: "4500"}],
        add_on_options: [{id: "cojin-extra", name: "Cojín extra", family: "cojin", price_net: "250", compatible_base_option_ids: []}],
        snapshot: {name: "Sofá Conceptos", image_url: "", availability: "Sobre pedido", warnings: []},
      };
      const pendingSelection = picker.createCanonicalProductSelection(pending);
      const configurableSelection = picker.createCanonicalProductSelection(configurable, "tela-b", ["cojin-extra"]);
      const invalidSelection = {...pendingSelection, identity: {...pendingSelection.identity, internal_id: ""}, display_key: ""};
      let invalidTarget = "";
      try { workspace.createProjectPickerTarget(invalidSelection); } catch (error) { invalidTarget = error.message; }
      const pendingLine = cart.createMixedCartLine({...workspace.createProjectPickerTarget(pendingSelection), sectionId: "section-1"});
      const configurableLine = cart.createMixedCartLine({...workspace.createProjectPickerTarget(configurableSelection), sectionId: "section-1"});
      console.log(JSON.stringify({
        pendingPrice: picker.productPriceLabel(pending),
        pendingCanConfirm: picker.canConfirmProductSelection(pending, ""),
        missingQuotableCanConfirm: picker.canConfirmProductSelection({...configurable, quotable: undefined}, "tela-b"),
        falseQuotableCanConfirm: picker.canConfirmProductSelection({...configurable, quotable: false}, "tela-b"),
        invalidCanConfirm: picker.canConfirmProductSelection(invalidSelection, ""),
        invalidTarget,
        pending: {catalog: pendingLine.catalog, code: pendingLine.snapshot.code, displayKey: pendingLine.snapshot.displayKey, warnings: pendingLine.snapshot.warnings},
        configurable: {catalog: configurableLine.catalog, identity: configurableLine.identity, configuration: configurableLine.snapshot.configuration},
      }));
    """)

    assert result == {
        "pendingPrice": "Precio por confirmar",
        "pendingCanConfirm": True,
        "missingQuotableCanConfirm": False,
        "falseQuotableCanConfirm": False,
        "invalidCanConfirm": False,
        "invalidTarget": "SelecciÃ³n de catÃ¡logo invÃ¡lida",
        "pending": {
            "catalog": "idelika",
            "code": "",
            "displayKey": "idelika:mesa-01",
            "warnings": ["Código por verificar", "Precio por confirmar"],
        },
        "configurable": {
            "catalog": "conceptos",
            "identity": {
                "internal_id": "conceptos:sofa-02",
                "base_option_id": "tela-b",
                "add_on_option_ids": ["cojin-extra"],
            },
            "configuration": "Tela B + Cojín extra",
        },
    }
    assert "canConfirmProductSelection" in dialog


def test_no_sku_technical_identity_persists_hydrates_matches_and_replaces():
    result = run_project_selection_flow(r"""
      const pending = {
        catalog: "idelika", official_code: "", display_key: "idelika:school:mesa-01",
        identity: {internal_id: "idelika:school:mesa-01", base_option_id: "", add_on_option_ids: []},
        snapshot: {name: "Mesa escolar", code: "", image_url: "", warnings: ["Código por verificar", "Precio por confirmar"]},
      };
      const replacement = {
        catalog: "conceptos", official_code: "CON-2", display_key: "conceptos:sofa-02",
        identity: {internal_id: "conceptos:sofa-02", base_option_id: "base-a", add_on_option_ids: []},
        snapshot: {name: "Sofá", code: "CON-2", image_url: "", warnings: []},
      };
      const line = cart.createMixedCartLine({
        ...workspace.createProjectPickerTarget(picker.createCanonicalProductSelection(pending)),
        sectionId: "section-1", position: 0,
      });
      const quoteFields = {proyecto: "", cliente: "", correo: "", telefono: "", direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40", template: "official_2026_gdl", description_language: "es"};
      const serialized = cart.serializeProject({quoteFields, sections: [{id: "section-1", concept: "Principal"}], lines: [line]});
      const hydrated = cart.hydrateProject(serialized);
      const selector = cart.projectLineSelector(hydrated.lines[0]);
      const replaced = cart.replaceAllProjectLines(
        hydrated.lines,
        selector,
        workspace.createProjectPickerTarget(picker.createCanonicalProductSelection(replacement)),
      );
      console.log(JSON.stringify({
        persistedCode: serialized.lines[0].official_code,
        persistedIdentity: serialized.lines[0].identity.internal_id,
        hydratedCode: hydrated.lines[0].officialCode,
        hydratedDisplayKey: hydrated.lines[0].snapshot.displayKey,
        hasMatch: cart.projectLineHasMatchIdentity(hydrated.lines[0]),
        affected: replaced.summary.affected,
        replacementIdentity: replaced.lines[0].identity.internal_id,
      }));
    """)

    assert result == {
        "persistedCode": "",
        "persistedIdentity": "idelika:school:mesa-01",
        "hydratedCode": "",
        "hydratedDisplayKey": "idelika:school:mesa-01",
        "hasMatch": True,
        "affected": 1,
        "replacementIdentity": "conceptos:sofa-02",
    }
