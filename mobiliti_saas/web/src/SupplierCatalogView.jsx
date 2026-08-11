import React, { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FolderKanban,
  ImageOff,
  PackageSearch,
  Plus,
  RefreshCw,
  Search,
} from "lucide-react";
import { createMixedCartLine } from "./mixedCart.js";
import {
  filterCatalogVariantGroups,
  productBaseConfigurationLabel,
  productPriceLabel,
  productVariantConfiguration,
} from "./productPicker.js";

const SUPPLIER_CACHE_VERSION = "v2";
const SUPPLIER_PAGE_SIZE = 24;
const QUANTITY_SCALE = 1000000;
const QUANTITY_LIMIT_MICROUNITS = 1000000 * QUANTITY_SCALE;

function catalogCacheKey(userId, supplier, source_hash) {
  return `supplier-catalog:${SUPPLIER_CACHE_VERSION}:${userId}:${supplier}:${source_hash}`;
}

function catalogPointerKey(userId, supplier) {
  return `supplier-catalog:${SUPPLIER_CACHE_VERSION}:${userId}:${supplier}:current`;
}

function readCatalogCache(userId, supplier) {
  try {
    const sourceHash = sessionStorage.getItem(catalogPointerKey(userId, supplier));
    if (!sourceHash) return null;
    const cached = JSON.parse(sessionStorage.getItem(catalogCacheKey(userId, supplier, sourceHash)) || "null");
    if (cached?.supplier === supplier && cached?.source_hash === sourceHash && Array.isArray(cached.items)) {
      return cached;
    }
  } catch {
    return null;
  }
  return null;
}

function writeCatalogCache(userId, supplier, data) {
  try {
    const key = catalogCacheKey(userId, supplier, data.source_hash);
    sessionStorage.setItem(key, JSON.stringify(data));
    sessionStorage.setItem(catalogPointerKey(userId, supplier), data.source_hash);
  } catch {
    // The live response remains usable when session storage is unavailable.
  }
}

function clearCatalogCache(userId, supplier) {
  try {
    const pointer = catalogPointerKey(userId, supplier);
    const sourceHash = sessionStorage.getItem(pointer);
    if (sourceHash) sessionStorage.removeItem(catalogCacheKey(userId, supplier, sourceHash));
    sessionStorage.removeItem(pointer);
  } catch {
    // Storage can be unavailable in privacy-restricted browser sessions.
  }
}

function normalizeText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function sourceCode(item) {
  if (item.code_status === "verified" && item.sku) return item.sku;
  return item.attributes?.source_code
    || item.attributes?.source_erp_code
    || item.attributes?.source_model_code
    || item.sku
    || "";
}

function decimal(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function nullableDecimal(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value, maximumFractionDigits = 6) {
  return new Intl.NumberFormat("es-MX", { maximumFractionDigits }).format(decimal(value));
}

function formatMoney(value, currency) {
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency,
    maximumFractionDigits: 2
  }).format(decimal(value));
}

function formatConfiguredPrice(item, value) {
  if (item.base_currency === "XXX" || decimal(value) <= 0) return "Por confirmar";
  return formatMoney(value, item.base_currency);
}

function isSquareMeterUnit(unit) {
  const normalized = String(unit || "").normalize("NFKC").replace(/\s+/g, "").toUpperCase();
  return normalized === "M2" || normalized === "M^2";
}

function quantityRules(item) {
  const rules = isSquareMeterUnit(item?.unit)
    ? { min: "0.000001", step: "0.000001", maxDecimals: 6, max: "1000000", integer: false }
    : { min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true };
  if (item?.is_out_of_stock) {
    return { ...rules, warningAt: "0", confirmOnInsufficient: true };
  }
  if (item?.availability_type === "stocked") {
    const stock = Number(item.stock ?? 0);
    const warningAt = Number.isFinite(stock)
      ? String(Math.min(Math.max(stock, 0), 1000000))
      : "0";
    return { ...rules, warningAt, confirmOnInsufficient: true };
  }
  return rules;
}

function quantityMicrounits(value) {
  const text = String(value ?? "").trim();
  if (!/^\d+(?:\.\d{1,6})?$/.test(text)) return null;
  const scaled = Math.round(Number(text) * QUANTITY_SCALE);
  if (!Number.isSafeInteger(scaled) || scaled <= 0 || scaled > QUANTITY_LIMIT_MICROUNITS) return null;
  return scaled;
}

function quantityFromMicrounits(value) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > QUANTITY_LIMIT_MICROUNITS) return null;
  const whole = Math.floor(value / QUANTITY_SCALE);
  const fraction = String(value % QUANTITY_SCALE).padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}

function validQuantity(item, value) {
  const microunits = quantityMicrounits(value);
  const rules = quantityRules(item);
  return microunits !== null && (!rules.integer || microunits % QUANTITY_SCALE === 0);
}

function familyLabel(family) {
  return family === "cushion" ? "Cojín opcional" : family;
}

function emptyFamilyLabel(family) {
  return family === "cushion" ? "Sin cojín" : "Sin agregado";
}

function productLinkLabel(item, supplier) {
  const status = item.attributes?.product_url_match?.status;
  if (supplier === "lumbro") return status === "exact_index" ? "Ver producto" : "Ver catálogo Lumbro";
  if (status === "catalog_fallback") return "Ver catálogo general";
  if (status === "collection_index") return "Ver colección";
  return "Ver producto";
}

function fixedBaseLabel(item) {
  const directEvidence = (item.attributes?.price_evidence || []).find((entry) => entry.kind === "direct");
  return directEvidence?.label || "Base sin cojín";
}

function groupCatalogItems(items) {
  const groups = new Map();
  items.forEach((item) => {
    const key = item.product_key || item.internal_id;
    const group = groups.get(key) || { product_key: key, variants: [] };
    group.variants.push(item);
    groups.set(key, group);
  });
  return Array.from(groups.values());
}

function configuredBasePrice(item, configuration) {
  const baseOptions = item.base_price_options || [];
  const selectedBase = baseOptions.find((option) => option.id === configuration.base_option_id);
  const basePrice = nullableDecimal(baseOptions.length ? selectedBase?.price_net : item.price_net);
  if (basePrice === null) return null;
  const selectedAddOns = new Set(configuration.add_on_option_ids || []);
  const selectedOptions = (item.add_on_options || [])
    .filter((option) => selectedAddOns.has(option.id));
  if (selectedOptions.some((option) => nullableDecimal(option.price_net) === null)) return null;
  const addOnPrice = selectedOptions.reduce((sum, option) => sum + decimal(option.price_net), 0);
  return basePrice + addOnPrice;
}

function pendingPriceContract(item, configuredPrice) {
  const status = warningKey(item.attributes?.price_status).replace(/_/g, " ");
  const pendingStatus = new Set([
    "pending", "price pending", "por confirmar", "precio por confirmar",
  ]).has(status);
  const pendingWarning = (item.warnings || []).some((warning) => {
    const key = warningKey(warning).replace(/_/g, " ");
    return key === "price pending" || key === "precio por confirmar";
  });
  return configuredPrice === null
    && item.attributes?.quotable === true
    && pendingStatus
    && pendingWarning;
}

function canAddSupplierItem(item, _supplier, configuredPrice) {
  const reviewAllowed = item.code_status === "needs_review"
    && decimal(item.tax_rate) === 0.16
    && (
      item.attributes?.quotable === true
      || (item.warnings || []).some((warning) => (
        warningKey(warning).replace(/_/g, " ") === "missing code"
        || warningKey(warning) === "codigo por verificar"
      ))
    );
  const codeAllowed = item.code_status === "verified" || reviewAllowed;
  const priceAllowed = decimal(configuredPrice) > 0 || pendingPriceContract(item, configuredPrice);
  return codeAllowed && priceAllowed && item.base_currency !== "XXX";
}

function warningKey(value) {
  return String(value || "")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .trim().toLowerCase();
}

function cartWarnings(item) {
  const result = [...(item.warnings || [])]
    .map((value) => String(value))
    .filter((value) => !["missing_code", "price_pending"].includes(value.trim().toLowerCase()));
  if (item.code_status === "needs_review") result.push("Código por verificar");
  if (pendingPriceContract(item, configuredBasePrice(item, initialConfiguration(item)))) {
    result.push("Precio por confirmar");
  }
  const seen = new Set();
  return result.filter((value) => {
    const key = warningKey(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function visibleConfiguration(item, configuration) {
  const selectedBase = (item.base_price_options || []).find(
    (option) => option.id === (configuration.base_option_id || "")
  );
  const selectedAddOns = new Set(configuration.add_on_option_ids || []);
  const addOnNames = (item.add_on_options || [])
    .filter((option) => selectedAddOns.has(option.id))
    .map((option) => String(option.name || "").trim())
    .filter(Boolean);
  return [String(selectedBase?.name || "").trim(), ...addOnNames]
    .filter(Boolean)
    .join(" + ");
}

function optionFamilies(item) {
  const families = new Map();
  (item.add_on_options || []).forEach((option) => {
    const family = option.family || "adicional";
    if (!families.has(family)) families.set(family, []);
    families.get(family).push(option);
  });
  return Array.from(families.entries());
}

function initialConfiguration(item) {
  return {
    base_option_id: "",
    add_on_option_ids: []
  };
}

function availabilityLabel(item) {
  if (item.availability_type === "made_to_order") return "Sobre pedido";
  if (item.is_out_of_stock) return "Agotado";
  if (item.availability_type === "stocked") return "Con existencia";
  if (item.availability_type === "unknown") return "Disponibilidad por confirmar";
  return "Disponibilidad por confirmar";
}

function availabilityByLeadTime(item) {
  const grouped = new Map();
  (item.attributes?.availability_buckets || []).forEach((bucket) => {
    const leadTime = String(bucket.lead_time || "").trim();
    if (!leadTime) return;
    grouped.set(leadTime, decimal(grouped.get(leadTime)) + decimal(bucket.quantity));
  });
  return Array.from(grouped, ([lead_time, quantity]) => ({ lead_time, quantity }));
}

export default function SupplierCatalogView({
  supplier,
  label,
  request,
  userId,
  onAddCartLine,
  onOpenCart,
  cartLineCount,
  cartBusy
}) {
  const [catalog, setCatalog] = useState(null);
  const [registry, setRegistry] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [query, setQuery] = useState("");
  const [brand, setBrand] = useState("");
  const [collection, setCollection] = useState("");
  const [availability, setAvailability] = useState("");
  const [page, setPage] = useState(1);
  const [selectedVariantByProduct, setSelectedVariantByProduct] = useState({});
  const [configurationByItem, setConfigurationByItem] = useState({});
  const [quantityByItem, setQuantityByItem] = useState({});
  const [submitError, setSubmitError] = useState("");

  useEffect(() => {
    let active = true;
    async function loadCatalog() {
      setLoading(true);
      setError("");
      const cached = readCatalogCache(userId, supplier);
      if (cached && active) setCatalog(cached);
      try {
        const registryData = await request("/catalogs");
        const data = await request(`/catalogs/${supplier}`);
        if (!active) return;
        setRegistry(registryData.suppliers || []);
        setCatalog(data);
        writeCatalogCache(userId, supplier, data);
      } catch (loadError) {
        if (active) {
          setError(cached
            ? "No se pudo actualizar el catalogo. Se muestran datos guardados; los apartados pueden estar desactualizados."
            : loadError.message || "No se pudo cargar el catalogo");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    loadCatalog();
    return () => {
      active = false;
    };
  }, [request, reloadKey, supplier, userId]);

  useEffect(() => {
    setPage(1);
  }, [query, brand, collection, availability, supplier]);

  const groups = useMemo(() => groupCatalogItems(catalog?.items || []), [catalog]);
  const brands = useMemo(
    () => Array.from(new Set((catalog?.items || []).map((item) => item.brand).filter(Boolean))).sort(),
    [catalog]
  );
  const collections = useMemo(
    () => Array.from(new Set((catalog?.items || []).map((item) => item.collection).filter(Boolean))).sort(),
    [catalog]
  );

  const filteredGroups = useMemo(() => {
    return filterCatalogVariantGroups(groups, {
      query,
      brand,
      collection,
      availability,
    });
  }, [availability, brand, collection, groups, query]);

  const pageCount = Math.max(1, Math.ceil(filteredGroups.length / SUPPLIER_PAGE_SIZE));
  const visibleGroups = filteredGroups.slice((page - 1) * SUPPLIER_PAGE_SIZE, page * SUPPLIER_PAGE_SIZE);
  const filteredVariantCount = filteredGroups.reduce(
    (total, group) => total + group.matchingVariants.length,
    0
  );
  function activeVariant(group) {
    const candidates = group.matchingVariants || group.variants;
    const selectedId = selectedVariantByProduct[group.product_key];
    return candidates.find((variant) => variant.internal_id === selectedId) || candidates[0];
  }

  function selectVariant(productKey, internalId) {
    setSelectedVariantByProduct((current) => ({ ...current, [productKey]: internalId }));
  }

  function configurationFor(item) {
    return configurationByItem[item.internal_id] || initialConfiguration(item);
  }

  function changeBaseOption(item, baseOptionId) {
    setConfigurationByItem((current) => ({
      ...current,
      [item.internal_id]: {
        base_option_id: baseOptionId,
        add_on_option_ids: (current[item.internal_id]?.add_on_option_ids || []).filter((optionId) => {
          const option = (item.add_on_options || []).find((candidate) => candidate.id === optionId);
          return !option?.compatible_base_option_ids?.length
            || option.compatible_base_option_ids.includes(baseOptionId);
        })
      }
    }));
  }

  function changeAddOnFamily(item, family, optionId) {
    setConfigurationByItem((current) => {
      const existing = current[item.internal_id] || initialConfiguration(item);
      const familyIds = new Set(
        (item.add_on_options || []).filter((option) => option.family === family).map((option) => option.id)
      );
      const retained = existing.add_on_option_ids.filter((id) => !familyIds.has(id));
      return {
        ...current,
        [item.internal_id]: {
          ...existing,
          add_on_option_ids: optionId ? [...retained, optionId] : retained
        }
      };
    });
  }

  function addToCart(item) {
    setSubmitError("");
    const configuration = configurationFor(item);
    if ((item.base_price_options || []).length && !configuration.base_option_id) {
      setSubmitError(`Selecciona ${productBaseConfigurationLabel(item).toLowerCase()} para ${item.name}.`);
      return;
    }
    const quantity = String(quantityByItem[item.internal_id] ?? "1").trim();
    if (!validQuantity(item, quantity)) {
      const requirement = quantityRules(item).integer ? "un número entero" : "una cantidad válida";
      setSubmitError(`Captura ${requirement} para ${item.name}.`);
      return;
    }
    try {
      const added = onAddCartLine(createMixedCartLine({
        catalog: supplier,
        identity: {
          internal_id: item.internal_id,
          base_option_id: configuration.base_option_id || "",
          add_on_option_ids: [...configuration.add_on_option_ids]
        },
        quantity,
        quantityRules: quantityRules(item),
        snapshot: {
          name: item.name,
          code: sourceCode(item),
          displayKey: item.internal_id,
          image_url: item.image_url || "",
          unit: item.unit,
          availability: availabilityLabel(item),
          configuration: visibleConfiguration(item, configuration),
          warnings: cartWarnings(item)
        },
        officialCode: sourceCode(item),
      }));
      if (added) onOpenCart();
    } catch (cartFailure) {
      setSubmitError(cartFailure.message || "No se pudo agregar el producto");
    }
  }

  const registered = registry.some((entry) => entry.supplier === supplier);

  return (
    <section className="supplier-catalog-view">
      <header className="supplier-catalog-header">
        <div>
          <h2>{label}</h2>
          <p>
            {catalog
              ? `${groups.length} productos agrupados - ${catalog.total ?? catalog.items.length} variantes`
              : "Catalogo de proveedor"}
            {catalog?.generated_at ? ` - ${new Date(catalog.generated_at).toLocaleString("es-MX")}` : ""}
          </p>
        </div>
        <div className="supplier-header-actions">
          <button
            className="ghost-action"
            type="button"
            onClick={() => {
              clearCatalogCache(userId, supplier);
              setReloadKey((value) => value + 1);
            }}
          >
            <RefreshCw size={17} /> Refrescar
          </button>
          <button
            className="supplier-cart-toggle"
            type="button"
            aria-label="Abrir proyecto"
            title="Abrir proyecto"
            onClick={onOpenCart}
          >
            <FolderKanban size={19} />
            <span>{cartLineCount}</span>
          </button>
        </div>
      </header>

      {!loading && registry.length > 0 && !registered ? (
        <div className="supplier-alert"><AlertTriangle size={18} /> Este catalogo no esta habilitado en el entorno actual.</div>
      ) : null}
      {error ? <div className="error-line">{error}</div> : null}
      {submitError ? <div className="error-line" role="alert">{submitError}</div> : null}

      <div className="supplier-filters" aria-label="Filtros de catalogo">
        <label className="supplier-search">
          <span>Buscar</span>
          <Search size={18} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Codigo, producto o atributo" />
        </label>
        <label>
          <span>Marca</span>
          <select value={brand} onChange={(event) => setBrand(event.target.value)}>
            <option value="">Todas</option>
            {brands.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>
          <span>Coleccion</span>
          <select value={collection} onChange={(event) => setCollection(event.target.value)}>
            <option value="">Todas</option>
            {collections.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>
          <span>Disponibilidad</span>
          <select value={availability} onChange={(event) => setAvailability(event.target.value)}>
            <option value="">Toda</option>
            <option value="stocked">Con existencia</option>
            <option value="made_to_order">Sobre pedido</option>
            <option value="out">Agotado</option>
          </select>
        </label>
        <strong>{filteredGroups.length} productos - {filteredVariantCount} variantes visibles</strong>
      </div>

      <div className="supplier-catalog-layout">
        <div className="supplier-results">
          {loading && !catalog ? <div className="supplier-empty"><PackageSearch size={32} /> Cargando catalogo...</div> : null}
          {!loading && !visibleGroups.length ? <div className="supplier-empty">No hay productos para estos filtros.</div> : null}
          <div className="supplier-product-grid">
            {visibleGroups.map((group) => {
              const item = activeVariant(group);
              const matchingVariants = group.matchingVariants || group.variants;
              const configuration = configurationFor(item);
              const families = optionFamilies(item);
              const selectedBaseId = configuration.base_option_id;
              const configuredPrice = configuredBasePrice(item, configuration);
              const variantConfiguration = productVariantConfiguration(item);
              const baseConfigurationLabel = productBaseConfigurationLabel(item);
              const linkText = productLinkLabel(item, supplier);
              const productQuantity = quantityRules(item);
              const availabilityBuckets = availabilityByLeadTime(item);
              const hasFixedConfigurableBase = (
                !(item.base_price_options || []).length
                && families.length > 0
                && decimal(item.price_net) > 0
                && ["kun", "mondecasa"].includes(normalizeText(item.brand))
              );
              return (
                <article className="supplier-product-card" key={group.product_key}>
                  <div className="supplier-product-main">
                    <div className="supplier-image-frame">
                      {item.image_url ? (
                        <img loading="lazy" src={item.image_url} alt={`${item.name} ${sourceCode(item)}`} />
                      ) : <ImageOff size={34} aria-label="Sin imagen" />}
                    </div>
                    <div className="supplier-product-copy">
                      <div className="supplier-product-code">
                        <strong>{sourceCode(item) || "Código por verificar"}</strong>
                        {item.product_url ? (
                          <a className="supplier-product-link" href={item.product_url} target="_blank" rel="noreferrer" title={linkText} aria-label={`${linkText} oficial de ${item.name}; abre en una pestaña nueva`}>
                            <ExternalLink size={17} />
                            <span>{linkText}</span>
                          </a>
                        ) : null}
                      </div>
                      <h3>{item.name}</h3>
                      <p>{item.description}</p>
                      <div className="supplier-badges">
                        {item.code_status === "needs_review" ? <span className="supplier-badge warning">Código por verificar</span> : null}
                        {decimal(configuredPrice) <= 0 || item.base_currency === "XXX" ? <span className="supplier-badge warning">Precio por confirmar</span> : null}
                        {item.availability_type === "made_to_order" ? <span className="supplier-badge order">Sobre pedido</span> : null}
                        {item.image_kind === "generated_reference" ? <span className="supplier-badge reference">Imagen de referencia</span> : null}
                        {item.image_kind === "placeholder" ? <span className="supplier-badge warning">Imagen por verificar</span> : null}
                        {item.attributes?.product_url_match?.status === "catalog_fallback" ? (
                          <span className="supplier-badge general" aria-label="Enlace al catálogo general">Catálogo general</span>
                        ) : null}
                        {item.reserved_by_others ? <span className="supplier-badge reserved">Apartado</span> : null}
                        {item.is_out_of_stock ? <span className="supplier-badge exhausted">Agotado</span> : null}
                      </div>
                      <dl className="supplier-product-facts">
                        <div><dt>Unidad</dt><dd>{item.unit}</dd></div>
                        <div><dt>Existencia</dt><dd>{availabilityLabel(item)}</dd></div>
                        <div><dt>Apartado</dt><dd>{formatNumber(item.reserved_quantity)}</dd></div>
                        {item.attributes?.dimensions ? (
                          <div className="supplier-product-dimensions"><dt>Dimensiones</dt><dd>{item.attributes.dimensions}</dd></div>
                        ) : null}
                        {variantConfiguration ? (
                          <div><dt>Acabado / material del aro</dt><dd>{variantConfiguration}</dd></div>
                        ) : null}
                        {decimal(configuredPrice) <= 0 && item.attributes?.source_price_printed ? (
                          <div><dt>Precio fuente (moneda por confirmar)</dt><dd>{item.attributes.source_price_printed}</dd></div>
                        ) : null}
                        {item.attributes?.color ? (
                          <div><dt>Color</dt><dd>{item.attributes.color}</dd></div>
                        ) : null}
                        {item.attributes?.warranty ? (
                          <div><dt>Garantia</dt><dd>{item.attributes.warranty}</dd></div>
                        ) : null}
                        {Array.isArray(item.attributes?.product_notes) && item.attributes.product_notes.length ? (
                          <div><dt>Notas</dt><dd>{item.attributes.product_notes.join(" - ")}</dd></div>
                        ) : null}
                        {item.lead_time ? (
                          <div><dt>Entrega</dt><dd>{item.lead_time}</dd></div>
                        ) : null}
                        {availabilityBuckets.length ? (
                          <div className="supplier-availability-buckets">
                            <dt>Disponibilidad por plazo</dt>
                            <dd>
                              {availabilityBuckets.map((bucket, index) => (
                                <span key={`${bucket.lead_time || "plazo"}-${index}`}>
                                  {formatNumber(bucket.quantity)} {item.unit} - {bucket.lead_time}
                                </span>
                              ))}
                            </dd>
                          </div>
                        ) : null}
                      </dl>
                    </div>
                  </div>

                  {matchingVariants.length > 1 ? (
                    <label className="supplier-config-field">
                      <span>{supplier === "alma" ? "Acabado / material del aro" : "Variante"}</span>
                      <select disabled={cartBusy} value={item.internal_id} onChange={(event) => selectVariant(group.product_key, event.target.value)}>
                        {matchingVariants.map((variant) => (
                          <option value={variant.internal_id} key={variant.internal_id}>
                            {sourceCode(variant) || variant.internal_id} - {productVariantConfiguration(variant) || variant.attributes?.color || variant.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}

                  {(item.base_price_options || []).length ? (
                    <fieldset className="supplier-config-field supplier-option-field">
                      <legend>{baseConfigurationLabel} *</legend>
                      <div className="supplier-option-buttons" role="group" aria-label={`${baseConfigurationLabel} de ${item.name}`}>
                        {item.base_price_options.filter((option) => option.available).map((option) => {
                          const active = configuration.base_option_id === option.id;
                          return (
                            <button
                              type="button"
                              className={`supplier-option-button ${active ? "active" : ""}`}
                              aria-pressed={active}
                              disabled={cartBusy}
                              onClick={() => changeBaseOption(item, option.id)}
                              key={option.id}
                            >
                              <span>{option.name}</span>
                              <strong>{formatMoney(option.price_net, item.base_currency)}</strong>
                            </button>
                          );
                        })}
                      </div>
                    </fieldset>
                  ) : null}

                  {hasFixedConfigurableBase ? (
                    <fieldset className="supplier-config-field supplier-option-field">
                      <legend>Materialidad base</legend>
                      <div className="supplier-option-buttons" role="group" aria-label={`Materialidad base de ${item.name}`}>
                        <button
                          type="button"
                          className="supplier-option-button active fixed"
                          aria-pressed={true}
                          aria-disabled={true}
                        >
                          <span>{fixedBaseLabel(item)}</span>
                          <strong>{formatMoney(item.price_net, item.base_currency)}</strong>
                        </button>
                      </div>
                    </fieldset>
                  ) : null}

                  {families.map(([family, options]) => {
                    const selectedOptionId = configuration.add_on_option_ids.find((id) => options.some((option) => option.id === id)) || "";
                    const availableOptions = options
                      .filter((option) => option.available)
                      .filter((option) => !option.compatible_base_option_ids?.length || option.compatible_base_option_ids.includes(selectedBaseId));
                    return (
                      <fieldset className="supplier-config-field supplier-option-field" key={family}>
                        <legend>{familyLabel(family)}</legend>
                        <div className="supplier-option-buttons" role="group" aria-label={`${familyLabel(family)} de ${item.name}`}>
                          <button
                            type="button"
                            className={`supplier-option-button ${selectedOptionId ? "" : "active"}`}
                            aria-pressed={!selectedOptionId}
                            disabled={cartBusy}
                            onClick={() => changeAddOnFamily(item, family, "")}
                          >
                            <span>{emptyFamilyLabel(family)}</span>
                            <strong>Sin costo adicional</strong>
                          </button>
                          {availableOptions.map((option) => {
                            const active = selectedOptionId === option.id;
                            return (
                              <button
                                type="button"
                                className={`supplier-option-button ${active ? "active" : ""}`}
                                aria-pressed={active}
                                disabled={cartBusy}
                                onClick={() => changeAddOnFamily(item, family, option.id)}
                                key={option.id}
                              >
                                <span>{option.name}</span>
                                <strong>+ {formatMoney(option.price_net, item.base_currency)}</strong>
                              </button>
                            );
                          })}
                        </div>
                      </fieldset>
                    );
                  })}

                  <div className="supplier-card-footer">
                    <div>
                      <span>Precio neto</span>
                      <strong>{productPriceLabel(
                        item,
                        configuration.base_option_id,
                        configuration.add_on_option_ids,
                      )}</strong>
                      <small>mas IVA</small>
                    </div>
                    <label>
                      <span>Cant.</span>
                      <input
                        type="number"
                        min={productQuantity.min}
                        step={productQuantity.step}
                        value={quantityByItem[item.internal_id] ?? "1"}
                        disabled={cartBusy}
                        onChange={(event) => setQuantityByItem((current) => ({ ...current, [item.internal_id]: event.target.value }))}
                        placeholder="1"
                      />
                    </label>
                    <button
                      className="primary-action"
                      type="button"
                      disabled={cartBusy || !canAddSupplierItem(item, supplier, configuredPrice)}
                      onClick={() => addToCart(item)}
                    >
                      <Plus size={18} /> Agregar
                    </button>
                  </div>
                  {item.is_out_of_stock ? (
                    <p className="supplier-stock-warning">Advertencia: agotado. Se puede cotizar; verificar disponibilidad.</p>
                  ) : null}
                </article>
              );
            })}
          </div>

          <nav className="supplier-pagination" aria-label="Paginacion del catalogo">
            <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
              <ChevronLeft size={17} /> Pagina anterior
            </button>
            <span>Pagina {page} de {pageCount}</span>
            <button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>
              Pagina siguiente <ChevronRight size={17} />
            </button>
          </nav>
        </div>

      </div>
    </section>
  );
}
