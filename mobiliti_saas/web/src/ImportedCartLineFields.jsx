import {
  changeImportedLineDraft,
  commitImportedLineDraft,
} from "./importedCartLineDraft.js";

export function importedLineDomPrefix(lineKey) {
  const encodedKey = Array.from(String(lineKey ?? ""))
    .map((character) => character.codePointAt(0).toString(36))
    .join("-");
  return `imported-line-${encodedKey || "empty"}`;
}

export function importedLineErrorId(lineKey, field) {
  const safeField = String(field).replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
  return `${importedLineDomPrefix(lineKey)}-${safeField}-error`;
}

export default function ImportedCartLineFields({
  line,
  editorKey,
  draft,
  busy,
  onChange,
  onDraftChange,
  onValidityChange,
}) {
  const errorId = (field) => importedLineErrorId(editorKey || line.key, field);

  if (line.kind !== "imported") return null;

  function applyDraft(next) {
    onDraftChange(next);
    onValidityChange(editorKey || line.key, Object.keys(next.invalidFields).length > 0);
  }

  function handleChange(field, value) {
    applyDraft(changeImportedLineDraft(draft, field, value));
  }

  function handleBlur(field) {
    applyDraft(commitImportedLineDraft(draft, field, onChange));
  }

  return (
    <details className="imported-line-editor">
      <summary>Editar datos importados</summary>
      <label>
        Nombre
        <input name="name" disabled={busy} value={draft.values.name} aria-invalid={Boolean(draft.invalidFields.name)} aria-describedby={draft.errors.name ? errorId("name") : undefined} onChange={(event) => handleChange("name", event.target.value)} onBlur={() => handleBlur("name")} />
        {draft.errors.name ? <small id={errorId("name")} role="alert">{draft.errors.name}</small> : null}
      </label>
      <label>
        Descripción
        <textarea name="description" disabled={busy} value={draft.values.description} aria-invalid={Boolean(draft.invalidFields.description)} aria-describedby={draft.errors.description ? errorId("description") : undefined} onChange={(event) => handleChange("description", event.target.value)} onBlur={() => handleBlur("description")} />
        {draft.errors.description ? <small id={errorId("description")} role="alert">{draft.errors.description}</small> : null}
      </label>
      <label>
        Dimensiones
        <input name="dimension" disabled={busy} value={draft.values.dimension} aria-invalid={Boolean(draft.invalidFields.dimension)} aria-describedby={draft.errors.dimension ? errorId("dimension") : undefined} onChange={(event) => handleChange("dimension", event.target.value)} onBlur={() => handleBlur("dimension")} />
        {draft.errors.dimension ? <small id={errorId("dimension")} role="alert">{draft.errors.dimension}</small> : null}
      </label>
      <label>
        Precio unitario
        <input name="unitPrice" inputMode="decimal" disabled={busy} value={draft.values.unitPrice} aria-invalid={Boolean(draft.invalidFields.unitPrice)} aria-describedby={draft.errors.unitPrice ? errorId("unitPrice") : undefined} onChange={(event) => handleChange("unitPrice", event.target.value)} onBlur={() => handleBlur("unitPrice")} />
        {draft.errors.unitPrice ? <small id={errorId("unitPrice")} role="alert">{draft.errors.unitPrice}</small> : null}
      </label>
      <label>
        Proveedor
        <input name="provider" disabled={busy} value={draft.values.provider} aria-invalid={Boolean(draft.invalidFields.provider)} aria-describedby={draft.errors.provider ? errorId("provider") : undefined} onChange={(event) => handleChange("provider", event.target.value)} onBlur={() => handleBlur("provider")} />
        {draft.errors.provider ? <small id={errorId("provider")} role="alert">{draft.errors.provider}</small> : null}
      </label>
    </details>
  );
}
