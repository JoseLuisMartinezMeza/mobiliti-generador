export function createProjectOperationId(cryptoApi = globalThis.crypto) {
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("El navegador no puede generar un identificador de operación seguro.");
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export function createProjectLoadGuard() {
  let mounted = true;
  let currentEpoch = 0;
  return {
    begin() {
      currentEpoch += 1;
      return currentEpoch;
    },
    canApply(epoch) {
      return mounted && epoch === currentEpoch;
    },
    isMounted() {
      return mounted;
    },
    dispose() {
      mounted = false;
      currentEpoch += 1;
    },
  };
}

export const SAFE_PICKER_QUANTITY_RULES = Object.freeze({
  min: "1",
  step: "1",
  maxDecimals: 0,
  max: "1000000",
  integer: true,
});

/**
 * Catalog search intentionally omits commercial quantity rules. A product that
 * only exists in the picker therefore starts with this conservative integer
 * contract; it never inherits rules or quantity from the line being edited.
 */
export function createProjectPickerTarget(selection) {
  const technicalKey = String(
    selection?.display_key
    || selection?.identity?.internal_id
    || selection?.identity?.inventory_key
    || selection?.identity?.code
    || "",
  ).trim();
  if (!selection?.catalog || !selection?.identity || !technicalKey) {
    throw new Error("Selección de catálogo inválida");
  }
  const snapshot = selection.snapshot || {};
  return {
    catalog: selection.catalog,
    identity: structuredClone(selection.identity),
    officialCode: selection.official_code,
    provider: selection.catalog,
    quantity: "1",
    quantityRules: {...SAFE_PICKER_QUANTITY_RULES},
    snapshot: {
      name: snapshot.name || selection.official_code || technicalKey,
      code: selection.official_code || "",
      displayKey: technicalKey,
      image_url: snapshot.image_url || "",
      unit: "PZA",
      availability: snapshot.availability || "",
      configuration: snapshot.configuration || "",
      warnings: [...(snapshot.warnings || [])],
    },
  };
}

function decimalParts(value) {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)) {
    throw new Error("Cantidad decimal inválida");
  }
  const [integer, fraction = ""] = value.split(".");
  return {
    coefficient: BigInt(`${integer}${fraction}`),
    scale: fraction.length,
  };
}

export function multiplyProjectQuantity(left, right) {
  const first = decimalParts(left);
  const second = decimalParts(right);
  const coefficient = (first.coefficient * second.coefficient).toString();
  const scale = first.scale + second.scale;
  if (!scale) return coefficient;
  const padded = coefficient.padStart(scale + 1, "0");
  const split = padded.length - scale;
  const normalized = `${padded.slice(0, split)}.${padded.slice(split)}`
    .replace(/\.?0+$/, "");
  return normalized || "0";
}

export function projectMixedQuoteLines(lines) {
  if (!Array.isArray(lines)) throw new Error("Líneas de Proyecto inválidas");
  const parentById = new Map(lines.map((line) => [line.lineId, line]));
  return lines.map((line) => {
    if (line.role !== "complement") return {...line};
    const parent = parentById.get(line.parentLineId);
    if (!parent || parent.role !== "principal") {
      throw new Error("Padre de complemento inválido");
    }
    if (line._projectQuantityProjected === true) {
      return {...line, sectionId: parent.sectionId};
    }
    const quantity = line.quantityMode === "per_parent_unit"
      ? multiplyProjectQuantity(parent.quantity, line.quantity)
      : line.quantity;
    return {
      ...line,
      sectionId: parent.sectionId,
      quantity,
      _projectQuantityProjected: true,
    };
  });
}
