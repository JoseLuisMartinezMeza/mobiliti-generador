import {useEffect, useMemo, useRef, useState} from "react";
import {
  buildCatalogSearchPath,
  canConfirmProductSelection,
  CATALOG_OPTIONS,
  catalogLabel,
  createCanonicalProductSelection,
  initialAddOnOptionIds,
  initialBaseOptionId,
  normalizeWarnings,
  productAddOnFamilies,
  productAddOnOptions,
  productBaseConfigurationLabel,
  productBaseOptions,
  productDimensions,
  productOptionLabel,
  productPriceLabel,
  productSelectionKey,
  productTechnicalKey,
  productVariantConfiguration,
  shouldShowProductImage,
} from "./productPicker";

const PAGE_SIZE = 20;

const COPY_BY_MODE = {
  add: "Agregar al Proyecto",
  "replace-one": "Cambiar producto",
  "replace-all": "Cambiar todos los iguales",
  complement: "Agregar complemento",
};

function focusableElements(container) {
  return [...container.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
  )].filter((element) => !element.hidden);
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
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedBaseOptionId, setSelectedBaseOptionId] = useState("");
  const [selectedAddOnOptionIds, setSelectedAddOnOptionIds] = useState([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [failedImageKey, setFailedImageKey] = useState("");
  const dialogRef = useRef(null);
  const searchRef = useRef(null);
  const requestVersionRef = useRef(0);
  const previousFocusRef = useRef(null);

  const confirmar = () => {
    if (canConfirm) {
      onConfirm(createCanonicalProductSelection(
        selected,
        selectedBaseOptionId,
        selectedAddOnOptionIds,
      ));
    }
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
      setSelectedBaseOptionId("");
      setSelectedAddOnOptionIds([]);
      setOffset(0);
      setTotal(0);
      setError("");
      setFailedImageKey("");
      return undefined;
    }
    previousFocusRef.current = document.activeElement;
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      previousFocusRef.current?.focus();
    };
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
        const response = await request(
          buildCatalogSearchPath({query, supplier, offset, limit: PAGE_SIZE}),
          {signal: controller.signal},
        );
        if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
        const items = Array.isArray(response?.items) ? response.items : [];
        setResults(items);
        setTotal(Number(response?.total) || 0);
        setSelected((current) => (
          current && items.some((item) => (
            productSelectionKey(item) === productSelectionKey(current)
          )) ? current : null
        ));
      } catch (requestError) {
        if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
        setResults([]);
        setTotal(0);
        setSelected(null);
        setSelectedBaseOptionId("");
        setSelectedAddOnOptionIds([]);
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
  const previewSupplier = catalogLabel(selected?.catalog);
  const warnings = normalizeWarnings(preview.warnings);
  const selectedKey = productSelectionKey(selected);
  const showImage = shouldShowProductImage(preview.image_url, failedImageKey === selectedKey);
  const baseOptions = productBaseOptions(selected);
  const baseConfigurationLabel = productBaseConfigurationLabel(selected);
  const variantConfiguration = productVariantConfiguration(selected);
  const selectedBaseOption = baseOptions.find((option) => option.id === selectedBaseOptionId);
  const addOnOptions = productAddOnOptions(selected, selectedBaseOptionId);
  const addOnFamilies = productAddOnFamilies(selected, selectedBaseOptionId);
  const canConfirm = canConfirmProductSelection(selected, selectedBaseOptionId);
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
          if (event.key === "Tab") {
            const focusables = focusableElements(dialogRef.current);
            const first = focusables[0];
            const last = focusables.at(-1);
            if (!first || !last) return;
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first.focus();
            }
          }
          if (event.key === "Escape") {
            event.preventDefault();
            cancelar();
          }
          if (event.key === "Enter" && event.target === dialogRef.current && canConfirm) {
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
                setSelectedBaseOptionId("");
                setSelectedAddOnOptionIds([]);
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
                setSelectedBaseOptionId("");
                setSelectedAddOnOptionIds([]);
              }}
            >
              <option value="">Todos los proveedores</option>
              {CATALOG_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
        </div>

        <div className="project-picker-layout">
          <section aria-label="Resultados de catálogo">
            <div className="project-picker-results" aria-label="Resultados">
              {loading && <p className="project-picker-state" role="status">Cargando productos…</p>}
              {!loading && error && <p className="project-picker-state error-line" role="alert">{error}</p>}
              {!loading && !error && results.length === 0 && (
                <p className="project-picker-state">No se encontraron productos para esta búsqueda.</p>
              )}
              {!loading && !error && results.map((item) => {
                const itemSnapshot = item.snapshot || {};
                const itemVariantConfiguration = productVariantConfiguration(item);
                const itemDimensions = productDimensions(item);
                const itemPrice = productPriceLabel(item);
                const itemKey = productSelectionKey(item);
                const isSelected = selectedKey === itemKey;
                return (
                  <button
                    className={`project-picker-result${isSelected ? " selected" : ""}`}
                    type="button"
                    aria-pressed={isSelected}
                    key={itemKey}
                    onClick={() => {
                      const next = {...item, snapshot: item.snapshot || {}};
                      const nextBaseId = initialBaseOptionId(next);
                      setSelected(next);
                      setSelectedBaseOptionId(nextBaseId);
                      setSelectedAddOnOptionIds(initialAddOnOptionIds(next, nextBaseId));
                      setFailedImageKey("");
                    }}
                  >
                    <span className="project-picker-result-image">
                      {itemSnapshot.image_url
                        ? <img src={itemSnapshot.image_url} alt={itemSnapshot.name || "Producto"} loading="lazy" />
                        : <small>Sin imagen</small>}
                    </span>
                    <span className="project-picker-result-copy">
                      <strong>{itemSnapshot.name || "Producto sin nombre"}</strong>
                      <span>{catalogLabel(item.catalog)}</span>
                      {itemVariantConfiguration ? (
                        <small>Acabado / material del aro: {itemVariantConfiguration}</small>
                      ) : null}
                      <small>{item.official_code || "Código por verificar"} · {item.catalog}</small>
                      {!item.official_code ? (
                        <small>Referencia técnica: {productTechnicalKey(item)}</small>
                      ) : null}
                      {itemDimensions ? <small>Medidas: {itemDimensions}</small> : null}
                      <small>Precio: {itemPrice}</small>
                    </span>
                  </button>
                );
              })}
            </div>
            <nav className="project-picker-pagination" aria-label="Paginación de resultados">
              <button type="button" className="ghost-action" disabled={!canGoBack || loading} onClick={() => {
                setOffset((current) => Math.max(0, current - PAGE_SIZE));
                setSelected(null);
                setSelectedBaseOptionId("");
                setSelectedAddOnOptionIds([]);
              }}>Anterior</button>
              <span aria-live="polite">{rangeLabel}</span>
              <button type="button" className="ghost-action" disabled={!canGoForward || loading} onClick={() => {
                setOffset((current) => current + PAGE_SIZE);
                setSelected(null);
                setSelectedBaseOptionId("");
                setSelectedAddOnOptionIds([]);
              }}>Siguiente</button>
            </nav>
          </section>

          <aside className="project-product-preview" aria-live="polite">
            <h3>Vista previa</h3>
            {!selected && <p>Selecciona un producto para revisar sus datos.</p>}
            {selected && (
              <>
                {showImage
                  ? <img src={preview.image_url} alt={selected.snapshot.name} onError={() => setFailedImageKey(selectedKey)} />
                  : <span className="project-picker-no-image">Sin imagen</span>}
                <strong>{preview.name}</strong>
                <span>{previewSupplier || "Proveedor no disponible"}</span>
                <span>Código: {selected.official_code || "Código por verificar"}</span>
                {!selected.official_code ? (
                  <span>Referencia técnica: {productTechnicalKey(selected)}</span>
                ) : null}
                {variantConfiguration ? (
                  <span>Acabado / material del aro: {variantConfiguration}</span>
                ) : null}
                {productDimensions(selected) ? (
                  <span>Medidas: {productDimensions(selected)}</span>
                ) : null}
                <span>
                  Precio: {productPriceLabel(
                    selected,
                    selectedBaseOptionId,
                    selectedAddOnOptionIds,
                  )}
                </span>
                {baseOptions.length > 1 ? (
                  <label>
                    {baseConfigurationLabel}
                    <select
                      aria-label={baseConfigurationLabel}
                      value={selectedBaseOptionId}
                      onChange={(event) => {
                        const nextBaseId = event.target.value;
                        const compatibleIds = new Set(
                          productAddOnOptions(selected, nextBaseId).map((option) => option.id),
                        );
                        setSelectedBaseOptionId(nextBaseId);
                        setSelectedAddOnOptionIds((current) => (
                          current.filter((optionId) => compatibleIds.has(optionId))
                        ));
                      }}
                    >
                      <option value="">Selecciona {baseConfigurationLabel.toLowerCase()}</option>
                      {baseOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {productOptionLabel(option, selected)}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <span>{baseConfigurationLabel}: {selectedBaseOption?.name || preview.configuration || "No especificada"}</span>
                )}
                {baseOptions.length > 1 && !selectedBaseOption ? (
                  <small>Selecciona {baseConfigurationLabel.toLowerCase()} para continuar.</small>
                ) : null}
                {addOnFamilies.map((family) => {
                  const selectedOption = addOnOptions.find((option) => (
                    option.family === family.family
                    && selectedAddOnOptionIds.includes(option.id)
                  ));
                  return (
                    <label key={family.family}>
                      {family.family}
                      <select
                        aria-label={`Configuración ${family.family}`}
                        value={selectedOption?.id || ""}
                        onChange={(event) => {
                          const familyIds = new Set(family.options.map((option) => option.id));
                          const nextId = event.target.value;
                          setSelectedAddOnOptionIds((current) => [
                            ...current.filter((optionId) => !familyIds.has(optionId)),
                            ...(nextId ? [nextId] : []),
                          ]);
                        }}
                      >
                        <option value="">Sin complemento</option>
                        {family.options.map((option) => (
                          <option key={option.id} value={option.id}>
                            {productOptionLabel(option, selected, {additive: true})}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                })}
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
          <button type="button" className="primary-action" disabled={!canConfirm} onClick={confirmar}>{confirmLabel}</button>
        </footer>
      </section>
    </div>
  );
}
