# Carrito global y cotización mixta de catálogos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que Tarkett, Offiho, CR Global, Sonara, Sunon, ALMA y Lumbro compartan un carrito y produzcan un solo trabajo y un solo Excel, corrigiendo además la moneda MXN y la cotización de códigos por verificar de Sonara.

**Architecture:** Elevar el carrito y el formulario a `App`, enviar únicamente identidades autoritativas a un endpoint aditivo `POST /catalogs/mixed-quote` y normalizar los tres contratos existentes en `mixed_catalog_cart`. Una RPC única reserva atómicamente las tres familias de inventario; el worker crea una sola hoja `Quotation` enriquecida y ejecuta una sola vez el motor Excel actual, que leerá proveedor, descuento, modo de precio, tasa congelada y política de electrificación por línea.

**Tech Stack:** Python 3.14, FastAPI, pytest, `Decimal`, Supabase PostgreSQL/REST, React 19, Vite 7, Node.js, Playwright, openpyxl y el motor `mobiliti_saas.quote_engine` existente.

## Global Constraints

- Trabajar únicamente en `C:\Users\pepem\Downloads\ARMADO_DE_CARATULA_prod_git_worktree`, rama `codex/offiho-catalog-20260709`.
- El worktree ya contiene dos bases aprobadas que esta entrega adopta: cotización Lumbro `needs_review` en los dos `supplier_catalog.py` y sus tres pruebas (`tests/test_lumbro_catalog_e2e.py`, `tests/test_quote_jobs_api.py`, `tests/test_supplier_catalog.py`), cuyo diff inicial tiene hash `608063cca3e1def3a332e6fdc710e7262b3948b6`; y alta Lumbro/calidad `object-fit` en `SupplierCatalogView.jsx`, `styles.css` y `tests/test_supplier_catalog_ui.py`, hash `738b3ae0f20aae1289e7b1b9fb96e0c4e4928d0b`. Task 1 y Task 9 verifican y absorben respectivamente esos hunks antes de añadir Sonara/carrito global.
- Los cambios en `.superpowers/sdd/` y `.playwright-mcp/` no pertenecen a esta entrega y nunca se preparan. Antes de editar cualquier otra ruta, revisar `git status --short` y `git diff -- <ruta>`; no sobrescribir hunks no enumerados arriba.
- Nunca usar `git add -A`, `git add -p`, `git clean`, `git reset`, `git checkout`, `git restore` ni eliminación permanente. Ejecutar las tareas en orden y preparar cada commit con las rutas completas explícitas que indica su paso final; revisar `git diff --cached --name-only`, `git diff --cached --check` y `git diff --cached`.
- Mantener byte-idénticas las tres copias de API: `mobiliti_saas/api/index.py`, `mobiliti_saas/web/api/index.py` y `vercel_deploy/api/index.py`.
- Mantener byte-idénticas las copias raíz/web de `supplier_catalog.py`, `catalog_cart.py` y el nuevo `mixed_catalog.py`.
- No añadir dependencias, Context de React, persistencia en `localStorage`/`sessionStorage`, un segundo generador Excel ni una plantilla nueva.
- Seguir TDD estricto en cada tarea: prueba roja, fallo observado, cambio mínimo, prueba verde, regresión focalizada y commit pequeño.
- El navegador envía únicamente el discriminador `catalog` permitido, identidad/configuración y cantidad; nunca envía precio, moneda base, tasa, impuesto, stock, proveedor comercial, nombre, imagen, URL, advertencia ni referencia de fuente. El backend los reconstruye desde el catálogo vigente.
- Aceptar entre 1 y 500 líneas, como máximo siete grupos y una sola línea por clave canónica; conservar además los límites más estrictos de cada constructor existente.
- Moneda final permitida: `MXN`, `USD` o `EUR`; valor inicial `MXN`. Monedas base: Tarkett/Offiho/CR Global/Sonara/Lumbro `MXN`, Sunon/ALMA `USD`.
- IVA único admitido: `0.160000`. Cualquier otra tasa aborta todo el checkout antes de encolar un trabajo.
- Tarkett y Offiho conservan descuento comercial por línea, inicialmente 40 %. Los cinco proveedores genéricos usan precio neto y descuento `0`.
- El Excel recibe precios ya convertidos a la moneda final; el motor registra `<quote_currency>/<quote_currency>` y tasa `1` para impedir una segunda conversión.
- Conservar `/tarkett/quote`, `/offiho/quote`, `/catalogs/{supplier}/quote` y los `source_type` `tarkett_cart`, `offiho_cart` y `supplier_cart` sin cambios contractuales.
- No aplicar migraciones, publicar el candidato Sonara, cargar assets, sincronizar SharePoint ni desplegar Vercel/Supabase/producción sin una autorización separada.
- Las fuentes locales reales de Sonara, si existen, permanecen ignoradas bajo `.cache/catalog-sources/sonara/`; nunca se agregan a Git.

---

## File and interface map

| Unidad | Archivos | Responsabilidad |
|---|---|---|
| Regla Sonara | `mobiliti_saas/worker/catalog_sync/importers/sonara.py` | Resolver moneda explícita, override MXN auditable y rechazo cerrado. |
| Carrito genérico | dos copias de `supplier_catalog.py` | Permitir `needs_review` únicamente para Sonara/Lumbro bajo datos comerciales válidos. |
| Dominio mixto | dos copias nuevas de `mixed_catalog.py` | Validar entrada estricta, agrupar, llamar los tres builders, congelar FX y producir grupos de reserva. |
| Reservas | nueva migración, `create_tables.sql`, tres copias de API | Reservar/liberar Tarkett, Offiho y proveedores en una sola transacción o sección crítica DEV. |
| Endpoint | tres copias de API | Crear un job, un input JSON y una respuesta, con compensación completa ante fallo. |
| Quotation | dos copias de `catalog_cart.py` y `mixed_catalog.py` | Escribir una sola hoja con secciones por proveedor, columnas L–S e imágenes por allowlist. |
| Parser/motor | `parser.py`, `engine.py` | Consumir campos mixtos por línea manteniendo defaults legados. |
| Worker | `quote_worker.py` | Convertir `mixed_catalog_cart` a `quotation_from_mixed_catalog.xlsx` y ejecutar una vez el generador. |
| Estado UI | `mixedCart.js`, `MixedCartDrawer.jsx`, `main.jsx`, `SupplierCatalogView.jsx`, `styles.css` | Mantener una sola cesta/formulario durante navegación y enviar una solicitud mínima. |
| Aceptación | pruebas Python/Node/Playwright y `scripts/verify-saas.ps1` | Probar atomicidad, seguridad, Excel único, regresiones, escritorio y móvil. |

Las interfaces fijadas para las tareas posteriores son `MIXED_CATALOG_CART_SOURCE_TYPE = "mixed_catalog_cart"`, `build_mixed_catalog_cart_payload(raw_items: list[dict[str, object]], *, catalogs: dict[str, dict], rate_rows: list[dict], quote_currency: str, commercial_discount_percent: object, today: date | None = None) -> dict`, `build_mixed_reservation_groups(payload: dict) -> list[dict]` y `create_mixed_catalog_quotation_workbook(payload: dict, output_path: str | Path, *, image_dir: str | Path | None = None) -> Path`. Cada tarea que crea una de ellas incluye abajo el contrato ejecutable que fija su comportamiento.

---

### Task 1: Make Sonara MXN auditable and safely quotable

**Files:**

- Modify: `mobiliti_saas/worker/catalog_sync/importers/sonara.py:256-325,628-726`
- Modify: `mobiliti_saas/quote_engine/supplier_catalog.py:17-24,161-223`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py:17-24,161-223`
- Modify: `tests/test_catalog_importers_sonara.py:194-238,396-467`
- Modify: `tests/test_lumbro_catalog_e2e.py:408-422` (adopted Lumbro baseline)
- Modify: `tests/test_supplier_catalog.py`
- Modify: `tests/test_quote_jobs_api.py`

**Interfaces:**

- Consumes: `build_sonara_snapshot(files) -> dict`, `build_supplier_cart_payload(raw_items, catalog, quote_currency, rate_rows) -> dict`.
- Produces: `_SONARA_MXN_RULE = "sonara_mxn_confirmed_2026-07-19"`; `attributes.source_currency_status` in `verified|business_override|rejected`; optional `attributes.source_currency_rule`; `REVIEW_QUOTABLE_SUPPLIERS = frozenset({"lumbro", "sonara"})`; `EXPECTED_SUPPLIER_BASE_CURRENCY` for all five generic catalogs.
- Guarantees: only the SHA-256-confirmed Sonara 2026 price list may use the missing-declaration MXN override; explicit/broad USD/EUR evidence, conflicts or an unrecognized missing-currency file yield `XXX/0`; every Sonara item requires MXN and a review item additionally requires positive price and IVA 16 %.

- [ ] **Step 1: Replace the old missing-currency expectation with red business-rule tests**

Before editing, verify the adopted Lumbro backend baseline exactly; a mismatch means inspect and preserve the new hunks before continuing:

```powershell
$adopted = @('mobiliti_saas/quote_engine/supplier_catalog.py','mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py','tests/test_lumbro_catalog_e2e.py','tests/test_quote_jobs_api.py','tests/test_supplier_catalog.py')
$baselineHash = git diff --binary -- $adopted | git hash-object --stdin
if ($baselineHash -ne '608063cca3e1def3a332e6fdc710e7262b3948b6') { throw "La base Lumbro aprobada cambio: $baselineHash" }
```

Add these cases to `tests/test_catalog_importers_sonara.py` using the existing `_write_price_pdf`, `_write_catalog_pdf`, `_bundle` and `source_bundle` helpers. Import `hashlib` and the importer module as `sonara` in addition to the existing function import:

```python
def test_missing_currency_uses_auditable_mxn_business_override(source_bundle, tmp_path, monkeypatch):
    price_list = tmp_path / "missing-currency.pdf"
    _write_price_pdf(
        price_list,
        [("PANEL 01", "Panel acustico 60 x 120 cm", "1880.00")],
        currency="Lista vigente marzo 2026",
    )
    monkeypatch.setattr(
        sonara,
        "_SONARA_PRICE_SHA256",
        hashlib.sha256(price_list.read_bytes()).hexdigest(),
    )
    item = build_sonara_snapshot(_bundle(price_list, source_bundle[0].local_path))["items"][0]
    assert item["base_currency"] == "MXN"
    assert item["price_net"] == "1880.000000"
    assert item["tax_rate"] == "0.160000"
    assert item["attributes"]["source_currency_status"] == "business_override"
    assert item["attributes"]["source_currency_rule"] == "sonara_mxn_confirmed_2026-07-19"


@pytest.mark.parametrize(
    "declaration",
    (
        "Moneda: USD", "Moneda: EUR", "Moneda: MXN / Moneda: USD",
        "Precios USD", "US$ 1,880.00", "Precios en €",
    ),
)
def test_foreign_or_contradictory_currency_fails_closed(source_bundle, tmp_path, declaration):
    price_list = tmp_path / "rejected-currency.pdf"
    _write_price_pdf(
        price_list,
        [("PANEL 01", "Panel acustico 60 x 120 cm", "1880.00")],
        currency=declaration,
    )
    item = build_sonara_snapshot(_bundle(price_list, source_bundle[0].local_path))["items"][0]
    assert item["base_currency"] == "XXX"
    assert item["price_net"] == "0.000000"
    assert item["attributes"]["source_currency_status"] == "rejected"
    assert any("moneda" in warning.lower() for warning in item["warnings"])


def test_unrecognized_missing_currency_file_fails_closed(source_bundle, tmp_path):
    price_list = tmp_path / "untrusted-missing-currency.pdf"
    _write_price_pdf(
        price_list,
        [("PANEL 01", "Panel acustico 60 x 120 cm", "1880.00")],
        currency="Lista sin moneda",
    )
    item = build_sonara_snapshot(_bundle(price_list, source_bundle[0].local_path))["items"][0]
    assert item["base_currency"] == "XXX"
    assert item["price_net"] == "0.000000"
    assert item["attributes"]["source_currency_status"] == "rejected"
```

In the existing `test_conflicting_prices_and_missing_currency_are_blocked`, remove the old missing-currency `XXX/0` subcase and retain only the genuinely conflicting-price assertion. Update `test_currency_and_iva_require_contextual_explicit_declarations` so a document with no `Moneda:` declaration but without “mas IVA” proves the independent tax failure:

```python
assert item["base_currency"] == "MXN"
assert item["attributes"]["source_currency_status"] == "business_override"
assert item["price_net"] == "0.000000"
assert item["tax_rate"] == "0.000000"
assert not any("moneda" in warning.lower() for warning in item["warnings"])
assert any("iva" in warning.lower() for warning in item["warnings"])
```

For the independent missing-IVA test, monkeypatch `_SONARA_PRICE_SHA256` to that synthetic price PDF's actual SHA before building, so it exercises `business_override` plus tax rejection rather than the untrusted-source rejection. In `test_snapshot_contract_coordinates_authority_tax_and_determinism`, whose source explicitly declares MXN, add:

```python
assert celosia["attributes"]["source_currency_status"] == "verified"
assert "source_currency_rule" not in celosia["attributes"]
```

- [ ] **Step 2: Run the Sonara currency cases and observe the old fail-closed behavior**

Run:

```powershell
python -m pytest tests/test_catalog_importers_sonara.py -k "missing_currency or foreign_or_contradictory or contextual_explicit" -q
```

Expected: FAIL because missing declaration still returns `base_currency == "XXX"`, `price_net == "0.000000"`, and no rule ID.

- [ ] **Step 3: Implement the three-state currency decision in the importer**

Add near the other module constants and replace the currency block in `_price_rows`:

```python
_SONARA_MXN_RULE = "sonara_mxn_confirmed_2026-07-19"


_FOREIGN_CURRENCY_EVIDENCE = re.compile(
    r"(?i)(?:\bUSD\b|\bEUR\b|\bUS\s*\$|€)"
)


def _source_currency(
    currencies: set[str],
    full_text: str,
    source_sha256: str,
    confirmed_price_sha256: str,
) -> tuple[str | None, str]:
    if _FOREIGN_CURRENCY_EVIDENCE.search(full_text) or currencies - {"MXN"}:
        return None, "rejected"
    if currencies == {"MXN"}:
        return "MXN", "verified"
    if not currencies and source_sha256 == confirmed_price_sha256:
        return "MXN", "business_override"
    return None, "rejected"
```

```python
def _price_rows(row, data, confirmed_price_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", confirmed_price_sha256):
        raise ValueError("SONARA_PRICE_HASH")
    source_sha256 = hashlib.sha256(data).hexdigest()
    document = fitz.open(stream=data, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
        currencies = {
            match.group(1).upper()
            for match in re.finditer(
                r"(?i)\bmoneda\s*:?\s*(MXN|USD|EUR)\b", full_text
            )
        }
        currency, currency_status = _source_currency(
            currencies, full_text, source_sha256, confirmed_price_sha256
        )
        plus_iva = bool(re.search(r"(?i)\bm[aá]s\s+IVA\b", full_text))
    finally:
        document.close()
```

Insert the existing `for page_number, page in enumerate(document, 1):` geometry loop immediately after `plus_iva` at its current indentation without changing its bbox/column/price extraction statements. Inside only its `records.append` dictionary, replace the old `currency`, `currency_ok` and `plus_iva` entries with the four exact entries below; no other geometry line moves.

The geometry parser runs under `multiprocessing.get_context("spawn")`, where a parent-process monkeypatch of a module constant is not inherited. Therefore thread the confirmed hash explicitly as immutable serializable input: `_build_sonara` calls `_parse_sonara_documents_isolated(price_data, catalog_data, include_assets, _SONARA_PRICE_SHA256)`; that function validates a 64-lowercase-hex string and passes it in the `Process(args=(control, output, price_data, catalog_data, include_assets, confirmed_price_sha256))`; `_geometry_worker(control, output, price_data, catalog_data, include_assets, confirmed_price_sha256)` calls `_price_rows(None, price_data, confirmed_price_sha256)`. The worker computes the actual SHA-256 from the exact validated bytes as above; never trust a parent-supplied actual hash and never dereference `row.sha256` because `row` is intentionally `None` in the child.

Every `_price_rows` record must contain these exact fields:

```python
"currency": currency,
"currency_ok": currency == "MXN",
"currency_status": currency_status,
"plus_iva": plus_iva,
```

In `_item`, build the attributes and rejection warning with this exact branch:

```python
attributes = {
    "row_description": record["description"],
    "source_price_printed": f"$ {record['price']:,.2f}",
    "source_currency_status": record["currency_status"],
}
if record["currency_status"] == "business_override":
    attributes["source_currency_rule"] = _SONARA_MXN_RULE
if record["currency_status"] == "rejected":
    price = Decimal(0)
    warnings.append("Moneda no confirmada, extranjera o contradictoria; verificar precio.")
```

Replace the old `if not record["currency_ok"]` warning branch with the `currency_status == "rejected"` branch above; do not keep both. After code status is resolved, ensure every review line carries one canonical warning in addition to any detailed evidence:

```python
if code_status == "needs_review" and "Codigo por verificar" not in warnings:
    warnings.append("Codigo por verificar")
```

Keep the independent IVA branch exactly as follows, so missing “más IVA” still fails closed even when the currency override is valid:

```python
if not record["plus_iva"]:
    price = Decimal(0)
    warnings.append("Tratamiento de IVA no declarado explicitamente; requiere revision.")
```

Update `test_pdf_geometry_parser_is_isolated_and_consumes_validated_bytes` so its audited wrapper accepts the fourth `confirmed_price_sha256` argument and asserts it equals the parent constant. The synthetic missing-currency test must execute the real spawn path after monkeypatching `_SONARA_PRICE_SHA256` to the generated PDF hash; success proves the expected hash crossed the process boundary, while changing one byte or passing a different confirmed hash must yield the rejected currency state rather than `SONARA_PDF_INVALID`.

- [ ] **Step 4: Update the real-source contract to require all 39 prices in MXN**

In `test_ignored_real_sources_reconcile_only_exact_sacc_variants_and_assets`, replace the old `XXX/0` assertion with:

```python
assert len(items) == 39
assert all(item["base_currency"] == "MXN" for item in items)
assert all(item["price_net"] != "0.000000" for item in items)
assert all(
    item["attributes"]["source_currency_status"] == "business_override"
    for item in items
)
assert all(
    item["attributes"]["source_currency_rule"] == "sonara_mxn_confirmed_2026-07-19"
    for item in items
)
assert sum(item["code_status"] == "needs_review" for item in items) == 32
```

In `test_ignored_real_sources_pass_contract_and_report_metrics`, keep both existing SHA-256 assertions and add:

```python
assert metrics["rows"] == 39
assert metrics["nonzero_prices"] == 39
assert metrics["blocked_prices"] == 0
assert metrics["verified_codes"] == 7
assert metrics["needs_review"] == 32
assert metrics["currency_warnings"] == 0
```

- [ ] **Step 5: Add red supplier-cart tests for the narrow review exception**

Add to `tests/test_supplier_catalog.py`, reusing its catalog fixture helper:

```python
def test_sonara_cart_accepts_review_item_with_positive_mxn_price_and_warning():
    payload = catalog_payload(supplier="sonara")
    item = payload["items"][0]
    item.update(
        internal_id="sonara:review-panel",
        supplier="sonara",
        sku="",
        code_status="needs_review",
        base_currency="MXN",
        price_net="77.000000",
        tax_rate="0.160000",
        base_price_options=[],
        add_on_options=[],
        warnings=["Codigo por verificar"],
    )
    cart = build_supplier_cart_payload(
        [{"internal_id": item["internal_id"], "quantity": "2", "add_on_option_ids": []}],
        payload,
        "MXN",
        [],
    )
    line = cart["items"][0]
    assert line["code_status"] == "needs_review"
    assert line["sku"] == ""
    assert line["unit_price"] == "77.00"
    assert line["tax_rate"] == "0.160000"
    assert "Codigo por verificar" in line["warnings"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("price_net", "0.000000", "precio por confirmar"),
        ("base_currency", "XXX", "moneda base por verificar"),
        ("base_currency", "USD", "moneda base por verificar"),
        ("tax_rate", "0.080000", "IVA 16"),
    ),
)
def test_sonara_review_item_fails_closed_without_valid_commercial_data(field, value, message):
    payload = catalog_payload(supplier="sonara")
    item = payload["items"][0]
    item.update(
        internal_id="sonara:review-panel",
        supplier="sonara",
        sku="",
        code_status="needs_review",
        base_currency="MXN",
        price_net="77.000000",
        tax_rate="0.160000",
        base_price_options=[],
        add_on_options=[],
    )
    item[field] = value
    with pytest.raises(ValueError, match=message):
        build_supplier_cart_payload(
            [{"internal_id": item["internal_id"], "quantity": "1", "add_on_option_ids": []}],
            payload,
            "MXN",
            [],
        )
```

Keep `test_build_cart_rejects_review_codes` green for ALMA and add the same assertion with a CR Global fixture, proving the allowlist did not expand beyond Sonara/Lumbro.

Add a seam test in `tests/test_catalog_importers_sonara.py` that builds a local snapshot with `_write_price_pdf`/`_write_catalog_pdf`, selects a real `needs_review` item from `build_sonara_snapshot`, then calls `build_supplier_cart_payload([{"internal_id": item["internal_id"], "quantity": "1", "add_on_option_ids": []}], snapshot, "MXN", [])`. Assert the returned line has its unchanged positive price, empty SKU, `code_status == "needs_review"`, preserves the importer's detailed evidence warning and contains exactly one normalized warning whose key is `codigo por verificar`, with the emitted value exactly `Codigo por verificar`. Extend the existing local Lumbro end-to-end fixture in `tests/test_lumbro_catalog_e2e.py` through the same builder and make the same exact-one canonical assertion while preserving Lumbro's detailed missing-code warning. In `tests/test_supplier_catalog.py`, add one `needs_review` fixture whose input contains both `Código por verificar` and ` codigo por VERIFICAR ` and assert the output replaces them with one exact canonical value; add a verified fixture and assert no canonical warning is injected and its distinct warning list is unchanged. These seams prevent a manually decorated catalog fixture from hiding importer/builder drift.

- [ ] **Step 6: Run the review tests and observe Sonara rejection**

Run:

```powershell
python -m pytest tests/test_supplier_catalog.py -k "sonara_cart or sonara_review or review_codes" -q
```

Expected: FAIL with `codigo por verificar; el producto no se puede cotizar` for the valid Sonara case.

- [ ] **Step 7: Restrict review quoting to Sonara and Lumbro**

Add and use this constant in both `supplier_catalog.py` copies:

```python
REVIEW_QUOTABLE_SUPPLIERS = frozenset({"lumbro", "sonara"})
EXPECTED_SUPPLIER_BASE_CURRENCY = {
    "cr-global": "MXN", "sonara": "MXN", "sunon": "USD", "alma": "USD", "lumbro": "MXN",
}
```

The module already imports `unicodedata`; keep that import and add this stable warning merge beside the cart-line helpers. It preserves detailed evidence, deduplicates exact normalized messages and always replaces accent/case/whitespace variants of the review category with one exact canonical value:

```python
def _normalized_supplier_warning(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_marks = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def _supplier_line_warnings(item: dict[str, Any]) -> list[str]:
    canonical = "Codigo por verificar"
    canonical_key = _normalized_supplier_warning(canonical)
    review_line = item["code_status"] == "needs_review"
    result: list[str] = []
    seen: set[str] = set()
    for raw_warning in item["warnings"]:
        warning = str(raw_warning).strip()
        key = _normalized_supplier_warning(warning)
        if not warning or not key:
            continue
        if review_line and key == canonical_key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(warning)
    if review_line:
        if len(result) >= MAX_WARNINGS_PER_ITEM:
            raise ValueError("Se excede el limite de warnings al agregar codigo por verificar")
        result.append(canonical)
    return result
```

In `_cart_line`, replace only `"warnings": list(item["warnings"])` with:

```python
"warnings": _supplier_line_warnings(item),
```

Immediately after the authoritative item lookup, enforce the expected currency for verified and review lines alike, then replace the current Lumbro-only condition:

```python
expected_currency = EXPECTED_SUPPLIER_BASE_CURRENCY[loaded["supplier"]]
if item["base_currency"] != expected_currency:
    raise ValueError("moneda base por verificar; el producto no se puede cotizar")

if item["code_status"] != "verified":
    if loaded["supplier"] not in REVIEW_QUOTABLE_SUPPLIERS:
        raise ValueError("codigo por verificar; el producto no se puede cotizar")
    if item["base_currency"] != "MXN":
        raise ValueError("moneda base por verificar; el producto no se puede cotizar")
    if Decimal(item["tax_rate"]) != Decimal("0.160000"):
        raise ValueError("IVA 16% requerido para codigo por verificar")
```

Add a verified Sonara-in-USD test and assert the same closed error, proving this is not only a `needs_review` rule. Leave the common currency and positive configured-price checks in place. Copy the finished root module byte-for-byte to the web package through `Copy-Item`; this is a mechanical mirror, not a reimplementation.

- [ ] **Step 8: Prove the existing Sonara endpoint freezes the review line**

Add an API test adjacent to the Lumbro review test. The stub must capture uploaded JSON and assert:

```python
assert response.status_code == 200
line = uploaded_payload["items"][0]
assert line["code_status"] == "needs_review"
assert line["sku"] == ""
assert line["base_currency"] == "MXN"
assert line["unit_price"] == "77.00"
assert line["tax_rate"] == "0.160000"
assert "Codigo por verificar" in line["warnings"]
```

Use `POST /catalogs/sonara/quote`; no API production code change is needed because that route already calls `build_supplier_cart_payload`.

- [ ] **Step 9: Run the complete Sonara/backend gate**

Run:

```powershell
python -m pytest tests/test_catalog_importers_sonara.py tests/test_lumbro_catalog_e2e.py tests/test_supplier_catalog.py tests/test_quote_jobs_api.py -q
```

Expected: PASS; the real-source tests may SKIP only when the two ignored PDFs are absent.

- [ ] **Step 10: Commit only the Sonara rule and backend gate**

```powershell
git add -- mobiliti_saas/worker/catalog_sync/importers/sonara.py mobiliti_saas/quote_engine/supplier_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py tests/test_catalog_importers_sonara.py tests/test_lumbro_catalog_e2e.py tests/test_supplier_catalog.py tests/test_quote_jobs_api.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "fix(catalog): habilitar cotizacion Sonara y Lumbro"
```

---

### Task 2: Build the strict server-side mixed catalog contract

**Files:**

- Create: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/quote_engine/supplier_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py`
- Create: `tests/test_mixed_catalog_cart.py`
- Modify: `tests/test_supplier_catalog.py`

**Interfaces:**

- Consumes: `build_tarkett_cart_payload(raw_items, *, catalog)`, `build_offiho_cart_payload(raw_items, *, catalog)`, `build_supplier_cart_payload(raw_items, catalog, quote_currency, rate_rows, *, today: date | None = None)`, `resolve_conversion_rate(base_currency, quote_currency, rate_rows, today)`.
- Produces: constants `MIXED_CATALOG_CART_SOURCE_TYPE`, `MIXED_CATALOG_ORDER`, `MIXED_CATALOG_LABELS`, `MIXED_EXPECTED_BASE_CURRENCY`, `MAX_MIXED_CATALOG_LINES`, `MAX_MIXED_REQUEST_BYTES`, `MIXED_LINE_FIELDS`, `MIXED_RESERVATION_RESULT_FIELDS`, `MIXED_GROUP_FIELDS`; `preflight_mixed_catalog_items(raw_items: object) -> list[dict[str, Any]]`; `mixed_cart_key(raw: dict[str, Any]) -> str`; `build_mixed_catalog_cart_payload(raw_items: list[dict[str, object]], *, catalogs: dict[str, dict], rate_rows: list[dict], quote_currency: str, commercial_discount_percent: object, today: date | None = None) -> dict`; `validate_mixed_catalog_payload(payload: object) -> dict`; `build_mixed_reservation_groups(payload: dict) -> list[dict]`.
- Output group order: Tarkett, Offiho, CR Global, Sonara, Sunon, ALMA, Lumbro. Output amounts are decimal strings; `unit_price` is rounded to two places in quote currency.

- [ ] **Step 1: Add red tests for strict input, ordering and canonical identities**

Create `tests/test_mixed_catalog_cart.py` with fixture builders for one item from each existing family and these assertions:

```python
def test_mixed_cart_groups_seven_catalogs_in_canonical_order(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload(
        browser_rows_for_all_catalogs(),
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )
    assert payload["source_type"] == "mixed_catalog_cart"
    assert [group["catalog"] for group in payload["groups"]] == [
        "tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro"
    ]
    assert sum(len(group["items"]) for group in payload["groups"]) == 7


@pytest.mark.parametrize(
    "field",
    ("unit_price", "base_currency", "exchange_rate", "stock", "image_url", "product_url", "supplier", "warnings"),
)
def test_mixed_cart_rejects_browser_owned_commercial_fields(mixed_catalogs, rate_rows, field):
    row = {"catalog": "tarkett", "code": "25731726", "quantity": "1", field: "tampered"}
    with pytest.raises(ValueError, match="Campo mixto no permitido"):
        build_mixed_catalog_cart_payload(
            [row], catalogs=mixed_catalogs, rate_rows=rate_rows,
            quote_currency="MXN", commercial_discount_percent="40",
            today=date(2026, 7, 19),
        )


def test_mixed_cart_distinguishes_supplier_configurations(mixed_catalogs, rate_rows):
    first, second = alma_rows_with_distinct_add_ons()
    payload = build_mixed_catalog_cart_payload(
        [first, second], catalogs=mixed_catalogs, rate_rows=rate_rows,
        quote_currency="MXN", commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )
    assert [item["canonical_key"] for item in payload["groups"][0]["items"]] == [
        'alma:["alma:desk-1","base-a",["addon-a"]]',
        'alma:["alma:desk-1","base-a",["addon-b"]]',
    ]
```

- [ ] **Step 2: Run the focused tests and observe the missing module**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py -q
```

Expected: collection ERROR with `ModuleNotFoundError: mobiliti_saas.quote_engine.mixed_catalog`.

- [ ] **Step 3: Define the immutable catalog contract and strict field maps**

Start `mixed_catalog.py` with these exact constants:

```python
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
from typing import Any
import unicodedata

from .offiho_catalog import build_offiho_cart_payload
from .supplier_catalog import build_supplier_cart_payload, resolve_conversion_rate
from .tarkett_catalog import build_tarkett_cart_payload

MIXED_CATALOG_CART_SOURCE_TYPE = "mixed_catalog_cart"
MIXED_CATALOG_ORDER = ("tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro")
MIXED_CATALOG_LABELS = {
    "tarkett": "Tarkett", "offiho": "Offiho", "cr-global": "CR Global",
    "sonara": "Sonara", "sunon": "Sunon", "alma": "ALMA", "lumbro": "Lumbro",
}
MIXED_EXPECTED_BASE_CURRENCY = {
    "tarkett": "MXN", "offiho": "MXN", "cr-global": "MXN", "sonara": "MXN",
    "sunon": "USD", "alma": "USD", "lumbro": "MXN",
}
MIXED_QUOTE_CURRENCIES = frozenset({"MXN", "USD", "EUR"})
MAX_MIXED_CATALOG_LINES = 500
MAX_MIXED_REQUEST_BYTES = 1_000_000
MAX_MIXED_PAYLOAD_BYTES = 5_000_000
MAX_MIXED_TEXT = 2_000
MAX_MIXED_URL = 2_048
MAX_MIXED_WARNINGS = 50
MAX_MIXED_IDENTITY = 1_000
MAX_MIXED_OPTIONS_PER_LINE = 200
MIXED_ALLOWED_FIELDS = {
    "tarkett": frozenset({"catalog", "code", "quantity"}),
    "offiho": frozenset({"catalog", "inventory_key", "quantity"}),
    "supplier": frozenset({"catalog", "internal_id", "quantity", "base_option_id", "add_on_option_ids"}),
}
MIXED_REQUIRED_FIELDS = {
    "tarkett": frozenset({"catalog", "code", "quantity"}),
    "offiho": frozenset({"catalog", "inventory_key", "quantity"}),
    "supplier": frozenset({"catalog", "internal_id", "quantity"}),
}
```

Use explicit allowed/required checks, not identity aliases. Supplier configuration fields are optional and receive server-side structural defaults:

```python
def _field_family(catalog: str) -> str:
    return catalog if catalog in {"tarkett", "offiho"} else "supplier"


def _identity_text(value: object, field: str, *, allow_empty: bool = False, limit: int = MAX_MIXED_IDENTITY) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} invalido")
    text = value.strip()
    if (not text and not allow_empty) or len(text) > limit:
        raise ValueError(f"{field} invalido")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in text):
        raise ValueError(f"{field} invalido")
    return text


def _validate_browser_row(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Cada producto mixto debe ser un objeto")
    catalog = raw.get("catalog")
    if not isinstance(catalog, str) or catalog not in MIXED_CATALOG_ORDER:
        raise ValueError("Catalogo mixto no soportado")
    family = _field_family(catalog)
    unexpected = set(raw) - MIXED_ALLOWED_FIELDS[family]
    if unexpected:
        raise ValueError(f"Campo mixto no permitido: {min(unexpected)}")
    missing = MIXED_REQUIRED_FIELDS[family] - set(raw)
    if missing:
        raise ValueError(f"Campo mixto requerido: {min(missing)}")
    normalized = dict(raw)
    identity_field = {
        "tarkett": "code", "offiho": "inventory_key", "supplier": "internal_id",
    }[family]
    normalized[identity_field] = _identity_text(normalized.get(identity_field), identity_field)
    if len(str(normalized.get("quantity", ""))) > 64:
        raise ValueError("quantity invalida")
    if family == "supplier":
        normalized.setdefault("base_option_id", "")
        normalized.setdefault("add_on_option_ids", [])
        normalized["base_option_id"] = _identity_text(
            normalized["base_option_id"], "base_option_id", allow_empty=True, limit=500
        )
        add_on_ids = normalized["add_on_option_ids"]
        if (
            not isinstance(add_on_ids, list)
            or len(add_on_ids) > MAX_MIXED_OPTIONS_PER_LINE
            or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 500
                or any(
                    unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                    for character in value
                )
                for value in add_on_ids
            )
        ):
            raise ValueError("add_on_option_ids debe ser una lista de textos")
        cleaned_add_ons = [value.strip() for value in add_on_ids]
        if len(cleaned_add_ons) != len(set(cleaned_add_ons)):
            raise ValueError("add_on_option_ids contiene duplicados")
        normalized["add_on_option_ids"] = sorted(cleaned_add_ons)
    return normalized


def _commercial_discount_percent(value: object) -> Decimal:
    try:
        discount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Descuento comercial invalido") from exc
    if not discount.is_finite() or discount < 0 or discount > 100:
        raise ValueError("Descuento comercial debe estar entre 0 y 100")
    return discount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
```

Resolve `commercial_discount = _commercial_discount_percent(commercial_discount_percent)` once, before invoking any catalog builder. Never convert it through `float`; reuse the resulting `Decimal` for every Tarkett/Offiho line.

Add a parameterized test removing each required identity field (`code`, `inventory_key`, `internal_id`, `quantity`) and assert `Campo mixto requerido`. Add oversized/control/DEL/zero-width/surrogate identities, 201 add-ons, oversized or surrogate option IDs and a 65-character quantity; assert a stable HTTP/domain error occurs before UTF-8 encoding, any builder call or add-on sorting. Add a simple Sonara row without either configuration field and assert it normalizes to `base_option_id == ""` and `add_on_option_ids == []`; this is the exact shape accepted from `toMixedQuoteItem`.

- [ ] **Step 4: Make supplier rate resolution accept the frozen effective date**

In both `supplier_catalog.py` copies, extend the existing function compatibly:

```python
def build_supplier_cart_payload(
    raw_items: list[dict[str, object]],
    catalog: dict[str, Any],
    quote_currency: str,
    rate_rows: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
```

Pass `today or date.today()` to `resolve_conversion_rate` inside that builder. Existing positional callers remain unchanged. Add `test_supplier_cart_uses_explicit_effective_date_for_rate_selection` with two rate rows on consecutive dates and assert the older `today` selects only the eligible row. In `mixed_catalog.py`, compute `effective_today = today or date.today()` once and pass `today=effective_today` to every `build_supplier_cart_payload` call and every direct `resolve_conversion_rate` call.

- [ ] **Step 5: Implement canonical keys and grouping before calling builders**

Add:

```python
def mixed_cart_key(raw: dict[str, Any]) -> str:
    catalog = str(raw["catalog"])
    if catalog == "tarkett":
        identity = str(raw.get("code") or "").strip()
        return f"tarkett:{identity}"
    if catalog == "offiho":
        identity = str(raw.get("inventory_key") or "").strip()
        return f"offiho:{identity}"
    internal_id = str(raw.get("internal_id") or "").strip()
    base_option_id = str(raw.get("base_option_id") or "").strip()
    add_on_ids = list(raw.get("add_on_option_ids", []))
    identity_tuple = [internal_id, base_option_id, add_on_ids]
    return f"{catalog}:{json.dumps(identity_tuple, ensure_ascii=False, separators=(',', ':'))}"


def _group_browser_rows(raw_items: list[dict[str, object]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for raw in preflight_mixed_catalog_items(raw_items):
        key = mixed_cart_key(raw)
        if key in seen:
            raise ValueError(f"Clave mixta duplicada: {key}")
        seen.add(key)
        groups.setdefault(str(raw["catalog"]), []).append(raw)
    return groups


def preflight_mixed_catalog_items(raw_items: object) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_MIXED_CATALOG_LINES:
        raise ValueError("El carrito mixto debe contener entre 1 y 500 filas")
    return [_validate_browser_row(candidate) for candidate in raw_items]
```

Add a collision test with delimiter-bearing IDs (for example `internal_id="a|b", base="c"` versus `internal_id="a", base="b|c"`) and assert the JSON-tuple keys differ. Spy on `build_supplier_cart_payload` and assert add-on IDs reach it sorted, while duplicates are rejected before the builder.

- [ ] **Step 6: Add red conversion, tax, discount and audit tests**

Add tests that use a USD ALMA line and MXN Sonara line:

```python
@pytest.mark.parametrize(
    ("quote_currency", "alma_price", "sonara_price", "auto_rate"),
    (
        ("MXN", "1850.00", "77.00", "1.000000"),
        ("USD", "100.00", "4.16", "0.054054"),
        ("EUR", "90.24", "3.76", "0.048780"),
    ),
)
def test_mixed_cart_freezes_each_group_without_double_conversion(
    mixed_catalogs, rate_rows, quote_currency, alma_price, sonara_price, auto_rate
):
    payload = build_mixed_catalog_cart_payload(
        [tarkett_standard_row(), sonara_review_row(), alma_standard_row()],
        catalogs=mixed_catalogs,
        rate_rows=rate_rows,
        quote_currency=quote_currency,
        commercial_discount_percent="40",
        today=date(2026, 7, 19),
    )
    lines = {group["catalog"]: group["items"][0] for group in payload["groups"]}
    assert lines["alma"]["unit_price"] == alma_price
    assert lines["sonara"]["unit_price"] == sonara_price
    assert lines["alma"]["original_currency"] == "USD"
    assert lines["sonara"]["original_currency"] == "MXN"
    assert lines["alma"]["discount_percent"] == "0.000000"
    assert lines["sonara"]["discount_percent"] == "0.000000"
    assert all(group["quote_currency"] == quote_currency for group in payload["groups"])
    assert payload["auto_electrification_rate"] == {
        "base_currency": "MXN",
        "quote_currency": quote_currency,
        "exchange_rate": auto_rate,
        "rate_source": payload["auto_electrification_rate"]["rate_source"],
        "rate_effective_date": payload["auto_electrification_rate"]["rate_effective_date"],
        "rate_retrieved_at": payload["auto_electrification_rate"]["rate_retrieved_at"],
    }


def test_mixed_cart_applies_discount_only_to_tarkett_and_offiho(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload(
        browser_rows_for_all_catalogs(), catalogs=mixed_catalogs, rate_rows=rate_rows,
        quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19),
    )
    discounts = {
        group["catalog"]: {item["discount_percent"] for item in group["items"]}
        for group in payload["groups"]
    }
    assert discounts["tarkett"] == {"40.000000"}
    assert discounts["offiho"] == {"40.000000"}
    assert all(discounts[name] == {"0.000000"} for name in MIXED_CATALOG_ORDER[2:])


def test_mixed_cart_rejects_non_sixteen_percent_tax(mixed_catalogs, rate_rows):
    mixed_catalogs["alma"]["items"][0]["tax_rate"] = "0.080000"
    with pytest.raises(ValueError, match="alma:.*IVA 16"):
        build_mixed_catalog_cart_payload(
            [alma_standard_row()], catalogs=mixed_catalogs, rate_rows=rate_rows,
            quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19),
        )


@pytest.mark.parametrize("discount", ("-0.01", "100.01", "NaN", "Infinity", "texto"))
def test_mixed_cart_rejects_invalid_commercial_discount(mixed_catalogs, rate_rows, discount):
    with pytest.raises(ValueError, match="Descuento comercial"):
        build_mixed_catalog_cart_payload(
            [tarkett_standard_row()], catalogs=mixed_catalogs, rate_rows=rate_rows,
            quote_currency="MXN", commercial_discount_percent=discount, today=date(2026, 7, 19),
        )
```

- [ ] **Step 7: Normalize each existing builder into one frozen line contract**

Implement helper branches with `Decimal` and `ROUND_HALF_UP`. The normalized line must contain all these keys:

```python
MIXED_LINE_FIELDS = frozenset({
    "canonical_key", "catalog", "supplier", "code", "name", "description", "unit",
    "quantity", "unit_price", "discount_percent", "original_currency",
    "original_unit_price", "frozen_exchange_rate", "source_reference", "price_mode",
    "auto_electrification", "tax_rate", "image_url", "product_url", "warnings",
    "code_status", "configuration", "attributes", "variant", "availability_type",
    "available_quantity", "stock", "lead_time", "price_source", "stock_status",
    "image_kind", "reservation",
})
MIXED_RESERVATION_RESULT_FIELDS = frozenset({
    "reserved_quantity", "available_after_reservations", "reserved_by_others",
})
MIXED_GROUP_FIELDS = frozenset({
    "catalog", "catalog_source_hash", "base_currency", "quote_currency",
    "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at", "items",
})
```

For Tarkett/Offiho, obtain the identity rate with `resolve_conversion_rate("MXN", quote_currency, rate_rows, effective_today)` and calculate:

```python
original = Decimal(str(line["unit_price"]))
converted = (original * rate.exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
normalized.update({
    "unit_price": f"{converted:.2f}",
    "discount_percent": f"{commercial_discount:.6f}",
    "original_currency": "MXN",
    "original_unit_price": f"{original:.6f}",
    "frozen_exchange_rate": f"{rate.exchange_rate:.6f}",
    "price_mode": "list",
    "auto_electrification": True,
    "tax_rate": "0.160000",
})
```

For supplier groups, call `build_supplier_cart_payload` separately per supplier and map:

```python
normalized.update({
    "unit_price": line["unit_price"],
    "discount_percent": "0.000000",
    "original_currency": line["base_currency"],
    "original_unit_price": line["unit_price_base"],
    "frozen_exchange_rate": supplier_payload["exchange_rate"],
    "price_mode": "net",
    "auto_electrification": False,
    "tax_rate": line["tax_rate"],
})
```

Complete every non-price field explicitly; do not rely on similarly named keys happening to exist across the three legacy payloads. Normalize every quantity/stock to a six-place string in the frozen payload even when its browser precision is stricter:

```python
SIX_PLACES = Decimal("0.000001")


def _six(value: object) -> str:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Decimal mixto invalido")
    return f"{number.quantize(SIX_PLACES, rounding=ROUND_HALF_UP):.6f}"


def _stable_warnings(*groups: list[object], derived: list[str] | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*(item for group in groups for item in group), *(derived or [])]:
        text = str(value or "").strip()
        key = " ".join(
            "".join(
                character
                for character in unicodedata.normalize("NFKD", text.casefold())
                if not unicodedata.combining(character)
            ).split()
        )
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result
```

Use these exact family projections in addition to the monetary fields above:

| Frozen field | Tarkett | Offiho | Generic supplier |
|---|---|---|---|
| `canonical_key` | `tarkett:<code>` | `offiho:<inventory_key>` | JSON-tuple key from the validated browser row |
| `supplier` | `"Tarkett"` | `"Offiho"` | `MIXED_CATALOG_LABELS[catalog]` (never the browser) |
| `code` / `code_status` | authoritative `code` / `verified` | authoritative `code or inventory_key` / `verified` | `line["sku"]` / builder status; empty only for allowed review |
| `name`, `description`, `unit` | builder values; description `""` | builder values | builder values |
| `quantity` | `_six(line["quantity"])` | `_six(line["quantity"])` | `_six(line["quantity"])` |
| `source_reference` | `f"tarkett:{source_hash}:{code}"` | `f"offiho:{source_hash}:{inventory_key}"` | authoritative `line["source_reference"]` |
| `configuration`, `variant`, `attributes` | `""`, `""`, `{}` | `""`, authoritative variant, `{}` | authoritative configuration, `""`, deep-copied attributes |
| `availability_type` | `stocked` | `stocked` | authoritative enum |
| `available_quantity`, `stock` | both `_six(available_quantity)` | both `_six(available_quantity)` | both `_six(stock)` for stocked; otherwise both `None` |
| `lead_time` | `""` | `""` | authoritative lead time |
| `price_source` | authoritative source | authoritative source | `"catalog"` after the positive-price builder gate |
| `stock_status` | `available` (the Tarkett builder already hard-rejects overstock) | authoritative status | `out_of_stock` when stocked stock ≤ 0, `insufficient_stock` when requested > stock, otherwise `available`; empty for non-stocked |
| `image_kind` | `official` when URL exists, else `placeholder` | same | authoritative enum |
| `image_url`, `product_url` | authoritative URLs | authoritative URLs | authoritative URLs |

Build warnings with `_stable_warnings` from the builder warnings plus these derived values exactly once: `Codigo por verificar` for `needs_review`; `Imagen de referencia` for `image_kind="generated_reference"`; and `Precio por confirmar` for a legacy-family `price_source="missing"`. Availability remains structured in the fields above so the shared workbook writer can produce its existing agotado/insuficiente text after the reservation snapshot; do not flatten stock into an unauditable browser warning.

Build `reservation` from the same normalized values, never separately parsed browser data:

```python
if catalog == "tarkett":
    reservation = {
        "identity": line["code"], "sku": line["code"],
        "quantity": normalized["quantity"], "stock": normalized["stock"],
    }
elif catalog == "offiho":
    reservation = {
        "identity": line["inventory_key"],
        "sku": line["code"] or line["inventory_key"],
        "quantity": normalized["quantity"], "stock": normalized["stock"],
    }
elif line["availability_type"] == "stocked":
    reservation = {
        "identity": line["internal_id"], "sku": line["sku"],
        "quantity": normalized["quantity"], "stock": normalized["stock"],
    }
else:
    reservation = None
normalized["reservation"] = reservation
```

Add one parameterized mapping test per all seven catalogs. Assert every exact field set, six-place quantity/stock, deterministic source reference, supplier label and expected reservation identity. Include: stocked ALMA/Sonara with a reservation; made-to-order Sunon/ALMA and unknown Sonara with `reservation=None`; Offiho missing price plus insufficient stock; and a generated-reference generic image. These tests must prove the latter cases retain variant, delivery/availability, `Precio por confirmar` and `Imagen de referencia` through the frozen fields rather than merely in a hand-decorated workbook fixture.

For every used catalog, append exactly one group with the common frozen envelope (using the direct `rate` for Tarkett/Offiho or the returned supplier payload fields):

```python
normalized_groups.append({
    "catalog": catalog,
    "catalog_source_hash": source_payload["catalog_source_hash"],
    "base_currency": rate_payload["base_currency"],
    "quote_currency": rate_payload["quote_currency"],
    "exchange_rate": rate_payload["exchange_rate"],
    "rate_source": rate_payload["rate_source"],
    "rate_effective_date": rate_payload["rate_effective_date"],
    "rate_retrieved_at": rate_payload["rate_retrieved_at"],
    "items": normalized_items,
})
```

For the direct Tarkett/Offiho rate snapshot, construct `rate_payload` with the same six decimal/date strings as the supplier builder. `source_payload` is the corresponding existing cart payload and `normalized_items` is never empty.

Before appending any group, require `rate_payload["base_currency"] == MIXED_EXPECTED_BASE_CURRENCY[catalog]`; this applies to verified and `needs_review` lines alike, so a verified Sonara snapshot in USD also fails closed. Require every normalized line's `original_currency` to equal the same expected value. Add a parameterized test that mutates each catalog fixture to a wrong base currency and assert the builder identifies that catalog before returning any payload.

Reject the line before returning when `Decimal(normalized["tax_rate"]) != Decimal("0.160000")`, and include catalog plus canonical key in the message.

Preserve the legacy electrification policy exactly: Tarkett/Offiho lines use `auto_electrification=True`; all five generic supplier lines, including ALMA/Sunon/Sonara/CR Global/Lumbro, use `False` because their previous `list_price_net` flow suppressed automatic accessories. Those eligible groups already carry the required MXN→quote snapshot because Tarkett and Offiho have canonical base currency MXN. Derive the accessory snapshot from those frozen groups instead of resolving another rate, and require every eligible group to agree byte-for-byte on all six audit fields:

```python
AUTO_ELECTRIFICATION_RATE_FIELDS = (
    "base_currency", "quote_currency", "exchange_rate", "rate_source",
    "rate_effective_date", "rate_retrieved_at",
)
eligible_rate_snapshots = [
    {field: group[field] for field in AUTO_ELECTRIFICATION_RATE_FIELDS}
    for group in normalized_groups
    if any(item["auto_electrification"] for item in group["items"])
]
if eligible_rate_snapshots:
    auto_electrification_rate = eligible_rate_snapshots[0]
    if any(
        snapshot != auto_electrification_rate
        for snapshot in eligible_rate_snapshots[1:]
    ):
        raise ValueError("Tasa de electrificacion mixta inconsistente")
else:
    auto_electrification_rate = None
```

For an eligible MXN job this is identity `1.000000`; for USD/EUR it is the same frozen inverse MXN rate already recorded by Tarkett/Offiho. This is auditing for derived automatic accessories, not an eighth catalog group. Add tests asserting that Tarkett-only and Tarkett+Offiho payloads copy the exact six-field group projection, that disagreeing eligible group snapshots fail closed, and that an ALMA-only USD payload leaves the field `None` and performs no MXN/USD lookup.

- [ ] **Step 8: Assemble the frozen payload and reservation groups**

The outer payload must be built from used groups only:

```python
return {
    "source_type": MIXED_CATALOG_CART_SOURCE_TYPE,
    "quote_currency": quote_currency,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "groups": normalized_groups,
    "item_count": sum(len(group["items"]) for group in normalized_groups),
    "auto_electrification_rate": auto_electrification_rate,
    "rate_summary": [
        {
            key: group[key]
            for key in (
                "catalog", "base_currency", "quote_currency", "exchange_rate",
                "rate_source", "rate_effective_date", "rate_retrieved_at",
            )
        }
        for group in normalized_groups
    ],
}
```

Implement `validate_mixed_catalog_payload` in the same module and call it on the assembled dictionary before returning. It must reject before any workbook/image work unless all of these hold:

- The outer field set is exactly `source_type,quote_currency,created_at,groups,item_count,auto_electrification_rate,rate_summary`; source type and quote currency are valid.
- The compact UTF-8 JSON is at most `MAX_MIXED_PAYLOAD_BYTES`; `created_at` is a timezone-aware ISO timestamp. Recursive `attributes` reuse the existing supplier limits for JSON types/depth/bytes, and `configuration` is a string no longer than `MAX_MIXED_TEXT`.
- `groups` has 1–7 nonempty objects with field set exactly `MIXED_GROUP_FIELDS`, appears in canonical order without duplicates, and contains 1–500 total lines. Every `catalog_source_hash` is 64 lowercase hex; group base currency equals `MIXED_EXPECTED_BASE_CURRENCY[catalog]`; group quote currency equals the root; rates are positive six-place decimals. For `rate_source="identity"`, require base currency equal to quote currency, rate exactly `1.000000`, a parseable effective date and `rate_retrieved_at == ""`. For every non-identity source, require a bounded nonempty source, parseable effective date and timezone-aware nonempty retrieval timestamp; an empty timestamp is invalid in that branch.
- Every line field set equals `MIXED_LINE_FIELDS` or `MIXED_LINE_FIELDS | MIXED_RESERVATION_RESULT_FIELDS`; its `catalog` matches the group, canonical key is unique, bounded and starts with the exact catalog prefix, and `supplier == MIXED_CATALOG_LABELS[catalog]`. Required name/unit/source-reference strings are nonempty and bounded; code may be empty only for an allowed `needs_review` line. Warnings are at most `MAX_MIXED_WARNINGS` bounded strings. Treat the two URL fields differently: `image_url` is empty or a bounded credential-free HTTPS URL and must pass the source-specific image allowlist plus DNS/connected-peer checks immediately before the only permitted GET; `product_url` is empty or a bounded credential-free HTTPS/443 commercial link, is never fetched by backend/worker code and is written only through `safe_excel_text`. Do not apply the Storage/image-host allowlist to legitimate commercial product links.
- `variant`, `configuration` and `lead_time` are bounded strings; `attributes` passes the recursive JSON contract. `availability_type` is exactly `stocked|made_to_order|unknown`; `image_kind` is exactly `official|generated_reference|placeholder`; `price_source` is bounded and nonempty; `stock_status` is exactly `""|available|out_of_stock|insufficient_stock`. Stocked lines require equal nonnegative six-place `available_quantity`/`stock`; non-stocked lines require both `None` and empty stock status. Tarkett/Offiho must be stocked. `generated_reference`, `missing` price and `needs_review` each require their canonical warning exactly once after accent/case-normalized comparison.
- Quantity is a finite decimal string in `(0, 1000000]` with at most six places. Original/current prices are finite nonnegative decimals, tax is exactly `0.160000`, line original currency and frozen rate equal the group's base/rate, and `unit_price == ROUND_HALF_UP(original_unit_price * frozen_exchange_rate, 2)`.
- Catalog semantics are exact: Tarkett/Offiho use `price_mode="list"`, supplier label matching their catalog, discount in `[0,100]` and `auto_electrification=True`; all five generic groups use `price_mode="net"`, discount `0.000000` and `auto_electrification=False`. Both flags must be real `bool`/known text, never truthy aliases.
- `reservation` is either `None` or an exact `{identity,sku,quantity,stock}` object: identity is nonempty/bounded, SKU is bounded and may be empty only for the Sonara/Lumbro review exception, quantity equals the line quantity and stock equals the line's structured stock. Every stocked line has a reservation; made-to-order/unknown lines use `None`. Identity must equal code for Tarkett, the authoritative inventory key encoded after `offiho:` for Offiho, and the authoritative internal ID stored in the generic canonical tuple for suppliers.
- Optional reservation-result fields must appear all together, use nonnegative six-place decimal strings plus a real boolean, and may appear only when `reservation` is non-`None`. Require `available_after_reservations == max(reservation.stock - reserved_quantity, 0)`. A Tarkett line whose quantity exceeds that value is invalid; a non-Tarkett reservable line in that state must carry exactly one canonical `Existencia insuficiente; verificar disponibilidad.` warning. This cross-check runs again in the worker so tampering after API upload cannot falsify availability.
- `item_count` equals the actual line count. `rate_summary` equals exactly the ordered projection of the seven group audit fields used in Step 8; it may neither omit a used group nor include an unused one.
- `auto_electrification=True` is permitted only for Tarkett/Offiho. If any line is true, `auto_electrification_rate` has exactly the six frozen-rate fields and equals the six-field projection of every eligible group's frozen snapshot; therefore Tarkett and Offiho must themselves agree exactly when both are eligible. This equality covers base/quote currency, positive six-place rate, source, effective date and retrieval timestamp—not merely the currency pair. If no line is true, that field must be `None`.

Return the same validated dict (not a normalized replacement) so the worker and workbook adapter can share this one root validator. Add red tests for wrong `item_count`, swapped groups, summary/group mismatch, oversize/deep JSON, invalid URL/hash/timestamp, wrong base/original/quote currency, line/group rate mismatch, converted-price mismatch, supplier spoofing, `net` with discount, generic `list`/auto flag, non-boolean auto flag, malformed reservation/result fields and missing/extra frozen auto rate. Add URL cases proving a valid `https://sonara.mx/producto/panel` product link survives as inert text, while the same host in `image_url` is rejected unless configured by that source's image policy; reject credentials, non-443 ports and non-HTTPS in both. Include a valid MXN/MXN payload whose group and automatic-accessory snapshots both use `rate_source="identity"`, rate `1.000000` and an empty retrieval timestamp; then reject identity with a nonempty timestamp and non-identity with an empty timestamp. Parameterize six more mutations that alter only one field of `auto_electrification_rate` while keeping the eligible group snapshot valid; each must fail with `Tasa de electrificacion mixta invalida`.

Use stable top-level messages `Grupos mixtos invalidos`, `Conteo mixto inconsistente`, `Resumen de tasas mixtas inconsistente` and `Tasa de electrificacion mixta invalida`, followed by catalog/key detail where applicable; worker tests match these messages.

Keep `validate_mixed_catalog_payload` pure and offline: it validates URL type/length/scheme/credentials/port only. It must not resolve DNS, open sockets or invoke `_validate_official_https_url`. Task 5's `_download_catalog_image`, selected through `MIXED_GROUP_SOURCE_TYPES`, is the sole place that applies the image-host allowlist, DNS public-IP check, redirect validation and connected-peer check. This separation lets deterministic `.test` workbook fixtures run with the downloader stubbed while the real downloader policy remains covered independently.

Implement reservation projection without leaking visual fields and aggregate distinct configurations of the same supplier item into one inventory row:

```python
def build_mixed_reservation_groups(payload: dict) -> list[dict]:
    groups: list[dict] = []
    for group in payload["groups"]:
        aggregated: dict[str, dict] = {}
        for line in group["items"]:
            reservation = deepcopy(line["reservation"])
            if reservation is None:
                continue
            identity = reservation["identity"]
            existing = aggregated.get(identity)
            if existing is None:
                aggregated[identity] = reservation
                continue
            if existing["sku"] != reservation["sku"] or existing["stock"] != reservation["stock"]:
                raise ValueError(f"Reserva mixta incompatible: {group['catalog']}:{identity}")
            existing["quantity"] = f"{Decimal(existing['quantity']) + Decimal(reservation['quantity']):.6f}"
        if aggregated:
            groups.append({
                "catalog": group["catalog"],
                "items": [aggregated[key] for key in sorted(aggregated)],
            })
    return groups
```

Each `reservation` object must use exactly `{identity, sku, quantity, stock}`. Set `reservation=None` for `made_to_order`/unknown supplier lines. Add a test with two ALMA configurations at quantities 1 and 2: both remain in `payload["groups"][0]["items"]`, while `build_mixed_reservation_groups` returns one `alma:desk-1` reservation at `3.000000`.

- [ ] **Step 9: Run contract, conversion and tamper tests**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py -q
python -m pytest tests/test_tarkett_catalog.py tests/test_offiho_catalog.py tests/test_supplier_catalog.py -q
```

Expected: PASS. Confirm the new payload contains no client-provided commercial field by mutating every forbidden field in the parameterized test.

- [ ] **Step 10: Mirror the module and assert byte identity**

Add to `tests/test_mixed_catalog_cart.py`:

```python
def test_mixed_catalog_module_copies_are_byte_identical():
    paths = [
        Path("mobiliti_saas/quote_engine/mixed_catalog.py"),
        Path("mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py"),
    ]
    assert len({hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}) == 1
```

Then copy the root file and rerun the test.

- [ ] **Step 11: Commit the pure mixed-domain layer**

```powershell
git add -- mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/quote_engine/supplier_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py tests/test_mixed_catalog_cart.py tests/test_supplier_catalog.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(quote): normalizar carrito mixto autoritativo"
```

---

### Task 3: Reserve and release all catalog families atomically

**Files:**

- Create: `mobiliti_saas/supabase_setup/2026_07_mixed_catalog_cart.sql`
- Modify: `mobiliti_saas/supabase_setup/create_tables.sql`
- Modify: `mobiliti_saas/api/index.py:900-1570,2625-2710`
- Modify: `mobiliti_saas/web/api/index.py:900-1570,2625-2710`
- Modify: `vercel_deploy/api/index.py:900-1570,2625-2710`
- Modify: `tests/test_catalog_migrations.py`
- Modify: `tests/test_quote_jobs_api.py`
- Create: `tests/test_mixed_catalog_postgres.py`

**Interfaces:**

- Consumes: the aggregated output of `build_mixed_reservation_groups(payload)`, which may be empty when every line is made-to-order/unknown; existing tables `saas_tarkett_reservations`, `saas_offiho_reservations`, `saas_catalog_reservations`; a `draft` row in `saas_quote_jobs`.
- Produces SQL: `saas_reserve_mixed_cart(p_usuario_id INTEGER, p_quote_job_id UUID, p_groups JSONB) RETURNS JSONB`; `saas_release_mixed_cart(p_quote_job_id UUID) RETURNS JSONB`.
- Produces Python: `db_reserve_mixed_cart(usuario_id: int, quote_job_id: str, groups: list[dict]) -> list[dict]`; `db_release_mixed_cart(quote_job_id: str) -> dict`; `db_queue_mixed_quote_job(quote_job_id: str, metadata: dict) -> dict` with a compare-and-set `draft -> queued` transition.
- Atomicity: a failed validation or insert persists zero reservations; DEV performs one `_dev_load()` and one `_dev_save()` only after all groups validate.

- [ ] **Step 1: Add red migration-security tests**

First make the existing function extractor support a standalone additive migration whose last function is at EOF:

```python
def _function_sql(sql, name):
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    end = sql.find("\nCREATE OR REPLACE FUNCTION ", start + 1)
    if end == -1:
        end = sql.find("\nALTER TABLE saas_catalog_sources ENABLE", start)
    if end == -1:
        end = len(sql)
    return sql[start:end]
```

Then extend `tests/test_catalog_migrations.py`:

```python
MIXED_MIGRATION = SETUP / "2026_07_mixed_catalog_cart.sql"


def test_mixed_cart_rpcs_are_additive_atomic_and_service_role_only():
    for path in (MIXED_MIGRATION, BOOTSTRAP):
        sql = path.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", sql.lower())
        for name in ("saas_reserve_mixed_cart", "saas_release_mixed_cart"):
            function = _function_sql(sql, name)
            assert "SECURITY DEFINER" in function
            assert "SET search_path = public, pg_temp" in function
        assert "pg_advisory_xact_lock" in normalized
        assert "order by catalog, identity" in normalized
        assert "pg_temp.mixed_reservation_lines" in normalized
        assert "to_char(" in normalized
        release = _function_sql(sql, "saas_release_mixed_cart").lower()
        assert "from saas_quote_jobs" in release
        assert "for update" in release
        assert "status = 'failed'" in release
        assert "revoke all on function saas_reserve_mixed_cart" in normalized
        assert "revoke all on function saas_release_mixed_cart" in normalized
        assert "from public" in normalized
        assert "from anon" in normalized
        assert "from authenticated" in normalized
        assert "grant execute on function saas_reserve_mixed_cart" in normalized
        assert "grant execute on function saas_release_mixed_cart" in normalized
        assert "to service_role" in normalized
        assert "create temp table if not exists mixed_reservation_lines" in normalized
        assert "delete from pg_temp.mixed_reservation_lines" in normalized
        assert "drop table" not in normalized
        assert "truncate" not in normalized
```

- [ ] **Step 2: Run the migration test and observe the absent file/functions**

Run:

```powershell
python -m pytest tests/test_catalog_migrations.py -k "mixed_cart" -q
```

Expected: FAIL with `FileNotFoundError` for `2026_07_mixed_catalog_cart.sql`.

- [ ] **Step 3: Write the bounded SQL contract and job ownership check**

Create the migration with this exact function shell and validation order:

```sql
CREATE OR REPLACE FUNCTION saas_reserve_mixed_cart(
    p_usuario_id INTEGER,
    p_quote_job_id UUID,
    p_groups JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_group JSONB;
    v_item JSONB;
    v_catalog TEXT;
    v_identity TEXT;
    v_sku TEXT;
    v_quantity NUMERIC;
    v_stock NUMERIC;
    v_total_lines INTEGER := 0;
    v_result JSONB := '[]'::JSONB;
    v_seen_catalogs TEXT[] := ARRAY[]::TEXT[];
    v_row RECORD;
    v_reserved_before NUMERIC(20, 6);
    v_available_before NUMERIC(20, 6);
    v_reserved_by_others BOOLEAN;
    v_insufficient BOOLEAN;
BEGIN
    PERFORM 1
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id
      AND usuario_id = p_usuario_id
      AND status = 'draft'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mixed quote job is invalid';
    END IF;

    IF p_groups IS NULL OR jsonb_typeof(p_groups) <> 'array'
       OR jsonb_array_length(p_groups) NOT BETWEEN 0 AND 7 THEN
        RAISE EXCEPTION 'mixed groups must be a bounded array';
    END IF;

```

For every group require keys exactly `catalog,items`; for every item require exactly `identity,quantity,sku,stock`. Require a catalog from the seven-value allowlist; trimmed identity of 1–500 characters without controls; SKU of 0–500 characters without controls (empty is valid only for server-built Sonara/Lumbro `needs_review` reservations); quantity matching `^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$`, in `(0,1000000]`; and non-null stock matching the same bounded decimal grammar in `[0,1000000000]`. Lines without authoritative stock are omitted from reservation groups before the RPC, so JSON null is invalid here. Reject duplicate catalog groups and duplicate `(catalog, identity)` rows before any insert. Apply the same normalized six-place rules in the Python boundary.

- [ ] **Step 4: Acquire all locks in a stable global order**

Materialize the validated entries in a transaction-local temporary table and lock them before calculating any availability:

```sql
CREATE TEMP TABLE IF NOT EXISTS mixed_reservation_lines (
    catalog TEXT NOT NULL,
    identity TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity NUMERIC(20, 6) NOT NULL,
    stock NUMERIC(20, 6) NOT NULL,
    PRIMARY KEY (catalog, identity)
) ON COMMIT DROP;

DELETE FROM pg_temp.mixed_reservation_lines;

FOR v_catalog, v_identity IN
    SELECT catalog, identity
    FROM pg_temp.mixed_reservation_lines
    ORDER BY catalog, identity
LOOP
    PERFORM pg_advisory_xact_lock(
        hashtextextended(v_catalog || ':' || v_identity, 0)
    );
END LOOP;
```

`CREATE TEMP TABLE IF NOT EXISTS` places the relation in the session's temporary schema and permits a second RPC call inside the same transaction. Immediately empty only that transaction-local staging relation with `DELETE FROM pg_temp.mixed_reservation_lines`; this never touches a persistent table. Every later insert/select/update also uses the `pg_temp` qualification. That explicit qualification prevents a public relation from shadowing staging under the hardened search path. The literal `ORDER BY catalog, identity` is part of the static test and prevents cross-family deadlock inversions. The key deliberately matches the existing generic supplier RPC's `<supplier>:<internal_id>` namespace, so a mixed ALMA/Sonara/etc. reservation serializes against the corresponding legacy supplier reservation too.

- [ ] **Step 5: Insert all three families and return one grouped snapshot**

Before inserting, reject the job if any of the three reservation tables has any row with `quote_job_id = p_quote_job_id`, regardless of status; existing uniqueness is not partial, and a released row therefore makes the same job ID non-reusable. For each staged row, compute `reserved_before` from matching active rows, `available_before = GREATEST(stock - reserved_before, 0)`, and `reserved_by_others` from a different `usuario_id`. Insert with these exact mappings:

```sql
INSERT INTO saas_tarkett_reservations
    (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_identity, v_quantity, 'active', NOW(), NOW());

INSERT INTO saas_offiho_reservations
    (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
VALUES
    (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_identity, v_quantity, 'active', NOW(), NOW());

INSERT INTO saas_catalog_reservations
    (supplier, internal_id, sku, quantity, usuario_id, quote_job_id, status, created_at, updated_at)
VALUES
    (v_catalog, v_identity, v_sku, v_quantity, p_usuario_id, p_quote_job_id, 'active', NOW(), NOW());
```

Append one result object per identity with exact keys `catalog`, `identity`, `reserved_before`, `available_before`, `insufficient`, `reserved_by_others`. Emit both decimal values as JSON strings with exactly six places using `to_char(value, 'FM999999999999999999999999990.000000')`; do not let `jsonb_build_object` serialize PostgreSQL `NUMERIC` as a JSON number. Booleans remain JSON booleans. Because any exception escapes the function, PostgreSQL rolls back the temporary work and every insert.

The shell in Step 3 is not a placeholder. Complete it with this exact validation/staging/lock/insert body before the final `END; $$;` (replace its early `RETURN` tail rather than defining a second function):

```sql
    CREATE TEMP TABLE IF NOT EXISTS mixed_reservation_lines (
        catalog TEXT NOT NULL,
        identity TEXT NOT NULL,
        sku TEXT NOT NULL,
        quantity NUMERIC(20, 6) NOT NULL,
        stock NUMERIC(20, 6) NOT NULL,
        PRIMARY KEY (catalog, identity)
    ) ON COMMIT DROP;

    DELETE FROM pg_temp.mixed_reservation_lines;

    FOR v_group IN SELECT value FROM jsonb_array_elements(p_groups)
    LOOP
        IF jsonb_typeof(v_group) <> 'object'
           OR NOT (v_group ?& ARRAY['catalog','items'])
           OR (v_group - ARRAY['catalog','items']::TEXT[]) <> '{}'::JSONB
           OR jsonb_typeof(v_group -> 'catalog') <> 'string'
           OR jsonb_typeof(v_group -> 'items') <> 'array' THEN
            RAISE EXCEPTION 'mixed group has invalid shape';
        END IF;

        v_catalog := btrim(v_group ->> 'catalog');
        IF v_catalog NOT IN ('tarkett','offiho','cr-global','sonara','sunon','alma','lumbro') THEN
            RAISE EXCEPTION 'mixed catalog is invalid';
        END IF;
        IF v_catalog = ANY(v_seen_catalogs) THEN
            RAISE EXCEPTION 'mixed catalog is duplicated';
        END IF;
        v_seen_catalogs := array_append(v_seen_catalogs, v_catalog);
        IF jsonb_array_length(v_group -> 'items') = 0 THEN
            RAISE EXCEPTION 'mixed reservation group is empty';
        END IF;
        v_total_lines := v_total_lines + jsonb_array_length(v_group -> 'items');
        IF v_total_lines > 500 THEN
            RAISE EXCEPTION 'mixed reservation line count is invalid';
        END IF;

        FOR v_item IN SELECT value FROM jsonb_array_elements(v_group -> 'items')
        LOOP
            IF jsonb_typeof(v_item) <> 'object'
               OR NOT (v_item ?& ARRAY['identity','quantity','sku','stock'])
               OR (v_item - ARRAY['identity','quantity','sku','stock']::TEXT[]) <> '{}'::JSONB
               OR jsonb_typeof(v_item -> 'identity') <> 'string'
               OR jsonb_typeof(v_item -> 'quantity') <> 'string'
               OR jsonb_typeof(v_item -> 'sku') <> 'string'
               OR jsonb_typeof(v_item -> 'stock') <> 'string' THEN
                RAISE EXCEPTION 'mixed reservation item has invalid shape';
            END IF;

            v_identity := btrim(v_item ->> 'identity');
            v_sku := btrim(v_item ->> 'sku');
            IF char_length(v_identity) NOT BETWEEN 1 AND 500
               OR v_identity ~ '[[:cntrl:]]'
               OR char_length(v_sku) > 500
               OR v_sku ~ '[[:cntrl:]]' THEN
                RAISE EXCEPTION 'mixed reservation identity is invalid';
            END IF;
            IF v_sku = '' AND v_catalog NOT IN ('sonara','lumbro') THEN
                RAISE EXCEPTION 'mixed reservation sku is invalid';
            END IF;
            IF (v_item ->> 'quantity') !~ '^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$'
               OR (v_item ->> 'stock') !~ '^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?$' THEN
                RAISE EXCEPTION 'mixed reservation decimal is invalid';
            END IF;
            v_quantity := (v_item ->> 'quantity')::NUMERIC;
            v_stock := (v_item ->> 'stock')::NUMERIC;
            IF v_quantity <= 0 OR v_quantity > 1000000
               OR v_stock < 0 OR v_stock > 1000000000 THEN
                RAISE EXCEPTION 'mixed reservation decimal is out of range';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_temp.mixed_reservation_lines
                WHERE catalog = v_catalog AND identity = v_identity
            ) THEN
                RAISE EXCEPTION 'mixed reservation identity is duplicated';
            END IF;
            INSERT INTO pg_temp.mixed_reservation_lines
                (catalog, identity, sku, quantity, stock)
            VALUES (v_catalog, v_identity, v_sku, v_quantity, v_stock);
        END LOOP;
    END LOOP;

    IF EXISTS (SELECT 1 FROM saas_tarkett_reservations WHERE quote_job_id = p_quote_job_id)
       OR EXISTS (SELECT 1 FROM saas_offiho_reservations WHERE quote_job_id = p_quote_job_id)
       OR EXISTS (SELECT 1 FROM saas_catalog_reservations WHERE quote_job_id = p_quote_job_id) THEN
        RAISE EXCEPTION 'mixed quote job already has reservations';
    END IF;

    IF v_total_lines = 0 THEN
        RETURN '[]'::JSONB;
    END IF;

    FOR v_catalog, v_identity IN
        SELECT catalog, identity
        FROM pg_temp.mixed_reservation_lines
        ORDER BY catalog, identity
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(v_catalog || ':' || v_identity, 0)
        );
    END LOOP;

    FOR v_row IN
        SELECT catalog, identity, sku, quantity, stock
        FROM pg_temp.mixed_reservation_lines
        ORDER BY catalog, identity
    LOOP
        IF v_row.catalog = 'tarkett' THEN
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_tarkett_reservations
            WHERE product_code = v_row.identity AND status = 'active';
        ELSIF v_row.catalog = 'offiho' THEN
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_offiho_reservations
            WHERE product_code = v_row.identity AND status = 'active';
        ELSE
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_catalog_reservations
            WHERE supplier = v_row.catalog
              AND internal_id = v_row.identity
              AND status = 'active';
        END IF;

        v_available_before := GREATEST(v_row.stock - v_reserved_before, 0);
        v_insufficient := v_row.quantity > v_available_before;

        IF v_row.catalog = 'tarkett' THEN
            INSERT INTO saas_tarkett_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_row.identity,
                 v_row.quantity, 'active', NOW(), NOW());
        ELSIF v_row.catalog = 'offiho' THEN
            INSERT INTO saas_offiho_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_row.identity,
                 v_row.quantity, 'active', NOW(), NOW());
        ELSE
            INSERT INTO saas_catalog_reservations
                (supplier, internal_id, sku, quantity, usuario_id, quote_job_id,
                 status, created_at, updated_at)
            VALUES
                (v_row.catalog, v_row.identity, v_row.sku, v_row.quantity,
                 p_usuario_id, p_quote_job_id, 'active', NOW(), NOW());
        END IF;

        v_result := v_result || jsonb_build_array(jsonb_build_object(
            'catalog', v_row.catalog,
            'identity', v_row.identity,
            'reserved_before', to_char(
                v_reserved_before, 'FM999999999999999999999999990.000000'
            ),
            'available_before', to_char(
                v_available_before, 'FM999999999999999999999999990.000000'
            ),
            'insufficient', v_insufficient,
            'reserved_by_others', v_reserved_by_others
        ));
    END LOOP;

    RETURN v_result;
END;
$$;
```

The validated loop above owns counting so malformed `items` cannot reach `jsonb_array_length` first. This is the single authoritative reserve implementation used verbatim in both SQL files.

- [ ] **Step 6: Add the atomic release RPC and permissions**

Use one function call to lock the same job row, establish a terminal state for an unqueued draft, update all families and return counts. The draft-to-failed transition closes the race where release wins first and a later reserve would otherwise insert active rows:

```sql
CREATE OR REPLACE FUNCTION saas_release_mixed_cart(p_quote_job_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_tarkett INTEGER;
    v_offiho INTEGER;
    v_supplier INTEGER;
    v_job_status TEXT;
BEGIN
    SELECT status
    INTO v_job_status
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mixed quote job is invalid';
    END IF;

    IF v_job_status = 'draft' THEN
        UPDATE saas_quote_jobs
        SET status = 'failed',
            error_message = COALESCE(NULLIF(error_message, ''), 'mixed reservations released'),
            updated_at = NOW()
        WHERE id = p_quote_job_id;
    END IF;

    UPDATE saas_tarkett_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_tarkett = ROW_COUNT;

    UPDATE saas_offiho_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_offiho = ROW_COUNT;

    UPDATE saas_catalog_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_supplier = ROW_COUNT;

    RETURN jsonb_build_object(
        'tarkett', v_tarkett, 'offiho', v_offiho, 'supplier', v_supplier
    );
END;
$$;

REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM anon;
REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) TO service_role;

REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM anon;
REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_release_mixed_cart(UUID) TO service_role;
```

Because reserve also locks this row before checking `status='draft'`, either order is safe: reserve-first is subsequently released, while release-first changes the draft to failed and the waiting reserve is rejected. Mirror this status/lock behavior inside the single DEV critical section. Copy the complete additive block verbatim into `create_tables.sql`; do not alter existing tables or RPCs.

- [ ] **Step 7: Add red DEV rollback, aggregation and serialization tests**

Add to `tests/test_quote_jobs_api.py`:

```python
JOB_MIXED_UUID = "11111111-1111-4111-8111-111111111111"
JOB_A_UUID = "22222222-2222-4222-8222-222222222222"
JOB_B_UUID = "33333333-3333-4333-8333-333333333333"


def test_mixed_dev_reservation_saves_once_only_after_all_groups_validate(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID, user_id=7)
    saves = []
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(index, "_dev_save", lambda data: saves.append(data))
    groups = [
        {"catalog": "tarkett", "items": [{"identity": "T-1", "sku": "T-1", "quantity": "1", "stock": "5"}]},
        {"catalog": "alma", "items": [{"identity": "alma:desk", "sku": "AL-1", "quantity": "bad", "stock": "5"}]},
    ]
    with pytest.raises(RuntimeError, match="reserva mixta"):
        index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, groups)
    assert saves == []


def test_mixed_dev_reservation_serializes_availability_under_one_lock(monkeypatch):
    state = dev_state_with_two_draft_jobs(JOB_A_UUID, JOB_B_UUID, user_id=7)
    configure_thread_safe_dev_store(monkeypatch, state)
    groups = [{
        "catalog": "offiho",
        "items": [{"identity": "OFF-1", "sku": "OFF-1", "quantity": "3", "stock": "5"}],
    }]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(index.db_reserve_mixed_cart, 7, JOB_A_UUID, groups)
        second = pool.submit(index.db_reserve_mixed_cart, 7, JOB_B_UUID, groups)
        snapshots = [first.result(), second.result()]
    assert sorted(row[0]["reserved_before"] for row in snapshots) == ["0.000000", "3.000000"]
```

The helper fixtures in the test must create `quote_jobs`, `tarkett_reservations`, `offiho_reservations` and `catalog_reservations` arrays explicitly; do not depend on developer machine state. Assert every snapshot decimal is a six-place string, not a Python float/JSON number.

Add two deterministic two-thread DEV tests using events around the shared lock: reserve-first then release must end with every inserted row `released`; release-first must transition the job to `failed`, make the waiting reserve raise the invalid-job error and leave zero active rows. Repeat one case through the legacy Tarkett wrapper to prove it shares the same lifecycle lock. Add a legacy generic-supplier versus mixed-ALMA reservation test for the same `internal_id` and assert the serialized snapshots are `0.000000` then the first quantity, never two stale zero snapshots. Add a released-row retry case and assert the same job ID is rejected before any new insert, matching the unfiltered unique indexes.

- [ ] **Step 8: Implement the Python RPC boundary and DEV critical section**

Reuse the existing DEV catalog reservation lock as the one namespace for every family; do not introduce an independent lock that can race with `db_reserve_catalog_items`:

```python
_MIXED_CART_RESERVATION_LOCK = _DEV_CATALOG_RESERVATION_LOCK
```

`db_reserve_mixed_cart` must normalize every decimal to six places before entering storage. Its non-DEV branch always makes exactly one database call, including `groups=[]`, so database ownership and `draft` status are validated uniformly:

```python
def db_reserve_mixed_cart(usuario_id, quote_job_id, groups):
    try:
        clean_user_id = int(usuario_id)
        clean_job_id = str(uuid.UUID(str(quote_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Cotizacion de reserva mixta invalida") from None
    if clean_user_id <= 0:
        raise RuntimeError("Cotizacion de reserva mixta invalida")
    normalized = _normalize_mixed_reservation_groups(groups)
    if DEV_MODE:
        response = _dev_reserve_mixed_cart(clean_user_id, clean_job_id, normalized)
    elif _use_postgres():
        rows = _pg_rows(
            "SELECT saas_reserve_mixed_cart(%s, %s, %s::jsonb) AS snapshot",
            (clean_user_id, clean_job_id, json.dumps(normalized, separators=(",", ":"))),
        )
        response = rows[0].get("snapshot") if len(rows) == 1 else None
    else:
        response = _supabase_req(
            "POST",
            "/rpc/saas_reserve_mixed_cart",
            json_data={
                "p_usuario_id": clean_user_id,
                "p_quote_job_id": clean_job_id,
                "p_groups": normalized,
            },
        )
    return _validate_mixed_reservation_response(response, normalized)
```

Implement the Python normalization and DEV core explicitly; both remote branches consume the same `normalized` list and the response validator below:

```python
_MIXED_RESERVATION_CATALOGS = (
    "tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro"
)


def _mixed_reservation_decimal(value, field, *, positive):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?", value
    ):
        raise RuntimeError("Reserva mixta invalida")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError("Reserva mixta invalida") from None
    scale = max(-number.as_tuple().exponent, 0)
    lower_ok = number > 0 if positive else number >= 0
    maximum = Decimal("1000000") if positive else Decimal("1000000000")
    if not number.is_finite() or not lower_ok or number > maximum or scale > 6:
        raise RuntimeError("Reserva mixta invalida")
    return f"{number:.6f}"


def _mixed_reservation_text(value, field, *, allow_empty=False):
    if not isinstance(value, str):
        raise RuntimeError("Reserva mixta invalida")
    text = value.strip()
    if (not text and not allow_empty) or len(text) > 500:
        raise RuntimeError("Reserva mixta invalida")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in text):
        raise RuntimeError("Reserva mixta invalida")
    return text


def _mixed_reservation_result_decimal(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,6})?", value
    ):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    number = Decimal(value)
    if not number.is_finite() or number > Decimal("99999999999999.999999"):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    return f"{number:.6f}"


def _normalize_mixed_reservation_groups(groups):
    if not isinstance(groups, list) or len(groups) > 7:
        raise RuntimeError("Reserva mixta invalida")
    normalized = []
    seen_catalogs = set()
    seen_keys = set()
    total = 0
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"catalog", "items"}:
            raise RuntimeError("Reserva mixta invalida")
        catalog = str(group.get("catalog") or "").strip()
        items = group.get("items")
        if catalog not in _MIXED_RESERVATION_CATALOGS or catalog in seen_catalogs:
            raise RuntimeError("Reserva mixta invalida")
        if not isinstance(items, list) or not items:
            raise RuntimeError("Reserva mixta invalida")
        seen_catalogs.add(catalog)
        clean_items = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"identity", "sku", "quantity", "stock"}:
                raise RuntimeError("Reserva mixta invalida")
            identity = _mixed_reservation_text(item.get("identity"), "identity")
            sku = _mixed_reservation_text(
                item.get("sku"), "sku", allow_empty=catalog in {"sonara", "lumbro"}
            )
            key = (catalog, identity)
            if key in seen_keys:
                raise RuntimeError("Reserva mixta invalida")
            seen_keys.add(key)
            clean_items.append({
                "identity": identity,
                "sku": sku,
                "quantity": _mixed_reservation_decimal(
                    item.get("quantity"), "quantity", positive=True
                ),
                "stock": _mixed_reservation_decimal(
                    item.get("stock"), "stock", positive=False
                ),
            })
            total += 1
            if total > 500:
                raise RuntimeError("Reserva mixta invalida")
        normalized.append({
            "catalog": catalog,
            "items": sorted(clean_items, key=lambda row: row["identity"]),
        })
    return sorted(
        normalized, key=lambda group: _MIXED_RESERVATION_CATALOGS.index(group["catalog"])
    )
```

In all three API copies, add `import unicodedata` and extend the decimal import to `from decimal import Decimal, InvalidOperation, ROUND_HALF_UP`. In the DEV branch, use this complete load/validate/compute/save structure; `_dev_save` is deliberately below every possible stored-row/input validation:

```python
def _dev_reserve_mixed_cart(clean_user_id, clean_job_id, normalized):
    with _MIXED_CART_RESERVATION_LOCK:
        data = _dev_load()
        job = next(
            (row for row in data.get("quote_jobs", []) if str(row.get("id")) == clean_job_id),
            None,
        )
        if (
            not job
            or int(job.get("usuario_id") or 0) != clean_user_id
            or job.get("status") != "draft"
        ):
            raise RuntimeError("Cotizacion de reserva mixta invalida")

        tables = {
            "tarkett": data.setdefault("tarkett_reservations", []),
            "offiho": data.setdefault("offiho_reservations", []),
            "supplier": data.setdefault("catalog_reservations", []),
        }
        if any(
            str(row.get("quote_job_id") or "") == clean_job_id
            for rows in tables.values() for row in rows
        ):
            raise RuntimeError("La cotizacion ya tiene reservas mixtas")

        now = _iso(datetime.now(timezone.utc))
        snapshot = []
        pending = {name: [] for name in tables}
        for group in normalized:
            catalog = group["catalog"]
            table_name = catalog if catalog in {"tarkett", "offiho"} else "supplier"
            identity_field = "product_code" if table_name != "supplier" else "internal_id"
            for item in group["items"]:
                reserved_before = Decimal(0)
                reserved_by_others = False
                for row in tables[table_name]:
                    same_identity = row.get(identity_field) == item["identity"]
                    same_supplier = table_name != "supplier" or row.get("supplier") == catalog
                    if not same_identity or not same_supplier or row.get("status") != "active":
                        continue
                    try:
                        stored = Decimal(str(row.get("quantity")))
                    except (InvalidOperation, TypeError, ValueError):
                        raise RuntimeError("Reserva mixta almacenada invalida") from None
                    if not stored.is_finite() or stored <= 0:
                        raise RuntimeError("Reserva mixta almacenada invalida")
                    reserved_before += stored
                    if reserved_before > Decimal("99999999999999.999999"):
                        raise RuntimeError("Reserva mixta almacenada invalida")
                    reserved_by_others |= int(row.get("usuario_id") or 0) != clean_user_id

                quantity = Decimal(item["quantity"])
                stock = Decimal(item["stock"])
                available_before = max(stock - reserved_before, Decimal(0))
                snapshot.append({
                    "catalog": catalog,
                    "identity": item["identity"],
                    "reserved_before": f"{reserved_before:.6f}",
                    "available_before": f"{available_before:.6f}",
                    "insufficient": quantity > available_before,
                    "reserved_by_others": reserved_by_others,
                })
                common = {
                    "id": str(uuid.uuid4()), "usuario_id": clean_user_id,
                    "quote_job_id": clean_job_id, "quantity": item["quantity"],
                    "status": "active", "created_at": now, "updated_at": now,
                }
                if table_name == "supplier":
                    pending[table_name].append({
                        **common, "supplier": catalog, "internal_id": item["identity"],
                        "sku": item["sku"],
                    })
                else:
                    pending[table_name].append({
                        **common, "product_code": item["identity"]
                    })

        if snapshot:
            for name, rows in pending.items():
                tables[name].extend(rows)
            _dev_save(data)
        return snapshot
```

Validate DEV/PostgreSQL/Supabase through the same exact response boundary:

```python
def _validate_mixed_reservation_response(response, normalized):
    fields = {
        "catalog", "identity", "reserved_before", "available_before",
        "insufficient", "reserved_by_others",
    }
    expected = {
        (group["catalog"], item["identity"]): item
        for group in normalized for item in group["items"]
    }
    if not isinstance(response, list) or len(response) != len(expected):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    seen = set()
    result = []
    for candidate in response:
        if not isinstance(candidate, dict) or set(candidate) != fields:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        key = (candidate.get("catalog"), candidate.get("identity"))
        if key not in expected or key in seen:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        seen.add(key)
        reserved = _mixed_reservation_result_decimal(candidate.get("reserved_before"))
        available = _mixed_reservation_result_decimal(candidate.get("available_before"))
        if type(candidate.get("insufficient")) is not bool or type(
            candidate.get("reserved_by_others")
        ) is not bool:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        item = expected[key]
        expected_available = max(
            Decimal(item["stock"]) - Decimal(reserved), Decimal(0)
        )
        expected_insufficient = Decimal(item["quantity"]) > expected_available
        if Decimal(available) != expected_available or candidate["insufficient"] != expected_insufficient:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        result.append({
            "catalog": key[0], "identity": key[1],
            "reserved_before": reserved, "available_before": available,
            "insufficient": candidate["insufficient"],
            "reserved_by_others": candidate["reserved_by_others"],
        })
    if seen != set(expected):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    return sorted(
        result,
        key=lambda row: (
            _MIXED_RESERVATION_CATALOGS.index(row["catalog"]), row["identity"]
        ),
    )
```

In DEV, hold `_MIXED_CART_RESERVATION_LOCK`, load once, validate the owned `draft` job and all groups, calculate all snapshots against the in-memory copy, append all reservation rows, then call `_dev_save(data)` once when at least one reservation was added. Format `reserved_before` and `available_before` as six-place strings. If any validation raises, do not catch it inside the lock and do not save. Keep the existing generic supplier reserve branch under that same underlying lock. Add a test asserting an all-made-to-order projection loads once, validates the owned draft, returns `[]`, performs zero saves and would be rejected for a missing/foreign/non-draft job.

- [ ] **Step 9: Route legacy Tarkett/Offiho creation through the same lock namespace**

Refactor `db_create_tarkett_reservations` and `db_create_offiho_reservations` to project their existing authoritative cart lines into one-group calls to `db_reserve_mixed_cart`; do not perform direct row-by-row inserts anymore:

```python
def _legacy_reservation_decimal(value, field: str, *, positive: bool) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError("Reserva mixta invalida") from None
    maximum = Decimal("1000000") if positive else Decimal("1000000000")
    lower_ok = number > 0 if positive else number >= 0
    if not number.is_finite() or not lower_ok or number > maximum:
        raise RuntimeError("Reserva mixta invalida")
    normalized = number.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if positive and normalized <= 0:
        raise RuntimeError("Reserva mixta invalida")
    return f"{normalized:.6f}"


def _legacy_mixed_group(catalog: str, lines: list[dict]) -> list[dict]:
    identity_field = "code" if catalog == "tarkett" else "inventory_key"
    return [{
        "catalog": catalog,
        "items": [{
            "identity": str(line[identity_field]),
            "sku": str(line.get("sku") or line[identity_field]),
            "quantity": _legacy_reservation_decimal(
                line["quantity"], "quantity", positive=True
            ),
            "stock": _legacy_reservation_decimal(
                line["available_quantity"], "stock", positive=False
            ),
        } for line in lines],
    }]
```

Each wrapper calls `db_reserve_mixed_cart(usuario_id, quote_job_id, projected)` exactly once, then returns a compatibility list with one entry per requested product and the corresponding snapshot audit fields (`reserved_before`, `available_before`, `insufficient`, `reserved_by_others`); routes do not use those values. Update direct DEV tests to include an owned `draft` job and `available_quantity`. Feed each wrapper the actual numeric `quantity` and `available_quantity` emitted by its real legacy builder (`int`/`float`), capture the projected group and assert both fields are six-place strings before the mixed boundary. Generic supplier legacy reservations continue through `saas_reserve_catalog_items`, whose `<supplier>:<internal_id>` advisory key already matches the mixed RPC in PostgreSQL. In DEV, its existing `_DEV_CATALOG_RESERVATION_LOCK` is the same object aliased above, so the generic and mixed code paths also serialize.

Add a two-thread DEV test in which one call uses `db_create_tarkett_reservations` and the other `db_reserve_mixed_cart` for the same Tarkett identity; assert their snapshots observe serialized `reserved_before` values. Add the equivalent Offiho test and a barrier-controlled `db_reserve_catalog_items` versus mixed ALMA test. For non-DEV, assert neither legacy wrapper calls direct table insert helpers and both issue only `/rpc/saas_reserve_mixed_cart`.

- [ ] **Step 10: Implement idempotent mixed release and cleanup routing**

`db_release_mixed_cart` uses the same lock/load/save discipline in DEV and one `saas_release_mixed_cart` call in Postgres/Supabase. Implement its DEV branch exactly once:

```python
def _dev_release_mixed_cart(clean_job_id):
    with _MIXED_CART_RESERVATION_LOCK:
        data = _dev_load()
        job = next(
            (row for row in data.get("quote_jobs", []) if str(row.get("id")) == clean_job_id),
            None,
        )
        if not job:
            raise RuntimeError("Cotizacion de reserva mixta invalida")
        now = _iso(datetime.now(timezone.utc))
        changed = False
        if job.get("status") == "draft":
            job.update({
                "status": "failed",
                "error_message": job.get("error_message") or "mixed reservations released",
                "updated_at": now,
            })
            changed = True
        counts = {"tarkett": 0, "offiho": 0, "supplier": 0}
        for name, key in (
            ("tarkett_reservations", "tarkett"),
            ("offiho_reservations", "offiho"),
            ("catalog_reservations", "supplier"),
        ):
            for row in data.setdefault(name, []):
                if (
                    str(row.get("quote_job_id") or "") == clean_job_id
                    and row.get("status") == "active"
                ):
                    row.update({"status": "released", "updated_at": now})
                    counts[key] += 1
                    changed = True
        if changed:
            _dev_save(data)
        return counts
```

Validate `quote_job_id` through `uuid.UUID` before this call. For PostgreSQL select `saas_release_mixed_cart(%s) AS snapshot`; for Supabase call only `/rpc/saas_release_mixed_cart` with `{"p_quote_job_id": clean_job_id}`. Require the returned object to have exactly integer nonnegative `tarkett,offiho,supplier` keys. The DEV job transition is saved even when no reservation rows exist; a second release returns zero counts without another save unless a transition/row actually changed.

Queue a mixed job only with a compare-and-set that shares the DEV lifecycle lock and has the same row-lock serialization as the release RPC in PostgreSQL. Never call the unconditional `db_update_quote_job` for this transition:

```python
def db_queue_mixed_quote_job(quote_job_id: str, metadata: dict) -> dict:
    try:
        clean_job_id = str(uuid.UUID(str(quote_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Cotizacion mixta invalida") from None
    if not isinstance(metadata, dict):
        raise RuntimeError("Cotizacion mixta invalida")
    now = _iso(datetime.now(timezone.utc))
    payload = {
        "status": "queued", "metadata": deepcopy(metadata),
        "error_message": None, "updated_at": now,
    }
    if DEV_MODE:
        with _MIXED_CART_RESERVATION_LOCK:
            data = _dev_load()
            matches = [
                row for row in data.get("quote_jobs", [])
                if str(row.get("id")) == clean_job_id
            ]
            if len(matches) != 1 or matches[0].get("status") != "draft":
                raise RuntimeError("La cotizacion mixta ya no esta en borrador")
            matches[0].update(payload)
            _dev_save(data)
            row = deepcopy(matches[0])
    elif _use_postgres():
        row = _pg_write(
            """
            UPDATE saas_quote_jobs
            SET status = 'queued', metadata = %s, error_message = NULL, updated_at = %s
            WHERE id = %s AND status = 'draft'
            RETURNING *
            """,
            (payload["metadata"], now, clean_job_id),
        )
    else:
        rows = _supabase_req(
            "PATCH", "/saas_quote_jobs",
            params={"id": f"eq.{clean_job_id}", "status": "eq.draft"},
            json_data=payload,
        )
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
    if not isinstance(row, dict) or row.get("status") != "queued":
        raise RuntimeError("La cotizacion mixta ya no esta en borrador")
    return row
```

Use deterministic valid UUIDs to test `reserve -> release -> queue` in DEV: release must set the draft to `failed`, the compare-and-set must raise, no row may end `queued`, and all reservations must be `released`. Add PostgreSQL SQL-shape and Supabase request-shape tests proving the update/PATCH both filter `status='draft'` and require exactly one returned row.

Also wrap the DEV load/mutate/save branches of `db_release_tarkett_reservations`, `db_release_offiho_reservations` and `db_release_catalog_reservations` in that same underlying `_DEV_CATALOG_RESERVATION_LOCK`; otherwise a legacy cleanup can overwrite a concurrent mixed reservation's full DEV-state save. Add a barrier-based legacy-release versus mixed-reserve test that proves both the released legacy row and newly inserted mixed row survive. Update `_release_quote_reservations`:

```python
metadata = _quote_job_metadata(job)
if metadata.get("source_type") == "mixed_catalog_cart":
    db_release_mixed_cart(job_id)
    return
db_release_tarkett_reservations(job_id)
db_release_offiho_reservations(job_id)
db_release_catalog_reservations(job_id)
```

This preserves all legacy release paths and makes deletion/retention atomic only for mixed jobs.

- [ ] **Step 11: Prove one RPC call and a validated snapshot response**

Add a non-DEV test that monkeypatches `_supabase_req` and asserts its only captured request is:

```python
assert captured == [{
    "method": "POST",
    "path": "/rpc/saas_reserve_mixed_cart",
    "json_data": {
        "p_usuario_id": 7,
        "p_quote_job_id": "11111111-1111-1111-1111-111111111111",
        "p_groups": normalized_groups,
    },
}]
```

Return a row with an unknown or duplicate identity and assert `RuntimeError("Respuesta de reserva mixta invalida")`; the API must never apply an unverified snapshot.

- [ ] **Step 12: Add an opt-in PostgreSQL execution test with rollback**

Create `tests/test_mixed_catalog_postgres.py`. At module collection, read the two opt-ins first and call `pytest.skip("Postgres mixed-cart test is opt-in", allow_module_level=True)` unless `MIXED_CART_TEST_DATABASE_URL` is nonempty and `MIXED_CART_TEST_ALLOW_DDL=1`; only then call `psycopg = pytest.importorskip("psycopg")`. Parse DSNs with `psycopg.conninfo.conninfo_to_dict`, compare the normalized `(host,port,dbname,user)` tuple against `DATABASE_URL`, `POSTGRES_URL` and `SUPABASE_DB_URL`, and fail on any match even if URL encoding or parameter order differs. Require the parsed and actual `current_database()` name to start with `test_` or end with `_test`. The named database is explicitly disposable. Never point this test at production or run the Supabase migration command from the test.

Use `psycopg` and one connection transaction. Before any DDL, require `to_regclass('public.<table>') IS NULL` for all four reservation/job table names; otherwise fail with `La base de prueba debe estar vacia` so the function cannot touch a real table ahead of `pg_temp` in its search path. Require `SELECT gen_random_uuid()` to succeed, then create these exact minimal TEMP schemas (the release RPC needs `error_message`/`updated_at` even when the reserve test does not):

```sql
CREATE TEMP TABLE saas_quote_jobs (
    id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, status TEXT NOT NULL,
    error_message TEXT, updated_at TIMESTAMPTZ
);
CREATE TEMP TABLE saas_tarkett_reservations (
    id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, quote_job_id UUID NOT NULL,
    product_code TEXT NOT NULL, quantity NUMERIC NOT NULL, status TEXT NOT NULL,
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
);
CREATE TEMP TABLE saas_offiho_reservations (
    id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, quote_job_id UUID NOT NULL,
    product_code TEXT NOT NULL, quantity NUMERIC NOT NULL, status TEXT NOT NULL,
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
);
CREATE TEMP TABLE saas_catalog_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), supplier TEXT NOT NULL,
    internal_id TEXT NOT NULL, sku TEXT NOT NULL, quantity NUMERIC NOT NULL,
    usuario_id INTEGER NOT NULL, quote_job_id UUID, status TEXT NOT NULL,
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
);
```

Extract and execute only the two `CREATE OR REPLACE FUNCTION` blocks delimited by their `AS $$` and closing `$$;` markers from the additive migration, and always call `connection.rollback()` in `finally`. The test must:

1. insert two owned draft jobs and prove `saas_reserve_mixed_cart(7, '11111111-1111-4111-8111-111111111111'::uuid, '[]'::jsonb)` returns `[]`; without ending the transaction, create a savepoint, call the same function for the second job with one valid Tarkett row, assert success, then `ROLLBACK TO` that savepoint. This explicitly proves consecutive invocations reuse the transaction-local staging table;
2. use a savepoint, send a valid first group plus an invalid second group, catch the database exception, roll back to the savepoint and prove all three reservation tables still contain zero rows;
3. reserve one Tarkett and one ALMA row successfully, assert the grouped snapshot identities, call `saas_release_mixed_cart`, and assert both rows are `released`;
4. query the exact `to_regprocedure('saas_reserve_mixed_cart(integer,uuid,jsonb)')` and `to_regprocedure('saas_release_mixed_cart(uuid)')` OIDs in `pg_proc` before the outer rollback and assert both transaction-scoped test definitions have `prosecdef = true` plus `search_path=public, pg_temp` in `proconfig`.

The extraction helper is intentionally bounded to the function terminator:

```python
def _function_definition(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION\s+{re.escape(name)}\b[\s\S]*?\n\$\$;",
        sql,
        flags=re.IGNORECASE,
    )
    assert match, f"Funcion ausente: {name}"
    return match.group(0)
```

Run it explicitly when the disposable database is available:

```powershell
$env:MIXED_CART_TEST_DATABASE_URL = "postgresql://localhost/mobiliti_mixed_cart_test"
$env:MIXED_CART_TEST_ALLOW_DDL = "1"
python -m pytest tests/test_mixed_catalog_postgres.py -q
```

Expected: PASS and zero persistent schema/data changes because the final rollback restores any prior function definitions. Without both opt-in variables, one documented SKIP is expected and does not replace the mandatory static and DEV tests.

- [ ] **Step 13: Run SQL/API reservation gates**

```powershell
python -m pytest tests/test_catalog_migrations.py -k "mixed_cart" -q
python -m pytest tests/test_quote_jobs_api.py -k "mixed and reservation" -q
python -m pytest tests/test_mixed_catalog_postgres.py -q
```

Expected: mandatory tests PASS, including rollback, one-save DEV behavior, one remote RPC and concurrent serialization; PostgreSQL test PASS when explicitly configured or emits its single documented SKIP.

- [ ] **Step 14: Mirror APIs and commit the additive reservation layer**

Copy `mobiliti_saas/api/index.py` byte-for-byte to both deployable copies, then run the existing SHA test. Commit only this task:

```powershell
python -m pytest tests/test_quote_jobs_api.py -k "deployable_api_copies or mixed and reservation" -q
git add -- mobiliti_saas/supabase_setup/2026_07_mixed_catalog_cart.sql mobiliti_saas/supabase_setup/create_tables.sql mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py vercel_deploy/api/index.py tests/test_catalog_migrations.py tests/test_mixed_catalog_postgres.py
git add -- tests/test_quote_jobs_api.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(quote): reservar carrito mixto atomicamente"
```

---

### Task 4: Create one mixed quote job through one endpoint

**Files:**

- Modify: `mobiliti_saas/api/index.py:31,35-60,3600-3750,4157-4185`
- Modify: `mobiliti_saas/web/api/index.py:31,35-60,3600-3750,4157-4185`
- Modify: `vercel_deploy/api/index.py:31,35-60,3600-3750,4157-4185`
- Modify: `tests/test_quote_jobs_api.py`

**Interfaces:**

- Consumes: `build_mixed_catalog_cart_payload`, `validate_mixed_catalog_payload`, `build_mixed_reservation_groups`, `db_reserve_mixed_cart`, `db_release_mixed_cart`, `db_queue_mixed_quote_job`, catalog loaders, `_validate_metadata`, `_storage_upload_bytes`.
- Produces: authenticated `POST /catalogs/mixed-quote`; exactly one `draft -> queued` job; one `users/{usuario_id}/jobs/{job_id}/input.json`; `_apply_mixed_reservation_snapshot(cart_payload: dict, snapshot: list[dict]) -> None`; response `{"mensaje": str, "job": dict}`.
- Cleanup: any failure after job creation calls `_cleanup_failed_catalog_quote(job_id, input_path, db_release_mixed_cart)` and retains no usable partial job.

- [ ] **Step 1: Add red route, auth and strict-body tests**

Add to `tests/test_quote_jobs_api.py`:

```python
def test_mixed_quote_route_is_registered_before_supplier_quote_route():
    post_paths = [
        route.path for route in index.app.routes
        if "POST" in getattr(route, "methods", set())
    ]
    assert "/catalogs/mixed-quote" in post_paths
    assert post_paths.index("/catalogs/mixed-quote") < post_paths.index("/catalogs/{supplier}/quote")


def test_mixed_quote_requires_authentication_before_catalog_loading(monkeypatch):
    loaded = []
    monkeypatch.setattr(index, "_load_tarkett_catalog_cached", lambda: loaded.append("tarkett"))
    response = _client().post("/catalogs/mixed-quote", json=_valid_mixed_body())
    assert response.status_code == 401
    assert loaded == []


@pytest.mark.parametrize(
    "field",
    ("unit_price", "base_currency", "exchange_rate", "stock", "image_url", "product_url"),
)
def test_mixed_quote_rejects_unexpected_browser_fields_before_job_creation(monkeypatch, field):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    body = _valid_mixed_body()
    body["items"][0][field] = "tampered"
    response = _client().post("/catalogs/mixed-quote", headers=_auth_headers(), json=body)
    assert response.status_code == 400
    assert "Campo mixto no permitido" in response.json()["detail"]
    assert state["jobs"] == []
    assert state["uploads"] == []
```

Add an authenticated companion that monkeypatches `_require_active_subscription` to capture its argument and asserts it receives integer user ID `7`, not the user dictionary, before any catalog load.

- [ ] **Step 2: Run the endpoint cases and observe HTTP 404**

```powershell
python -m pytest tests/test_quote_jobs_api.py -k "mixed_quote_route or mixed_quote_requires or unexpected_browser" -q
```

Expected: FAIL because `/catalogs/mixed-quote` is not registered.

- [ ] **Step 3: Define and enforce the exact top-level request allowlist**

Add near the catalog constants:

```python
MIXED_QUOTE_BODY_FIELDS = frozenset({
    "items", "quote_currency", "descuento", "proyecto", "cliente", "correo",
    "telefono", "direccion", "razon_social", "cotizacion", "template",
    "description_language", "image_provider", "image_cleanup_strength",
    "image_background", "image_prompt",
})
```

Import `Request` from FastAPI and `json`. Do not declare a FastAPI `Body` parameter: read this one endpoint's body as a bounded stream so a huge identity/add-on array cannot be parsed or sorted before the limit. After authentication/subscription and before any catalog/rate/job call, enforce `Content-Length` when present, stream chunks with a running limit, then decode exactly one JSON value:

```python
def _mixed_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Campo JSON duplicado: {key}")
        result[key] = value
    return result


def _reject_mixed_json_constant(value):
    raise ValueError(f"Constante JSON invalida: {value}")


async def _read_mixed_quote_body(request: Request) -> object:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            declared = int(raw_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length invalido") from exc
        if declared < 0 or declared > MAX_MIXED_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Solicitud mixta demasiado grande")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_MIXED_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Solicitud mixta demasiado grande")
        chunks.append(chunk)
    try:
        return json.loads(
            b"".join(chunks), object_pairs_hook=_mixed_json_object,
            parse_constant=_reject_mixed_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Solicitud mixta invalida") from exc


body = await _read_mixed_quote_body(request)
if not isinstance(body, dict):
    raise HTTPException(status_code=400, detail="Solicitud mixta invalida")
unexpected = set(body) - MIXED_QUOTE_BODY_FIELDS
if unexpected:
    raise HTTPException(status_code=400, detail=f"Campo de cotizacion no permitido: {min(unexpected)}")
raw_items = body.get("items")
if not isinstance(raw_items, list):
    raise HTTPException(status_code=400, detail="Items mixtos debe ser una lista")
if not 1 <= len(raw_items) <= MAX_MIXED_CATALOG_LINES:
    raise HTTPException(status_code=400, detail="El carrito mixto debe contener entre 1 y 500 filas")
try:
    preflight_items = preflight_mixed_catalog_items(raw_items)
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
```

Add authenticated `json=[]`, `items=[]` and 501-row tests that expect HTTP 400 and zero catalog/rate/job/storage calls. Send both a declared-oversize body and a chunked/omitted-length body that crosses `MAX_MIXED_REQUEST_BYTES`; expect HTTP 413. Send raw JSON with a duplicate `items` key and with `quantity: NaN`; both must return 400 before any catalog loader. Add oversized/control-character identity, 201-add-on and 65-character quantity cases; expect HTTP 400 before any catalog loader, rate query, job or storage call. The nested allowlist remains centralized in `preflight_mixed_catalog_items`/the pure builder; do not duplicate price-bearing fields in API models.

- [ ] **Step 4: Load authoritative catalogs only for requested groups**

After `_require_active_subscription`, validate catalog names from the browser rows, then load:

```python
requested_catalogs = {
    str(row.get("catalog") or "").strip().lower()
    for row in preflight_items
}
if not requested_catalogs or not requested_catalogs <= set(MIXED_CATALOG_ORDER):
    raise HTTPException(status_code=400, detail="Catalogo mixto no soportado")

catalogs = {}
if "tarkett" in requested_catalogs:
    catalogs["tarkett"] = _load_tarkett_catalog_cached()
if "offiho" in requested_catalogs:
    catalogs["offiho"] = _load_offiho_catalog_cached()
for supplier in sorted(requested_catalogs - {"tarkett", "offiho"}):
    _require_enabled_catalog_supplier(supplier)
    catalogs[supplier] = _load_supplier_catalog_cached(supplier)
rate_rows = db_list_exchange_rates()
```

Then call the pure builder with `preflight_items`, `quote_currency=str(body.get("quote_currency") or "MXN")` and `commercial_discount_percent=body.get("descuento", "40")`. The builder deliberately repeats its own preflight so direct callers remain safe. Convert `ValueError` to HTTP 400 with its catalog/identity message; loading/database failures remain HTTP 503.

- [ ] **Step 5: Add red one-job, server-authority and metadata tests**

The successful API test must capture calls in order and decode the one upload:

```python
assert response.status_code == 200
assert events == ["create_job", "reserve_mixed", "upload", "queue", "wake"]
assert len(created_jobs) == 1
assert len(uploaded_inputs) == 1
payload = json.loads(uploaded_inputs[0]["content"])
assert payload["source_type"] == "mixed_catalog_cart"
assert payload["item_count"] == 3
assert {group["catalog"] for group in payload["groups"]} == {"tarkett", "sonara", "alma"}
assert response.json()["job"]["id"] == created_jobs[0]["id"]
metadata = created_jobs[0]["metadata"]
assert metadata["source_type"] == "mixed_catalog_cart"
assert metadata["mixed_item_count"] == 3
assert metadata["catalog_item_counts"] == {"tarkett": 1, "sonara": 1, "alma": 1}
assert metadata["catalog_source_hashes"] == {
    group["catalog"]: group["catalog_source_hash"] for group in payload["groups"]
}
assert metadata["quote_currency"] == "MXN"
assert metadata["rate_summary"] == payload["rate_summary"]
assert metadata["auto_electrification_rate"] == payload["auto_electrification_rate"]
```

Assert the uploaded lines use fixture-catalog prices after the request body was given no price fields.

- [ ] **Step 6: Create one draft job, reserve, upload and queue**

Register this route before `/catalogs/{supplier}/quote`:

```python
@app.post("/catalogs/mixed-quote")
async def mixed_catalog_quote(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    _require_active_subscription(current_user["id"])
    body = await _read_mixed_quote_body(request)
```

Build metadata with one assigned folio and these exact additions:

```python
metadata = _validate_metadata({
    **body,
    "image_provider": body.get("image_provider") or "pillow",
})
metadata.update({
    "source_type": "mixed_catalog_cart",
    "original_filename": "mixed-catalog-cart.json",
    "input_extension": ".json",
    "storage_provider": _storage_provider_name(),
    "input_storage_provider": _storage_provider_name(),
    "mixed_item_count": cart_payload["item_count"],
    "catalog_item_counts": {
        group["catalog"]: len(group["items"]) for group in cart_payload["groups"]
    },
    "catalog_source_hashes": {
        group["catalog"]: group["catalog_source_hash"] for group in cart_payload["groups"]
    },
    "quote_currency": cart_payload["quote_currency"],
    "rate_summary": cart_payload["rate_summary"],
    "auto_electrification_rate": cart_payload["auto_electrification_rate"],
    "estimated_duration_seconds": 120,
})

assigned_quote_number = _next_quote_number_for_user(current_user)
if assigned_quote_number:
    metadata["cotizacion"] = assigned_quote_number
elif not metadata.get("cotizacion"):
    metadata["cotizacion"] = metadata["proyecto"]

template = str(body.get("template") or "Formato Cotizacion 2026 GDL (1).xlsx").strip()
if not template:
    raise HTTPException(status_code=400, detail="Template requerido")
_enforce_active_quote_limit(current_user["id"])
```

This deliberately reuses the legacy customer-field limits, image settings, quote-number assignment, template validation and active-job quota before creating `job_id`; do not create a weaker parallel validator.

Only after every preflight above succeeds, define the identifiers once:

```python
job_id = str(uuid.uuid4())
input_path = f"users/{current_user['id']}/jobs/{job_id}/input.json"
```

The state-changing block must keep this order and every line, including draft creation, must be inside the compensating `try` from Step 9:

```python
try:
    db_create_quote_job(current_user["id"], template, metadata, input_path, job_id=job_id)
    snapshot = db_reserve_mixed_cart(
        current_user["id"], job_id, build_mixed_reservation_groups(cart_payload)
    )
    _apply_mixed_reservation_snapshot(cart_payload, snapshot)
    validate_mixed_catalog_payload(cart_payload)
    content = json.dumps(
        cart_payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    _storage_upload_bytes(input_path, content, "application/json")
    updated = db_queue_mixed_quote_job(job_id, metadata)
except Exception as exc:
    _cleanup_failed_catalog_quote(job_id, input_path, db_release_mixed_cart)
    raise HTTPException(
        status_code=503, detail="No fue posible crear cotizacion mixta"
    ) from exc
```

Wake the worker only after the queued update succeeds.

- [ ] **Step 7: Validate and apply the complete reservation snapshot**

`_apply_mixed_reservation_snapshot` must derive its expected `(catalog, identity)` keys only from lines whose `reservation` is not `None`, aggregating requested quantity exactly as `build_mixed_reservation_groups` does. Then index every response row, reject missing/extra/duplicate keys, validate nonnegative finite decimals and booleans, and verify `available_before == max(stock - reserved_before, 0)` plus `insufficient == (aggregated_quantity > available_before)`. Only after the complete snapshot passes may it mutate the payload. Thus an all-made-to-order payload plus `snapshot=[]` succeeds without adding availability fields. The API modules already import `unicodedata`; add the following exact-normalized append helper beside `_apply_mixed_reservation_snapshot` so a supplier warning and the reservation result cannot duplicate the canonical availability warning:

```python
def _append_mixed_warning_once(line: dict[str, Any], warning: str) -> None:
    def normalized(value: object) -> str:
        decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
        without_marks = "".join(
            character for character in decomposed
            if not unicodedata.combining(character)
        )
        return " ".join(without_marks.split())

    warning_key = normalized(warning)
    if all(normalized(current) != warning_key for current in line["warnings"]):
        line["warnings"].append(warning)
```

Apply these fields only to reservable lines:

```python
line["reserved_quantity"] = row["reserved_before"]
line["available_after_reservations"] = row["available_before"]
line["reserved_by_others"] = row["reserved_by_others"]
if row["insufficient"]:
    _append_mixed_warning_once(
        line, "Existencia insuficiente; verificar disponibilidad."
    )
```

After validating the complete snapshot but before mutating any line, raise `ValueError(f"tarkett:{identity} sin existencia suficiente")` when a Tarkett row is insufficient; this preserves Tarkett's hard stock limit and triggers whole-checkout compensation. Offiho and generic stocked suppliers retain the warning path above. For distinct configurations aggregated into one supplier reservation, map the same identity snapshot to every matching configuration line without duplicating the database reservation.

Add a test where concurrent reservations reduce Tarkett availability after catalog loading: the endpoint must return failure, call `db_release_mixed_cart`, remove/mark the draft through the existing cleanup helper and never upload or queue. Add the parallel Offiho case with an accent/case/whitespace variant of the same warning already present and assert it uploads one payload containing exactly one normalized canonical insufficient-stock warning.

Add a deterministic route test whose upload seam calls `db_release_mixed_cart(job_id)` immediately after recording the input and before the queue compare-and-set. The endpoint must return 503, never call `_wake_worker`, run idempotent compensation, and leave no `queued` job; this is the API-level proof that a release winning after reserve cannot be overwritten by an unconditional status update.

- [ ] **Step 8: Add red compensation tests for reservation, upload and queue failures**

Parameterize the stage and expected order:

```python
@pytest.mark.parametrize(
    ("stage", "events"),
    (
        ("reserve", ["create_job", "reserve", "release", "delete_job", "delete_input"]),
        ("upload", ["create_job", "reserve", "upload", "release", "delete_job", "delete_input"]),
        ("queue", ["create_job", "reserve", "upload", "queue", "release", "delete_job", "delete_input"]),
    ),
)
def test_mixed_quote_failure_compensates_all_families(monkeypatch, stage, events):
    observed = configure_mixed_failure(monkeypatch, stage)
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert observed == events
```

Also make `db_release_mixed_cart` raise and assert the retained job is `failed` with `cleanup_pending:release_reservations` and its input is not silently treated as usable. Cover `cleanup_pending:delete_job` too: reservations may already be released while the stale input path remains.

- [ ] **Step 9: Wire compensating cleanup and one response**

Use the complete `try/except` block written in Step 6 verbatim; do not wrap only reservation/upload while leaving `db_create_quote_job` outside it. After that block succeeds, call `_wake_worker()` and return only:

Return only:

```python
return {"mensaje": "Cotizacion mixta en cola", "job": updated}
```

Do not return the frozen payload, catalog URLs, rates beyond job metadata, or multiple jobs.

Harden the existing `/cotizaciones/{job_id}/retry` endpoint for both mixed and legacy cleanup failures before it clears `error_message`:

```python
cleanup_error = str(job.get("error_message") or "").strip()
if cleanup_error.startswith("cleanup_pending:"):
    raise HTTPException(
        status_code=409,
        detail="La cotizacion requiere limpieza administrativa antes de reintentar",
    )
```

Add parameterized tests for `cleanup_pending:release_reservations` and `cleanup_pending:delete_job`; retry returns 409 and calls neither `db_update_quote_job` nor `_wake_worker`. Ordinary worker failures remain retryable. This prevents requeueing a job whose reservations or input may already have been removed.

- [ ] **Step 10: Run endpoint and legacy-route regression tests**

```powershell
python -m pytest tests/test_quote_jobs_api.py -k "mixed_quote or catalog_routes or tarkett_quote or offiho_quote or supplier_quote" -q
```

Expected: PASS. The legacy route tests must remain unchanged except for shared fixture setup.

- [ ] **Step 11: Mirror all API copies and commit the endpoint**

```powershell
python -m pytest tests/test_quote_jobs_api.py -k "deployable_api_copies or mixed_quote" -q
git add -- mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py vercel_deploy/api/index.py tests/test_quote_jobs_api.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(api): encolar una cotizacion mixta"
```

---

### Task 5: Write one enriched `Quotation` workbook for every provider

**Files:**

- Modify: `mobiliti_saas/quote_engine/catalog_cart.py:54-157,260-360`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py:54-157,260-360`
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Create: `tests/test_mixed_catalog_workbook.py`
- Modify: `tests/test_mixed_catalog_cart.py`

**Interfaces:**

- Consumes: the validated frozen payload from Task 2 and existing HTTPS/image allowlist helpers.
- Produces in `catalog_cart.py`: `write_catalog_quotation_headers(ws, extra_headers: dict[int, str] | None = None) -> None`; `write_catalog_quotation_item(ws, *, row: int, index: int, item: dict, source_type: str, images_root: Path, text_transform: Callable[[object], str], image_file_key: str | None = None) -> None`; `_download_catalog_image(url: Any, image_dir: Path, code: str, source_type: str, destination_key: str | None = None) -> Path | None`.
- Produces in `mixed_catalog.py`: `create_mixed_catalog_quotation_workbook(payload, output_path, *, image_dir=None) -> Path`.
- Workbook contract: one `Quotation`; A–K unchanged; L `Supplier`, M `Discount Percent`, N `Original Currency`, O `Original Unit Price`, P `Frozen Exchange Rate`, Q `Source Reference`, R `Price Mode`, S `Auto Electrification`.

- [ ] **Step 1: Add a red golden for sections, global numbering and L–S**

Create `tests/test_mixed_catalog_workbook.py` with a three-provider frozen payload and assert:

```python
def test_mixed_workbook_has_one_quotation_with_provider_sections_and_audit_columns(tmp_path):
    output = tmp_path / "mixed.xlsx"
    create_mixed_catalog_quotation_workbook(frozen_payload(), output, image_dir=tmp_path / "images")
    wb = load_workbook(output, data_only=False)
    assert wb.sheetnames == ["Quotation"]
    ws = wb["Quotation"]
    assert [ws.cell(7, column).value for column in range(12, 20)] == [
        "Supplier", "Discount Percent", "Original Currency", "Original Unit Price",
        "Frozen Exchange Rate", "Source Reference", "Price Mode", "Auto Electrification",
    ]
    assert [ws.cell(row, 1).value for row in (8, 10, 12)] == ["- Tarkett", "- Sonara", "- ALMA"]
    assert [ws.cell(row, 1).value for row in (9, 11, 13)] == [1, 2, 3]
    assert [ws.cell(row, 12).value for row in (9, 11, 13)] == ["Tarkett", "Sonara", "ALMA"]
    assert [ws.cell(row, 13).value for row in (9, 11, 13)] == [40, 0, 0]
    assert ws["N13"].value == "USD"
    assert ws["O13"].value == 100
    assert ws["P13"].value == 18.5
    assert ws["R13"].value == "net"
    assert ws["S9"].value is True
    assert ws["S11"].value is False
    assert ws["S13"].value is False
    wb.close()
```

- [ ] **Step 2: Run the golden and observe the missing workbook function**

```powershell
python -m pytest tests/test_mixed_catalog_workbook.py -q
```

Expected: collection ERROR because `create_mixed_catalog_quotation_workbook` is not yet exported.

- [ ] **Step 3: Extract one shared row writer without changing legacy output**

Rename `_write_headers` to `write_catalog_quotation_headers` and accept additive headers:

```python
def write_catalog_quotation_headers(ws, extra_headers: dict[int, str] | None = None) -> None:
    headers = {
        1: "No.", 2: "Item", 3: "Image", 4: "Description", 5: "Dimension",
        7: "Qty", 10: "List Price", 11: "URL",
    }
    headers.update(extra_headers or {})
    for col, title in headers.items():
        cell = ws.cell(7, col)
        cell.value = title
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B2F6B")
```

Extract the existing loop body into the exact signature below. Preserve quantity precision, description/warnings, row height, text transform and `_add_catalog_image`:

```python
def write_catalog_quotation_item(
    ws,
    *,
    row: int,
    index: int,
    item: dict[str, Any],
    source_type: str,
    images_root: Path,
    text_transform: Callable[[object], str],
    image_file_key: str | None = None,
) -> None:
```

Before extracting the loop, make the shared description helper merge derived and catalog warnings semantically. Derived warnings win because they carry current quantity/availability detail; raw warnings still preserve their original order when no equivalent category has appeared:

```python
def _catalog_warning_key(value: object) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    for category in (
        "precio por confirmar", "imagen de referencia", "codigo por verificar",
        "existencia insuficiente", "producto agotado", "agotado",
    ):
        if category in normalized:
            return "agotado" if category in {"producto agotado", "agotado"} else category
    return normalized


def _merge_catalog_warnings(derived: list[str], raw: object) -> list[str]:
    candidates = [*derived]
    if isinstance(raw, list):
        candidates.extend(str(value).strip() for value in raw if str(value).strip())
    result = []
    seen = set()
    for warning in candidates:
        key = _catalog_warning_key(warning)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result
```

Replace the current append-only warning block inside `_description_for_item` with:

```python
derived_warnings = [
    warning
    for warning in (
        "Codigo por verificar" if item.get("code_status") == "needs_review" else "",
        "Imagen de referencia" if item.get("image_kind") == "generated_reference" else "",
        _stock_warning(item, quantity),
        _price_warning(item),
    )
    if warning
]
warnings = _merge_catalog_warnings(derived_warnings, item.get("warnings"))
parts.extend(warnings)
return " | ".join(part for part in parts if part), " | ".join(warnings)
```

Keep the row writer's existing local fallback exactly as `code = str(item.get("code") or item.get("sku") or "").strip()`; legacy supplier rows often have `sku` and no `code`. Extend `_add_catalog_image(ws, row: int, image_url: Any, image_dir: Path, code: str, source_type: str, destination_key: str | None = None) -> None` and have it call `_download_catalog_image(image_url, image_dir, code, source_type, destination_key=destination_key)`. The mixed row writer passes `destination_key=image_file_key`; every legacy caller omits it, so both display semantics and filenames continue to use the existing `code/sku` fallback. Use this compatible downloader signature:

```python
def _download_catalog_image(
    url: Any,
    image_dir: Path,
    code: str,
    source_type: str,
    destination_key: str | None = None,
) -> Path | None:
    clean_url = str(url or "").strip()
    allowed_hosts = _allowed_image_hosts(source_type)
    if not allowed_hosts:
        return None
    try:
        _validate_official_https_url(clean_url, allowed_hosts)
        request = urllib.request.Request(
            clean_url, headers={"User-Agent": "Mobiliti Official Catalog/1.0"}
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _OfficialRedirectHandler(allowed_hosts)
        )
        with opener.open(request, timeout=18) as response:
            _validate_connected_peer(response)
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            content_length = response.headers.get("content-length")
            if not content_type.startswith("image/"):
                return None
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                return None
            data = response.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        suffix = (
            mimetypes.guess_extension(content_type)
            or Path(urlsplit(clean_url).path).suffix
            or ".jpg"
        )
        safe_key = re.sub(
            r"[^A-Za-z0-9_-]+", "_", destination_key or code or "producto"
        )
        destination = image_dir / f"{safe_key}{suffix}"
        destination.write_bytes(data)
        return destination
    except Exception:
        return None
```

`create_catalog_quotation_workbook` omits the new argument and must retain exactly the same row numbers, filenames and A–K values for all three legacy source types. Add a legacy `supplier_cart` golden with only `sku` (no `code`) and assert its image filename/anchor and A–K values remain unchanged.

- [ ] **Step 4: Prove the extraction did not alter legacy adapters**

Run:

```powershell
python -m pytest tests/test_tarkett_catalog.py tests/test_offiho_catalog.py tests/test_supplier_catalog.py -k "workbook or quotation or image" -q
```

Expected: PASS before adding mixed behavior.

- [ ] **Step 5: Implement the mixed workbook loop with one global index**

At the first line of `create_mixed_catalog_quotation_workbook`, call `payload = validate_mixed_catalog_payload(payload)` before creating a workbook or image directory. That shared validator enforces source type, nonempty canonical groups and exact line/result fields; do not duplicate a weaker adapter-only validator. Map image policy per group:

```python
MIXED_GROUP_SOURCE_TYPES = {
    "tarkett": "tarkett_cart",
    "offiho": "offiho_cart",
    "cr-global": "supplier_cart",
    "sonara": "supplier_cart",
    "sunon": "supplier_cart",
    "alma": "supplier_cart",
    "lumbro": "supplier_cart",
}
```

Write rows with this control flow:

```python
row = 8
product_index = 1
for group in payload["groups"]:
    ws.cell(row, 1).value = f"- {MIXED_CATALOG_LABELS[group['catalog']]}"
    ws.cell(row, 1).font = Font(bold=True)
    row += 1
    for item in group["items"]:
        workbook_item = deepcopy(item)
        workbook_item["description"] = " | ".join(
            part for part in (
                str(item.get("description") or "").strip(),
                f"Fuente: {item['source_reference']}",
            )
            if part
        )
        write_catalog_quotation_item(
            ws, row=row, index=product_index, item=workbook_item,
            source_type=MIXED_GROUP_SOURCE_TYPES[group["catalog"]],
            images_root=images_root, text_transform=safe_excel_text,
            image_file_key=(
                f"{group['catalog']}-{row}-"
                f"{hashlib.sha256(item['canonical_key'].encode('utf-8')).hexdigest()[:16]}"
            ),
        )
        ws.cell(row, 12).value = safe_excel_text(item["supplier"])
        ws.cell(row, 13).value = float(Decimal(item["discount_percent"]))
        ws.cell(row, 14).value = item["original_currency"]
        ws.cell(row, 15).value = float(Decimal(item["original_unit_price"]))
        ws.cell(row, 16).value = float(Decimal(item["frozen_exchange_rate"]))
        ws.cell(row, 17).value = safe_excel_text(item["source_reference"])
        ws.cell(row, 18).value = item["price_mode"]
        if not isinstance(item["auto_electrification"], bool):
            raise ValueError("Auto Electrification mixto debe ser booleano")
        ws.cell(row, 19).value = item["auto_electrification"]
        row += 1
        product_index += 1
```

Store M as numeric percent points (`40`, not `0.4`) with number format `0.000000`, never Excel's `%` format, so it displays 40 rather than 4000 %. The parser/engine then divides those points by 100 exactly once. Set widths for L–S without reducing A–K.

- [ ] **Step 6: Add red warning/configuration/formula-injection tests**

Add:

```python
def test_mixed_workbook_preserves_configuration_review_warning_and_safe_text(tmp_path):
    payload = frozen_payload()
    sonara = payload["groups"][1]["items"][0]
    sonara["name"] = "=HYPERLINK(\"bad\")"
    sonara["code_status"] = "needs_review"
    sonara["warnings"] = ["Codigo por verificar"]
    sonara["source_reference"] = "sonara:catalogo-2026:pagina-4"
    alma = payload["groups"][2]["items"][0]
    alma["configuration"] = "Cubierta nogal; electrificacion A+C"
    output = create_mixed_catalog_quotation_workbook(payload, tmp_path / "safe.xlsx")
    wb = load_workbook(output, data_only=False)
    ws = wb["Quotation"]
    assert str(ws["B11"].value).startswith("'")
    assert "Codigo por verificar" in ws["D11"].value
    assert "Fuente: sonara:catalogo-2026:pagina-4" in ws["D11"].value
    assert ws["D11"].fill.fgColor.rgb.endswith("FFF2CC")
    assert "Cubierta nogal; electrificacion A+C" in ws["D13"].value
    wb.close()
```

Add `test_mixed_workbook_preserves_existing_visual_semantics` using normalized rows produced by Task 2 rather than manually appended prose: Offiho has `variant="Negro"`, `price_source="missing"`, `stock_status="insufficient_stock"` and a reservation result below requested quantity; a made-to-order ALMA row has `lead_time="6 semanas"`; and a generic row has `image_kind="generated_reference"`. Assert their `Quotation!D` cells contain, respectively, the variant, `PRECIO POR CONFIRMAR`, `EXISTENCIA INSUFICIENTE`, delivery text and `Imagen de referencia`, each once. Then generate the final workbook in Task 6's golden and assert the same descriptions reach the corresponding `Cotizacion` rows. This test protects the exact structured fields added to `MIXED_LINE_FIELDS` from being dropped by the shared row writer.

Add a legacy regression that calls `_description_for_item` with derived states plus raw accent/case variants (`precio por confirmar`, `IMAGEN DE REFERENCIA`, `Código por verificar`, `existencia insuficiente; verificar disponibilidad`) and one unrelated manual warning. Normalize the returned description with `_catalog_warning_key`'s accent/case rules; assert each of the four semantic categories occurs exactly once, the richer derived stock warning is retained, the unrelated warning survives once, and the original legacy row/warning order is otherwise unchanged.

- [ ] **Step 7: Add an image-policy test for three source families**

Monkeypatch `_download_catalog_image` through the shared row writer, record `(source_type, url, row)`, and return three small PNG fixtures. Assert calls are exactly:

```python
assert calls == [
    ("tarkett_cart", "https://media.tarkett-image.com/tarkett.png", 9),
    ("offiho_cart", "https://www.offiho.com/offiho.png", 11),
    ("supplier_cart", "https://assets.example.test/alma.png", 13),
]
assert sorted(image.anchor._from.row + 1 for image in ws._images) == [9, 11, 13]
```

Do not accept an image URL from a browser request; all three URLs must originate in the frozen server payload fixture.

Add a collision regression with two mixed lines whose codes are both empty (or equal) but whose canonical keys and PNG bytes differ. Exercise the real destination-name logic with the network opener stubbed, open the resulting XLSX as a ZIP, and assert `xl/media` contains two distinct filenames and two expected SHA-256 hashes anchored to the correct rows. This proves Sonara `needs_review` images cannot overwrite one another in the shared `images_root`.

- [ ] **Step 8: Run workbook and image gates**

```powershell
python -m pytest tests/test_mixed_catalog_workbook.py tests/test_mixed_catalog_cart.py -q
python -m pytest tests/test_tarkett_catalog.py tests/test_offiho_catalog.py tests/test_supplier_catalog.py -q
```

- [ ] **Step 9: Mirror both modules and prove hashes**

Extend the hash test to cover `catalog_cart.py` and `mixed_catalog.py`, copy each root file byte-for-byte to its web package, then run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py -k "copies_are_byte_identical" -q
```

- [ ] **Step 10: Commit the single-Quotation adapter**

```powershell
git add -- mobiliti_saas/quote_engine/catalog_cart.py mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py tests/test_mixed_catalog_workbook.py tests/test_mixed_catalog_cart.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(quote): crear Quotation mixta unica"
```

---

### Task 6: Make the final quote engine honor mixed fields per line

**Files:**

- Modify: `mobiliti_saas/quote_engine/parser.py:18-136`
- Modify: `mobiliti_saas/quote_engine/engine.py:60,230-285,1612-1845,1986-2125`
- Create: `tests/test_mixed_quote_engine.py`
- Modify: `tests/test_quote_engine_golden.py`
- Modify: `tests/test_quote_engine_lumbro.py`

**Interfaces:**

- Consumes: `Quotation` headers L–S from Task 5 and metadata `catalog_price_mode="mixed_catalog_converted"`, `quote_currency`, `rate_summary`, `auto_electrification_rate`.
- Produces `QuoteItem` fields: `proveedor`, `descuento`, `moneda_original`, `precio_original`, `tipo_cambio_congelado`, `referencia_fuente`, `modo_precio`, `electrificacion_automatica`.
- Legacy defaults: provider `""`, discount `None`, original fields empty/`None`, price mode `""`, auto electrification `None`; old workbooks retain global metadata behavior.
- Engine result: provider and discount per product, already-converted unit price, quote/quote pair with rate 1, compact FX legend and one totals block.

- [ ] **Step 1: Add red parser tests for headers and backward-compatible defaults**

Create `tests/test_mixed_quote_engine.py`:

```python
def test_parser_reads_mixed_audit_columns_by_header(tmp_path):
    source = write_mixed_quotation_fixture(tmp_path / "mixed.xlsx")
    items, columns = read_items(source)
    product = next(item for item in items if item.tipo == "producto")
    assert columns["proveedor"] == "L"
    assert columns["descuento"] == "M"
    assert columns["electrificacion_automatica"] == "S"
    assert product.proveedor == "ALMA"
    assert product.descuento == 0
    assert product.moneda_original == "USD"
    assert product.precio_original == 100
    assert product.tipo_cambio_congelado == 18.5
    assert product.referencia_fuente == "source:alma:1"
    assert product.modo_precio == "net"
    assert product.electrificacion_automatica is False


def test_parser_keeps_legacy_defaults_when_mixed_headers_are_absent(tmp_path):
    source = write_legacy_quotation_fixture(tmp_path / "legacy.xlsx")
    product = next(item for item in read_items(source)[0] if item.tipo == "producto")
    assert product.proveedor == ""
    assert product.descuento is None
    assert product.moneda_original == ""
    assert product.precio_original is None
    assert product.tipo_cambio_congelado is None
    assert product.modo_precio == ""
    assert product.electrificacion_automatica is None
```

- [ ] **Step 2: Run parser cases and observe missing dataclass fields**

```powershell
python -m pytest tests/test_mixed_quote_engine.py -k "parser" -q
```

Expected: FAIL with `AttributeError` for `QuoteItem.proveedor`.

- [ ] **Step 3: Extend `QuoteItem`, header detection and row construction**

Add fields to the dataclass:

```python
proveedor: Any = ""
descuento: Any = None
moneda_original: Any = ""
precio_original: Any = None
tipo_cambio_congelado: Any = None
referencia_fuente: Any = ""
modo_precio: Any = ""
electrificacion_automatica: Any = None
```

Add exact keyword groups:

```python
"proveedor": ["supplier", "provider", "proveedor"],
"descuento": ["discount percent", "discount", "descuento"],
"moneda_original": ["original currency", "base currency", "moneda original"],
"precio_original": ["original unit price", "base unit price", "precio original"],
"tipo_cambio_congelado": ["frozen exchange rate", "exchange rate", "tipo de cambio"],
"referencia_fuente": ["source reference", "referencia fuente"],
"modo_precio": ["price mode", "modo precio"],
"electrificacion_automatica": ["auto electrification", "electrificacion automatica"],
```

Read each field with `col_index` only when its key exists; otherwise assign the defaults above. Do not change A–K fallback columns.

- [ ] **Step 4: Add red per-line provider, discount and converted-price tests**

Generate a minimal mixed source with Tarkett discount 40 and ALMA discount 0, then call `generate_quote`. Assert:

```python
assert mobiliti.cell(tarkett_mobiliti_row, 6).value == "Tarkett"
assert mobiliti.cell(alma_mobiliti_row, 6).value == "ALMA"
assert cot.cell(tarkett_cot_row, 7).value == 0.4
assert cot.cell(alma_cot_row, 7).value == 0
assert cot.cell(tarkett_cot_row, 6).value == f"=ROUND(Mobiliti!X{tarkett_mobiliti_row},2)"
assert cot.cell(alma_cot_row, 6).value == f"=ROUND(Mobiliti!X{alma_mobiliti_row},2)"
assert mobiliti["J6"].value == "MXN/MXN"
assert mobiliti["K6"].value == 1
```

Parameterize `quote_currency` with MXN/USD/EUR and assert `J6` equals `MXN/MXN`, `USD/USD`, `EUR/EUR` respectively while K6 stays 1.

- [ ] **Step 5: Add explicit mixed-mode helpers while retaining legacy branches**

Add:

```python
def _uses_mixed_catalog_prices(metadata: dict[str, Any] | None) -> bool:
    return str((metadata or {}).get("catalog_price_mode") or "").strip() == "mixed_catalog_converted"


def _uses_converted_catalog_prices(metadata: dict[str, Any] | None) -> bool:
    return _uses_catalog_list_prices(metadata) or _uses_mixed_catalog_prices(metadata)


def _item_discount_rate(item: QuoteItem, metadata: dict[str, Any]) -> float:
    if _uses_mixed_catalog_prices(metadata):
        mode = str(item.modo_precio or "").strip().lower()
        provider = str(item.proveedor or "").strip()
        if mode not in {"list", "net"}:
            raise ValueError("Modo de precio mixto invalido")
        value = _num(item.descuento, -1)
        if not math.isfinite(value) or value < 0 or value > 100:
            raise ValueError("Descuento mixto por linea invalido")
        if mode == "net" and value != 0:
            raise ValueError("Precio neto mixto no admite descuento")
        if mode == "list" and provider not in {"Tarkett", "Offiho"}:
            raise ValueError("Precio de lista mixto solo admite Tarkett u Offiho")
        return value / 100.0
    return _discount_rate(metadata)


def _item_auto_electrification(item: QuoteItem, metadata: dict[str, Any]) -> bool:
    if _uses_mixed_catalog_prices(metadata):
        if not isinstance(item.electrificacion_automatica, bool):
            raise ValueError("Politica de electrificacion mixta invalida")
        if item.electrificacion_automatica and str(item.proveedor or "").strip() not in {"Tarkett", "Offiho"}:
            raise ValueError("Electrificacion automatica mixta solo admite Tarkett u Offiho")
        return item.electrificacion_automatica
    return not _uses_catalog_list_prices(metadata)


def _mixed_auto_electrification_rate(metadata: dict[str, Any]) -> float:
    snapshot = metadata.get("auto_electrification_rate")
    expected = {
        "base_currency", "quote_currency", "exchange_rate", "rate_source",
        "rate_effective_date", "rate_retrieved_at",
    }
    quote_currency = str(metadata.get("quote_currency") or "").strip().upper()
    if not isinstance(snapshot, dict) or set(snapshot) != expected:
        raise ValueError("Tasa de electrificacion mixta incompleta")
    if snapshot.get("base_currency") != "MXN" or snapshot.get("quote_currency") != quote_currency:
        raise ValueError("Par de electrificacion mixta invalido")
    rate = _positive_num(snapshot.get("exchange_rate"))
    if rate is None:
        raise ValueError("Tasa de electrificacion mixta invalida")
    return rate
```

In both `_write_mobiliti` and `_write_cotizacion`, compute `converted_catalog_prices = _uses_converted_catalog_prices(metadata)` and use it for J→X identity formulas, two-place product formulas and the rounded five-row totals chain. Keep `catalog_list_prices = _uses_catalog_list_prices(metadata)` only for the explicit legacy supplier branch shown below. Never let mixed mode fall into the unrounded legacy/default branch.

Add red engine tests for unknown `Price Mode`, `net` with a nonzero discount, generic provider with `list`, and ALMA/Sunon with `Auto Electrification=True`; all must fail before a final workbook is saved. Keep one Tarkett `list/40/True` and one ALMA `net/0/False` green to prove the mode/policy governs line semantics rather than merely being audit columns.

- [ ] **Step 6: Use provider and cover discount from each product**

Inside `_write_mobiliti`, set:

```python
provider = safe_excel_text(item.proveedor) if _uses_mixed_catalog_prices(metadata) else provider_label
ws.cell(row, 6).value = provider
line_discount_rate = _item_discount_rate(item, metadata)
```

Pass `line_discount_rate` into `mark_written_row` and write the cover formula with that line's value:

```python
def mark_written_row(
    row_number: int,
    line_discount_rate: float,
    region: str = DEFAULT_MOBILITI_REGION,
) -> None:
    ws.cell(row_number, MOBILITI_REGION_COL).value = region
    ws.cell(row_number, MOBILITI_COVER_DISCOUNT_COL).value = (
        f"=MIN({_excel_decimal(line_discount_rate)},"
        f"{get_column_letter(MOBILITI_MAX_DISCOUNT_COL)}{row_number})"
    )
    ws.cell(row_number, MOBILITI_DISCOUNT_AMOUNT_COL).value = f"=X{row_number}*AA{row_number}"
    ws.cell(row_number, MOBILITI_FINAL_PRICE_COL).value = (
        f'=IF(AA{row_number}>Z{row_number},"ERROR",(X{row_number}-AB{row_number}))'
    )
    ws.cell(row_number, MOBILITI_COMMERCIAL_TOTAL_COL).value = f"=AC{row_number}*H{row_number}"
    if _uses_converted_catalog_prices(metadata):
        ws.cell(row_number, MOBILITI_UNIT_PRICE_COL).value = f"=ROUND(J{row_number},2)"
        ws.cell(row_number, MOBILITI_MIN_UNIT_PRICE_COL).value = f"=ROUND(J{row_number},2)"
    written_rows.add(row_number)
```

In `_write_cotizacion`, set column G to `_item_discount_rate(item, metadata)` for mixed jobs. Retain the first-row/global-reference formula unchanged for every non-mixed job.

- [ ] **Step 7: Preserve automatic Lumbro accessories per source line**

At `_write_mobiliti` entry, validate the extra frozen rate once for mixed mode:

```python
auto_items = [
    item for item in items
    if item.tipo == "producto" and _item_auto_electrification(item, metadata)
]
mixed_auto_rate = None
if _uses_mixed_catalog_prices(metadata) and auto_items:
    mixed_auto_rate = _mixed_auto_electrification_rate(metadata)
elif _uses_mixed_catalog_prices(metadata) and metadata.get("auto_electrification_rate") is not None:
    raise ValueError("Tasa de electrificacion mixta inesperada")
```

Replace the global suppression with:

```python
accessories = (
    _lumbro_accessories_for_item(item, category)
    if _item_auto_electrification(item, metadata)
    else []
)
```

Change the nested accessory writer so automatic rows inherit the parent line discount and convert the MXN guide price with the outer frozen rate in mixed mode:

```python
def write_lumbro_row(
    row_number: int,
    code: str,
    quantity: int,
    line_discount_rate: float,
    region: str = DEFAULT_MOBILITI_REGION,
) -> None:
    price_ref = lumbro_prices.get(code)
    ws.cell(row_number, 4).value = code
    ws.cell(row_number, 5).value = LUMBRO_CATEGORY
    ws.cell(row_number, 6).value = LUMBRO_PROVIDER
    ws.cell(row_number, 8).value = quantity
    if price_ref and mixed_auto_rate is not None:
        ws.cell(row_number, 10).value = (
            f"=ROUND('SPEC-GUIDE-LUMBRO'!E{price_ref.row}*"
            f"{_excel_decimal(mixed_auto_rate)},2)"
        )
    elif price_ref:
        ws.cell(row_number, 10).value = f"='SPEC-GUIDE-LUMBRO'!E{price_ref.row}/$K$6"
    else:
        ws.cell(row_number, 10).value = "=0" if mixed_auto_rate is not None else "=0/$K$6"
    ws.cell(row_number, 11).value = 0
    mark_written_row(row_number, line_discount_rate, region)
```

Call `mark_written_row(row, line_discount_rate)` for the parent and `write_lumbro_row(accessory_row, code, quantity, line_discount_rate)` for each automatic row. In `_write_cotizacion`, keep accessories in the mixed parent price while applying the parent discount exactly once:

```python
if _uses_mixed_catalog_prices(metadata):
    if lumbro_rows:
        price_terms = [
            f"Mobiliti!X{mob_row}*Mobiliti!H{mob_row}",
            *(f"Mobiliti!X{row}*Mobiliti!H{row}" for row in lumbro_rows),
        ]
        total_formula = "+".join(price_terms)
        ws.cell(current_row, 6).value = (
            f"=ROUND(IFERROR(({total_formula})/Mobiliti!H{mob_row},0),2)"
        )
    else:
        ws.cell(current_row, 6).value = f"=ROUND(Mobiliti!X{mob_row},2)"
elif catalog_list_prices:
    ws.cell(current_row, 6).value = f"=ROUND(Mobiliti!X{mob_row},2)"
elif lumbro_rows:
    price_terms = [
        f"Mobiliti!X{mob_row}*Mobiliti!H{mob_row}",
        *(f"Mobiliti!Y{row}" for row in lumbro_rows),
    ]
    total_formula = "+".join(price_terms)
    ws.cell(current_row, 6).value = f"=IFERROR(({total_formula})/Mobiliti!H{mob_row},0)"
else:
    ws.cell(current_row, 6).value = f"=Mobiliti!X{mob_row}"
```

Add a test with one Tarkett/Offiho workstation line (`Auto Electrification=True`), one ALMA workstation-named line (`False`) and one manually selected Lumbro product (`False`). Assert only the eligible legacy-family line creates its expected automatic `LIDO.OP-INT`, `JUMP-1.5M`, `CAJA-FUS` rows; ALMA and manual Lumbro remain separate products and neither suppresses nor duplicates those rows. Parameterize `MXN/USD/EUR` with frozen rates `1.000000/0.054054/0.048780`; assert each accessory J formula multiplies `SPEC-GUIDE-LUMBRO` by that exact rate, every accessory AA formula uses its parent's line discount for the Mobiliti audit, and the parent `Cotizacion!F` formula includes each accessory's raw `Mobiliti!X*Mobiliti!H` term once. Assert `Cotizacion!G` contains the parent rate and no accessory price is discounted before entering that formula. Add an ALMA-only mixed job with `auto_electrification_rate=None` and assert it succeeds without any accessory rows.

- [ ] **Step 8: Write quote/quote settings and a safe compact rate legend**

In `_write_mobiliti_settings`:

```python
if _uses_mixed_catalog_prices(metadata):
    quote_currency = str(metadata.get("quote_currency") or "").strip().upper()
    if quote_currency not in {"MXN", "USD", "EUR"}:
        raise ValueError("Moneda mixta incompleta")
    exchange_pair = f"{quote_currency}/{quote_currency}"
    exchange_rate = 1
elif _uses_catalog_list_prices(metadata):
    exchange_rate = _positive_num(metadata.get("exchange_rate"))
    if exchange_rate is None:
        raise ValueError("Tipo de cambio congelado invalido")
    base_currency = str(metadata.get("base_currency") or "").strip().upper()
    quote_currency = str(metadata.get("quote_currency") or "").strip().upper()
    if not base_currency or not quote_currency:
        raise ValueError("Moneda de catalogo incompleta")
    exchange_pair = f"{base_currency}/{quote_currency}"
else:
    exchange_rate = _exchange_rate(metadata)
    exchange_pair = "USD/MXN"
```

Use the final currency in visible number formats instead of the template's fixed dollar sign:

```python
MIXED_MONEY_FORMATS = {
    "MXN": '"MXN" $#,##0.00;[Red]-"MXN" $#,##0.00;"-"',
    "USD": '"USD" $#,##0.00;[Red]-"USD" $#,##0.00;"-"',
    "EUR": '"EUR" €#,##0.00;[Red]-"EUR" €#,##0.00;"-"',
}


def _money_format(metadata: dict[str, Any]) -> str:
    if not _uses_mixed_catalog_prices(metadata):
        return MONEY_FORMAT
    quote_currency = str(metadata.get("quote_currency") or "").strip().upper()
    try:
        return MIXED_MONEY_FORMATS[quote_currency]
    except KeyError as exc:
        raise ValueError("Moneda mixta incompleta") from exc
```

For mixed mode, apply this format to Cotizacion product columns F/H/I/J and the five totals' amount column, plus Mobiliti written product/accessory and subtotal/total money columns J, M:N, Q:Y, AB:AD and AF:AG. This explicitly includes `M` (`Costo Unitario Real`), `R` (`Flete` ajustado) and `T` (`Instalación` ajustada). Keep percentage/quantity cells unchanged and preserve `MONEY_FORMAT` byte-for-byte for non-mixed jobs. Parameterize MXN/USD/EUR in the golden and assert visible product and total cells contain the corresponding literal/symbol, including explicit assertions for Mobiliti columns M/R/T; specifically no mixed EUR money cell may retain the dollar-only `MONEY_FORMAT`.

In `_write_header`, protect every user/job header in both catalog modes, not just the rate legend:

```python
converted_catalog_prices = _uses_converted_catalog_prices(metadata)
text = safe_excel_text if converted_catalog_prices else (lambda value: value)
ws["B3"] = text(metadata.get("cotizacion", ""))
# B7:B12 use the same text function for proyecto, cliente, correo,
# telefono, direccion and razon_social.
```

For mixed mode, format `rate_summary` in canonical provider order using only validated catalog/currency/rate/source/date fields, cap the complete B4 value at 1000 characters, then pass it through `safe_excel_text`. Example expected value:

```text
MXN | precios mixtos mas IVA | Tarkett MXN/MXN 1.000000; ALMA USD/MXN 18.500000 Banco de Mexico / DOF 2026-07-15
```

Add a mixed-header injection test that parameterizes `=1+1`, `+SUM(A1:A2)`, `-2+3` and `@cmd` across B3 and B7:B12; every saved cell must be text prefixed safely and none may have `data_type == "f"`. Keep the legacy non-catalog header golden unchanged.

- [ ] **Step 9: Add the final workbook golden and totals assertions**

Extend `tests/test_quote_engine_golden.py` with a mixed source produced by Task 5. Assert:

```python
assert {"Cotizacion", "Mobiliti", "Quotation"} <= set(wb.sheetnames)
assert wb.sheetnames.count("Cotizacion") == 1
assert wb.sheetnames.count("Mobiliti") == 1
assert wb.sheetnames.count("Quotation") == 1
labels = [
    cot.cell(row, 4).value for row in range(1, cot.max_row + 1)
    if cot.cell(row, 4).value in {"SUBTOTAL:", "COSTO DE FLETE:", "IVA:", "TOTAL:"}
]
assert labels == ["SUBTOTAL:", "COSTO DE FLETE:", "SUBTOTAL:", "IVA:", "TOTAL:"]
assert "16%" in str(cot.cell(iva_row, 8).value)
assert all(_formula_uses_round_2(cot.cell(row, 8).value) for row in total_rows)
```

Do not depend on nonexistent cached Excel values or an external formula engine. Add this deterministic test-local reference and pair it with exact formula assertions:

```python
MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def reference_totals(rows: list[tuple[Decimal, Decimal, Decimal]]):
    # rows are (already-converted unit price, quantity, discount rate)
    subtotal = Decimal("0")
    for price, quantity, discount in rows:
        unit = money(price)
        discount_amount = money(unit * discount)
        net_unit = money(unit - discount_amount)
        subtotal += money(quantity * net_unit)
    subtotal = money(subtotal)
    freight = money(subtotal * Decimal("0.12"))
    before_tax = money(subtotal + freight)
    tax = money(before_tax * Decimal("0.16"))
    return subtotal, freight, before_tax, tax, money(before_tax + tax)
```

Use fixture constants for one 40 % Tarkett row and one 0 % ALMA row and assert the returned tuple numerically. Choose product names that do not generate automatic accessories in this totals fixture, retain the `(cot_row, mobiliti_row)` mapping, and assert the exact product chain:

```python
for cot_row, mobiliti_row in product_row_map:
    assert cot.cell(cot_row, 6).value == f"=ROUND(Mobiliti!X{mobiliti_row},2)"
    assert cot.cell(cot_row, 8).value == f"=ROUND(F{cot_row}*G{cot_row},2)"
    assert cot.cell(cot_row, 9).value == f"=ROUND(F{cot_row}-H{cot_row},2)"
    assert cot.cell(cot_row, 10).value == f"=ROUND(E{cot_row}*I{cot_row},2)"

assert cot.cell(subtotal_row, 8).value == f"=ROUND(SUM(J{first_product}:J{last_product}),2)"
assert cot.cell(freight_row, 8).value == f"=ROUND(H{subtotal_row}*12%,2)"
assert cot.cell(before_tax_row, 8).value == f"=ROUND(H{subtotal_row}+H{freight_row},2)"
assert cot.cell(iva_row, 8).value == f"=ROUND(H{before_tax_row}*16%,2)"
assert cot.cell(total_row, 8).value == f"=ROUND(H{before_tax_row}+H{iva_row},2)"
```

The independent Decimal tuple plus exact formula chain detects a second discount/conversion without pretending openpyxl evaluates formulas.

- [ ] **Step 10: Run parser, engine, Lumbro and legacy goldens**

```powershell
python -m pytest tests/test_mixed_quote_engine.py tests/test_quote_engine_golden.py tests/test_quote_engine_lumbro.py -q
```

Expected: PASS. Specifically verify `test_legacy_workbook_keeps_existing_provider_header_and_formulas` and the existing single-supplier frozen-currency golden remain green.

- [ ] **Step 11: Commit the per-line engine behavior**

```powershell
git add -- mobiliti_saas/quote_engine/parser.py mobiliti_saas/quote_engine/engine.py tests/test_mixed_quote_engine.py tests/test_quote_engine_golden.py tests/test_quote_engine_lumbro.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(engine): aplicar proveedor y descuento por linea"
```

---

### Task 7: Dispatch `mixed_catalog_cart` through the worker once

**Files:**

- Modify: `mobiliti_saas/worker/quote_worker.py:40-48,607-690`
- Modify: `tests/test_quote_worker.py:450-720`

**Interfaces:**

- Consumes: frozen `mixed_catalog_cart` JSON, `validate_mixed_catalog_payload`, `create_mixed_catalog_quotation_workbook`, existing `_prepare_generator_input` conversion map and `_run_generator`.
- Produces: `MIXED_CATALOG_CART_SOURCE_TYPE`; `_convert_mixed_catalog_cart_to_quotation(source_json, output_xlsx, payload) -> None`; `quotation_from_mixed_catalog.xlsx`; metadata flag `mixed_catalog_converted=True`.
- Generator metadata: `catalog_price_mode="mixed_catalog_converted"`, `base_currency=quote_currency`, `quote_currency=quote_currency`, `exchange_rate="1.000000"`, `descuento=0`, frozen `rate_summary` and frozen `auto_electrification_rate`.

- [ ] **Step 1: Add a red worker conversion test**

Add to `tests/test_quote_worker.py`:

```python
def _valid_mixed_worker_payload():
    rate = {
        "catalog": "tarkett", "base_currency": "MXN", "quote_currency": "EUR",
        "exchange_rate": "0.048780", "rate_source": "saas_exchange_rates",
        "rate_effective_date": "2026-07-19", "rate_retrieved_at": "2026-07-19T20:00:00Z",
    }
    line = {
        "canonical_key": "tarkett:T-1", "catalog": "tarkett", "supplier": "Tarkett",
        "code": "T-1", "name": "Piso Tarkett", "description": "Piso de prueba", "unit": "M2",
        "quantity": "1.000000", "unit_price": "4.88", "discount_percent": "40.000000",
        "original_currency": "MXN", "original_unit_price": "100.000000",
        "frozen_exchange_rate": "0.048780", "source_reference": "tarkett:test:T-1",
        "price_mode": "list", "auto_electrification": True, "tax_rate": "0.160000",
        "image_url": "", "product_url": "", "warnings": [], "code_status": "verified",
        "configuration": "", "attributes": {}, "variant": "",
        "availability_type": "stocked", "available_quantity": "10.000000",
        "stock": "10.000000", "lead_time": "", "price_source": "catalog",
        "stock_status": "available", "image_kind": "placeholder",
        "reservation": {"identity": "T-1", "sku": "T-1", "quantity": "1.000000", "stock": "10.000000"},
    }
    return {
        "source_type": "mixed_catalog_cart", "quote_currency": "EUR",
        "created_at": "2026-07-19T20:00:00+00:00", "item_count": 1,
        "groups": [{
            "catalog": "tarkett", "catalog_source_hash": "a" * 64,
            "base_currency": "MXN", "quote_currency": "EUR", "exchange_rate": "0.048780",
            "rate_source": "saas_exchange_rates", "rate_effective_date": "2026-07-19",
            "rate_retrieved_at": "2026-07-19T20:00:00Z", "items": [line],
        }],
        "rate_summary": [rate],
        "auto_electrification_rate": {
            "base_currency": "MXN", "quote_currency": "EUR", "exchange_rate": "0.048780",
            "rate_source": "saas_exchange_rates", "rate_effective_date": "2026-07-19",
            "rate_retrieved_at": "2026-07-19T20:00:00Z",
        },
    }


def test_process_job_converts_mixed_cart_once_and_sets_identity_exchange(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    payload = _valid_mixed_worker_payload()
    client.input_content = json.dumps(payload).encode("utf-8")
    seen = {"converter_calls": 0, "generator_calls": 0}

    def fake_convert(source_json, output_xlsx, cart_payload):
        seen["converter_calls"] += 1
        seen["output_name"] = output_xlsx.name
        assert cart_payload is payload or cart_payload == payload
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_calls"] += 1
        seen["generator_input"] = input_path.name
        seen["metadata"] = dict(job["metadata"])
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_convert_mixed_catalog_cart_to_quotation", fake_convert, raising=False)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)
    quote_worker.process_job(client, {
        "id": "job-1", "usuario_id": 7,
        "input_path": "users/7/jobs/job-1/input.json",
        "metadata": {"source_type": "mixed_catalog_cart", "input_extension": ".json"},
    })
    assert seen["converter_calls"] == 1
    assert seen["generator_calls"] == 1
    assert seen["output_name"] == "quotation_from_mixed_catalog.xlsx"
    assert seen["generator_input"] == "quotation_from_mixed_catalog.xlsx"
    assert seen["metadata"]["mixed_catalog_converted"] is True
    assert seen["metadata"]["catalog_price_mode"] == "mixed_catalog_converted"
    assert seen["metadata"]["base_currency"] == "EUR"
    assert seen["metadata"]["quote_currency"] == "EUR"
    assert seen["metadata"]["exchange_rate"] == "1.000000"
    assert seen["metadata"]["descuento"] == 0
    assert seen["metadata"]["auto_electrification_rate"] == payload["auto_electrification_rate"]
```

- [ ] **Step 2: Run the worker test and observe unsupported source type**

```powershell
python -m pytest tests/test_quote_worker.py -k "mixed_cart_once" -q
```

Expected: FAIL with `Tipo de fuente JSON no soportado` or missing converter.

- [ ] **Step 3: Register the source type and converter**

Add:

```python
MIXED_CATALOG_CART_SOURCE_TYPE = "mixed_catalog_cart"
JSON_CART_SOURCE_TYPES = frozenset({
    TARKETT_CART_SOURCE_TYPE,
    OFFIHO_CART_SOURCE_TYPE,
    SUPPLIER_CART_SOURCE_TYPE,
    MIXED_CATALOG_CART_SOURCE_TYPE,
})


def _convert_mixed_catalog_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.mixed_catalog import create_mixed_catalog_quotation_workbook
    create_mixed_catalog_quotation_workbook(payload, output_xlsx)
```

Register in `_prepare_generator_input`:

```python
MIXED_CATALOG_CART_SOURCE_TYPE: (
    "quotation_from_mixed_catalog.xlsx",
    _convert_mixed_catalog_cart_to_quotation,
    "mixed_catalog_converted",
),
```

Immediately after the payload/metadata source-type equality check and before selecting or invoking any converter, validate the mixed root contract:

```python
if source_type == MIXED_CATALOG_CART_SOURCE_TYPE:
    from mobiliti_saas.quote_engine.mixed_catalog import validate_mixed_catalog_payload
    try:
        payload = validate_mixed_catalog_payload(payload)
    except ValueError as exc:
        raise RuntimeError(f"Payload de cotizacion mixta invalido: {exc}") from exc
```

The real converter calls the same validator defensively, but this worker call must happen first so malformed groups/rates never reach image download or workbook creation.

- [ ] **Step 4: Freeze mixed metadata after conversion**

Add a dedicated branch after the single-supplier branch:

```python
if source_type == MIXED_CATALOG_CART_SOURCE_TYPE:
    quote_currency = payload["quote_currency"]
    metadata.update({
        "catalog_price_mode": "mixed_catalog_converted",
        "base_currency": quote_currency,
        "quote_currency": quote_currency,
        "exchange_rate": "1.000000",
        "rate_summary": deepcopy(payload["rate_summary"]),
        "auto_electrification_rate": deepcopy(payload["auto_electrification_rate"]),
        "descuento": 0,
    })
```

Do not fetch rates or reload catalogs in the worker; retry must reuse the exact frozen JSON. Mutate the source payload's summary/rate objects after metadata construction in a unit test and assert metadata remains unchanged, proving there is no alias.

Add `from copy import deepcopy` at the top of the worker for metadata isolation.

- [ ] **Step 5: Add mismatch and malformed-payload cases**

Clone `_valid_mixed_worker_payload()` and mutate one field per case:

```python
@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda payload: payload.update(groups=[]), "Grupos mixtos invalidos"),
        (lambda payload: payload.update(item_count=2), "Conteo mixto inconsistente"),
        (lambda payload: payload.update(rate_summary=[]), "Resumen de tasas mixtas inconsistente"),
        (lambda payload: payload.update(auto_electrification_rate=None), "Tasa de electrificacion mixta invalida"),
    ),
)
def test_mixed_payload_is_validated_before_converter(monkeypatch, tmp_path, mutation, message):
    payload = _valid_mixed_worker_payload()
    mutation(payload)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    called = []
    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        lambda *args: called.append("convert"),
    )
    with pytest.raises(RuntimeError, match=message):
        quote_worker._prepare_generator_input(
            {"metadata": {"source_type": "mixed_catalog_cart", "input_extension": ".json"}},
            source,
            tmp_path,
        )
    assert called == []
```

Add separate pre-converter mutation cases for: canonical base currency (keep `rate_summary` synchronized so the base-currency invariant is what fails), line original currency, line/group frozen rate, converted unit-price arithmetic, supplier label spoofing, generic `price_mode="list"`, generic `auto_electrification=True`, invalid reservation stock and oversized/deep attributes. Include an `auto_electrification_rate.exchange_rate` mutation from the valid `0.048780` to a different positive six-place value while leaving the eligible group snapshot unchanged; it must fail with `Tasa de electrificacion mixta invalida` before the converter. Every case must leave converter and `_run_generator` call lists empty.

Extend `test_process_job_rejects_invalid_json_cart_before_generator` without changing its existing payload/metadata `source_type` mismatch row; its existing `called == []` assertion must still prove `_run_generator` remains uncalled.

- [ ] **Step 6: Run worker and legacy source-type regressions**

```powershell
python -m pytest tests/test_quote_worker.py -q
```

Expected: PASS for mixed, Tarkett, Offiho, supplier and PDF/XLSX paths.

- [ ] **Step 7: Commit the worker dispatch**

```powershell
git add -- mobiliti_saas/worker/quote_worker.py tests/test_quote_worker.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(worker): procesar cotizacion mixta una vez"
```

---

### Task 8: Create a dependency-free global cart model for React

**Files:**

- Create: `mobiliti_saas/web/src/mixedCart.js`
- Create: `tests/test_mixed_catalog_cart_ui.py`

**Interfaces:**

- Consumes: catalog identity, quantity rules and a display-only snapshot produced by the three catalog views.
- Produces JavaScript exports: `mixedCartKey(catalog, identity)`, `createMixedCartLine(input)`, `validateLineQuantity(line, quantity)`, `lineNeedsAvailabilityConfirmation(line)`, `lineNeedsPriceConfirmation(line)`, `upsertMixedCartLine(lines, incoming)`, `updateMixedCartQuantity(lines, key, quantity)`, `removeMixedCartLine(lines, key)`, `toMixedQuoteItem(line)`.
- Line shape: `{key, catalog, identity, quantity, quantityRules, snapshot}`. `quantityRules` contains `min`, `step`, `maxDecimals`, optional `integer`, required commercial `max`, and optional UI-only `warningAt`, `confirmOnInsufficient` and `confirmOnMissingPrice`; `snapshot` contains exactly `name,code,image_url,unit,availability,configuration,warnings` and is never serialized by `toMixedQuoteItem`. `configuration` is a display-only stable string built from authoritative option names, not browser-supplied commercial data.
- Canonical keys: `tarkett:<code>`, `offiho:<inventory_key>`, `<supplier>:<JSON.stringify([internal_id,base_option_id,sorted_add_ons])>`. The JSON tuple avoids delimiter collisions and must match Python's compact `json.dumps` byte-for-byte for these strings. Quantities use decimal strings with at most six places globally and the stricter precision of each source: Tarkett 6, Offiho 3, supplier M2 6 and supplier PZA 0.

- [ ] **Step 1: Add red Node-driven tests for keys and exact decimal accumulation**

Create `tests/test_mixed_catalog_cart_ui.py` with a helper that invokes `node --input-type=module`, then add:

```python
def test_mixed_cart_keys_are_stable_and_configuration_sensitive():
    result = run_mixed_cart_js("""
      const keys = [
        mixedCartKey("tarkett", {code: "25731726"}),
        mixedCartKey("offiho", {inventory_key: "OHE-405 NEGRO ALUFSEN"}),
        mixedCartKey("alma", {
          internal_id: "alma:desk-1", base_option_id: "base-a",
          add_on_option_ids: ["addon-b", "addon-a"]
        })
      ];
      console.log(JSON.stringify(keys));
    """)
    assert result == [
        "tarkett:25731726",
        "offiho:OHE-405 NEGRO ALUFSEN",
        'alma:["alma:desk-1","base-a",["addon-a","addon-b"]]',
    ]


def test_upsert_accumulates_without_float_drift_and_preserves_other_catalogs():
    result = run_mixed_cart_js("""
      const tarkett = createMixedCartLine({
        catalog: "tarkett", identity: {code: "T-1"}, quantity: "0.1",
        quantityRules: {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "5"},
        snapshot: {name: "Tarkett", code: "T-1", image_url: "", unit: "M2", availability: "", configuration: "", warnings: []}
      });
      const sonara = createMixedCartLine({
        catalog: "sonara", identity: {internal_id: "sonara:panel", base_option_id: "", add_on_option_ids: []},
        quantity: "1", quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Panel", code: "Codigo por verificar", image_url: "", unit: "PZA", availability: "", configuration: "", warnings: ["Codigo por verificar"]}
      });
      let lines = upsertMixedCartLine([], tarkett);
      lines = upsertMixedCartLine(lines, sonara);
      lines = upsertMixedCartLine(lines, {...tarkett, quantity: "0.2"});
      console.log(JSON.stringify(lines.map(line => [line.key, line.quantity])));
    """)
    assert result == [["tarkett:T-1", "0.3"], ['sonara:["sonara:panel","",[]]', "1"]]


def test_quantity_precision_is_enforced_per_catalog_without_float_rounding():
    result = run_mixed_cart_js("""
      const offiho = createMixedCartLine({
        catalog: "offiho", identity: {inventory_key: "OFF-1"}, quantity: "1.001",
        quantityRules: {min: "0.001", step: "0.001", maxDecimals: 3, max: "1000000"},
        snapshot: {name: "Offiho", code: "OFF-1", image_url: "", unit: "PZA", availability: "", configuration: "Negro", warnings: []}
      });
      let message = "";
      try { updateMixedCartQuantity([offiho], offiho.key, "1.0001"); }
      catch (error) { message = error.message; }
      console.log(JSON.stringify({allowed: offiho.quantity, rejected: message}));
    """)
    assert result == {"allowed": "1.001", "rejected": "Cantidad excede 3 decimales"}


def test_visual_supplier_configurations_are_distinct_and_display_named():
    result = run_mixed_cart_js("""
      const makeLine = (addOnId, configuration) => createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "base-a", add_on_option_ids: [addOnId]},
        quantity: "1",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Escritorio ALMA", code: "AL-1", image_url: "", unit: "PZA", availability: "Sobre pedido", configuration, warnings: []}
      });
      const lines = [
        makeLine("addon-a", "Base A + Electrificacion A"),
        makeLine("addon-b", "Base A + Pasacables B")
      ];
      console.log(JSON.stringify(lines.map(line => ({
        key: line.key, name: line.snapshot.name,
        configuration: line.snapshot.configuration, serialized: toMixedQuoteItem(line)
      }))));
    """)
    assert result[0]["key"] != result[1]["key"]
    assert [row["name"] for row in result] == ["Escritorio ALMA", "Escritorio ALMA"]
    assert [row["configuration"] for row in result] == [
        "Base A + Electrificacion A", "Base A + Pasacables B",
    ]
    assert all("configuration" not in row["serialized"] for row in result)
```

- [ ] **Step 2: Run the pure UI tests and observe the missing module**

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "keys or upsert" -q
```

Expected: FAIL because `mobiliti_saas/web/src/mixedCart.js` does not exist.

- [ ] **Step 3: Implement strict identity and quantity helpers with `BigInt`**

Start the module with:

```javascript
export const MIXED_CATALOGS = Object.freeze([
  "tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro"
]);

const SUPPLIER_CATALOGS = new Set(MIXED_CATALOGS.slice(2));
const QUANTITY_PATTERN = /^(?:0|[1-9]\d{0,6})(?:\.(\d{1,6}))?$/;

function normalizedText(value, field) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} requerido`);
  return value.trim();
}

function quantityMicrounits(value) {
  const text = String(value).trim();
  const match = QUANTITY_PATTERN.exec(text);
  if (!match) throw new Error("Cantidad invalida");
  const [integer, fraction = ""] = text.split(".");
  const result = BigInt(integer) * 1000000n + BigInt((fraction + "000000").slice(0, 6));
  if (result <= 0n) throw new Error("Cantidad invalida");
  return result;
}

function quantityFromMicrounits(value) {
  const integer = value / 1000000n;
  const fraction = String(value % 1000000n).padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${integer}.${fraction}` : String(integer);
}
```

Implement `mixedCartKey` with explicit catalog branches and sorted copied add-ons; reject unknown catalogs, empty identities and duplicate add-on IDs. The supplier branch returns `` `${catalog}:${JSON.stringify([internalId, baseOptionId, sortedAddOnIds])}` `` so arbitrary `|`, `:` or Unicode inside an ID cannot collide.

- [ ] **Step 4: Validate and copy the display-only line shape**

`createMixedCartLine` must copy, not retain, caller arrays/objects:

```javascript
export function createMixedCartLine({catalog, identity, quantity, quantityRules, snapshot}) {
  const key = mixedCartKey(catalog, identity);
  const normalizedQuantity = quantityFromMicrounits(quantityMicrounits(quantity));
  if (!quantityRules || typeof quantityRules !== "object") throw new Error("Reglas de cantidad requeridas");
  if (quantityRules.max == null || String(quantityRules.max).trim() === "") {
    throw new Error("Maximo comercial requerido");
  }
  if (!snapshot || typeof snapshot !== "object") throw new Error("Snapshot visual requerido");
  const visualSnapshot = {
    name: String(snapshot.name || ""),
    code: String(snapshot.code || ""),
    image_url: String(snapshot.image_url || ""),
    unit: String(snapshot.unit || ""),
    availability: String(snapshot.availability || ""),
    configuration: String(snapshot.configuration || "").slice(0, 2000),
    warnings: [...(snapshot.warnings || [])].map(value => String(value))
  };
  const line = {
    key,
    catalog,
    identity: {
      ...identity,
      add_on_option_ids: [...(identity.add_on_option_ids || [])].sort()
    },
    quantity: normalizedQuantity,
    quantityRules: {...quantityRules},
    snapshot: visualSnapshot
  };
  return {...line, quantity: validateLineQuantity(line, normalizedQuantity)};
}
```

The model is held in React state; do not call browser storage APIs in this module.

- [ ] **Step 5: Implement immutable upsert/update/remove with per-line limits**

```javascript
export function validateLineQuantity(line, quantity) {
  const text = String(quantity).trim();
  const units = quantityMicrounits(quantity);
  const maxDecimals = Number(line.quantityRules.maxDecimals);
  if (!Number.isInteger(maxDecimals) || maxDecimals < 0 || maxDecimals > 6) {
    throw new Error("Precision de cantidad invalida");
  }
  const decimals = (text.split(".")[1] || "").length;
  if (decimals > maxDecimals) {
    throw new Error(`Cantidad excede ${maxDecimals} decimales`);
  }
  if (line.quantityRules.integer && units % 1000000n !== 0n) {
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
  const index = lines.findIndex(line => line.key === incoming.key);
  if (index < 0) return [...lines, {...incoming, quantity: validateLineQuantity(incoming, incoming.quantity)}];
  const combined = quantityMicrounits(lines[index].quantity) + quantityMicrounits(incoming.quantity);
  const refreshed = {
    ...lines[index],
    identity: incoming.identity,
    quantityRules: incoming.quantityRules,
    snapshot: incoming.snapshot
  };
  const quantity = validateLineQuantity(refreshed, quantityFromMicrounits(combined));
  return lines.map((line, position) => position === index ? {...refreshed, quantity} : line);
}

export function updateMixedCartQuantity(lines, key, quantity) {
  if (!lines.some(line => line.key === key)) throw new Error("Linea de carrito no encontrada");
  return lines.map(line => line.key === key ? {...line, quantity: validateLineQuantity(line, quantity)} : line);
}

export function removeMixedCartLine(lines, key) {
  return lines.filter(line => line.key !== key);
}
```

Offiho uses `{min:"0.001", step:"0.001", maxDecimals:3, max:"1000000", warningAt:<min(available,1000000)>, confirmOnInsufficient:true, confirmOnMissingPrice:<price_source === "missing">}`: overstock relative to current availability remains quotable up to the existing commercial cap and triggers confirmation. Supplier PZA/M2 also cap at `1000000`; stocked lines use the capped stock as `warningAt`, and every `is_out_of_stock` line uses `warningAt:"0"` plus `confirmOnInsufficient:true` even if its availability type is unknown. Only made-to-order/unknown lines that are not marked out of stock omit that confirmation. Tarkett uses `{min:"0.000001", step:"0.000001", maxDecimals:6, max:<min(available,1000000)>}` and remains hard-blocked.

Add red cases rejecting a missing/zero commercial `max`, Offiho `1000000.001` and supplier PZA `1000001`, while allowing Offiho quantity above `warningAt` and asserting `lineNeedsAvailabilityConfirmation(line) is true`. Assert an Offiho line with `confirmOnMissingPrice:true` returns true from `lineNeedsPriceConfirmation`, while warning text alone does not. Add a refresh-upsert test where the same key arrives with lower `max`, new confirmation flags, warnings and image: validate against incoming rules and replace the visual snapshot rather than retaining stale catalog data.

- [ ] **Step 6: Add red serialization-whitelist tests**

```python
def test_mixed_quote_serializer_sends_only_identity_configuration_and_quantity():
    result = run_mixed_cart_js("""
      const line = createMixedCartLine({
        catalog: "alma",
        identity: {internal_id: "alma:desk", base_option_id: "base-a", add_on_option_ids: ["b", "a"]},
        quantity: "2", quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {
          name: "Desk", code: "AL-1", image_url: "https://evil.test/x.png", unit: "PZA",
          availability: "5", configuration: "Base A + Electrificacion A",
          warnings: ["visual"], unit_price: "1", base_currency: "XXX",
          exchange_rate: "999", stock: "999", product_url: "https://evil.test"
        }
      });
      console.log(JSON.stringify({
        item: toMixedQuoteItem(line),
        snapshotKeys: Object.keys(line.snapshot).sort()
      }));
    """)
    assert result == {
        "item": {
            "catalog": "alma", "internal_id": "alma:desk", "quantity": "2",
            "base_option_id": "base-a", "add_on_option_ids": ["a", "b"],
        },
        "snapshotKeys": ["availability", "code", "configuration", "image_url", "name", "unit", "warnings"],
    }
```

- [ ] **Step 7: Implement the three exact serializer branches**

```javascript
export function toMixedQuoteItem(line) {
  if (line.catalog === "tarkett") {
    return {catalog: "tarkett", code: line.identity.code, quantity: line.quantity};
  }
  if (line.catalog === "offiho") {
    return {catalog: "offiho", inventory_key: line.identity.inventory_key, quantity: line.quantity};
  }
  if (!SUPPLIER_CATALOGS.has(line.catalog)) throw new Error("Catalogo mixto no soportado");
  const result = {
    catalog: line.catalog,
    internal_id: line.identity.internal_id,
    quantity: line.quantity,
    add_on_option_ids: [...(line.identity.add_on_option_ids || [])].sort()
  };
  if (line.identity.base_option_id) result.base_option_id = line.identity.base_option_id;
  return result;
}
```

- [ ] **Step 8: Run all pure model tests and a source scan for persistence**

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py -q
rg -n "localStorage|sessionStorage|unit_price|exchange_rate|product_url" mobiliti_saas/web/src/mixedCart.js
```

Expected: tests PASS; storage scan returns no matches; commercial names appear only inside the negative test, not the serializer module.

- [ ] **Step 9: Commit the isolated global-cart model**

```powershell
git add -- mobiliti_saas/web/src/mixedCart.js tests/test_mixed_catalog_cart_ui.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(web): modelar carrito global mixto"
```

---

### Task 9: Lift all seven catalogs into one drawer and one submit flow

**Files:**

- Create: `mobiliti_saas/web/src/MixedCartDrawer.jsx`
- Modify: `mobiliti_saas/web/src/main.jsx:32-35,463-485,771-1390,1868-2010`
- Modify: `mobiliti_saas/web/src/SupplierCatalogView.jsx:189-396,548-594,875-990`
- Modify: `mobiliti_saas/web/src/styles.css:901-1600,1603-2250`
- Modify: `tests/test_mixed_catalog_cart_ui.py`
- Modify: `tests/test_supplier_catalog_ui.py`
- Modify: `tests/test_web_ui_defaults.py`

**Interfaces:**

- Consumes: all Task 8 helpers and `request("/catalogs/mixed-quote", options)` from `useApi`.
- Produces `MixedCartDrawer({lines, open, form, busy, error, notice, onClose, onFieldChange, onQuantityChange, onRemove, onSubmit})`, where submit calls `onSubmit(event, committedLines)` after validating local quantity drafts.
- App callbacks: `onAddCartLine(line)`, `onUpdateCartLine(key, quantity)`, `onRemoveCartLine(key)`; header props `cartCount`, `onOpenCart`; no catalog view owns a quote form or cart array. While a checkout request is in flight, every add/edit/remove control is disabled and the callbacks also reject mutation using the synchronous submission ref.
- Session semantics: navigation preserves; refresh/close does not; failure preserves; success/logout/auth expiry clears.

- [ ] **Step 1: Add red static tests for one owner, one drawer and one endpoint**

Before editing, verify the adopted Lumbro/image UI baseline exactly; a mismatch means inspect and preserve the new hunks before continuing:

```powershell
$adoptedUi = @('mobiliti_saas/web/src/SupplierCatalogView.jsx','mobiliti_saas/web/src/styles.css','tests/test_supplier_catalog_ui.py')
$baselineUiHash = git diff --binary -- $adoptedUi | git hash-object --stdin
if ($baselineUiHash -ne '738b3ae0f20aae1289e7b1b9fb96e0c4e4928d0b') { throw "La base UI aprobada cambio: $baselineUiHash" }
```

Extend `tests/test_mixed_catalog_cart_ui.py`:

```python
def test_app_owns_one_mixed_cart_and_one_submit_endpoint():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    supplier = Path("mobiliti_saas/web/src/SupplierCatalogView.jsx").read_text(encoding="utf-8")
    drawer = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx")
    assert drawer.is_file()
    assert main.count("useState([])") >= 1
    assert "const [mixedCart, setMixedCart] = useState([])" in main
    assert main.count('request("/catalogs/mixed-quote"') == 1
    assert 'request("/tarkett/quote"' not in main
    assert 'request("/offiho/quote"' not in main
    assert "/catalogs/${supplier}/quote" not in supplier
    assert main.count("<MixedCartDrawer") == 1
    assert "const [cart, setCart]" not in supplier


def test_all_catalog_views_receive_the_same_add_callback():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert re.search(r"<TarkettView[\s\S]*?onAddCartLine=\{addMixedCartLine\}", main)
    assert re.search(r"<OffihoView[\s\S]*?onAddCartLine=\{addMixedCartLine\}", main)
    assert re.search(r"<SupplierCatalogView[\s\S]*?onAddCartLine=\{addMixedCartLine\}", main)
```

- [ ] **Step 2: Run the static tests and observe the missing drawer/local carts**

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_supplier_catalog_ui.py tests/test_web_ui_defaults.py -q
```

Expected: FAIL because three local carts/forms and three old submit routes remain.

- [ ] **Step 3: Add App-level state and the canonical empty form**

Import the new module/component and add:

```javascript
const EMPTY_MIXED_QUOTE = Object.freeze({
  proyecto: "", cliente: "", correo: "", telefono: "", direccion: "",
  razon_social: "", quote_currency: "MXN", descuento: "40",
  template: "Formato Cotizacion 2026 GDL (1).xlsx"
});
```

Inside `App`:

```javascript
const [mixedCart, setMixedCart] = useState([]);
const mixedCartRef = useRef([]);
const [mixedCartOpen, setMixedCartOpen] = useState(false);
const [mixedQuote, setMixedQuote] = useState({...EMPTY_MIXED_QUOTE});
const [mixedQuoteBusy, setMixedQuoteBusy] = useState(false);
const [mixedQuoteError, setMixedQuoteError] = useState("");
const [mixedQuoteNotice, setMixedQuoteNotice] = useState("");
const mixedQuoteSubmittingRef = useRef(false);
const mixedQuoteSessionEpochRef = useRef(0);

function replaceMixedCart(next) {
  mixedCartRef.current = next;
  setMixedCart(next);
}

function addMixedCartLine(line) {
  if (mixedQuoteSubmittingRef.current) {
    setMixedQuoteError("Espera a que termine la cotizacion en curso");
    return false;
  }
  try {
    const next = upsertMixedCartLine(mixedCartRef.current, line);
    replaceMixedCart(next);
    setMixedQuoteError("");
    setMixedQuoteNotice("");
    setMixedCartOpen(true);
    return true;
  } catch (error) {
    setMixedQuoteError(error.message || "No se pudo agregar el producto");
    return false;
  }
}

function updateMixedCartLine(key, quantity) {
  if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
  const next = updateMixedCartQuantity(mixedCartRef.current, key, quantity);
  replaceMixedCart(next);
}

function removeMixedCartLineFromApp(key) {
  if (mixedQuoteSubmittingRef.current) throw new Error("Cotizacion en curso");
  replaceMixedCart(removeMixedCartLine(mixedCartRef.current, key));
}
```

Do not initialize state from browser storage and do not add an effect that writes it.

- [ ] **Step 4: Convert Tarkett cards into global line producers**

Change the signature to `TarkettView({token, userId, cartLines, onAddCartLine, onOpenCart, cartBusy})`; retain catalog cache, filters and stock. Replace the cart-backed card input with local `quantityDraftsByCode` state, initialized from the matching global line quantity or `min(1, available)`. Draft edits never mutate the global cart; clicking Add validates and submits the draft. Disable its quantity/Add controls when `cartBusy`. On add, create:

```javascript
onAddCartLine(createMixedCartLine({
  catalog: "tarkett",
  identity: {code: item.code},
  quantity: String(quantity),
  quantityRules: {
    min: "0.000001", step: "0.000001", maxDecimals: 6,
    max: String(Math.min(Number(item.available_quantity), 1000000))
  },
  snapshot: {
    name: item.name, code: item.code, image_url: item.image_url || "", unit: item.unit,
    availability: String(item.available_quantity), configuration: "", warnings: []
  }
}));
```

Remove only Tarkett's local cart panel, local quote form and `createTarkettQuote`; keep its product/catalog behavior. Add a component test proving an empty/invalid draft shows its quantity error and leaves the global callback uncalled.

- [ ] **Step 5: Convert Offiho cards while preserving overstock semantics**

Change the signature to `OffihoView({token, userId, cartLines, onAddCartLine, onOpenCart, cartBusy})`. Preserve a local `quantityDraftsByInventoryKey` map so transient values `""` and `"1."` remain editable without entering the global cart. Disable its quantity/Add controls when `cartBusy`. On Add, pass the existing `normalizeOffihoQuantity` result into a line with `identity.inventory_key` and rules `{min:"0.001", step:"0.001", maxDecimals:3, max:"1000000", warningAt:String(Math.min(offihoStockLimit(item), 1000000)), confirmOnInsufficient:true, confirmOnMissingPrice:hasMissingPrice(item)}`. Put agotado/insuficiente/precio-por-confirmar text in `snapshot.warnings`; the explicit boolean controls the UI confirmation and the text remains presentation only. Remove the local cart/form and `createOffihoQuote`, but retain fractional parsing, source cache and product filters.

```javascript
function addOffihoItem(item) {
  const draft = quantityDraftsByInventoryKey[item.inventory_key] ?? "1";
  const normalized = normalizeOffihoQuantity(draft);
  if (normalized.error) {
    setQuantityError(normalized.error);
    return;
  }
  const available = Math.min(offihoStockLimit(item), 1000000);
  const warning = offihoStockWarning(item, normalized.quantity);
  const warnings = [
    ...(warning ? [warning] : []),
    ...(hasMissingPrice(item) ? ["Precio por confirmar"] : []),
  ];
  const added = onAddCartLine(createMixedCartLine({
    catalog: "offiho",
    identity: {inventory_key: item.inventory_key},
    quantity: normalized.rawQuantity,
    quantityRules: {
      min: "0.001", step: "0.001", maxDecimals: 3, max: "1000000",
      warningAt: String(available), confirmOnInsufficient: true,
      confirmOnMissingPrice: hasMissingPrice(item),
    },
    snapshot: {
      name: item.name, code: item.code || item.inventory_key,
      image_url: item.image_url || "", unit: item.unit,
      availability: String(item.available_quantity),
      configuration: String(item.variant || ""), warnings,
    },
  }));
  if (added) setQuantityError("");
}
```

- [ ] **Step 6: Convert the five supplier catalogs and allow Sonara review items**

Change `SupplierCatalogView` props to:

```javascript
export default function SupplierCatalogView({
  supplier, label, request, userId, onAddCartLine, onOpenCart, cartLineCount, cartBusy
}) {
```

Update the add gate:

```javascript
const reviewAllowed = ["lumbro", "sonara"].includes(supplier)
  && item.code_status === "needs_review"
  && item.base_currency === "MXN"
  && decimal(item.tax_rate) === 0.16;
const codeAllowed = item.code_status === "verified" || reviewAllowed;
return codeAllowed && item.base_currency !== "XXX" && decimal(configuredPrice) > 0;
```

Extend the existing supplier rule helper without changing its unit detection:

```javascript
function quantityRules(item) {
  const rules = isSquareMeterUnit(item?.unit)
    ? {min: "0.000001", step: "0.000001", maxDecimals: 6, max: "1000000", integer: false}
    : {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true};
  if (item?.is_out_of_stock) {
    return {...rules, warningAt: "0", confirmOnInsufficient: true};
  }
  if (item?.availability_type === "stocked") {
    const stock = Number(item.stock ?? 0);
    const warningAt = Number.isFinite(stock)
      ? String(Math.min(Math.max(stock, 0), 1000000))
      : "0";
    return {...rules, warningAt, confirmOnInsufficient: true};
  }
  return rules;
}
```

Every `item.is_out_of_stock` line uses `warningAt:"0"` even when its availability type is `unknown`; only made-to-order/unknown lines not marked out of stock omit the threshold. Keep `quantityByItem` as the supplier card's local draft state.

Create global supplier lines with copied configuration and quantity rules. Add a small stable deduplicator so the canonical review warning already emitted by the Sonara/Lumbro snapshot is not appended a second time (compare case-insensitively and without accents):

```javascript
function warningKey(value) {
  return String(value || "")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .trim().toLowerCase();
}

function cartWarnings(item) {
  const result = [...(item.warnings || [])].map(value => String(value));
  if (
    item.code_status === "needs_review"
    && !result.some(value => warningKey(value) === "codigo por verificar")
  ) {
    result.push("Codigo por verificar");
  }
  const seen = new Set();
  return result.filter(value => {
    const key = warningKey(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function visibleConfiguration(item, configuration) {
  const selectedBase = (item.base_price_options || []).find(
    option => option.id === (configuration.base_option_id || "")
  );
  const selectedAddOns = new Set(configuration.add_on_option_ids || []);
  const addOnNames = (item.add_on_options || [])
    .filter(option => selectedAddOns.has(option.id))
    .map(option => String(option.name || "").trim())
    .filter(Boolean);
  return [String(selectedBase?.name || "").trim(), ...addOnNames]
    .filter(Boolean)
    .join(" + ");
}
```

```javascript
onAddCartLine(createMixedCartLine({
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
    code: sourceCode(item) || "Codigo por verificar",
    image_url: item.image_url || "",
    unit: item.unit,
    availability: availabilityLabel(item),
    configuration: visibleConfiguration(item, configuration),
    warnings: cartWarnings(item)
  }
}));
```

Add a fixture whose Sonara item already contains `Codigo por verificar` and assert the card/cart/drawer model contains exactly one normalized occurrence. Keep Task 10's exact dialog locator as the browser proof.

Disable supplier quantity/configuration/Add controls when `cartBusy`. Remove supplier `cart`, rates, quote form, drawer and `submitQuote`; retain filters, variant selection, configurator, quantities and cards.

- [ ] **Step 7: Add red drawer behavior tests before its implementation**

Static assertions must require: `role="dialog"`, `aria-modal`, overlay, Escape handler, Tab focus loop, remove buttons, catalog labels, `Codigo por verificar`, quote currency options MXN/USD/EUR, discount explanation, all six required customer fields, local `quantityDrafts` and `validateLineQuantity` before `onSubmit(event, committedLines)`.

Add a Node/source test asserting the drawer calls `onQuantityChange`/`onRemove` and never calls API itself; only `App` may own the request. Add browser-level assertions in Task 10 that an Offiho drawer quantity can pass through `"" -> "1." -> "1.25"`, an invalid draft focuses its input and makes zero POSTs, and blur commits a valid normalized quantity to the App-owned cart.

```python
def test_mixed_drawer_is_accessible_presentational_and_commits_callbacks():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    for marker in (
        'role="dialog"', 'aria-modal="true"', "mixed-cart-overlay", 'event.key === "Escape"',
        'event.key === "Tab"', "onQuantityChange(line.key", "onRemove(line.key)",
        "['MXN', 'USD', 'EUR']", "value={currency}", "Codigo por verificar",
        "quantityDrafts", "validateLineQuantity", "onSubmit(event, committedLines)",
        "line.snapshot.name", "line.snapshot.configuration",
    ):
        assert marker in source
    assert "name={field}" in source
    for field in ("proyecto", "cliente", "correo", "telefono", "direccion", "razon_social"):
        assert f'"{field}"' in source
    assert "request(" not in source
    assert "fetch(" not in source


def test_app_is_the_only_mixed_quote_request_owner():
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    drawer = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert main.count('request("/catalogs/mixed-quote"') == 1
    assert "/catalogs/mixed-quote" not in drawer
```

- [ ] **Step 8: Implement the presentational global drawer**

`MixedCartDrawer.jsx` must render every line in insertion order and use only callbacks. Maintain `quantityDrafts` and `quantityErrors` keyed by `line.key`; initialize a new key from `line.quantity`, remove vanished keys, and refresh a draft from a changed committed quantity only when the user has not diverged from the previous committed value. Inputs accept transient `""` and `"1."`. On blur, call `validateLineQuantity`; on success call `onQuantityChange(key, normalized)` and clear that error, while an invalid blur leaves the draft intact and shows the error.

Render the authoritative visual identity before each quantity input so configurations with the same product name remain distinguishable:

```jsx
<div className="mixed-cart-line-copy">
  <strong>{line.snapshot.name || line.snapshot.code}</strong>
  {line.snapshot.configuration ? (
    <span className="mixed-cart-line-configuration">{line.snapshot.configuration}</span>
  ) : null}
  <small>{line.snapshot.code} · {line.catalog}</small>
</div>
```

The form uses a local handler, not `onSubmit` directly:

```jsx
function handleDrawerSubmit(event) {
  event.preventDefault();
  const errors = {};
  const committedLines = lines.map((line) => {
    try {
      const quantity = validateLineQuantity(line, quantityDrafts[line.key] ?? line.quantity);
      return {...line, quantity};
    } catch (error) {
      errors[line.key] = error.message || "Cantidad invalida";
      return null;
    }
  });
  setQuantityErrors(errors);
  if (Object.keys(errors).length) {
    quantityInputRefs.current[Object.keys(errors)[0]]?.focus();
    return;
  }
  onSubmit(event, committedLines);
}
```

Then render the customer form:

```jsx
<form className="mixed-quote-form" onSubmit={handleDrawerSubmit}>
  {[
    ["Proyecto *", "proyecto", "text"],
    ["Cliente *", "cliente", "text"],
    ["Correo *", "correo", "email"],
    ["Telefono *", "telefono", "tel"],
    ["Direccion *", "direccion", "text"],
    ["Razon social *", "razon_social", "text"],
  ].map(([label, field, type]) => (
    <label key={field}>
      <span>{label}</span>
      <input name={field} type={type} required value={form[field]}
        onChange={event => onFieldChange(field, event.target.value)} />
    </label>
  ))}
  <label>
    <span>Moneda de cotizacion</span>
    <select value={form.quote_currency} onChange={event => onFieldChange("quote_currency", event.target.value)}>
      {['MXN', 'USD', 'EUR'].map(currency => (
        <option key={currency} value={currency}>{currency}</option>
      ))}
    </select>
  </label>
  <label>
    <span>Descuento Tarkett y Offiho (%)</span>
    <input type="number" min="0" max="100" step="0.01" value={form.descuento}
      onChange={event => onFieldChange("descuento", event.target.value)} />
    <small>CR Global, Sonara, Sunon, ALMA y Lumbro conservan precio neto sin descuento adicional.</small>
  </label>
  <button className="primary-action" disabled={busy || !lines.length} type="submit">
    {busy ? "Cotizando..." : "Cotizar todos los catalogos"}
  </button>
</form>
```

Port the existing focus capture, focus restore, Escape and Tab loop from the supplier drawer. Use a fixed overlay on narrow layouts; do not duplicate that logic in each view.

- [ ] **Step 9: Implement the one App submit with success/failure semantics**

```javascript
async function submitMixedQuote(event, submissionLines = mixedCartRef.current) {
  event.preventDefault();
  if (mixedQuoteSubmittingRef.current || !submissionLines.length) return;

  let committedLines;
  try {
    committedLines = submissionLines.map((line) => ({
      ...line,
      quantity: validateLineQuantity(line, line.quantity)
    }));
  } catch (error) {
    setMixedQuoteError(error.message || "Cantidad invalida");
    return;
  }
  replaceMixedCart(committedLines);

  const availabilityWarnings = committedLines.filter(lineNeedsAvailabilityConfirmation);
  const priceWarnings = committedLines.filter(lineNeedsPriceConfirmation);
  if ((availabilityWarnings.length || priceWarnings.length) && !window.confirm(
    `Hay ${availabilityWarnings.length} producto(s) agotado(s) o con existencia insuficiente ` +
    `y ${priceWarnings.length} producto(s) con precio por confirmar. ¿Deseas continuar?`
  )) return;

  mixedQuoteSubmittingRef.current = true;
  const submissionEpoch = mixedQuoteSessionEpochRef.current;
  setMixedQuoteBusy(true);
  setMixedQuoteError("");
  setMixedQuoteNotice("");
  try {
    const data = await request("/catalogs/mixed-quote", {
      method: "POST",
      body: JSON.stringify({
        ...mixedQuote,
        items: committedLines.map(toMixedQuoteItem)
      })
    });
    if (submissionEpoch !== mixedQuoteSessionEpochRef.current) return;
    if (!data?.job?.id) throw new Error("Respuesta de trabajo mixto invalida");
    setJobs(current => [data.job, ...current.filter(job => job.id !== data.job.id)]);
    replaceMixedCart([]);
    setMixedCartOpen(false);
    setMixedQuoteNotice("Cotizacion mixta en cola. Revisa el avance en Cotizaciones.");
    try {
      await refreshJobs();
    } catch {
      if (submissionEpoch === mixedQuoteSessionEpochRef.current) {
        setMixedQuoteNotice("Cotizacion mixta en cola. Actualiza Cotizaciones para ver el avance.");
      }
    }
  } catch (error) {
    if (submissionEpoch !== mixedQuoteSessionEpochRef.current) return;
    setMixedQuoteError(error.message || "No se pudo generar la cotizacion mixta");
  } finally {
    if (submissionEpoch === mixedQuoteSessionEpochRef.current) {
      mixedQuoteSubmittingRef.current = false;
      setMixedQuoteBusy(false);
    }
  }
}
```

The outer `catch` handles a failed mixed POST or a malformed 200 response and must not clear lines or form. Committing drawer drafts before confirmation means canceling or failing retains the quantities the user just edited. Once a valid job response arrives, clear the cart before the best-effort refresh so a refresh failure cannot invite a duplicate quote. The ref, not React's asynchronous busy state, is both the duplicate-request guard and the mutation lock; pass `cartBusy={mixedQuoteBusy}` to all three catalog view families and `busy` to the drawer, disabling line inputs/remove buttons as well as submit. The epoch prevents a response from an expired/logged-out session from clearing a newer cart or inserting its job/notice. Do not compute an authoritative converted total in the browser; display only line count, catalog labels and warnings.

Add tests for: confirmation canceled means zero POSTs and retained lines; current edited quantity recomputes the availability warning; missing Offiho price requests confirmation through `confirmOnMissingPrice`; two submit events while the first request is pending produce exactly one POST; HTTP 200 without `job.id` shows the invalid-response error and preserves cart/form. With a deferred POST, assert catalog Add plus drawer edit/remove controls are disabled and direct callback attempts cannot mutate `mixedCartRef`. Then call logout before resolving the POST and assert the late response adds no job/notice and does not touch the reset/new-session cart.

- [ ] **Step 10: Clear cart on both authentication exits**

Add to `handleAuthExpired` and `logout`:

```javascript
mixedQuoteSessionEpochRef.current += 1;
mixedQuoteSubmittingRef.current = false;
setMixedQuoteBusy(false);
replaceMixedCart([]);
setMixedCartOpen(false);
setMixedQuote({...EMPTY_MIXED_QUOTE});
setMixedQuoteError("");
setMixedQuoteNotice("");
```

Navigation via `setView` never clears it. Keep `key={view}` if useful for resetting supplier filters/configurators; App-owned lines survive the remount.

- [ ] **Step 11: Add one global header counter and responsive layout**

Change `Header({user, subscription, cartCount, onOpenCart})` and render a button with `Carrito (N)`. Mount one `MixedCartDrawer` next to `mainView`. Change `.tarkett-layout` and `.supplier-catalog-layout` to one content column because no local cart occupies column two.

Because success closes the drawer, render the notice at App level immediately below `Header`, not only inside `MixedCartDrawer`:

```jsx
{mixedQuoteNotice ? (
  <div className="mixed-quote-notice" role="status" aria-live="polite">
    {mixedQuoteNotice}
  </div>
) : null}
```

Add CSS:

```css
.mixed-cart-drawer {
  position: fixed;
  top: 0;
  right: 0;
  width: min(32rem, 100vw);
  height: 100dvh;
  overflow-y: auto;
  z-index: 60;
  transform: translateX(100%);
  transition: transform 180ms ease;
}
.mixed-cart-drawer.open { transform: translateX(0); }
.mixed-cart-overlay { position: fixed; inset: 0; z-index: 50; background: rgb(15 23 42 / 45%); }
@media (max-width: 720px) {
  .mixed-cart-drawer { width: 100vw; }
  .mixed-cart-line { grid-template-columns: minmax(0, 1fr) auto; }
}
```

All long names use `overflow-wrap:anywhere`; `body` and `.content-shell` must not gain horizontal scrolling.

- [ ] **Step 12: Update old UI contracts to the new ownership model**

In `tests/test_supplier_catalog_ui.py`, replace `SUPPLIER_VIEW_PROPS` with the eight props above, move drawer/focus assertions to `MixedCartDrawer.jsx`, remove local rate/form assertions, and extend the review test expected vector to:

```python
assert result == [True, True, False, True, False, False, False]
```

The entries represent valid Sonara review, valid Lumbro review, CR Global review, verified Sonara, zero-price Sonara, XXX Sonara and 8 %-IVA Sonara.

In `tests/test_web_ui_defaults.py`, preserve catalog/cache/filter assertions but replace old POST checks with one mixed endpoint and one global counter.

- [ ] **Step 13: Run UI contracts and Vite build**

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_supplier_catalog_ui.py tests/test_web_ui_defaults.py -q
Push-Location mobiliti_saas\web
npm.cmd run build
Pop-Location
```

Expected: all tests PASS and Vite exits 0 without adding packages or lockfile dependencies.

- [ ] **Step 14: Commit the unified React experience**

The baseline check in Step 1 makes every listed hunk part of this task, so stage the complete explicit paths non-interactively:

```powershell
git add -- mobiliti_saas/web/src/mixedCart.js mobiliti_saas/web/src/MixedCartDrawer.jsx mobiliti_saas/web/src/main.jsx mobiliti_saas/web/src/SupplierCatalogView.jsx mobiliti_saas/web/src/styles.css tests/test_mixed_catalog_cart_ui.py tests/test_supplier_catalog_ui.py tests/test_web_ui_defaults.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat(web): compartir un carrito entre catalogos"
```

---

### Task 10: Prove the mixed cart in desktop and mobile browsers

**Files:**

- Create: `tests/test_mixed_catalog_browser_e2e.py`
- Modify: `mobiliti_saas/web/src/MixedCartDrawer.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`

**Interfaces:**

- Consumes: built Vite app, browser session fixture, jobs/registry stubs and the four catalog HTTP contracts exercised by the browser test; static and server tests cover all seven allowed catalogs.
- Produces: a Playwright acceptance covering Tarkett → Offiho → Sonara → ALMA navigation, one mixed request, Offiho quantity drafts/confirmation, error retention, success clearing, duplicate-submit protection, focus behavior and no horizontal overflow.
- Network isolation: all API calls are fulfilled by local route stubs; no SharePoint, supplier website, Vercel or Supabase request is made.

- [ ] **Step 1: Add a red four-catalog navigation test with captured POST body**

Create a test using the existing Vite/Chrome launch pattern from `test_supplier_catalog_ui.py`. Stub `/cotizaciones`, `/tarkett/catalog`, `/offiho/catalog`, `/catalogs/sonara`, `/catalogs/alma` and `/catalogs/mixed-quote`. The core interaction is:

Use these deterministic fixture bodies in the same test; every commercial field is a server response, never request input:

```python
TARKETT_CATALOG = {
    "source_hash": "tarkett-e2e", "generated_at": "2026-07-19T20:00:00Z", "total": 1,
    "items": [{
        "code": "25731726", "name": "Piso Tarkett", "unit": "M2", "unit_price": "650.00",
        "price_source": "catalog", "available_quantity": "10", "reserved_quantity": "0",
        "reserved_by_others": False, "image_url": "", "product_url": "",
    }],
}

OFFIHO_CATALOG = {
    "source_hash": "offiho-e2e", "generated_at": "2026-07-19T20:00:00Z", "total": 1,
    "items": [{
        "inventory_key": "OFF-1", "code": "OFF-1", "name": "Silla Offiho",
        "variant": "Negro", "unit": "PZA", "pieces_per_box": "1",
        "available_quantity": "1", "reserved_quantity": "0", "reserved_by_others": False,
        "is_out_of_stock": False, "unit_price": "0", "price_source": "missing",
        "product_url": "", "image_url": "", "description": "Silla operativa",
        "description_source": "inventory_label", "match_status": "unmatched",
        "source_updated_at": "2026-07-19T20:00:00Z",
    }],
}

SONARA_CATALOG = {
    "supplier": "sonara", "source_hash": "sonara-e2e", "generated_at": "2026-07-19T20:00:00Z",
    "total": 1, "items": [{
        "internal_id": "sonara:review-panel", "supplier": "sonara", "product_key": "panel",
        "sku": "", "code_status": "needs_review", "brand": "Sonara", "collection": "Paneles",
        "name": "Panel Sonara", "description": "Panel liso", "unit": "PZA",
        "availability_type": "unknown", "stock": None, "reserved_quantity": "0",
        "reserved_by_others": False, "is_out_of_stock": False, "lead_time": "Por confirmar",
        "base_price_options": [], "add_on_options": [], "base_currency": "MXN",
        "price_net": "77.00", "tax_rate": "0.160000", "attributes": {},
        "image_url": "", "image_kind": "placeholder", "product_url": "",
        "warnings": ["Codigo por verificar"], "source_reference": "sonara:e2e:1",
    }],
}

ALMA_CATALOG = {
    "supplier": "alma", "source_hash": "alma-e2e", "generated_at": "2026-07-19T20:00:00Z",
    "total": 1, "items": [{
        "internal_id": "alma:desk", "supplier": "alma", "product_key": "desk",
        "sku": "AL-1", "code_status": "verified", "brand": "ALMA", "collection": "Workstations",
        "name": "Escritorio ALMA", "description": "Escritorio configurable", "unit": "PZA",
        "availability_type": "made_to_order", "stock": None, "reserved_quantity": "0",
        "reserved_by_others": False, "is_out_of_stock": False, "lead_time": "6 semanas",
        "base_price_options": [{"id": "base-a", "name": "Base A", "price_net": "100.00", "available": True}],
        "add_on_options": [{
            "id": "addon-a", "name": "Electrificacion A", "family": "electrificacion",
            "price_net": "25.00", "available": True,
        }],
        "base_currency": "USD", "price_net": "0", "tax_rate": "0.160000", "attributes": {},
        "image_url": "", "image_kind": "placeholder", "product_url": "", "warnings": [],
        "source_reference": "alma:e2e:1",
    }],
}

CATALOG_REGISTRY = {
    "suppliers": [
        {"supplier": "sonara", "label": "Sonara", "enabled": True},
        {"supplier": "alma", "label": "ALMA", "enabled": True},
    ]
}
```

The route dispatcher must return `{"cotizaciones": []}` for `/cotizaciones`, the objects above for their exact paths, and `CATALOG_REGISTRY` for `/catalogs`. Any unhandled `127.0.0.1:8000` path returns HTTP 500 with `{"detail":"stub faltante"}` and is appended to `unexpected_requests`; assert that list is empty at the end.

```python
page.get_by_role("button", name=re.compile(r"^Tarkett")).click()
page.get_by_role("button", name="Agregar", exact=True).first.click()
assert page.get_by_role("button", name=re.compile(r"Carrito \(1\)")).is_visible()

page.get_by_role("button", name=re.compile(r"^Offiho")).click()
page.get_by_role("button", name="Agregar", exact=True).first.click()
assert page.get_by_role("button", name=re.compile(r"Carrito \(2\)")).is_visible()

page.get_by_role("button", name=re.compile(r"^Sonara")).click()
page.get_by_role("button", name="Agregar", exact=True).first.click()
assert page.get_by_role("button", name=re.compile(r"Carrito \(3\)")).is_visible()

page.get_by_role("button", name=re.compile(r"^ALMA")).click()
page.get_by_role("button", name="Base A", exact=True).click()
page.get_by_role("button", name="Electrificacion A", exact=True).click()
page.get_by_role("button", name="Agregar", exact=True).first.click()
assert page.get_by_role("button", name=re.compile(r"Carrito \(4\)")).is_visible()

page.get_by_role("button", name=re.compile(r"Carrito \(4\)")).click()
dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
assert dialog.get_by_text("Codigo por verificar", exact=True).is_visible()
assert dialog.get_by_text("Escritorio ALMA", exact=True).is_visible()
assert dialog.get_by_text("Base A + Electrificacion A", exact=True).is_visible()
```

Fill the six fields and keep MXN and 40 %. Exercise the Offiho draft without losing intermediate text; an empty submit must stay local and focus that quantity input:

```python
for label, value in (
    ("Proyecto *", "Proyecto E2E"),
    ("Cliente *", "Cliente E2E"),
    ("Correo *", "cliente@example.test"),
    ("Telefono *", "3330000000"),
    ("Direccion *", "Guadalajara"),
    ("Razon social *", "Cliente E2E SA de CV"),
):
    dialog.get_by_label(label, exact=True).fill(value)

offiho_quantity = dialog.get_by_label("Cantidad para Silla Offiho", exact=True)
offiho_quantity.fill("")
dialog.get_by_role("button", name="Cotizar todos los catalogos", exact=True).click()
assert mixed_post_bodies == []
assert offiho_quantity.evaluate("element => document.activeElement === element")
offiho_quantity.fill("1.")
assert offiho_quantity.input_value() == "1."
offiho_quantity.fill("1.25")
offiho_quantity.press("Tab")
assert offiho_quantity.input_value() == "1.25"

confirmation_messages = []
def accept_confirmation(prompt):
    confirmation_messages.append(prompt.message)
    prompt.accept()

page.once("dialog", accept_confirmation)
dialog.get_by_role("button", name="Cotizar todos los catalogos", exact=True).click()
assert confirmation_messages == [
    "Hay 1 producto(s) agotado(s) o con existencia insuficiente y 1 producto(s) con precio por confirmar. ¿Deseas continuar?"
]
```

- [ ] **Step 2: Make the first checkout fail and assert all state is retained**

The route handler returns HTTP 422 on its first mixed POST:

```python
route.fulfill(
    status=422,
    content_type="application/json",
    body=json.dumps({"detail": "sonara:sonara:review-panel requiere revision"}),
)
```

Assert the error text, four rendered cart lines, `Carrito (4)`, the committed Offiho quantity and the previously filled project/customer values remain. The captured body must equal:

```python
assert request_body["items"] == [
    {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
    {"catalog": "offiho", "inventory_key": "OFF-1", "quantity": "1.25"},
    {"catalog": "sonara", "internal_id": "sonara:review-panel", "quantity": "1", "add_on_option_ids": []},
    {"catalog": "alma", "internal_id": "alma:desk", "quantity": "1", "base_option_id": "base-a", "add_on_option_ids": ["addon-a"]},
]
for forbidden in ("unit_price", "base_currency", "exchange_rate", "stock", "image_url", "product_url", "warnings"):
    assert all(forbidden not in item for item in request_body["items"])
```

- [ ] **Step 3: Make the retry succeed and assert one job plus an empty cart**

The second POST returns:

```json
{"mensaje":"Cotizacion mixta en cola","job":{"id":"job-mixed-1","status":"queued","metadata":{"source_type":"mixed_catalog_cart"}}}
```

Register a second dialog accept, click submit again and assert `Carrito (0)`, the drawer closes, the success notice is visible, and the page made exactly two POST attempts to the same endpoint—not one request per catalog.

Add a companion fresh-page test containing only one Tarkett line. Fill the required fields, dispatch two cancelable `submit` events synchronously on `form.mixed-quote-form`, let the stub return one valid job, and assert the route captured exactly one mixed POST. This proves `mixedQuoteSubmittingRef`, not a delayed React render, prevents duplicate jobs.

- [ ] **Step 4: Run the test red against the pre-E2E UI**

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py -q
```

Expected: the test fails at the first missing/global-cart interaction or at retained-state behavior.

- [ ] **Step 5: Add desktop console and overflow assertions**

Register `page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)` and `page.on("pageerror", lambda error: page_errors.append(str(error)))`; after success:

```python
assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
assert page_errors == []
assert [message for message in console_errors if "favicon" not in message.lower()] == []
```

Use a 1440×1000 context and verify the drawer does not cover the entire product grid until opened.

- [ ] **Step 6: Repeat the drawer checks at a mobile viewport**

Use a 390×844 page, add one Sonara line, open the drawer and assert:

```python
assert page.get_by_role("dialog", name="Carrito de todos los catalogos").is_visible()
assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
page.keyboard.press("Escape")
assert not page.get_by_role("dialog", name="Carrito de todos los catalogos").is_visible()
```

Reopen, focus the close button, press Shift+Tab/Tab around the boundary, and assert focus stays inside the dialog. If any assertion fails, adjust only `MixedCartDrawer.jsx` focus handling or the `.mixed-cart-*` responsive rules.

- [ ] **Step 7: Run E2E, static UI and build together**

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py tests/test_mixed_catalog_cart_ui.py tests/test_supplier_catalog_ui.py tests/test_web_ui_defaults.py -q
Push-Location mobiliti_saas\web
npm.cmd run build
Pop-Location
```

Expected: PASS with no console errors, no horizontal overflow and no network outside the local API stubs.

- [ ] **Step 8: Commit the browser acceptance**

```powershell
git add -- tests/test_mixed_catalog_browser_e2e.py mobiliti_saas/web/src/MixedCartDrawer.jsx mobiliti_saas/web/src/styles.css
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "test(web): cubrir carrito mixto en navegador"
```

---

### Task 11: Exercise API → worker → one final Excel and run the local release gate

**Files:**

- Create: `tests/test_mixed_catalog_quote_e2e.py`

**Interfaces:**

- Consumes: real authenticated mixed API route, real mixed builder, captured in-memory API storage, real `process_job`, workbook adapter, parser, engine and the existing corporate worker template.
- Produces: one queued job, one frozen seven-catalog JSON upload, one worker claim/download/generator invocation/output upload and one local final `.xlsx`; proof of one totals block, line-level suppliers/discounts, warning/configuration/image propagation and no double conversion.
- Sonara evidence: real ignored PDFs are parsed into an in-memory/local candidate only; no repository stage, publish, storage upload or deployment call.

- [ ] **Step 1: Add a red API-to-worker full-pipeline test**

Create `tests/test_mixed_catalog_quote_e2e.py`. Configure the actual `/catalogs/mixed-quote` endpoint with authoritative seven-catalog fixtures and deterministic rates while replacing only authentication/subscription, database persistence, remote storage and worker wakeup with in-memory fakes. Do not monkeypatch `build_mixed_catalog_cart_payload`, `validate_mixed_catalog_payload`, the mixed workbook converter, `process_job`, `_prepare_generator_input` or the quote engine.

POST the minimal browser contract and capture the API's one uploaded input:

```python
ROOT = Path(__file__).resolve().parents[1]
WORKER_TEMPLATE = (
    ROOT / "mobiliti_saas" / "worker" / "templates" / "Formato Cotizacion 2026 GDL.xlsx"
)
assert WORKER_TEMPLATE.is_file()

output_xlsx = tmp_path / "cotizacion_mixta_final.xlsx"

body = {
    "items": browser_rows_for_all_catalogs_and_two_alma_configs(),
    "quote_currency": quote_currency,
    "descuento": "40",
    "proyecto": "Proyecto mixto",
    "cliente": "Cliente prueba",
    "correo": "cliente@example.test",
    "telefono": "3330000000",
    "direccion": "Guadalajara",
    "razon_social": "Cliente SA de CV",
    "image_provider": "pillow",
    "template": WORKER_TEMPLATE.name,
}

response = api_client.post(
    "/catalogs/mixed-quote",
    headers=auth_headers,
    json=body,
)
assert response.status_code == 200
queued_job = response.json()["job"]
assert api_events == ["create_job", "reserve_mixed", "upload", "queue", "wake"]
assert len(api_uploaded_objects) == 1
input_path, input_bytes, content_type = api_uploaded_objects[0]
assert input_path == queued_job["input_path"]
assert content_type == "application/json"
payload = json.loads(input_bytes)
assert payload["source_type"] == "mixed_catalog_cart"
```

Feed that exact queued job and storage object into `EndToEndWorkerClient`. Its `rest` method must implement the real claim/progress/completed PATCH contracts; download writes `input_bytes`; upload reads the temporary output into memory; delete records only the in-memory input path. Wrap but do not replace the real generator:

```python
worker_client = EndToEndWorkerClient(queued_job, {input_path: input_bytes})
real_run_generator = quote_worker._run_generator
generator_calls = []

def counted_run_generator(job, generator_input, local_output):
    generator_calls.append(generator_input.name)
    return real_run_generator(job, generator_input, local_output)

monkeypatch.setattr(quote_worker, "_run_generator", counted_run_generator)
monkeypatch.setattr(quote_worker, "_template_path", lambda: str(WORKER_TEMPLATE))
monkeypatch.setattr(quote_worker, "QUOTE_ENGINE", "python")

completed = quote_worker.process_job(worker_client, queued_job)
assert completed
assert generator_calls == ["quotation_from_mixed_catalog.xlsx"]
assert worker_client.downloads == [input_path]
assert len(worker_client.uploads) == 1
uploaded_path, output_bytes = worker_client.uploads[0]
assert uploaded_path == f"users/7/jobs/{queued_job['id']}/output.xlsx"
assert output_bytes.startswith(b"PK")
assert worker_client.deleted_inputs == [input_path]
output_xlsx.write_bytes(output_bytes)
assert output_xlsx.is_file()
```

Assert the completed worker metadata has `mixed_catalog_converted=True`, `catalog_price_mode="mixed_catalog_converted"`, quote/quote currency, rate `1.000000`, deep-copied rate summaries, unchanged `catalog_source_hashes` and `descuento=0`. After the worker deletes the input object, assert the completed job metadata still contains the seven source hashes, preserving catalog audit without retaining the reservation-bearing JSON. The in-memory fake must record exactly one completed output and no failed update.

The fixture must contain one line per catalog, two ALMA configurations for one `internal_id` whose selected add-ons have the distinct visible names `Electrificacion A` and `Pasacables B`, one Sonara `needs_review` line, one Tarkett/Offiho furniture line eligible for legacy auto electrification and one manual Lumbro line. No generic supplier line may be marked auto-electrified.

- [ ] **Step 2: Provide local deterministic images for three source policies**

Monkeypatch the adapter download helper to map the authoritative frozen URLs to three generated PNGs (Tarkett, Offiho, ALMA). Do not patch image extraction or the final engine. This proves the single `Quotation` carries images that the real final engine copies.

```python
def make_png(name, color):
    path = tmp_path / name
    Image.new("RGB", (96, 72), color).save(path, format="PNG")
    return path


local_images = {
    ("tarkett_cart", "https://media.tarkett-image.com/e2e-tarkett.png"):
        make_png("tarkett.png", (20, 70, 120)),
    ("offiho_cart", "https://offiho.com.mx/e2e-offiho.png"):
        make_png("offiho.png", (120, 70, 20)),
    ("supplier_cart", "https://alma.example.test/e2e-alma.png"):
        make_png("alma.png", (40, 120, 70)),
}
image_calls = []


def local_catalog_image(url, image_dir, code, source_type, destination_key=None):
    image_calls.append((source_type, url, destination_key))
    return local_images[(source_type, url)]


monkeypatch.setattr(catalog_cart, "_download_catalog_image", local_catalog_image)
```

Use the three URLs above in the authoritative catalog fixtures and leave every other fixture `image_url` empty. After conversion, assert `image_calls` contains exactly those three source/URL pairs and three distinct nonempty `destination_key` values before checking both workbook image collections.

- [ ] **Step 3: Run the pipeline test and observe the first uncovered integration failure**

```powershell
python -m pytest tests/test_mixed_catalog_quote_e2e.py -q
```

Expected before final integration fixes: FAIL at the earliest missing wiring between worker metadata, parser fields, engine formulas or image copying. Do not weaken the assertion; fix the owning task's code.

- [ ] **Step 4: Assert exactly one workbook set and canonical provider order**

Open the final workbook with `data_only=False`:

```python
assert wb.sheetnames.count("Cotizacion") == 1
assert wb.sheetnames.count("Mobiliti") == 1
assert wb.sheetnames.count("Quotation") == 1
quotation_ws = wb["Quotation"]
assert [
    quotation_ws.cell(row, 1).value
    for row in range(8, quotation_ws.max_row + 1)
    if isinstance(quotation_ws.cell(row, 1).value, str)
] == ["- Tarkett", "- Offiho", "- CR Global", "- Sonara", "- Sunon", "- ALMA", "- Lumbro"]
```

Assert global product numbers are consecutive despite category rows.

- [ ] **Step 5: Assert provider, discount, warnings, configurations and images survive**

Map each `Quotation!B<row>` formula to its `Mobiliti`/`Cotizacion` row and assert:

```python
assert providers == ["Tarkett", "Offiho", "CR Global", "Sonara", "Sunon", "ALMA", "ALMA", "Lumbro"]
assert discounts[:2] == [0.4, 0.4]
assert discounts[2:] == [0, 0, 0, 0, 0, 0]
assert "Codigo por verificar" in sonara_description
assert "electrificacion a" in alma_a_description.casefold()
assert "pasacables b" in alma_b_description.casefold()
assert alma_a_description != alma_b_description
assert len(quotation_ws._images) >= 3
assert len(wb["Cotizacion"]._images) >= 3
```

Also assert the manual Lumbro product exists as its own row and automatic accessories remain attached only to lines whose S column is true.

- [ ] **Step 6: Assert identity exchange and one rounded totals block**

For each final currency `MXN`, `USD`, `EUR`, build a payload and assert:

```python
assert mobiliti["J6"].value == f"{quote_currency}/{quote_currency}"
assert mobiliti["K6"].value == 1
assert payload["auto_electrification_rate"]["exchange_rate"] == {
    "MXN": "1.000000", "USD": "0.054054", "EUR": "0.048780",
}[quote_currency]
assert len(total_rows) == 5
assert [cot.cell(row, 4).value for row in total_rows] == [
    "SUBTOTAL:", "COSTO DE FLETE:", "SUBTOTAL:", "IVA:", "TOTAL:"
]
```

For every automatic Lumbro row, assert its `Mobiliti!J` formula contains `SPEC-GUIDE-LUMBRO`, multiplies by the exact mapped rate above and never references `$K$6`; assert the parent `Cotizacion!F` formula includes every corresponding raw accessory term `Mobiliti!X<row>*Mobiliti!H<row>` exactly once and contains no accessory `Mobiliti!Y` reference. Manual Lumbro rows remain ordinary products and create no automatic children.

Use this deterministic helper in the test to calculate the expected commercial totals from the frozen rows:

```python
MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def expected_mixed_totals(
    payload: dict,
    accessories_by_parent: dict[str, list[tuple[Decimal, Decimal]]],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    net = Decimal("0")
    auto_rate = Decimal(payload["auto_electrification_rate"]["exchange_rate"])
    for group in payload["groups"]:
        for item in group["items"]:
            price = Decimal(item["unit_price"])
            discount = Decimal(item["discount_percent"]) / Decimal("100")
            quantity = Decimal(item["quantity"])
            accessory_total = sum(
                (
                    money(unit_price_mxn * auto_rate) * accessory_quantity
                    for unit_price_mxn, accessory_quantity
                    in accessories_by_parent.get(item["canonical_key"], [])
                ),
                Decimal("0"),
            )
            combined_unit = money(((price * quantity) + accessory_total) / quantity)
            discount_amount = money(combined_unit * discount)
            net_unit = money(combined_unit - discount_amount)
            net += money(net_unit * quantity)
    net = money(net)
    freight = money(net * Decimal("0.12"))
    before_tax = money(net + freight)
    tax = money(before_tax * Decimal("0.16"))
    return net, freight, before_tax, tax, money(before_tax + tax)
```

Build `accessories_by_parent` from the test's known `LIDO.OP-INT`, `JUMP-1.5M` and `CAJA-FUS` quantities and the numeric `SPEC-GUIDE-LUMBRO!E` values in `WORKER_TEMPLATE`; each entry is `(unit_price_mxn, accessory_quantity)`. The helper deliberately follows Excel order `ROUND(unit_price_mxn * rate, 2) * quantity`, not `ROUND(unit_price_mxn * quantity * rate, 2)`. Assert Quotation J contains each frozen `unit_price` unchanged, Cotizacion G contains `0.4` only for Tarkett/Offiho and `0` for every generic row, all mixed product price formulas avoid `$K$6`, and the five totals formulas are the exact rounded chain shown in Task 6. Assert the independently calculated tuple is internally consistent (`before_tax == net + freight`, `total == before_tax + tax`) and that each workbook formula references exactly those inputs. These assertions make either a second FX multiplication or a second generic discount fail without depending on Excel cached values.

- [ ] **Step 7: Run the real local Sonara candidate audit without publishing**

Verify the two ignored source hashes remain exactly `35c4abd3c4b3fef5c11cb8b7b22509f9913343b9ee79bf4cc6ae9c6aac3f0099` for `Catalogo-Sonara.pdf` and `c497314221f5e700d6722deb92a3dbb02c4686e7b39e17766332bee6a6e05128` for `Lista de precios Sonara 2026.pdf`, then execute:

```powershell
python -m pytest tests/test_catalog_importers_sonara.py -k "ignored_real_sources" -q -s
```

Expected when sources are present:

```text
SONARA_REAL_METRICS contains rows=39, nonzero_prices=39, blocked_prices=0, verified_codes=7, needs_review=32, currency_warnings=0
```

The candidate exists only in the test process/temp files. Do not call `run_supplier_sync`, `stage_candidate`, `auto_publish_candidate`, the admin approval endpoint, or any storage upload. If the ignored PDFs are absent, report the SKIP as an environmental limitation; do not download or publish them under this task.

- [ ] **Step 8: Run all focused backend/worker/engine/UI gates**

```powershell
python -m pytest tests/test_catalog_importers_sonara.py tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_workbook.py tests/test_catalog_migrations.py tests/test_mixed_catalog_postgres.py tests/test_quote_jobs_api.py tests/test_quote_worker.py tests/test_mixed_quote_engine.py tests/test_quote_engine_golden.py tests/test_quote_engine_lumbro.py tests/test_mixed_catalog_cart_ui.py tests/test_supplier_catalog_ui.py tests/test_web_ui_defaults.py tests/test_mixed_catalog_browser_e2e.py tests/test_mixed_catalog_quote_e2e.py -q
```

Expected: PASS, with only the documented real-source/browser environment skips permitted.

- [ ] **Step 9: Run the complete local verification command**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\verify-saas.ps1 -SkipSmoke
```

Expected final line: `Mobiliti SaaS verify OK`. This runs the complete Python suite and Vite production build but does not deploy.

- [ ] **Step 10: Audit mirrors, working tree and forbidden production actions**

```powershell
python -m pytest tests/test_quote_jobs_api.py -k "deployable_api_copies or module_copies" -q
python -m pytest tests/test_mixed_catalog_cart.py -k "copies_are_byte_identical" -q
git diff --check
git status --short
```

Confirm no SQL was applied, no remote snapshot/storage changed, no production command ran, `.cache/catalog-sources/sonara` is untracked/ignored, and every pre-existing unrelated change remains present.

- [ ] **Step 11: Commit the end-to-end regression only**

```powershell
git add -- tests/test_mixed_catalog_quote_e2e.py
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "test(quote): cerrar flujo mixto hasta Excel"
```

If Step 3 required a code correction, commit that correction separately with its focused regression before this test-only commit.

---

## Final acceptance trace

| Approved criterion | Evidence task |
|---|---|
| Sonara missing currency → auditable MXN/nonzero; foreign/conflict → closed | Task 1 |
| Sonara/Lumbro review codes only under valid commercial data | Tasks 1 and 9 |
| One global cart across seven tabs, configurations remain distinct | Tasks 8–10 |
| Browser POST contains identities/configuration/quantity only | Tasks 2, 8 and 10 |
| Server reloads catalogs, rejects tampering, freezes MXN/USD/EUR | Tasks 2 and 4 |
| Tarkett/Offiho discount; generic price net/0 %; IVA 16 % | Tasks 2 and 6 |
| One atomic reserve/release with rollback and concurrency | Task 3 |
| Upload/queue failures compensate all families | Task 4 |
| One Quotation with provider sections, L–S audit and safe images | Task 5 |
| Provider/discount/electrification per line; quote/quote rate 1 | Task 6 |
| Worker supports all four JSON source types and invokes generator once | Task 7 |
| One final Excel, one totals block, correct flete/IVA/total | Task 11 |
| Failure retains cart; success/logout/expiry clear it | Tasks 9 and 10 |
| Legacy endpoints/jobs stay green | Tasks 4, 6, 7 and 11 |
| Desktop/mobile, no console errors or horizontal overflow | Task 10 |
| Candidate Sonara local only; no publish/migration/deploy | Task 11 |

The implementation is complete only when every applicable checkbox above is green and the final working-tree audit shows no unrelated hunk was staged or lost.
