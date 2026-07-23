import {useEffect, useMemo, useRef, useState} from "react";

const PAGE_SIZE = 20;

const COPY_BY_MODE = {
  add: "Agregar al Proyecto",
  "replace-one": "Cambiar producto",
  "replace-all": "Cambiar todos los iguales",
  complement: "Agregar complemento",
};

function normalizarAdvertencias(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [value] : [];
}

function crearSeleccionConfirmable(item) {
  const snapshot = item.snapshot || {};
  return {
    catalog: item.catalog,
    official_code: item.official_code,
    supplier: item.supplier || item.provider || snapshot.supplier || "",
    snapshot: {
      name: snapshot.name || "",
      image_url: snapshot.image_url || "",
      configuration: snapshot.configuration || "",
      availability: snapshot.availability || "",
      warnings: normalizarAdvertencias(snapshot.warnings || item.warnings),
    },
  };
}

export default function ProductPickerDialog({
  open,
  mode,
  request,
  impact,
  onCancel,
  onConfirm,
}) {
  const [query, setQuery] = useState("");
  const [supplier, setSupplier] = useState("");
  const [supplierOptions, setSupplierOptions] = useState([]);
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef(null);
  const searchRef = useRef(null);
  const requestVersionRef = useRef(0);

  const confirmar = () => {
    if (selected) onConfirm(crearSeleccionConfirmable(selected));
  };

  const cancelar = () => {
    onCancel();
  };

  useEffect(() => {
    if (!open) {
      setQuery("");
      setSupplier("");
      setResults([]);
      setSelected(null);
      setOffset(0);
      setTotal(0);
      setError("");
      return undefined;
    }
    searchRef.current?.focus();
    return undefined;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    const requestVersion = requestVersionRef.current + 1;
    requestVersionRef.current = requestVersion;
    setLoading(true);
    setError("");
    const timer = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams({
          q: query,
          supplier,
          offset: String(offset),
          limit: String(PAGE_SIZE),
        });
        const response = await request(
          `/catalogs/search?${params.toString()}`,
          {signal: controller.signal},
        );
        if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
        const items = Array.isArray(response?.items) ? response.items : [];
        setResults(items);
        setTotal(Number(response?.total) || 0);
        setSupplierOptions((current) => Array.from(new Set([
          ...current,
          ...(response?.suppliers || []),
          ...items.map((item) => item.supplier || item.provider || item.snapshot?.supplier),
        ].filter(Boolean))).sort());
        setSelected((current) => (
          current && items.some((item) => (
            item.catalog === current.catalog
            && item.official_code === current.official_code
          )) ? current : null
        ));
      } catch (requestError) {
        if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
        setResults([]);
        setTotal(0);
        setSelected(null);
        setError(requestError?.message || "No fue posible cargar el catálogo.");
      } finally {
        if (!controller.signal.aborted && requestVersion === requestVersionRef.current) {
          setLoading(false);
        }
      }
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query, supplier, offset, request]);

  const rangeLabel = useMemo(() => {
    if (!total) return "0–0 de 0";
    return `${offset + 1}–${Math.min(offset + results.length, total)} de ${total}`;
  }, [offset, results.length, total]);

  if (!open) return null;

  const confirmLabel = COPY_BY_MODE[mode] || COPY_BY_MODE.add;
  const preview = selected?.snapshot || {};
  const previewSupplier = selected?.supplier || selected?.provider || preview.supplier;
  const warnings = normalizarAdvertencias(preview.warnings || selected?.warnings);
  const canGoBack = offset > 0;
  const canGoForward = offset + results.length < total;

  return (
    <div className="project-picker-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) cancelar();
    }}>
      <section
        ref={dialogRef}
        className="project-product-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Seleccionar producto"
        aria-describedby="project-picker-help"
        tabIndex="-1"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            cancelar();
          }
          if (event.key === "Enter" && event.target === dialogRef.current && selected) {
            event.preventDefault();
            confirmar();
          }
        }}
      >
        <header className="project-picker-header">
          <div>
            <h2>{confirmLabel}</h2>
            <p id="project-picker-help">Busca un producto del catálogo para seleccionarlo.</p>
          </div>
          <button className="picker-close" type="button" aria-label="Cerrar selector" onClick={cancelar}>×</button>
        </header>

        {mode === "replace-all" && (
          <p className="project-picker-impact">
            {impact?.affected || 0} ocurrencias · {impact?.imported || 0} importadas · {impact?.sections || 0} secciones · {impact?.removedComplements || 0} complementos retirados · {impact?.excludedUnlinked || 0} sin proveedor/código excluidas
          </p>
        )}

        <div className="project-picker-filters">
          <label>
            Buscar producto
            <input
              ref={searchRef}
              aria-label="Buscar producto"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
                setSelected(null);
              }}
            />
          </label>
          <label>
            Proveedor
            <select
              aria-label="Proveedor"
              value={supplier}
              onChange={(event) => {
                setSupplier(event.target.value);
                setOffset(0);
                setSelected(null);
              }}
            >
              <option value="">Todos los proveedores</option>
              {supplierOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
        </div>

        <div className="project-picker-layout">
          <section aria-label="Resultados de catálogo">
            <div className="project-picker-results" role="listbox" aria-label="Resultados">
              {loading && <p className="project-picker-state" role="status">Cargando productos…</p>}
              {!loading && error && <p className="project-picker-state error-line" role="alert">{error}</p>}
              {!loading && !error && results.length === 0 && (
                <p className="project-picker-state">No se encontraron productos para esta búsqueda.</p>
              )}
              {!loading && !error && results.map((item) => {
                const itemSnapshot = item.snapshot || {};
                const isSelected = selected?.catalog === item.catalog
                  && selected?.official_code === item.official_code;
                return (
                  <button
                    className={`project-picker-result${isSelected ? " selected" : ""}`}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    key={`${item.catalog}:${item.official_code}`}
                    onClick={() => setSelected({...item, snapshot: item.snapshot || {}})}
                  >
                    <strong>{itemSnapshot.name || "Producto sin nombre"}</strong>
                    <span>{item.supplier || item.provider || itemSnapshot.supplier || "Proveedor no disponible"}</span>
                    <small>{item.official_code} · {item.catalog}</small>
                  </button>
                );
              })}
            </div>
            <nav className="project-picker-pagination" aria-label="Paginación de resultados">
              <button type="button" className="ghost-action" disabled={!canGoBack || loading} onClick={() => {
                setOffset((current) => Math.max(0, current - PAGE_SIZE));
                setSelected(null);
              }}>Anterior</button>
              <span aria-live="polite">{rangeLabel}</span>
              <button type="button" className="ghost-action" disabled={!canGoForward || loading} onClick={() => {
                setOffset((current) => current + PAGE_SIZE);
                setSelected(null);
              }}>Siguiente</button>
            </nav>
          </section>

          <aside className="project-product-preview" aria-live="polite">
            <h3>Vista previa</h3>
            {!selected && <p>Selecciona un producto para revisar sus datos.</p>}
            {selected && (
              <>
                {preview.image_url
                  ? <img src={preview.image_url} alt={selected.snapshot.name} />
                  : <span className="project-picker-no-image">Sin imagen</span>}
                <strong>{preview.name}</strong>
                <span>{previewSupplier || "Proveedor no disponible"}</span>
                <span>Código: {selected.official_code}</span>
                <span>Configuración: {preview.configuration || "No especificada"}</span>
                <span>Disponibilidad: {preview.availability || "No especificada"}</span>
                {warnings.length > 0 && (
                  <ul className="project-picker-warnings">
                    {warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}
                  </ul>
                )}
              </>
            )}
          </aside>
        </div>

        <footer className="project-picker-actions">
          <button type="button" className="ghost-action" onClick={cancelar}>Cancelar</button>
          <button type="button" className="primary-action" disabled={!selected} onClick={confirmar}>{confirmLabel}</button>
        </footer>
      </section>
    </div>
  );
}
