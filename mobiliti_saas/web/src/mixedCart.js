export const MIXED_CATALOGS = Object.freeze([
  "tarkett",
  "offiho",
  "cr-global",
  "sonara",
  "sunon",
  "alma",
  "lumbro",
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
const IMPORTED_EDIT_FIELDS = new Set(["name", "description", "dimension", "unitPrice", "provider"]);
const IMPORTED_PRICE_PATTERN = /^(?:0|[1-9]\d{0,9})(?:\.\d{1,6})?$/;
const IMPORTED_MAX_QUANTITY = 1000000n * QUANTITY_SCALE;

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
  if (Object.keys(record).length !== IMPORTED_EDIT_FIELDS.size
      || Object.keys(record).some((field) => !IMPORTED_EDIT_FIELDS.has(field))) {
    throw new Error("Ediciones importadas invalidas");
  }
  return {
    name: normalizedImportedText(record.name, "Nombre", { limit: 1000 }),
    description: normalizedImportedText(record.description, "Descripcion", {
      allowEmpty: true,
      limit: 10000,
    }),
    dimension: normalizedImportedText(record.dimension, "Dimension", {
      allowEmpty: true,
      limit: 1000,
    }),
    unitPrice: normalizedImportedPrice(record.unitPrice),
    provider: normalizedImportedText(record.provider, "Proveedor", { limit: 1000 }),
  };
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

function createImportedCartLine({ preview, item, sourceCurrency, provider, sectionId }) {
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    throw new Error("Item importado invalido");
  }
  const identity = normalizedImportedIdentity(preview.import_id, item.source_row, item.key);
  const edits = validatedImportedEdits({
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
    importId: identity.importId,
    sourceRow: identity.sourceRow,
    sourceCurrency: normalizedImportCurrency(sourceCurrency),
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
  return {
    kind: "imported",
    key: identity.key,
    importId: identity.importId,
    sourceRow: identity.sourceRow,
    sourceCurrency: normalizedImportCurrency(line.sourceCurrency),
    quantity: normalizedImportedQuantity(line.quantity),
    quantityRules: importedQuantityRules(),
    snapshot: copyImportedSnapshot(line.snapshot || {}),
    sectionId: normalizedSectionId(line.sectionId),
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
  const lines = preview.items.map((item) => {
    if (!sectionByKey.has(item?.key)) throw new Error("Cobertura de secciones importadas invalida");
    return createImportedCartLine({
      preview,
      item,
      sourceCurrency: item.source_currency || selectedCurrency,
      provider: selectedProvider,
      sectionId: sectionByKey.get(item.key),
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

export function updateImportedCartLine(lines, key, edits) {
  if (!Array.isArray(lines)) throw new Error("Lineas de carrito invalidas");
  const index = lines.findIndex((line) => line.key === key);
  if (index < 0 || lines[index]?.kind !== "imported") throw new Error("Linea importada no encontrada");
  const updates = identityRecord(edits);
  if (Object.keys(updates).some((field) => !IMPORTED_EDIT_FIELDS.has(field))) {
    throw new Error("Ediciones importadas invalidas");
  }
  const line = copyImportedCartLine(lines[index]);
  const nextEdits = validatedImportedEdits({ ...line.edits, ...updates });
  return lines.map((current, position) => (
    position === index ? { ...line, edits: nextEdits } : current
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

export function createMixedCartLine({
  catalog,
  identity,
  quantity,
  quantityRules,
  snapshot,
  sectionId = "section-1",
}) {
  if (!MIXED_CATALOGS.includes(catalog)) throw new Error("Catalogo mixto no soportado");
  const copiedIdentity = normalizedIdentity(catalog, identity);
  const copiedRules = copyQuantityRules(quantityRules);
  const line = {
    key: keyFromIdentity(catalog, copiedIdentity),
    catalog,
    identity: copiedIdentity,
    quantity: quantityFromMicrounits(quantityMicrounits(quantity)),
    quantityRules: copiedRules,
    snapshot: copySnapshot(snapshot),
    sectionId: normalizedSectionId(sectionId),
  };
  return { ...line, quantity: validateLineQuantity(line, line.quantity) };
}

export function validateLineQuantity(line, quantity) {
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
  const copiedIncoming = createMixedCartLine(incoming);
  const index = lines.findIndex((line) => line.key === copiedIncoming.key);
  if (index < 0) return [...lines, copiedIncoming];
  const combined = quantityMicrounits(lines[index].quantity)
    + quantityMicrounits(copiedIncoming.quantity);
  const refreshed = {
    ...lines[index],
    catalog: copiedIncoming.catalog,
    identity: copiedIncoming.identity,
    quantityRules: copiedIncoming.quantityRules,
    snapshot: copiedIncoming.snapshot,
    sectionId: normalizedSectionId(lines[index].sectionId || "section-1"),
  };
  const quantity = validateLineQuantity(refreshed, quantityFromMicrounits(combined));
  return lines.map((line, position) => (position === index ? { ...refreshed, quantity } : line));
}

export function updateMixedCartQuantity(lines, key, quantity) {
  if (!lines.some((line) => line.key === key)) throw new Error("Linea de carrito no encontrada");
  return lines.map((line) => (
    line.key === key ? { ...line, quantity: validateLineQuantity(line, quantity) } : line
  ));
}

export function removeMixedCartLine(lines, key) {
  return lines.filter((line) => line.key !== key);
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
