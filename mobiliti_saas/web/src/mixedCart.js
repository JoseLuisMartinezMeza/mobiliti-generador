export const MIXED_CATALOGS = Object.freeze([
  "tarkett",
  "offiho",
  "cr-global",
  "sonara",
  "sunon",
  "alma",
  "lumbro",
  "jome",
  "lauco",
]);

export const MAX_MIXED_CART_SECTIONS = 32;
export const DEFAULT_MIXED_SECTION_CONCEPTS = Object.freeze([
  "Recepción",
  "Sala de estar",
  "Operativos",
  "Privados",
  "Sala de juntas",
  "Dirección",
  "Áreas comunes",
  "Capacitación",
  "Comedor",
  "Otro",
]);

const SUPPLIER_CATALOGS = new Set(MIXED_CATALOGS.slice(2));
const QUANTITY_PATTERN = /^(?:0|[1-9]\d{0,6})(?:\.(\d{1,6}))?$/;
const IDENTITY_CONTROL_PATTERN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const PYTHON_EDGE_WHITESPACE_PATTERN = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;
const QUANTITY_SCALE = 1000000n;
const hasOwn = (value, field) => Object.prototype.hasOwnProperty.call(value, field);
const SECTION_ID_PATTERN = /^section-([1-9]\d*)$/;
const IMPORTED_CURRENCIES = new Set(["MXN", "USD", "EUR"]);
const IMPORTED_EDIT_FIELDS = new Set([
  "officialCode", "name", "description", "dimension", "unitPrice", "provider",
]);
const IMPORTED_DESCRIPTION_LIMIT = 2000;
const IMPORTED_PRICE_PATTERN = /^(?:0|[1-9]\d{0,9})(?:\.\d{1,6})?$/;
const IMPORTED_MAX_QUANTITY = 1000000n * QUANTITY_SCALE;
const PROJECT_SCHEMA_VERSION = 1;
const PROJECT_LINE_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const PROJECT_ROLES = new Set(["principal", "complement"]);
const COMPLEMENT_QUANTITY_MODES = new Set(["per_parent_unit", "fixed_project"]);
const PROJECT_QUOTE_REQUIRED_FIELDS = new Set([
  "proyecto", "cliente", "correo", "telefono", "direccion", "razon_social",
  "quote_currency", "descuento",
]);
const PROJECT_QUOTE_FIELDS = new Set([
  ...PROJECT_QUOTE_REQUIRED_FIELDS,
  "template", "description_language",
]);
const PROJECT_TEMPLATES = new Set(["official_2026_gdl", "sunon_cdmx_v1c"]);
const PROJECT_DESCRIPTION_LANGUAGES = new Set(["es", "en"]);

function pythonStrip(value) {
  return value.replace(PYTHON_EDGE_WHITESPACE_PATTERN, "");
}

function normalizedText(value, field, { allowEmpty = false, limit = 1000 } = {}) {
  if (typeof value !== "string") throw new Error(`${field} requerido`);
  const text = pythonStrip(value);
  const codePointLength = Array.from(text).length;
  if ((!text && !allowEmpty) || codePointLength > limit || IDENTITY_CONTROL_PATTERN.test(text)) {
    throw new Error(`${field} requerido`);
  }
  return text;
}

function defaultSectionConcept(index) {
  return DEFAULT_MIXED_SECTION_CONCEPTS[index] || `Espacio ${index + 1}`;
}

function normalizedSectionId(value) {
  const sectionId = normalizedText(value, "Seccion", { limit: 64 });
  if (!SECTION_ID_PATTERN.test(sectionId)) throw new Error("Seccion invalida");
  return sectionId;
}

function normalizedProjectLineId(value) {
  if (typeof value !== "string" || !PROJECT_LINE_ID_PATTERN.test(value)) {
    throw new Error("lineId invalido");
  }
  return value;
}

function normalizedProjectRole(value) {
  if (!PROJECT_ROLES.has(value)) throw new Error("Rol de linea invalido");
  return value;
}

function normalizedPosition(value) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("Posicion de linea invalida");
  return value;
}

function normalizedOfficialCode(value, snapshot) {
  return normalizedText(value ?? snapshot?.code ?? "", "Codigo oficial", { allowEmpty: true, limit: 500 });
}

function normalizedProvider(value, catalog) {
  return normalizedText(value ?? catalog ?? "", "Proveedor", { allowEmpty: true, limit: 500 });
}

function validatedSections(sections) {
  if (!Array.isArray(sections) || !sections.length || sections.length > MAX_MIXED_CART_SECTIONS) {
    throw new Error("Secciones invalidas");
  }
  const seen = new Set();
  return sections.map((section, index) => {
    if (!section || typeof section !== "object" || Array.isArray(section)) {
      throw new Error("Seccion invalida");
    }
    const id = normalizedSectionId(section.id);
    if (seen.has(id)) throw new Error("Seccion duplicada");
    seen.add(id);
    const concept = normalizedText(section.concept ?? "", "Concepto", {
      allowEmpty: true,
      limit: 120,
    });
    return { id, concept };
  });
}

export function groupMixedCartLines(sections, lines) {
  if (!Array.isArray(sections) || !Array.isArray(lines)) {
    throw new Error("Lineas de carrito invalidas");
  }
  const grouped = new Map(sections.map((section) => [section.id, []]));
  for (const line of lines) {
    const bucket = grouped.get(line.sectionId);
    if (!bucket) throw new Error("Seccion de producto invalida");
    bucket.push(line);
  }
  return grouped;
}

export function createInitialMixedCartSections() {
  return [{ id: "section-1", concept: defaultSectionConcept(0) }];
}

export function mixedCartSectionLabel(section, index) {
  const concept = normalizedText(section?.concept ?? "", "Concepto", {
    allowEmpty: true,
    limit: 120,
  }) || defaultSectionConcept(index);
  return `${index + 1}-${concept}`;
}

export function closeMixedCartSection(sections, lines) {
  const current = validatedSections(sections);
  const active = current[current.length - 1];
  if (!lines.some((line) => line.sectionId === active.id)) {
    throw new Error("Agrega al menos un producto antes de cerrar la seccion");
  }
  if (current.length >= MAX_MIXED_CART_SECTIONS) {
    throw new Error(`Limite de ${MAX_MIXED_CART_SECTIONS} secciones alcanzado`);
  }
  const highestId = current.reduce((highest, section) => {
    const match = SECTION_ID_PATTERN.exec(section.id);
    return Math.max(highest, Number(match?.[1] || 0));
  }, 0);
  return [
    ...current,
    { id: `section-${highestId + 1}`, concept: defaultSectionConcept(current.length) },
  ];
}

export function renameMixedCartSection(sections, id, concept) {
  const current = validatedSections(sections);
  const sectionId = normalizedSectionId(id);
  const index = current.findIndex((section) => section.id === sectionId);
  if (index < 0) throw new Error("Seccion no encontrada");
  const nextConcept = normalizedText(concept, "Concepto", { allowEmpty: true, limit: 120 });
  return current.map((section, position) => (
    position === index ? { ...section, concept: nextConcept } : section
  ));
}

export function mergeMixedCartSection(sections, lines, id) {
  const current = validatedSections(sections);
  const sectionId = normalizedSectionId(id);
  const index = current.findIndex((section) => section.id === sectionId);
  if (index <= 0) throw new Error("La primera seccion no se puede unir con una anterior");
  const targetId = current[index - 1].id;
  return {
    sections: current.filter((section) => section.id !== sectionId),
    lines: lines.map((line) => (
      line.sectionId === sectionId ? { ...line, sectionId: targetId } : line
    )),
  };
}

export function moveMixedCartLine(lines, key, direction) {
  if (!new Set(["up", "down"]).has(direction)) throw new Error("Direccion invalida");
  const index = lines.findIndex((line) => line.key === key);
  if (index < 0) throw new Error("Linea de carrito no encontrada");
  const sectionId = lines[index].sectionId;
  const positions = lines.flatMap((line, position) => (
    line.sectionId === sectionId ? [position] : []
  ));
  const sectionPosition = positions.indexOf(index);
  const targetSectionPosition = direction === "up" ? sectionPosition - 1 : sectionPosition + 1;
  if (targetSectionPosition < 0 || targetSectionPosition >= positions.length) return [...lines];
  const targetIndex = positions[targetSectionPosition];
  const next = [...lines];
  [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
  return next;
}

export function moveMixedCartLineToSection(lines, sections, key, targetId) {
  const currentSections = validatedSections(sections);
  const sectionId = normalizedSectionId(targetId);
  const targetSectionIndex = currentSections.findIndex((section) => section.id === sectionId);
  if (targetSectionIndex < 0) throw new Error("Seccion no encontrada");
  const sourceIndex = lines.findIndex((line) => line.key === key);
  if (sourceIndex < 0) throw new Error("Linea de carrito no encontrada");
  if (lines[sourceIndex].sectionId === sectionId) return [...lines];

  const moved = { ...lines[sourceIndex], sectionId };
  const remaining = lines.filter((_, index) => index !== sourceIndex);
  const targetPositions = remaining.flatMap((line, index) => (
    line.sectionId === sectionId ? [index] : []
  ));
  if (targetPositions.length) {
    const insertion = targetPositions[targetPositions.length - 1] + 1;
    return [...remaining.slice(0, insertion), moved, ...remaining.slice(insertion)];
  }
  const order = new Map(currentSections.map((section, index) => [section.id, index]));
  const insertion = remaining.findIndex((line) => (
    (order.get(line.sectionId) ?? Number.POSITIVE_INFINITY) > targetSectionIndex
  ));
  if (insertion < 0) return [...remaining, moved];
  return [...remaining.slice(0, insertion), moved, ...remaining.slice(insertion)];
}

export function compactMixedCartSections(sections, lines) {
  const current = validatedSections(sections);
  if (!lines.length) {
    return { sections: createInitialMixedCartSections(), lines: [] };
  }
  const known = new Set(current.map((section) => section.id));
  if (lines.some((line) => !known.has(line.sectionId))) throw new Error("Seccion de producto invalida");
  const occupied = new Set(lines.map((line) => line.sectionId));
  const lastId = current[current.length - 1].id;
  return {
    sections: current.filter((section) => occupied.has(section.id) || section.id === lastId),
    lines: [...lines],
  };
}

export function toMixedQuoteSections(sections, lines) {
  const current = validatedSections(sections);
  const groupedLines = groupMixedCartLines(current, lines);
  return current.flatMap((section, index) => {
    const itemKeys = groupedLines.get(section.id).map((line) => line.key);
    if (!itemKeys.length) return [];
    return [{
      id: section.id,
      title: normalizedText(section.concept, "Concepto", { allowEmpty: true, limit: 120 })
        || defaultSectionConcept(index),
      item_keys: itemKeys,
    }];
  });
}

function freezeMixedQuoteRequestValue(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freezeMixedQuoteRequestValue);
  return Object.freeze(value);
}

export function createMixedQuoteRequestSnapshot(form, sections, lines) {
  return freezeMixedQuoteRequestValue({
    ...form,
    items: lines.map(toMixedQuoteItem),
    sections: toMixedQuoteSections(sections, lines),
  });
}

function identityRecord(identity) {
  if (!identity || typeof identity !== "object" || Array.isArray(identity)) {
    throw new Error("Identidad invalida");
  }
  const prototype = Object.getPrototypeOf(identity);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new Error("Identidad invalida");
  }
  return identity;
}

function compareUnicodeCodePoints(left, right) {
  const leftPoints = Array.from(left);
  const rightPoints = Array.from(right);
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = leftPoints[index].codePointAt(0) - rightPoints[index].codePointAt(0);
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

function normalizedAddOns(value) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 200) throw new Error("Add-ons invalidos");
  const result = Array.from({ length: value.length }, (_, index) => {
    if (!hasOwn(value, index)) throw new Error("Add-on requerido");
    return normalizedText(value[index], "Add-on", { limit: 500 });
  });
  if (new Set(result).size !== result.length) throw new Error("Add-ons duplicados");
  return result.sort(compareUnicodeCodePoints);
}

function normalizedIdentity(catalog, identity) {
  const record = identityRecord(identity);
  if (catalog === "tarkett") {
    return { code: normalizedText(hasOwn(record, "code") ? record.code : undefined, "code") };
  }
  if (catalog === "offiho") {
    return {
      inventory_key: normalizedText(
        hasOwn(record, "inventory_key") ? record.inventory_key : undefined,
        "inventory_key",
      ),
    };
  }
  if (!SUPPLIER_CATALOGS.has(catalog)) throw new Error("Catalogo mixto no soportado");
  const internalId = normalizedText(
    hasOwn(record, "internal_id") ? record.internal_id : undefined,
    "internal_id",
  );
  const baseOptionId = !hasOwn(record, "base_option_id")
    ? ""
    : normalizedText(record.base_option_id, "base_option_id", { allowEmpty: true, limit: 500 });
  return {
    internal_id: internalId,
    base_option_id: baseOptionId,
    add_on_option_ids: normalizedAddOns(
      hasOwn(record, "add_on_option_ids") ? record.add_on_option_ids : undefined,
    ),
  };
}

function keyFromIdentity(catalog, identity) {
  if (catalog === "tarkett") return `tarkett:${identity.code}`;
  if (catalog === "offiho") return `offiho:${identity.inventory_key}`;
  return `${catalog}:${JSON.stringify([
    identity.internal_id,
    identity.base_option_id,
    identity.add_on_option_ids,
  ])}`;
}

export function mixedCartKey(catalog, identity) {
  if (!MIXED_CATALOGS.includes(catalog)) throw new Error("Catalogo mixto no soportado");
  return keyFromIdentity(catalog, normalizedIdentity(catalog, identity));
}

function quantityMicrounits(value) {
  if (typeof value !== "string") throw new Error("Cantidad invalida");
  const text = value.trim();
  const match = QUANTITY_PATTERN.exec(text);
  if (!match) throw new Error("Cantidad invalida");
  const [integer, fraction = ""] = text.split(".");
  const result = BigInt(integer) * QUANTITY_SCALE
    + BigInt((fraction + "000000").slice(0, 6));
  if (result === 0n) throw new Error("Cantidad invalida");
  return result;
}

function quantityFromMicrounits(value) {
  const integer = value / QUANTITY_SCALE;
  const fraction = String(value % QUANTITY_SCALE).padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${integer}.${fraction}` : String(integer);
}

function normalizedPersistedDecimalText(value, field) {
  if (typeof value !== "string") throw new Error(`${field} invalido`);
  const text = pythonStrip(value);
  if (!text || IDENTITY_CONTROL_PATTERN.test(text)) throw new Error(`${field} invalido`);
  return text;
}

function normalizedBackendProjectQuantity(value) {
  const text = normalizedPersistedDecimalText(value, "Cantidad");
  const normalized = text.replace(/_/g, "");
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(normalized);
  if (!match || match[1] === "-") throw new Error("Cantidad invalida");
  const integer = match[2] || "";
  const fraction = match[3] ?? match[4] ?? "";
  const digits = `${integer}${fraction}`.replace(/^0+/, "");
  if (!digits) throw new Error("Cantidad invalida");
  const scale = BigInt(fraction.length) - BigInt(match[5] || "0");
  const digitLength = BigInt(digits.length);

  if (scale >= 0n) {
    const maximumLength = 7n + scale;
    if (digitLength > maximumLength) throw new Error("Cantidad mayor al maximo permitido");
    if (digitLength === maximumLength) {
      const maximum = `1${"0".repeat(Number(6n + scale))}`;
      if (digits > maximum) throw new Error("Cantidad mayor al maximo permitido");
    }
  } else {
    const expandedLength = digitLength - scale;
    if (expandedLength > 7n) throw new Error("Cantidad mayor al maximo permitido");
    if (expandedLength === 7n) {
      const expanded = `${digits}${"0".repeat(Number(-scale))}`;
      if (expanded > "1000000") throw new Error("Cantidad mayor al maximo permitido");
    }
  }

  if (scale > 1000000n || scale < -1000000n) return normalized;
  if (scale <= 0n) return `${digits}${"0".repeat(Number(-scale))}`;
  if (digitLength > scale) {
    const split = Number(digitLength - scale);
    return `${digits.slice(0, split)}.${digits.slice(split)}`;
  }
  return `0.${"0".repeat(Number(scale - digitLength))}${digits}`;
}

function normalizedBackendImportedPrice(value) {
  const text = normalizedPersistedDecimalText(value, "Precio importado");
  const normalized = text.replace(/_/g, "");
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(normalized);
  if (!match) throw new Error("Precio importado invalido");
  const integer = match[2] || "";
  const fraction = match[3] ?? match[4] ?? "";
  const digits = `${integer}${fraction}`.replace(/^0+/, "");
  const isZero = !digits;
  if (match[1] === "-" && !isZero) throw new Error("Precio importado invalido");
  const scale = BigInt(fraction.length) - BigInt(match[5] || "0");
  const coefficient = digits || "0";
  const sign = match[1] === "-" && isZero ? "-" : "";

  if (scale > 1000000n || scale < -1000000n) return normalized;
  if (scale <= 0n) return `${sign}${coefficient}${"0".repeat(Number(-scale))}`;
  if (BigInt(coefficient.length) > scale) {
    const split = Number(BigInt(coefficient.length) - scale);
    return `${sign}${coefficient.slice(0, split)}.${coefficient.slice(split)}`;
  }
  return `${sign}0.${"0".repeat(Number(scale - BigInt(coefficient.length)))}${coefficient}`;
}

function copyQuantityRules(quantityRules) {
  if (!quantityRules || typeof quantityRules !== "object" || Array.isArray(quantityRules)) {
    throw new Error("Reglas de cantidad requeridas");
  }
  if (quantityRules.max == null || String(quantityRules.max).trim() === "") {
    throw new Error("Maximo comercial requerido");
  }
  try {
    quantityMicrounits(quantityRules.max);
  } catch {
    throw new Error("Maximo comercial invalido");
  }
  const result = {
    min: quantityRules.min,
    step: quantityRules.step,
    maxDecimals: quantityRules.maxDecimals,
    max: quantityRules.max,
  };
  if (quantityRules.integer !== undefined) result.integer = quantityRules.integer === true;
  if (quantityRules.warningAt !== undefined) result.warningAt = quantityRules.warningAt;
  if (quantityRules.confirmOnInsufficient !== undefined) {
    result.confirmOnInsufficient = quantityRules.confirmOnInsufficient === true;
  }
  if (quantityRules.confirmOnMissingPrice !== undefined) {
    result.confirmOnMissingPrice = quantityRules.confirmOnMissingPrice === true;
  }
  return result;
}

function copySnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    throw new Error("Snapshot visual requerido");
  }
  if (snapshot.warnings != null && !Array.isArray(snapshot.warnings)) {
    throw new Error("Advertencias visuales invalidas");
  }
  return {
    name: String(snapshot.name || ""),
    code: String(snapshot.code || ""),
    image_url: String(snapshot.image_url || ""),
    unit: String(snapshot.unit || ""),
    availability: String(snapshot.availability || ""),
    configuration: Array.from(String(snapshot.configuration || "")).slice(0, 2000).join(""),
    warnings: [...(snapshot.warnings || [])].map((value) => String(value)),
  };
}

function normalizedImportedText(value, field, { allowEmpty = false, limit = 1000 } = {}) {
  const text = normalizedText(value, field, { allowEmpty, limit });
  if (text && new Set(["=", "+", "-", "@"]).has(text[0])) {
    throw new Error(`${field} invalido`);
  }
  return text;
}

function normalizedImportCurrency(value) {
  if (typeof value !== "string" || !IMPORTED_CURRENCIES.has(value.trim().toUpperCase())) {
    throw new Error("Moneda de origen requerida");
  }
  return value.trim().toUpperCase();
}

function normalizedImportedQuantity(value) {
  if (typeof value !== "string") throw new Error("Cantidad importada invalida");
  const units = quantityMicrounits(value);
  if (units > IMPORTED_MAX_QUANTITY) throw new Error("Cantidad importada invalida");
  return quantityFromMicrounits(units);
}

function normalizedImportedPrice(value) {
  if (typeof value !== "string") throw new Error("Precio importado invalido");
  const text = value.trim();
  if (!IMPORTED_PRICE_PATTERN.test(text)) throw new Error("Precio importado invalido");
  return text;
}

function importedKey(importId, sourceRow) {
  return `import:${importId}:${sourceRow}`;
}

function normalizedImportedIdentity(importId, sourceRow, key) {
  const normalizedImportId = normalizedImportedText(importId, "import_id", { limit: 100 });
  if (typeOfInteger(sourceRow) === false || sourceRow <= 0) {
    throw new Error("Fila importada invalida");
  }
  const expectedKey = importedKey(normalizedImportId, sourceRow);
  if (key !== expectedKey) throw new Error("Clave importada invalida");
  return { importId: normalizedImportId, sourceRow, key: expectedKey };
}

function typeOfInteger(value) {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function importedQuantityRules() {
  return {
    min: "0.000001",
    step: "0.000001",
    maxDecimals: 6,
    max: "1000000",
  };
}

function validatedImportedEdits(edits) {
  const record = identityRecord(edits);
  const fieldCount = Object.keys(record).length;
  if ((fieldCount !== IMPORTED_EDIT_FIELDS.size && fieldCount !== IMPORTED_EDIT_FIELDS.size - 1)
      || Object.keys(record).some((field) => !IMPORTED_EDIT_FIELDS.has(field))
      || !["name", "description", "dimension", "unitPrice", "provider"]
        .every((field) => hasOwn(record, field))) {
    throw new Error("Ediciones importadas invalidas");
  }
  const result = {
    name: normalizedImportedText(record.name, "Nombre", { limit: 1000 }),
    description: normalizedImportedText(record.description, "Descripcion", {
      allowEmpty: true,
      limit: IMPORTED_DESCRIPTION_LIMIT,
    }),
    dimension: normalizedImportedText(record.dimension, "Dimension", {
      allowEmpty: true,
      limit: 1000,
    }),
    unitPrice: normalizedImportedPrice(record.unitPrice),
    provider: normalizedImportedText(record.provider, "Proveedor", { limit: 1000 }),
  };
  if (hasOwn(record, "officialCode")) {
    result.officialCode = normalizedImportedText(record.officialCode, "Codigo oficial", {
      allowEmpty: true,
      limit: 500,
    });
  }
  return result;
}

function validatedImportedEditUpdates(updates) {
  const result = {};
  for (const [field, value] of Object.entries(updates)) {
    if (field === "officialCode") {
      result.officialCode = normalizedImportedText(value, "Codigo oficial", {
        allowEmpty: true,
        limit: 500,
      });
    } else if (field === "name") {
      result.name = normalizedImportedText(value, "Nombre", { limit: 1000 });
    } else if (field === "description") {
      result.description = normalizedImportedText(value, "Descripcion", {
        allowEmpty: true,
        limit: IMPORTED_DESCRIPTION_LIMIT,
      });
    } else if (field === "dimension") {
      result.dimension = normalizedImportedText(value, "Dimension", {
        allowEmpty: true,
        limit: 1000,
      });
    } else if (field === "unitPrice") {
      result.unitPrice = normalizedImportedPrice(value);
    } else if (field === "provider") {
      result.provider = normalizedImportedText(value, "Proveedor", { limit: 1000 });
    }
  }
  return result;
}

export function validateImportedCartEdits(edits) {
  return validatedImportedEdits(edits);
}

function copyImportedSnapshot(snapshot) {
  return copySnapshot({
    name: snapshot.name,
    code: snapshot.code || "",
    image_url: snapshot.image_url || "",
    unit: snapshot.unit || "PZA",
    availability: snapshot.availability || "",
    configuration: snapshot.configuration || "",
    warnings: snapshot.warnings || [],
  });
}

function createImportedCartLine({
  preview,
  item,
  sourceCurrency,
  provider,
  sectionId,
  position = 0,
}) {
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    throw new Error("Item importado invalido");
  }
  const identity = normalizedImportedIdentity(preview.import_id, item.source_row, item.key);
  const edits = validatedImportedEdits({
    officialCode: item.official_code ?? item.code ?? "",
    name: item.name,
    description: item.description || "",
    dimension: item.dimension || "",
    unitPrice: item.unit_price,
    provider,
  });
  const quantity = normalizedImportedQuantity(item.quantity);
  const snapshot = copyImportedSnapshot({
    name: edits.name,
    image_url: typeof item.image_url === "string" ? item.image_url : "",
    availability: `Importado de ${preview.original_filename || "Quotation"}`,
    configuration: edits.dimension,
  });
  return {
    kind: "imported",
    key: identity.key,
    lineId: createProjectLineId(),
    officialCode: edits.officialCode || normalizedOfficialCode(item.official_code ?? item.code, snapshot),
    provider: edits.provider,
    role: "principal",
    parentLineId: null,
    quantityMode: null,
    position: normalizedPosition(position),
    importId: identity.importId,
    sourceRow: identity.sourceRow,
    sourceCurrency: normalizedImportCurrency(sourceCurrency),
    imageAssetKey: normalizedText(item.image_asset_key || "", "Imagen", { allowEmpty: true, limit: 500 }),
    sourceAssetKey: normalizedText(item.source_asset_key || "", "Fuente", { allowEmpty: true, limit: 500 }),
    quantity,
    quantityRules: importedQuantityRules(),
    snapshot,
    sectionId: normalizedSectionId(sectionId),
    edits,
    editorRevision: 0,
  };
}

function copyImportedCartLine(line) {
  if (!line || typeof line !== "object" || Array.isArray(line) || line.kind !== "imported") {
    throw new Error("Linea importada invalida");
  }
  const identity = normalizedImportedIdentity(line.importId, line.sourceRow, line.key);
  const role = normalizedProjectRole(line.role ?? "principal");
  const parentLineId = line.parentLineId == null ? null : normalizedProjectLineId(line.parentLineId);
  if ((role === "principal" && parentLineId !== null) || (role === "complement" && parentLineId === null)) {
    throw new Error("Relacion de linea invalida");
  }
  const quantityMode = role === "complement"
    ? line.quantityMode
    : null;
  if (role === "complement" && !COMPLEMENT_QUANTITY_MODES.has(quantityMode)) {
    throw new Error("Complemento invalido");
  }
  const sectionId = role === "principal"
    ? normalizedSectionId(line.sectionId)
    : null;
  return {
    kind: "imported",
    key: identity.key,
    lineId: normalizedProjectLineId(line.lineId ?? createProjectLineId()),
    officialCode: normalizedOfficialCode(line.officialCode, line.snapshot),
    provider: normalizedProvider(line.provider ?? line.edits?.provider, ""),
    role,
    parentLineId,
    quantityMode,
    position: normalizedPosition(line.position ?? 0),
    importId: identity.importId,
    sourceRow: identity.sourceRow,
    sourceCurrency: normalizedImportCurrency(line.sourceCurrency),
    imageAssetKey: normalizedText(line.imageAssetKey || "", "Imagen", { allowEmpty: true, limit: 500 }),
    sourceAssetKey: normalizedText(line.sourceAssetKey || "", "Fuente", { allowEmpty: true, limit: 500 }),
    quantity: normalizedImportedQuantity(line.quantity),
    quantityRules: importedQuantityRules(),
    snapshot: copyImportedSnapshot(line.snapshot || {}),
    sectionId,
    edits: validatedImportedEdits(line.edits),
    editorRevision: Number.isSafeInteger(line.editorRevision) && line.editorRevision >= 0
      ? line.editorRevision
      : 0,
  };
}

function importedPresentationSections(previewSections, currentSections) {
  if (!Array.isArray(previewSections) || !previewSections.length) {
    throw new Error("Secciones importadas invalidas");
  }
  const current = validatedSections(currentSections);
  const replacesInitialSection = current.length === 1 && current[0].id === "section-1";
  const highestId = current.reduce((highest, section) => (
    Math.max(highest, Number(SECTION_ID_PATTERN.exec(section.id)?.[1] || 0))
  ), 0);
  if (current.length - Number(replacesInitialSection) + previewSections.length > MAX_MIXED_CART_SECTIONS) {
    throw new Error(`Limite de ${MAX_MIXED_CART_SECTIONS} secciones alcanzado`);
  }
  const seenKeys = new Set();
  const seenSourceIds = new Set();
  return previewSections.map((section, index) => {
    if (!section || typeof section !== "object" || Array.isArray(section)
        || typeof section.id !== "string" || seenSourceIds.has(section.id)
        || !Array.isArray(section.item_keys) || !section.item_keys.length) {
      throw new Error("Seccion importada invalida");
    }
    seenSourceIds.add(section.id);
    const itemKeys = section.item_keys.map((key) => {
      if (typeof key !== "string" || seenKeys.has(key)) throw new Error("Items importados invalidos");
      seenKeys.add(key);
      return key;
    });
    return {
      id: `section-${replacesInitialSection ? index + 1 : highestId + index + 1}`,
      concept: normalizedImportedText(section.title, "Concepto", { limit: 120 }),
      itemKeys,
    };
  });
}

function importedCurrentSections(currentSections) {
  if (Array.isArray(currentSections) && currentSections.length === 0) {
    return createInitialMixedCartSections();
  }
  return validatedSections(currentSections);
}

export function createImportedCartBundle(preview, sourceCurrency, provider, currentSections) {
  if (!preview || typeof preview !== "object" || Array.isArray(preview)
      || !Array.isArray(preview.items) || !preview.items.length) {
    throw new Error("Previsualizacion importada invalida");
  }
  const current = importedCurrentSections(currentSections);
  const importedSections = importedPresentationSections(preview.sections, current);
  const sectionByKey = new Map(importedSections.flatMap((section) => (
    section.itemKeys.map((key) => [key, section.id])
  )));
  if (sectionByKey.size !== preview.items.length) throw new Error("Cobertura de secciones importadas invalida");
  const selectedCurrency = sourceCurrency || preview.source_currency;
  const selectedProvider = provider || preview.provider;
  const nextPositionBySection = new Map();
  const lines = preview.items.map((item) => {
    if (!sectionByKey.has(item?.key)) throw new Error("Cobertura de secciones importadas invalida");
    const sectionId = sectionByKey.get(item.key);
    const position = nextPositionBySection.get(sectionId) || 0;
    nextPositionBySection.set(sectionId, position + 1);
    return createImportedCartLine({
      preview,
      item,
      sourceCurrency: item.source_currency || selectedCurrency,
      provider: item.provider || selectedProvider,
      sectionId,
      position,
    });
  });
  return {
    lines,
    sections: [
      ...(current.length === 1 && current[0].id === "section-1" ? [] : current),
      ...importedSections.map(({ itemKeys, ...section }) => section),
    ],
  };
}

const IMPORT_MANIFEST_FIELDS = Object.freeze([
  "schema_version", "import_id", "source_hash", "original_filename", "provider",
  "source_currency", "currency_status", "columns", "sections", "items",
]);
const IMPORT_ITEM_FIELDS = Object.freeze([
  "key", "source_row", "category", "name", "description", "dimension", "provider",
  "official_code", "quantity", "unit_price", "source_currency", "row_hash",
  "source_reference",
]);
const IMPORT_UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const IMPORT_HASH_PATTERN = /^[0-9a-f]{64}$/;

function hasExactFields(value, fields) {
  const record = identityRecord(value);
  const actual = Object.keys(record).sort(compareUnicodeCodePoints);
  const expected = [...fields].sort(compareUnicodeCodePoints);
  return actual.length === expected.length
    && actual.every((field, index) => field === expected[index]);
}

function canonicalJsonValue(value) {
  if (Array.isArray(value)) return value.map(canonicalJsonValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(identityRecord(value))
        .sort(compareUnicodeCodePoints)
        .map((key) => [key, canonicalJsonValue(value[key])]),
    );
  }
  return value;
}

function sameJsonValue(left, right) {
  return JSON.stringify(canonicalJsonValue(left)) === JSON.stringify(canonicalJsonValue(right));
}

function validatedPromotionContract(preview, promotion, expected) {
  if (!hasExactFields(promotion, ["source_asset_key", "image_asset_keys", "manifest"])
      || !hasExactFields(expected, ["userId", "projectId", "importId"])
      || !hasExactFields(preview, IMPORT_MANIFEST_FIELDS)
      || !hasExactFields(promotion.manifest, IMPORT_MANIFEST_FIELDS)
      || !Array.isArray(preview.items) || !preview.items.length
      || !Array.isArray(preview.sections) || !preview.sections.length) {
    throw new Error("Promocion importada invalida");
  }
  const userId = expected.userId;
  const projectId = expected.projectId;
  const importId = expected.importId;
  if (!Number.isSafeInteger(userId) || userId <= 0
      || typeof projectId !== "string" || !IMPORT_UUID_PATTERN.test(projectId)
      || typeof importId !== "string" || !IMPORT_UUID_PATTERN.test(importId)
      || preview.schema_version !== 1
      || preview.import_id !== importId
      || typeof preview.source_hash !== "string" || !IMPORT_HASH_PATTERN.test(preview.source_hash)
      || !["detected", "required"].includes(preview.currency_status)) {
    throw new Error("Promocion importada invalida");
  }
  const filename = normalizedText(preview.original_filename, "Archivo importado", {limit: 255});
  if (filename !== preview.original_filename
      || filename === "." || filename === ".."
      || filename.includes("/") || filename.includes("\\")
      || !filename.toLowerCase().endsWith(".xlsx")) {
    throw new Error("Promocion importada invalida");
  }
  normalizedImportedText(preview.provider, "Proveedor", {allowEmpty: true});
  if (preview.source_currency !== null) normalizedImportCurrency(preview.source_currency);
  const columns = identityRecord(preview.columns);
  for (const [name, column] of Object.entries(columns)) {
    normalizedText(name, "Columna", {limit: 100});
    normalizedText(column, "Columna", {limit: 20});
  }

  const canonicalItems = [];
  const expectedImageRows = new Set();
  const itemKeys = new Set();
  const sourceRows = new Set();
  for (const item of preview.items) {
    if (!hasExactFields(item, [...IMPORT_ITEM_FIELDS, "image_url"])
        || !Number.isSafeInteger(item.source_row) || item.source_row <= 7
        || typeof item.row_hash !== "string" || !IMPORT_HASH_PATTERN.test(item.row_hash)
        || item.key !== importedKey(importId, item.source_row)
        || item.source_reference !== `${filename}#Quotation!${item.source_row}`
        || itemKeys.has(item.key) || sourceRows.has(item.source_row)
        || typeof item.image_url !== "string") {
      throw new Error("Promocion importada invalida");
    }
    normalizedImportedText(item.category, "Categoria", {allowEmpty: true});
    normalizedImportedText(item.name, "Nombre");
    normalizedImportedText(item.description, "Descripcion", {
      allowEmpty: true,
      limit: IMPORTED_DESCRIPTION_LIMIT,
    });
    normalizedImportedText(item.dimension, "Dimension", {allowEmpty: true});
    normalizedImportedText(item.provider, "Proveedor", {allowEmpty: true});
    normalizedImportedText(item.official_code, "Codigo oficial", {allowEmpty: true, limit: 500});
    normalizedImportedQuantity(item.quantity);
    normalizedImportedPrice(item.unit_price);
    if (item.source_currency !== null) normalizedImportCurrency(item.source_currency);
    itemKeys.add(item.key);
    sourceRows.add(item.source_row);
    if (item.image_url) expectedImageRows.add(String(item.source_row));
    const {image_url: _transientImage, ...canonicalItem} = item;
    canonicalItems.push(canonicalItem);
  }

  const coveredKeys = new Set();
  preview.sections.forEach((section, index) => {
    if (!hasExactFields(section, ["id", "title", "item_keys"])
        || section.id !== `import-section-${index + 1}`
        || !Array.isArray(section.item_keys) || !section.item_keys.length) {
      throw new Error("Promocion importada invalida");
    }
    normalizedImportedText(section.title, "Seccion", {allowEmpty: true});
    section.item_keys.forEach((key) => {
      if (!itemKeys.has(key) || coveredKeys.has(key)) throw new Error("Promocion importada invalida");
      coveredKeys.add(key);
    });
  });
  if (coveredKeys.size !== itemKeys.size) throw new Error("Promocion importada invalida");

  const canonicalManifest = {...preview, items: canonicalItems};
  if (!sameJsonValue(canonicalManifest, promotion.manifest)) {
    throw new Error("La promocion no corresponde a la importacion");
  }
  const prefix = `projects/${userId}/${projectId}`;
  const sourceAssetKey = `${prefix}/sources/${preview.source_hash}.xlsx`;
  if (promotion.source_asset_key !== sourceAssetKey) {
    throw new Error("Fuente durable invalida");
  }
  const imageAssetKeys = identityRecord(promotion.image_asset_keys);
  const actualImageRows = Object.keys(imageAssetKeys);
  if (actualImageRows.length !== expectedImageRows.size
      || actualImageRows.some((row) => !expectedImageRows.has(row))) {
    const missingRow = [...expectedImageRows].find((row) => !hasOwn(imageAssetKeys, row));
    if (missingRow) throw new Error(`Falta imagen durable para la fila importada ${missingRow}`);
    throw new Error("Mapa de imagenes durables invalido");
  }
  for (const row of actualImageRows) {
    const expectedKey = `${prefix}/images/${preview.source_hash.slice(0, 16)}-row-${row}.png`;
    if (imageAssetKeys[row] !== expectedKey) throw new Error("Imagen durable invalida");
  }
  return {sourceAssetKey, imageAssetKeys};
}

export function withDurableImportedAssets(preview, promotion, expected) {
  const {sourceAssetKey, imageAssetKeys} = validatedPromotionContract(
    preview,
    promotion,
    expected,
  );
  return {
    ...preview,
    source_asset_key: sourceAssetKey,
    items: preview.items.map((item) => ({
      ...item,
      image_url: "",
      image_asset_key: imageAssetKeys[String(item.source_row)] || "",
      source_asset_key: sourceAssetKey,
    })),
  };
}

export function updateImportedCartLine(lines, key, edits) {
  if (!Array.isArray(lines)) throw new Error("Lineas de carrito invalidas");
  const index = lines.findIndex((line) => line.key === key);
  if (index < 0 || lines[index]?.kind !== "imported") throw new Error("Linea importada no encontrada");
  const updates = identityRecord(edits);
  if (Object.keys(updates).some((field) => !IMPORTED_EDIT_FIELDS.has(field))) {
    throw new Error("Ediciones importadas invalidas");
  }
  const persistedLine = lines[index].projectPersistedImported === true;
  const line = persistedLine ? lines[index] : copyImportedCartLine(lines[index]);
  const nextEdits = persistedLine
    ? { ...line.edits, ...validatedImportedEditUpdates(updates) }
    : validatedImportedEdits({ ...line.edits, ...updates });
  return lines.map((current, position) => (
    position === index ? {
      ...line,
      officialCode: hasOwn(nextEdits, "officialCode")
        ? nextEdits.officialCode
        : line.officialCode,
      provider: nextEdits.provider,
      edits: nextEdits,
    } : current
  ));
}

export function replaceImportedCartBundle(lines, sections, bundle) {
  if (!Array.isArray(lines) || !bundle || typeof bundle !== "object" || Array.isArray(bundle)
      || !Array.isArray(bundle.lines) || !Array.isArray(bundle.sections)) {
    throw new Error("Bundle importado invalido");
  }
  const currentSections = importedCurrentSections(sections);
  let incomingSections = validatedSections(bundle.sections);
  let nextImportedLines = bundle.lines.map(copyImportedCartLine);
  const previousImportedSectionIds = new Set(
    lines.filter((line) => line?.kind === "imported").map((line) => line.sectionId),
  );
  const catalogLines = lines.filter((line) => line?.kind !== "imported");
  const catalogSectionIds = new Set(catalogLines.map((line) => line?.sectionId));
  if (currentSections.length === 1 && catalogSectionIds.has("section-1")
      && incomingSections[0]?.id === "section-1"
      && nextImportedLines.some((line) => line.sectionId === "section-1")) {
    const sectionIds = new Map(incomingSections.map((section, index) => [
      section.id,
      `section-${index + 2}`,
    ]));
    incomingSections = incomingSections.map((section) => ({
      ...section,
      id: sectionIds.get(section.id),
    }));
    nextImportedLines = nextImportedLines.map((line) => ({
      ...line,
      sectionId: sectionIds.get(line.sectionId),
    }));
  }
  const finalLines = [...catalogLines, ...nextImportedLines];
  const nextSectionIds = new Set(nextImportedLines.map((line) => line.sectionId));
  const incomingById = new Map(incomingSections.map((section) => [section.id, section]));
  const resultSections = [];
  const seenSectionIds = new Set();
  const addSection = (section) => {
    if (!seenSectionIds.has(section.id)) {
      seenSectionIds.add(section.id);
      resultSections.push({ ...section });
    }
  };
  for (const section of currentSections) {
    const removedPreviousImport = previousImportedSectionIds.has(section.id)
      && !catalogSectionIds.has(section.id)
      && !nextSectionIds.has(section.id);
    if (!removedPreviousImport) addSection(incomingById.get(section.id) || section);
  }
  for (const section of incomingSections) addSection(section);
  if (resultSections.length > MAX_MIXED_CART_SECTIONS
      || finalLines.some((line) => !seenSectionIds.has(line?.sectionId))) {
    throw new Error("Secciones importadas invalidas");
  }
  return { lines: finalLines, sections: resultSections };
}

export function createProjectLineId() {
  return globalThis.crypto.randomUUID();
}

function normalizeProjectMatchPart(value) {
  return String(value || "").normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .trim()
    .toLocaleUpperCase()
    .replace(/\s+/gu, " ");
}

export function projectMatchKey(provider, officialCode) {
  const cleanProvider = normalizeProjectMatchPart(provider);
  const cleanCode = normalizeProjectMatchPart(officialCode);
  return cleanProvider && cleanCode ? `${cleanProvider}\u0000${cleanCode}` : "";
}

function projectImportedMatchKey({kind, importId, provider, officialCode, importedName}) {
  if (kind !== "imported" || projectMatchKey(provider, officialCode)) return "";
  const cleanImportId = normalizeProjectMatchPart(importId);
  const cleanProvider = normalizeProjectMatchPart(provider);
  const cleanName = normalizeProjectMatchPart(importedName);
  return cleanImportId && cleanProvider && cleanName
    ? `${cleanImportId}\u0000${cleanProvider}\u0000${cleanName}`
    : "";
}

export function projectLineSelector(line) {
  return {
    kind: line?.kind,
    provider: line?.provider || line?.catalog,
    officialCode: line?.officialCode,
    importId: line?.kind === "imported" ? line?.importId : "",
    importedName: line?.kind === "imported" ? line?.snapshot?.name : "",
  };
}

export function projectLineHasMatchIdentity(line) {
  const selector = projectLineSelector(line);
  return Boolean(
    projectMatchKey(selector.provider, selector.officialCode)
    || projectImportedMatchKey(selector),
  );
}

export function projectLineMatches(line, selector) {
  const selectorPrimaryKey = projectMatchKey(selector.provider, selector.officialCode);
  if (selectorPrimaryKey) {
    return projectMatchKey(line.provider || line.catalog, line.officialCode)
      === selectorPrimaryKey;
  }
  const selectorImportedKey = projectImportedMatchKey(selector);
  return selectorImportedKey !== ""
    && projectImportedMatchKey({
      kind: line?.kind,
      importId: line?.importId,
      provider: line?.provider || line?.catalog,
      officialCode: line?.officialCode,
      importedName: line?.snapshot?.name,
    }) === selectorImportedKey;
}

export function projectComplements(lines, parentLineId) {
  return lines.filter((line) => line.role === "complement" && line.parentLineId === parentLineId)
    .sort((left, right) => left.position - right.position);
}

export function createMixedCartLine({
  catalog,
  identity,
  quantity,
  quantityRules,
  snapshot,
  sectionId = "section-1",
  lineId = createProjectLineId(),
  officialCode,
  provider,
  role = "principal",
  parentLineId = null,
  quantityMode = null,
  position = 0,
}) {
  if (!MIXED_CATALOGS.includes(catalog)) throw new Error("Catalogo mixto no soportado");
  const copiedIdentity = normalizedIdentity(catalog, identity);
  const copiedRules = copyQuantityRules(quantityRules);
  const copiedSnapshot = copySnapshot(snapshot);
  const copiedRole = normalizedProjectRole(role);
  const copiedParentLineId = parentLineId == null ? null : normalizedProjectLineId(parentLineId);
  if ((copiedRole === "principal" && copiedParentLineId !== null)
      || (copiedRole === "complement" && copiedParentLineId === null)) {
    throw new Error("Relacion de linea invalida");
  }
  if (copiedRole === "complement" && !COMPLEMENT_QUANTITY_MODES.has(quantityMode)) {
    throw new Error("Complemento invalido");
  }
  const line = {
    key: normalizedProjectLineId(lineId),
    lineId: normalizedProjectLineId(lineId),
    catalog,
    identity: copiedIdentity,
    officialCode: normalizedOfficialCode(officialCode, copiedSnapshot),
    provider: normalizedProvider(provider, catalog),
    role: copiedRole,
    parentLineId: copiedParentLineId,
    quantityMode: copiedRole === "complement" ? quantityMode : null,
    position: normalizedPosition(position),
    quantity: quantityFromMicrounits(quantityMicrounits(quantity)),
    quantityRules: copiedRules,
    snapshot: copiedSnapshot,
    sectionId: copiedRole === "principal" ? normalizedSectionId(sectionId) : null,
  };
  return { ...line, quantity: validateLineQuantity(line, line.quantity) };
}

export function validateLineQuantity(line, quantity) {
  if (line?.projectQuantityFallback === true || line?.projectPersistedImported === true) {
    return normalizedBackendProjectQuantity(quantity);
  }
  const text = typeof quantity === "string" ? quantity.trim() : "";
  const units = quantityMicrounits(quantity);
  const maxDecimals = line?.quantityRules?.maxDecimals;
  if (typeof maxDecimals !== "number"
      || !Number.isInteger(maxDecimals)
      || maxDecimals < 0
      || maxDecimals > 6) {
    throw new Error("Precision de cantidad invalida");
  }
  const decimals = (text.split(".")[1] || "").length;
  if (decimals > maxDecimals) throw new Error(`Cantidad excede ${maxDecimals} decimales`);
  if (line.quantityRules.integer && units % QUANTITY_SCALE !== 0n) {
    throw new Error("Cantidad entera requerida");
  }
  const minimum = quantityMicrounits(line.quantityRules.min);
  const step = quantityMicrounits(line.quantityRules.step);
  if (units < minimum) throw new Error("Cantidad menor al minimo");
  if ((units - minimum) % step !== 0n) throw new Error("Incremento de cantidad invalido");
  const maximum = quantityMicrounits(line.quantityRules.max);
  if (units > maximum) throw new Error("Cantidad mayor al maximo permitido");
  return quantityFromMicrounits(units);
}

export function lineNeedsAvailabilityConfirmation(line) {
  if (!line.quantityRules.confirmOnInsufficient || line.quantityRules.warningAt == null) return false;
  const warningAt = String(line.quantityRules.warningAt).trim();
  if (/^0(?:\.0{1,6})?$/.test(warningAt)) return true;
  return quantityMicrounits(line.quantity) > quantityMicrounits(warningAt);
}

export function lineNeedsPriceConfirmation(line) {
  return line.quantityRules.confirmOnMissingPrice === true;
}

export function upsertMixedCartLine(lines, incoming) {
  if (!Array.isArray(lines)) throw new Error("Lineas de carrito invalidas");
  const requestedId = incoming?.lineId;
  const lineId = lines.some((line) => line.lineId === requestedId)
    ? createProjectLineId()
    : requestedId;
  const copiedIncoming = createMixedCartLine({
    ...incoming,
    lineId,
    position: incoming?.role === "complement"
      ? incoming?.position
      : lines.filter((line) => (
        line.role === "principal" && line.sectionId === (incoming?.sectionId || "section-1")
      )).length,
  });
  return [...lines, copiedIncoming];
}

export function updateMixedCartQuantity(lines, key, quantity) {
  if (!lines.some((line) => line.key === key)) throw new Error("Linea de carrito no encontrada");
  return lines.map((line) => {
    if (line.key !== key) return line;
    if (line.projectQuantityFallback === true && line.projectQuantityRulesCache === true) {
      const { projectQuantityFallback, projectQuantityRulesCache, ...interactiveLine } = line;
      return { ...interactiveLine, quantity: validateLineQuantity(interactiveLine, quantity) };
    }
    if (line.projectPersistedImported === true) {
      const { projectPersistedImported, ...interactiveLine } = line;
      const interactiveImportedLine = { ...interactiveLine, quantityRules: importedQuantityRules() };
      return {
        ...interactiveImportedLine,
        quantity: validateLineQuantity(interactiveImportedLine, quantity),
      };
    }
    return { ...line, quantity: validateLineQuantity(line, quantity) };
  });
}

export function removeMixedCartLine(lines, key) {
  return lines.filter((line) => line.key !== key);
}

export function removeProjectLineTree(lines, lineId) {
  const current = lines.find((line) => line.lineId === lineId);
  if (!current) throw new Error("Producto del Proyecto no encontrado");
  const removed = current.role === "principal"
    ? new Set([lineId, ...projectComplements(lines, lineId).map((line) => line.lineId)])
    : new Set([lineId]);
  return lines.filter((line) => !removed.has(line.lineId));
}

export function addProjectComplement(lines, parentLineId, target, quantityMode) {
  if (!Array.isArray(lines)) throw new Error("Lineas de carrito invalidas");
  const parent = lines.find((line) => line.lineId === parentLineId);
  if (!parent) throw new Error("Producto del Proyecto no encontrado");
  if (parent.role !== "principal") throw new Error("Un complemento no puede tener complementos");
  if (!COMPLEMENT_QUANTITY_MODES.has(quantityMode)) throw new Error("Complemento invalido");
  const children = projectComplements(lines, parentLineId);
  const requestedId = target?.lineId;
  const lineId = lines.some((line) => line.lineId === requestedId)
    ? createProjectLineId()
    : requestedId;
  const complement = createMixedCartLine({
    ...target,
    lineId,
    sectionId: null,
    role: "complement",
    parentLineId,
    quantityMode,
    position: children.length,
  });
  return [...lines, complement];
}

export function replaceProjectLine(lines, lineId, target) {
  const current = lines.find((line) => line.lineId === lineId);
  if (!current) throw new Error("Producto del Proyecto no encontrado");
  const children = current.role === "principal"
    ? projectComplements(lines, lineId).map((line) => line.lineId)
    : [];
  const preservesPersistedQuantity = current.projectQuantityFallback === true
    || current.projectPersistedImported === true;
  const replacement = createMixedCartLine({
    ...target,
    lineId: current.lineId,
    quantity: preservesPersistedQuantity ? target.quantity : current.quantity,
    sectionId: current.sectionId || "section-1",
    role: current.role,
    parentLineId: current.parentLineId || null,
    quantityMode: current.quantityMode || null,
    position: current.position,
  });
  const replacementWithQuantity = preservesPersistedQuantity
    ? {
      ...replacement,
      quantity: current.quantity,
      projectQuantityFallback: true,
      projectQuantityRulesCache: true,
    }
    : replacement;
  const kept = lines.filter((line) => !children.includes(line.lineId));
  return {
    lines: kept.map((line) => line.lineId === lineId ? replacementWithQuantity : line),
    removedComplementIds: children,
  };
}

export function replaceAllProjectLines(lines, selector, target) {
  const matched = lines.filter((line) => projectLineMatches(line, selector));
  const ids = matched.map((line) => line.lineId);
  const parentById = new Map(lines.map((line) => [line.lineId, line]));
  const sectionIds = new Set(matched.map((line) => (
    line.sectionId
    || parentById.get(line.parentLineId)?.sectionId
  )).filter(Boolean));
  let result = [...lines];
  const removed = [];
  for (const lineId of ids) {
    if (!result.some((line) => line.lineId === lineId)) continue;
    const next = replaceProjectLine(result, lineId, target);
    result = next.lines;
    removed.push(...next.removedComplementIds);
  }
  return {
    lines: result,
    summary: {
      affected: ids.length,
      catalog: matched.filter((line) => line.kind !== "imported").length,
      imported: matched.filter((line) => line.kind === "imported").length,
      sections: sectionIds.size,
      removedComplements: removed.length,
      excludedUnlinked: lines.filter((line) => !projectLineHasMatchIdentity(line)).length,
    },
  };
}

export function toMixedQuoteItem(line) {
  if (line?.kind === "imported") {
    const imported = copyImportedCartLine(line);
    return {
      kind: "imported",
      import_id: imported.importId,
      source_row: imported.sourceRow,
      source_currency: imported.sourceCurrency,
      quantity: imported.quantity,
      overrides: {
        name: imported.edits.name,
        description: imported.edits.description,
        dimension: imported.edits.dimension,
        unit_price: imported.edits.unitPrice,
        provider: imported.edits.provider,
      },
    };
  }
  if (line.catalog === "tarkett") {
    return { catalog: "tarkett", code: line.identity.code, quantity: line.quantity };
  }
  if (line.catalog === "offiho") {
    return {
      catalog: "offiho",
      inventory_key: line.identity.inventory_key,
      quantity: line.quantity,
    };
  }
  if (!SUPPLIER_CATALOGS.has(line.catalog)) throw new Error("Catalogo mixto no soportado");
  const result = {
    catalog: line.catalog,
    internal_id: line.identity.internal_id,
    quantity: line.quantity,
    add_on_option_ids: [...(line.identity.add_on_option_ids || [])].sort(compareUnicodeCodePoints),
  };
  if (line.identity.base_option_id) result.base_option_id = line.identity.base_option_id;
  return result;
}

function exactProjectKeys(value, keys, message) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).length !== keys.size
      || Object.keys(value).some((key) => !keys.has(key))) {
    throw new Error(message);
  }
}

function projectQuoteFields(quoteFields) {
  if (!quoteFields || typeof quoteFields !== "object" || Array.isArray(quoteFields)
      || Object.keys(quoteFields).some((key) => !PROJECT_QUOTE_FIELDS.has(key))
      || [...PROJECT_QUOTE_REQUIRED_FIELDS].some((field) => !hasOwn(quoteFields, field))) {
    throw new Error("Datos de cotizacion invalidos");
  }
  const result = {};
  for (const field of PROJECT_QUOTE_REQUIRED_FIELDS) {
    result[field] = normalizedText(quoteFields[field], field, { allowEmpty: true, limit: 500 });
  }
  const currency = result.quote_currency.toUpperCase();
  if (!IMPORTED_CURRENCIES.has(currency)) throw new Error("Moneda de cotizacion invalida");
  if (!/^(?:0|[1-9]\d{0,2})(?:\.\d+)?$/.test(result.descuento)
      || Number(result.descuento) > 100) {
    throw new Error("Descuento invalido");
  }
  const template = normalizedText(
    quoteFields.template ?? "official_2026_gdl",
    "template",
    {limit: 64},
  );
  if (!PROJECT_TEMPLATES.has(template)) throw new Error("Plantilla de cotizacion invalida");
  const descriptionLanguage = normalizedText(
    quoteFields.description_language ?? "es",
    "description_language",
    {limit: 2},
  ).toLowerCase();
  if (!PROJECT_DESCRIPTION_LANGUAGES.has(descriptionLanguage)) {
    throw new Error("Idioma de descripciones invalido");
  }
  return {
    ...result,
    quote_currency: currency,
    template,
    description_language: descriptionLanguage,
  };
}

function projectDisplayCache(snapshot) {
  const copied = copySnapshot(snapshot);
  return {
    name: copied.name,
    code: copied.code,
    image_url: copied.image_url,
    configuration: copied.configuration,
  };
}

function hydrateProjectDisplayCache(displayCache) {
  const required = new Set(["name", "code", "image_url"]);
  const allowed = new Set([...required, "configuration"]);
  if (!displayCache || typeof displayCache !== "object" || Array.isArray(displayCache)
      || [...required].some((key) => !hasOwn(displayCache, key))
      || Object.keys(displayCache).some((key) => !allowed.has(key))) {
    throw new Error("display_cache invalido");
  }
  return {
    name: displayCache.name,
    code: displayCache.code,
    image_url: displayCache.image_url,
    unit: "",
    availability: "",
    configuration: displayCache.configuration || "",
    warnings: [],
  };
}

function hydrateProjectQuantityRules(quantityRules) {
  const required = new Set(["min", "step", "maxDecimals", "max"]);
  const allowed = new Set([
    ...required,
    "integer",
    "warningAt",
    "confirmOnInsufficient",
    "confirmOnMissingPrice",
  ]);
  if (!quantityRules || typeof quantityRules !== "object" || Array.isArray(quantityRules)
      || Object.keys(quantityRules).some((key) => !allowed.has(key))
      || [...required].some((key) => !hasOwn(quantityRules, key))) {
    throw new Error("Reglas de cantidad invalidas");
  }
  return copyQuantityRules(quantityRules);
}

function defaultProjectQuantityRules() {
  return {};
}

function hydrateProjectCatalogIdentity(catalog, identity) {
  const fields = catalog === "tarkett"
    ? new Set(["code"])
    : catalog === "offiho"
      ? new Set(["inventory_key"])
      : new Set(["internal_id", "base_option_id", "add_on_option_ids"]);
  exactProjectKeys(identity, fields, "Identidad de catalogo invalida");
  return identity;
}

function hydratePersistedCatalogLine(line, quantityRules, hasQuantityRulesCache) {
  if (!MIXED_CATALOGS.includes(line.catalog)) throw new Error("Catalogo mixto no soportado");
  const role = normalizedProjectRole(line.role);
  const parentLineId = line.parent_line_id == null ? null : normalizedProjectLineId(line.parent_line_id);
  if ((role === "principal" && parentLineId !== null)
      || (role === "complement" && parentLineId === null)) {
    throw new Error("Relacion de linea invalida");
  }
  const quantityMode = role === "complement" ? line.quantity_mode : null;
  if (role === "complement" && !COMPLEMENT_QUANTITY_MODES.has(quantityMode)) {
    throw new Error("Complemento invalido");
  }
  const snapshot = copySnapshot(hydrateProjectDisplayCache(line.display_cache));
  return {
    key: normalizedProjectLineId(line.line_id),
    lineId: normalizedProjectLineId(line.line_id),
    catalog: line.catalog,
    identity: normalizedIdentity(line.catalog, hydrateProjectCatalogIdentity(line.catalog, line.identity)),
    officialCode: normalizedOfficialCode(line.official_code, snapshot),
    provider: normalizedProvider(undefined, line.catalog),
    role,
    parentLineId,
    quantityMode,
    position: normalizedPosition(line.position),
    quantity: normalizedBackendProjectQuantity(line.quantity),
    quantityRules,
    projectQuantityFallback: true,
    projectQuantityRulesCache: hasQuantityRulesCache,
    snapshot,
    sectionId: role === "principal" ? line.section_id : null,
  };
}

function hydratePersistedImportedLine(line) {
  const role = normalizedProjectRole(line.role);
  const parentLineId = line.parent_line_id == null ? null : normalizedProjectLineId(line.parent_line_id);
  if ((role === "principal" && parentLineId !== null)
      || (role === "complement" && parentLineId === null)) {
    throw new Error("Relacion de linea invalida");
  }
  const quantityMode = role === "complement" ? line.quantity_mode : null;
  if (role === "complement" && !COMPLEMENT_QUANTITY_MODES.has(quantityMode)) {
    throw new Error("Complemento invalido");
  }
  const identity = normalizedImportedIdentity(
    line.import_id,
    line.source_row,
    importedKey(line.import_id, line.source_row),
  );
  const snapshot = copyImportedSnapshot(hydrateProjectDisplayCache(line.display_cache));
  const provider = normalizedImportedText(line.provider, "Proveedor", { limit: 500 });
  return {
    kind: "imported",
    key: identity.key,
    lineId: normalizedProjectLineId(line.line_id),
    officialCode: normalizedImportedText(line.official_code, "Codigo oficial", {
      allowEmpty: true,
      limit: 500,
    }),
    provider,
    role,
    parentLineId,
    quantityMode,
    position: normalizedPosition(line.position),
    importId: identity.importId,
    sourceRow: identity.sourceRow,
    sourceCurrency: normalizedImportCurrency(line.source_currency),
    imageAssetKey: normalizedText(line.image_asset_key || "", "Imagen", { allowEmpty: true, limit: 500 }),
    sourceAssetKey: normalizedText(line.source_asset_key || "", "Fuente", { allowEmpty: true, limit: 500 }),
    quantity: normalizedBackendProjectQuantity(line.quantity),
    quantityRules: {},
    projectPersistedImported: true,
    snapshot,
    sectionId: role === "principal" ? line.section_id : null,
    edits: {
      officialCode: normalizedImportedText(line.official_code, "Codigo oficial", {
        allowEmpty: true,
        limit: 500,
      }),
      name: normalizedImportedText(line.name, "Nombre", { limit: 500 }),
      description: normalizedImportedText(line.description, "Descripcion", {
        allowEmpty: true,
        limit: IMPORTED_DESCRIPTION_LIMIT,
      }),
      dimension: normalizedImportedText(line.dimension, "Dimension", {
        allowEmpty: true,
        limit: 500,
      }),
      unitPrice: normalizedBackendImportedPrice(line.unit_price),
      provider,
    },
    editorRevision: 0,
  };
}

function projectLineRelationship(line, sectionIds) {
  const role = normalizedProjectRole(line.role);
  const lineId = normalizedProjectLineId(line.lineId);
  const position = normalizedPosition(line.position);
  if (role === "principal") {
    if (line.parentLineId !== null || !sectionIds.has(line.sectionId)) {
      throw new Error("Principal fuera de seccion");
    }
    return { lineId, role, position, sectionId: line.sectionId, parentLineId: null, quantityMode: null };
  }
  if (line.sectionId !== null || !COMPLEMENT_QUANTITY_MODES.has(line.quantityMode)) {
    throw new Error("Complemento invalido");
  }
  return {
    lineId,
    role,
    position,
    sectionId: null,
    parentLineId: normalizedProjectLineId(line.parentLineId),
    quantityMode: line.quantityMode,
  };
}

function validateProjectLineGraph(lines, sectionIds) {
  const lineIds = new Set();
  const positions = new Map();
  for (const line of lines) {
    const relation = projectLineRelationship(line, sectionIds);
    if (lineIds.has(relation.lineId)) throw new Error("lineId duplicado");
    lineIds.add(relation.lineId);
    const group = relation.role === "principal" ? relation.sectionId : relation.parentLineId;
    const groupPositions = positions.get(group) || new Set();
    if (groupPositions.has(relation.position)) throw new Error("Posicion de linea duplicada");
    groupPositions.add(relation.position);
    positions.set(group, groupPositions);
  }
  for (const line of lines) {
    if (line.role === "complement") {
      const parent = lines.find((candidate) => candidate.lineId === line.parentLineId);
      if (!parent || parent.role !== "principal") throw new Error("Padre de complemento invalido");
    }
  }
  for (const groupPositions of positions.values()) {
    if ([...groupPositions].sort((left, right) => left - right)
      .some((position, index) => position !== index)) {
      throw new Error("Posicion de linea invalida");
    }
  }
}

function serializeProjectLine(line, sectionIds) {
  const relationship = projectLineRelationship(line, sectionIds);
  const displayCache = projectDisplayCache(line.snapshot);
  if (line.kind === "imported" && line.imageAssetKey) displayCache.image_url = "";
  const common = {
    line_id: relationship.lineId,
    role: relationship.role,
    section_id: relationship.sectionId,
    parent_line_id: relationship.parentLineId,
    position: relationship.position,
    quantity: validateLineQuantity(line, line.quantity),
    source: line.kind === "imported" ? "imported" : "catalog",
    official_code: normalizedOfficialCode(line.edits?.officialCode || line.officialCode, line.snapshot),
    display_cache: displayCache,
  };
  if (!common.official_code && common.source !== "imported") {
    throw new Error("Codigo oficial requerido");
  }
  if (relationship.role === "complement") common.quantity_mode = relationship.quantityMode;
  if (common.source === "catalog") {
    if (line.projectQuantityFallback === true) {
      const result = {
        ...common,
        catalog: line.catalog,
        identity: normalizedIdentity(line.catalog, line.identity),
      };
      if (line.projectQuantityRulesCache === true) {
        result.quantity_rules_cache = hydrateProjectQuantityRules(line.quantityRules);
      }
      return result;
    }
    const copied = createMixedCartLine(line);
    return {
      ...common,
      catalog: copied.catalog,
      identity: copied.identity,
      quantity_rules_cache: copied.quantityRules,
    };
  }
  if (line.projectPersistedImported === true) {
    return {
      ...common,
      import_id: line.importId,
      source_row: line.sourceRow,
      source_currency: line.sourceCurrency,
      provider: line.edits.provider,
      name: line.edits.name,
      description: line.edits.description,
      dimension: line.edits.dimension,
      unit_price: normalizedBackendImportedPrice(line.edits.unitPrice),
      image_asset_key: line.imageAssetKey,
      source_asset_key: line.sourceAssetKey,
    };
  }
  const imported = copyImportedCartLine(line);
  return {
    ...common,
    import_id: imported.importId,
    source_row: imported.sourceRow,
    source_currency: imported.sourceCurrency,
    provider: imported.edits.provider,
    name: imported.edits.name,
    description: imported.edits.description,
    dimension: imported.edits.dimension,
    unit_price: imported.edits.unitPrice,
    image_asset_key: imported.imageAssetKey,
    source_asset_key: imported.sourceAssetKey,
  };
}

export function serializeProject({ quoteFields, sections, lines }) {
  const currentSections = validatedSections(sections);
  if (!Array.isArray(lines)) throw new Error("Lineas de Proyecto invalidas");
  const sectionIds = new Set(currentSections.map((section) => section.id));
  validateProjectLineGraph(lines, sectionIds);
  return {
    schema_version: PROJECT_SCHEMA_VERSION,
    quote_fields: projectQuoteFields(quoteFields),
    sections: currentSections.map((section, position) => ({
      section_id: section.id,
      concept: section.concept || defaultSectionConcept(position),
      position,
    })),
    lines: lines.map((line) => serializeProjectLine(line, sectionIds)),
  };
}

function hydrateProjectSection(section) {
  exactProjectKeys(section, new Set(["section_id", "concept", "position"]), "Seccion de Proyecto invalida");
  return {
    id: normalizedSectionId(section.section_id),
    concept: normalizedText(section.concept, "Concepto", { limit: 120 }),
    position: normalizedPosition(section.position),
  };
}

function hydrateCatalogProjectLine(line) {
  const commonKeys = new Set([
    "line_id", "role", "section_id", "parent_line_id", "position", "quantity",
    "source", "official_code", "display_cache", "catalog", "identity",
  ]);
  if (line?.role === "complement") commonKeys.add("quantity_mode");
  if (!line || typeof line !== "object" || Array.isArray(line)
      || [...commonKeys].some((key) => !hasOwn(line, key))
      || Object.keys(line).some((key) => key !== "quantity_rules_cache" && !commonKeys.has(key))) {
    throw new Error("Linea de Proyecto invalida");
  }
  if (line.source !== "catalog") throw new Error("Origen de linea invalido");
  const hasQuantityRulesCache = hasOwn(line, "quantity_rules_cache");
  return hydratePersistedCatalogLine(
    line,
    hasQuantityRulesCache
      ? hydrateProjectQuantityRules(line.quantity_rules_cache)
      : defaultProjectQuantityRules(),
    hasQuantityRulesCache,
  );
}

function hydrateImportedProjectLine(line) {
  const allowed = new Set([
    "line_id", "role", "section_id", "parent_line_id", "position", "quantity",
    "source", "official_code", "display_cache", "import_id", "source_row",
    "source_currency", "provider", "name", "description", "dimension", "unit_price",
    "image_asset_key", "source_asset_key",
  ]);
  if (line?.role === "complement") allowed.add("quantity_mode");
  exactProjectKeys(line, allowed, "Linea de Proyecto invalida");
  if (line.source !== "imported") throw new Error("Origen de linea invalido");
  return hydratePersistedImportedLine(line);
}

export function hydrateProject(payload) {
  exactProjectKeys(payload, new Set(["schema_version", "quote_fields", "sections", "lines"]), "Proyecto invalido");
  if (payload.schema_version !== PROJECT_SCHEMA_VERSION) throw new Error("Version de Proyecto no soportada");
  if (!Array.isArray(payload.sections) || !Array.isArray(payload.lines)) {
    throw new Error("Proyecto invalido");
  }
  const rawSections = payload.sections.map(hydrateProjectSection)
    .sort((left, right) => left.position - right.position);
  if (rawSections.some((section, position) => section.position !== position)) {
    throw new Error("Orden de secciones invalido");
  }
  const sections = validatedSections(rawSections.map(({ id, concept }) => ({ id, concept })));
  const lines = payload.lines.map((line) => (
    line?.source === "catalog" ? hydrateCatalogProjectLine(line) : hydrateImportedProjectLine(line)
  ));
  validateProjectLineGraph(lines, new Set(sections.map((section) => section.id)));
  return { quoteFields: projectQuoteFields(payload.quote_fields), sections, lines };
}
