// Proveedores que acepta la plantilla. La lista vive en el motor y se pide una vez a la API.
let catalog = {providers: [], aliases: {}};
const listeners = new Set();

export function officialProvidersSnapshot() {
  return catalog;
}

export function subscribeOfficialProviders(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function loadOfficialProviders(request) {
  return request("/proveedores-oficiales")
    .then((data) => {
      if (!Array.isArray(data?.providers) || !data.providers.length) return;
      catalog = {providers: data.providers.map(String), aliases: {...(data.aliases || {})}};
      listeners.forEach((listener) => listener());
    })
    // Sin la lista (API anterior) el campo sigue siendo texto libre.
    .catch(() => {});
}

// Misma normalizacion que official_provider_name en el motor.
function providerKey(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, " ")
    .trim();
}

// Valor inicial del campo: el oficial reconocido; con la lista cargada, lo no reconocido queda sin elegir.
export function suggestedProvider(value) {
  return catalog.providers.length ? officialProviderName(value) : String(value || "").trim();
}

// Devuelve el proveedor oficial reconocido, o "" para que el usuario lo elija.
export function officialProviderName(value, {providers = [], aliases = {}} = catalog) {
  const key = providerKey(value);
  if (!key) return "";
  return aliases[key] || providers.find((provider) => providerKey(provider) === key) || "";
}
