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
  const options = Array.isArray(item?.base_options)
    ? item.base_options
    : Array.isArray(item?.base_price_options)
      ? item.base_price_options
      : [];
  return options.filter((option) => (
    option
    && typeof option.id === "string"
    && option.id
    && typeof option.name === "string"
    && option.name
  ));
}

function safePositivePrice(value) {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  const price = Number(String(value).trim());
  return Number.isFinite(price) && price > 0 ? price : null;
}

function safeCurrency(value) {
  const currency = String(value || "").trim().toUpperCase();
  return /^[A-Z]{3}$/.test(currency) ? currency : "";
}

function formattedMoney(currency, amount) {
  if (!currency || !Number.isFinite(amount) || amount <= 0) return "";
  return `${currency} ${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount)}`;
}

export function productDimensions(item) {
  return String(
    item?.snapshot?.dimensions
    || item?.attributes?.dimensions
    || item?.dimensions
    || "",
  ).trim();
}

export function productPriceLabel(
  item,
  selectedBaseOptionId = "",
  selectedAddOnOptionIds = [],
) {
  const currency = safeCurrency(item?.base_currency);
  if (!currency) return "Precio por confirmar";

  const baseOptions = productBaseOptions(item);
  const selectedBase = baseOptions.find((option) => option.id === selectedBaseOptionId);
  if (selectedBase) {
    const basePrice = safePositivePrice(selectedBase.price_net);
    if (basePrice === null) return "Precio por confirmar";
    const selectedIds = new Set(Array.isArray(selectedAddOnOptionIds) ? selectedAddOnOptionIds : []);
    const addOnTotal = productAddOnOptions(item, selectedBaseOptionId)
      .filter((option) => selectedIds.has(option.id))
      .reduce((total, option) => total + (safePositivePrice(option.price_net) || 0), 0);
    return formattedMoney(currency, basePrice + addOnTotal) || "Precio por confirmar";
  }

  const availableBasePrices = baseOptions
    .map((option) => safePositivePrice(option.price_net))
    .filter((price) => price !== null);
  if (availableBasePrices.length) {
    const amount = Math.min(...availableBasePrices);
    const label = formattedMoney(currency, amount);
    return baseOptions.length > 1 ? `Desde ${label}` : label;
  }

  return formattedMoney(currency, safePositivePrice(item?.price_net)) || "Precio por confirmar";
}

export function productOptionLabel(option, item, {additive = false} = {}) {
  const name = String(option?.name || "").trim();
  const price = formattedMoney(
    safeCurrency(item?.base_currency),
    safePositivePrice(option?.price_net),
  );
  if (!price) return name;
  return `${name} · ${additive ? "+ " : ""}${price}`;
}

function normalizedSupplier(item) {
  return String(item?.catalog || item?.supplier || "").trim().toLowerCase();
}

function productDisplayName(item) {
  return String(item?.snapshot?.name || item?.name || "").trim();
}

export function productVariantConfiguration(item) {
  if (normalizedSupplier(item) !== "alma") return "";
  const match = productDisplayName(item).match(/\brim\s*:\s*(.+)$/i);
  return match?.[1]?.trim() || "";
}

export function productBaseConfigurationLabel(item) {
  const optionNames = productBaseOptions(item)
    .map((option) => String(option.name || "").toLowerCase())
    .join(" ");
  if (normalizedSupplier(item) === "alma" && optionNames.includes("tela")) {
    return "Calidad de tela";
  }
  return "Configuración base";
}

function normalizedText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function variantSourceCode(item) {
  if (item?.code_status === "verified" && item?.sku) return item.sku;
  return item?.attributes?.source_code
    || item?.attributes?.source_erp_code
    || item?.attributes?.source_model_code
    || item?.official_code
    || item?.sku
    || "";
}

export function filterCatalogVariantGroups(
  groups,
  {query = "", brand = "", collection = "", availability = ""} = {},
) {
  const normalizedQuery = normalizedText(query);
  return (Array.isArray(groups) ? groups : []).map((group) => {
    const eligibleVariants = (Array.isArray(group?.variants) ? group.variants : []).filter((item) => {
      const matchesAvailability = !availability
        || (availability === "out"
          ? item?.is_out_of_stock
          : availability === "stocked"
            ? item?.availability_type === "stocked" && !item?.is_out_of_stock
            : item?.availability_type === availability);
      return (!brand || item?.brand === brand)
        && (!collection || item?.collection === collection)
        && matchesAvailability;
    });
    if (!eligibleVariants.length) return {...group, matchingVariants: []};
    const groupMatchesQuery = !normalizedQuery || eligibleVariants.some((item) => {
      const searchable = normalizedText([
        variantSourceCode(item),
        item?.name,
        item?.description,
        item?.brand,
        item?.collection,
        JSON.stringify(item?.attributes || {}),
      ].join(" "));
      return searchable.includes(normalizedQuery);
    });
    return {
      ...group,
      matchingVariants: groupMatchesQuery ? eligibleVariants : [],
    };
  }).filter((group) => group.matchingVariants.length > 0);
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
