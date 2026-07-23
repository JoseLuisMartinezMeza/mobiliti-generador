import { validateImportedCartEdits } from "./mixedCart.js";

function validationError(values) {
  try {
    validateImportedCartEdits(values);
    return "";
  } catch (error) {
    return error.message || "Datos importados invalidos";
  }
}

function withoutField(record, field) {
  const next = { ...record };
  delete next[field];
  return next;
}

export function createImportedLineDraft(edits) {
  return {
    values: { ...edits },
    errors: {},
    invalidFields: {},
  };
}

export function changeImportedLineDraft(draft, field, value) {
  const values = { ...draft.values, [field]: value };
  const error = validationError(values);
  return {
    values,
    errors: withoutField(draft.errors, field),
    invalidFields: error ? { [field]: error } : {},
  };
}

export function commitImportedLineDraft(draft, field, onCommit) {
  const error = validationError(draft.values);
  if (error) {
    return {
      ...draft,
      errors: { ...draft.errors, [field]: error },
      invalidFields: { [field]: error },
    };
  }
  try {
    const failure = onCommit({ [field]: draft.values[field] });
    if (typeof failure === "string" && failure) throw new Error(failure);
    return {
      ...draft,
      errors: withoutField(draft.errors, field),
      invalidFields: {},
    };
  } catch (commitFailure) {
    const message = commitFailure.message || "No se pudo guardar el dato importado";
    return {
      ...draft,
      errors: { ...draft.errors, [field]: message },
      invalidFields: { [field]: message },
    };
  }
}
