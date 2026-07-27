export const CATALOG_OPTIONS = Object.freeze([
  {value: "tarkett", label: "Tarkett"},
  {value: "offiho", label: "Offiho"},
  {value: "cr-global", label: "CR Global"},
  {value: "sonara", label: "Sonara"},
  {value: "sunon", label: "Sunon"},
  {value: "alma", label: "ALMA"},
  {value: "lumbro", label: "Lumbro"},
  {value: "jome", label: "JOME"},
  {value: "lauco", label: "Lauco"},
]);

const CATALOG_LABELS = new Map(CATALOG_OPTIONS.map((option) => [option.value, option.label]));

export function catalogLabel(catalog) {
  return CATALOG_LABELS.get(catalog) || "Catálogo no disponible";
}

export function buildCatalogSearchPath({query = "", supplier = "", offset = 0, limit = 20}) {
  const params = new URLSearchParams({
    q: String(query),
    offset: String(offset),
    limit: String(limit),
  });
  if (CATALOG_LABELS.has(supplier)) params.set("supplier", supplier);
  return `/catalogs/search?${params.toString()}`;
}

export function normalizeWarnings(value) {
  if (Array.isArray(value)) return value.filter(Boolean).map(String);
  return value ? [String(value)] : [];
}

function deepCopy(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export function productBaseOptions(item) {
  if (!Array.isArray(item?.base_options)) return [];
  return item.base_options.filter((option) => (
    option
    && typeof option.id === "string"
    && option.id
    && typeof option.name === "string"
    && option.name
  ));
}

export function initialBaseOptionId(item) {
  const options = productBaseOptions(item);
  const current = item?.identity?.base_option_id || "";
  if (options.some((option) => option.id === current)) return current;
  return options.length === 1 ? options[0].id : "";
}

export function productAddOnOptions(item, selectedBaseOptionId) {
  if (!Array.isArray(item?.add_on_options)) return [];
  const baseId = String(selectedBaseOptionId || "");
  return item.add_on_options.filter((option) => {
    if (
      !option
      || typeof option.id !== "string"
      || !option.id
      || typeof option.name !== "string"
      || !option.name
      || typeof option.family !== "string"
      || !option.family
    ) return false;
    const compatible = Array.isArray(option.compatible_base_option_ids)
      ? option.compatible_base_option_ids.filter((value) => typeof value === "string" && value)
      : [];
    return !compatible.length || compatible.includes(baseId);
  });
}

export function productAddOnFamilies(item, selectedBaseOptionId) {
  const grouped = new Map();
  for (const option of productAddOnOptions(item, selectedBaseOptionId)) {
    if (!grouped.has(option.family)) grouped.set(option.family, []);
    grouped.get(option.family).push(option);
  }
  return [...grouped.entries()].map(([family, options]) => ({family, options}));
}

export function initialAddOnOptionIds(item, selectedBaseOptionId) {
  if (!Array.isArray(item?.add_on_options)) {
    return [...new Set(
      (Array.isArray(item?.identity?.add_on_option_ids)
        ? item.identity.add_on_option_ids
        : [])
        .filter((value) => typeof value === "string" && value),
    )];
  }
  const options = productAddOnOptions(item, selectedBaseOptionId);
  const seenFamilies = new Set();
  const result = [];
  for (const rawId of Array.isArray(item?.identity?.add_on_option_ids)
    ? item.identity.add_on_option_ids
    : []) {
    const option = options.find((candidate) => candidate.id === rawId);
    if (!option || seenFamilies.has(option.family)) continue;
    seenFamilies.add(option.family);
    result.push(option.id);
  }
  return result;
}

export function createCanonicalProductSelection(
  item,
  selectedBaseOptionId,
  selectedAddOnOptionIds,
) {
  const snapshot = item?.snapshot || {};
  const identity = deepCopy(item?.identity);
  const baseOptions = productBaseOptions(item);
  let configuration = snapshot.configuration || "";
  let requestedBaseId = selectedBaseOptionId === undefined
    ? initialBaseOptionId(item)
    : String(selectedBaseOptionId || "");
  if (baseOptions.length) {
    const selectedBase = baseOptions.find((option) => option.id === requestedBaseId);
    if (!selectedBase) throw new Error("Selecciona una configuración base");
    identity.base_option_id = selectedBase.id;
    configuration = selectedBase.name;
  } else {
    requestedBaseId = "";
  }
  const compatibleAddOns = productAddOnOptions(item, requestedBaseId);
  const requestedAddOnIds = selectedAddOnOptionIds === undefined
    ? initialAddOnOptionIds(item, requestedBaseId)
    : selectedAddOnOptionIds;
  if (!Array.isArray(requestedAddOnIds)) throw new Error("Configuración adicional inválida");
  const selectedAddOns = [];
  const seenFamilies = new Set();
  const seenIds = new Set();
  if (!Array.isArray(item?.add_on_options)) {
    identity.add_on_option_ids = requestedAddOnIds.filter((optionId) => (
      typeof optionId === "string" && optionId
    ));
  }
  for (const rawId of requestedAddOnIds) {
    if (!Array.isArray(item?.add_on_options)) break;
    const optionId = String(rawId || "");
    if (!optionId || seenIds.has(optionId)) continue;
    const option = compatibleAddOns.find((candidate) => candidate.id === optionId);
    if (!option) throw new Error(`Configuración adicional incompatible: ${optionId}`);
    if (seenFamilies.has(option.family)) {
      throw new Error(`No se permite más de una configuración de la familia ${option.family}`);
    }
    seenIds.add(optionId);
    seenFamilies.add(option.family);
    selectedAddOns.push(option);
  }
  if (Array.isArray(item?.add_on_options)) {
    identity.add_on_option_ids = selectedAddOns.map((option) => option.id);
  }
  configuration = [
    configuration,
    ...selectedAddOns.map((option) => option.name),
  ].filter(Boolean).join(" + ");
  return {
    catalog: item?.catalog || "",
    identity,
    official_code: item?.official_code || "",
    provider: catalogLabel(item?.catalog),
    snapshot: {
      name: snapshot.name || "",
      image_url: snapshot.image_url || "",
      configuration,
      availability: snapshot.availability || "",
      warnings: normalizeWarnings(snapshot.warnings),
    },
  };
}

export function shouldShowProductImage(imageUrl, imageLoadFailed) {
  return Boolean(imageUrl) && !imageLoadFailed;
}
