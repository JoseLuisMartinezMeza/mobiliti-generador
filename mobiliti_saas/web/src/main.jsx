import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Armchair,
  ArrowDownToLine,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock3,
  ExternalLink,
  FileSpreadsheet,
  History,
  ImageOff,
  LayoutDashboard,
  Loader2,
  LogOut,
  PackageSearch,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  ShoppingCart,
  Trash2,
  UploadCloud,
  UserRound,
  UsersRound,
  XCircle
} from "lucide-react";
import SupplierCatalogView from "./SupplierCatalogView";
import CatalogAdminPanel from "./CatalogAdminPanel";
import MixedCartDrawer from "./MixedCartDrawer";
import {
  closeMixedCartSection,
  compactMixedCartSections,
  createImportedCartBundle,
  createInitialMixedCartSections,
  createMixedCartLine,
  createMixedQuoteRequestSnapshot,
  lineNeedsAvailabilityConfirmation,
  lineNeedsPriceConfirmation,
  mergeMixedCartSection,
  moveMixedCartLine,
  moveMixedCartLineToSection,
  removeMixedCartLine,
  replaceImportedCartBundle,
  renameMixedCartSection,
  updateMixedCartQuantity,
  updateImportedCartLine,
  upsertMixedCartLine,
  validateLineQuantity,
} from "./mixedCart.js";
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
  const filename = data.filename || quoteDownloadFallbackName(job);
  const link = document.createElement("a");
  link.href = signedUrl;
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

const EMPTY_MIXED_QUOTE = Object.freeze({
  proyecto: "",
  cliente: "",
  correo: "",
  telefono: "",
  direccion: "",
  razon_social: "",
  quote_currency: "MXN",
  descuento: "40",
  template: "Formato Cotizacion 2026 GDL (1).xlsx"
});

const TARKETT_CATALOG_CACHE_KEY = "mobiliti_tarkett_catalog";
const OFFIHO_CATALOG_CACHE_KEY = "mobiliti_offiho_catalog";
const OFFIHO_PAGE_SIZE = 24;
const quantityFormatter = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 3 });
const catalogCurrencyFormatter = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" });

function clearCatalogCaches() {
  try {
    sessionStorage.removeItem(TARKETT_CATALOG_CACHE_KEY);
    sessionStorage.removeItem(OFFIHO_CATALOG_CACHE_KEY);
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith("supplier-catalog:")) sessionStorage.removeItem(key);
    }
  } catch {
    // Storage can be unavailable in privacy-restricted browser sessions.
  }
}

function normalizeCatalogText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function formatQuantity(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0";
  return quantityFormatter.format(numeric);
}

function formatCatalogCurrency(value) {
  const numeric = Number(value);
  return catalogCurrencyFormatter.format(Number.isFinite(numeric) ? numeric : 0);
}

function hasMissingCatalogPrice(item) {
  return item?.price_source === "missing" || !(Number(item?.unit_price) > 0);
}

function stockLimit(item) {
  const numeric = Number(item?.available_quantity);
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
}

const statusLabels = {
  draft: "Borrador sin enviar",
  queued: "En cola",
  processing: "Procesando datos",
  completed: "Cotizacion lista",
  failed: "Error al generar"
};

const fallbackProgress = {
  draft: 0,
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
  return ["queued", "processing"].includes(job?.status);
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
  const baseline = provider === "dezgo" ? 360000 : ["sunon_web", "sunon_catalog"].includes(provider) ? 180000 : 90000;
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
  if (job?.status === "draft") return "Pendiente de enviar";
  if (job?.status === "queued") {
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
      const headers = { ...(options.headers || {}) };
      if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
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
    ["admin", "Admin", Settings],
    ["tarkett", "Tarkett", PackageSearch],
    ["offiho", "Offiho", Armchair],
    ["cr-global", "CR Global", PackageSearch],
    ["sonara", "Sonara", PackageSearch],
    ["sunon", "Sunon", PackageSearch],
    ["alma", "ALMA", PackageSearch],
    ["lumbro", "Lumbro", PackageSearch]
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

function Header({ user, subscription, cartCount, onOpenCart }) {
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
      <button className="global-cart-toggle" type="button" onClick={onOpenCart}>
        <ShoppingCart size={19} />
        Carrito ({cartCount})
      </button>
      <div className="user-chip">
        <div>{initials}</div>
        <span>{user?.nombre || user?.email}<small>{user?.es_admin ? "Administrador" : "Usuario"}</small></span>
      </div>
    </header>
  );
}

function previewNeedsSourceCurrency(preview) {
  return preview?.currency_status === "required";
}

function QuoteForm({ token, onJobChange, recentJobs, refreshJobs, onOpenHistory, onImportPreview }) {
  const { request } = useApi(token);
  const [form, setForm] = useState(emptyQuote);
  const [file, setFile] = useState(null);
  const [job, setJob] = useState(null);
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadState, setDownloadState] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [importPreview, setImportPreview] = useState(null);
  const [importCurrency, setImportCurrency] = useState("");
  const [importProvider, setImportProvider] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef(null);
  const uploadDraftRef = useRef(null);
  const requestInFlightRef = useRef(false);
  const requestEpochRef = useRef(0);

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
    if (busy) return;
    requestEpochRef.current += 1;
    setError("");
    setImportPreview(null);
    setImportCurrency("");
    setImportProvider("");
    uploadDraftRef.current = null;
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

  async function uploadQuoteDraft(selectedFile, template) {
    const existing = uploadDraftRef.current;
    if (existing?.file === selectedFile && existing.template === template) return existing;
    const init = await request("/cotizaciones/init-upload", {
        method: "POST",
        body: JSON.stringify({ filename: selectedFile.name, size: selectedFile.size, template })
      });
      if (init.signed_upload_url) {
        const uploadRes = await fetch(init.signed_upload_url, {
          method: "PUT",
          headers: {
            "Content-Type": quoteInputContentType(selectedFile.name)
          },
          body: selectedFile
        });
        const uploadData = await uploadRes.json().catch(() => ({}));
        if (!uploadRes.ok) throw new Error(uploadData.message || uploadData.error || "Error subiendo archivo");
      } else if (init.upload_url) {
        const body = new FormData();
        body.append("file", selectedFile);
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
      const draft = { job_id: init.job_id, file: selectedFile, template };
      uploadDraftRef.current = draft;
      return draft;
  }

  async function createQuote(event) {
    event.preventDefault();
    if (requestInFlightRef.current) return;
    setError("");
    setDownloadUrl("");
    setDownloadState(null);

    if (!file) {
      setError("Selecciona un archivo .xlsx o .pdf primero.");
      return;
    }
    const requestEpoch = ++requestEpochRef.current;
    requestInFlightRef.current = true;
    setBusy(true);
    try {
      const draft = await uploadQuoteDraft(file, form.template);

      const submitted = await request(`/cotizaciones/${draft.job_id}/submit`, {
        method: "POST",
        body: JSON.stringify(form)
      });
      if (requestEpoch !== requestEpochRef.current) return;
      setJob(submitted.job);
      onJobChange(submitted.job);
      refreshJobs();
      setImportPreview(null);
      uploadDraftRef.current = null;
    } catch (err) {
      if (requestEpoch === requestEpochRef.current) setError(err.message);
    } finally {
      if (requestEpoch === requestEpochRef.current) {
        requestInFlightRef.current = false;
        setBusy(false);
      }
    }
  }

  async function previewImport() {
    if (requestInFlightRef.current) return;
    if (!file) {
      setError("Selecciona un archivo .xlsx para previsualizar.");
      return;
    }
    if (!/\.xlsx$/i.test(file.name)) {
      setError("La importacion editable solo admite archivos .xlsx con hoja Quotation.");
      return;
    }
    const requestEpoch = ++requestEpochRef.current;
    requestInFlightRef.current = true;
    setBusy(true);
    setError("");
    try {
      const draft = await uploadQuoteDraft(file, form.template);
      const preview = await request(`/cotizaciones/${draft.job_id}/import-preview`, { method: "POST" });
      if (requestEpoch !== requestEpochRef.current) return;
      setImportPreview(preview);
      setImportCurrency(preview.source_currency || "");
      setImportProvider(preview.provider || "");
    } catch (err) {
      if (requestEpoch === requestEpochRef.current) setError(err.message);
    } finally {
      if (requestEpoch === requestEpochRef.current) {
        requestInFlightRef.current = false;
        setBusy(false);
      }
    }
  }

  function confirmImport() {
    const detectedCurrency = importPreview?.source_currency || "";
    const sourceCurrency = detectedCurrency || importCurrency;
    const provider = importProvider.trim();
    if (!importPreview || (previewNeedsSourceCurrency(importPreview) && !sourceCurrency) || !provider) return;
    const imported = onImportPreview(importPreview, { sourceCurrency, provider, quoteForm: form });
    if (imported) setImportPreview(null);
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
            disabled={busy}
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
              disabled={busy}
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
              disabled={busy}
            />
            <Field label="Proyecto" value={form.proyecto} onChange={(value) => updateField("proyecto", value)} disabled={busy} />
            <Field label="Cliente" value={form.cliente} onChange={(value) => updateField("cliente", value)} disabled={busy} />
            <Field label="Correo" type="email" value={form.correo} onChange={(value) => updateField("correo", value)} disabled={busy} />
            <Field label="Telefono" value={form.telefono} onChange={(value) => updateField("telefono", value)} disabled={busy} />
            <Field label="Direccion" value={form.direccion} onChange={(value) => updateField("direccion", value)} disabled={busy} />
            <Field label="Descuento (%)" type="number" min="0" max="100" value={form.descuento} onChange={updateDiscount} disabled={busy} />
            <Field label="Razon social" value={form.razon_social} onChange={(value) => updateField("razon_social", value)} wide disabled={busy} />
          </div>

          <h3>3. Plantilla y render</h3>
          <div className="template-grid">
            <select value={form.template} disabled={busy} onChange={(event) => updateField("template", event.target.value)}>
              <option>Formato Cotizacion 2026 GDL (1).xlsx</option>
              <option>Plantilla Corporativa Mobiliti 2025</option>
            </select>
            <select value={form.description_language} disabled={busy} onChange={(event) => updateField("description_language", event.target.value)}>
              <option value="es">Descripciones en espanol</option>
              <option value="en">Descripciones en ingles</option>
            </select>
            <select value={form.image_provider} disabled={busy} onChange={(event) => updateField("image_provider", event.target.value)}>
              <option value="dezgo">IA Dezgo recomendado - genera faltantes realistas</option>
              <option value="sunon_catalog">Catalogo Sunon preciso - solo codigo exacto</option>
              <option value="sunon_web">Sunon web experimental - buscar por codigo</option>
              <option value="pillow">Local sin IA - no inventa imagenes faltantes</option>
            </select>
            <select value={form.image_cleanup_strength} disabled={busy} onChange={(event) => updateField("image_cleanup_strength", event.target.value)}>
              <option value="balanced">Limpieza balanceada</option>
              <option value="normal">Limpieza conservadora</option>
              <option value="aggressive">Limpieza fuerte</option>
            </select>
            <div className="render-summary">
              <Sparkles size={18} />
              <span>
                {form.image_provider === "dezgo"
                  ? "IA Dezgo mejora y genera faltantes"
                  : form.image_provider === "sunon_catalog"
                    ? "Catalogo Sunon usa solo matches exactos"
                  : form.image_provider === "sunon_web"
                    ? "Sunon web busca imagen oficial y cae a local"
                    : "Render local solo mejora imagenes existentes"} - Fondo {form.image_background === "white" ? "blanco" : "transparente"}
              </span>
            </div>
            <label className="prompt-field">
              Prompt para imagenes
              <textarea
                value={form.image_prompt}
                disabled={busy}
                onChange={(event) => updateField("image_prompt", event.target.value)}
                placeholder={DEFAULT_IMAGE_PROMPT}
                rows={3}
              />
            </label>
          </div>

          {error ? <div className="error-line" role="alert">{error}</div> : null}
          {busy ? <div className="sr-only" role="status" aria-live="polite">Procesando archivo, espera antes de continuar.</div> : null}
          <DownloadStatusLine state={downloadState} />
          {downloadUrl && !downloadState ? <div className="download-line">Ultima descarga: {downloadUrl}</div> : null}

          {importPreview ? (
            <section className="quotation-import-preview" aria-label="Previsualizacion de importacion">
              <h3>Previsualizacion: {importPreview.original_filename || file?.name}</h3>
              <p>{importPreview.items?.length || 0} producto(s) en {importPreview.sections?.length || 0} seccion(es).</p>
              {previewNeedsSourceCurrency(importPreview) || importPreview.source_currency ? <label>
                Moneda de origen {importPreview.source_currency ? "detectada" : "*"}
                <select
                  value={importPreview.source_currency || importCurrency}
                  disabled={busy || Boolean(importPreview.source_currency)}
                  required={!importPreview.source_currency}
                  aria-describedby="import-currency-help"
                  onChange={(event) => setImportCurrency(event.target.value)}
                >
                  {!importPreview.source_currency ? <option value="">Selecciona una moneda</option> : null}
                  {['MXN', 'USD', 'EUR'].map((currency) => <option key={currency} value={currency}>{currency}</option>)}
                </select>
              </label> : null}
              <small id="import-currency-help">{
                importPreview.source_currency
                  ? "La moneda detectada no se puede reemplazar."
                  : previewNeedsSourceCurrency(importPreview)
                    ? "Selecciona la moneda antes de confirmar la importacion."
                    : "Las monedas explicitas de cada producto se conservaran sin una seleccion global."
              }</small>
              <label>
                Proveedor *
                <input name="import-provider" value={importProvider} disabled={busy} required onChange={(event) => setImportProvider(event.target.value)} />
              </label>
              <button
                type="button"
                className="primary-action"
                disabled={busy || !importProvider.trim() || (previewNeedsSourceCurrency(importPreview) && !(importPreview.source_currency || importCurrency))}
                onClick={confirmImport}
              >
                Confirmar importacion al carrito
              </button>
            </section>
          ) : null}

          <div className="actions-row">
            <button className="secondary-action" type="button" disabled={busy} onClick={previewImport}>
              {busy ? <Loader2 className="spin" size={18} /> : <UploadCloud size={18} />}
              Previsualizar e importar al carrito
            </button>
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

function Field({ label, value, onChange, type = "text", wide = false, min, max, required = true, readOnly = false, placeholder = "", disabled = false }) {
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
        disabled={disabled}
        placeholder={placeholder}
      />
    </label>
  );
}

function TarkettView({ token, userId, cartLines, onAddCartLine, onOpenCart, cartBusy }) {
  const { request } = useApi(token);
  const [catalog, setCatalog] = useState({ source_hash: "", generated_at: "", total: 0, items: [] });
  const [query, setQuery] = useState("");
  const [unitFilter, setUnitFilter] = useState("all");
  const [quantityDraftsByCode, setQuantityDraftsByCode] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [quantityError, setQuantityError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    try {
      const cached = JSON.parse(sessionStorage.getItem(TARKETT_CATALOG_CACHE_KEY) || "null");
      if (cached?.source_hash && cached?.user_id === userId && Array.isArray(cached.items)) {
        setCatalog(cached);
        setLoading(false);
      }
    } catch {
      sessionStorage.removeItem(TARKETT_CATALOG_CACHE_KEY);
    }

    async function loadCatalog() {
      setLoading(true);
      setError("");
      try {
        const data = await request("/tarkett/catalog");
        if (cancelled) return;
        setCatalog(data);
        sessionStorage.setItem(TARKETT_CATALOG_CACHE_KEY, JSON.stringify({ ...data, user_id: userId }));
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadCatalog();
    return () => {
      cancelled = true;
    };
  }, [request, reloadKey, userId]);

  const unitOptions = useMemo(() => {
    const units = new Set((catalog.items || []).map((item) => item.unit).filter(Boolean));
    return Array.from(units).sort((a, b) => a.localeCompare(b, "es"));
  }, [catalog.items]);

  const filteredItems = useMemo(() => {
    const cleanQuery = normalizeCatalogText(query);
    return (catalog.items || []).filter((item) => {
      const matchesUnit = unitFilter === "all" || item.unit === unitFilter;
      const haystack = normalizeCatalogText(`${item.code} ${item.name} ${item.unit}`);
      return matchesUnit && (!cleanQuery || haystack.includes(cleanQuery));
    });
  }, [catalog.items, query, unitFilter]);

  function addTarkettItem(item) {
    const existing = cartLines.find((line) => line.catalog === "tarkett" && line.identity.code === item.code);
    const available = Math.min(stockLimit(item), 1000000);
    const draft = quantityDraftsByCode[item.code]
      ?? existing?.quantity
      ?? String(Math.min(1, available));
    try {
      const added = onAddCartLine(createMixedCartLine({
        catalog: "tarkett",
        identity: { code: item.code },
        quantity: String(draft),
        quantityRules: {
          min: "0.000001",
          step: "0.000001",
          maxDecimals: 6,
          max: String(available),
        },
        snapshot: {
          name: item.name,
          code: item.code,
          image_url: item.image_url || "",
          unit: item.unit,
          availability: String(item.available_quantity),
          configuration: "",
          warnings: [],
        },
      }));
      if (added) setQuantityError("");
    } catch (quantityFailure) {
      setQuantityError(quantityFailure.message || "Cantidad invalida");
    }
  }

  return (
    <section className="tarkett-shell">
      <div className="card-head tarkett-head">
        <div>
          <h2>Tarkett</h2>
          <p>{catalog.total || catalog.items.length} productos indexados{catalog.generated_at ? ` - ${formatDate(catalog.generated_at)}` : ""}</p>
        </div>
        <div className="catalog-head-actions">
          <button className="ghost-action" type="button" onClick={() => {
            sessionStorage.removeItem(TARKETT_CATALOG_CACHE_KEY);
            setReloadKey((value) => value + 1);
          }}>
            <RefreshCw size={16} />
            Refrescar
          </button>
          <button className="ghost-action" type="button" onClick={onOpenCart}>
            <ShoppingCart size={17} /> Carrito ({cartLines.length})
          </button>
        </div>
      </div>

      <div className="tarkett-layout">
        <div className="tarkett-catalog">
          <div className="tarkett-toolbar">
            <label className="search-box">
              <Search size={18} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar clave, producto o unidad" />
            </label>
            <select value={unitFilter} onChange={(event) => setUnitFilter(event.target.value)}>
              <option value="all">Todas las unidades</option>
              {unitOptions.map((unit) => <option key={unit} value={unit}>{unit}</option>)}
            </select>
            <span>{loading ? "Cargando..." : `${filteredItems.length} visibles`}</span>
          </div>

          {error ? <div className="error-line">{error}</div> : null}
          {quantityError ? <div className="error-line" role="alert">{quantityError}</div> : null}

          <div className="tarkett-grid">
            {filteredItems.map((item) => {
              const existing = cartLines.find((line) => line.catalog === "tarkett" && line.identity.code === item.code);
              const reserved = Number(item.reserved_quantity || 0);
              const available = Math.min(stockLimit(item), 1000000);
              const draft = quantityDraftsByCode[item.code]
                ?? existing?.quantity
                ?? String(Math.min(1, available));
              return (
                <article className="tarkett-product" key={item.code}>
                  <div className="product-media">
                    {item.image_url ? (
                      <img src={item.image_url} alt={item.name} loading="lazy" />
                    ) : (
                      <div className="product-placeholder"><ImageOff size={24} /></div>
                    )}
                  </div>
                  <div className="product-info">
                    <div className="product-title-row">
                      <span>{item.code}</span>
                      {item.product_url ? (
                        <a href={item.product_url} target="_blank" rel="noreferrer" aria-label={`Abrir ${item.name}`}>
                          <ExternalLink size={15} />
                        </a>
                      ) : null}
                    </div>
                    <strong>{item.name}</strong>
                    <small>{item.unit}</small>
                    <div className="tarkett-price-row">
                      <span>Precio unitario</span>
                      <strong>{hasMissingCatalogPrice(item) ? "Por confirmar" : formatCatalogCurrency(item.unit_price)}</strong>
                    </div>
                    <div className="stock-row">
                      <span>Existencia {formatQuantity(item.available_quantity)}</span>
                      {item.reserved_by_others && reserved > 0 ? <em>Apartado {formatQuantity(reserved)}</em> : null}
                    </div>
                  </div>
                  <div className="product-actions">
                    <input
                      type="number"
                      min="0.000001"
                      step="0.000001"
                      max={available || undefined}
                      value={draft}
                      disabled={cartBusy}
                      onChange={(event) => setQuantityDraftsByCode((current) => ({
                        ...current,
                        [item.code]: event.target.value,
                      }))}
                      placeholder="Cant."
                    />
                    <button
                      className="primary-action"
                      type="button"
                      onClick={() => addTarkettItem(item)}
                      disabled={cartBusy || available <= 0}
                    >
                      <Plus size={16} />
                      Agregar
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </div>

      </div>
    </section>
  );
}

function OffihoView({ token, userId, cartLines, onAddCartLine, onOpenCart, cartBusy }) {
  const { request } = useApi(token);
  const [catalog, setCatalog] = useState({ source_hash: "", generated_at: "", total: 0, items: [] });
  const [query, setQuery] = useState("");
  const [unitFilter, setUnitFilter] = useState("all");
  const [availabilityFilter, setAvailabilityFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [quantityDraftsByInventoryKey, setQuantityDraftsByInventoryKey] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [quantityError, setQuantityError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const offihoQuantityFormatter = useMemo(() => new Intl.NumberFormat("es-MX", { maximumFractionDigits: 3 }), []);
  const offihoCurrencyFormatter = useMemo(() => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 2 }), []);

  function normalizeOffihoText(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  function offihoStockLimit(item) {
    const numeric = Number(item?.available_quantity);
    return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
  }

  function formatOffihoQuantity(value) {
    const numeric = Number(value);
    return offihoQuantityFormatter.format(Number.isFinite(numeric) ? numeric : 0);
  }

  function formatOffihoCurrency(value) {
    const numeric = Number(value);
    return offihoCurrencyFormatter.format(Number.isFinite(numeric) ? numeric : 0);
  }

  function hasMissingPrice(item) {
    return item?.price_source === "missing";
  }

  function normalizeOffihoQuantity(rawQuantity) {
    const raw = String(rawQuantity ?? "").trim();
    if (!/^\d+(?:\.\d{1,3})?$/.test(raw)) return { error: "Ingresa una cantidad mayor a 0 con hasta 3 decimales." };
    const numeric = Number(raw);
    if (!Number.isFinite(numeric) || numeric <= 0 || numeric > 1000000) return { error: "La cantidad debe estar entre 0.001 y 1000000." };
    return { quantity: numeric, rawQuantity: String(numeric) };
  }

  function isOffihoQuantityDraft(rawQuantity) {
    // Preserve drafts such as "" and "1." until blur validates them.
    return /^\d*(?:\.\d{0,3})?$/.test(rawQuantity);
  }

  function offihoStockWarning(item, quantity) {
    const available = offihoStockLimit(item);
    if (available <= 0 || item?.is_out_of_stock) return "Agotado";
    return Number(quantity) > available ? "Stock insuficiente" : "";
  }

  useEffect(() => {
    let cancelled = false;
    try {
      const cached = JSON.parse(sessionStorage.getItem(OFFIHO_CATALOG_CACHE_KEY) || "null");
      if (cached?.source_hash && cached?.user_id === userId && Array.isArray(cached.items)) {
        setCatalog(cached);
        setLoading(false);
      }
    } catch {
      sessionStorage.removeItem(OFFIHO_CATALOG_CACHE_KEY);
    }

    async function loadCatalog() {
      setLoading(true);
      setError("");
      try {
        const data = await request("/offiho/catalog");
        if (cancelled) return;
        setCatalog(data);
        sessionStorage.setItem(OFFIHO_CATALOG_CACHE_KEY, JSON.stringify({ ...data, user_id: userId }));
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadCatalog();
    return () => { cancelled = true; };
  }, [request, reloadKey, userId]);

  const unitOptions = useMemo(() => Array.from(new Set((catalog.items || []).map((item) => item.unit).filter(Boolean))).sort((a, b) => a.localeCompare(b, "es")), [catalog.items]);
  const filteredItems = useMemo(() => {
    const cleanQuery = normalizeOffihoText(query);
    return (catalog.items || []).filter((item) => {
      const available = offihoStockLimit(item);
      const matchesUnit = unitFilter === "all" || item.unit === unitFilter;
      const matchesAvailability = availabilityFilter === "all" || (availabilityFilter === "available" && available > 0) || (availabilityFilter === "out" && available <= 0);
      const haystack = normalizeOffihoText(`${item.inventory_key} ${item.code} ${item.name} ${item.variant} ${item.unit}`);
      return matchesUnit && matchesAvailability && (!cleanQuery || haystack.includes(cleanQuery));
    });
  }, [availabilityFilter, catalog.items, query, unitFilter]);
  const pageCount = Math.max(1, Math.ceil(filteredItems.length / OFFIHO_PAGE_SIZE));
  const pageStart = (page - 1) * OFFIHO_PAGE_SIZE;
  const pagedItems = useMemo(() => filteredItems.slice(pageStart, pageStart + OFFIHO_PAGE_SIZE), [filteredItems, pageStart]);

  useEffect(() => { setPage(1); }, [availabilityFilter, query, unitFilter]);
  useEffect(() => { setPage((current) => Math.min(current, pageCount)); }, [pageCount]);

  function changeOffihoDraft(inventoryKey, rawQuantity) {
    const raw = String(rawQuantity);
    if (!isOffihoQuantityDraft(raw)) setQuantityError("Usa solo numeros y hasta 3 decimales; no se permiten exponentes.");
    else setQuantityError("");
    if (isOffihoQuantityDraft(raw)) {
      setQuantityDraftsByInventoryKey((current) => ({ ...current, [inventoryKey]: raw }));
    }
  }

  function normalizeOffihoDraft(item) {
    const draft = quantityDraftsByInventoryKey[item.inventory_key] ?? "1";
    const normalized = normalizeOffihoQuantity(draft);
    if (normalized.error) {
      setQuantityError(normalized.error);
      return false;
    }
    setQuantityError("");
    setQuantityDraftsByInventoryKey((current) => ({
      ...current,
      [item.inventory_key]: normalized.rawQuantity,
    }));
    return true;
  }

  function addOffihoItem(item) {
    const draft = quantityDraftsByInventoryKey[item.inventory_key] ?? "1";
    const normalized = normalizeOffihoQuantity(draft);
    if (normalized.error) {
      setQuantityError(normalized.error);
      return;
    }
    const available = Math.min(offihoStockLimit(item), 1000000);
    const warning = offihoStockWarning(item, normalized.quantity);
    const warnings = [
      ...(warning ? [warning] : []),
      ...(hasMissingPrice(item) ? ["Precio por confirmar"] : []),
    ];
    try {
      const added = onAddCartLine(createMixedCartLine({
        catalog: "offiho",
        identity: { inventory_key: item.inventory_key },
        quantity: normalized.rawQuantity,
        quantityRules: {
          min: "0.001",
          step: "0.001",
          maxDecimals: 3,
          max: "1000000",
          warningAt: String(available),
          confirmOnInsufficient: true,
          confirmOnMissingPrice: hasMissingPrice(item),
        },
        snapshot: {
          name: item.name,
          code: item.code || item.inventory_key,
          image_url: item.image_url || "",
          unit: item.unit,
          availability: String(item.available_quantity),
          configuration: String(item.variant || ""),
          warnings,
        },
      }));
      if (added) setQuantityError("");
    } catch (quantityFailure) {
      setQuantityError(quantityFailure.message || "No se pudo agregar el producto");
    }
  }

  return (
    <section className="tarkett-shell offiho-shell">
      <div className="card-head tarkett-head">
        <div><h2>Offiho</h2><p>{catalog.total || catalog.items.length} productos indexados{catalog.generated_at ? ` - ${formatDate(catalog.generated_at)}` : ""}</p></div>
        <div className="catalog-head-actions">
          <button className="ghost-action" type="button" onClick={() => { sessionStorage.removeItem(OFFIHO_CATALOG_CACHE_KEY); setReloadKey((value) => value + 1); }}><RefreshCw size={16} />Refrescar</button>
          <button className="ghost-action" type="button" onClick={onOpenCart}><ShoppingCart size={17} /> Carrito ({cartLines.length})</button>
        </div>
      </div>

      <div className="tarkett-layout">
        <div className="tarkett-catalog">
          <div className="tarkett-toolbar offiho-toolbar">
            <label className="search-box"><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar clave, modelo, variante o producto" aria-label="Buscar catalogo Offiho" /></label>
            <select value={unitFilter} onChange={(event) => setUnitFilter(event.target.value)} aria-label="Filtrar por unidad"><option value="all">Todas las unidades</option>{unitOptions.map((unit) => <option key={unit} value={unit}>{unit}</option>)}</select>
            <select value={availabilityFilter} onChange={(event) => setAvailabilityFilter(event.target.value)} aria-label="Filtrar por disponibilidad"><option value="all">Toda disponibilidad</option><option value="available">Con existencia</option><option value="out">Agotados</option></select>
            <span>{loading ? "Cargando..." : `${filteredItems.length} visibles`}</span>
          </div>
          {error ? <div className="error-line">{error}</div> : null}
          {quantityError ? <div className="error-line" role="alert">{quantityError}</div> : null}
          {loading ? <p className="empty">Cargando catalogo Offiho...</p> : null}
          {!loading && !filteredItems.length ? <p className="empty">No hay productos que coincidan con los filtros.</p> : null}

          <div className="tarkett-grid offiho-grid">
            {pagedItems.map((item) => {
              const reserved = Number(item.reserved_quantity || 0);
              const draft = quantityDraftsByInventoryKey[item.inventory_key] ?? "1";
              const normalizedDraft = normalizeOffihoQuantity(draft);
              const stockWarning = normalizedDraft.error
                ? (offihoStockLimit(item) <= 0 ? "Agotado" : "")
                : offihoStockWarning(item, normalizedDraft.quantity);
              return (
                <article className="tarkett-product offiho-product" key={item.inventory_key}>
                  <div className="product-media">{item.image_url ? <img src={item.image_url} alt={`${item.name || item.inventory_key} ${item.variant || ""}`.trim()} loading="lazy" /> : <div className="product-placeholder" aria-label="Imagen no disponible"><ImageOff size={24} /></div>}</div>
                  <div className="product-info">
                    <div className="product-title-row"><span>{item.code}</span>{item.product_url ? <a href={item.product_url} target="_blank" rel="noreferrer noopener" aria-label={`Abrir sitio oficial de ${item.name || item.code}`} title="Abrir sitio oficial"><ExternalLink size={15} /></a> : null}</div>
                    <strong>{item.name || item.inventory_key}</strong>
                    <small>{item.variant || "Sin variante"} - {item.unit}</small>
                    {item.description ? <p className="offiho-description" title={item.description}>{item.description}</p> : null}
                    <div className="offiho-meta"><span>{item.unit} - {formatOffihoQuantity(item.pieces_per_box)} pzas/caja</span><strong>{hasMissingPrice(item) ? "Precio por confirmar" : formatOffihoCurrency(item.unit_price)}</strong></div>
                    <div className="stock-row"><span>Existencia {formatOffihoQuantity(item.available_quantity)}</span>{item.reserved_by_others && reserved > 0 ? <em>Apartado {formatOffihoQuantity(reserved)}</em> : null}{stockWarning ? <b className={`offiho-warning ${stockWarning === "Agotado" ? "out" : "insufficient"}`}>{stockWarning}</b> : null}</div>
                  </div>
                  <div className="product-actions">
                    <input type="text" inputMode="decimal" value={draft} disabled={cartBusy} onChange={(event) => changeOffihoDraft(item.inventory_key, event.target.value)} onBlur={() => normalizeOffihoDraft(item)} placeholder="Cant." aria-label={`Cantidad para ${item.name || item.inventory_key}`} />
                    <button className="primary-action" type="button" disabled={cartBusy} onClick={() => addOffihoItem(item)}><Plus size={16} />Agregar</button>
                  </div>
                </article>
              );
            })}
          </div>
          {!loading && filteredItems.length ? <div className="offiho-pagination"><button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1} aria-label="Pagina anterior" title="Pagina anterior"><ChevronLeft size={18} /></button><span>Pagina {page} de {pageCount}</span><button type="button" onClick={() => setPage((current) => Math.min(pageCount, current + 1))} disabled={page >= pageCount} aria-label="Pagina siguiente" title="Pagina siguiente"><ChevronRight size={18} /></button></div> : null}
        </div>

      </div>
    </section>
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

function ImageProviderWarning({ job }) {
  const metadata = job?.metadata || {};
  const existingFailures = Number(metadata.image_ai_failed_count);
  const missingFailures = Number(metadata.image_ai_missing_failed_count);
  const count = (Number.isFinite(existingFailures) ? existingFailures : 0)
    + (Number.isFinite(missingFailures) ? missingFailures : 0);
  if (job?.status !== "completed" || count <= 0) return null;

  return (
    <small className="image-provider-warning" role="status">
      IA no disponible en {count} {count === 1 ? "producto" : "productos"}; la cotizacion continuo con las imagenes locales disponibles.
    </small>
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
      <ImageProviderWarning job={job} />
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
              <ImageProviderWarning job={job} />
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

function QuotesView({ jobs, onDownload, onRetry, onDelete, onCreateNew, refreshJobs, downloadState, deleteState }) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const stats = useMemo(() => ({
    total: jobs.length,
    completed: jobs.filter((job) => job.status === "completed").length,
    drafts: jobs.filter((job) => job.status === "draft").length,
    active: jobs.filter((job) => ["queued", "processing"].includes(job.status)).length,
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
        <StatCard label="Borradores" value={stats.drafts} />
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
          <option value="draft">Borradores</option>
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
              <ImageProviderWarning job={job} />
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
              ) : job.status === "draft" ? (
                <button
                  className="danger-action"
                  type="button"
                  onClick={() => onDelete(job)}
                  disabled={deleteState?.jobId === job.id}
                >
                  {deleteState?.jobId === job.id ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}
                  {deleteState?.jobId === job.id ? "Descartando" : "Descartar borrador"}
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
    <>
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
      <CatalogAdminPanel request={request} />
    </>
  );
}

function createMixedQuoteController({
  cartRef: mixedCartRef,
  sectionsRef: mixedSectionsRef = null,
  submittingRef: mixedQuoteSubmittingRef,
  sessionEpochRef: mixedQuoteSessionEpochRef,
  importRevisionRef: mixedImportRevisionRef = null,
  emptyForm,
  replaceCart,
  replaceSections = null,
  setOpen,
  setForm,
  getForm,
  setBusy,
  setError,
  setNotice,
  setJobs,
  request,
  confirmQuote,
  confirmImport,
  waitForJobResult,
}) {
  mixedSectionsRef = mixedSectionsRef || { current: createInitialMixedCartSections() };
  replaceSections = replaceSections || (() => {});
  const askImport = confirmImport || (() => true);
  const awaitJobResult = waitForJobResult || (async (job) => ({ ...job, status: "completed" }));
  mixedImportRevisionRef = mixedImportRevisionRef || { current: 0 };

  function add(line) {
    if (mixedQuoteSubmittingRef.current) {
      setError("Espera a que termine la cotizacion en curso");
      setOpen(true);
      return false;
    }
    try {
      const activeSection = mixedSectionsRef.current[mixedSectionsRef.current.length - 1]
        || createInitialMixedCartSections()[0];
      const next = upsertMixedCartLine(
        mixedCartRef.current,
        { ...line, sectionId: activeSection.id },
      );
      replaceCart(next);
      setError("");
      setNotice("");
      setOpen(true);
      return true;
    } catch (cartFailure) {
      setError(cartFailure.message || "No se pudo agregar el producto");
      setOpen(true);
      return false;
    }
  }

  function update(key, quantity) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    replaceCart(updateMixedCartQuantity(mixedCartRef.current, key, quantity));
  }

  function updateImported(key, edits) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    replaceCart(updateImportedCartLine(mixedCartRef.current, key, edits));
  }

  function importPreview(preview, { sourceCurrency, provider }) {
    if (mixedQuoteSubmittingRef.current) {
      setError("Espera a que termine la cotizacion en curso");
      setOpen(true);
      return false;
    }
    const hasImportedLines = mixedCartRef.current.some((line) => line.kind === "imported");
    if (hasImportedLines && !askImport("Se reemplazaran solo los productos importados actuales. Los productos de catalogo se conservaran. ¿Continuar?")) {
      return false;
    }
    try {
      const bundle = createImportedCartBundle(
        preview,
        sourceCurrency,
        provider,
        mixedSectionsRef.current,
      );
      mixedImportRevisionRef.current += 1;
      bundle.lines = bundle.lines.map((line) => ({
        ...line,
        editorRevision: mixedImportRevisionRef.current,
      }));
      const next = replaceImportedCartBundle(
        mixedCartRef.current,
        mixedSectionsRef.current,
        bundle,
      );
      replaceCart(next.lines);
      replaceSections(next.sections);
      setError("");
      setNotice("");
      setOpen(true);
      return true;
    } catch (cartFailure) {
      setError(cartFailure.message || "No se pudo importar la previsualizacion");
      setOpen(true);
      return false;
    }
  }

  function remove(key) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    const nextLines = removeMixedCartLine(mixedCartRef.current, key);
    const compacted = compactMixedCartSections(mixedSectionsRef.current, nextLines);
    replaceCart(compacted.lines);
    replaceSections(compacted.sections);
  }

  function closeSection() {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    replaceSections(closeMixedCartSection(mixedSectionsRef.current, mixedCartRef.current));
  }

  function renameSection(sectionId, concept) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    replaceSections(renameMixedCartSection(mixedSectionsRef.current, sectionId, concept));
  }

  function mergeSection(sectionId) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    const merged = mergeMixedCartSection(
      mixedSectionsRef.current,
      mixedCartRef.current,
      sectionId,
    );
    replaceCart(merged.lines);
    replaceSections(merged.sections);
  }

  function moveLine(key, direction) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    replaceCart(moveMixedCartLine(mixedCartRef.current, key, direction));
  }

  function moveLineToSection(key, sectionId) {
    if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
    const nextLines = moveMixedCartLineToSection(
      mixedCartRef.current,
      mixedSectionsRef.current,
      key,
      sectionId,
    );
    const compacted = compactMixedCartSections(mixedSectionsRef.current, nextLines);
    replaceCart(compacted.lines);
    replaceSections(compacted.sections);
  }

  function updateField(field, value) {
    if (mixedQuoteSubmittingRef.current) return;
    setForm((current) => ({ ...current, [field]: value }));
  }

  function resetSession() {
    mixedQuoteSessionEpochRef.current += 1;
    mixedImportRevisionRef.current = 0;
    mixedQuoteSubmittingRef.current = false;
    setBusy(false);
    replaceCart([]);
    replaceSections(createInitialMixedCartSections());
    setOpen(false);
    setForm({ ...emptyForm });
    setError("");
    setNotice("");
  }

  async function submit(event, submissionLines = mixedCartRef.current, preparedRequest = null) {
    event.preventDefault();
    if (mixedQuoteSubmittingRef.current || !submissionLines.length) return;

    let committedLines;
    try {
      committedLines = submissionLines.map((line) => ({
        ...line,
        quantity: validateLineQuantity(line, line.quantity),
      }));
    } catch (quantityFailure) {
      setError(quantityFailure.message || "Cantidad invalida");
      return;
    }
    replaceCart(committedLines);

    const availabilityWarnings = committedLines.filter(lineNeedsAvailabilityConfirmation);
    const priceWarnings = committedLines.filter(lineNeedsPriceConfirmation);
    if ((availabilityWarnings.length || priceWarnings.length) && !confirmQuote(
      `Hay ${availabilityWarnings.length} producto(s) agotado(s) o con existencia insuficiente `
      + `y ${priceWarnings.length} producto(s) con precio por confirmar. ¿Deseas continuar?`,
    )) return;

    mixedQuoteSubmittingRef.current = true;
    const submissionEpoch = mixedQuoteSessionEpochRef.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const mixedRequest = preparedRequest || createMixedQuoteRequestSnapshot(
        getForm(),
        mixedSectionsRef.current,
        committedLines,
      );
      const data = await request("/catalogs/mixed-quote", {
        method: "POST",
        body: JSON.stringify(mixedRequest),
      });
      if (submissionEpoch !== mixedQuoteSessionEpochRef.current) return;
      if (!data?.job?.id) throw new Error("Respuesta de trabajo mixto invalida");
      setJobs((current) => [data.job, ...current.filter((job) => job.id !== data.job.id)]);
      setNotice("Cotizacion mixta en cola. Revisa el avance en Cotizaciones.");
      const finalJob = await awaitJobResult(
        data.job,
        () => submissionEpoch === mixedQuoteSessionEpochRef.current,
      );
      if (submissionEpoch !== mixedQuoteSessionEpochRef.current || !finalJob) return;
      setJobs((current) => [finalJob, ...current.filter((job) => job.id !== finalJob.id)]);
      if (finalJob.status === "failed") {
        setError(finalJob.error_message || "La cotizacion fallo; el carrito se conservo para reintentar.");
        setNotice("");
        setOpen(true);
        return;
      }
      if (finalJob.status !== "completed") throw new Error("Estado final de cotizacion invalido");
      replaceCart([]);
      replaceSections(createInitialMixedCartSections());
      setOpen(false);
      setNotice("Cotizacion mixta lista. Revisa el archivo en Cotizaciones.");
    } catch (quoteFailure) {
      if (submissionEpoch !== mixedQuoteSessionEpochRef.current) return;
      setError(quoteFailure.message || "No se pudo generar la cotizacion mixta");
    } finally {
      if (submissionEpoch === mixedQuoteSessionEpochRef.current) {
        mixedQuoteSubmittingRef.current = false;
        setBusy(false);
      }
    }
  }

  return {
    add,
    update,
    updateImported,
    importPreview,
    remove,
    closeSection,
    renameSection,
    mergeSection,
    moveLine,
    moveLineToSection,
    updateField,
    resetSession,
    submit,
  };
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
  const [mixedCart, setMixedCart] = useState([]);
  const mixedCartRef = useRef([]);
  const [mixedCartSections, setMixedCartSections] = useState(
    () => createInitialMixedCartSections(),
  );
  const mixedCartSectionsRef = useRef(createInitialMixedCartSections());
  const [mixedCartOpen, setMixedCartOpen] = useState(false);
  const [mixedQuote, setMixedQuote] = useState({ ...EMPTY_MIXED_QUOTE });
  const [mixedQuoteBusy, setMixedQuoteBusy] = useState(false);
  const [mixedQuoteError, setMixedQuoteError] = useState("");
  const [mixedQuoteNotice, setMixedQuoteNotice] = useState("");
  const mixedQuoteSubmittingRef = useRef(false);
  const mixedQuoteSessionEpochRef = useRef(0);
  const mixedImportRevisionRef = useRef(0);
  const { request } = useApi(session?.access_token);
  const mixedRequest = useMemo(
    () => createMixedQuoteRequestSnapshot({}, mixedCartSections, mixedCart),
    [mixedCart, mixedCartSections],
  );

  async function waitForMixedQuoteJob(job, isCurrent) {
    let current = job;
    while (isCurrent() && !["completed", "failed"].includes(current?.status)) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      if (!isCurrent()) return null;
      const response = await request(`/cotizaciones/${job.id}`);
      current = response?.job;
      if (!current?.id) throw new Error("Respuesta de estado de cotizacion invalida");
    }
    return isCurrent() ? current : null;
  }

  function replaceMixedCart(next) {
    mixedCartRef.current = next;
    setMixedCart(next);
  }

  function replaceMixedCartSections(next) {
    mixedCartSectionsRef.current = next;
    setMixedCartSections(next);
  }

  const mixedQuoteController = createMixedQuoteController({
    cartRef: mixedCartRef,
    sectionsRef: mixedCartSectionsRef,
    submittingRef: mixedQuoteSubmittingRef,
    sessionEpochRef: mixedQuoteSessionEpochRef,
    importRevisionRef: mixedImportRevisionRef,
    emptyForm: EMPTY_MIXED_QUOTE,
    replaceCart: replaceMixedCart,
    replaceSections: replaceMixedCartSections,
    setOpen: setMixedCartOpen,
    setForm: setMixedQuote,
    getForm: () => mixedQuote,
    setBusy: setMixedQuoteBusy,
    setError: setMixedQuoteError,
    setNotice: setMixedQuoteNotice,
    setJobs,
    request,
    confirmQuote: (message) => window.confirm(message),
    confirmImport: (message) => window.confirm(message),
    waitForJobResult: waitForMixedQuoteJob,
  });

  function resetMixedQuoteSession() {
    mixedQuoteController.resetSession();
  }

  useEffect(() => {
    function handleAuthExpired() {
      localStorage.removeItem("mobiliti_session");
      clearCatalogCaches();
      resetMixedQuoteSession();
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
    clearCatalogCaches();
    resetMixedQuoteSession();
    setSession(null);
    setSessionNotice("");
  }

  if (!session) return <Login onLogin={login} notice={sessionNotice} />;

  const isAdmin = Boolean(session.usuario?.es_admin);
  const supplierLabels = {
    "cr-global": "CR Global",
    sonara: "Sonara",
    sunon: "Sunon",
    alma: "ALMA",
    lumbro: "Lumbro"
  };

  function addMixedCartLine(line) {
    return mixedQuoteController.add(line);
  }

  function updateMixedCartLine(key, quantity) {
    mixedQuoteController.update(key, quantity);
  }

  function updateImportedCartLineFromApp(key, edits) {
    try {
      mixedQuoteController.updateImported(key, edits);
      setMixedQuoteError("");
      return "";
    } catch (cartFailure) {
      const message = cartFailure.message || "No se pudo editar el producto importado";
      setMixedQuoteError(message);
      return message;
    }
  }

  function importQuotationPreview(preview, options) {
    return mixedQuoteController.importPreview(preview, options);
  }

  function removeMixedCartLineFromApp(key) {
    mixedQuoteController.remove(key);
  }

  function closeMixedCartSectionFromApp() {
    mixedQuoteController.closeSection();
  }

  function renameMixedCartSectionFromApp(sectionId, concept) {
    mixedQuoteController.renameSection(sectionId, concept);
  }

  function mergeMixedCartSectionFromApp(sectionId) {
    mixedQuoteController.mergeSection(sectionId);
  }

  function moveMixedCartLineFromApp(key, direction) {
    mixedQuoteController.moveLine(key, direction);
  }

  function moveMixedCartLineToSectionFromApp(key, sectionId) {
    mixedQuoteController.moveLineToSection(key, sectionId);
  }

  function updateMixedQuoteField(field, value) {
    mixedQuoteController.updateField(field, value);
  }

  function openMixedCart() {
    setMixedCartOpen(true);
  }

  async function submitMixedQuote(event, submissionLines = mixedCartRef.current) {
    const preparedRequest = submissionLines === mixedCartRef.current
      ? Object.freeze({ ...mixedQuote, ...mixedRequest })
      : null;
    return mixedQuoteController.submit(event, submissionLines, preparedRequest);
  }
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
      onImportPreview={importQuotationPreview}
      onJobChange={(job) => {
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      }}
    />
  );
  const mainView = view === "cotizaciones"
    ? <QuotesView jobs={jobs} onDownload={downloadJob} onRetry={retryJob} onDelete={deleteJob} onCreateNew={() => setView("nueva")} refreshJobs={refreshJobs} downloadState={downloadState} deleteState={deleteState} />
    : view === "nueva"
      ? quoteForm
      : view === "historial"
        ? <HistoryView jobs={jobs} onDownload={downloadJob} onRetry={retryJob} onDelete={deleteJob} downloadState={downloadState} deleteState={deleteState} />
        : view === "tarkett"
          ? <TarkettView token={session.access_token} userId={session.usuario?.id} cartLines={mixedCart} onAddCartLine={addMixedCartLine} onOpenCart={openMixedCart} cartBusy={mixedQuoteBusy} />
          : view === "offiho"
            ? <OffihoView token={session.access_token} userId={session.usuario?.id} cartLines={mixedCart} onAddCartLine={addMixedCartLine} onOpenCart={openMixedCart} cartBusy={mixedQuoteBusy} />
            : Object.hasOwn(supplierLabels, view)
              ? <SupplierCatalogView key={view} supplier={view} label={supplierLabels[view]} request={request} userId={session.usuario?.id} onAddCartLine={addMixedCartLine} onOpenCart={openMixedCart} cartLineCount={mixedCart.length} cartBusy={mixedQuoteBusy} />
        : (view === "admin" || view === "clientes") && isAdmin
          ? <AdminView token={session.access_token} />
          : quoteForm;

  return (
    <div className="app-shell">
      <Sidebar view={view} setView={setView} isAdmin={isAdmin} onLogout={logout} />
      <main className="content-shell">
        <Header user={session.usuario} subscription={session.suscripcion} cartCount={mixedCart.length} onOpenCart={openMixedCart} />
        {mixedQuoteNotice ? (
          <div className="mixed-quote-notice" role="status" aria-live="polite">
            {mixedQuoteNotice}
          </div>
        ) : null}
        {mainView}
        <MixedCartDrawer
          lines={mixedCart}
          sections={mixedCartSections}
          open={mixedCartOpen}
          form={mixedQuote}
          busy={mixedQuoteBusy}
          error={mixedQuoteError}
          notice=""
          onClose={() => setMixedCartOpen(false)}
          onFieldChange={updateMixedQuoteField}
          onQuantityChange={updateMixedCartLine}
          onImportedLineChange={updateImportedCartLineFromApp}
          onRemove={removeMixedCartLineFromApp}
          onCloseSection={closeMixedCartSectionFromApp}
          onRenameSection={renameMixedCartSectionFromApp}
          onMergeSection={mergeMixedCartSectionFromApp}
          onMoveLine={moveMixedCartLineFromApp}
          onMoveLineToSection={moveMixedCartLineToSectionFromApp}
          onSubmit={submitMixedQuote}
        />
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
