import { useCallback, useEffect, useRef, useState } from "react";
import {
  Archive,
  Check,
  Copy,
  FolderOpen,
  Loader2,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import {createInitialMixedCartSections, serializeProject} from "./mixedCart.js";
import { createProjectLoadGuard, createProjectOperationId } from "./projectWorkspace";

const EMPTY_PROJECT_QUOTE_FIELDS = Object.freeze({
  proyecto: "",
  cliente: "",
  correo: "",
  telefono: "",
  direccion: "",
  razon_social: "",
  quote_currency: "MXN",
  descuento: "40",
});

export async function createNewProject(
  request,
  onActivateProject,
  projectState = null,
  inFlightRef = {current: false},
  submittedAdoption = null,
  projectName = "Nuevo Proyecto",
) {
  if (inFlightRef.current) return null;
  inFlightRef.current = true;
  try {
    const state = projectState || {
      quoteFields: {...EMPTY_PROJECT_QUOTE_FIELDS},
      sections: createInitialMixedCartSections(),
      lines: [],
    };
    const data = await request("/projects", {
      method: "POST",
      body: JSON.stringify({
        name: String(projectName || "").trim() || "Nuevo Proyecto",
        payload: serializeProject(state),
      }),
    });
    const created = data?.project;
    if (!created?.id || !created?.payload) throw new Error("Respuesta de Proyecto inválida");
    const activated = await onActivateProject(created, submittedAdoption);
    return activated && typeof activated === "object" ? activated : created;
  } finally {
    inFlightRef.current = false;
  }
}

function formatDate(value) {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Sin fecha";
  return new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function ProjectCard({
  project,
  activeProjectId,
  busyProjectId,
  onOpenProject,
  onDuplicate,
  onStatusChange,
  onRename,
  onDelete,
}) {
  const archived = project.status === "archived";
  const summary = project.summary || {};
  const busy = busyProjectId === project.id;
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(project.name);

  useEffect(() => {
    if (!editingName) setNameDraft(project.name);
  }, [editingName, project.name]);

  async function saveName() {
    const name = nameDraft.trim();
    if (!name || name === project.name) {
      setNameDraft(project.name);
      setEditingName(false);
      return;
    }
    const saved = await onRename(project, name);
    if (saved) setEditingName(false);
  }

  return (
    <article className={`project-card${project.id === activeProjectId ? " selected" : ""}`}>
      <div className="project-card-copy">
        {editingName ? (
          <label className="project-name-editor">
            <span className="sr-only">Nombre del Proyecto</span>
            <input
              type="text"
              value={nameDraft}
              maxLength={120}
              disabled={busy}
              onChange={(event) => setNameDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") saveName();
                if (event.key === "Escape") {
                  setNameDraft(project.name);
                  setEditingName(false);
                }
              }}
              autoFocus
            />
          </label>
        ) : <strong>{project.name}</strong>}
        <small>Actualizado: {formatDate(project.updated_at)}</small>
        <span>{summary.principals || 0} productos · {summary.complements || 0} complementos</span>
      </div>
      <div className="project-card-actions">
        {editingName ? (
          <>
            <button type="button" className="ghost-action" disabled={busy} onClick={saveName}>
              <Check size={16} /> Guardar
            </button>
            <button
              type="button"
              className="ghost-action"
              disabled={busy}
              onClick={() => {
                setNameDraft(project.name);
                setEditingName(false);
              }}
            >
              <X size={16} /> Cancelar
            </button>
          </>
        ) : (
          <button
            type="button"
            className="ghost-action"
            disabled={busy}
            onClick={() => setEditingName(true)}
          >
            <Pencil size={16} /> Renombrar
          </button>
        )}
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
        {archived ? (
          <button
            type="button"
            className="danger-action"
            disabled={busy}
            onClick={() => onDelete(project)}
          >
            <Trash2 size={16} /> Eliminar definitivamente
          </button>
        ) : null}
      </div>
    </article>
  );
}

export default function ProjectsView({
  request,
  onOpenProject,
  onActivateProject,
  projectDraft,
  projectAdoptionDraft,
  activeProjectId,
  onProjectDeleted,
}) {
  const [activeProjects, setActiveProjects] = useState([]);
  const [archivedProjects, setArchivedProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyProjectId, setBusyProjectId] = useState("");
  const [error, setError] = useState("");
  const creatingRef = useRef(false);
  const abortControllerRef = useRef(null);
  const loadGuardRef = useRef(null);

  function canUpdate() {
    return loadGuardRef.current?.isMounted() || false;
  }

  const loadProjects = useCallback(async () => {
    const guard = loadGuardRef.current;
    if (!guard) return;
    const epoch = guard.begin();
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    if (guard.canApply(epoch)) {
      setLoading(true);
      setError("");
    }
    try {
      const [active, archived] = await Promise.all([
        request("/projects?status=active", { signal: controller.signal }),
        request("/projects?status=archived", { signal: controller.signal }),
      ]);
      if (!guard.canApply(epoch)) return;
      setActiveProjects(active.projects || []);
      setArchivedProjects(archived.projects || []);
    } catch (failure) {
      if (!guard.canApply(epoch)) return;
      setError(failure.message || "No se pudieron cargar los proyectos.");
    } finally {
      if (guard.canApply(epoch)) setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    const guard = createProjectLoadGuard();
    loadGuardRef.current = guard;
    loadProjects();
    return () => {
      abortControllerRef.current?.abort();
      guard.dispose();
    };
  }, [loadProjects]);

  async function duplicate(project) {
    if (!canUpdate()) return;
    setBusyProjectId(project.id);
    setError("");
    try {
      await request(`/projects/${project.id}/duplicate`, { method: "POST" });
      await loadProjects();
    } catch (failure) {
      if (canUpdate()) setError(failure.message || "No se pudo duplicar el proyecto.");
    } finally {
      if (canUpdate()) setBusyProjectId("");
    }
  }

  async function changeStatus(project, action) {
    if (!canUpdate()) return;
    setBusyProjectId(project.id);
    setError("");
    try {
      await request(`/projects/${project.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({
          expected_revision: project.revision,
          operation_id: createProjectOperationId(),
        }),
      });
      await loadProjects();
    } catch (failure) {
      if (canUpdate()) setError(failure.message || "No se pudo actualizar el proyecto.");
    } finally {
      if (canUpdate()) setBusyProjectId("");
    }
  }

  async function renameProject(project, name) {
    if (!canUpdate()) return false;
    setBusyProjectId(project.id);
    setError("");
    try {
      await request(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name,
          payload: project.payload,
          expected_revision: project.revision,
          operation_id: createProjectOperationId(),
        }),
      });
      await loadProjects();
      return true;
    } catch (failure) {
      if (canUpdate()) setError(failure.message || "No se pudo renombrar el Proyecto.");
      return false;
    } finally {
      if (canUpdate()) setBusyProjectId("");
    }
  }

  async function deleteProject(project) {
    if (!canUpdate() || project.status !== "archived") return;
    const confirmation = window.prompt(
      `Escribe el nombre exacto "${project.name}" para eliminar definitivamente este Proyecto.`,
    );
    if (confirmation === null) return;
    if (confirmation !== project.name) {
      setError("Escribe el nombre exacto del Proyecto para eliminarlo.");
      return;
    }
    setBusyProjectId(project.id);
    setError("");
    try {
      await request(`/projects/${project.id}`, {
        method: "DELETE",
        body: JSON.stringify({
          expected_revision: project.revision,
          confirm_name: confirmation,
        }),
      });
      onProjectDeleted?.(project.id);
      await loadProjects();
    } catch (failure) {
      if (canUpdate()) setError(failure.message || "No se pudo eliminar el Proyecto.");
    } finally {
      if (canUpdate()) setBusyProjectId("");
    }
  }

  async function createProject() {
    if (!canUpdate() || creatingRef.current) return;
    setCreating(true);
    setError("");
    try {
      await createNewProject(
        request,
        onActivateProject,
        projectDraft,
        creatingRef,
        projectAdoptionDraft,
      );
    } catch (failure) {
      if (canUpdate()) setError(failure.message || "No se pudo crear el Proyecto.");
    } finally {
      if (canUpdate()) setCreating(false);
    }
  }

  return (
    <section className="projects-workspace" aria-labelledby="projects-heading">
      <div className="projects-heading">
        <div>
          <h2 id="projects-heading">Proyectos</h2>
          <p>Abre, duplica o archiva los proyectos de tu espacio de trabajo.</p>
        </div>
        <div className="projects-heading-actions">
          <button className="primary-action" type="button" onClick={createProject} disabled={creating}>
            {creating ? <Loader2 className="spin" size={16} /> : <Plus size={16} />} Nuevo Proyecto
          </button>
          <button className="ghost-action" type="button" onClick={loadProjects} disabled={loading}>
            {loading ? <Loader2 className="spin" size={16} /> : null} Actualizar
          </button>
        </div>
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
            onRename={renameProject}
            onDelete={deleteProject}
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
            onRename={renameProject}
            onDelete={deleteProject}
          />
        ))}
      </section>
    </section>
  );
}
