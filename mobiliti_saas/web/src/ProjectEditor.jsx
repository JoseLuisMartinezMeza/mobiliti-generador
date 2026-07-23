import {useEffect, useMemo, useState} from "react";
import {ImageOff, Trash2} from "lucide-react";

import ImportedCartLineFields from "./ImportedCartLineFields";
import {createImportedLineDraft} from "./importedCartLineDraft.js";
import {
  DEFAULT_MIXED_SECTION_CONCEPTS,
  MAX_MIXED_CART_SECTIONS,
  addProjectComplement,
  closeMixedCartSection,
  createMixedCartLine,
  groupMixedCartLines,
  mergeMixedCartSection,
  moveMixedCartLine,
  moveMixedCartLineToSection,
  projectComplements,
  projectLineMatches,
  projectMatchKey,
  removeProjectLineTree,
  renameMixedCartSection,
  replaceAllProjectLines,
  replaceProjectLine,
  updateImportedCartLine,
  updateMixedCartQuantity,
  upsertMixedCartLine,
  validateLineQuantity,
} from "./mixedCart.js";
import ProductPickerDialog from "./ProductPickerDialog";

const CUSTOMER_FIELDS = Object.freeze([
  ["Proyecto *", "proyecto", "text"],
  ["Cliente *", "cliente", "text"],
  ["Correo *", "correo", "email"],
  ["TelÃ©fono *", "telefono", "tel"],
  ["DirecciÃ³n *", "direccion", "text"],
  ["RazÃ³n social *", "razon_social", "text"],
]);

const DEFAULT_QUANTITY_RULES = Object.freeze({
  min: "1",
  step: "1",
  maxDecimals: 0,
  max: "1000000",
  integer: true,
});

const AUTOSAVE_COPY = Object.freeze({
  saving: "Guardando",
  saved: "Guardado",
  pending: "Cambios pendientes",
  conflict: "Conflicto de ediciÃ³n",
});

function defaultConcept(index) {
  return DEFAULT_MIXED_SECTION_CONCEPTS[index] || `Espacio ${index + 1}`;
}

function importedEditorKey(line) {
  return line?.kind === "imported"
    ? `${line.key}:editor-${Number.isSafeInteger(line.editorRevision) ? line.editorRevision : 0}`
    : line?.key;
}

function normalizeProjectPositions(sections, lines) {
  const principalPosition = new Map(sections.map((section) => [section.id, 0]));
  const complementPosition = new Map();
  return lines.map((line) => {
    if (line.role === "complement") {
      const position = complementPosition.get(line.parentLineId) || 0;
      complementPosition.set(line.parentLineId, position + 1);
      return {...line, position};
    }
    const position = principalPosition.get(line.sectionId) || 0;
    principalPosition.set(line.sectionId, position + 1);
    return {...line, position};
  });
}

function pickerTarget(selection, referenceLine = null) {
  const snapshot = selection.snapshot || {};
  return {
    catalog: selection.catalog,
    identity: selection.identity,
    officialCode: selection.official_code,
    provider: selection.provider,
    quantity: referenceLine?.quantity || "1",
    quantityRules: referenceLine?.quantityRules || DEFAULT_QUANTITY_RULES,
    snapshot: {
      name: snapshot.name || selection.official_code,
      code: selection.official_code,
      image_url: snapshot.image_url || "",
      unit: referenceLine?.snapshot?.unit || "PZA",
      availability: snapshot.availability || "",
      configuration: snapshot.configuration || "",
      warnings: snapshot.warnings || [],
    },
  };
}

function projectImpact(lines, line) {
  const selector = {
    provider: line.provider || line.catalog,
    officialCode: line.officialCode,
  };
  const matched = lines.filter((candidate) => projectLineMatches(candidate, selector));
  const parents = new Map(lines.map((candidate) => [candidate.lineId, candidate]));
  const sectionIds = new Set(matched.map((candidate) => (
    candidate.sectionId || parents.get(candidate.parentLineId)?.sectionId
  )).filter(Boolean));
  return {
    affected: matched.length,
    catalog: matched.filter((candidate) => candidate.kind !== "imported").length,
    imported: matched.filter((candidate) => candidate.kind === "imported").length,
    sections: sectionIds.size,
    removedComplements: matched.reduce(
      (total, candidate) => total + (
        candidate.role === "principal" ? projectComplements(lines, candidate.lineId).length : 0
      ),
      0,
    ),
    excludedUnlinked: lines.filter((candidate) => (
      !projectMatchKey(candidate.provider || candidate.catalog, candidate.officialCode)
    )).length,
  };
}

function QuantityEditor({line, disabled, onCommit}) {
  const [draft, setDraft] = useState(line.quantity);
  const [error, setError] = useState("");

  useEffect(() => {
    setDraft(line.quantity);
    setError("");
  }, [line.key, line.quantity]);

  function commit() {
    try {
      const quantity = validateLineQuantity(line, draft);
      onCommit(quantity);
      setDraft(quantity);
      setError("");
    } catch (failure) {
      setError(failure.message || "Cantidad invÃ¡lida");
    }
  }

  return (
    <label className="project-line-quantity">
      <span>Cantidad</span>
      <input
        inputMode="decimal"
        value={draft}
        disabled={disabled}
        aria-invalid={Boolean(error)}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
      />
      {error ? <small role="alert">{error}</small> : null}
    </label>
  );
}

function ImportedFields({line, disabled, onCommit}) {
  const editorKey = importedEditorKey(line);
  const [draft, setDraft] = useState(() => createImportedLineDraft(line.edits || {}));

  useEffect(() => {
    setDraft(createImportedLineDraft(line.edits || {}));
  }, [editorKey]);

  return (
    <ImportedCartLineFields
      key={editorKey}
      line={line}
      editorKey={editorKey}
      draft={draft}
      busy={disabled}
      onChange={onCommit}
      onDraftChange={setDraft}
      onValidityChange={() => {}}
    />
  );
}

function ComplementCard({
  child,
  disabled,
  onQuantity,
  onQuantityMode,
  onImportedChange,
  onRemove,
}) {
  return (
    <article className="project-complement">
      <div className="project-complement-image">
        {child.snapshot.image_url
          ? <img src={child.snapshot.image_url} alt={child.snapshot.name} />
          : <span>Sin imagen</span>}
      </div>
      <div className="project-complement-copy">
        <strong>+ {child.snapshot.name}</strong>
        <small>{child.officialCode} Â· {child.provider || child.catalog}</small>
        <ImportedFields
          line={child}
          disabled={disabled}
          onCommit={(edits) => onImportedChange(child.key, edits)}
        />
      </div>
      <QuantityEditor
        line={child}
        disabled={disabled}
        onCommit={(quantity) => onQuantity(child.key, quantity)}
      />
      <label>
        <span>Modo de cantidad</span>
        <select
          value={child.quantityMode}
          disabled={disabled}
          onChange={(event) => onQuantityMode(child.lineId, event.target.value)}
        >
          <option value="per_parent_unit">Por unidad</option>
          <option value="fixed_project">Cantidad fija</option>
        </select>
      </label>
      <button
        className="project-line-remove"
        type="button"
        disabled={disabled}
        aria-label={`Quitar ${child.snapshot.name}`}
        onClick={() => onRemove(child.lineId)}
      >
        <Trash2 size={18} />
      </button>
    </article>
  );
}

export default function ProjectEditor({
  project,
  request,
  autosave,
  onProjectChange,
  onGenerateQuote,
}) {
  const [activeTab, setActiveTab] = useState("products");
  const [picker, setPicker] = useState(null);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const lines = project?.lines || [];
  const sections = project?.sections || [];
  const quoteFields = project?.quoteFields || {};
  const principals = useMemo(
    () => lines.filter((line) => line.role !== "complement"),
    [lines],
  );
  const grouped = useMemo(
    () => groupMixedCartLines(sections, principals),
    [sections, principals],
  );
  const disabled = generating || autosave?.status === "conflict";

  function commit(next) {
    setError("");
    onProjectChange(next);
  }

  function commitLines(nextLines, nextSections = sections) {
    commit({
      ...project,
      sections: nextSections,
      lines: normalizeProjectPositions(nextSections, nextLines),
    });
  }

  function openPicker(mode, line) {
    setPicker({
      mode,
      line,
      impact: mode === "replace-all" ? projectImpact(lines, line) : null,
    });
  }

  function confirmRemoval(children) {
    return !children.length
      || window.confirm(`Este cambio retirarÃ¡ ${children.length} complemento(s). Â¿Continuar?`);
  }

  function confirmPicker(selection) {
    if (!picker) return;
    try {
      const {mode, line} = picker;
      const target = pickerTarget(selection, line?.lineId ? line : null);
      if (mode === "add") {
        const incoming = createMixedCartLine({
          ...target,
          sectionId: line.sectionId,
          position: principals.filter((candidate) => candidate.sectionId === line.sectionId).length,
        });
        commitLines(upsertMixedCartLine(lines, incoming));
      } else if (mode === "complement") {
        commitLines(addProjectComplement(lines, line.lineId, target, "per_parent_unit"));
      } else if (mode === "replace-one") {
        const children = line.role === "principal" ? projectComplements(lines, line.lineId) : [];
        if (!confirmRemoval(children)) return;
        commitLines(replaceProjectLine(lines, line.lineId, target).lines);
      } else if (mode === "replace-all") {
        const selector = {
          provider: line.provider || line.catalog,
          officialCode: line.officialCode,
        };
        const children = lines
          .filter((candidate) => candidate.role === "principal" && projectLineMatches(candidate, selector))
          .flatMap((candidate) => projectComplements(lines, candidate.lineId));
        if (!confirmRemoval(children)) return;
        commitLines(replaceAllProjectLines(lines, selector, target).lines);
      }
      setPicker(null);
    } catch (failure) {
      setError(failure.message || "No se pudo actualizar el producto.");
    }
  }

  function updateQuantity(key, quantity) {
    try {
      commitLines(updateMixedCartQuantity(lines, key, quantity));
    } catch (failure) {
      setError(failure.message || "Cantidad invÃ¡lida.");
    }
  }

  function updateImported(key, edits) {
    try {
      commitLines(updateImportedCartLine(lines, key, edits));
    } catch (failure) {
      setError(failure.message || "No se pudo editar el producto importado.");
    }
  }

  function removeLine(lineId) {
    try {
      commitLines(removeProjectLineTree(lines, lineId));
    } catch (failure) {
      setError(failure.message || "No se pudo quitar el producto.");
    }
  }

  async function generateQuote() {
    setGenerating(true);
    setError("");
    try {
      await onGenerateQuote(project);
    } catch (failure) {
      setError(failure.message || "No se pudo generar la cotizaciÃ³n.");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <section className="project-editor" aria-labelledby="project-editor-title">
      <header className="project-editor-header">
        <div>
          <span>Proyecto activo</span>
          <input
            id="project-editor-title"
            aria-label="Nombre del Proyecto"
            maxLength={120}
            value={project?.name || ""}
            disabled={disabled}
            onChange={(event) => commit({...project, name: event.target.value})}
          />
        </div>
        <div className={`project-autosave-status ${autosave?.status || "saved"}`} role="status">
          {AUTOSAVE_COPY[autosave?.status] || "Guardado"}
          {autosave?.message ? <small>{autosave.message}</small> : null}
        </div>
        <button
          type="button"
          className="primary-action"
          disabled={disabled || !principals.length || autosave?.status !== "saved"}
          onClick={generateQuote}
        >
          {generating ? "Generando cotizaciÃ³nâ€¦" : "Generar cotizaciÃ³n"}
        </button>
      </header>

      {autosave?.status === "conflict" ? (
        <div className="project-conflict" role="alert">
          <strong>El Proyecto cambiÃ³ en otra sesiÃ³n.</strong>
          <span>Vuelve a Proyectos y abre la versiÃ³n mÃ¡s reciente antes de continuar.</span>
        </div>
      ) : null}
      {error ? <p className="error-line" role="alert">{error}</p> : null}

      <nav className="project-editor-tabs" aria-label="Secciones del editor">
        <button
          type="button"
          className={activeTab === "products" ? "active" : ""}
          aria-selected={activeTab === "products"}
          onClick={() => setActiveTab("products")}
        >
          Productos
        </button>
        <button
          type="button"
          className={activeTab === "quote" ? "active" : ""}
          aria-selected={activeTab === "quote"}
          onClick={() => setActiveTab("quote")}
        >
          Datos de cotizaciÃ³n
        </button>
      </nav>

      {activeTab === "products" ? (
        <div className="project-editor-products">
          {sections.map((section, sectionIndex) => {
            const sectionLines = grouped.get(section.id) || [];
            return (
              <section className="project-editor-section" key={section.id}>
                <header className="project-editor-section-header">
                  <span>{sectionIndex + 1}-</span>
                  <input
                    aria-label={`Concepto de la secciÃ³n ${sectionIndex + 1}`}
                    maxLength={120}
                    value={section.concept}
                    disabled={disabled}
                    onChange={(event) => commit({
                      ...project,
                      sections: renameMixedCartSection(sections, section.id, event.target.value),
                    })}
                    onBlur={() => {
                      if (!section.concept.trim()) {
                        commit({
                          ...project,
                          sections: renameMixedCartSection(
                            sections,
                            section.id,
                            defaultConcept(sectionIndex),
                          ),
                        });
                      }
                    }}
                  />
                  <span>{sectionLines.length} producto(s)</span>
                  <button
                    type="button"
                    className="ghost-action"
                    disabled={disabled}
                    onClick={() => openPicker("add", {sectionId: section.id})}
                  >
                    Agregar producto
                  </button>
                  {sectionIndex > 0 ? (
                    <button
                      type="button"
                      className="ghost-action"
                      disabled={disabled}
                      onClick={() => {
                        const merged = mergeMixedCartSection(sections, lines, section.id);
                        commitLines(merged.lines, merged.sections);
                      }}
                    >
                      Unir con la anterior
                    </button>
                  ) : null}
                </header>

                <div className="project-editor-lines">
                  {sectionLines.map((line, lineIndex) => {
                    const children = projectComplements(lines, line.lineId);
                    return (
                      <article className="project-principal" key={line.lineId}>
                        <div className="project-principal-main">
                          <div className="project-line-image">
                            {line.snapshot.image_url
                              ? <img src={line.snapshot.image_url} alt={line.snapshot.name} />
                              : <ImageOff size={28} aria-label="Sin imagen" />}
                          </div>
                          <div className="project-line-copy">
                            <strong>{line.snapshot.name}</strong>
                            <small>{line.officialCode} Â· {line.provider || line.catalog}</small>
                            {line.snapshot.configuration ? <span>{line.snapshot.configuration}</span> : null}
                            <ImportedFields
                              line={line}
                              disabled={disabled}
                              onCommit={(edits) => updateImported(line.key, edits)}
                            />
                          </div>
                          <QuantityEditor
                            line={line}
                            disabled={disabled}
                            onCommit={(quantity) => updateQuantity(line.key, quantity)}
                          />
                          <div className="project-line-actions">
                            <button
                              type="button"
                              disabled={disabled || lineIndex === 0}
                              onClick={() => commitLines(moveMixedCartLine(lines, line.key, "up"))}
                            >
                              Subir
                            </button>
                            <button
                              type="button"
                              disabled={disabled || lineIndex === sectionLines.length - 1}
                              onClick={() => commitLines(moveMixedCartLine(lines, line.key, "down"))}
                            >
                              Bajar
                            </button>
                            <select
                              aria-label={`Mover ${line.snapshot.name} a otra secciÃ³n`}
                              value={section.id}
                              disabled={disabled || sections.length === 1}
                              onChange={(event) => commitLines(
                                moveMixedCartLineToSection(lines, sections, line.key, event.target.value),
                              )}
                            >
                              {sections.map((targetSection, targetIndex) => (
                                <option key={targetSection.id} value={targetSection.id}>
                                  {targetIndex + 1}-{targetSection.concept || defaultConcept(targetIndex)}
                                </option>
                              ))}
                            </select>
                            <button type="button" disabled={disabled} onClick={() => openPicker("replace-one", line)}>
                              Cambiar producto
                            </button>
                            <button type="button" disabled={disabled} onClick={() => openPicker("replace-all", line)}>
                              Cambiar todos los iguales
                            </button>
                            <button type="button" disabled={disabled} onClick={() => openPicker("complement", line)}>
                              Agregar complemento
                            </button>
                            <button
                              className="project-line-remove"
                              type="button"
                              disabled={disabled}
                              aria-label={`Quitar ${line.snapshot.name}`}
                              onClick={() => removeLine(line.lineId)}
                            >
                              <Trash2 size={18} />
                            </button>
                          </div>
                        </div>

                        {children.length ? (
                          <div className="project-complements" aria-label={`Complementos de ${line.snapshot.name}`}>
                            {children.map((child) => (
                              <ComplementCard
                                key={child.lineId}
                                child={child}
                                disabled={disabled}
                                onQuantity={updateQuantity}
                                onImportedChange={updateImported}
                                onRemove={removeLine}
                                onQuantityMode={(lineId, quantityMode) => commitLines(lines.map(
                                  (candidate) => candidate.lineId === lineId
                                    ? {...candidate, quantityMode}
                                    : candidate,
                                ))}
                              />
                            ))}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                  {!sectionLines.length ? <p className="projects-empty">Esta secciÃ³n no tiene productos.</p> : null}
                </div>

                {sectionIndex === sections.length - 1 ? (
                  <button
                    type="button"
                    className="ghost-action"
                    disabled={
                      disabled
                      || !sectionLines.length
                      || sections.length >= MAX_MIXED_CART_SECTIONS
                    }
                    onClick={() => commit({
                      ...project,
                      sections: closeMixedCartSection(sections, lines),
                    })}
                  >
                    Cerrar secciÃ³n y abrir otra
                  </button>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : (
        <form className="project-quote-fields" onSubmit={(event) => event.preventDefault()}>
          {CUSTOMER_FIELDS.map(([label, field, type]) => (
            <label key={field}>
              <span>{label}</span>
              <input
                name={field}
                type={type}
                required
                disabled={disabled}
                value={quoteFields[field] || ""}
                onChange={(event) => commit({
                  ...project,
                  quoteFields: {...quoteFields, [field]: event.target.value},
                })}
              />
            </label>
          ))}
          <label>
            <span>Moneda de cotizaciÃ³n</span>
            <select
              value={quoteFields.quote_currency || "MXN"}
              disabled={disabled}
              onChange={(event) => commit({
                ...project,
                quoteFields: {...quoteFields, quote_currency: event.target.value},
              })}
            >
              {["MXN", "USD", "EUR"].map((currency) => (
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
              disabled={disabled}
              value={quoteFields.descuento || "0"}
              onChange={(event) => commit({
                ...project,
                quoteFields: {...quoteFields, descuento: event.target.value},
              })}
            />
            <small>El primer producto controla el descuento de todos los productos en Excel.</small>
          </label>
        </form>
      )}

      <ProductPickerDialog
        open={Boolean(picker)}
        mode={picker?.mode || "add"}
        request={request}
        impact={picker?.impact}
        onCancel={() => setPicker(null)}
        onConfirm={confirmPicker}
      />
    </section>
  );
}
