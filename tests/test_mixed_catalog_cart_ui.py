import json
import re
import subprocess
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.mixed_catalog import (
    mixed_cart_key as python_mixed_cart_key,
    preflight_mixed_catalog_items,
)


MODULE_PATH = Path("mobiliti_saas/web/src/mixedCart.js")
EXPORTS = (
    "MIXED_CATALOGS",
    "MAX_MIXED_CART_SECTIONS",
    "mixedCartKey",
    "createMixedCartLine",
    "createInitialMixedCartSections",
    "mixedCartSectionLabel",
    "closeMixedCartSection",
    "renameMixedCartSection",
    "mergeMixedCartSection",
    "moveMixedCartLine",
    "moveMixedCartLineToSection",
    "compactMixedCartSections",
    "groupMixedCartLines",
    "toMixedQuoteSections",
    "createMixedQuoteRequestSnapshot",
    "validateLineQuantity",
    "lineNeedsAvailabilityConfirmation",
    "lineNeedsPriceConfirmation",
    "upsertMixedCartLine",
    "updateMixedCartQuantity",
    "removeMixedCartLine",
    "toMixedQuoteItem",
    "createImportedCartBundle",
    "replaceImportedCartBundle",
    "updateImportedCartLine",
    "serializeProject",
    "hydrateProject",
)


def run_mixed_cart_js(source):
    module_url = MODULE_PATH.resolve().as_uri()
    script = (
        f'import {{ {", ".join(EXPORTS)} }} from {json.dumps(module_url)};\n'
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


def javascript_function(source, name):
    start = re.search(rf"(?:async\s+)?function\s+{name}\s*\([^)]*\)\s*\{{", source)
    assert start, f"Missing JavaScript helper: {name}"
    depth = 0
    body_start = start.end() - 1
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start.start() : index + 1]
    raise AssertionError(f"Unclosed JavaScript helper: {name}")


def run_ui_helper_js(path, names, source):
    module = Path(path).read_text(encoding="utf-8")
    helpers = "\n".join(javascript_function(module, name) for name in names)
    return run_mixed_cart_js(f"{helpers}\n{source}")


def supplier_line_source(
    *,
    catalog="sonara",
    internal_id="sonara:panel",
    quantity="1",
    rules=None,
    warnings=None,
):
    rules = rules or {
        "min": "1",
        "step": "1",
        "maxDecimals": 0,
        "max": "1000000",
        "integer": True,
    }
    warnings = warnings or []
    return json.dumps(
        {
            "catalog": catalog,
            "identity": {
                "internal_id": internal_id,
                "base_option_id": "",
                "add_on_option_ids": [],
            },
            "quantity": quantity,
            "quantityRules": rules,
            "snapshot": {
                "name": "Panel",
                "code": "Codigo por verificar",
                "image_url": "",
                "unit": "PZA",
                "availability": "",
                "configuration": "",
                "warnings": warnings,
            },
        }
    )


def test_manual_sections_label_reorder_move_merge_and_serialize():
    result = run_mixed_cart_js(
        r"""
      const makeLine = (internalId, sectionId) => createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: internalId, base_option_id: "", add_on_option_ids: []},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: internalId, code: internalId, image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId,
      });

      let sections = createInitialMixedCartSections();
      let lines = [makeLine("alma:a", sections[0].id), makeLine("alma:b", sections[0].id)];
      sections = closeMixedCartSection(sections, lines);
      sections = renameMixedCartSection(sections, sections[1].id, "Privados");
      lines = [...lines, makeLine("alma:c", sections[1].id)];
      lines = moveMixedCartLine(lines, lines[1].key, "up");
      const firstSectionMovedKey = lines[1].key;
      lines = moveMixedCartLineToSection(lines, sections, firstSectionMovedKey, sections[1].id);
      const serialized = toMixedQuoteSections(sections, lines);
      const labels = sections.map(mixedCartSectionLabel);

      const merged = mergeMixedCartSection(sections, lines, sections[1].id);
      const withEmptyTail = closeMixedCartSection(merged.sections, merged.lines);
      const compacted = compactMixedCartSections(withEmptyTail, merged.lines);
      const serializedWithoutEmptyTail = toMixedQuoteSections(compacted.sections, compacted.lines);

      let emptyCloseError = "";
      try {
        closeMixedCartSection(createInitialMixedCartSections(), []);
      } catch (error) {
        emptyCloseError = error.message;
      }
      const limitSections = Array.from({length: 32}, (_, index) => ({
        id: `section-${index + 1}`,
        concept: `Espacio ${index + 1}`,
      }));
      let limitCloseError = "";
      try {
        closeMixedCartSection(limitSections, [{sectionId: "section-32"}]);
      } catch (error) {
        limitCloseError = error.message;
      }

      console.log(JSON.stringify({
        labels,
        serialized,
        mergedSectionCount: merged.sections.length,
        mergedOrder: merged.lines.map((line) => line.snapshot.name),
        compactedSectionCount: compacted.sections.length,
        serializedWithoutEmptyTail,
        emptyCloseError,
        limitCloseError,
        maxSections: MAX_MIXED_CART_SECTIONS,
      }));
        """
    )

    assert result["labels"] == ["1-Recepción", "2-Privados"]
    assert [section["title"] for section in result["serialized"]] == [
        "Recepción",
        "Privados",
    ]
    assert [len(section["item_keys"]) for section in result["serialized"]] == [1, 2]
    assert result["mergedSectionCount"] == 1
    assert result["mergedOrder"] == ["alma:b", "alma:c", "alma:a"]
    assert result["compactedSectionCount"] == 2
    assert len(result["serializedWithoutEmptyTail"]) == 1
    assert "producto" in result["emptyCloseError"].lower()
    assert result["limitCloseError"] == "Limite de 32 secciones alcanzado"
    assert result["maxSections"] == 32


def test_imported_bundle_supports_35_sections_and_137_products_through_project_round_trip():
    result = run_mixed_cart_js(
        r"""
      const makePreview = (importId, prefix) => {
        const items = Array.from({length: 137}, (_, index) => {
          const sourceRow = index + 9;
          return {
            key: `import:${importId}:${sourceRow}`,
            source_row: sourceRow,
            name: `${prefix} Producto ${sourceRow}`,
            description: "",
            dimension: "",
            quantity: "1",
            unit_price: "10",
            image_url: "",
          };
        });
        return {
          import_id: importId,
          original_filename: `${prefix}.xlsx`,
          provider: "Proveedor",
          source_currency: "USD",
          sections: Array.from({length: 35}, (_, index) => ({
            id: `import-section-${index + 1}`,
            title: `${prefix} Seccion ${index + 1}`,
            item_keys: items.slice(index * 4, (index + 1) * 4).map((item) => item.key),
          })),
          items,
        };
      };
      const firstImportId = "11111111-1111-4111-8111-111111111111";
      const secondImportId = "22222222-2222-4222-8222-222222222222";
      const firstBundle = createImportedCartBundle(
        makePreview(firstImportId, "Primera importacion"),
        "USD",
        "Proveedor",
        createInitialMixedCartSections(),
      );
      const firstState = replaceImportedCartBundle(
        [],
        createInitialMixedCartSections(),
        firstBundle,
      );
      const secondBundle = createImportedCartBundle(
        makePreview(secondImportId, "Segunda importacion"),
        "USD",
        "Proveedor",
        firstState.sections,
      );
      const replaced = replaceImportedCartBundle(
        firstState.lines,
        firstState.sections,
        secondBundle,
      );
      const quoteFields = {
        proyecto: "Sales del Valle",
        cliente: "Cliente",
        correo: "cliente@example.com",
        telefono: "555-0100",
        direccion: "Direccion",
        razon_social: "Empresa",
        quote_currency: "USD",
        descuento: "0",
      };
      const serialized = serializeProject({quoteFields, ...replaced});
      const hydrated = hydrateProject(serialized);
      const coverage = (state) => ({
        sectionIds: new Set(state.sections.map((section) => section.id)).size,
        lineSectionIds: new Set(state.lines.map((line) => line.sectionId)).size,
        linesPerSection: state.sections.map((section) => state.lines.filter((line) => line.sectionId === section.id).length),
      });
      console.log(JSON.stringify({
        first: {sections: firstState.sections.length, lines: firstState.lines.length, coverage: coverage(firstState)},
        secondBundle: {sections: secondBundle.sections.length, lines: secondBundle.lines.length},
        replaced: {sections: replaced.sections.length, lines: replaced.lines.length, coverage: coverage(replaced)},
        obsoleteSections: firstState.sections
          .map((section) => section.id)
          .filter((id) => replaced.sections.some((section) => section.id === id)),
        importIds: [...new Set(replaced.lines.map((line) => line.importId))],
        serialized: {sections: serialized.sections.length, lines: serialized.lines.length},
        hydrated: {
          sections: hydrated.sections.length,
          lines: hydrated.lines.length,
          coverage: coverage(hydrated),
          lastSection: hydrated.sections.at(-1),
          lastProduct: hydrated.lines.at(-1),
        },
      }));
        """
    )

    assert result["replaced"]["sections"] == 35, result["replaced"]
    for state in (result["first"], result["replaced"], result["hydrated"]):
        assert state["sections"] == 35
        assert state["lines"] == 137
        assert state["coverage"]["sectionIds"] == 35
        assert state["coverage"]["lineSectionIds"] == 35
    assert result["replaced"]["coverage"]["linesPerSection"] == [4] * 34 + [1]
    assert result["hydrated"]["coverage"]["linesPerSection"] == [4] * 34 + [1]
    assert result["obsoleteSections"] == []
    assert result["importIds"] == ["22222222-2222-4222-8222-222222222222"]
    assert result["serialized"] == {"sections": 35, "lines": 137}
    assert result["hydrated"]["lastSection"] == {
        "id": "section-70",
        "concept": "Segunda importacion Seccion 35",
    }
    assert result["hydrated"]["lastProduct"]["sourceRow"] == 145
    assert result["hydrated"]["lastProduct"]["edits"]["name"] == "Segunda importacion Producto 145"


def test_group_mixed_cart_lines_preserves_order_and_rejects_unknown_sections():
    result = run_mixed_cart_js(
        r"""
      const sections = Array.from({length: 20}, (_, index) => ({
        id: `section-${index + 1}`,
        concept: `Espacio ${index + 1}`,
      }));
      const lines = Array.from({length: 700}, (_, index) => ({
        key: `line-${index}`,
        sectionId: sections[index % sections.length].id,
      }));
      const grouped = groupMixedCartLines(sections, lines);
      let unknownError = "";
      try {
        groupMixedCartLines(sections, [...lines, {key: "unknown", sectionId: "section-999"}]);
      } catch (error) {
        unknownError = error.message;
      }
      console.log(JSON.stringify({
        counts: sections.map((section) => grouped.get(section.id).length),
        firstKeys: sections.slice(0, 3).map((section) => grouped.get(section.id)[0].key),
        unknownError,
      }));
        """
    )

    assert result["counts"] == [35] * 20
    assert result["firstKeys"] == ["line-0", "line-1", "line-2"]
    assert result["unknownError"] == "Seccion de producto invalida"


def test_mixed_request_snapshot_is_compact_and_deeply_frozen():
    result = run_mixed_cart_js(
        r"""
      const sections = [{id: "section-1", concept: "Recepcion"}];
      const line = createMixedCartLine({
        catalog: "tarkett",
        identity: {code: "T-1"},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "100"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1",
      });
      const form = {proyecto: "Original"};
      const snapshot = createMixedQuoteRequestSnapshot(form, sections, [line]);
      form.proyecto = "Mutado";
      sections[0].concept = "Mutada";
      line.quantity = "2";
      console.log(JSON.stringify({
        snapshot,
        frozen: Object.isFrozen(snapshot)
          && Object.isFrozen(snapshot.items)
          && Object.isFrozen(snapshot.items[0])
          && Object.isFrozen(snapshot.sections)
          && Object.isFrozen(snapshot.sections[0])
          && Object.isFrozen(snapshot.sections[0].item_keys),
      }));
        """
    )

    assert result["frozen"] is True
    assert result["snapshot"]["proyecto"] == "Original"
    assert result["snapshot"]["items"] == [
        {"catalog": "tarkett", "code": "T-1", "quantity": "1"}
    ]
    assert result["snapshot"]["sections"][0]["id"] == "section-1"
    assert result["snapshot"]["sections"][0]["title"] == "Recepcion"
    assert len(result["snapshot"]["sections"][0]["item_keys"]) == 1
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        result["snapshot"]["sections"][0]["item_keys"][0],
    )


def test_controller_sends_manual_sections_and_resets_them_after_success():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const makeLine = (internalId) => createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: internalId, base_option_id: "", add_on_option_ids: []},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: internalId, code: internalId, image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
      });
      const state = {cart: [], sections: createInitialMixedCartSections(), jobs: []};
      const cartRef = {current: []};
      const sectionsRef = {current: state.sections};
      const bodies = [];
      const controller = createMixedQuoteController({
        cartRef,
        sectionsRef,
        submittingRef: {current: false},
        sessionEpochRef: {current: 0},
        emptyForm: {},
        replaceCart(next) { state.cart = next; cartRef.current = next; },
        replaceSections(next) { state.sections = next; sectionsRef.current = next; },
        setOpen() {}, setForm() {}, getForm() { return {proyecto: "Proyecto"}; },
        setBusy() {}, setError() {}, setNotice() {},
        setJobs(value) { state.jobs = typeof value === "function" ? value(state.jobs) : value; },
        async request(path, options = {}) {
          if (path === "/catalogs/mixed-quote") {
            bodies.push(JSON.parse(options.body));
            return {job: {id: "job-1"}};
          }
          return {cotizaciones: []};
        },
        confirmQuote() { return true; },
        async waitForJobResult(job) { return {...job, status: "completed"}; },
      });

      controller.add(makeLine("alma:a"));
      controller.closeSection();
      controller.renameSection(state.sections[1].id, "Privados");
      controller.add(makeLine("alma:b"));
      await controller.submit({preventDefault() {}});

      console.log(JSON.stringify({
        sections: bodies[0].sections,
        itemCount: bodies[0].items.length,
        cartCount: state.cart.length,
        resetLabels: state.sections.map(mixedCartSectionLabel),
      }));
        """,
    )

    assert [section["title"] for section in result["sections"]] == [
        "Recepción",
        "Privados",
    ]
    assert [len(section["item_keys"]) for section in result["sections"]] == [1, 1]
    assert result["itemCount"] == 2
    assert result["cartCount"] == 0
    assert result["resetLabels"] == ["1-Recepción"]


def test_app_owns_one_mixed_cart_and_one_submit_endpoint():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    supplier = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    drawer = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx")
    assert drawer.is_file()
    assert "const [mixedCart, setMixedCart] = useState([])" in main
    assert main.count('"/catalogs/mixed-quote"') == 1
    assert main.count("`/projects/${encodeURIComponent(projectQuote.id)}/quote`") == 1
    assert 'request("/tarkett/quote"' not in main
    assert 'request("/offiho/quote"' not in main
    assert "/catalogs/${supplier}/quote" not in supplier
    assert main.count("<MixedCartDrawer") == 1
    assert "const [cart, setCart]" not in supplier


def test_all_catalog_views_receive_the_same_add_callback():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert re.search(r"<TarkettView[\s\S]*?onAddCartLine=\{addMixedCartLine\}", main)
    assert re.search(r"<OffihoView[\s\S]*?onAddCartLine=\{addMixedCartLine\}", main)
    assert re.search(r"<SupplierCatalogView[\s\S]*?onAddCartLine=\{addMixedCartLine\}", main)


def test_mixed_drawer_is_accessible_presentational_and_commits_callbacks():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    for marker in (
        'role="dialog"', 'aria-modal="true"', "mixed-cart-overlay", 'event.key === "Escape"',
        'event.key === "Tab"', "onQuantityChange(line.key", "onRemove(line.key)",
        "['MXN', 'USD', 'EUR']", "value={currency}", "Codigo por verificar",
        "quantityDrafts", "validateLineQuantity", "onSubmit(event, committedLines)",
        "line.snapshot.name", "line.snapshot.configuration",
    ):
        assert marker in source
    assert "name={field}" in source
    for field in ("proyecto", "cliente", "correo", "telefono", "direccion", "razon_social"):
        assert f'"{field}"' in source
    assert "request(" not in source
    assert "fetch(" not in source


def test_mixed_drawer_explains_currency_conversion_and_general_discount():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert "Todos los precios se convierten una sola vez a la moneda seleccionada." in source
    assert "Descuento general (%)" in source
    assert "El primer producto controla el descuento de todos los productos en Excel." in source
    assert "Descuento Tarkett y Offiho (%)" not in source
    assert "conservan precio neto sin descuento adicional" not in source


def test_mixed_drawer_exposes_editable_accessible_section_controls():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    for marker in (
        "mixed-cart-section",
        "Concepto de la sección",
        "Cerrar sección y abrir otra",
        "Unir con la anterior",
        "Subir",
        "Bajar",
        "Mover",
        'aria-live="polite"',
        "onRenameSection",
        "onCloseSection",
        "onMergeSection",
        "onMoveLine",
        "onMoveLineToSection",
    ):
        assert marker in source
    assert "request(" not in source
    assert "fetch(" not in source


def test_drawer_groups_lines_once_and_supports_collapsed_sections():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")

    for marker in (
        "groupMixedCartLines",
        "useMemo",
        "collapsedSectionIds",
        "isMixedCartSectionCollapsed",
        "aria-expanded",
        "aria-controls",
        "sectionContentId",
        "sectionLines.length > 50",
    ):
        assert marker in source
    assert "lines.filter((line) => line.sectionId === section.id)" not in source


def test_collapsed_hidden_quantity_draft_is_still_validated_before_submit():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/MixedCartDrawer.jsx",
        ("importedEditorKey", "isMixedCartSectionCollapsed", "submitMixedDrawerDrafts"),
        r"""
      const line = createMixedCartLine({
        catalog: "tarkett", identity: {code: "T-1"}, quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "10"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1",
      });
      let errors = {}; let posts = 0;
      const collapsedByDefault = isMixedCartSectionCollapsed({}, "section-1", 51);
      const manuallyExpanded = isMixedCartSectionCollapsed({"section-1": false}, "section-1", 51);
      const accepted = submitMixedDrawerDrafts({
        event: {preventDefault() {}}, lines: [line], quantityDrafts: {[line.key]: ""},
        setErrors(value) { errors = value; }, focusFirst() {}, onSubmit() { posts += 1; },
      });
      console.log(JSON.stringify({collapsedByDefault, manuallyExpanded, accepted, errors, posts}));
        """,
    )

    assert result["collapsedByDefault"] is True
    assert result["manuallyExpanded"] is False
    assert result["accepted"] is False
    assert result["posts"] == 0
    assert list(result["errors"].values()) == ["Cantidad invalida"]


def test_app_is_the_only_mixed_quote_request_owner():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    drawer = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert main.count('"/catalogs/mixed-quote"') == 1
    assert main.count("`/projects/${encodeURIComponent(projectQuote.id)}/quote`") == 1
    assert "/catalogs/mixed-quote" not in drawer


def test_mixed_cart_session_and_submission_guards_are_explicit():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert "const mixedCartRef = useRef([])" in main
    assert "const mixedQuoteSubmittingRef = useRef(false)" in main
    assert "const mixedQuoteSessionEpochRef = useRef(0)" in main
    assert "if (mixedQuoteSubmittingRef.current)" in main
    assert "mixedQuoteSubmittingRef.current = true" in main
    assert "submissionEpoch !== mixedQuoteSessionEpochRef.current" in main
    assert "createMixedQuoteRequestSnapshot" in main
    assert "body: JSON.stringify(quoteRequest)" in main
    assert "const mixedRequest = useMemo(" in main
    assert "Respuesta de trabajo mixto invalida" in main
    assert "replaceCart([])" in main
    assert "localStorage.setItem(\"mixed" not in main
    assert "sessionStorage.setItem(\"mixed" not in main


def test_memoized_compact_request_does_not_rebuild_for_customer_form_edits():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert re.search(
        r"const mixedRequest = useMemo\(\s*"
        r"\(\) => createMixedQuoteRequestSnapshot\(\s*"
        r"\{\},\s*mixedCartSections,\s*projectMixedQuoteLines\(mixedCart\),\s*\),\s*"
        r"\[mixedCart, mixedCartSections\]",
        main,
    )
    assert "[mixedCart, mixedCartSections, mixedQuote]" not in main
    assert "Object.freeze({ ...mixedQuote, ...mixedRequest })" in main


def test_drawer_focus_trap_does_not_restart_when_parent_callbacks_change():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert "const onCloseRef = useRef(onClose)" in source
    assert "onCloseRef.current = onClose" in source
    assert "handleMixedCartEscape(event, busyRef.current, onCloseRef.current)" in source
    assert re.search(r"window\.addEventListener\(\"keydown\"[\s\S]*?\n\s*}, \[open\]\);", source)
    assert 'className="mixed-cart-overlay"' in source
    assert "disabled={busy}" in source


def test_controller_commits_edits_before_confirmation_and_cancel_retains_state():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const makeLine = (quantity = "1", missingPrice = false) => createMixedCartLine({
        catalog: "offiho", identity: {inventory_key: `OFF-${missingPrice ? "P" : "S"}`},
        quantity,
        quantityRules: {
          min: "0.001", step: "0.001", maxDecimals: 3, max: "1000000",
          warningAt: "1", confirmOnInsufficient: true,
          confirmOnMissingPrice: missingPrice
        },
        snapshot: {name: "Offiho", code: "OFF", image_url: "", unit: "PZA",
          availability: "1", configuration: "", warnings: []}
      });
      const makeHarness = () => {
        const state = {cart: [], form: {proyecto: "Inicial"}, open: false, busy: false,
          error: "", notice: "", jobs: []};
        const cartRef = {current: []}; const submittingRef = {current: false};
        const sessionEpochRef = {current: 0}; const requests = []; const confirms = [];
        const controller = createMixedQuoteController({
          cartRef, submittingRef, sessionEpochRef, emptyForm: {proyecto: ""},
          replaceCart(next) { state.cart = next; cartRef.current = next; },
          setOpen(value) { state.open = value; },
          setForm(value) { state.form = typeof value === "function" ? value(state.form) : value; },
          getForm() { return state.form; }, setBusy(value) { state.busy = value; },
          setError(value) { state.error = value; }, setNotice(value) { state.notice = value; },
          setJobs(value) { state.jobs = typeof value === "function" ? value(state.jobs) : value; },
          async request(path) { requests.push(path); return path === "/cotizaciones" ? {cotizaciones: []} : {job: {id: "job-1"}}; },
          confirmQuote(message) { confirms.push(message); return false; }
        });
        return {state, requests, confirms, controller};
      };
      const availability = makeHarness();
      const stockLine = makeLine();
      availability.controller.add(stockLine);
      availability.controller.updateField("proyecto", "Proyecto retenido");
      await availability.controller.submit({preventDefault() {}}, [{...stockLine, quantity: "2"}]);

      const price = makeHarness();
      const priceLine = makeLine("1", true);
      price.controller.add(priceLine);
      await price.controller.submit({preventDefault() {}}, [priceLine]);
      console.log(JSON.stringify({
        availability: {
          postCount: availability.requests.filter(path => path === "/catalogs/mixed-quote").length,
          quantity: availability.state.cart[0].quantity,
          proyecto: availability.state.form.proyecto,
          confirmation: availability.confirms[0]
        },
        price: {
          postCount: price.requests.filter(path => path === "/catalogs/mixed-quote").length,
          confirmation: price.confirms[0]
        }
      }));
    """,
    )
    assert result["availability"]["postCount"] == 0
    assert result["availability"]["quantity"] == "2"
    assert result["availability"]["proyecto"] == "Proyecto retenido"
    assert "Hay 1 producto(s)" in result["availability"]["confirmation"]
    assert result["price"]["postCount"] == 0
    assert "y 1 producto(s) con precio por confirmar" in result["price"]["confirmation"]


def test_controller_locks_mutations_deduplicates_submit_and_keeps_malformed_response_state():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
      const line = createMixedCartLine({catalog: "tarkett", identity: {code: "T-1"}, quantity: "1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "10"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2", availability: "10", configuration: "", warnings: []}});
      const harness = (mixedResponse) => {
        const state = {cart: [], form: {proyecto: "Retenido"}, open: false, busy: false, error: "", notice: "", jobs: []};
        const cartRef = {current: []}; const submittingRef = {current: false}; const sessionEpochRef = {current: 0};
        const requests = [];
        const controller = createMixedQuoteController({cartRef, submittingRef, sessionEpochRef, emptyForm: {proyecto: ""},
          replaceCart(next) { state.cart = next; cartRef.current = next; }, setOpen(value) { state.open = value; },
          setForm(value) { state.form = typeof value === "function" ? value(state.form) : value; }, getForm() { return state.form; },
          setBusy(value) { state.busy = value; }, setError(value) { state.error = value; }, setNotice(value) { state.notice = value; },
          setJobs(value) { state.jobs = typeof value === "function" ? value(state.jobs) : value; },
          request(path) { requests.push(path); return path === "/catalogs/mixed-quote" ? mixedResponse : Promise.resolve({cotizaciones: state.jobs}); },
          confirmQuote() { return true; }});
        return {state, cartRef, requests, controller};
      };

      const pending = deferred(); const locked = harness(pending.promise); locked.controller.add(line);
      const first = locked.controller.submit({preventDefault() {}});
      const second = locked.controller.submit({preventDefault() {}});
      const before = JSON.stringify(locked.cartRef.current);
      const busyAdd = locked.controller.add({...line, quantity: "2"});
      let editError = ""; let removeError = "";
      try { locked.controller.update(line.key, "2"); } catch (error) { editError = error.message; }
      try { locked.controller.remove(line.key); } catch (error) { removeError = error.message; }
      const lockedSnapshot = {postCount: locked.requests.filter(path => path === "/catalogs/mixed-quote").length,
        busy: locked.state.busy, busyAdd, open: locked.state.open, error: locked.state.error,
        unchanged: before === JSON.stringify(locked.cartRef.current), editError, removeError};
      pending.resolve({job: {id: "job-ok"}}); await Promise.all([first, second]);

      const rejected = harness(Promise.resolve({job: {id: "unused"}}));
      const rejectedAdd = rejected.controller.add({...line, catalog: "unsupported"});
      const malformed = harness(Promise.resolve({job: {}})); malformed.controller.add(line);
      await malformed.controller.submit({preventDefault() {}});
      console.log(JSON.stringify({locked: lockedSnapshot, finished: {cart: locked.state.cart, notice: locked.state.notice},
        rejected: {accepted: rejectedAdd, open: rejected.state.open, error: rejected.state.error},
        malformed: {error: malformed.state.error, cart: malformed.state.cart, form: malformed.state.form}}));
    """,
    )
    assert result["locked"] == {
        "postCount": 1,
        "busy": True,
        "busyAdd": False,
        "open": True,
        "error": "Espera a que termine la cotizacion en curso",
        "unchanged": True,
        "editError": "Cotizacion en curso",
        "removeError": "Cotizacion en curso",
    }
    assert result["finished"]["cart"] == []
    assert "Cotizacion mixta lista" in result["finished"]["notice"]
    assert result["rejected"] == {
        "accepted": False,
        "open": True,
        "error": "Catalogo mixto no soportado",
    }
    assert result["malformed"]["error"] == "Respuesta de trabajo mixto invalida"
    assert result["malformed"]["cart"][0]["quantity"] == "1"
    assert result["malformed"]["form"]["proyecto"] == "Retenido"


def test_controller_session_reset_ignores_late_logout_and_auth_expiry_responses():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
      const line = code => createMixedCartLine({catalog: "tarkett", identity: {code}, quantity: "1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "10"},
        snapshot: {name: code, code, image_url: "", unit: "M2", availability: "10", configuration: "", warnings: []}});
      const runExit = async label => {
        const pending = deferred();
        const state = {cart: [], form: {proyecto: "Viejo"}, open: false, busy: false, error: "", notice: "", jobs: []};
        const cartRef = {current: []}; const submittingRef = {current: false}; const sessionEpochRef = {current: 0};
        const controller = createMixedQuoteController({cartRef, submittingRef, sessionEpochRef, emptyForm: {proyecto: ""},
          replaceCart(next) { state.cart = next; cartRef.current = next; }, setOpen(value) { state.open = value; },
          setForm(value) { state.form = typeof value === "function" ? value(state.form) : value; }, getForm() { return state.form; },
          setBusy(value) { state.busy = value; }, setError(value) { state.error = value; }, setNotice(value) { state.notice = value; },
          setJobs(value) { state.jobs = typeof value === "function" ? value(state.jobs) : value; },
          request(path) { return path === "/catalogs/mixed-quote" ? pending.promise : Promise.resolve({cotizaciones: []}); },
          confirmQuote() { return true; }});
        controller.add(line(`OLD-${label}`)); const submission = controller.submit({preventDefault() {}});
        controller.resetSession(); controller.add(line(`NEW-${label}`));
        pending.resolve({job: {id: `late-${label}`}}); await submission;
        return {cartCode: state.cart[0].identity.code, jobs: state.jobs, notice: state.notice, busy: state.busy, form: state.form};
      };
      console.log(JSON.stringify({logout: await runExit("logout"), authExpiry: await runExit("auth")}));
    """,
    )
    for exit_state, new_code in ((result["logout"], "NEW-logout"), (result["authExpiry"], "NEW-auth")):
        assert exit_state == {
            "cartCode": new_code,
            "jobs": [],
            "notice": "",
            "busy": False,
            "form": {"proyecto": ""},
        }
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert main.count("resetMixedQuoteSession();") == 2


def test_drawer_invalid_transient_draft_focuses_error_and_never_submits():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/MixedCartDrawer.jsx",
        ("importedEditorKey", "submitMixedDrawerDrafts"),
        r"""
      const line = createMixedCartLine({catalog: "tarkett", identity: {code: "T-1"}, quantity: "1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "10"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2", availability: "10", configuration: "", warnings: []}});
      const drafts = {[line.key]: ""}; let errors = {}; let focused = ""; let posts = 0; let prevented = 0;
      const accepted = submitMixedDrawerDrafts({event: {preventDefault() { prevented += 1; }}, lines: [line],
        quantityDrafts: drafts, setErrors(value) { errors = value; }, focusFirst(key) { focused = key; },
        onSubmit() { posts += 1; }});
      console.log(JSON.stringify({accepted, errors, focused, posts, prevented, draft: drafts[line.key]}));
    """,
    )
    assert result["accepted"] is False
    assert result["posts"] == 0
    assert result["prevented"] == 1
    assert result["draft"] == ""
    assert result["focused"] in result["errors"]
    assert result["errors"][result["focused"]] == "Cantidad invalida"


def test_drawer_escape_handler_executes_only_when_not_busy():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/MixedCartDrawer.jsx",
        ("handleMixedCartEscape",),
        r"""
      const run = busy => { let prevented = 0; let closed = 0;
        const handled = handleMixedCartEscape({key: "Escape", preventDefault() { prevented += 1; }}, busy, () => { closed += 1; });
        return {handled, prevented, closed}; };
      console.log(JSON.stringify({busy: run(true), idle: run(false)}));
    """,
    )
    assert result == {
        "busy": {"handled": False, "prevented": 0, "closed": 0},
        "idle": {"handled": True, "prevented": 1, "closed": 1},
    }
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert "const busyRef = useRef(busy)" in source
    assert "busyRef.current = busy" in source
    assert not re.search(
        r"useEffect\(\(\) => \{\s*busyRef\.current = busy;\s*\}, \[busy\]\);",
        source,
    )
    assert "handleMixedCartEscape(event, busyRef.current" in source
    assert re.search(r"window\.addEventListener\(\"keydown\"[\s\S]*?\n\s*}, \[open\]\);", source)


def test_mixed_cart_keys_are_stable_and_configuration_sensitive():
    result = run_mixed_cart_js(
        """
      const keys = [
        mixedCartKey("tarkett", {code: "25731726"}),
        mixedCartKey("offiho", {inventory_key: "OHE-405 NEGRO ALUFSEN"}),
        mixedCartKey("alma", {
          internal_id: "alma:desk-1", base_option_id: "base-a",
          add_on_option_ids: ["addon-b", "addon-a"]
        }),
        mixedCartKey("sunon", {
          internal_id: "mesa|uno:日本", base_option_id: "base|:azul",
          add_on_option_ids: ["cojín:b", "cojín|a"]
        })
      ];
      console.log(JSON.stringify(keys));
    """
    )
    assert result == [
        "tarkett:25731726",
        "offiho:OHE-405 NEGRO ALUFSEN",
        'alma:["alma:desk-1","base-a",["addon-a","addon-b"]]',
        'sunon:["mesa|uno:日本","base|:azul",["cojín:b","cojín|a"]]',
    ]


def test_mixed_cart_keys_reject_ambiguous_or_unsupported_identities():
    result = run_mixed_cart_js(
        """
      const cases = [
        () => mixedCartKey("unknown", {code: "X"}),
        () => mixedCartKey("tarkett", {code: "  "}),
        () => mixedCartKey("offiho", {}),
        () => mixedCartKey("alma", {internal_id: "x", base_option_id: "", add_on_option_ids: ["a", "a"]}),
        () => mixedCartKey("alma", {internal_id: "x", base_option_id: "", add_on_option_ids: "a"})
      ];
      console.log(JSON.stringify(cases.map(run => {
        try { run(); return "accepted"; } catch (error) { return error.message; }
      })));
    """
    )
    assert result == [
        "Catalogo mixto no soportado",
        "code requerido",
        "inventory_key requerido",
        "Add-ons duplicados",
        "Add-ons invalidos",
    ]


def test_upsert_appends_independent_occurrences_without_float_drift():
    result = run_mixed_cart_js(
        """
      const tarkett = createMixedCartLine({
        catalog: "tarkett", identity: {code: "T-1"}, quantity: "0.1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "5"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2", availability: "", configuration: "", warnings: []}
      });
      const sonara = createMixedCartLine({
        catalog: "sonara", identity: {internal_id: "sonara:panel", base_option_id: "", add_on_option_ids: []},
        quantity: "1", quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Panel", code: "Codigo por verificar", image_url: "", unit: "PZA", availability: "", configuration: "", warnings: ["Codigo por verificar"]}
      });
      let lines = upsertMixedCartLine([], tarkett);
      lines = upsertMixedCartLine(lines, sonara);
      lines = upsertMixedCartLine(lines, {...tarkett, quantity: "0.2"});
      console.log(JSON.stringify({
        quantities: lines.map(line => line.quantity),
        uniqueKeys: new Set(lines.map(line => line.key)).size,
      }));
    """
    )
    assert result == {"quantities": ["0.1", "1", "0.2"], "uniqueKeys": 3}


def test_quantity_precision_is_enforced_per_catalog_without_float_rounding():
    result = run_mixed_cart_js(
        """
      const offiho = createMixedCartLine({
        catalog: "offiho", identity: {inventory_key: "OFF-1"}, quantity: "1.001",
        quantityRules: {min: "0.001", step: "0.001", maxDecimals: 3, max: "1000000"},
        snapshot: {name: "Offiho", code: "OFF-1", image_url: "", unit: "PZA", availability: "", configuration: "Negro", warnings: []}
      });
      let message = "";
      try { updateMixedCartQuantity([offiho], offiho.key, "1.0001"); }
      catch (error) { message = error.message; }
      console.log(JSON.stringify({allowed: offiho.quantity, rejected: message}));
    """
    )
    assert result == {
        "allowed": "1.001",
        "rejected": "Cantidad excede 3 decimales",
    }


def test_visual_supplier_configurations_are_distinct_and_display_named():
    result = run_mixed_cart_js(
        """
      const makeLine = (addOnId, configuration) => createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "base-a", add_on_option_ids: [addOnId]},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Escritorio ALMA", code: "AL-1", image_url: "", unit: "PZA", availability: "Sobre pedido", configuration, warnings: []}
      });
      const lines = [
        makeLine("addon-a", "Base A + Electrificacion A"),
        makeLine("addon-b", "Base A + Pasacables B")
      ];
      console.log(JSON.stringify(lines.map(line => ({
        key: line.key, name: line.snapshot.name,
        configuration: line.snapshot.configuration, serialized: toMixedQuoteItem(line)
      }))));
    """
    )
    assert result[0]["key"] != result[1]["key"]
    assert [row["name"] for row in result] == ["Escritorio ALMA", "Escritorio ALMA"]
    assert [row["configuration"] for row in result] == [
        "Base A + Electrificacion A",
        "Base A + Pasacables B",
    ]
    assert all("configuration" not in row["serialized"] for row in result)


def test_line_shape_is_exact_and_defensively_copies_visual_and_identity_data():
    result = run_mixed_cart_js(
        """
      const identity = {
        internal_id: "alma:desk", base_option_id: "base-a",
        add_on_option_ids: ["b", "a"], ignored: "commercial leak"
      };
      const rules = {min: "1", step: "1", maxDecimals: 0, max: "10", integer: true};
      const warnings = ["visual"];
      const snapshot = {
        name: "Desk", code: "AL-1", image_url: "https://example.test/a.png",
        unit: "PZA", availability: "5", configuration: "x".repeat(2001), warnings,
        unit_price: "1", exchange_rate: "999", product_url: "https://evil.test"
      };
      const line = createMixedCartLine({catalog: "alma", identity, quantity: "2.000000", quantityRules: rules, snapshot});
      identity.add_on_option_ids.push("c"); warnings.push("changed"); rules.max = "2";
      console.log(JSON.stringify({
        lineKeys: Object.keys(line).sort(),
        identity: line.identity,
        originalIdentity: identity.add_on_option_ids,
        quantity: line.quantity,
        rulesMax: line.quantityRules.max,
        snapshotKeys: Object.keys(line.snapshot).sort(),
        snapshotDisplayKey: line.snapshot.displayKey,
        warnings: line.snapshot.warnings,
        configurationLength: line.snapshot.configuration.length
      }));
    """
    )
    assert result == {
        "lineKeys": [
            "catalog",
            "identity",
            "key",
            "lineId",
            "officialCode",
            "parentLineId",
            "position",
            "provider",
            "quantity",
            "quantityMode",
            "quantityRules",
            "role",
            "sectionId",
            "snapshot",
        ],
        "identity": {
            "internal_id": "alma:desk",
            "base_option_id": "base-a",
            "add_on_option_ids": ["a", "b"],
        },
        "originalIdentity": ["b", "a", "c"],
        "quantity": "2",
        "rulesMax": "10",
        "snapshotKeys": [
            "availability",
            "code",
            "configuration",
            "displayKey",
            "image_url",
            "name",
            "unit",
            "warnings",
        ],
        "snapshotDisplayKey": "",
        "warnings": ["visual"],
        "configurationLength": 2000,
    }


@pytest.mark.parametrize(
    ("quantity", "message"),
    [
        ("0.5", "Cantidad menor al minimo"),
        ("1.5", "Cantidad entera requerida"),
        ("2", "Incremento de cantidad invalido"),
        ("11", "Cantidad mayor al maximo permitido"),
        ("1.0000001", "Cantidad invalida"),
    ],
)
def test_quantity_limits_have_stable_errors(quantity, message):
    rules = {
        "min": "1",
        "step": "2",
        "maxDecimals": 6,
        "max": "10",
        "integer": quantity == "1.5",
    }
    source = supplier_line_source(quantity="1", rules=rules)
    result = run_mixed_cart_js(
        f"""
      const line = createMixedCartLine({source});
      let message = "accepted";
      try {{ validateLineQuantity(line, {json.dumps(quantity)}); }}
      catch (error) {{ message = error.message; }}
      console.log(JSON.stringify(message));
    """
    )
    assert result == message


def test_quantity_requires_decimal_strings_and_valid_precision_rules():
    source = supplier_line_source()
    result = run_mixed_cart_js(
        f"""
      const line = createMixedCartLine({source});
      const attempts = [
        () => validateLineQuantity(line, 2),
        () => validateLineQuantity({{...line, quantityRules: {{...line.quantityRules, maxDecimals: 7}}}}, "2")
      ];
      console.log(JSON.stringify(attempts.map(run => {{
        try {{ run(); return "accepted"; }} catch (error) {{ return error.message; }}
      }})));
    """
    )
    assert result == ["Cantidad invalida", "Precision de cantidad invalida"]


def test_commercial_max_is_required_positive_and_enforced_for_all_sources():
    offiho = {
        "catalog": "offiho",
        "identity": {"inventory_key": "OFF-1"},
        "quantity": "1",
        "quantityRules": {
            "min": "0.001",
            "step": "0.001",
            "maxDecimals": 3,
            "max": "1000000",
        },
        "snapshot": {
            "name": "Offiho",
            "code": "OFF-1",
            "image_url": "",
            "unit": "PZA",
            "availability": "",
            "configuration": "",
            "warnings": [],
        },
    }
    supplier = json.loads(supplier_line_source())
    result = run_mixed_cart_js(
        f"""
      const offihoInput = {json.dumps(offiho)};
      const supplierInput = {json.dumps(supplier)};
      const attempts = [
        () => createMixedCartLine({{...supplierInput, quantityRules: {{...supplierInput.quantityRules, max: undefined}}}}),
        () => createMixedCartLine({{...supplierInput, quantityRules: {{...supplierInput.quantityRules, max: "0"}}}}),
        () => createMixedCartLine({{...offihoInput, quantity: "1000000.001"}}),
        () => createMixedCartLine({{...supplierInput, quantity: "1000001"}})
      ];
      console.log(JSON.stringify(attempts.map(run => {{
        try {{ run(); return "accepted"; }} catch (error) {{ return error.message; }}
      }})));
    """
    )
    assert result == [
        "Maximo comercial requerido",
        "Maximo comercial invalido",
        "Cantidad mayor al maximo permitido",
        "Cantidad mayor al maximo permitido",
    ]


def test_confirmation_helpers_are_rule_driven_not_warning_text_driven():
    offiho = {
        "catalog": "offiho",
        "identity": {"inventory_key": "OFF-1"},
        "quantity": "7",
        "quantityRules": {
            "min": "0.001",
            "step": "0.001",
            "maxDecimals": 3,
            "max": "1000000",
            "warningAt": "5",
            "confirmOnInsufficient": True,
            "confirmOnMissingPrice": True,
        },
        "snapshot": {
            "name": "Offiho",
            "code": "OFF-1",
            "image_url": "",
            "unit": "PZA",
            "availability": "5",
            "configuration": "",
            "warnings": ["visual only"],
        },
    }
    warning_only = json.loads(supplier_line_source(warnings=["Precio por confirmar"]))
    out_of_stock = json.loads(supplier_line_source())
    out_of_stock["quantityRules"].update(
        {"warningAt": "0", "confirmOnInsufficient": True}
    )
    result = run_mixed_cart_js(
        f"""
      const offiho = createMixedCartLine({json.dumps(offiho)});
      const warningOnly = createMixedCartLine({json.dumps(warning_only)});
      const outOfStock = createMixedCartLine({json.dumps(out_of_stock)});
      console.log(JSON.stringify({{
        overstockAllowed: offiho.quantity,
        availability: [
          lineNeedsAvailabilityConfirmation(offiho),
          lineNeedsAvailabilityConfirmation(warningOnly),
          lineNeedsAvailabilityConfirmation(outOfStock)
        ],
        price: [lineNeedsPriceConfirmation(offiho), lineNeedsPriceConfirmation(warningOnly)]
      }}));
    """
    )
    assert result == {
        "overstockAllowed": "7",
        "availability": [True, False, True],
        "price": [True, False],
    }


def test_upsert_appends_validated_snapshot_without_mutating_inputs():
    result = run_mixed_cart_js(
        """
      const make = (quantity, max, image, warning) => createMixedCartLine({
        catalog: "offiho", identity: {inventory_key: "OFF-1"}, quantity,
        quantityRules: {
          min: "0.001", step: "0.001", maxDecimals: 3, max,
          warningAt: "1", confirmOnInsufficient: true,
          confirmOnMissingPrice: warning === "new"
        },
        snapshot: {name: "Offiho", code: "OFF-1", image_url: image, unit: "PZA", availability: "1", configuration: "", warnings: [warning]}
      });
      const oldLine = make("1", "100", "old.png", "old");
      const incoming = make("1", "3", "new.png", "new");
      const original = [oldLine];
      const refreshed = upsertMixedCartLine(original, incoming);
      let loweredLimit = "accepted";
      try { upsertMixedCartLine([make("2", "100", "old.png", "old")], make("2", "3", "new.png", "new")); }
      catch (error) { loweredLimit = error.message; }
      incoming.snapshot.warnings.push("caller mutation");
      console.log(JSON.stringify({
        quantity: refreshed[1].quantity,
        max: refreshed[1].quantityRules.max,
        image: refreshed[1].snapshot.image_url,
        warnings: refreshed[1].snapshot.warnings,
        needsAvailability: lineNeedsAvailabilityConfirmation(refreshed[1]),
        needsPrice: lineNeedsPriceConfirmation(refreshed[1]),
        oldUnchanged: original[0].snapshot.image_url,
        loweredLimit
      }));
    """
    )
    assert result == {
        "quantity": "1",
        "max": "3",
        "image": "new.png",
        "warnings": ["new"],
        "needsAvailability": False,
        "needsPrice": True,
        "oldUnchanged": "old.png",
        "loweredLimit": "accepted",
    }


def test_update_and_remove_are_immutable_and_preserve_unrelated_catalog_lines():
    result = run_mixed_cart_js(
        f"""
      const first = createMixedCartLine({supplier_line_source(internal_id="sonara:first")});
      const second = createMixedCartLine({supplier_line_source(catalog="lumbro", internal_id="lumbro:second")});
      const original = [first, second];
      const updated = updateMixedCartQuantity(original, first.key, "2");
      const removed = removeMixedCartLine(updated, first.key);
      let missing = "accepted";
      try {{ updateMixedCartQuantity(original, "missing", "2"); }}
      catch (error) {{ missing = error.message; }}
      console.log(JSON.stringify({{
        original: original.map(line => line.quantity),
        updated: updated.map(line => line.quantity),
        removed: removed.map(line => line.quantity),
        secondPreservedByReference: original[1] === updated[1],
        missing
      }}));
    """
    )
    assert result == {
        "original": ["1", "1"],
        "updated": ["2", "1"],
        "removed": ["1"],
        "secondPreservedByReference": True,
        "missing": "Linea de carrito no encontrada",
    }


def test_mixed_quote_serializer_sends_only_identity_configuration_and_quantity():
    result = run_mixed_cart_js(
        """
      const line = createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "base-a", add_on_option_ids: ["b", "a"]},
        quantity: "2", quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {
          name: "Desk", code: "AL-1", image_url: "https://evil.test/x.png", unit: "PZA",
          availability: "5", configuration: "Base A + Electrificacion A",
          warnings: ["visual"], unit_price: "1", base_currency: "XXX",
          exchange_rate: "999", stock: "999", product_url: "https://evil.test"
        }
      });
      console.log(JSON.stringify({
        item: toMixedQuoteItem(line),
        snapshotKeys: Object.keys(line.snapshot).sort()
      }));
    """
    )
    assert result == {
        "item": {
            "catalog": "alma",
            "internal_id": "alma:desk",
            "quantity": "2",
            "base_option_id": "base-a",
            "add_on_option_ids": ["a", "b"],
        },
        "snapshotKeys": [
            "availability",
            "code",
            "configuration",
            "displayKey",
            "image_url",
            "name",
            "unit",
            "warnings",
        ],
    }


def test_mixed_quote_serializer_has_three_exact_commercial_branches():
    tarkett = {
        "catalog": "tarkett",
        "identity": {"code": "T-1", "ignored": "x"},
        "quantity": "0.5",
        "quantityRules": {
            "min": "0.000001",
            "step": "0.000001",
            "maxDecimals": 6,
            "max": "2",
        },
        "snapshot": {
            "name": "Tarkett",
            "code": "T-1",
            "image_url": "",
            "unit": "M2",
            "availability": "2",
            "configuration": "",
            "warnings": [],
        },
    }
    offiho = {
        "catalog": "offiho",
        "identity": {"inventory_key": "OFF-1", "ignored": "x"},
        "quantity": "2",
        "quantityRules": {
            "min": "0.001",
            "step": "0.001",
            "maxDecimals": 3,
            "max": "10",
        },
        "snapshot": {
            "name": "Offiho",
            "code": "OFF-1",
            "image_url": "",
            "unit": "PZA",
            "availability": "10",
            "configuration": "",
            "warnings": [],
        },
    }
    supplier_without_base = json.loads(
        supplier_line_source(catalog="cr-global", internal_id="cr-global:chair")
    )
    result = run_mixed_cart_js(
        f"""
      const lines = [
        createMixedCartLine({json.dumps(tarkett)}),
        createMixedCartLine({json.dumps(offiho)}),
        createMixedCartLine({json.dumps(supplier_without_base)})
      ];
      console.log(JSON.stringify(lines.map(toMixedQuoteItem)));
    """
    )
    assert result == [
        {"catalog": "tarkett", "code": "T-1", "quantity": "0.5"},
        {"catalog": "offiho", "inventory_key": "OFF-1", "quantity": "2"},
        {
            "catalog": "cr-global",
            "internal_id": "cr-global:chair",
            "quantity": "1",
            "add_on_option_ids": [],
        },
    ]


def test_catalog_list_is_frozen_and_complete():
    result = run_mixed_cart_js(
        """
      let mutation = "accepted";
      try { MIXED_CATALOGS.push("other"); } catch (error) { mutation = error.name; }
      console.log(JSON.stringify({catalogs: MIXED_CATALOGS, mutation}));
    """
    )
    assert result == {
        "catalogs": [
            "tarkett",
            "offiho",
            "cr-global",
            "sonara",
            "sunon",
            "alma",
            "lumbro",
            "jome",
            "lauco",
            "idelika",
            "conceptos",
        ],
        "mutation": "TypeError",
    }


def test_project_picker_keeps_idelika_and_conceptos_on_the_shared_selection_path():
    picker = Path("mobiliti_saas/web/src/ProductPickerDialog.jsx").read_text(encoding="utf-8")

    assert "CATALOG_OPTIONS.map" in picker
    assert "productPriceLabel" in picker
    assert "productBaseConfigurationLabel" in picker
    assert 'catalog === "idelika"' not in picker
    assert 'catalog === "conceptos"' not in picker


def test_sparse_supplier_add_ons_are_rejected_before_key_or_payload_creation():
    result = run_mixed_cart_js(
        """
      const input = (add_on_option_ids) => ({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "", add_on_option_ids},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Desk", code: "AL-1", image_url: "", unit: "PZA", availability: "", configuration: "", warnings: []}
      });
      const partlySparse = ["addon-a", "addon-b"];
      delete partlySparse[1];
      const attempts = [
        () => mixedCartKey("alma", input(new Array(1)).identity),
        () => toMixedQuoteItem(createMixedCartLine(input(partlySparse)))
      ];
      console.log(JSON.stringify(attempts.map(run => {
        try { return {accepted: true, value: run()}; }
        catch (error) { return {accepted: false, message: error.message}; }
      })));
    """
    )
    assert result == [
        {"accepted": False, "message": "Add-on requerido"},
        {"accepted": False, "message": "Add-on requerido"},
    ]
    assert "[null]" not in json.dumps(result)


def test_identity_fields_must_be_own_and_prototype_pollution_is_not_consumed():
    result = run_mixed_cart_js(
        """
      const inheritedRequired = Object.create({code: "T-inherited"});
      const inheritedOptional = Object.create({
        base_option_id: "base-polluted", add_on_option_ids: ["addon-polluted"]
      });
      inheritedOptional.internal_id = "alma:desk";
      const nullPrototype = Object.create(null);
      nullPrototype.code = "T-null";
      const attempts = [
        () => mixedCartKey("tarkett", inheritedRequired),
        () => mixedCartKey("alma", inheritedOptional),
        () => mixedCartKey("tarkett", nullPrototype)
      ];
      const originalBase = Object.getOwnPropertyDescriptor(Object.prototype, "base_option_id");
      const originalAddOns = Object.getOwnPropertyDescriptor(Object.prototype, "add_on_option_ids");
      let pollutionResult;
      try {
        Object.prototype.base_option_id = "base-polluted";
        Object.prototype.add_on_option_ids = ["addon-polluted"];
        pollutionResult = mixedCartKey("alma", {internal_id: "alma:safe"});
      } finally {
        if (originalBase) Object.defineProperty(Object.prototype, "base_option_id", originalBase);
        else delete Object.prototype.base_option_id;
        if (originalAddOns) Object.defineProperty(Object.prototype, "add_on_option_ids", originalAddOns);
        else delete Object.prototype.add_on_option_ids;
      }
      console.log(JSON.stringify({
        attempts: attempts.map(run => {
          try { return {accepted: true, value: run()}; }
          catch (error) { return {accepted: false, message: error.message}; }
        }),
        pollutionResult,
        pollutionCleaned: !Object.hasOwn(Object.prototype, "base_option_id")
          && !Object.hasOwn(Object.prototype, "add_on_option_ids")
      }));
    """
    )
    assert result == {
        "attempts": [
            {"accepted": False, "message": "Identidad invalida"},
            {"accepted": False, "message": "Identidad invalida"},
            {"accepted": True, "value": "tarkett:T-null"},
        ],
        "pollutionResult": 'alma:["alma:safe","",[]]',
        "pollutionCleaned": True,
    }


def test_javascript_identity_validation_matches_actual_python_backend():
    rows = [
        {"catalog": "tarkett", "code": "\u2003T-1\u2003", "quantity": "1"},
        {
            "catalog": "offiho",
            "inventory_key": "OHE-405 NEGRO ALUFSEN",
            "quantity": "1",
        },
        {
            "catalog": "alma",
            "internal_id": "alma:desk|uno",
            "base_option_id": "base:a",
            "add_on_option_ids": ["𐀀", "\ue000", "addon-a"],
            "quantity": "1",
        },
        {"catalog": "tarkett", "code": "😀" * 1000, "quantity": "1"},
        {"catalog": "tarkett", "code": "😀" * 1001, "quantity": "1"},
        {"catalog": "tarkett", "code": "\ufeffT-FEFF", "quantity": "1"},
        {
            "catalog": "alma",
            "internal_id": "alma:desk",
            "base_option_id": "😀" * 500,
            "add_on_option_ids": ["addon-a"],
            "quantity": "1",
        },
        {
            "catalog": "alma",
            "internal_id": "alma:desk",
            "base_option_id": "😀" * 501,
            "add_on_option_ids": [],
            "quantity": "1",
        },
    ]

    python_results = []
    for row in rows:
        try:
            normalized = preflight_mixed_catalog_items([row])[0]
            python_results.append(
                {"accepted": True, "key": python_mixed_cart_key(normalized)}
            )
        except ValueError:
            python_results.append({"accepted": False})

    javascript_results = run_mixed_cart_js(
        f"const rows = {json.dumps(rows)};\n"
        + """
      const identityFor = (row) => {
        if (row.catalog === "tarkett") return {code: row.code};
        if (row.catalog === "offiho") return {inventory_key: row.inventory_key};
        return {
          internal_id: row.internal_id,
          base_option_id: row.base_option_id,
          add_on_option_ids: row.add_on_option_ids
        };
      };
      console.log(JSON.stringify(rows.map(row => {
        try { return {accepted: true, key: mixedCartKey(row.catalog, identityFor(row))}; }
        catch (error) { return {accepted: false}; }
      })));
    """
    )

    assert javascript_results == python_results
    assert python_results[3]["accepted"] is True  # 1000 astral code points
    assert python_results[4] == {"accepted": False}
    assert python_results[5] == {"accepted": False}  # FEFF is Cf, not Python strip
    assert python_results[6]["accepted"] is True  # 500 astral base-option code points
    assert python_results[7] == {"accepted": False}


def test_imported_preview_replaces_only_imported_lines_without_intermediate_cart_state():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const preview = (importId, name) => ({
        import_id: importId, original_filename: "Proveedor.xlsx", provider: "Proveedor detectado", source_currency: null,
        currency_status: "required",
        sections: [{id: "source-section", title: "Sala", item_keys: [`import:${importId}:9`]}],
        items: [{key: `import:${importId}:9`, source_row: 9, name, description: "", dimension: "", quantity: "1", unit_price: "10", image_url: ""}],
      });
      const catalogLine = createMixedCartLine({
        catalog: "alma", identity: {internal_id: "alma:desk", base_option_id: "", add_on_option_ids: []}, quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Catalogo", code: "AL-1", image_url: "", unit: "PZA", availability: "", configuration: "", warnings: []},
      });
      const state = {cart: [catalogLine], sections: createInitialMixedCartSections(), confirmations: 0};
      const cartRef = {current: state.cart};
      const sectionsRef = {current: state.sections};
      const controller = createMixedQuoteController({
        cartRef, sectionsRef, submittingRef: {current: false}, sessionEpochRef: {current: 0}, emptyForm: {},
        replaceCart(next) { state.cart = next; cartRef.current = next; },
        replaceSections(next) { state.sections = next; sectionsRef.current = next; },
        setOpen() {}, setForm() {}, getForm() { return {}; }, setBusy() {}, setError() {}, setNotice() {}, setJobs() {},
        async request() { return {cotizaciones: []}; }, confirmQuote() { return true; },
        confirmImport() { state.confirmations += 1; return true; },
      });
      const first = controller.importPreview(preview("11111111-1111-4111-8111-111111111111", "Primero"), {sourceCurrency: "USD", provider: "Proveedor"});
      const second = controller.importPreview(preview("22222222-2222-4222-8222-222222222222", "Segundo"), {sourceCurrency: "EUR", provider: "Proveedor"});
      console.log(JSON.stringify({first, second, confirmations: state.confirmations, lines: state.cart, sections: state.sections}));
        """,
    )

    assert result["first"] is True
    assert result["second"] is True
    assert result["confirmations"] == 1
    assert [line["snapshot"]["name"] for line in result["lines"]] == ["Catalogo", "Segundo"]
    assert result["lines"][1]["sourceCurrency"] == "EUR"
    assert {line["sectionId"] for line in result["lines"]} <= {section["id"] for section in result["sections"]}


def test_import_preview_without_active_project_keeps_draft_and_does_not_mutate_lines():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("runProjectLineEntry", "importQuotationPreviewForProject"),
        r"""
      const preview = {import_id: "11111111-1111-4111-8111-111111111111"};
      const state = {draft: preview, importCalls: 0, blocked: 0};
      const imported = importQuotationPreviewForProject({
        activeProject: null,
        preview,
        options: {sourceCurrency: "USD", provider: "Proveedor"},
        controller: {
          importPreview() {
            state.importCalls += 1;
            return true;
          },
        },
        onBlocked() { state.blocked += 1; },
      });
      if (imported) state.draft = null;
      console.log(JSON.stringify({
        imported,
        draftRetained: state.draft === preview,
        importCalls: state.importCalls,
        blocked: state.blocked,
      }));
        """,
    )
    assert result == {
        "imported": False,
        "draftRetained": True,
        "importCalls": 0,
        "blocked": 1,
    }


def test_pending_import_draft_is_adopted_without_mutating_the_local_state():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("projectStateWithImportDraft",),
        r"""
      const importId = "11111111-1111-4111-8111-111111111111";
      const base = {
        quoteFields: {proyecto: "Base"},
        sections: createInitialMixedCartSections(),
        lines: [],
      };
      const pending = {
        preview: {
          import_id: importId,
          original_filename: "Proveedor.xlsx",
          provider: "Proveedor detectado",
          source_currency: "USD",
          sections: [{id: "source-section", title: "Sala", item_keys: [`import:${importId}:9`]}],
          items: [{
            key: `import:${importId}:9`,
            source_row: 9,
            name: "Producto importado",
            description: "",
            dimension: "",
            quantity: "2",
            unit_price: "10",
            source_currency: "USD",
            image_url: "",
          }],
        },
        options: {
          sourceCurrency: "USD",
          provider: "Proveedor elegido",
          quoteForm: {proyecto: "Desde importacion"},
        },
      };
      const adopted = projectStateWithImportDraft(base, pending);
      console.log(JSON.stringify({
        baseLines: base.lines.length,
        baseProject: base.quoteFields.proyecto,
        adoptedLines: adopted.lines.length,
        adoptedName: adopted.lines[0].snapshot.name,
        adoptedProvider: adopted.lines[0].provider,
        adoptedProject: adopted.quoteFields.proyecto,
        validSections: adopted.lines.every(line =>
          adopted.sections.some(section => section.id === line.sectionId)),
      }));
        """,
    )
    assert result == {
        "baseLines": 0,
        "baseProject": "Base",
        "adoptedLines": 1,
        "adoptedName": "Producto importado",
        "adoptedProvider": "Proveedor elegido",
        "adoptedProject": "Desde importacion",
        "validSections": True,
    }


def test_new_project_from_active_project_uses_canonical_empty_state():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("projectCreationPlan", "projectStateWithImportDraft"),
        r"""
      const line = createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:1", base_option_id: "", add_on_option_ids: []},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Propiedad del activo", code: "A-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
      });
      const emptyState = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: createInitialMixedCartSections(),
        lines: [],
      };
      const plan = projectCreationPlan({
        activeProject: {id: "project-active"},
        pendingImportDraft: null,
        localState: {
          quoteFields: {proyecto: "Proyecto activo"},
          sections: createInitialMixedCartSections(),
          lines: [line],
        },
        emptyState,
      });
      const selected = plan.projectState;
      console.log(JSON.stringify({
        isCanonicalEmpty: selected === emptyState,
        project: selected.quoteFields.proyecto,
        lines: selected.lines.length,
        submittedAdoption: plan.submittedAdoption,
      }));
        """,
    )
    assert result == {
        "isCanonicalEmpty": True,
        "project": "",
        "lines": 0,
        "submittedAdoption": None,
    }


def test_new_project_without_active_project_adopts_pending_import():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("projectCreationPlan", "projectStateWithImportDraft"),
        r"""
      const importId = "11111111-1111-4111-8111-111111111111";
      const emptyState = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: createInitialMixedCartSections(),
        lines: [],
      };
      const pendingImportDraft = {
        preview: {
          import_id: importId,
          original_filename: "Proveedor.xlsx",
          provider: "Proveedor",
          source_currency: "USD",
          sections: [{id: "source", title: "Sala", item_keys: [`import:${importId}:9`]}],
          items: [{
            key: `import:${importId}:9`, source_row: 9, name: "Importado",
            description: "", dimension: "", quantity: "1", unit_price: "10",
            source_currency: "USD", image_url: "",
          }],
        },
        options: {
          sourceCurrency: "USD",
          provider: "Proveedor",
          quoteForm: {proyecto: "Borrador recuperado", template: "no-persistir.xlsx"},
        },
      };
      const plan = projectCreationPlan({
        activeProject: null,
        pendingImportDraft,
        localState: emptyState,
        emptyState,
      });
      const selected = plan.projectState;
      console.log(JSON.stringify({
        project: selected.quoteFields.proyecto,
        quoteKeys: Object.keys(selected.quoteFields).sort(),
        lines: selected.lines.length,
        onlyNonImported: selected.lines.every(line => line.kind !== "imported"),
        submittedOwnDraft: plan.submittedAdoption === pendingImportDraft,
      }));
        """,
    )
    assert result == {
        "project": "Borrador recuperado",
        "quoteKeys": [
            "cliente",
            "correo",
            "descuento",
            "direccion",
            "proyecto",
            "quote_currency",
            "razon_social",
            "telefono",
        ],
        "lines": 0,
        "onlyNonImported": True,
        "submittedOwnDraft": True,
    }


def test_active_project_pending_import_creation_excludes_active_lines():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("projectCreationPlan", "projectStateWithImportDraft"),
        r"""
      const activeLine = createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:active", base_option_id: "", add_on_option_ids: []},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Linea activa", code: "ACT-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
      });
      const importId = "22222222-2222-4222-8222-222222222222";
      const pendingImportDraft = {
        preview: {
          import_id: importId,
          original_filename: "Pendiente.xlsx",
          provider: "Proveedor pendiente",
          source_currency: "USD",
          sections: [{id: "source", title: "Importados", item_keys: [`import:${importId}:4`]}],
          items: [{
            key: `import:${importId}:4`, source_row: 4, name: "Solo pendiente",
            official_code: "IMP-4",
            description: "", dimension: "", quantity: "1", unit_price: "25",
            source_currency: "USD", image_url: "",
          }],
        },
        options: {
          sourceCurrency: "USD",
          provider: "Proveedor pendiente",
          quoteForm: {proyecto: "Adopcion pendiente"},
        },
      };
      const emptyState = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: createInitialMixedCartSections(),
        lines: [],
      };
      const plan = projectCreationPlan({
        activeProject: {id: "project-conflict"},
        pendingImportDraft,
        localState: {
          quoteFields: {...emptyState.quoteFields, proyecto: "Proyecto activo"},
          sections: createInitialMixedCartSections(),
          lines: [activeLine],
        },
        emptyState,
      });
      const postPayload = serializeProject(plan.projectState);
      console.log(JSON.stringify({
        submittedOwnDraft: plan.submittedAdoption === pendingImportDraft,
        postLineNames: postPayload.lines.map(line => line.display_cache.name),
        postHasActiveLine: postPayload.lines.some(line =>
          line.display_cache.name === activeLine.snapshot.name),
        postHasImportedLine: postPayload.lines.some(line => line.source === "imported"),
        postProject: postPayload.quote_fields.proyecto,
      }));
        """,
    )
    assert result == {
        "submittedOwnDraft": True,
        "postLineNames": [],
        "postHasActiveLine": False,
        "postHasImportedLine": False,
        "postProject": "Adopcion pendiente",
    }


def test_created_project_adoption_orders_promotion_before_guarded_patch_and_retains_failures():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("persistCreatedProjectAdoption", "projectStateWithImportDraft"),
        r"""
      const importId = "22222222-2222-4222-8222-222222222222";
      const projectId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
      const baseState = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
          razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: createInitialMixedCartSections(),
        lines: [],
      };
      const created = {
        id: projectId,
        name: "Nuevo Proyecto",
        revision: 0,
        payload: serializeProject(baseState),
      };
      const submittedAdoption = {
        preview: {
          import_id: importId,
          original_filename: "Proveedor.xlsx",
          provider: "Proveedor",
          source_currency: "USD",
          sections: [{id: "source", title: "Sala", item_keys: [`import:${importId}:9`]}],
          items: [{
            key: `import:${importId}:9`, source_row: 9, name: "Importado",
            official_code: "IMP-9", provider: "Proveedor",
            description: "", dimension: "", quantity: "1", unit_price: "10",
            source_currency: "USD", image_url: "",
          }],
        },
        options: {
          sourceCurrency: "USD",
          provider: "Proveedor",
          quoteForm: {proyecto: "Borrador recuperado"},
        },
      };
      const durableDraft = {
        ...submittedAdoption,
        preview: {
          ...submittedAdoption.preview,
          items: submittedAdoption.preview.items.map(item => ({
            ...item,
            image_url: "",
            image_asset_key: "",
            source_asset_key: `projects/7/${projectId}/sources/source.xlsx`,
          })),
        },
      };
      const operationId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

      const promotionFailureOrder = [];
      let promotionFailure = "";
      try {
        await persistCreatedProjectAdoption({
          request: async () => {
            promotionFailureOrder.push("patch");
            throw new Error("unexpected patch");
          },
          created,
          submittedAdoption,
          operationId,
          promoteImport: async () => {
            promotionFailureOrder.push("promote");
            throw new Error("promotion failed");
          },
        });
      } catch (error) {
        promotionFailure = error.message;
      }

      const patchFailureOrder = [];
      let patchFailure = "";
      let patchBody = null;
      try {
        await persistCreatedProjectAdoption({
          request: async (path, options) => {
            patchFailureOrder.push("patch");
            patchBody = {path, ...JSON.parse(options.body)};
            throw new Error("patch failed");
          },
          created,
          submittedAdoption,
          operationId,
          promoteImport: async () => {
            patchFailureOrder.push("promote");
            return durableDraft;
          },
        });
      } catch (error) {
        patchFailure = error.message;
      }

      console.log(JSON.stringify({
        promotionFailure,
        promotionFailureOrder,
        patchFailure,
        patchFailureOrder,
        expectedRevision: patchBody.expected_revision,
        operationId: patchBody.operation_id,
        patchPath: patchBody.path,
        patchImportedSources: patchBody.payload.lines.map(line => line.source),
        patchAssetKeys: patchBody.payload.lines.map(line => line.source_asset_key),
        baseStillEmpty: created.payload.lines.length === 0,
        draftStillTransient: submittedAdoption.preview.items[0].image_url === "",
      }));
        """,
    )
    assert result == {
        "promotionFailure": "promotion failed",
        "promotionFailureOrder": ["promote"],
        "patchFailure": "patch failed",
        "patchFailureOrder": ["promote", "patch"],
        "expectedRevision": 0,
        "operationId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "patchPath": "/projects/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "patchImportedSources": ["imported"],
        "patchAssetKeys": [
            "projects/7/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/sources/source.xlsx",
        ],
        "baseStillEmpty": True,
        "draftStillTransient": True,
    }


def test_confirmed_creation_clears_only_the_submitted_pending_draft():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("pendingDraftAfterConfirmedCreation",),
        r"""
      const submitted = {preview: {import_id: "submitted"}};
      const newer = {preview: {import_id: "newer"}};
      console.log(JSON.stringify({
        matchingCleared: pendingDraftAfterConfirmedCreation(submitted, submitted) === null,
        newerRetained: pendingDraftAfterConfirmedCreation(newer, submitted) === newer,
        unsubmittedRetained: pendingDraftAfterConfirmedCreation(newer, null) === newer,
      }));
        """,
    )
    assert result == {
        "matchingCleared": True,
        "newerRetained": True,
        "unsubmittedRetained": True,
    }


def test_failed_project_switch_keeps_previous_state_and_retry_can_succeed():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("loadProjectSnapshot", "projectStateWithImportDraft"),
        r"""
      const previous = {
        project: {id: "project-a", name: "Proyecto A", revision: 3},
        state: {
          quoteFields: {proyecto: "A"},
          sections: createInitialMixedCartSections(),
          lines: [{key: "owned-by-a"}],
        },
      };
      let visible = previous;
      let attempts = 0;
      const request = async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("network");
        return {
          project: {
            id: "project-b",
            name: "Proyecto B",
            revision: 7,
            payload: {quote_fields: {proyecto: "B"}, sections: [], lines: []},
          },
        };
      };
      try {
        visible = await loadProjectSnapshot({
          request,
          projectId: "project-b",
          adoptionDraft: null,
          hydrate: value => ({
            quoteFields: value.quote_fields,
            sections: createInitialMixedCartSections(),
            lines: [],
          }),
        });
      } catch {}
      const afterFailure = {
        projectId: visible.project.id,
        line: visible.state.lines[0].key,
      };
      const loaded = await loadProjectSnapshot({
        request,
        projectId: "project-b",
        adoptionDraft: null,
        hydrate: value => ({
          quoteFields: value.quote_fields,
          sections: createInitialMixedCartSections(),
          lines: [],
        }),
      });
      visible = {project: loaded.project, state: loaded.state};
      console.log(JSON.stringify({
        attempts,
        afterFailure,
        afterSuccess: {
          projectId: visible.project.id,
          revision: visible.project.revision,
          projectName: visible.state.quoteFields.proyecto,
        },
      }));
        """,
    )
    assert result == {
        "attempts": 2,
        "afterFailure": {
            "projectId": "project-a",
            "line": "owned-by-a",
        },
        "afterSuccess": {
            "projectId": "project-b",
            "revision": 7,
            "projectName": "B",
        },
    }


def test_conflict_blocks_catalog_add_and_import_without_mutating_project():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("canMutateProject", "runProjectLineEntry", "importQuotationPreviewForProject"),
        r"""
      const state = {mutations: 0, changeVersion: 5, blocked: 0, draft: null};
      const activeProject = {id: "project-a"};
      const allowed = canMutateProject({
        activeProject,
        projectLoadStatus: "ready",
        autosaveStatus: "conflict",
      });
      const onBlocked = value => {
        state.blocked += 1;
        if (value) state.draft = value;
      };
      const addResult = runProjectLineEntry({
        allowed,
        mutate() {
          state.mutations += 1;
          state.changeVersion += 1;
          return true;
        },
        onBlocked,
      });
      const preview = {import_id: "pending-import"};
      const options = {sourceCurrency: "USD", provider: "Proveedor"};
      const importResult = importQuotationPreviewForProject({
        activeProject,
        allowed,
        preview,
        options,
        controller: {
          importPreview() {
            state.mutations += 1;
            state.changeVersion += 1;
            return true;
          },
        },
        onBlocked: () => onBlocked({preview, options}),
      });
      console.log(JSON.stringify({
        allowed,
        addResult,
        importResult,
        mutations: state.mutations,
        changeVersion: state.changeVersion,
        blocked: state.blocked,
        retainedDraft: state.draft?.preview === preview,
      }));
        """,
    )
    assert result == {
        "allowed": False,
        "addResult": False,
        "importResult": False,
        "mutations": 0,
        "changeVersion": 5,
        "blocked": 2,
        "retainedDraft": True,
    }


def test_loading_target_blocks_external_line_entry():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("canMutateProject",),
        r"""
      console.log(JSON.stringify({
        allowed: canMutateProject({
          activeProject: {id: "project-a"},
          projectLoadStatus: "loading",
          autosaveStatus: "saved",
        }),
      }));
        """,
    )
    assert result == {"allowed": False}


def test_confirmed_adoption_save_clears_only_its_pending_import_draft():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("saveProjectSnapshot",),
        r"""
      const draft = {preview: {import_id: "draft-b"}};
      const adoptionRef = {
        current: {projectId: "project-b", operationId: "", draft},
      };
      const confirmed = [];
      const saved = await saveProjectSnapshot({
        request: async (path, options) => ({
          project: {
            id: "project-b",
            name: "Proyecto B",
            revision: 8,
            requestPath: path,
            requestBody: JSON.parse(options.body),
          },
        }),
        snapshot: {id: "project-b", name: "Proyecto B", payload: {schema_version: 1}},
        expectedRevision: 7,
        operationId: "operation-b",
        adoptionRef,
        onSaved() {},
        onAdoptionConfirmed(value) { confirmed.push(value); },
      });
      console.log(JSON.stringify({
        savedId: saved.id,
        requestPath: saved.requestPath,
        expectedRevision: saved.requestBody.expected_revision,
        markerCleared: adoptionRef.current === null,
        confirmedOwnDraft: confirmed[0] === draft,
      }));
        """,
    )
    assert result == {
        "savedId": "project-b",
        "requestPath": "/projects/project-b",
        "expectedRevision": 7,
        "markerCleared": True,
        "confirmedOwnDraft": True,
    }


def test_failed_or_conflicted_adoption_save_retains_the_pending_import_marker():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("saveProjectSnapshot",),
        r"""
      const attempt = async (kind) => {
        const draft = {preview: {import_id: kind}};
        const adoptionRef = {
          current: {projectId: "project-b", operationId: "", draft},
        };
        const error = Object.assign(new Error(kind), kind === "conflict"
          ? {status: 409, project: {id: "project-b", revision: 8}}
          : {});
        let confirmed = 0;
        try {
          await saveProjectSnapshot({
            request: async () => { throw error; },
            snapshot: {id: "project-b", name: "Proyecto B", payload: {}},
            expectedRevision: 7,
            operationId: `operation-${kind}`,
            adoptionRef,
            onSaved() {},
            onAdoptionConfirmed() { confirmed += 1; },
          });
        } catch {}
        return {
          retained: adoptionRef.current?.draft === draft,
          operationId: adoptionRef.current?.operationId,
          confirmed,
        };
      };
      console.log(JSON.stringify({
        network: await attempt("network"),
        conflict: await attempt("conflict"),
      }));
        """,
    )
    assert result == {
        "network": {
            "retained": True,
            "operationId": "operation-network",
            "confirmed": 0,
        },
        "conflict": {
            "retained": True,
            "operationId": "operation-conflict",
            "confirmed": 0,
        },
    }


def test_stale_prior_project_save_cannot_clear_current_project_import_draft():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("saveProjectSnapshot",),
        r"""
      const draftB = {preview: {import_id: "draft-b"}};
      const adoptionRef = {
        current: {projectId: "project-b", operationId: "", draft: draftB},
      };
      const paths = [];
      let confirmed = 0;
      await saveProjectSnapshot({
        request: async (path) => {
          paths.push(path);
          return {project: {id: "project-a", name: "Proyecto A", revision: 4}};
        },
        snapshot: {id: "project-a", name: "Proyecto A", payload: {}},
        expectedRevision: 3,
        operationId: "stale-operation-a",
        adoptionRef,
        onSaved() {},
        onAdoptionConfirmed() { confirmed += 1; },
      });
      console.log(JSON.stringify({
        paths,
        retainedCurrentDraft: adoptionRef.current?.draft === draftB,
        markerOperation: adoptionRef.current?.operationId,
        confirmed,
      }));
        """,
    )
    assert result == {
        "paths": ["/projects/project-a"],
        "retainedCurrentDraft": True,
        "markerOperation": "",
        "confirmed": 0,
    }


def test_reimporting_same_preview_changes_editor_revision_and_resets_canonical_model():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const importId = "11111111-1111-4111-8111-111111111111";
      const preview = name => ({
        import_id: importId, original_filename: "Proveedor.xlsx", provider: "Proveedor", source_currency: "USD",
        currency_status: "detected",
        sections: [{id: "source-section", title: "Sala", item_keys: [`import:${importId}:9`]}],
        items: [{key: `import:${importId}:9`, source_row: 9, name, description: "", dimension: "", quantity: "1", unit_price: "10", source_currency: "USD", image_url: ""}],
      });
      const state = {cart: [], sections: createInitialMixedCartSections()};
      const cartRef = {current: state.cart}; const sectionsRef = {current: state.sections};
      const controller = createMixedQuoteController({
        cartRef, sectionsRef, submittingRef: {current: false}, sessionEpochRef: {current: 0}, emptyForm: {},
        replaceCart(next) { state.cart = next; cartRef.current = next; },
        replaceSections(next) { state.sections = next; sectionsRef.current = next; },
        setOpen() {}, setForm() {}, getForm() { return {}; }, setBusy() {}, setError() {}, setNotice() {}, setJobs() {},
        async request() { return {cotizaciones: []}; }, confirmQuote() { return true; }, confirmImport() { return true; },
      });
      controller.importPreview(preview("Original"), {sourceCurrency: null, provider: "Proveedor"});
      const first = state.cart[0];
      controller.updateImported(first.key, {name: "Edicion local"});
      controller.importPreview(preview("Reimportado"), {sourceCurrency: null, provider: "Proveedor"});
      const second = state.cart[0];
      console.log(JSON.stringify({
        sameCanonicalKey: first.key === second.key,
        revisions: [first.editorRevision, second.editorRevision],
        name: second.edits.name,
      }));
        """,
    )

    assert result["sameCanonicalKey"] is True
    assert result["revisions"] == [1, 2]
    assert result["name"] == "Reimportado"


def test_reimporting_same_line_drops_old_invalid_editor_state():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/MixedCartDrawer.jsx",
        ("importedEditorKey", "retainActiveImportedDraftValidity"),
        r"""
      const line = {kind: "imported", key: "import:source:9", editorRevision: 2};
      const activeKey = importedEditorKey(line);
      const oldKey = importedEditorKey({...line, editorRevision: 1});
      console.log(JSON.stringify({
        oldKey, activeKey,
        afterReplacement: retainActiveImportedDraftValidity({[oldKey]: true}, [line]),
        currentInvalid: retainActiveImportedDraftValidity({[activeKey]: true}, [line]),
        currentValid: retainActiveImportedDraftValidity({[activeKey]: false}, [line]),
      }));
        """,
    )
    assert result["oldKey"] != result["activeKey"]
    assert result["afterReplacement"] == {}
    assert result["currentInvalid"] == {result["activeKey"]: True}
    assert result["currentValid"] == {result["activeKey"]: False}


def test_reimporting_same_line_replaces_stale_invalid_quantity_draft_and_submits_new_quantity():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/MixedCartDrawer.jsx",
        ("importedEditorKey", "reconcileQuantityDraftState", "submitMixedDrawerDrafts"),
        r"""
      const oldLine = {
        kind: "imported", key: "import:source:9", editorRevision: 1, quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "100", integer: true},
      };
      const newLine = {...oldLine, editorRevision: 2, quantity: "2"};
      const oldKey = importedEditorKey(oldLine);
      const newKey = importedEditorKey(newLine);
      const reconciled = reconcileQuantityDraftState(
        {[oldKey]: "0"},
        {[oldKey]: "1"},
        {[oldKey]: "Cantidad invalida"},
        [newLine],
      );
      let submitErrors = null; let submitted = null;
      const accepted = submitMixedDrawerDrafts({
        event: {preventDefault() {}}, lines: [newLine], quantityDrafts: reconciled.drafts,
        setErrors(value) { submitErrors = value; }, focusFirst() {},
        onSubmit(_event, lines) { submitted = lines; },
      });
      console.log(JSON.stringify({oldKey, newKey, reconciled, accepted, submitErrors, submitted}));
        """,
    )

    assert result["oldKey"] != result["newKey"]
    assert result["reconciled"] == {
        "drafts": {result["newKey"]: "2"},
        "errors": {},
        "committed": {result["newKey"]: "2"},
    }
    assert result["accepted"] is True
    assert result["submitErrors"] == {}
    assert result["submitted"][0]["quantity"] == "2"


def test_mixed_explicit_row_currencies_do_not_require_global_selector():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("previewNeedsSourceCurrency",),
        r"""
      console.log(JSON.stringify({
        mixed: previewNeedsSourceCurrency({currency_status: "detected", source_currency: null}),
        missing: previewNeedsSourceCurrency({currency_status: "required", source_currency: null}),
        uniform: previewNeedsSourceCurrency({currency_status: "detected", source_currency: "USD"}),
      }));
        """,
    )
    assert result == {"mixed": False, "missing": True, "uniform": False}

    bundle = run_mixed_cart_js(r"""
      const importId = "11111111-1111-4111-8111-111111111111";
      const preview = {
        import_id: importId, original_filename: "Mixto.xlsx", provider: "Proveedor", source_currency: null,
        currency_status: "detected",
        sections: [{id: "source", title: "Sala", item_keys: [`import:${importId}:9`, `import:${importId}:10`]}],
        items: [
          {key: `import:${importId}:9`, source_row: 9, name: "USD", description: "", dimension: "", quantity: "1", unit_price: "10", source_currency: "USD", image_url: ""},
          {key: `import:${importId}:10`, source_row: 10, name: "EUR", description: "", dimension: "", quantity: "1", unit_price: "9", source_currency: "EUR", image_url: ""},
        ],
      };
      const bundle = createImportedCartBundle(preview, null, "Proveedor", createInitialMixedCartSections());
      console.log(JSON.stringify(bundle.lines.map(line => line.sourceCurrency)));
    """)
    assert bundle == ["USD", "EUR"]


def test_mixed_submit_clears_only_after_completed_and_preserves_failed_snapshot():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const line = createMixedCartLine({catalog: "tarkett", identity: {code: "T-1"}, quantity: "1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "10"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2", availability: "10", configuration: "", warnings: []}});
      const run = async status => {
        const state = {cart: [line], sections: createInitialMixedCartSections(), open: true, error: "", notice: "", jobs: []};
        const cartRef = {current: state.cart}; const sectionsRef = {current: state.sections};
        const controller = createMixedQuoteController({cartRef, sectionsRef, submittingRef: {current: false}, sessionEpochRef: {current: 0}, emptyForm: {},
          replaceCart(next) { state.cart = next; cartRef.current = next; }, replaceSections(next) { state.sections = next; sectionsRef.current = next; },
          setOpen(value) { state.open = value; }, setForm() {}, getForm() { return {proyecto: "P"}; }, setBusy() {},
          setError(value) { state.error = value; }, setNotice(value) { state.notice = value; },
          setJobs(value) { state.jobs = typeof value === "function" ? value(state.jobs) : value; },
          async request() { return {job: {id: `job-${status}`, status: "queued"}}; }, confirmQuote() { return true; },
          async waitForJobResult(job) { return {...job, status, error_message: status === "failed" ? "worker failed" : null}; },
        });
        await controller.submit({preventDefault() {}});
        return {cartCount: state.cart.length, open: state.open, error: state.error, notice: state.notice};
      };
      console.log(JSON.stringify({completed: await run("completed"), failed: await run("failed")}));
        """,
    )
    assert result["completed"]["cartCount"] == 0
    assert result["completed"]["open"] is False
    assert result["failed"]["cartCount"] == 1
    assert result["failed"]["open"] is True
    assert "worker failed" in result["failed"]["error"]


def test_project_submit_reuses_controller_without_clearing_persistent_lines():
    result = run_ui_helper_js(
        "mobiliti_saas/web/src/main.jsx",
        ("createMixedQuoteController",),
        r"""
      const line = createMixedCartLine({
        catalog: "tarkett", identity: {code: "T-1"}, quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "10"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2",
          availability: "10", configuration: "", warnings: []},
      });
      const state = {cart: [line], sections: createInitialMixedCartSections(), open: false};
      const cartRef = {current: state.cart};
      const sectionsRef = {current: state.sections};
      const requests = [];
      const controller = createMixedQuoteController({
        cartRef, sectionsRef, submittingRef: {current: false}, sessionEpochRef: {current: 0},
        emptyForm: {},
        replaceCart(next) { state.cart = next; cartRef.current = next; },
        replaceSections(next) { state.sections = next; sectionsRef.current = next; },
        setOpen(value) { state.open = value; }, setForm() {}, getForm() { return {proyecto: "P"}; },
        setBusy() {}, setError() {}, setNotice() {}, setJobs() {},
        async request(path, options) {
          requests.push({path, body: JSON.parse(options.body)});
          return {job: {id: "job-project"}};
        },
        confirmQuote() { return true; },
        async waitForJobResult(job) { return {...job, status: "completed"}; },
      });
      await controller.submit(
        {preventDefault() {}},
        state.cart,
        null,
        {
          preserveProject: true,
          projectQuote: {
            id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            revision: 18,
          },
        },
      );
      console.log(JSON.stringify({
        cartCount: state.cart.length,
        sectionCount: state.sections.length,
        requests,
      }));
        """,
    )
    assert result == {
        "cartCount": 1,
        "sectionCount": 1,
        "requests": [{
            "path": "/projects/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/quote",
            "body": {"expected_revision": 18},
        }],
    }
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert "projectQuote: {" in source
    assert "id: project.id" in source
    assert "revision: project.revision" in source


def test_imported_cart_editor_exposes_only_approved_fields():
    source = Path("mobiliti_saas/web/src/ImportedCartLineFields.jsx").read_text(encoding="utf-8")

    for field in ("name", "description", "dimension", "unitPrice", "provider"):
        assert f'name="{field}"' in source
    assert 'name="image"' not in source
    assert 'line.kind !== "imported"' in source


def test_imported_unit_price_draft_survives_clear_shows_blur_error_and_commits_replacement():
    module_url = (Path("mobiliti_saas/web/src/importedCartLineDraft.js").resolve().as_uri())
    script = f'''import {{
  changeImportedLineDraft,
  commitImportedLineDraft,
  createImportedLineDraft,
}} from {json.dumps(module_url)};
const initial = createImportedLineDraft({{
  name: "Silla", description: "", dimension: "", unitPrice: "80.50", provider: "Proveedor",
}});
const cleared = changeImportedLineDraft(initial, "unitPrice", "");
const failedBlur = commitImportedLineDraft(cleared, "unitPrice", () => {{ throw new Error("No debe confirmar"); }});
const replacement = changeImportedLineDraft(failedBlur, "unitPrice", "82.00");
const commits = [];
const confirmed = commitImportedLineDraft(replacement, "unitPrice", (edits) => commits.push(edits));
console.log(JSON.stringify({{cleared, failedBlur, replacement, confirmed, commits}}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)

    assert result["cleared"]["values"]["unitPrice"] == ""
    assert result["cleared"]["errors"] == {}
    assert result["cleared"]["invalidFields"]["unitPrice"] == "Precio importado invalido"
    assert result["failedBlur"]["values"]["unitPrice"] == ""
    assert result["failedBlur"]["errors"]["unitPrice"] == "Precio importado invalido"
    assert result["replacement"]["values"]["unitPrice"] == "82.00"
    assert result["replacement"]["errors"] == {}
    assert result["confirmed"]["invalidFields"] == {}
    assert result["commits"] == [{"unitPrice": "82.00"}]


def test_imported_draft_editor_marks_blur_errors_accessibly_and_meets_touch_target():
    component = Path("mobiliti_saas/web/src/ImportedCartLineFields.jsx").read_text(encoding="utf-8")
    drawer = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    for marker in (
        "draft,",
        "onDraftChange",
        "onBlur",
        "aria-invalid",
        'role="alert"',
        "onValidityChange",
        "DescripciÃ³n",
    ):
        assert marker.encode("latin-1").decode("utf-8") in component
    assert "key={editorKey}" in drawer
    assert "editorRevision" in drawer
    assert "handleImportedDraftValidity" in drawer
    assert "hasInvalidImportedDrafts" in drawer
    assert "const [importedDrafts, setImportedDrafts] = useState({})" in drawer
    assert "reconcileImportedDraftState" in drawer
    assert "draft={importedDrafts[editorKey]" in drawer
    assert "useState(" not in component
    assert "useRef(" not in component
    assert "min-height: 44px" in styles
    assert "Â¿Continuar?" not in main
    assert "¿Continuar?" in main


def test_imported_line_error_ids_are_unique_and_valid_for_two_failing_lines():
    component_url = Path("mobiliti_saas/web/src/ImportedCartLineFields.jsx").resolve().as_uri()
    vite_url = Path("mobiliti_saas/web/node_modules/vite/dist/node/index.js").resolve().as_uri()
    script = f'''import {{ createServer }} from {json.dumps(vite_url)};
const server = await createServer({{root: "mobiliti_saas/web", server: {{middlewareMode: true}}, appType: "custom"}});
const module = await server.ssrLoadModule({json.dumps(component_url)});
const keys = ["import:proveedor/a:9", "import:proveedor?a:9"];
const ids = typeof module.importedLineErrorId === "function"
  ? keys.map((key) => module.importedLineErrorId(key, "unitPrice"))
  : [];
await server.close();
console.log(JSON.stringify({{hasFactory: typeof module.importedLineErrorId === "function", ids, describedBy: ids}}));
'''
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    component = Path("mobiliti_saas/web/src/ImportedCartLineFields.jsx").read_text(encoding="utf-8")

    assert result["hasFactory"] is True
    assert len(result["ids"]) == 2
    assert len(set(result["ids"])) == 2
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", error_id) for error_id in result["ids"])
    assert result["describedBy"] == result["ids"]
    assert 'const errorId = (field) => importedLineErrorId(editorKey || line.key, field);' in component
    assert 'aria-describedby={draft.errors.unitPrice ? errorId("unitPrice") : undefined}' in component
    assert '<small id={errorId("unitPrice")} role="alert">' in component


def test_imported_editor_touch_targets_use_their_exact_selectors():
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert 'className="quotation-import-preview"' in main
    for selector in (
        ".quotation-import-preview input",
        ".quotation-import-preview select",
        ".imported-line-editor input",
        ".imported-line-editor textarea",
    ):
        matching_rules = [
            declarations
            for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", styles)
            if selector in {candidate.strip() for candidate in selectors.split(",")}
        ]
        assert matching_rules, f"Falta la regla para {selector}"
        assert any(re.search(r"min-height:\s*(?:4[4-9]|[5-9]\d|[1-9]\d{2,})px", rule) for rule in matching_rules)


def test_quote_form_offers_preview_import_without_direct_generation():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert "Previsualizar e importar al proyecto" in source
    assert "Generar cotizacion" not in source
    assert "/import-preview" in source
