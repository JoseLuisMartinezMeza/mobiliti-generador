export const CATALOG_OPTIONS = Object.freeze([
  {value: "tarkett", label: "Tarkett"},
  {value: "offiho", label: "Offiho"},
  {value: "cr-global", label: "CR Global"},
  {value: "sonara", label: "Sonara"},
  {value: "sunon", label: "Sunon"},
  {value: "alma", label: "ALMA"},
  {value: "lumbro", label: "Lumbro"},
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

export function createCanonicalProductSelection(item) {
  const snapshot = item?.snapshot || {};
  return {
    catalog: item?.catalog || "",
    identity: deepCopy(item?.identity),
    official_code: item?.official_code || "",
    provider: catalogLabel(item?.catalog),
    snapshot: {
      name: snapshot.name || "",
      image_url: snapshot.image_url || "",
      configuration: snapshot.configuration || "",
      availability: snapshot.availability || "",
      warnings: normalizeWarnings(snapshot.warnings),
    },
  };
}

export function shouldShowProductImage(imageUrl, imageLoadFailed) {
  return Boolean(imageUrl) && !imageLoadFailed;
}
