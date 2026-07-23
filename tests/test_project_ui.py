import json
import subprocess
from pathlib import Path

from mobiliti_saas.quote_engine.project_model import normalize_project_payload


PROJECTS_VIEW = Path("mobiliti_saas/web/src/ProjectsView.jsx")
MAIN = Path("mobiliti_saas/web/src/main.jsx")
DRAWER = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx")
SUPPLIER_VIEW = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx")
WORKSPACE_MODULE = Path("mobiliti_saas/web/src/projectWorkspace.js").resolve().as_uri()
PICKER_MODULE = Path("mobiliti_saas/web/src/productPicker.js").resolve().as_uri()


def test_project_editor_has_tabs_and_line_actions():
    source = Path("mobiliti_saas/web/src/ProjectEditor.jsx").read_text(encoding="utf-8")
    for copy in (
        "Productos", "Datos de cotizaciÃ³n", "Cambiar producto",
        "Cambiar todos los iguales", "Agregar complemento",
        "Guardando", "Guardado", "Cambios pendientes",
    ):
        assert copy in source
    assert "parentLineId" in source
    assert "quantityMode" in source


def test_quick_panel_has_only_project_copy():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert 'aria-label="Proyecto activo"' in source
    assert "<h2>Proyecto</h2>" in source
    assert "Carrito" not in source


def test_project_editor_composes_existing_model_operations_and_picker_contexts():
    source = Path("mobiliti_saas/web/src/ProjectEditor.jsx").read_text(encoding="utf-8")
    for operation in (
        "addProjectComplement",
        "closeMixedCartSection",
        "mergeMixedCartSection",
        "moveMixedCartLine",
        "moveMixedCartLineToSection",
        "removeProjectLineTree",
        "renameMixedCartSection",
        "replaceAllProjectLines",
        "replaceProjectLine",
        "updateImportedCartLine",
        "updateMixedCartQuantity",
    ):
        assert operation in source
    for mode in ('"add"', '"replace-one"', '"replace-all"', '"complement"'):
        assert f"openPicker({mode}" in source
    assert "<ProductPickerDialog" in source
    assert "window.confirm(`Este cambio retirarÃ¡ ${children.length} complemento(s). Â¿Continuar?`)" in source


def test_project_editor_renders_direct_complements_and_quantity_modes():
    source = Path("mobiliti_saas/web/src/ProjectEditor.jsx").read_text(encoding="utf-8")
    assert 'className="project-complement"' in source
    assert "child.snapshot.image_url" in source
    assert "<strong>+ {child.snapshot.name}</strong>" in source
    assert 'value={child.quantityMode}' in source
    assert '<option value="per_parent_unit">Por unidad</option>' in source
    assert '<option value="fixed_project">Cantidad fija</option>' in source


def test_picker_target_uses_own_safe_quantity_and_canonical_catalog_provider():
    result = run_workspace(r"""
      const principal = {
        quantity: "5",
        quantityRules: {min: "0.5", step: "0.5", maxDecimals: 1, max: "100"},
      };
      const selection = {
        catalog: "cr-global",
        provider: "CR Global",
        official_code: "CR-1",
        identity: {internal_id: "cr:1", base_option_id: "", add_on_option_ids: []},
        snapshot: {name: "Silla CR", image_url: "", availability: "", configuration: "", warnings: []},
      };
      const target = workspace.createProjectPickerTarget(selection, principal);
      console.log(JSON.stringify({
        provider: target.provider,
        quantity: target.quantity,
        rules: target.quantityRules,
      }));
    """)
    assert result == {
        "provider": "cr-global",
        "quantity": "1",
        "rules": {
            "min": "1", "step": "1", "maxDecimals": 0,
            "max": "1000000", "integer": True,
        },
    }


def test_cr_global_picker_target_matches_before_and_after_project_round_trip():
    model_url = Path("mobiliti_saas/web/src/mixedCart.js").resolve().as_uri()
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f"""
          import * as workspace from {json.dumps(WORKSPACE_MODULE)};
          import * as model from {json.dumps(model_url)};
          const target = workspace.createProjectPickerTarget({{
            catalog: "cr-global", provider: "CR Global", official_code: "CR-1",
            identity: {{internal_id: "cr:1", base_option_id: "", add_on_option_ids: []}},
            snapshot: {{name: "Silla CR", image_url: "", availability: "",
              configuration: "", warnings: []}},
          }});
          const line = model.createMixedCartLine({{...target, sectionId: "section-1"}});
          const selector = {{provider: "cr-global", officialCode: "CR-1"}};
          const state = {{
            quoteFields: {{proyecto: "", cliente: "", correo: "", telefono: "",
              direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40"}},
            sections: [{{id: "section-1", concept: "RecepciÃ³n"}}],
            lines: [line],
          }};
          const reopened = model.hydrateProject(model.serializeProject(state)).lines[0];
          console.log(JSON.stringify({{
            beforeProvider: line.provider,
            afterProvider: reopened.provider,
            beforeMatches: model.projectLineMatches(line, selector),
            afterMatches: model.projectLineMatches(reopened, selector),
          }}));
        """,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "beforeProvider": "cr-global",
        "afterProvider": "cr-global",
        "beforeMatches": True,
        "afterMatches": True,
    }


def test_complement_selection_waits_for_explicit_mode_quantity_and_confirmation():
    source = Path("mobiliti_saas/web/src/ProjectEditor.jsx").read_text(encoding="utf-8")
    assert "pendingComplement" in source
    assert "setPendingComplement" in source
    assert "Confirmar complemento" in source
    assert "Impacto" in source
    assert "validateLineQuantity(pendingComplement.target" in source
    complement_branch = source[source.index('mode === "complement"'):source.index('mode === "replace-one"')]
    assert "commitLines(" not in complement_branch
    assert "addProjectComplement(" not in complement_branch


def test_project_quote_projection_uses_decimal_safe_modes_without_double_multiplication():
    result = run_workspace(r"""
      const parent = {
        key: "parent", lineId: "parent", role: "principal", sectionId: "section-1",
        quantity: "2.5",
      };
      const perUnit = {
        key: "per", lineId: "per", role: "complement", parentLineId: "parent",
        sectionId: null, quantity: "0.4", quantityMode: "per_parent_unit",
      };
      const fixed = {
        key: "fixed", lineId: "fixed", role: "complement", parentLineId: "parent",
        sectionId: null, quantity: "0.4", quantityMode: "fixed_project",
      };
      const original = [parent, perUnit, fixed];
      const first = workspace.projectMixedQuoteLines(original);
      const second = workspace.projectMixedQuoteLines(first);
      console.log(JSON.stringify({
        first: first.map((line) => ({id: line.lineId, quantity: line.quantity, sectionId: line.sectionId})),
        second: second.map((line) => ({id: line.lineId, quantity: line.quantity, sectionId: line.sectionId})),
        original: original.map((line) => ({id: line.lineId, quantity: line.quantity, sectionId: line.sectionId})),
        fractional: workspace.multiplyProjectQuantity("1.25", "0.08"),
      }));
    """)
    assert result == {
        "first": [
            {"id": "parent", "quantity": "2.5", "sectionId": "section-1"},
            {"id": "per", "quantity": "1", "sectionId": "section-1"},
            {"id": "fixed", "quantity": "0.4", "sectionId": "section-1"},
        ],
        "second": [
            {"id": "parent", "quantity": "2.5", "sectionId": "section-1"},
            {"id": "per", "quantity": "1", "sectionId": "section-1"},
            {"id": "fixed", "quantity": "0.4", "sectionId": "section-1"},
        ],
        "original": [
            {"id": "parent", "quantity": "2.5", "sectionId": "section-1"},
            {"id": "per", "quantity": "0.4", "sectionId": None},
            {"id": "fixed", "quantity": "0.4", "sectionId": None},
        ],
        "fractional": "0.1",
    }


def test_app_opens_hydrates_and_autosaves_the_same_project_state():
    source = MAIN.read_text(encoding="utf-8")
    assert 'request(`/projects/${projectId}`' in source
    assert "loadProjectSnapshot({" in source
    assert "hydrate: hydrateProject" in source
    assert "<ProjectEditor" in source
    assert "useProjectAutosave" in source
    assert 'request(`/projects/${snapshot.id}`' in source
    assert 'method: "PATCH"' in source
    assert "expected_revision: expectedRevision" in source
    assert "operation_id: operationId" in source
    assert "serializeProject" in source
    assert source.count("const [mixedCart, setMixedCart] = useState([])") == 1


def test_quick_panel_migration_keeps_project_errors_visible_in_app_shell():
    source = MAIN.read_text(encoding="utf-8")
    assert "{mixedQuoteError ? (" in source
    assert 'role="alert"' in source


def test_catalog_add_requires_active_project_and_projects_view_creates_one():
    main = MAIN.read_text(encoding="utf-8")
    projects = PROJECTS_VIEW.read_text(encoding="utf-8")
    assert "canMutateProject({" in main
    assert "Crea o abre un Proyecto antes de agregar productos." in main
    assert 'setView("proyectos")' in main
    assert "mixedQuoteController.add(line)" in main
    assert "mixedCartRef.current.length" in main
    assert "Los productos sin Proyecto se conservaron" in main
    assert "Nuevo Proyecto" in projects
    assert 'request("/projects", {' in projects
    assert 'method: "POST"' in projects
    assert "payload: serializeProject(" in projects
    assert "onActivateProject(created, submittedAdoption)" in projects
    assert "creatingRef.current" in projects
    assert "activateCreatedProject" in main
    assert "projectDraft={creationPlan.projectState}" in main
    assert "projectAdoptionDraft={creationPlan.submittedAdoption}" in main
    assert "onActivateProject={activateCreatedProject}" in main


def test_blocked_import_is_parked_and_routes_to_projects():
    main = MAIN.read_text(encoding="utf-8")
    start = main.index("function importQuotationPreview(preview, options)")
    end = main.index("function removeMixedCartLineFromApp", start)
    import_flow = main[start:end]
    assert "blockExternalProjectEntry({preview, options})" in import_flow
    assert "setPendingImportDraft((current) => current || pendingDraft)" in main
    assert 'setView("proyectos")' in main
    assert "El borrador importado se conservar" in main


def test_existing_project_adoption_retains_draft_until_confirmed_autosave():
    main = MAIN.read_text(encoding="utf-8")
    autosave_start = main.index("const projectAutosave = useProjectAutosave({")
    autosave_end = main.index("});", autosave_start)
    autosave_call = main[autosave_start:autosave_end]
    assert "`${activeProject.id}:${activeProject.loadKey}`" in autosave_call

    open_start = main.index("async function openProject(projectId)")
    open_end = main.index("function updateActiveProject", open_start)
    open_flow = main[open_start:open_end]
    assert "pendingImportAdoptionRef.current = {" in open_flow
    assert "projectId: loaded.project.id" in open_flow
    assert "setPendingImportDraft(null)" not in open_flow


def test_new_project_draft_distinguishes_active_project_from_orphan_state():
    main = MAIN.read_text(encoding="utf-8")
    assert "projectCreationPlan({" in main
    assert "activeProject," in main
    assert "emptyState:" in main
    assert "projectDraft={creationPlan.projectState}" in main
    assert "projectAdoptionDraft={creationPlan.submittedAdoption}" in main
    assert "function activateCreatedProject(created, submittedAdoption)" in main
    assert "pendingDraftAfterConfirmedCreation(current, submittedAdoption)" in main


def test_project_switch_commits_identity_only_after_target_is_hydrated():
    main = MAIN.read_text(encoding="utf-8")
    start = main.index("async function openProject(projectId)")
    request_start = main.index("try {", start)
    before_request = main[start:request_start]
    assert "setActiveProject(null)" not in before_request
    assert "setActiveProjectId(projectId)" not in before_request
    assert "setProjectLoadState({status: \"loading\"" in before_request

    end = main.index("function updateActiveProject", start)
    open_flow = main[start:end]
    assert "loadProjectSnapshot({" in open_flow
    assert "loadKey: loadEpoch" in open_flow


def test_external_line_entries_share_project_mutation_rule():
    main = MAIN.read_text(encoding="utf-8")
    assert "const canMutateActiveProject = canMutateProject({" in main
    add_start = main.index("function addMixedCartLine(line)")
    add_end = main.index("function updateMixedCartLine", add_start)
    assert "runProjectLineEntry({" in main[add_start:add_end]
    import_start = main.index("function importQuotationPreview(preview, options)")
    import_end = main.index("function removeMixedCartLineFromApp", import_start)
    assert "allowed: canMutateActiveProject" in main[import_start:import_end]
    assert "Reabre el Proyecto desde Proyectos" in main


def test_new_project_flow_posts_backend_valid_payload_then_opens_created_project():
    projects_url = PROJECTS_VIEW.resolve().as_uri()
    vite_url = Path("mobiliti_saas/web/node_modules/vite/dist/node/index.js").resolve().as_uri()
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f"""
          import {{createServer}} from {json.dumps(vite_url)};
          const server = await createServer({{
            root: "mobiliti_saas/web",
            server: {{middlewareMode: true}},
            appType: "custom",
          }});
          const module = await server.ssrLoadModule({json.dumps(projects_url)});
          const model = await server.ssrLoadModule("/src/mixedCart.js");
          const line = model.createMixedCartLine({{
            catalog: "cr-global",
            identity: {{internal_id: "cr:1", base_option_id: "", add_on_option_ids: []}},
            officialCode: "CR-1",
            provider: "cr-global",
            quantity: "2",
            quantityRules: {{min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true}},
            snapshot: {{name: "Silla CR", code: "CR-1", image_url: "", unit: "PZA",
              availability: "", configuration: "", warnings: []}},
          }});
          const projectState = {{
            quoteFields: {{proyecto: "Adoptado", cliente: "", correo: "", telefono: "",
              direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40"}},
            sections: model.createInitialMixedCartSections(),
            lines: [line],
          }};
          const calls = [];
          const activated = [];
          const created = await module.createNewProject(
            async (path, options) => {{
              calls.push({{path, options}});
              const body = JSON.parse(options.body);
              return {{project: {{
                id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                name: body.name,
                revision: 0,
                payload: body.payload,
              }}}};
            }},
            (project) => activated.push(project),
            projectState,
            {{current: false}},
          );
          await server.close();
          console.log(JSON.stringify({{calls, activated, created}}));
        """,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["calls"][0]["path"] == "/projects"
    assert result["calls"][0]["options"]["method"] == "POST"
    body = json.loads(result["calls"][0]["options"]["body"])
    assert body["name"] == "Nuevo Proyecto"
    assert normalize_project_payload(body["payload"]) == body["payload"]
    assert body["payload"]["quote_fields"]["proyecto"] == "Adoptado"
    assert len(body["payload"]["lines"]) == 1
    assert result["activated"][0]["id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert result["activated"][0]["payload"] == body["payload"]
    assert result["created"]["id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_new_project_passes_submitted_adoption_only_after_success():
    projects_url = PROJECTS_VIEW.resolve().as_uri()
    vite_url = Path("mobiliti_saas/web/node_modules/vite/dist/node/index.js").resolve().as_uri()
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f"""
          import {{createServer}} from {json.dumps(vite_url)};
          const server = await createServer({{
            root: "mobiliti_saas/web",
            server: {{middlewareMode: true}},
            appType: "custom",
          }});
          const module = await server.ssrLoadModule({json.dumps(projects_url)});
          const model = await server.ssrLoadModule("/src/mixedCart.js");
          const submittedAdoption = {{preview: {{import_id: "submitted"}}}};
          const activated = [];
          await module.createNewProject(
            async (path, options) => {{
              const body = JSON.parse(options.body);
              return {{project: {{
                id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                name: body.name,
                revision: 0,
                payload: body.payload,
              }}}};
            }},
            (project, adoption) => activated.push({{
              projectId: project.id,
              ownAdoption: adoption === submittedAdoption,
            }}),
            {{
              quoteFields: {{proyecto: "", cliente: "", correo: "", telefono: "",
                direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40"}},
              sections: model.createInitialMixedCartSections(),
              lines: [],
            }},
            {{current: false}},
            submittedAdoption,
          );
          await server.close();
          console.log(JSON.stringify({{activated}}));
        """,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "activated": [{
            "projectId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "ownAdoption": True,
        }],
    }


def test_failed_new_project_request_does_not_confirm_submitted_adoption():
    projects_url = PROJECTS_VIEW.resolve().as_uri()
    vite_url = Path("mobiliti_saas/web/node_modules/vite/dist/node/index.js").resolve().as_uri()
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f"""
          import {{createServer}} from {json.dumps(vite_url)};
          const server = await createServer({{
            root: "mobiliti_saas/web",
            server: {{middlewareMode: true}},
            appType: "custom",
          }});
          const module = await server.ssrLoadModule({json.dumps(projects_url)});
          const submittedAdoption = {{preview: {{import_id: "submitted"}}}};
          const activated = [];
          const inFlight = {{current: false}};
          let message = "";
          try {{
            await module.createNewProject(
              async () => {{ throw new Error("network"); }},
              (project, adoption) => activated.push({{project, adoption}}),
              null,
              inFlight,
              submittedAdoption,
            );
          }} catch (error) {{
            message = error.message;
          }}
          await server.close();
          console.log(JSON.stringify({{
            message,
            activations: activated.length,
            inFlight: inFlight.current,
          }}));
        """,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "message": "network",
        "activations": 0,
        "inFlight": False,
    }


def test_new_project_flow_guards_duplicate_clicks_while_post_is_in_flight():
    projects_url = PROJECTS_VIEW.resolve().as_uri()
    vite_url = Path("mobiliti_saas/web/node_modules/vite/dist/node/index.js").resolve().as_uri()
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f"""
          import {{createServer}} from {json.dumps(vite_url)};
          const server = await createServer({{
            root: "mobiliti_saas/web",
            server: {{middlewareMode: true}},
            appType: "custom",
          }});
          const module = await server.ssrLoadModule({json.dumps(projects_url)});
          let release;
          const response = new Promise((resolve) => {{ release = resolve; }});
          const calls = [];
          const activated = [];
          const inFlight = {{current: false}};
          const projectState = null;
          const request = async (path, options) => {{
            calls.push({{path, options}});
            return response;
          }};
          const first = module.createNewProject(request, (project) => activated.push(project), projectState, inFlight);
          const second = module.createNewProject(request, (project) => activated.push(project), projectState, inFlight);
          await new Promise((resolve) => setImmediate(resolve));
          const callsBeforeRelease = calls.length;
          release({{project: {{
            id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            name: "Nuevo Proyecto",
            revision: 0,
            payload: JSON.parse(calls[0].options.body).payload,
          }}}});
          const results = await Promise.all([first, second]);
          await server.close();
          console.log(JSON.stringify({{
            callsBeforeRelease,
            totalCalls: calls.length,
            activated: activated.map((project) => project.id),
            results: results.map((project) => project?.id || null),
            inFlight: inFlight.current,
          }}));
        """,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "callsBeforeRelease": 1,
        "totalCalls": 1,
        "activated": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
        "results": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", None],
        "inFlight": False,
    }


def test_closed_quick_panel_is_unmounted_and_editor_images_have_fallback():
    drawer = DRAWER.read_text(encoding="utf-8")
    editor = Path("mobiliti_saas/web/src/ProjectEditor.jsx").read_text(encoding="utf-8")
    default_export = drawer[drawer.index("export default function MixedCartDrawer"):]
    assert "if (!open) return null;" in default_export
    assert "onError" in editor
    assert "Sin imagen" in editor


def test_project_editor_layout_has_principal_complement_and_mobile_styles():
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    for selector in (
        ".project-editor",
        ".project-editor-tabs",
        ".project-principal",
        ".project-complement",
        ".project-complement-config",
        ".project-autosave-status",
        ".project-image-fallback",
        ".project-quick-summary",
        ".projects-heading-actions",
    ):
        assert selector in styles
    mobile = styles[styles.rfind("@media (max-width: 720px)"):]
    assert ".project-editor-header" in mobile
    assert ".project-principal-main" in mobile


def test_product_picker_covers_all_contexts_and_previews_images():
    source = Path("mobiliti_saas/web/src/ProductPickerDialog.jsx").read_text(encoding="utf-8")
    helper = Path("mobiliti_saas/web/src/productPicker.js").read_text(encoding="utf-8")
    for mode in ('add:', '"replace-one":', '"replace-all":', 'complement:'):
        assert mode in source
    for copy in (
        "Agregar al Proyecto", "Cambiar producto",
        "Cambiar todos los iguales", "Agregar complemento",
    ):
        assert copy in source
    assert 'alt={selected.snapshot.name}' in source
    assert "Sin imagen" in source
    assert "buildCatalogSearchPath" in source
    assert "/catalogs/search" in helper


def run_workspace(source):
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f'import * as workspace from {json.dumps(WORKSPACE_MODULE)};\n{source}',
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def run_picker(source):
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f'import * as picker from {json.dumps(PICKER_MODULE)};\n{source}',
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_product_picker_search_path_omits_empty_supplier_and_includes_valid_supplier():
    result = run_picker(r"""
      console.log(JSON.stringify({
        initial: picker.buildCatalogSearchPath({query: "", supplier: "", offset: 0, limit: 20}),
        filtered: picker.buildCatalogSearchPath({query: "silla", supplier: "alma", offset: 20, limit: 20}),
      }));
    """)
    assert result == {
        "initial": "/catalogs/search?q=&offset=0&limit=20",
        "filtered": "/catalogs/search?q=silla&offset=20&limit=20&supplier=alma",
    }


def test_product_picker_confirmation_is_a_deep_copied_canonical_allowlist():
    result = run_picker(r"""
      const raw = {
        catalog: "alma",
        identity: {internal_id: "alma-42", base_option_id: "base", add_on_option_ids: ["arm"]},
        official_code: "ALMA-42",
        snapshot: {
          name: "Silla Alma",
          image_url: "https://example.test/chair.png",
          availability: "Disponible",
          configuration: "Tela azul",
          warnings: ["Bajo pedido"],
          client_price: 123,
          arbitrary_metadata: {secret: true},
        },
        client_price: 999,
        arbitrary_metadata: {secret: true},
      };
      const selection = picker.createCanonicalProductSelection(raw);
      selection.identity.add_on_option_ids.push("mutated");
      selection.snapshot.warnings.push("mutated");
      console.log(JSON.stringify({
        selection,
        rawIdentity: raw.identity,
        rawWarnings: raw.snapshot.warnings,
        keys: Object.keys(selection).sort(),
        snapshotKeys: Object.keys(selection.snapshot).sort(),
      }));
    """)
    assert result["keys"] == ["catalog", "identity", "official_code", "provider", "snapshot"]
    assert result["snapshotKeys"] == ["availability", "configuration", "image_url", "name", "warnings"]
    assert result["selection"]["catalog"] == "alma"
    assert result["selection"]["identity"] == {
        "internal_id": "alma-42", "base_option_id": "base", "add_on_option_ids": ["arm", "mutated"],
    }
    assert result["selection"]["official_code"] == "ALMA-42"
    assert result["selection"]["provider"] == "ALMA"
    assert result["selection"]["snapshot"]["warnings"] == ["Bajo pedido", "mutated"]
    assert result["rawIdentity"]["add_on_option_ids"] == ["arm"]
    assert result["rawWarnings"] == ["Bajo pedido"]


def test_product_picker_uses_catalog_contract_for_supplier_choices_and_image_fallback():
    result = run_picker(r"""
      console.log(JSON.stringify({
        catalogs: picker.CATALOG_OPTIONS.map((option) => option.value),
        alma: picker.catalogLabel("alma"),
        imageWhenLoaded: picker.shouldShowProductImage("https://example.test/chair.png", false),
        imageWhenBroken: picker.shouldShowProductImage("https://example.test/chair.png", true),
      }));
    """)
    assert result == {
        "catalogs": ["tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro"],
        "alma": "ALMA",
        "imageWhenLoaded": True,
        "imageWhenBroken": False,
    }


def test_product_picker_uses_modal_focus_controls_and_mobile_layout():
    source = Path("mobiliti_saas/web/src/ProductPickerDialog.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    assert 'event.key === "Tab"' in source
    assert "previousFocusRef.current?.focus()" in source
    assert 'event.key === "Escape"' in source
    assert 'role="listbox"' not in source
    assert 'role="option"' not in source
    assert 'aria-pressed={isSelected}' in source
    assert 'onError={() => setFailedImageKey(selectedKey)}' in source
    assert "@media (max-width: 720px)" in styles
    assert ".project-picker-layout" in styles
    assert "grid-template-columns: 1fr;" in styles


def test_projects_view_has_recoverable_lifecycle_and_no_delete_action():
    source = PROJECTS_VIEW.read_text(encoding="utf-8")
    for copy in ("Proyectos activos", "Archivados", "Abrir", "Duplicar", "Archivar", "Restaurar"):
        assert copy in source
    assert "Eliminar" not in source
    assert 'method: "DELETE"' not in source


def test_projects_view_loads_both_lifecycle_lists_and_posts_actions():
    source = PROJECTS_VIEW.read_text(encoding="utf-8")
    assert 'request("/projects?status=active", { signal: controller.signal })' in source
    assert 'request("/projects?status=archived", { signal: controller.signal })' in source
    assert 'request(`/projects/${project.id}/duplicate`, { method: "POST" })' in source
    assert 'request(`/projects/${project.id}/${action}`, {' in source
    assert 'expected_revision: project.revision' in source
    assert "operation_id" in source


def test_sidebar_and_header_use_project_copy():
    source = MAIN.read_text(encoding="utf-8")
    assert '["proyectos", "Proyectos", FolderKanban]' in source
    assert "Proyecto (" in source
    assert "Carrito (" not in source
    assert 'view === "proyectos"' in source
    assert "<ProjectsView" in source


def test_project_operation_id_fallback_is_canonical_uuid_v4_or_fails_explicitly():
    result = run_workspace(r"""
      const fallbackCrypto = {
        getRandomValues(values) {
          values.set([...Array(16).keys()]);
          return values;
        },
      };
      let missingCryptoError = "";
      try { workspace.createProjectOperationId(null); }
      catch (error) { missingCryptoError = error.message; }
      const fallback = workspace.createProjectOperationId(fallbackCrypto);
      console.log(JSON.stringify({fallback, missingCryptoError}));
    """)
    assert result["fallback"] == "00010203-0405-4607-8809-0a0b0c0d0e0f"
    assert result["missingCryptoError"]


def test_project_load_guard_keeps_latest_result_and_disposes_pending_loads():
    result = run_workspace(r"""
      const guard = workspace.createProjectLoadGuard();
      const first = guard.begin();
      const refreshed = guard.begin();
      const beforeDispose = {
        first: guard.canApply(first),
        refreshed: guard.canApply(refreshed),
      };
      guard.dispose();
      console.log(JSON.stringify({
        beforeDispose,
        afterDispose: guard.canApply(refreshed),
      }));
    """)
    assert result == {
        "beforeDispose": {"first": False, "refreshed": True},
        "afterDispose": False,
    }


def test_projects_view_applies_loads_only_when_current_and_aborts_superseded_requests():
    source = PROJECTS_VIEW.read_text(encoding="utf-8")
    assert "new AbortController()" in source
    assert "abortControllerRef.current?.abort()" in source
    assert "signal: controller.signal" in source
    assert "guard.canApply(epoch)" in source
    assert "guard.dispose()" in source


def test_project_copy_is_complete_in_drawer_and_supplier_accessibility_labels():
    sources = {
        "main": MAIN.read_text(encoding="utf-8"),
        "drawer": DRAWER.read_text(encoding="utf-8"),
        "supplier": SUPPLIER_VIEW.read_text(encoding="utf-8"),
    }
    assert all("carrito" not in source.lower() for source in sources.values())
    assert 'aria-label="Proyecto de todos los catalogos"' in sources["drawer"]
    assert 'aria-label="Abrir proyecto"' in sources["supplier"]
    assert 'title="Abrir proyecto"' in sources["supplier"]
