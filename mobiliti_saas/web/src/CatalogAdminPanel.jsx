import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, UploadCloud, XCircle } from "lucide-react";

const SUPPLIERS = [
  ["cr-global", "CR Global"],
  ["sonara", "Sonara"],
  ["sunon", "Sunon"],
  ["alma", "ALMA"],
  ["lumbro", "Lumbro"],
  ["jome", "JOME"],
  ["lauco", "Lauco"],
  ["idelika", "IDÉLIKA"],
  ["conceptos", "Conceptos"]
];

function formatDate(value) {
  return value ? new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Sin fecha";
}

function metricValue(value) {
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function changedFields(diff, metrics) {
  if (Array.isArray(diff?.items)) return diff.items;
  if (Array.isArray(metrics?.changed_fields)) return metrics.changed_fields;
  if (Array.isArray(metrics?.diff?.items)) return metrics.diff.items;
  if (Array.isArray(metrics?.differences)) return metrics.differences;
  return [];
}

export default function CatalogAdminPanel({ request }) {
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [file, setFile] = useState(null);
  const [itemIndex, setItemIndex] = useState("0");
  const [imageKind, setImageKind] = useState("official");
  const [imageLabel, setImageLabel] = useState("");
  const [imageReferences, setImageReferences] = useState("");
  const fileInputRef = useRef(null);

  function resetCuration() {
    setNote("");
    setFile(null);
    setItemIndex("0");
    setImageKind("official");
    setImageLabel("");
    setImageReferences("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function updateSelected(run) {
    if (run?.id !== selected?.id) resetCuration();
    setSelected(run);
  }

  async function loadRuns(runId = "") {
    const data = await request("/admin/catalog-sync-runs");
    const nextRuns = Array.isArray(data.runs) ? data.runs : [];
    setRuns(nextRuns);
    const targetId = runId || selected?.id;
    if (targetId) {
      const detail = await request(`/admin/catalog-sync-runs/${targetId}`);
      updateSelected(detail.run || null);
    }
  }

  useEffect(() => {
    setBusy("loading");
    loadRuns().catch((err) => setError(err.message)).finally(() => setBusy(""));
  }, []);

  async function selectRun(runId) {
    setError("");
    setBusy(`run:${runId}`);
    try {
      const data = await request(`/admin/catalog-sync-runs/${runId}`);
      updateSelected(data.run || null);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function sync(supplier) {
    setError("");
    setMessage("");
    setBusy(`sync:${supplier}`);
    try {
      const data = await request(`/admin/catalog-sync/${supplier}`, { method: "POST" });
      setMessage(data.mensaje || "Sincronizacion solicitada");
      await loadRuns(data.run?.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function review(action) {
    if (!selected?.id || busy) return;
    if (action === "reject" && !note.trim()) {
      setError("La nota de rechazo es requerida.");
      return;
    }
    if (!window.confirm(`${action === "approve" ? "Aprobar" : "Rechazar"} este candidato?`)) return;
    setError("");
    setMessage("");
    setBusy(action);
    try {
      const data = await request(`/admin/catalog-sync-runs/${selected.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ review_note: note.trim() })
      });
      setMessage(data.mensaje || "Revision registrada");
      setNote("");
      await loadRuns(selected.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function uploadImage(event) {
    event.preventDefault();
    if (!selected?.id || !file || busy) return;
    const references = imageReferences.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (imageKind === "generated_reference" && (!imageLabel.trim() || !references.length)) {
      setError("La imagen de referencia requiere etiqueta y una referencia HTTPS.");
      return;
    }
    setError("");
    setMessage("");
    setBusy("image");
    try {
      const form = new FormData();
      form.append("item_index", itemIndex);
      form.append("file", file);
      form.append("image_kind", imageKind);
      form.append("image_label", imageLabel.trim());
      form.append("image_references", JSON.stringify(references));
      const data = await request(`/admin/catalog-sync-runs/${selected.id}/images`, { method: "POST", body: form });
      setMessage(data.mensaje || "Imagen aprobada");
      setFile(null);
      await loadRuns(selected.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  const metrics = selected?.metrics && typeof selected.metrics === "object" ? selected.metrics : {};
  const fields = changedFields(selected?.diff, metrics);
  const canReview = selected?.status === "awaiting_approval" && !busy;

  return (
    <section className="catalog-admin" aria-busy={Boolean(busy)}>
      <header className="catalog-admin-header">
        <div>
          <h2>Revision de catalogos</h2>
          <p>Sincronizaciones pendientes y cambios detectados por proveedor.</p>
        </div>
        <button type="button" className="icon-action" title="Actualizar sincronizaciones" aria-label="Actualizar sincronizaciones" onClick={() => loadRuns().catch((err) => setError(err.message))} disabled={Boolean(busy)}>
          <RefreshCw size={18} className={busy ? "spin" : ""} />
        </button>
      </header>

      <div className="catalog-admin-suppliers">
        {SUPPLIERS.map(([supplier, label]) => (
          <button type="button" key={supplier} onClick={() => sync(supplier)} disabled={Boolean(busy)}>
            {busy === `sync:${supplier}` ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            Sincronizar ahora {label}
          </button>
        ))}
      </div>
      {error ? <div className="error-line" role="alert">{error}</div> : null}
      {message ? <div className="success-line" role="status" aria-live="polite">{message}</div> : null}
      <span className="sr-only" role="status" aria-live="polite">{busy ? "Procesando solicitud" : ""}</span>

      <div className="catalog-admin-layout">
        <div className="catalog-admin-runs" aria-label="Sincronizaciones de catalogo">
          {runs.map((run) => (
            <button type="button" key={run.id} className={`catalog-admin-run ${selected?.id === run.id ? "selected" : ""}`} onClick={() => selectRun(run.id)} disabled={Boolean(busy)}>
              <strong>{run.label || run.supplier || "Proveedor"}</strong>
              <span>{run.status || "Sin estado"}</span>
              <small>{formatDate(run.requested_at || run.updated_at)}</small>
            </button>
          ))}
          {!runs.length ? <p className="empty">No hay sincronizaciones registradas.</p> : null}
        </div>

        <div className="catalog-admin-detail">
          {!selected ? <p className="empty">Selecciona una sincronizacion para revisar sus detalles.</p> : <>
            <div className="catalog-admin-summary">
              <strong>{selected.label || selected.supplier}</strong>
              <span className={`status ${selected.status}`}>{selected.status}</span>
              <span>Solicitada: {formatDate(selected.requested_at)}</span>
              {selected.started_at ? <span>Inicio: {formatDate(selected.started_at)}</span> : null}
              {selected.finished_at ? <span>Fin: {formatDate(selected.finished_at)}</span> : null}
            </div>
            {Object.keys(metrics).length ? <dl className="catalog-admin-metrics">
              {Object.entries(metrics).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{metricValue(value)}</dd></div>)}
            </dl> : null}
            {fields.length ? <div className="catalog-admin-diff">
              <h3>Diferencias</h3>
              {fields.map((field, index) => <dl key={`${field.field || field.name || "field"}-${index}`}>
                {field.field || field.name ? <><dt>Campo cambiado</dt><dd>{field.field || field.name}</dd></> : null}
                {Object.hasOwn(field, "before") || Object.hasOwn(field, "old_value") || Object.hasOwn(field, "previous") ? <><dt>Valor anterior</dt><dd>{metricValue(field.old_value ?? field.before ?? field.previous ?? "Sin valor")}</dd></> : null}
                {Object.hasOwn(field, "after") || Object.hasOwn(field, "new_value") || Object.hasOwn(field, "current") ? <><dt>Valor nuevo</dt><dd>{metricValue(field.new_value ?? field.after ?? field.current ?? "Sin valor")}</dd></> : null}
                {field.source_coordinate || field.source_reference ? <><dt>Coordenada fuente</dt><dd>{field.source_coordinate || field.source_reference}</dd></> : null}
                {field.material_type ? <><dt>Tipo material</dt><dd>{field.material_type}</dd></> : null}
              </dl>)}
            </div> : null}
            <label className="catalog-admin-note">Nota de revision (opcional para aprobar)
              <textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength="2000" />
            </label>
            <div className="catalog-admin-actions">
              <button type="button" className="primary-action" onClick={() => review("approve")} disabled={!canReview}>
                <CheckCircle2 size={17} /> Aceptar: Aprobar
              </button>
              <button type="button" className="danger-action" onClick={() => review("reject")} disabled={!canReview}>
                <XCircle size={17} /> Rechazar
              </button>
            </div>
            <form className="catalog-admin-upload" onSubmit={uploadImage}>
              <h3>Adjuntar imagen aprobada</h3>
              <label>Indice del producto<input type="number" min="0" value={itemIndex} onChange={(event) => setItemIndex(event.target.value)} required /></label>
              <label>Archivo de imagen<input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label>
              <label>Tipo de imagen<select value={imageKind} onChange={(event) => setImageKind(event.target.value)}><option value="official">Oficial</option><option value="generated_reference">Imagen de referencia</option></select></label>
              {imageKind === "generated_reference" ? <>
                <label>Etiqueta<input value={imageLabel} onChange={(event) => setImageLabel(event.target.value)} required /></label>
                <label>Referencias HTTPS<textarea value={imageReferences} onChange={(event) => setImageReferences(event.target.value)} required /></label>
              </> : null}
              <button className="primary-action" disabled={!canReview || !file || busy === "image"}>
                {busy === "image" ? <Loader2 className="spin" size={17} /> : <UploadCloud size={17} />} Adjuntar imagen aprobada
              </button>
            </form>
          </>}
        </div>
      </div>
    </section>
  );
}
