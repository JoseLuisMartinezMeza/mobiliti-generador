import React, { useEffect, useMemo, useRef, useState } from "react";
import { FolderKanban, ImageOff, Trash2, X } from "lucide-react";
import {
  DEFAULT_MIXED_SECTION_CONCEPTS,
  groupMixedCartLines,
  MAX_MIXED_CART_SECTIONS,
  validateLineQuantity,
} from "./mixedCart.js";
import ImportedCartLineFields from "./ImportedCartLineFields";
import { createImportedLineDraft } from "./importedCartLineDraft.js";

const CATALOG_LABELS = Object.freeze({
  tarkett: "Tarkett",
  offiho: "Offiho",
  "cr-global": "CR Global",
  sonara: "Sonara",
  sunon: "Sunon",
  alma: "ALMA",
  lumbro: "Lumbro",
  jome: "JOME",
  lauco: "Lauco",
  idelika: "IDÉLIKA",
  conceptos: "Conceptos",
  labenze: "Labenze",
  requiez: "Requiez",
});

const CUSTOMER_FIELDS = Object.freeze([
  ["Proyecto *", "proyecto", "text"],
  ["Cliente *", "cliente", "text"],
  ["Correo *", "correo", "email"],
  ["Telefono *", "telefono", "tel"],
  ["Direccion *", "direccion", "text"],
  ["Razon social *", "razon_social", "text"],
]);

const QUANTITY_SUBMISSION_ERROR = "Corrige la cantidad marcada antes de cotizar.";
const IMPORTED_SUBMISSION_ERROR = "Corrige los datos importados marcados antes de cotizar.";

function handleMixedCartEscape(event, busy, onClose) {
  if (event.key !== "Escape" || busy) return false;
  event.preventDefault();
  onClose();
  return true;
}

function importedEditorKey(line) {
  return line?.kind === "imported"
    ? `${line.key}:editor-${Number.isSafeInteger(line.editorRevision) ? line.editorRevision : 0}`
    : line?.key;
}

function isMixedCartSectionCollapsed(collapsedSectionIds, sectionId, defaultCollapsed) {
  return Object.prototype.hasOwnProperty.call(collapsedSectionIds, sectionId)
    ? collapsedSectionIds[sectionId]
    : Boolean(defaultCollapsed);
}

function retainActiveImportedDraftValidity(current, lines) {
  const activeEditorKeys = new Set(
    lines.filter((line) => line.kind === "imported").map(importedEditorKey),
  );
  return Object.fromEntries(
    Object.entries(current).filter(([key]) => activeEditorKeys.has(key)),
  );
}

function reconcileImportedDraftState(current, lines) {
  const drafts = {};
  lines.forEach((line) => {
    if (line.kind !== "imported") return;
    const draftKey = importedEditorKey(line);
    drafts[draftKey] = current[draftKey] || createImportedLineDraft(line.edits || {});
  });
  return drafts;
}

function reconcileQuantityDraftState(
  currentDrafts,
  previousCommitted,
  currentErrors,
  lines,
) {
  const activeKeys = new Set();
  const drafts = {};
  const committed = {};
  lines.forEach((line) => {
    const draftKey = importedEditorKey(line);
    activeKeys.add(draftKey);
    const previous = previousCommitted[draftKey];
    const current = currentDrafts[draftKey];
    const userHasDiverged = current !== undefined
      && previous !== undefined
      && current !== previous;
    drafts[draftKey] = userHasDiverged ? current : line.quantity;
    committed[draftKey] = line.quantity;
  });
  return {
    drafts,
    errors: Object.fromEntries(
      Object.entries(currentErrors).filter(([key]) => activeKeys.has(key)),
    ),
    committed,
  };
}

function submitMixedDrawerDrafts({
  event,
  lines,
  quantityDrafts,
  setErrors,
  focusFirst,
  onSubmit,
}) {
  event.preventDefault();
  const errors = {};
  let changed = false;
  const normalizedLines = lines.map((line) => {
    const draftKey = importedEditorKey(line);
    try {
      const quantity = validateLineQuantity(line, quantityDrafts[draftKey] ?? line.quantity);
      if (quantity === line.quantity) return line;
      changed = true;
      return { ...line, quantity };
    } catch (quantityError) {
      errors[draftKey] = quantityError.message || "Cantidad invalida";
      return null;
    }
  });
  const committedLines = changed ? normalizedLines : lines;
  setErrors(errors);
  if (Object.keys(errors).length) {
    focusFirst(Object.keys(errors)[0]);
    return false;
  }
  onSubmit(event, committedLines);
  return true;
}

function LegacyMixedProjectEditor({
  lines,
  sections = [],
  open,
  form,
  busy,
  error,
  notice,
  onClose,
  onFieldChange,
  onQuantityChange,
  onImportedLineChange,
  onRemove,
  onCloseSection,
  onRenameSection,
  onMergeSection,
  onMoveLine,
  onMoveLineToSection,
  onSubmit,
}) {
  const [quantityDrafts, setQuantityDrafts] = useState({});
  const [quantityErrors, setQuantityErrors] = useState({});
  const [importedDrafts, setImportedDrafts] = useState({});
  const [invalidImportedDrafts, setInvalidImportedDrafts] = useState({});
  const [importedSubmissionAttempted, setImportedSubmissionAttempted] = useState(false);
  const [quantitySubmissionAttempted, setQuantitySubmissionAttempted] = useState(false);
  const [collapsedSectionIds, setCollapsedSectionIds] = useState({});
  const previousCommittedRef = useRef({});
  const drawerRef = useRef(null);
  const quantityInputRefs = useRef({});
  const previousFocusRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousCommitted = previousCommittedRef.current;
    setQuantityDrafts((current) => reconcileQuantityDraftState(
      current,
      previousCommitted,
      {},
      lines,
    ).drafts);
    setQuantityErrors((current) => reconcileQuantityDraftState(
      {},
      {},
      current,
      lines,
    ).errors);
    previousCommittedRef.current = reconcileQuantityDraftState(
      {},
      {},
      {},
      lines,
    ).committed;
  }, [lines]);

  useEffect(() => {
    setImportedDrafts((current) => reconcileImportedDraftState(current, lines));
    setInvalidImportedDrafts((current) => retainActiveImportedDraftValidity(current, lines));
  }, [lines]);

  useEffect(() => {
    if (!open) return undefined;
    previousFocusRef.current = document.activeElement;
    drawerRef.current?.focus();
    function handleDrawerKeyDown(event) {
      if (event.key === "Escape") {
        handleMixedCartEscape(event, busyRef.current, onCloseRef.current);
        return;
      }
      if (event.key === "Tab") {
        const focusable = Array.from(drawerRef.current?.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) || []);
        if (!focusable.length) {
          event.preventDefault();
          drawerRef.current?.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === drawerRef.current)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", handleDrawerKeyDown);
    return () => {
      window.removeEventListener("keydown", handleDrawerKeyDown);
      previousFocusRef.current?.focus?.();
    };
  }, [open]);

  function commitQuantity(line) {
    const draftKey = importedEditorKey(line);
    const draft = quantityDrafts[draftKey] ?? line.quantity;
    try {
      const normalized = validateLineQuantity(line, draft);
      onQuantityChange(line.key, normalized);
      setQuantityDrafts((current) => ({ ...current, [draftKey]: normalized }));
      setQuantityErrors((current) => {
        const next = { ...current };
        delete next[draftKey];
        return next;
      });
    } catch (quantityError) {
      setQuantityErrors((current) => ({
        ...current,
        [draftKey]: quantityError.message || "Cantidad invalida",
      }));
    }
  }

  function handleDrawerSubmit(event) {
    const hasInvalidImportedDrafts = lines.some((line) => invalidImportedDrafts[importedEditorKey(line)]);
    if (hasInvalidImportedDrafts) {
      event.preventDefault();
      setImportedSubmissionAttempted(true);
      return false;
    }
    setImportedSubmissionAttempted(false);
    setQuantitySubmissionAttempted(false);
    submitMixedDrawerDrafts({
      event,
      lines,
      quantityDrafts,
      setErrors: setQuantityErrors,
      focusFirst: (key) => {
        const invalidLine = lines.find((line) => importedEditorKey(line) === key);
        if (invalidLine) {
          setCollapsedSectionIds((current) => ({
            ...current,
            [invalidLine.sectionId]: false,
          }));
        }
        setQuantitySubmissionAttempted(true);
        window.requestAnimationFrame(() => quantityInputRefs.current[key]?.focus());
      },
      onSubmit,
    });
  }

  function defaultConcept(index) {
    return DEFAULT_MIXED_SECTION_CONCEPTS[index] || `Espacio ${index + 1}`;
  }

  const visibleSections = useMemo(() => (
    sections.length
      ? sections
      : [{ id: "section-1", concept: defaultConcept(0) }]
  ), [sections]);
  const groupedLines = useMemo(
    () => groupMixedCartLines(visibleSections, lines),
    [visibleSections, lines],
  );
  const hasInvalidImportedDrafts = lines.some((line) => invalidImportedDrafts[importedEditorKey(line)]);
  const hasQuantityErrors = Object.keys(quantityErrors).length > 0;
  const draftSubmissionError = importedSubmissionAttempted && hasInvalidImportedDrafts
    ? IMPORTED_SUBMISSION_ERROR
    : quantitySubmissionAttempted && hasQuantityErrors
      ? QUANTITY_SUBMISSION_ERROR
      : "";

  useEffect(() => {
    if (!hasInvalidImportedDrafts && importedSubmissionAttempted) {
      setImportedSubmissionAttempted(false);
    }
    if (!hasQuantityErrors && quantitySubmissionAttempted) {
      setQuantitySubmissionAttempted(false);
    }
  }, [hasInvalidImportedDrafts, hasQuantityErrors, importedSubmissionAttempted, quantitySubmissionAttempted]);

  useEffect(() => {
    setCollapsedSectionIds((current) => {
      if (!lines.length) return {};
      const activeIds = new Set(visibleSections.map((section) => section.id));
      return Object.fromEntries(
        Object.entries(current).filter(([sectionId]) => activeIds.has(sectionId)),
      );
    });
  }, [lines.length, visibleSections]);

  function handleImportedDraftValidity(key, invalid) {
    setInvalidImportedDrafts((current) => ({ ...current, [key]: invalid }));
  }

  return (
    <>
      {open ? (
        <button
          className="mixed-cart-overlay"
          type="button"
          aria-label="Cerrar proyecto"
          disabled={busy}
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`mixed-cart-drawer ${open ? "open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
        aria-label="Proyecto de todos los catalogos"
        tabIndex="-1"
        ref={drawerRef}
      >
        <div className="mixed-cart-title">
          <div><FolderKanban size={22} /><h2>Proyecto</h2><span>{lines.length}</span></div>
          <button type="button" onClick={onClose} aria-label="Cerrar proyecto" disabled={busy}>
            <X size={20} />
          </button>
        </div>

        {!lines.length ? (
          <p className="mixed-cart-empty">Selecciona productos de cualquiera de los nueve catalogos.</p>
        ) : null}
        <p className="sr-only" aria-live="polite">
          {visibleSections.length} sección(es) en el proyecto.
        </p>
        <div className="mixed-cart-lines">
          {visibleSections.map((section, sectionIndex) => {
            const sectionLines = groupedLines.get(section.id) || [];
            const concept = section.concept || "";
            const sectionLabel = `${sectionIndex + 1}-${concept || defaultConcept(sectionIndex)}`;
            const sectionContentId = `mixed-cart-section-lines-${section.id}`;
            const sectionCollapsed = isMixedCartSectionCollapsed(
              collapsedSectionIds,
              section.id,
              sectionLines.length > 50,
            );
            return (
              <section
                className="mixed-cart-section"
                key={section.id}
                aria-label={`Sección ${sectionLabel}`}
              >
                <div className="mixed-cart-section-header">
                  <span className="mixed-cart-section-number" aria-hidden="true">
                    {sectionIndex + 1}-
                  </span>
                  <label className="mixed-cart-section-concept">
                    <span className="sr-only">Concepto de la sección {sectionIndex + 1}</span>
                    <input
                      value={concept}
                      maxLength={120}
                      disabled={busy}
                      aria-label={`Concepto de la sección ${sectionIndex + 1}`}
                      onChange={(event) => onRenameSection(section.id, event.target.value)}
                      onBlur={() => {
                        if (!concept.trim()) onRenameSection(section.id, defaultConcept(sectionIndex));
                      }}
                    />
                  </label>
                  <span className="mixed-cart-section-count">{sectionLines.length} producto(s)</span>
                  <button
                    className="mixed-cart-section-toggle"
                    type="button"
                    disabled={busy}
                    aria-expanded={!sectionCollapsed}
                    aria-controls={sectionContentId}
                    aria-label={`${sectionCollapsed ? "Mostrar" : "Ocultar"} productos de la seccion ${sectionLabel}`}
                    onClick={() => setCollapsedSectionIds((current) => ({
                      ...current,
                      [section.id]: !sectionCollapsed,
                    }))}
                  >
                    {sectionCollapsed ? "Mostrar productos" : "Ocultar productos"}
                  </button>
                  {sectionIndex > 0 ? (
                    <button
                      className="mixed-cart-merge-section"
                      type="button"
                      disabled={busy}
                      onClick={() => onMergeSection(section.id)}
                    >
                      Unir con la anterior
                    </button>
                  ) : null}
                </div>

                <div
                  className="mixed-cart-section-lines"
                  id={sectionContentId}
                  hidden={sectionCollapsed}
                >
                  {!sectionCollapsed ? sectionLines.map((line, lineIndex) => {
                    const productLabel = line.snapshot.name || line.snapshot.code;
                    const editorKey = importedEditorKey(line);
                    return (
                      <article className="mixed-cart-line" key={editorKey}>
                        <div className="mixed-cart-line-image">
                          {line.snapshot.image_url ? (
                            <img src={line.snapshot.image_url} alt="" loading="lazy" />
                          ) : <ImageOff size={22} aria-label="Sin imagen" />}
                        </div>
                        <div className="mixed-cart-line-copy">
                          <strong>{productLabel}</strong>
                          {line.kind === "imported" ? <small className="imported-line-badge">Importado · {line.sourceCurrency}</small> : null}
                          {line.snapshot.configuration ? (
                            <span className="mixed-cart-line-configuration">{line.snapshot.configuration}</span>
                          ) : null}
                          <small>{line.snapshot.code} · {line.kind === "imported" ? "Importado" : CATALOG_LABELS[line.catalog] || line.catalog}</small>
                          {line.snapshot.availability ? <small>{line.snapshot.availability}</small> : null}
                          {(line.snapshot.warnings || []).map((warning, index) => (
                            <em key={`${line.key}-warning-${index}`}>{warning || "Codigo por verificar"}</em>
                          ))}
                          <ImportedCartLineFields
                            key={editorKey}
                            line={line}
                            editorKey={editorKey}
                            draft={importedDrafts[editorKey] || createImportedLineDraft(line.edits || {})}
                            busy={busy}
                            onChange={(edits) => onImportedLineChange(line.key, edits)}
                            onDraftChange={(nextDraft) => setImportedDrafts((current) => ({
                              ...current,
                              [editorKey]: nextDraft,
                            }))}
                            onValidityChange={handleImportedDraftValidity}
                          />
                        </div>
                        <label className="mixed-cart-quantity">
                          <span>Cantidad</span>
                          <input
                            ref={(element) => { quantityInputRefs.current[editorKey] = element; }}
                            inputMode="decimal"
                            aria-label={`Cantidad para ${productLabel}`}
                            value={quantityDrafts[editorKey] ?? line.quantity}
                            disabled={busy}
                            aria-invalid={Boolean(quantityErrors[editorKey])}
                            onChange={(event) => setQuantityDrafts((current) => ({
                              ...current,
                              [editorKey]: event.target.value,
                            }))}
                            onBlur={() => commitQuantity(line)}
                          />
                          {quantityErrors[editorKey] ? <small role="alert">{quantityErrors[editorKey]}</small> : null}
                        </label>
                        <div className="mixed-cart-line-actions">
                          <div className="mixed-cart-order-controls" aria-label={`Orden de ${productLabel}`}>
                            <button
                              type="button"
                              disabled={busy || lineIndex === 0}
                              onClick={() => onMoveLine(line.key, "up")}
                              aria-label={`Subir ${productLabel}`}
                            >
                              Subir
                            </button>
                            <button
                              type="button"
                              disabled={busy || lineIndex === sectionLines.length - 1}
                              onClick={() => onMoveLine(line.key, "down")}
                              aria-label={`Bajar ${productLabel}`}
                            >
                              Bajar
                            </button>
                          </div>
                          <label className="mixed-cart-move-section">
                            <span>Mover a sección</span>
                            <select
                              value={section.id}
                              disabled={busy || visibleSections.length === 1}
                              aria-label={`Mover ${productLabel} a otra sección`}
                              onChange={(event) => onMoveLineToSection(line.key, event.target.value)}
                            >
                              {visibleSections.map((option, optionIndex) => (
                                <option key={option.id} value={option.id}>
                                  {optionIndex + 1}-{option.concept || defaultConcept(optionIndex)}
                                </option>
                              ))}
                            </select>
                          </label>
                          <button
                            className="mixed-cart-remove"
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              setInvalidImportedDrafts((current) => {
                                const next = { ...current };
                                delete next[line.key];
                                return next;
                              });
                              onRemove(line.key);
                            }}
                            aria-label={`Quitar ${productLabel}`}
                          >
                            <Trash2 size={18} />
                          </button>
                        </div>
                      </article>
                    );
                  }) : null}
                </div>

                {sectionIndex === visibleSections.length - 1 ? (
                  <div className="mixed-cart-section-actions">
                    <button
                      type="button"
                      disabled={
                        busy
                        || !sectionLines.length
                        || visibleSections.length >= MAX_MIXED_CART_SECTIONS
                      }
                      onClick={onCloseSection}
                    >
                      Cerrar sección y abrir otra
                    </button>
                    {!sectionLines.length ? (
                      <small>Agrega un producto para habilitar una nueva sección.</small>
                    ) : null}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>

        <form className="mixed-quote-form" onSubmit={handleDrawerSubmit}>
          {CUSTOMER_FIELDS.map(([label, field, type]) => (
            <label key={field}>
              <span>{label}</span>
              <input
                name={field}
                type={type}
                required
                disabled={busy}
                value={form[field]}
                onChange={(event) => onFieldChange(field, event.target.value)}
              />
            </label>
          ))}
          <label>
            <span>Moneda de cotizacion</span>
            <select
              value={form.quote_currency}
              disabled={busy}
              onChange={(event) => onFieldChange("quote_currency", event.target.value)}
            >
              {['MXN', 'USD', 'EUR'].map((currency) => (
                <option key={currency} value={currency}>{currency}</option>
              ))}
            </select>
            <small>Todos los precios se convierten una sola vez a la moneda seleccionada.</small>
          </label>
          <label>
            <span>Descuento general (%)</span>
            <input
              type="number"
              min="0"
              max="100"
              step="0.01"
              disabled={busy}
              value={form.descuento}
              onChange={(event) => onFieldChange("descuento", event.target.value)}
            />
            <small>El descuento global se aplica según las fórmulas y el redondeo de la plantilla seleccionada.</small>
          </label>
          {error ? <div className="error-line" role="alert">{error}</div> : null}
          {draftSubmissionError ? <div className="error-line" role="alert">{draftSubmissionError}</div> : null}
          {notice ? <div className="notice-line" role="status" aria-live="polite">{notice}</div> : null}
          <button className="primary-action" disabled={busy || !lines.length || hasInvalidImportedDrafts} type="submit">
            {busy ? "Cotizando..." : "Cotizar todos los catalogos"}
          </button>
        </form>
      </aside>
    </>
  );
}

export default function MixedCartDrawer({
  lines,
  sections = [],
  open,
  projectName,
  autosave,
  busy,
  onClose,
  onEditProject,
}) {
  const drawerRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    previousFocusRef.current = document.activeElement;
    drawerRef.current?.focus();
    function handleKeyDown(event) {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onClose();
      }
      if (event.key === "Tab") {
        const focusable = Array.from(drawerRef.current?.querySelectorAll(
          'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ) || []);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus?.();
    };
  }, [open, busy, onClose]);

  if (!open) return null;

  const principalCount = lines.filter((line) => line.role !== "complement").length;
  const complementCount = lines.length - principalCount;
  const occupiedSections = new Set(
    lines.filter((line) => line.role !== "complement").map((line) => line.sectionId),
  ).size;
  const autosaveCopy = {
    saving: "Guardando",
    saved: "Guardado",
    pending: "Cambios pendientes",
    conflict: "Conflicto de edición",
  };

  return (
    <>
      {open ? (
        <button
          className="mixed-cart-overlay"
          type="button"
          aria-label="Cerrar proyecto"
          disabled={busy}
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`mixed-cart-drawer project-quick-panel ${open ? "open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
        aria-label="Proyecto activo"
        tabIndex="-1"
        ref={drawerRef}
      >
        <div className="mixed-cart-title">
          <div><FolderKanban size={22} /><h2>Proyecto</h2><span>{lines.length}</span></div>
          <button type="button" onClick={onClose} aria-label="Cerrar proyecto" disabled={busy}>
            <X size={20} />
          </button>
        </div>
        <div className="project-quick-summary">
          <strong>{projectName || "Proyecto sin guardar"}</strong>
          <span>{principalCount} producto(s) principal(es)</span>
          <span>{complementCount} complemento(s)</span>
          <span>{occupiedSections || sections.length} sección(es)</span>
          <small role="status">{autosaveCopy[autosave?.status] || "Cambios pendientes"}</small>
        </div>
        <button
          type="button"
          className="primary-action project-quick-edit"
          disabled={busy}
          onClick={onEditProject}
        >
          Editar Proyecto
        </button>
      </aside>
    </>
  );
}
