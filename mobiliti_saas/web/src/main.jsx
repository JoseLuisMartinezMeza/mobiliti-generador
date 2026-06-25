import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDownToLine,
  CheckCircle2,
  Circle,
  Clock3,
  FileSpreadsheet,
  History,
  LayoutDashboard,
  Loader2,
  LogOut,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Trash2,
  UploadCloud,
  UserRound,
  UsersRound,
  XCircle
} from "lucide-react";
import "./styles.css";

const DEFAULT_API_BASE = ["127.0.0.1", "localhost"].includes(
  typeof window !== "undefined" ? window.location.hostname : ""
) ? "http://127.0.0.1:8000" : "";
const API_BASE = import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE;
const AUTH_EXPIRED_EVENT = "mobiliti:auth-expired";
const AUTH_EXPIRED_MESSAGE = "Tu sesion expiro. Vuelve a iniciar sesion para generar cotizaciones.";
const MAX_QUOTE_INPUT_MB = 25;

class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function apiUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  const base = API_BASE.replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

function isAuthExpiredResponse(status, detail) {
  const text = String(detail || "").toLowerCase();
  return status === 401 && text.includes("token") && (text.includes("expirado") || text.includes("invalido"));
}

function notifyAuthExpired() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}

function filenameFromDisposition(disposition, fallback) {
  const match = /filename="?([^"]+)"?/i.exec(disposition || "");
  return match?.[1] || fallback;
}

function isSupportedQuoteInput(fileName) {
  return /\.(xlsx|pdf)$/i.test(String(fileName || ""));
}

function quoteInputContentType(fileName) {
  return String(fileName || "").toLowerCase().endsWith(".pdf")
    ? "application/pdf"
    : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
}

function safeFilenamePart(value, limit = 80) {
  const cleaned = String(value || "")
    .trim()
    .replace(/[^\p{L}\p{N}_-]+/gu, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  return cleaned.slice(0, limit);
}

function quoteDownloadFallbackName(job) {
  const metadata = job?.metadata || {};
  const project = safeFilenamePart(metadata.proyecto, 80);
  const quoteNumber = safeFilenamePart(metadata.cotizacion, 40);
  const fallback = safeFilenamePart(job?.id, 80) || "cotizacion";
  const name = project && quoteNumber ? `${project}_${quoteNumber}` : quoteNumber || project || fallback;
  return `Cotizacion_${name.slice(0, 140)}.xlsx`;
}

function withDownloadFilename(url, filename) {
  try {
    const parsed = new URL(url);
    parsed.searchParams.set("download", filename);
    return parsed.toString();
  } catch {
    const separator = String(url).includes("?") ? "&" : "?";
    return `${url}${separator}download=${encodeURIComponent(filename)}`;
  }
}

async function downloadJobFile(job, token) {
  if (!job?.id) return "";
  const res = await fetch(apiUrl(`/cotizaciones/${job.id}/download`), {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data.detail || "Error descargando cotizacion";
    if (isAuthExpiredResponse(res.status, detail)) {
      notifyAuthExpired();
      throw new ApiError(AUTH_EXPIRED_MESSAGE, res.status);
    }
    throw new ApiError(detail, res.status);
  }
  const data = await res.json();
  const signedUrl = data.download_url;
  if (!signedUrl) throw new ApiError("La API no devolvio una URL de descarga", res.status);
  const filename = quoteDownloadFallbackName(job);
  const link = document.createElement("a");
  link.href = withDownloadFilename(signedUrl, filename);
  link.download = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  return link.download;
}

const DEFAULT_IMAGE_PROMPT = "Mejora la calidad de imagen y que este en fondo blanco";

const emptyQuote = {
  cotizacion: "",
  proyecto: "",
  cliente: "",
  correo: "",
  telefono: "",
  direccion: "",
  razon_social: "",
  descuento: "40",
  description_language: "es",
  image_provider: "dezgo",
  image_cleanup_strength: "balanced",
  image_background: "white",
  image_prompt: DEFAULT_IMAGE_PROMPT,
  template: "Formato Cotizacion 2026 GDL (1).xlsx"
};

const statusLabels = {
  draft: "Archivo preparado",
  queued: "En cola",
  processing: "Procesando datos",
  completed: "Cotizacion lista",
  failed: "Error al generar"
};

const fallbackProgress = {
  draft: 10,
  queued: 30,
  processing: 70,
  completed: 100,
  failed: 100
};

function jobProgress(job) {
  const raw = Number(job?.metadata?.progress_percent);
  if (Number.isFinite(raw)) return Math.max(0, Math.min(100, raw));
  return fallbackProgress[job?.status] ?? 0;
}

function isActiveJob(job) {
  return ["draft", "queued", "processing"].includes(job?.status);
}

function jobDurationMs(job, now = Date.now()) {
  const start = Date.parse(job?.created_at || "");
  if (!Number.isFinite(start)) return 0;
  const finished = Date.parse(job?.completed_at || job?.updated_at || "");
  const end = isActiveJob(job) || !Number.isFinite(finished) ? now : finished;
  return Math.max(0, end - start);
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatDurationApprox(ms) {
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  if (totalSeconds < 60) return `~${totalSeconds}s`;
  const minutes = Math.max(1, Math.round(totalSeconds / 60));
  if (minutes < 60) return `~${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `~${hours}h ${rest}m` : `~${hours}h`;
}

function estimatedJobDurationMs(job, now = Date.now()) {
  const metadata = job?.metadata || {};
  const explicitSeconds = Number(metadata.estimated_duration_seconds);
  const measuredSeconds = Number(metadata.generation_seconds);
  if (job?.status === "completed" && Number.isFinite(measuredSeconds) && measuredSeconds > 0) {
    return measuredSeconds * 1000;
  }
  const provider = String(metadata.image_provider || "").toLowerCase();
  const sourceImages = Number(metadata.image_source_count);
  const generatedImages = Number(metadata.image_ai_missing_attempted_count || metadata.image_ai_generated_count);
  const baseline = provider === "dezgo" ? 360000 : 90000;
  const imageBudget = provider === "dezgo"
    ? (Number.isFinite(sourceImages) ? sourceImages * 22000 : 0) + (Number.isFinite(generatedImages) ? generatedImages * 35000 : 0)
    : 0;
  const configured = Number.isFinite(explicitSeconds) && explicitSeconds > 0 ? explicitSeconds * 1000 : 0;
  const elapsed = jobDurationMs(job, now);
  const progress = jobProgress(job);
  const dynamic = progress > 12 && elapsed > 4000 ? elapsed / (progress / 100) : 0;
  return Math.min(1800000, Math.max(baseline + imageBudget, configured, dynamic));
}

function generationLabel(job, now = Date.now()) {
  const elapsed = jobDurationMs(job, now);
  if (job?.status === "completed") return `Tardo ${formatDuration(elapsed)}`;
  if (job?.status === "failed") return `Fallo tras ${formatDuration(elapsed)}`;
  if (job?.status === "draft" || job?.status === "queued") {
    return `Estimado aprox. ${formatDurationApprox(estimatedJobDurationMs(job, now))}`;
  }
  if (job?.status === "processing") {
    const remaining = Math.max(0, estimatedJobDurationMs(job, now) - elapsed);
    return `Faltan aprox. ${formatDurationApprox(remaining)}`;
  }
  return "";
}

function useTicker(active) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

async function runDownload(job, token, setDownloadState) {
  if (!job?.id) return "";
  const startedAt = Date.now();
  setDownloadState({ jobId: job.id, startedAt, status: "downloading" });
  try {
    const filename = await downloadJobFile(job, token);
    setDownloadState({ jobId: job.id, startedAt, finishedAt: Date.now(), status: "ready", filename });
    setTimeout(() => {
      setDownloadState((current) => (current?.jobId === job.id ? null : current));
    }, 5000);
    return filename;
  } catch (err) {
    setDownloadState({ jobId: job.id, startedAt, finishedAt: Date.now(), status: "failed", error: err.message });
    setTimeout(() => {
      setDownloadState((current) => (current?.jobId === job.id ? null : current));
    }, 7000);
    throw err;
  }
}

function useApi(token) {
  return useMemo(() => {
    async function request(path, options = {}) {
      const headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
      };
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(apiUrl(path), { ...options, headers });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail || data.message || "Error de API";
        if (isAuthExpiredResponse(res.status, detail)) {
          notifyAuthExpired();
          throw new ApiError(AUTH_EXPIRED_MESSAGE, res.status);
        }
        throw new ApiError(detail, res.status);
      }
      return data;
    }
    return { request };
  }, [token]);
}

function Login({ onLogin, notice = "" }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const api = useApi("");

  async function submit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const session = await api.request("/login", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      onLogin(session);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="brand-lockup">
          <div className="brand-mark">M</div>
          <div>
            <strong>Mobiliti</strong>
            <span>Cotizaciones</span>
          </div>
        </div>
        <h1>Acceso al cotizador</h1>
        <p>Genera propuestas desde una cotizacion de proveedor y consulta el historial de salida.</p>
        <form className="login-form" onSubmit={submit}>
          <label>
            Correo
            <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required />
          </label>
          <label>
            Contraseña
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" required />
          </label>
          {notice ? <div className="notice-line">{notice}</div> : null}
          {error ? <div className="error-line">{error}</div> : null}
          <button className="primary-action" disabled={loading}>
            {loading ? <Loader2 className="spin" size={18} /> : <LayoutDashboard size={18} />}
            Entrar
          </button>
        </form>
      </section>
    </main>
  );
}

function Sidebar({ view, setView, isAdmin, onLogout }) {
  const items = [
    ["cotizaciones", "Cotizaciones", FileSpreadsheet],
    ["nueva", "Nueva", UploadCloud],
    ["historial", "Historial", History],
    ["clientes", "Clientes", UsersRound],
    ["admin", "Admin", Settings]
  ];
  return (
    <aside className="sidebar">
      <div className="brand-lockup compact">
        <div className="brand-mark">M</div>
        <div>
          <strong>Mobiliti</strong>
          <span>Cotizaciones</span>
        </div>
      </div>
      <nav>
        {items.map(([key, label, Icon]) => (
          <button
            key={key}
            className={view === key ? "active" : ""}
            onClick={() => setView(key)}
            disabled={(key === "admin" || key === "clientes") && !isAdmin}
          >
            <Icon size={21} />
            {label}
            {key === "admin" && isAdmin ? <em>Admin</em> : null}
          </button>
        ))}
      </nav>
      <div className="side-bottom">
        <button onClick={onLogout}>
          <LogOut size={21} />
          Cerrar sesion
        </button>
      </div>
    </aside>
  );
}

function Header({ user, subscription }) {
  const initials = (user?.nombre || user?.email || "AV")
    .split(/\s|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  return (
    <header className="topbar">
      <div>
        <span>Proyecto</span>
        <h1>{user?.empresa || "Mobiliti Cotizaciones"}</h1>
      </div>
      <div className="subscription-pill">
        <span>Suscripcion</span>
        <strong>{subscription?.plan || "Plan activo"}</strong>
        <small>Vence: {formatDate(subscription?.fecha_fin)}</small>
      </div>
      <div className="user-chip">
        <div>{initials}</div>
        <span>{user?.nombre || user?.email}<small>{user?.es_admin ? "Administrador" : "Usuario"}</small></span>
      </div>
    </header>
  );
}

function QuoteForm({ token, onJobChange, recentJobs, refreshJobs, onOpenHistory }) {
  const { request } = useApi(token);
  const [form, setForm] = useState(emptyQuote);
  const [file, setFile] = useState(null);
  const [job, setJob] = useState(null);
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadState, setDownloadState] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!job?.id || !["queued", "processing", "draft"].includes(job.status)) return;
    const id = setInterval(async () => {
      try {
        const data = await request(`/cotizaciones/${job.id}`);
        setJob(data.job);
        onJobChange(data.job);
        if (data.job.status === "completed" || data.job.status === "failed") {
          refreshJobs();
        }
      } catch (err) {
        setError(err.message);
      }
    }, 3500);
    return () => clearInterval(id);
  }, [job?.id, job?.status, onJobChange, refreshJobs, request]);

  function updateField(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateDiscount(value) {
    if (value === "") {
      updateField("descuento", value);
      return;
    }
    const numeric = Math.max(0, Math.min(Number(value), 100));
    updateField("descuento", Number.isFinite(numeric) ? String(numeric) : "40");
  }

  function selectFile(nextFile) {
    setError("");
    if (!nextFile) {
      setFile(null);
      return;
    }
    if (!isSupportedQuoteInput(nextFile.name)) {
      setFile(null);
      setError("Selecciona un archivo .xlsx o .pdf valido.");
      return;
    }
    if (nextFile.size > MAX_QUOTE_INPUT_MB * 1024 * 1024) {
      setFile(null);
      setError(`El archivo supera el limite de ${MAX_QUOTE_INPUT_MB} MB.`);
      return;
    }
    setFile(nextFile);
  }

  function dropFile(event) {
    event.preventDefault();
    setIsDragging(false);
    selectFile(event.dataTransfer.files?.[0] || null);
  }

  async function createQuote(event) {
    event.preventDefault();
    setError("");
    setDownloadUrl("");
    setDownloadState(null);

    if (!file) {
      setError("Selecciona un archivo .xlsx o .pdf primero.");
      return;
    }
    setBusy(true);
    try {
      const init = await request("/cotizaciones/init-upload", {
        method: "POST",
        body: JSON.stringify({ filename: file.name, size: file.size, template: form.template })
      });
      if (init.signed_upload_url) {
        const uploadRes = await fetch(init.signed_upload_url, {
          method: "PUT",
          headers: {
            "Content-Type": quoteInputContentType(file.name)
          },
          body: file
        });
        const uploadData = await uploadRes.json().catch(() => ({}));
        if (!uploadRes.ok) throw new Error(uploadData.message || uploadData.error || "Error subiendo archivo");
      } else if (init.upload_url) {
        const body = new FormData();
        body.append("file", file);
        const uploadRes = await fetch(apiUrl(init.upload_url), {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body
        });
        const uploadData = await uploadRes.json().catch(() => ({}));
        if (!uploadRes.ok) throw new Error(uploadData.detail || "Error subiendo archivo");
      } else {
        throw new Error("La API no devolvio una ruta de carga valida.");
      }

      const submitted = await request(`/cotizaciones/${init.job_id}/submit`, {
        method: "POST",
        body: JSON.stringify(form)
      });
      setJob(submitted.job);
      onJobChange(submitted.job);
      refreshJobs();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function download(jobToDownload = job) {
    if (!jobToDownload?.id) return;
    setError("");
    try {
      const filename = await runDownload(jobToDownload, token, setDownloadState);
      setDownloadUrl(filename);
    } catch (err) {
      setError(err.message);
    }
  }

  async function retry(jobToRetry) {
    if (!jobToRetry?.id) return;
    setError("");
    try {
      const data = await request(`/cotizaciones/${jobToRetry.id}/retry`, { method: "POST" });
      setJob(data.job);
      onJobChange(data.job);
      refreshJobs();
    } catch (err) {
      setError(err.message);
    }
  }

  const displayJob = job || recentJobs[0];

  return (
    <div className="workspace-grid">
      <section className="main-card">
        <div className="card-head">
          <h2>Nueva cotizacion</h2>
          <p>Carga la cotizacion del proveedor y genera la propuesta para tu cliente.</p>
        </div>
        <form onSubmit={createQuote} className="quote-form">
          <h3>1. Cargar cotizacion del proveedor (Excel o PDF)</h3>
          <button
            className={`dropzone ${isDragging ? "dragging" : ""}`}
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={dropFile}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.pdf"
              onChange={(event) => selectFile(event.target.files?.[0] || null)}
              hidden
            />
            <FileSpreadsheet size={44} />
            <strong>{file ? file.name : "Arrastra y suelta tu archivo aqui"}</strong>
            <span>{file ? formatBytes(file.size) : "o selecciona un archivo .xlsx o .pdf"}</span>
            <small>Tamano maximo: {MAX_QUOTE_INPUT_MB} MB</small>
          </button>

          <h3>2. Informacion de la cotizacion</h3>
          <div className="form-grid">
            <Field
              label="Numero de cotizacion"
              value={form.cotizacion}
              onChange={() => {}}
              required={false}
              readOnly
              placeholder="Automatico por usuario"
            />
            <Field label="Proyecto" value={form.proyecto} onChange={(value) => updateField("proyecto", value)} />
            <Field label="Cliente" value={form.cliente} onChange={(value) => updateField("cliente", value)} />
            <Field label="Correo" type="email" value={form.correo} onChange={(value) => updateField("correo", value)} />
            <Field label="Telefono" value={form.telefono} onChange={(value) => updateField("telefono", value)} />
            <Field label="Direccion" value={form.direccion} onChange={(value) => updateField("direccion", value)} />
            <Field label="Descuento (%)" type="number" min="0" max="100" value={form.descuento} onChange={updateDiscount} />
            <Field label="Razon social" value={form.razon_social} onChange={(value) => updateField("razon_social", value)} wide />
          </div>

          <h3>3. Plantilla y render</h3>
          <div className="template-grid">
            <select value={form.template} onChange={(event) => updateField("template", event.target.value)}>
              <option>Formato Cotizacion 2026 GDL (1).xlsx</option>
              <option>Plantilla Corporativa Mobiliti 2025</option>
            </select>
            <select value={form.description_language} onChange={(event) => updateField("description_language", event.target.value)}>
              <option value="es">Descripciones en espanol</option>
              <option value="en">Descripciones en ingles</option>
            </select>
            <select value={form.image_provider} onChange={(event) => updateField("image_provider", event.target.value)}>
              <option value="dezgo">IA Dezgo recomendado - genera faltantes realistas</option>
              <option value="pillow">Local sin IA - no inventa imagenes faltantes</option>
            </select>
            <select value={form.image_cleanup_strength} onChange={(event) => updateField("image_cleanup_strength", event.target.value)}>
              <option value="balanced">Limpieza balanceada</option>
              <option value="normal">Limpieza conservadora</option>
              <option value="aggressive">Limpieza fuerte</option>
            </select>
            <div className="render-summary">
              <Sparkles size={18} />
              <span>
                {form.image_provider === "dezgo" ? "IA Dezgo mejora y genera faltantes" : "Render local solo mejora imagenes existentes"} - Fondo {form.image_background === "white" ? "blanco" : "transparente"}
              </span>
            </div>
            <label className="prompt-field">
              Prompt para imagenes
              <textarea
                value={form.image_prompt}
                onChange={(event) => updateField("image_prompt", event.target.value)}
                placeholder={DEFAULT_IMAGE_PROMPT}
                rows={3}
              />
            </label>
          </div>

          {error ? <div className="error-line">{error}</div> : null}
          <DownloadStatusLine state={downloadState} />
          {downloadUrl && !downloadState ? <div className="download-line">Ultima descarga: {downloadUrl}</div> : null}

          <div className="actions-row">
            <button className="primary-action" disabled={busy}>
              {busy ? <Loader2 className="spin" size={18} /> : <FileSpreadsheet size={18} />}
              Generar cotizacion
            </button>
          </div>
        </form>
      </section>
      <aside className="right-column">
        <StatusPanel job={displayJob} />
        <RecentOutputs jobs={recentJobs} onDownload={download} onRetry={retry} onOpenHistory={onOpenHistory} downloadState={downloadState} />
      </aside>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", wide = false, min, max, required = true, readOnly = false, placeholder = "" }) {
  return (
    <label className={wide ? "wide" : ""}>
      {label}{required ? " *" : ""}
      <input
        type={type}
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        readOnly={readOnly}
        placeholder={placeholder}
      />
    </label>
  );
}

function JobDuration({ job, tone = "" }) {
  const active = isActiveJob(job);
  const now = useTicker(active);
  const label = generationLabel(job, now);
  if (!label) return null;

  return (
    <span className={`job-duration ${active ? "active" : ""} ${tone}`.trim()}>
      <Clock3 size={14} />
      {label}
    </span>
  );
}

function DownloadButton({ job, onDownload, downloadState, className = "ghost-action" }) {
  const isCurrent = downloadState?.jobId === job?.id;
  const downloading = isCurrent && downloadState.status === "downloading";
  const ready = isCurrent && downloadState.status === "ready";
  const failed = isCurrent && downloadState.status === "failed";
  const now = useTicker(downloading);
  const elapsed = downloading ? formatDuration(now - downloadState.startedAt) : "";
  const disabled = job?.status !== "completed" || downloading;
  let label = "Descargar";

  if (downloading) label = `Descargando ${elapsed}`;
  else if (ready) label = "Descarga lista";
  else if (failed) label = "Error al descargar";

  return (
    <button
      className={`${className} ${isCurrent ? `download-${downloadState.status}` : ""}`.trim()}
      type="button"
      onClick={() => onDownload(job)}
      disabled={disabled}
    >
      {downloading ? <Loader2 className="spin" size={16} /> : ready ? <CheckCircle2 size={16} /> : <ArrowDownToLine size={16} />}
      {label}
    </button>
  );
}

function DownloadStatusLine({ state }) {
  const downloading = state?.status === "downloading";
  const now = useTicker(downloading);
  if (!state) return null;

  const elapsed = formatDuration((state.finishedAt || now) - state.startedAt);
  if (state.status === "failed") {
    return <div className="download-line failed">No se pudo descargar: {state.error}</div>;
  }
  if (state.status === "ready") {
    return <div className="download-line">Descarga iniciada: {state.filename} en {elapsed}</div>;
  }
  return (
    <div className="download-line active">
      <Loader2 className="spin" size={15} />
      Preparando descarga... {elapsed}
    </div>
  );
}

function StatusPanel({ job }) {
  const status = job?.status || "draft";
  const progress = jobProgress(job);
  const steps = [
    ["draft", "Archivo cargado", "Quotation listo para enviar"],
    ["queued", "En cola", "Esperando worker online"],
    ["processing", "Procesando datos", "Aplicando template y reglas de negocio"],
    ["completed", "Finalizado", "Archivo listo para descarga"]
  ];
  if (status === "failed") steps.push(["failed", "Error al generar", job?.error_message || "Revisa el worker"]);
  const statusOrder = ["draft", "queued", "processing", "completed"];
  const activeIndex = statusOrder.indexOf(status);

  return (
    <section className="side-card">
      <h2>Estado de generacion</h2>
      <div className="progress-card">
        <div className="progress-row">
          <strong>{statusLabels[status] || status}</strong>
          <span>{progress}%</span>
        </div>
        <div className="progress-track">
          <div className={`progress-fill ${status}`} style={{ width: `${progress}%` }} />
        </div>
        <JobDuration job={job} tone="panel" />
      </div>
      <div className="timeline">
        {steps.map(([key, title, desc], index) => {
          const done = key === "failed" ? false : index <= activeIndex;
          const active = key === status;
          return (
            <div className={`timeline-item ${done ? "done" : ""} ${active ? "current" : ""}`} key={key}>
              {key === "failed" ? <XCircle size={22} /> : done ? <CheckCircle2 size={22} /> : active ? <Clock3 size={22} /> : <Circle size={22} />}
              <div>
                <strong>{title}</strong>
                <span>{desc}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function RecentOutputs({ jobs, onDownload, onRetry, onOpenHistory, downloadState }) {
  return (
    <section className="side-card">
      <div className="section-row">
        <h2>Salidas recientes</h2>
        {onOpenHistory ? <button type="button" onClick={onOpenHistory}>Ver historial</button> : null}
      </div>
      <div className="output-list">
        {jobs.slice(0, 5).map((job) => (
          <div className="output-row" key={job.id}>
            <FileSpreadsheet size={24} />
            <div>
              <strong>{job.metadata?.cotizacion || job.metadata?.original_filename || "Cotizacion"}</strong>
              <span>{statusLabels[job.status] || job.status} - {formatDate(job.updated_at)}</span>
              <JobDuration job={job} />
              <div className="mini-progress-track">
                <div className={`mini-progress-fill ${job.status}`} style={{ width: `${jobProgress(job)}%` }} />
              </div>
            </div>
            {job.status === "failed" ? (
              <button onClick={() => onRetry(job)}>
                <Clock3 size={16} />
                Reintentar
              </button>
            ) : (
              <DownloadButton job={job} onDownload={onDownload} downloadState={downloadState} className="" />
            )}
          </div>
        ))}
        {!jobs.length ? <p className="empty">Aun no hay cotizaciones generadas.</p> : null}
      </div>
    </section>
  );
}

function QuotesView({ jobs, onDownload, onRetry, onCreateNew, refreshJobs, downloadState }) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const stats = useMemo(() => ({
    total: jobs.length,
    completed: jobs.filter((job) => job.status === "completed").length,
    active: jobs.filter((job) => ["draft", "queued", "processing"].includes(job.status)).length,
    failed: jobs.filter((job) => job.status === "failed").length
  }), [jobs]);

  const filteredJobs = useMemo(() => {
    const cleanQuery = query.trim().toLowerCase();
    return jobs.filter((job) => {
      const matchesStatus = statusFilter === "all" || job.status === statusFilter;
      const haystack = [
        job.metadata?.cotizacion,
        job.metadata?.proyecto,
        job.metadata?.cliente,
        job.metadata?.original_filename,
        job.status
      ].filter(Boolean).join(" ").toLowerCase();
      return matchesStatus && (!cleanQuery || haystack.includes(cleanQuery));
    });
  }, [jobs, query, statusFilter]);

  return (
    <section className="quotes-board">
      <div className="board-hero">
        <div>
          <span>Operacion</span>
          <h2>Cotizaciones</h2>
          <p>Revisa cola, errores y descargas sin entrar al formulario de creacion.</p>
        </div>
        <button className="primary-action" type="button" onClick={onCreateNew}>
          <UploadCloud size={18} />
          Nueva cotizacion
        </button>
      </div>

      <div className="stats-grid">
        <StatCard label="Total" value={stats.total} />
        <StatCard label="Listas" value={stats.completed} />
        <StatCard label="En proceso" value={stats.active} />
        <StatCard label="Con error" value={stats.failed} tone={stats.failed ? "danger" : ""} />
      </div>

      <div className="quote-toolbar">
        <label className="search-box">
          <Search size={18} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar por cliente, proyecto o numero" />
        </label>
        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
          <option value="all">Todos los estados</option>
          <option value="completed">Listas</option>
          <option value="processing">Procesando</option>
          <option value="queued">En cola</option>
          <option value="failed">Con error</option>
        </select>
        <button className="ghost-action" type="button" onClick={refreshJobs}>
          <RefreshCw size={16} />
          Actualizar
        </button>
      </div>

      <div className="quote-list">
        {filteredJobs.map((job) => (
          <article className="quote-card" key={job.id}>
            <div className="quote-icon">
              <FileSpreadsheet size={24} />
            </div>
            <div className="quote-main">
              <span>{job.metadata?.cotizacion || job.metadata?.original_filename || "Sin numero"}</span>
              <strong>{job.metadata?.proyecto || job.metadata?.cliente || "Cotizacion Mobiliti"}</strong>
              <small>{formatDate(job.updated_at || job.created_at)}</small>
              <JobDuration job={job} />
            </div>
            <div className="quote-status">
              <em className={`status ${job.status}`}>{statusLabels[job.status] || job.status}</em>
              <div className="mini-progress-track">
                <div className={`mini-progress-fill ${job.status}`} style={{ width: `${jobProgress(job)}%` }} />
              </div>
            </div>
            <div className="quote-actions">
              {job.status === "failed" ? (
                <button className="ghost-action" type="button" onClick={() => onRetry(job)}>
                  <Clock3 size={16} />
                  Reintentar
                </button>
              ) : (
                <DownloadButton job={job} onDownload={onDownload} downloadState={downloadState} />
              )}
            </div>
          </article>
        ))}
        {!filteredJobs.length ? (
          <div className="empty-board">
            <FileSpreadsheet size={34} />
            <strong>No hay cotizaciones con esos filtros.</strong>
            <button className="ghost-action" type="button" onClick={onCreateNew}>Crear una cotizacion</button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function StatCard({ label, value, tone = "" }) {
  return (
    <div className={`stat-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function HistoryView({ jobs, onDownload, onRetry, onDelete, downloadState, deleteState }) {
  return (
    <section className="main-card full">
      <div className="card-head">
        <h2>Historial</h2>
        <p>Cotizaciones recientes asociadas a tu cuenta.</p>
      </div>
      <div className="history-table">
        {jobs.map((job) => (
          <div className="history-row" key={job.id}>
            <span>{job.metadata?.cotizacion || "Sin numero"}</span>
            <strong>{job.metadata?.proyecto || job.metadata?.original_filename || "Proyecto"}</strong>
            <em className={`status ${job.status}`}>{statusLabels[job.status] || job.status}</em>
            <div className="history-progress">
              <span>{jobProgress(job)}%</span>
              <div className="mini-progress-track">
                <div className={`mini-progress-fill ${job.status}`} style={{ width: `${jobProgress(job)}%` }} />
              </div>
              <JobDuration job={job} />
            </div>
            <small>{formatDate(job.created_at)}</small>
            {job.status === "failed" ? (
              <button onClick={() => onRetry(job)}>Reintentar</button>
            ) : (
              <DownloadButton job={job} onDownload={onDownload} downloadState={downloadState} className="" />
            )}
            <button
              className="danger-action"
              type="button"
              onClick={() => onDelete(job)}
              disabled={deleteState?.jobId === job.id}
            >
              {deleteState?.jobId === job.id ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}
              {deleteState?.jobId === job.id ? "Eliminando" : "Eliminar"}
            </button>
          </div>
        ))}
        {!jobs.length ? <p className="empty">No hay historial todavia.</p> : null}
      </div>
    </section>
  );
}

function AdminView({ token }) {
  const { request } = useApi(token);
  const [users, setUsers] = useState([]);
  const [subs, setSubs] = useState([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [creatingUser, setCreatingUser] = useState(false);
  const [creatingSub, setCreatingSub] = useState(false);
  const [userForm, setUserForm] = useState({
    email: "",
    password: "",
    nombre: "",
    empresa: "",
    es_admin: false
  });
  const [subForm, setSubForm] = useState({
    usuario_id: "",
    plan: "mensual",
    dias: 30,
    estado: "activa"
  });

  const loadAdmin = React.useCallback(async () => {
    try {
      const [userData, subData] = await Promise.all([
        request("/admin/usuarios"),
        request("/admin/suscripciones")
      ]);
      setUsers(Array.isArray(userData) ? userData : []);
      setSubs(Array.isArray(subData) ? subData : []);
    } catch (err) {
      setError(err.message);
    }
  }, [request]);

  useEffect(() => {
    loadAdmin();
  }, [loadAdmin]);

  function updateUserForm(key, value) {
    setUserForm((current) => ({ ...current, [key]: value }));
  }

  function updateSubForm(key, value) {
    setSubForm((current) => ({ ...current, [key]: value }));
  }

  async function createUser(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    setCreatingUser(true);
    try {
      await request("/admin/usuarios", {
        method: "POST",
        body: JSON.stringify(userForm)
      });
      setUserForm({ email: "", password: "", nombre: "", empresa: "", es_admin: false });
      setMessage("Usuario creado.");
      await loadAdmin();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreatingUser(false);
    }
  }

  async function createSubscription(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    setCreatingSub(true);
    try {
      await request("/admin/suscripciones", {
        method: "POST",
        body: JSON.stringify({
          usuario_id: Number(subForm.usuario_id),
          plan: subForm.plan,
          dias: Number(subForm.dias),
          estado: subForm.estado
        })
      });
      setSubForm({ usuario_id: "", plan: "mensual", dias: 30, estado: "activa" });
      setMessage("Suscripcion creada.");
      await loadAdmin();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreatingSub(false);
    }
  }

  return (
    <section className="main-card full">
      <div className="card-head">
        <h2>Admin</h2>
        <p>Gestion basica de usuarios y suscripciones.</p>
      </div>
      {error ? <div className="error-line">{error}</div> : null}
      {message ? <div className="success-line">{message}</div> : null}
      <div className="admin-grid">
        <div>
          <h3>Crear usuario</h3>
          <form className="admin-form" onSubmit={createUser}>
            <label>
              Correo
              <input type="email" value={userForm.email} onChange={(event) => updateUserForm("email", event.target.value)} required />
            </label>
            <label>
              Password temporal
              <input type="password" value={userForm.password} onChange={(event) => updateUserForm("password", event.target.value)} required minLength={8} />
            </label>
            <label>
              Nombre
              <input value={userForm.nombre} onChange={(event) => updateUserForm("nombre", event.target.value)} />
            </label>
            <label>
              Empresa
              <input value={userForm.empresa} onChange={(event) => updateUserForm("empresa", event.target.value)} />
            </label>
            <label className="check-row">
              <input type="checkbox" checked={userForm.es_admin} onChange={(event) => updateUserForm("es_admin", event.target.checked)} />
              Admin
            </label>
            <button className="primary-action" disabled={creatingUser}>{creatingUser ? "Creando..." : "Crear usuario"}</button>
          </form>
          <h3>Usuarios</h3>
          {users.map((user) => (
            <div className="admin-row" key={user.id}>
              <UserRound size={18} />
              <span>{user.nombre || user.email}</span>
              <em>{user.es_admin ? "Admin" : "Usuario"}</em>
            </div>
          ))}
        </div>
        <div>
          <h3>Crear suscripcion</h3>
          <form className="admin-form" onSubmit={createSubscription}>
            <label>
              Usuario
              <select value={subForm.usuario_id} onChange={(event) => updateSubForm("usuario_id", event.target.value)} required>
                <option value="">Selecciona usuario</option>
                {users.map((user) => (
                  <option value={user.id} key={user.id}>{user.email}</option>
                ))}
              </select>
            </label>
            <label>
              Plan
              <input value={subForm.plan} onChange={(event) => updateSubForm("plan", event.target.value)} required />
            </label>
            <label>
              Dias
              <input type="number" min="1" max="3650" value={subForm.dias} onChange={(event) => updateSubForm("dias", event.target.value)} required />
            </label>
            <label>
              Estado
              <select value={subForm.estado} onChange={(event) => updateSubForm("estado", event.target.value)}>
                <option value="activa">activa</option>
                <option value="suspendida">suspendida</option>
                <option value="vencida">vencida</option>
                <option value="cancelada">cancelada</option>
              </select>
            </label>
            <button className="primary-action" disabled={creatingSub}>{creatingSub ? "Creando..." : "Crear suscripcion"}</button>
          </form>
          <h3>Suscripciones</h3>
          {subs.map((sub) => (
            <div className="admin-row" key={sub.id}>
              <CheckCircle2 size={18} />
              <span>{sub.saas_usuarios?.email || `Usuario ${sub.usuario_id}`}</span>
              <em>{sub.estado}</em>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function App() {
  const [session, setSession] = useState(() => {
    try {
      const raw = localStorage.getItem("mobiliti_session");
      return raw ? JSON.parse(raw) : null;
    } catch {
      localStorage.removeItem("mobiliti_session");
      return null;
    }
  });
  const [sessionNotice, setSessionNotice] = useState("");
  const [view, setView] = useState("cotizaciones");
  const [jobs, setJobs] = useState([]);
  const [downloadState, setDownloadState] = useState(null);
  const [deleteState, setDeleteState] = useState(null);
  const { request } = useApi(session?.access_token);

  useEffect(() => {
    function handleAuthExpired() {
      localStorage.removeItem("mobiliti_session");
      setSession(null);
      setJobs([]);
      setDownloadState(null);
      setDeleteState(null);
      setView("cotizaciones");
      setSessionNotice(AUTH_EXPIRED_MESSAGE);
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, []);

  const refreshJobs = React.useCallback(async () => {
    if (!session?.access_token) return;
    try {
      const data = await request("/cotizaciones");
      setJobs(data.cotizaciones || []);
    } catch {
      setJobs([]);
    }
  }, [request, session?.access_token]);

  useEffect(() => {
    refreshJobs();
  }, [refreshJobs]);

  function login(nextSession) {
    localStorage.setItem("mobiliti_session", JSON.stringify(nextSession));
    setSession(nextSession);
    setSessionNotice("");
  }

  function logout() {
    localStorage.removeItem("mobiliti_session");
    setSession(null);
    setSessionNotice("");
  }

  if (!session) return <Login onLogin={login} notice={sessionNotice} />;

  const isAdmin = Boolean(session.usuario?.es_admin);
  async function downloadJob(job) {
    try {
      await runDownload(job, session.access_token, setDownloadState);
    } catch {
      // The download state already exposes the error beside the clicked action.
    }
  }

  async function retryJob(job) {
    const data = await request(`/cotizaciones/${job.id}/retry`, { method: "POST" });
    setJobs((current) => [data.job, ...current.filter((item) => item.id !== data.job.id)]);
  }

  async function deleteJob(job) {
    if (!job?.id) return;
    const label = job.metadata?.cotizacion || job.metadata?.proyecto || "esta cotizacion";
    if (!window.confirm(`Eliminar ${label}?`)) return;
    setDeleteState({ jobId: job.id });
    try {
      await request(`/cotizaciones/${job.id}`, { method: "DELETE" });
      setJobs((current) => current.filter((item) => item.id !== job.id));
    } catch (err) {
      window.alert(err.message || "No se pudo eliminar la cotizacion");
    } finally {
      setDeleteState(null);
    }
  }

  const quoteForm = (
    <QuoteForm
      token={session.access_token}
      recentJobs={jobs}
      refreshJobs={refreshJobs}
      onOpenHistory={() => setView("historial")}
      onJobChange={(job) => {
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      }}
    />
  );
  const mainView = view === "cotizaciones"
    ? <QuotesView jobs={jobs} onDownload={downloadJob} onRetry={retryJob} onCreateNew={() => setView("nueva")} refreshJobs={refreshJobs} downloadState={downloadState} />
    : view === "nueva"
      ? quoteForm
      : view === "historial"
        ? <HistoryView jobs={jobs} onDownload={downloadJob} onRetry={retryJob} onDelete={deleteJob} downloadState={downloadState} deleteState={deleteState} />
        : view === "admin" || view === "clientes"
          ? <AdminView token={session.access_token} />
          : quoteForm;

  return (
    <div className="app-shell">
      <Sidebar view={view} setView={setView} isAdmin={isAdmin} onLogout={logout} />
      <main className="content-shell">
        <Header user={session.usuario} subscription={session.suscripcion} />
        {mainView}
      </main>
    </div>
  );
}

function formatDate(value) {
  if (!value) return "Sin fecha";
  return new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

createRoot(document.getElementById("root")).render(<App />);
