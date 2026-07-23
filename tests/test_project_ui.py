import json
import subprocess
from pathlib import Path


PROJECTS_VIEW = Path("mobiliti_saas/web/src/ProjectsView.jsx")
MAIN = Path("mobiliti_saas/web/src/main.jsx")
DRAWER = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx")
SUPPLIER_VIEW = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx")
WORKSPACE_MODULE = Path("mobiliti_saas/web/src/projectWorkspace.js").resolve().as_uri()
PICKER_MODULE = Path("mobiliti_saas/web/src/productPicker.js").resolve().as_uri()


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
