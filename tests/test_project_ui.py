import json
import subprocess
from pathlib import Path


PROJECTS_VIEW = Path("mobiliti_saas/web/src/ProjectsView.jsx")
MAIN = Path("mobiliti_saas/web/src/main.jsx")
DRAWER = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx")
SUPPLIER_VIEW = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx")
WORKSPACE_MODULE = Path("mobiliti_saas/web/src/projectWorkspace.js").resolve().as_uri()


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
