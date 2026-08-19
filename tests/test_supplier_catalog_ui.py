import re
import json
import socket
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.request import urlopen

import pytest


CATALOG_TABS = (
    ("tarkett", "Tarkett"),
    ("offiho", "Offiho"),
    ("cr-global", "CR Global"),
    ("sonara", "Sonara"),
    ("sunon", "Sunon"),
    ("alma", "ALMA"),
    ("lumbro", "Lumbro"),
    ("jome", "JOME"),
    ("lauco", "Lauco"),
    ("idelika", "IDÉLIKA"),
    ("conceptos", "Conceptos"),
)
SUPPLIER_VIEW_PROPS = (
    "supplier",
    "label",
    "request",
    "userId",
    "onAddCartLine",
    "onOpenCart",
    "cartLineCount",
    "cartBusy",
)


def _ascii_text(value):
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def _supplier_view_props(source):
    signature = re.search(
        r"(?:export\s+default\s+|export\s+)?function\s+SupplierCatalogView\s*"
        r"\(\s*\{(?P<props>[^}]*)\}\s*\)",
        source,
        re.DOTALL,
    )
    assert signature, "SupplierCatalogView must be a named component with destructured props"
    return tuple(
        part.strip().split("=", 1)[0].strip()
        for part in signature.group("props").split(",")
        if part.strip()
    )


def _sidebar_catalog_tabs(source):
    sidebar = re.search(
        r"function\s+Sidebar\b.*?const\s+items\s*=\s*\[(?P<items>.*?)\];",
        source,
        re.DOTALL,
    )
    assert sidebar, "Sidebar must keep an explicit, reviewable item list"
    entries = re.findall(
        r"\[\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']\s*,",
        sidebar.group("items"),
    )
    admin_index = entries.index(("admin", "Admin"))
    return tuple(entries[admin_index + 1 :])


def _has_css_rule(styles, selector_terms, declaration):
    normalized_declaration = re.sub(r"\s+", " ", declaration.strip()).lower()
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", styles):
        clean_selector = selector.lower()
        clean_body = re.sub(r"\s+", " ", body).lower()
        if all(term.lower() in clean_selector for term in selector_terms):
            if normalized_declaration in clean_body:
                return True
    return False


def _javascript_function(source, name):
    start = re.search(rf"function\s+{name}\s*\([^)]*\)\s*\{{", source)
    assert start, f"Missing JavaScript helper: {name}"
    depth = 0
    for index in range(start.start(), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start.start() : index + 1]
    raise AssertionError(f"Unclosed JavaScript helper: {name}")


def _run_javascript(script):
    completed = subprocess.run(
        ["node", "--input-type=module"],
        check=True,
        capture_output=True,
        input=script,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_supplier_catalog_ui_static_contracts_are_present():
    component_path = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx")
    assert component_path.is_file(), (
        "Task 14 requires mobiliti_saas/web/src/SupplierCatalogView.jsx before the "
        "shared supplier catalog UI contracts can pass"
    )

    component = component_path.read_text(encoding="utf-8")
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    combined = f"{main}\n{component}"
    visible_text = _ascii_text(component)
    compact_component = re.sub(r"\s+", " ", component)

    assert _sidebar_catalog_tabs(main) == CATALOG_TABS
    assert re.search(
        r"import\s+(?:\{\s*)?SupplierCatalogView(?:\s*\})?\s+from\s+"
        r"[\"']\./SupplierCatalogView(?:\.jsx)?[\"']",
        main,
    )
    assert main.count("<SupplierCatalogView") == 1
    assert _supplier_view_props(component) == SUPPLIER_VIEW_PROPS
    shared_view = re.search(r"<SupplierCatalogView\b(?P<props>[^>]*)>", main, re.DOTALL)
    assert shared_view
    assert re.search(r"\bkey\s*=\s*\{\s*view\s*\}", shared_view.group("props"))
    for prop in SUPPLIER_VIEW_PROPS:
        assert re.search(rf"\b{prop}\s*=", shared_view.group("props"))
    for duplicated_view in ("CRGlobalView", "SonaraView", "SunonView", "AlmaView"):
        assert f"function {duplicated_view}" not in main

    assert re.search(r"request\(\s*[\"']/catalogs[\"']", combined)
    assert re.search(
        r"request\(\s*`/catalogs/\$\{[^}]*supplier[^}]*\}(?:\?[^`]*)?`",
        component,
    )
    assert "/catalogs/exchange-rates" not in component
    assert "/catalogs/${supplier}/quote" not in component
    assert "onAddCartLine(createMixedCartLine" in component

    assert re.search(
        r"\b\w*CACHE_VERSION\b\s*=\s*(?:[\"']v?\d+[\"']|\d+)",
        component,
        re.IGNORECASE,
    )
    assert re.search(
        r"`(?=[^`]*(?:CACHE_VERSION|cacheVersion))(?=[^`]*userId)"
        r"(?=[^`]*supplier)(?=[^`]*(?:source_hash|sourceHash))[^`]+`",
        component,
        re.IGNORECASE,
    )
    assert "sessionStorage.getItem" in component
    assert "sessionStorage.setItem" in component
    assert "apartados pueden estar desactualizados" in visible_text.lower()

    for label in ("Buscar", "Marca", "Coleccion", "Disponibilidad"):
        assert label in visible_text
    for field in ("brand", "collection", "availability_type"):
        assert field in component
    assert re.search(r"\b[A-Z][A-Z0-9_]*PAGE_SIZE\s*=\s*24\b", component)
    assert ".slice(" in component
    assert "Pagina anterior" in visible_text
    assert "Pagina siguiente" in visible_text
    assert re.search(r"<img\b(?=[^>]*\bloading=[\"']lazy[\"'])[^>]*>", component, re.DOTALL)
    assert _has_css_rule(styles, ("supplier",), "object-fit: contain")
    assert _has_css_rule(styles, ("supplier-image-frame", "img"), "width: auto")
    assert _has_css_rule(styles, ("supplier-image-frame", "img"), "height: auto")
    assert _has_css_rule(styles, ("supplier-image-frame", "img"), "max-width: 100%")
    assert _has_css_rule(styles, ("supplier-image-frame", "img"), "max-height: 100%")
    assert _has_css_rule(
        styles,
        ("supplier-product-grid",),
        "grid-template-columns: repeat(auto-fit, minmax(min(100%, 440px), 1fr))",
    )

    for field in ("product_key", "attributes", "sku", "image_url"):
        assert field in component
    assert "attributes?.source_code" in component


def test_kundesign_fallback_link_is_disclosed_as_general_catalog():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    visible_text = _ascii_text(component)
    compact_component = re.sub(r"\s+", " ", component)

    assert 'product_url_match?.status === "catalog_fallback"' in component
    assert "Ver catalogo general" in visible_text
    assert "Ver producto" in visible_text
    assert re.search(r"\b(?:selected|active)\w*(?:variant|item)\w*\b", component, re.IGNORECASE)
    assert "matchingVariants" in component
    assert re.search(r"\b(?:set|select|change)\w*variant\w*\b", component, re.IGNORECASE)
    assert re.search(r"\bonChange\s*=", component)

    for field in (
        "base_price_options",
        "base_option_id",
        "add_on_options",
        "add_on_option_ids",
    ):
        assert field in component
    assert re.search(
        r"base_price_options[\s\S]{0,160}!configuration\.base_option_id",
        component,
        re.IGNORECASE,
    )
    assert re.search(
        r"(?:family[\s\S]{0,240}(?:filter|find|reduce|Map)|"
        r"(?:filter|find|reduce|Map)[\s\S]{0,240}family)",
        component,
        re.IGNORECASE,
    )

    for label in (
        "Precio neto",
        "mas IVA",
    ):
        assert label in visible_text

    for field in ("code_status", "availability_type", "image_kind", "reserved_by_others"):
        assert field in component
    for badge in (
        "Codigo por verificar",
        "Sobre pedido",
        "Imagen de referencia",
        "Imagen por verificar",
        "Apartado",
    ):
        assert badge in visible_text
    assert re.search(r"className\s*=.*badge", component, re.IGNORECASE)
    assert _has_css_rule(styles, ("supplier", "badge"), "display:")

    assert "stock" in component
    assert "reserved_quantity" in component
    assert "Existencia" in visible_text
    assert "Apartado" in visible_text
    assert "available_quantity" not in component
    assert not re.search(
        r"\b(?:item\.)?stock\s*-\s*(?:item\.)?reserved_quantity\b",
        compact_component,
    )

    assert "is_out_of_stock" in component
    assert "Agotado" in visible_text
    assert any(
        warning in visible_text.lower()
        for warning in ("advertencia", "verificar disponibilidad", "se puede cotizar", "se cotizara")
    )
    assert not re.search(r"disabled\s*=\s*\{[^}]*is_out_of_stock", component, re.DOTALL)

    assert "<FolderKanban" in component
    assert 'aria-label="Abrir proyecto"' in visible_text
    assert 'title="Abrir proyecto"' in visible_text
    assert "onOpenCart" in component
    assert "cartLineCount" in component
    assert "supplier-cart-drawer" not in component


def test_lumbro_link_labels_are_truthful_and_other_supplier_labels_are_unchanged():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    helper = _javascript_function(component, "productLinkLabel")
    cases = [
        ("lumbro", "exact_index"),
        ("lumbro", "collection_index"),
        ("lumbro", "catalog_fallback"),
        ("lumbro", ""),
        ("alma", "exact_index"),
        ("alma", "catalog_fallback"),
        ("sonara", "collection_index"),
        ("cr-global", "exact_code"),
    ]
    result = _run_javascript(
        f"{helper}\n"
        f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map(([supplier, status]) => "
        "productLinkLabel({attributes: {product_url_match: {status}}}, supplier))));"
    )

    assert result == [
        "Ver producto",
        "Ver catálogo Lumbro",
        "Ver catálogo Lumbro",
        "Ver catálogo Lumbro",
        "Ver producto",
        "Ver catálogo general",
        "Ver colección",
        "Ver producto",
    ]


def test_official_link_has_visible_adjacent_text_and_an_explanatory_accessible_name():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    link = re.search(
        r"\{item\.product_url\s*\?\s*\(\s*<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        component,
        re.DOTALL,
    )

    assert link, "Official link must remain conditional on the validated product_url"
    attrs = link.group("attrs")
    body = link.group("body")
    assert 'target="_blank"' in attrs
    assert re.search(r'rel="[^"]*(?:noreferrer|noopener)[^"]*"', attrs)
    assert re.search(r"aria-label=\{`[^`]*\$\{linkText\}[^`]*\$\{(?:item\.name|sourceCode\(item\))\}[^`]*`\}", attrs)
    assert re.search(r"<ExternalLink\b[^>]*/>\s*<span>\{linkText\}</span>", body, re.DOTALL)


def test_pza_quantities_are_validated_on_add_without_rewriting_input_state():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    constants = [
        re.search(rf"const\s+{name}\s*=.*?;", component).group(0)
        for name in ("QUANTITY_SCALE", "QUANTITY_LIMIT_MICROUNITS")
    ]
    helpers = constants + [
        _javascript_function(component, name)
        for name in ("isSquareMeterUnit", "quantityRules", "quantityMicrounits", "validQuantity")
    ]
    result = _run_javascript(
        f"{' '.join(helpers)}\n"
        "const pza = {unit: 'PZA'}; const m2 = {unit: 'M2'};"
        "console.log(JSON.stringify({"
        "pzaRules: quantityRules(pza), m2Rules: quantityRules(m2),"
        "validPza: ['1', '3'].map(value => validQuantity(pza, value)),"
        "invalidPza: ['2.75', '0', '-1', 'NaN', ''].map(value => validQuantity(pza, value)),"
        "m2Fraction: validQuantity(m2, '2.75')"
        "}));"
    )

    assert result == {
        "pzaRules": {"min": "1", "step": "1", "maxDecimals": 0, "max": "1000000", "integer": True},
        "m2Rules": {"min": "0.000001", "step": "0.000001", "maxDecimals": 6, "max": "1000000", "integer": False},
        "validPza": [True, True],
        "invalidPza": [False, False, False, False, False],
        "m2Fraction": True,
    }
    assert re.search(r"type=\"number\"[\s\S]{0,180}min=\{productQuantity\.min\}[\s\S]{0,100}step=\{productQuantity\.step\}", component)
    assert "quantityInputValue" not in component
    assert re.search(
        r"setQuantityByItem\(\(current\)\s*=>\s*\(\{[^}]*\[item\.internal_id\]:\s*event\.target\.value",
        component,
    )
    add_to_cart = _javascript_function(component, "addToCart")
    quantity_expression = re.search(
        r"const\s+quantity\s*=\s*(?P<expression>String\(quantityByItem\[item\.internal_id\].*?\.trim\(\))\s*;",
        add_to_cart,
    )
    assert quantity_expression
    resolved = _run_javascript(
        f"const resolve = (quantityByItem, item) => {quantity_expression.group('expression')};"
        "const item = {internal_id: 'product-1'};"
        "console.log(JSON.stringify([resolve({}, item), resolve({'product-1': ''}, item)]));"
    )
    assert resolved == ["1", ""]
    assert 'quantityByItem[item.internal_id] ?? "1"' in add_to_cart
    assert "if (!validQuantity(item, quantity))" in add_to_cart
    assert '"un número entero"' in add_to_cart
    assert "setSubmitError(`Captura ${requirement} para ${item.name}.`)" in add_to_cart
    assert "attributes?.dimensions" in component
    assert "Dimensiones" in _ascii_text(component)


def test_shared_supplier_lines_keep_configuration_and_delegate_commercial_quote():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    assert "visibleConfiguration(item, configuration)" in component
    assert "cartWarnings(item)" in component
    assert "createMixedCartLine" in component
    assert "toMixedQuoteItem" not in component
    assert "submitQuote" not in component
    visible_text = _ascii_text(component)
    assert "Descuento (%)" not in visible_text
    assert "Precio lista" not in visible_text


def test_shared_supplier_cards_render_dimensions_button_configurator_and_unit_aware_quantities():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    visible_text = _ascii_text(component)

    assert "attributes?.dimensions" in component
    assert "Dimensiones" in visible_text
    assert "aria-pressed" in component
    assert re.search(r"<button\b[^>]*aria-pressed", component, re.DOTALL)
    assert "Sin cojin" in visible_text
    assert "0.000001" in component
    assert re.search(r"(?:step|min)=\{[^}]*quantity", component, re.IGNORECASE)
    assert re.search(r"(?:step|min).*?[\"']1[\"']", component, re.DOTALL)
    assert _has_css_rule(styles, ("supplier-option-button",), "display:")
    assert "attributes?.warranty" in component
    assert "attributes?.product_notes" in component
    assert "Garantia" in visible_text
    assert "Notas" in visible_text
    assert "quantityMicrounits" in component
    assert "quantityFromMicrounits" in component
    assert not re.search(
        r"String\(\s*decimal\(line\.quantity\)\s*\+\s*decimal\(quantity\)\s*\)",
        component,
    )


def test_shared_supplier_cards_stack_readably_on_narrow_mobile_without_changing_desktop():
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    marker = "@media (max-width: 480px)"
    assert marker in styles
    narrow_start = styles.index(marker)
    narrow_end = styles.index("\n.catalog-admin", narrow_start)
    narrow = styles[narrow_start:narrow_end]

    assert _has_css_rule(narrow, ("supplier-product-main",), "grid-template-columns: 1fr")
    assert _has_css_rule(narrow, ("supplier-image-frame",), "width: 100%")
    assert _has_css_rule(narrow, ("supplier-image-frame",), "height: auto")
    assert _has_css_rule(narrow, ("supplier-image-frame",), "aspect-ratio: 4 / 3")
    assert _has_css_rule(narrow, ("supplier-card-footer",), "display: grid")
    assert _has_css_rule(narrow, ("supplier-card-footer",), "grid-template-columns: 1fr")
    assert _has_css_rule(narrow, ("supplier-card-footer", "label"), "width: 100%")
    assert _has_css_rule(narrow, ("supplier-card-footer", "primary-action"), "width: 100%")
    assert ".sidebar" not in narrow
    assert _has_css_rule(
        styles[:narrow_start],
        ("supplier-product-main",),
        "grid-template-columns: 180px minmax(0, 1fr)",
    )


def test_unknown_supplier_availability_is_disclosed_as_pending_confirmation():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    visible_text = _ascii_text(component)
    availability = re.search(
        r"function\s+availabilityLabel\([^)]*\)\s*\{(?P<body>.*?)\}",
        component,
        re.DOTALL,
    )

    assert availability
    assert "unknown" in availability.group("body")
    assert "Disponibilidad por confirmar" in visible_text
    assert re.search(
        r"<dt>Existencia</dt><dd>\{availabilityLabel\(item\)\}</dd>",
        component,
    )


def test_supplier_cards_fail_closed_when_price_or_currency_is_pending():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    visible_text = _ascii_text(component)

    assert "Precio por confirmar" in visible_text
    assert "source_price_printed" in component
    assert "Precio fuente (moneda por confirmar)" in visible_text
    assert "disabled={cartBusy || !canAddSupplierItem(item, supplier, configuredPrice)}" in component
    assert "formatConfiguredPrice" in component
    assert "Por confirmar" in visible_text
    assert "cartBusy" in component


def test_alma_supplier_ui_invalidates_legacy_cache_and_names_both_configuration_axes():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    picker = Path("mobiliti_saas/web/src/productPicker.js").read_text(encoding="utf-8")
    visible_text = _ascii_text(component)

    assert 'SUPPLIER_CACHE_VERSION = "v2"' in component
    assert "filterCatalogVariantGroups" in component
    assert "productVariantConfiguration" in component
    assert "productBaseConfigurationLabel" in component
    assert "Acabado / material del aro" in visible_text
    assert "Calidad de tela" in _ascii_text(picker)


def test_review_items_use_generic_quotable_contract_without_supplier_allowlist():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    helpers = "\n".join(
        _javascript_function(component, name)
        for name in ("decimal", "warningKey", "pendingPriceContract", "canAddSupplierItem")
    )
    result = _run_javascript(
        f"{helpers}\n"
        "const item = (code_status, price_net = '3253.00', base_currency = 'MXN', tax_rate = '0.16') => "
        "({code_status, price_net, base_currency, tax_rate, attributes: {quotable: true}, warnings: ['missing_code']});"
        "console.log(JSON.stringify(["
        "canAddSupplierItem(item('needs_review'), 'sonara', '3253.00'),"
        "canAddSupplierItem(item('needs_review'), 'lumbro', '3253.00'),"
        "canAddSupplierItem(item('needs_review'), 'cr-global', '3253.00'),"
        "canAddSupplierItem(item('verified'), 'sonara', '3253.00'),"
        "canAddSupplierItem(item('needs_review'), 'lumbro', '0'),"
        "canAddSupplierItem(item('needs_review', '3253.00', 'XXX'), 'lumbro', '3253.00')"
        ",canAddSupplierItem(item('needs_review', '3253.00', 'MXN', '0.08'), 'sonara', '3253.00')"
        "]));"
    )

    assert result == [True, True, True, True, False, False, False]


def test_supplier_cart_snapshot_deduplicates_review_warning_and_names_configuration():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    helpers = "\n".join(
        _javascript_function(component, name)
        for name in (
            "decimal", "nullableDecimal", "configuredBasePrice",
            "warningKey", "pendingPriceContract", "initialConfiguration",
            "cartWarnings", "visibleConfiguration",
        )
    )
    result = _run_javascript(
        f"{helpers}\n"
        "const item = {code_status: 'needs_review', warnings: ["
        "'Código por verificar', ' codigo por VERIFICAR ', 'Evidencia detallada'], "
        "base_price_options: [{id: 'base-a', name: 'Roble'}], "
        "add_on_options: [{id: 'addon-b', name: 'Pasacables'}, {id: 'addon-a', name: 'Electrificacion'}]};"
        "const warnings = cartWarnings(item);"
        "console.log(JSON.stringify({warnings, reviewCount: warnings.filter(value => "
        "warningKey(value) === 'codigo por verificar').length, configuration: "
        "visibleConfiguration(item, {base_option_id: 'base-a', add_on_option_ids: ['addon-a']})}));"
    )

    assert result == {
        "warnings": ["Código por verificar", "Evidencia detallada"],
        "reviewCount": 1,
        "configuration": "Roble + Electrificacion",
    }


def test_supplier_cart_snapshot_derives_generated_reference_warning_once():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    helpers = "\n".join(
        _javascript_function(component, name)
        for name in (
            "decimal", "nullableDecimal", "configuredBasePrice",
            "warningKey", "pendingPriceContract", "initialConfiguration",
            "cartWarnings",
        )
    )
    result = _run_javascript(
        f"{helpers}\n"
        "const derive = cartWarnings({code_status: 'verified', image_kind: "
        "'generated_reference', warnings: ['Entrega especial']});"
        "const deduplicated = cartWarnings({code_status: 'verified', image_kind: "
        "'generated_reference', warnings: [' imagen DE referencia ', "
        "'Entrega especial']});"
        "console.log(JSON.stringify({derive, deduplicated, generatedCount: "
        "deduplicated.filter(value => warningKey(value) === 'imagen de referencia').length}));"
    )

    assert result == {
        "derive": ["Entrega especial", "Imagen de referencia"],
        "deduplicated": ["Imagen de referencia", "Entrega especial"],
        "generatedCount": 1,
    }


def test_supplier_quantity_rules_confirm_stocked_and_out_of_stock_lines():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    helpers = "\n".join(
        _javascript_function(component, name)
        for name in ("isSquareMeterUnit", "quantityRules")
    )
    result = _run_javascript(
        f"{helpers}\n"
        "console.log(JSON.stringify(["
        "quantityRules({unit: 'PZA', availability_type: 'stocked', stock: '8'}),"
        "quantityRules({unit: 'PZA', availability_type: 'unknown', is_out_of_stock: true}),"
        "quantityRules({unit: 'M2', availability_type: 'made_to_order'})"
        "]));"
    )

    assert result == [
        {
            "min": "1", "step": "1", "maxDecimals": 0, "max": "1000000",
            "integer": True, "warningAt": "8", "confirmOnInsufficient": True,
        },
        {
            "min": "1", "step": "1", "maxDecimals": 0, "max": "1000000",
            "integer": True, "warningAt": "0", "confirmOnInsufficient": True,
        },
        {
            "min": "0.000001", "step": "0.000001", "maxDecimals": 6,
            "max": "1000000", "integer": False,
        },
    ]


def test_verified_variant_sku_is_shown_before_a_generic_model_code():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    source_code = re.search(
        r"function\s+sourceCode\([^)]*\)\s*\{(?P<body>.*?)\}",
        component,
        re.DOTALL,
    )

    assert source_code
    body = source_code.group("body")
    assert "code_status" in body
    assert body.index("item.sku") < body.index("source_model_code")


def test_supplier_view_does_not_request_or_compute_exchange_rates():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    assert "/catalogs/exchange-rates" not in component
    assert "exchange_rate" not in component
    assert "supplierCartTotals" not in component


def test_sunon_cards_show_source_codes_color_delivery_buckets_and_variant_counts():
    component = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    visible_text = _ascii_text(component)

    source_code = re.search(r"function\s+sourceCode\([^)]*\)\s*\{(?P<body>.*?)\}", component, re.DOTALL)
    assert source_code
    for fallback in ("source_code", "source_erp_code", "source_model_code", "sku"):
        assert fallback in source_code.group("body")
    for field in ("color", "lead_time", "availability_buckets"):
        assert field in component
    for label in ("Color", "Entrega", "Disponibilidad por plazo", "productos", "variantes"):
        assert label in visible_text
    assert "bucket.quantity" in component
    assert "bucket.lead_time" in component
    assert "availabilityByLeadTime" in component
    assert "new Map" in component
    assert "matchingVariants.length" in component
    assert _has_css_rule(styles, ("supplier-availability-buckets",), "display:")


def test_catalog_admin_panel_static_contracts_are_present():
    panel_path = Path("mobiliti_saas/web/src/CatalogAdminPanel.jsx")
    assert panel_path.is_file(), "Task 15 requires the catalog administration panel"

    panel = panel_path.read_text(encoding="utf-8")
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    styles = Path("mobiliti_saas/web/src/styles.css").read_text(encoding="utf-8")
    visible_text = _ascii_text(panel)

    assert re.search(r"import\s+CatalogAdminPanel\s+from\s+[\"']\./CatalogAdminPanel", main)
    assert "session.usuario?.es_admin" in main
    assert "<CatalogAdminPanel" in main
    assert re.search(r"\(view === \"admin\" \|\| view === \"clientes\"\) && isAdmin", main)
    for endpoint in (
        "/admin/catalog-sync-runs",
        "/admin/catalog-sync/${supplier}",
        "/admin/catalog-sync-runs/${runId}",
    ):
        assert endpoint in panel
    assert "/admin/catalog-sync-runs/${selected.id}/${action}" in panel
    assert "/admin/catalog-sync-runs/${selected.id}/images" in panel
    assert 'review("approve")' in panel
    assert 'review("reject")' in panel
    for label in ("Sincronizar ahora", "Aprobar", "Rechazar", "Adjuntar imagen aprobada"):
        assert label in visible_text
    assert "window.confirm" in panel
    assert "FormData" in panel
    for field in ("image_kind", "image_label", "image_references", "metrics", "diff", "source_reference", "material_type"):
        assert field in panel
    assert "aria-busy" in panel
    assert re.search(r"disabled\s*=", panel)
    assert _has_css_rule(styles, ("catalog-admin",), "display: grid")


def test_catalog_admin_exposes_idelika_and_conceptos_after_lauco():
    panel = Path("mobiliti_saas/web/src/CatalogAdminPanel.jsx").read_text(encoding="utf-8")
    suppliers = re.findall(r'\["([^"]+)", "([^"]+)"\]', panel)

    assert suppliers[-3:] == [
        ("lauco", "Lauco"),
        ("idelika", "IDÉLIKA"),
        ("conceptos", "Conceptos"),
    ]


def test_catalog_admin_panel_resets_curation_state_when_switching_runs(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    web_root = Path("mobiliti_saas/web").resolve()
    node = web_root / "node_modules" / "vite" / "bin" / "vite.js"
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.is_file():
        pytest.skip("System Chrome is required for the local interaction test")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        ["node", str(node), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=web_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}", timeout=1):
                    break
            except OSError:
                if time.time() >= deadline:
                    pytest.fail("Vite did not start for the interaction test")
                time.sleep(0.1)

        runs = [
            {"id": "run-1", "label": "Run Uno", "status": "awaiting_approval", "requested_at": "2026-07-16T10:00:00Z"},
            {"id": "run-2", "label": "Run Dos", "status": "awaiting_approval", "requested_at": "2026-07-16T11:00:00Z"},
        ]
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=str(chrome), headless=True)
            page = browser.new_page()
            page.add_init_script(
                "localStorage.setItem('mobiliti_session', JSON.stringify(%s))"
                % json.dumps({
                    "access_token": "test-token",
                    "usuario": {"id": 7, "email": "admin@example.test", "es_admin": True},
                    "suscripcion": {"estado": "activa", "fecha_fin": "2099-01-01T00:00:00Z"},
                })
            )

            def fulfill(route):
                path = route.request.url.split("?", 1)[0]
                if path.endswith("/cotizaciones"):
                    body = {"cotizaciones": []}
                elif path.endswith("/admin/usuarios") or path.endswith("/admin/suscripciones"):
                    body = []
                elif path.endswith("/admin/catalog-sync-runs"):
                    body = {"runs": runs}
                elif path.endswith("/admin/catalog-sync-runs/run-1"):
                    body = {"run": {**runs[0], "metrics": {}, "diff": {"items": []}}}
                elif path.endswith("/admin/catalog-sync-runs/run-2"):
                    body = {"run": {**runs[1], "metrics": {}, "diff": {"items": []}}}
                else:
                    body = {}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

            page.route("http://127.0.0.1:8000/**", fulfill)
            page.goto(f"http://127.0.0.1:{port}")
            page.get_by_role("button", name=re.compile(r"^Admin")).click()
            page.get_by_role("button", name=re.compile(r"Run Uno")).click()
            page.get_by_label("Nota de revision (opcional para aprobar)").fill("nota del primer run")
            page.get_by_label("Indice del producto").fill("7")
            page.get_by_label("Tipo de imagen").select_option("generated_reference")
            page.get_by_label("Etiqueta").fill("Referencia uno")
            page.get_by_label("Referencias HTTPS").fill("https://example.test/producto")
            page.get_by_label("Archivo de imagen").set_input_files({
                "name": "sample.png",
                "mimeType": "image/png",
                "buffer": b"\x89PNG\r\n\x1a\n",
            })

            page.get_by_role("button", name=re.compile(r"Run Dos")).click()

            assert page.get_by_label("Nota de revision (opcional para aprobar)").input_value() == ""
            assert page.get_by_label("Indice del producto").input_value() == "0"
            assert page.get_by_label("Tipo de imagen").input_value() == "official"
            assert page.get_by_label("Archivo de imagen").input_value() == ""
            assert page.get_by_label("Etiqueta").count() == 0
            assert page.get_by_label("Referencias HTTPS").count() == 0
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
