import { useCallback, useEffect, useState } from "react";
import { Archive, Copy, FolderOpen, Loader2, RotateCcw } from "lucide-react";

function formatDate(value) {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Sin fecha";
  return new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function operationId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
}

function ProjectCard({ project, activeProjectId, busyProjectId, onOpenProject, onDuplicate, onStatusChange }) {
  const archived = project.status === "archived";
  const summary = project.summary || {};
  const busy = busyProjectId === project.id;

  return (
    <article className={`project-card${project.id === activeProjectId ? " selected" : ""}`}>
      <div className="project-card-copy">
        <strong>{project.name}</strong>
        <small>Actualizado: {formatDate(project.updated_at)}</small>
        <span>{summary.principals || 0} productos · {summary.complements || 0} complementos</span>
      </div>
      <div className="project-card-actions">
        {!archived ? (
          <button type="button" className="primary-action" onClick={() => onOpenProject(project.id)}>
            <FolderOpen size={16} /> Abrir
          </button>
        ) : null}
        <button type="button" className="ghost-action" disabled={busy} onClick={() => onDuplicate(project)}>
          {busy ? <Loader2 className="spin" size={16} /> : <Copy size={16} />} Duplicar
        </button>
        <button
          type="button"
          className="ghost-action"
          disabled={busy}
          onClick={() => onStatusChange(project, archived ? "restore" : "archive")}
        >
          {archived ? <RotateCcw size={16} /> : <Archive size={16} />} {archived ? "Restaurar" : "Archivar"}
        </button>
      </div>
    </article>
  );
}

export default function ProjectsView({ request, onOpenProject, activeProjectId }) {
  const [activeProjects, setActiveProjects] = useState([]);
  const [archivedProjects, setArchivedProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyProjectId, setBusyProjectId] = useState("");
  const [error, setError] = useState("");

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [active, archived] = await Promise.all([
        request("/projects?status=active"),
        request("/projects?status=archived"),
      ]);
      setActiveProjects(active.projects || []);
      setArchivedProjects(archived.projects || []);
    } catch (failure) {
      setError(failure.message || "No se pudieron cargar los proyectos.");
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  async function duplicate(project) {
    setBusyProjectId(project.id);
    setError("");
    try {
      await request(`/projects/${project.id}/duplicate`, { method: "POST" });
      await loadProjects();
    } catch (failure) {
      setError(failure.message || "No se pudo duplicar el proyecto.");
    } finally {
      setBusyProjectId("");
    }
  }

  async function changeStatus(project, action) {
    setBusyProjectId(project.id);
    setError("");
    try {
      await request(`/projects/${project.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({
          expected_revision: project.revision,
          operation_id: operationId(),
        }),
      });
      await loadProjects();
    } catch (failure) {
      setError(failure.message || "No se pudo actualizar el proyecto.");
    } finally {
      setBusyProjectId("");
    }
  }

  return (
    <section className="projects-workspace" aria-labelledby="projects-heading">
      <div className="projects-heading">
        <div>
          <h2 id="projects-heading">Proyectos</h2>
          <p>Abre, duplica o archiva los proyectos de tu espacio de trabajo.</p>
        </div>
        <button className="ghost-action" type="button" onClick={loadProjects} disabled={loading}>
          {loading ? <Loader2 className="spin" size={16} /> : null} Actualizar
        </button>
      </div>

      {error ? <p className="error-line" role="alert">{error}</p> : null}
      {loading ? <p className="projects-loading" role="status">Cargando proyectos…</p> : null}

      <section className="project-list" aria-labelledby="active-projects-heading">
        <h3 id="active-projects-heading">Proyectos activos</h3>
        {!loading && !activeProjects.length ? <p className="projects-empty">Aún no hay proyectos activos.</p> : null}
        {activeProjects.map((project) => (
          <ProjectCard
            key={project.id}
            project={project}
            activeProjectId={activeProjectId}
            busyProjectId={busyProjectId}
            onOpenProject={onOpenProject}
            onDuplicate={duplicate}
            onStatusChange={changeStatus}
          />
        ))}
      </section>

      <section className="project-list archived-project-list" aria-labelledby="archived-projects-heading">
        <h3 id="archived-projects-heading">Archivados</h3>
        {!loading && !archivedProjects.length ? <p className="projects-empty">No hay proyectos archivados.</p> : null}
        {archivedProjects.map((project) => (
          <ProjectCard
            key={project.id}
            project={project}
            activeProjectId={activeProjectId}
            busyProjectId={busyProjectId}
            onOpenProject={onOpenProject}
            onDuplicate={duplicate}
            onStatusChange={changeStatus}
          />
        ))}
      </section>
    </section>
  );
}
