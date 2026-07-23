from pathlib import Path


PROJECTS_VIEW = Path("mobiliti_saas/web/src/ProjectsView.jsx")
MAIN = Path("mobiliti_saas/web/src/main.jsx")


def test_projects_view_has_recoverable_lifecycle_and_no_delete_action():
    source = PROJECTS_VIEW.read_text(encoding="utf-8")
    for copy in ("Proyectos activos", "Archivados", "Abrir", "Duplicar", "Archivar", "Restaurar"):
        assert copy in source
    assert "Eliminar" not in source
    assert 'method: "DELETE"' not in source


def test_projects_view_loads_both_lifecycle_lists_and_posts_actions():
    source = PROJECTS_VIEW.read_text(encoding="utf-8")
    assert 'request("/projects?status=active")' in source
    assert 'request("/projects?status=archived")' in source
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
