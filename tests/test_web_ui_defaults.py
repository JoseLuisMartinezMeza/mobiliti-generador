from pathlib import Path


def test_quote_form_recommends_dezgo_by_default():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert 'image_provider: "dezgo"' in source
    assert 'descuento: "40"' in source
    assert 'label="Numero de cotizacion"' in source
    assert 'readOnly' in source
    assert 'placeholder="Automatico por usuario"' in source
    assert 'label="Descuento (%)"' in source
    assert 'max="100"' in source
    assert 'image_prompt: DEFAULT_IMAGE_PROMPT' in source
    assert 'const DEFAULT_IMAGE_PROMPT = "Mejora la calidad de imagen y que este en fondo blanco";' in source
    assert "IA Dezgo recomendado - genera faltantes realistas" in source
    assert "Local sin IA - no inventa imagenes faltantes" in source
    assert "Prompt para imagenes" in source
    assert "MAX_QUOTE_INPUT_MB = 25" in source
    assert "El archivo supera el limite" in source


def test_download_and_generation_timers_are_visible():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")

    assert "function JobDuration" in source
    assert "function DownloadButton" in source
    assert "function DownloadStatusLine" in source
    assert "Preparando descarga..." in source
    assert "Descargando ${elapsed}" in source
    assert "Estimado aprox." in source
    assert "Faltan aprox." in source
    assert "Tardo ${formatDuration(elapsed)}" in source
    assert ".job-duration" in styles
    assert ".download-downloading" in styles
    assert ".download-line.active" in styles


def test_expired_session_is_cleared_instead_of_showing_raw_token_error():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    api = Path("mobiliti_saas/web/api/index.py").read_text(encoding="utf-8")

    assert 'const AUTH_EXPIRED_EVENT = "mobiliti:auth-expired";' in source
    assert "Tu sesion expiro. Vuelve a iniciar sesion" in source
    assert "window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))" in source
    assert "localStorage.removeItem(\"mobiliti_session\")" in source
    assert "notice-line" in source
    assert ".notice-line" in styles
    assert 'os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "720")' in api


def test_history_exposes_delete_quote_action():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")

    assert "Trash2" in source
    assert "async function deleteJob" in source
    assert 'method: "DELETE"' in source
    assert "Eliminar" in source
    assert ".danger-action" in styles


def test_download_does_not_mutate_signed_url_query():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert "withDownloadFilename" not in source
    assert "searchParams.set(\"download\"" not in source
    assert "download=${encodeURIComponent" not in source
    assert "const filename = data.filename || quoteDownloadFallbackName(job);" in source
    assert "link.href = signedUrl;" in source


def test_tarkett_tab_catalog_cache_and_cart_are_present():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    vercel = Path("mobiliti_saas/web/vercel.json").read_text(encoding="utf-8")

    assert '["tarkett", "Tarkett", PackageSearch]' in source
    assert "function TarkettView" in source
    assert 'request("/tarkett/catalog")' in source
    assert 'request("/tarkett/quote"' in source
    assert 'const TARKETT_CATALOG_CACHE_KEY = "mobiliti_tarkett_catalog";' in source
    assert "sessionStorage.setItem(TARKETT_CATALOG_CACHE_KEY" in source
    assert "Apartado {formatQuantity(reserved)}" in source
    assert ".tarkett-grid" in styles
    assert ".tarkett-cart-panel" in styles
    assert '"/tarkett/:path*"' in vercel


def test_offiho_tab_catalog_cart_and_warning_contracts_are_present():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    vercel = Path("mobiliti_saas/web/vercel.json").read_text(encoding="utf-8")

    assert 'Armchair' in source
    assert '["tarkett", "Tarkett", PackageSearch]' in source
    assert '["offiho", "Offiho", Armchair]' in source
    assert source.index('["tarkett", "Tarkett", PackageSearch]') < source.index('["offiho", "Offiho", Armchair]')
    assert "function OffihoView" in source
    assert 'request("/offiho/catalog")' in source
    assert 'request("/offiho/quote"' in source
    assert 'const OFFIHO_CATALOG_CACHE_KEY = "mobiliti_offiho_catalog";' in source
    assert "sessionStorage.removeItem(OFFIHO_CATALOG_CACHE_KEY)" in source
    assert "inventory_key" in source
    assert "Stock insuficiente" in source
    assert "Agotado" in source
    assert "window.confirm" in source
    assert "numeric > 1000000" in source
    assert "maximumFractionDigits: 2" in source
    assert "rel=\"noreferrer noopener\"" in source
    assert '"/offiho/:path*"' in vercel
    assert ".offiho-product" in styles
    assert ".offiho-warning" in styles


def test_offiho_catalog_uses_factual_filters_cache_and_pagination_contracts():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")

    assert 'const OFFIHO_PAGE_SIZE = 24;' in source
    assert "const pagedItems = useMemo" in source
    assert "filteredItems.slice(pageStart, pageStart + OFFIHO_PAGE_SIZE)" in source
    assert "pagedItems.map" in source
    assert "ChevronLeft" in source and "ChevronRight" in source
    assert 'aria-label="Pagina anterior"' in source
    assert 'aria-label="Pagina siguiente"' in source
    assert "Pagina {page} de {pageCount}" in source
    assert "unitFilter" in source
    assert "Todas las unidades" in source
    assert "brandFilter" not in source
    assert "categoryFilter" not in source
    assert "user_id: userId" in source
    assert "cached?.user_id === userId" in source
    assert "clearCatalogCaches" in source
    assert ".offiho-pagination" in styles


def test_offiho_quantity_price_and_submit_guard_contracts_are_present():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert "Precio por confirmar" in source
    assert "Total con precios disponibles" in source
    assert "precios por confirmar" in source
    assert "onBlur" in source
    assert "rawQuantity" in source
    assert "1." in source
    assert "isSubmittingRef" in source
    assert "if (isSubmittingRef.current) return;" in source
    assert "isSubmittingRef.current = true;" in source
    assert "isSubmittingRef.current = false;" in source
    assert 'role="status"' in source
    assert 'aria-live="polite"' in source


def test_catalog_shell_is_unframed_and_intermediate_breakpoint_prevents_overlap():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")

    assert 'className="tarkett-shell"' in source
    assert 'className="tarkett-shell offiho-shell"' in source
    assert 'main-card full tarkett-shell' not in source
    assert ".tarkett-shell {\n  margin: 18px;\n  overflow: visible;\n  background: transparent;\n  border: 0;\n  border-radius: 0;\n  box-shadow: none;" in styles
    assert "@media (max-width: 1390px)" in styles
    intermediate = styles.split("@media (max-width: 1390px)", 1)[1].split("@media (max-width: 1120px)", 1)[0]
    assert ".tarkett-toolbar,\n  .offiho-toolbar" in intermediate
    assert "grid-template-columns: 1fr;" in intermediate
    assert ".tarkett-grid" in intermediate
    assert "text-align: left;" in intermediate
    assert ".product-actions {\n  min-width: 0;" in styles
