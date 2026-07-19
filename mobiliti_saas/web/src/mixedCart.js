export const MIXED_CATALOGS = Object.freeze([
  "tarkett",
  "offiho",
  "cr-global",
  "sonara",
  "sunon",
  "alma",
  "lumbro",
]);

const SUPPLIER_CATALOGS = new Set(MIXED_CATALOGS.slice(2));
const QUANTITY_PATTERN = /^(?:0|[1-9]\d{0,6})(?:\.(\d{1,6}))?$/;
const IDENTITY_CONTROL_PATTERN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const QUANTITY_SCALE = 1000000n;

function normalizedText(value, field, { allowEmpty = false, limit = 1000 } = {}) {
  if (typeof value !== "string") throw new Error(`${field} requerido`);
  const text = value.trim();
  if ((!text && !allowEmpty) || text.length > limit || IDENTITY_CONTROL_PATTERN.test(text)) {
    throw new Error(`${field} requerido`);
  }
  return text;
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
  const result = value.map((candidate) => normalizedText(candidate, "Add-on", { limit: 500 }));
  if (new Set(result).size !== result.length) throw new Error("Add-ons duplicados");
  return result.sort(compareUnicodeCodePoints);
}

function normalizedIdentity(catalog, identity) {
  if (!identity || typeof identity !== "object" || Array.isArray(identity)) {
    if (catalog === "tarkett") throw new Error("code requerido");
    if (catalog === "offiho") throw new Error("inventory_key requerido");
    throw new Error("internal_id requerido");
  }
  if (catalog === "tarkett") {
    return { code: normalizedText(identity.code, "code") };
  }
  if (catalog === "offiho") {
    return { inventory_key: normalizedText(identity.inventory_key, "inventory_key") };
  }
  if (!SUPPLIER_CATALOGS.has(catalog)) throw new Error("Catalogo mixto no soportado");
  const internalId = normalizedText(identity.internal_id, "internal_id");
  const baseOptionId = identity.base_option_id === undefined
    ? ""
    : normalizedText(identity.base_option_id, "base_option_id", { allowEmpty: true, limit: 500 });
  return {
    internal_id: internalId,
    base_option_id: baseOptionId,
    add_on_option_ids: normalizedAddOns(identity.add_on_option_ids),
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

export function createMixedCartLine({ catalog, identity, quantity, quantityRules, snapshot }) {
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
