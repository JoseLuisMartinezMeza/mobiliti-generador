# Lumbro 2026 Supplier Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Incorporar Lumbro como catálogo independiente y completo, alimentado por las cinco fuentes oficiales de SharePoint 2026, con precio neto MXN más IVA, variantes verificables, imágenes y enlaces oficiales, sin alterar la electrificación Lumbro automática existente.

**Architecture:** Extender el pipeline genérico multi-proveedor existente. Un allowlist de archivos exactos descarga y valida las fuentes; el adaptador Lumbro reconcilia precio, identidad, especificaciones, imágenes y enlaces en un snapshot inmutable; API, worker, carrito, UI y XLSX consumen el contrato genérico `supplier_catalog`. No habrá crawling web en runtime, cálculo adicional de descuento, migraciones aplicadas ni despliegue a producción durante esta entrega.

**Tech Stack:** Python 3.14, pytest, openpyxl en modo `data_only`, pypdf/PDF helpers existentes, FastAPI, Supabase SQL (archivos de migración solamente), React/Vite, Playwright/in-app browser y Obsidian MCP.

## Global Constraints

- La rama de trabajo es `codex/offiho-catalog-20260709` en `C:\Users\pepem\Downloads\ARMADO_DE_CARATULA_prod_git_worktree`. Confirmar ambos antes de cada bloque de cambios.
- El worktree ya contiene cambios ajenos y archivos sin seguimiento. Nunca ejecutar `git add -A`, `git clean`, `git reset`, `git checkout`, `git restore` ni una eliminación permanente. Preparar cada commit con rutas explícitas y revisar `git diff --cached`.
- Las cinco fuentes se identifican por ruta relativa, `drive_item_id` y SHA-256. No aceptar archivos homónimos ni ampliar la raíz de Microsoft Graph.
- Autoridad de precio: `Precios Interconexión Sunón act.xlsx` hoja `2026` > lista de nuevos productos > lista general 2026. El spec guide y el catálogo PDF nunca fijan precios.
- Los importes de las fuentes comerciales ya son precios netos. No restar otro 10 %. `tax_rate=0.16` se conserva separado; moneda `MXN`; unidad `PZA`; cantidad positiva entera.
- No inventar SKU, ficha, imagen, stock ni disponibilidad. Una identidad o precio ambiguos producen `needs_review` y bloquean el carrito para esa variante.
- No modificar `LUMBRO_PRICE_ROWS` ni la lógica automática de electrificación en `mobiliti_saas/quote_engine/engine.py`.
- No aplicar SQL, sincronizar SharePoint, publicar snapshots/assets ni desplegar Vercel/Supabase/producción. La validación final usa únicamente el dev-store y navegador local.
- Seguir TDD estricto en cada tarea: escribir la prueba, observar el fallo esperado, implementar lo mínimo, observar el pase y ejecutar la regresión indicada.
- Los cinco archivos de inspección local residen en `.cache/catalog_sources/lumbro/sharepoint_2026-07-18/`, están ignorados y no se versionan.

## Source Contract

| Prioridad | Ruta relativa exacta | Graph item ID | SHA-256 | Uso |
|---|---|---|---|---|
| 1 | `LUMBRO/LP/Precios Interconexión Sunón act.xlsx` | `01DHXXN7Y4QLJBB6BVO5CLJR5WQHD6ETGY` | `48376c65038c65ce07c658f3570c741dac70c9cdf676f171dda3674a9925551b` | Precio/variantes de interconexión, solo hoja activa `2026` |
| 2 | `LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf` | `01DHXXN72MMCJPX2ENKRCLIVOLPBNYLFX7` | `19d38a8aa98df2d4f77f229cc94813b26f13d1809f0231c73b3fa9b64d4f1a29` | Precio de modelos nuevos |
| 3 | `LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf` | `01DHXXN73PQIV3NEC74BFIAXGF7HN3S3NE` | `83319649f387ba14107854e39e2cf9c70a03d0a121e71080efcec1d46e1654d5` | Precio general 2026 |
| enriquecimiento | `SPEC GUIDES 2026/LUMBRO/Spec guide-Lumbro-2026.xlsx` | `01DHXXN726RRTWDBVGDZH3DHSR4XUGGYNG` | `fce1a47fa719300fd3b6be5edf934f6c9a082676427ea6b5fa8d20bd06b8f3d1` | Código, descripción, medidas, color e imagen |
| enriquecimiento | `LUMBRO/CATALOGO/CATALOGO LUMBRO 2024 DIGITAL (1).pdf` | `01DHXXN7YFOCIP7S2WR5F3AFZF3Z5ITB3J` | `bbd810ebab20336d2a6bdc61123955bd062c5a64d57d4359556fcf6aef57e053` | Categoría, medidas y especificaciones técnicas |

## Task 1: Pin exact SharePoint sources in the catalog allowlist

**Files:**

- Modify: `mobiliti_saas/worker/catalog_sync/__init__.py`
- Modify: `mobiliti_saas/worker/catalog_sync/sources.json`
- Modify: `mobiliti_saas/worker/catalog_sync/service.py`
- Modify: `tests/test_catalog_source_config.py`
- Modify: `tests/test_catalog_sync_service.py`

**Consumes:** Existing `SupplierFileConfig`, `SupplierSourceConfig`, `_FIRST_WAVE_ALLOWLIST`, Graph discovery rows and `_configured_path`.

**Produces:** Optional immutable `drive_item_id` on a configured source; one exact five-file `lumbro` entry; discovery rejection when a same-name file has the wrong Graph item ID.

- [ ] Add failing tests asserting five Lumbro files, total configured file count `18`, exact item IDs, and rejection of a discovery row with the right path but a different ID.

```python
def test_lumbro_sources_are_pinned_by_path_kind_and_graph_id():
    lumbro = {row.supplier: row for row in load_source_config(SOURCES_JSON)}["lumbro"]
    assert len(lumbro.files) == 5
    assert {file.drive_item_id for file in lumbro.files} == {
        "01DHXXN73PQIV3NEC74BFIAXGF7HN3S3NE",
        "01DHXXN72MMCJPX2ENKRCLIVOLPBNYLFX7",
        "01DHXXN7Y4QLJBB6BVO5CLJR5WQHD6ETGY",
        "01DHXXN726RRTWDBVGDZH3DHSR4XUGGYNG",
        "01DHXXN7YFOCIP7S2WR5F3AFZF3Z5ITB3J",
    }
```

- [ ] Run the focused tests and observe failure because `lumbro` and `drive_item_id` do not exist:

```powershell
python -m pytest tests/test_catalog_source_config.py tests/test_catalog_sync_service.py -q
```

Expected failure: `KeyError: 'lumbro'`, an adapter-set assertion missing `lumbro`, or `AttributeError: SupplierFileConfig has no attribute drive_item_id`.

- [ ] Add `drive_item_id: str | None = None` to `SupplierFileConfig`; parse it with a strict Graph-item-ID regex when present and include it in equality/allowlist validation.
- [ ] Add the exact `lumbro` source object to both Python allowlist and `sources.json`. Use existing kinds only: three `price_list`, one `spec_guide`, one `catalog`.
- [ ] Update `_configured_path` so a configured ID must equal the discovered row ID when present; preserve existing path-only behavior for the four suppliers whose entries omit an ID.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Review and commit only this task's hunks:

```powershell
git add -p -- mobiliti_saas/worker/catalog_sync/__init__.py mobiliti_saas/worker/catalog_sync/sources.json mobiliti_saas/worker/catalog_sync/service.py tests/test_catalog_source_config.py tests/test_catalog_sync_service.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): fijar fuentes oficiales Lumbro"
```

## Task 2: Materialize a strict official-link manifest

**Files:**

- Create: `mobiliti_saas/worker/catalog_sync/data/lumbro_links.v1.json`
- Create: `mobiliti_saas/worker/catalog_sync/lumbro_links.py`
- Create: `tests/test_lumbro_links.py`

**Consumes:** Verified official pages under `https://www.lumbromx.com`, including `/product-page/venecia`, `/product-page/ibiza`, `/empotrados`, `/productos-1` and `/category/all-products`.

**Produces:** `resolve_lumbro_link(model, category)` returning an immutable URL plus status `exact_index`, `collection_index` or `catalog_fallback`; deterministic resource fingerprint for snapshot hashing.

- [ ] Write failing tests for exact Venecia/Ibiza links, category fallback, general fallback, forbidden host/scheme, duplicate normalized keys and fingerprint stability.

```python
def test_resolver_never_guesses_a_product_slug():
    result = resolve_lumbro_link("MODELO SIN FICHA", "Empotrables")
    assert result.url == "https://www.lumbromx.com/empotrados"
    assert result.status == "collection_index"

def test_exact_official_product_link():
    result = resolve_lumbro_link("VENECIA", "Empotrables")
    assert result.url == "https://www.lumbromx.com/product-page/venecia"
    assert result.status == "exact_index"
```

- [ ] Run and observe import failure:

```powershell
python -m pytest tests/test_lumbro_links.py -q
```

Expected failure: `ModuleNotFoundError: ...lumbro_links`.

- [ ] Implement a versioned JSON schema with explicit normalized model keys, category mappings and one general fallback. Reject any URL that is not HTTPS or whose hostname is not exactly `www.lumbromx.com`.
- [ ] Implement exact normalized lookup only; do not synthesize slugs or use fuzzy matching. Include canonical JSON bytes in `resource_fingerprint()`.
- [ ] Re-run tests and confirm they pass.
- [ ] Commit with explicit paths:

```powershell
git add -- mobiliti_saas/worker/catalog_sync/data/lumbro_links.v1.json mobiliti_saas/worker/catalog_sync/lumbro_links.py tests/test_lumbro_links.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): resolver enlaces oficiales Lumbro"
```

## Task 3: Parse the two commercial PDF price lists without mutating prices

**Files:**

- Create: `mobiliti_saas/worker/catalog_sync/importers/lumbro.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Create: `tests/fixtures/catalog_graph/lumbro/price_general_pages.json`
- Create: `tests/fixtures/catalog_graph/lumbro/price_new_pages.json`
- Create: `tests/test_catalog_importers_lumbro.py`

**Consumes:** Validated PDF page text through `iter_pdf_pages`; exact logical paths and source metadata.

**Produces:** Typed internal `LumbroPriceRecord` rows carrying model/configuration, net MXN, source path/page, authority rank and parse status.

- [ ] Add fixtures representative of general and new-product layouts and failing parser tests for `Barcelona=2824`, `Ibiza Carga A+C=824`, `Venecia inalámbrico`, repeated headings, malformed currency and duplicate conflicting prices.

```python
def test_general_pdf_keeps_published_net_price():
    rows = parse_lumbro_pdf_prices(general_pdf_source())
    barcelona = next(row for row in rows if row.identity == "barcelona")
    assert barcelona.net_price == Decimal("2824")
    assert barcelona.currency == "MXN"
    assert barcelona.tax_rate == Decimal("0.16")
    assert barcelona.source.page > 0
```

- [ ] Run and observe failure because the importer/parser is absent:

```powershell
python -m pytest tests/test_catalog_importers_lumbro.py -k "pdf or general or new" -q
```

Expected failure: import error for `parse_lumbro_pdf_prices`.

- [ ] Implement strict file-set validation and a line/state parser that only accepts explicit Lumbro product/price patterns. Parse money with `Decimal`, never `float`, and never subtract 10 % or add IVA.
- [ ] Keep duplicate rows as evidence until reconciliation; mark malformed or conflicting rows for review instead of silently discarding them.
- [ ] Export only the public builders from `importers/__init__.py`; test helpers may remain module-private and be exercised through a fixture builder.
- [ ] Re-run the focused tests and confirm pass.
- [ ] Commit:

```powershell
git add -- mobiliti_saas/worker/catalog_sync/importers/lumbro.py mobiliti_saas/worker/catalog_sync/importers/__init__.py tests/fixtures/catalog_graph/lumbro/price_general_pages.json tests/fixtures/catalog_graph/lumbro/price_new_pages.json tests/test_catalog_importers_lumbro.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): leer precios comerciales Lumbro"
```

## Task 4: Extract spec-guide identity, variants, dimensions and images

**Files:**

- Modify: `mobiliti_saas/worker/catalog_sync/importers/lumbro.py`
- Modify: `tests/test_catalog_importers_lumbro.py`

**Consumes:** `SPEC-GUIDE-LUMBRO` rows 8–520 loaded with `data_only=True`; embedded images anchored to product rows; columns A–F for code, image, description, measure/unit, displayed unit price and currency.

**Produces:** Identity/spec records with exact source row, official code when present, description continuations, dimensions, colors, mounting/configuration attributes and image binding. Column E is retained only as non-authoritative evidence.

- [ ] Add failing tests using a minimal generated workbook with merged/continued rows and an image anchor. Assert code, description, `245 x 102 x 60 mm`, colors, image binding and one variant per explicit configuration/color.
- [ ] Add a critical precedence assertion: spec guide cached `5648` must never become Barcelona's catalog price when the commercial PDF says `2824`.

```python
def test_spec_price_is_never_commercial_authority(lumbro_build):
    barcelona = item_by_model(lumbro_build.snapshot, "Barcelona")
    assert barcelona["price_net"] == "2824.000000"
    assert barcelona["attributes"]["spec_price_evidence"] == 5648
    evidence = json.loads(barcelona["source_reference"])
    assert evidence["price"]["path"].endswith("LISTA DE PRECIOS MULTICONTACTOS 2026.pdf")
```

- [ ] Run and observe the new assertions fail:

```powershell
python -m pytest tests/test_catalog_importers_lumbro.py -k "spec or image or variant or authority" -q
```

Expected failure: missing dimensions/image/variant fields or incorrect price `5648`.

- [ ] Implement block parsing: heading row → coded row → continuation/color/mounting/note rows until the next heading. Normalize whitespace without destroying source text.
- [ ] Bind each embedded image to its exact coded row. Permit model-level reuse for explicit color/configuration variants only as provenance `family_xlsx` plus `image_warning="El color puede variar"`; otherwise use the existing placeholder.
- [ ] Preserve formula/cached price only in diagnostic evidence and exclude it from price resolution.
- [ ] Re-run and confirm pass.
- [ ] Commit:

```powershell
git add -p -- mobiliti_saas/worker/catalog_sync/importers/lumbro.py tests/test_catalog_importers_lumbro.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): enriquecer variantes e imagenes Lumbro"
```

## Task 5: Parse only the active 2026 interconnection sheet and enforce precedence

**Files:**

- Modify: `mobiliti_saas/worker/catalog_sync/importers/lumbro.py`
- Modify: `tests/test_catalog_importers_lumbro.py`

**Consumes:** Workbook `Precios Interconexión Sunón act.xlsx`; active sheet must be named `2026`; cached values in G/H and the O/P pair on row 4; embedded images from the same sheet.

**Produces:** Interconnection price/spec records with the highest authority rank and exact workbook sheet/cell provenance.

- [ ] Add a generated two-sheet fixture (`2025`, active `2026`) and failing tests proving that `3003` wins over old `2587.5`, remains `3003` rather than `2702.7`, and captures row/cell evidence.

```python
def test_interconnection_uses_active_2026_cached_net_value(lumbro_build):
    item = item_by_source_code(lumbro_build.snapshot, "MULT-LIDO-INT")
    assert item["price_net"] == "3003.000000"
    assert item["attributes"]["price_source"]["sheet"] == "2026"
    assert item["attributes"]["price_source"]["cell"] == "H4"
    assert item["price_net"] != "2702.700000"
```

- [ ] Run and observe failure from missing Excel parsing or wrong sheet/price:

```powershell
python -m pytest tests/test_catalog_importers_lumbro.py -k "interconnection or active_2026 or cached_net" -q
```

Expected failure: no matching row or a price from `2025`/discounted twice.

- [ ] Open validated bytes through the existing passive-preflight/data-only helper. Reject the source if the active sheet is not exactly `2026` or the expected net-price header is absent.
- [ ] Parse explicit pairs only; preserve cached numeric formulas as their displayed value. Map the four existing internal codes only when description/configuration is exact: `MULT-LIDO-INT`, `LIDO.OP-INT`, `JUMP-1.5M`, `CAJA-FUS`.
- [ ] Keep any other row without a verifiable source code as `needs_review`; do not manufacture a SKU. Bind the 16 sheet images by anchor with the same image rules as Task 4.
- [ ] Re-run and confirm pass.
- [ ] Commit:

```powershell
git add -p -- mobiliti_saas/worker/catalog_sync/importers/lumbro.py tests/test_catalog_importers_lumbro.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): reconciliar interconexion Lumbro 2026"
```

## Task 6: Build a complete deterministic snapshot and coverage audit

**Files:**

- Modify: `mobiliti_saas/worker/catalog_sync/importers/lumbro.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Create: `scripts/audit_lumbro_catalog.py`
- Modify: `tests/test_catalog_importers_lumbro.py`
- Create: `tests/test_lumbro_catalog_audit.py`

**Consumes:** All parsed price/spec/catalog records, link resolver, image assets and the exact five-source set.

**Produces:** `build_lumbro_snapshot(files) -> dict`, `build_lumbro_snapshot_with_assets(files) -> CatalogSnapshotBuild`, deterministic `source_hash`, and coverage JSON in which every commercial source row is imported, reconciled or explicitly excluded with a reason.

- [ ] Add failing tests for precedence, stable IDs/hash, no silent row loss, exact JSON `source_reference`, no quoteability without price/code, category enrichment from the catalog PDF and fallback link labels/status.

```python
def test_every_price_row_has_an_audit_disposition(lumbro_build):
    coverage = lumbro_build.snapshot["metadata"]["coverage"]
    assert coverage["parsed_price_rows"] == (
        coverage["imported_rows"]
        + coverage["reconciled_rows"]
        + coverage["excluded_rows"]
    )
    assert all(row["reason"] for row in coverage["exclusions"])
```

- [ ] Run and observe missing builders/audit failure:

```powershell
python -m pytest tests/test_catalog_importers_lumbro.py tests/test_lumbro_catalog_audit.py -q
```

Expected failure: builders absent or coverage counters do not balance.

- [ ] Reconcile only exact compatible identities: model + technical configuration + color. Emit distinct variants when ports, cable, finish, color or price differ.
- [ ] Set `supplier="lumbro"`, `brand="Lumbro"`, `base_currency="MXN"`, `tax_rate="0.160000"`, `unit="PZA"`, `availability_type="unknown"`, `stock=None`; serialize `price_net` with six decimal places.
- [ ] Include normalized source file descriptors plus the link-manifest fingerprint in `source_hash`; sort records/assets deterministically.
- [ ] Implement the audit CLI with explicit arguments and no network writes:

```powershell
python scripts/audit_lumbro_catalog.py --source-dir .cache/catalog_sources/lumbro/sharepoint_2026-07-18 --output .superpowers/sdd/artifacts/lumbro-20260718/coverage.json
```

- [ ] Assert the real-source audit exits `0`, all five SHA-256 values match, totals balance and no source row disappears. Review exclusions manually.
- [ ] Run tests and confirm pass.
- [ ] Commit code/tests only; keep generated artifact ignored:

```powershell
git add -p -- mobiliti_saas/worker/catalog_sync/importers/lumbro.py mobiliti_saas/worker/catalog_sync/importers/__init__.py scripts/audit_lumbro_catalog.py tests/test_catalog_importers_lumbro.py tests/test_lumbro_catalog_audit.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): construir snapshot auditable Lumbro"
```

## Task 7: Register Lumbro in sync, repository and quote-domain contracts

**Files:**

- Modify: `mobiliti_saas/worker/catalog_sync/service.py`
- Modify: `mobiliti_saas/worker/catalog_sync/repository.py`
- Modify: `mobiliti_saas/quote_engine/supplier_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py`
- Modify: `mobiliti_saas/worker/quote_worker.py`
- Modify: `tests/test_catalog_sync_service.py`
- Modify: `tests/test_catalog_repository.py`
- Modify: `tests/test_supplier_catalog.py`
- Modify: `tests/test_quote_worker.py`

**Consumes:** Public Lumbro snapshot builder and existing generic supplier/cart/quote interfaces.

**Produces:** `lumbro` accepted by sync scheduling, repository, catalog item validation and worker generation; root/web domain copies remain byte-identical.

- [ ] Add failing registry and domain tests asserting `ADAPTERS["lumbro"]`, repository acceptance, label `Lumbro`, integer PZA quantities, rejected fractional quantities and correct net/IVA totals.

```python
def test_lumbro_cart_requires_integer_piece_quantity():
    catalog = load_supplier_catalog_data(lumbro_catalog_payload())
    cart = build_supplier_cart_payload(
        [{"internal_id": "lumbro:barcelona", "quantity": "2", "add_on_option_ids": []}],
        catalog,
        "MXN",
        [],
    )
    assert cart["items"][0]["quantity"] == "2"
    with pytest.raises(ValueError, match="entera"):
        build_supplier_cart_payload(
            [{"internal_id": "lumbro:barcelona", "quantity": "2.5", "add_on_option_ids": []}],
            catalog,
            "MXN",
            [],
        )
```

- [ ] Run and observe allowlist/registry failures:

```powershell
python -m pytest tests/test_catalog_sync_service.py tests/test_catalog_repository.py tests/test_supplier_catalog.py tests/test_quote_worker.py -q
```

Expected failure: `lumbro` not allowed/registered.

- [ ] Register the builder and supplier/label in the five generic registries. Do not add Lumbro-specific quote or cart branches.
- [ ] Copy the final domain module exactly to the web mirror and assert hashes match.
- [ ] Re-run tests and confirm pass.
- [ ] Commit explicit hunks:

```powershell
git add -p -- mobiliti_saas/worker/catalog_sync/service.py mobiliti_saas/worker/catalog_sync/repository.py mobiliti_saas/quote_engine/supplier_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py mobiliti_saas/worker/quote_worker.py tests/test_catalog_sync_service.py tests/test_catalog_repository.py tests/test_supplier_catalog.py tests/test_quote_worker.py
git diff --cached --check
git diff --cached
git commit -m "feat(catalog): registrar proveedor Lumbro"
```

## Task 8: Expose Lumbro through all three API copies and feature flags

**Files:**

- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Modify: `mobiliti_saas/.env.example`
- Modify: `mobiliti_saas/web/.env.example`
- Modify: `mobiliti_saas/CLOUD_DEPLOY.md`
- Modify: `tests/test_quote_jobs_api.py`

**Consumes:** Generic catalog endpoints, `ALLOWED_SUPPLIERS`, `CATALOG_SUPPLIER_ORDER`, `CATALOG_ENABLED_SUPPLIERS`.

**Produces:** Lumbro catalog/list/detail/quote-job behavior behind the existing feature flag, with canonical order `cr-global, sonara, sunon, alma, lumbro`; three API files byte-identical.

- [ ] Add failing tests for the canonical supplier registry, disabled-by-default behavior, enabled Lumbro endpoints, invalid supplier rejection and copy parity.
- [ ] Run and observe missing registry/API response:

```powershell
python -m pytest tests/test_quote_jobs_api.py -k "catalog and (registry or supplier or copies)" -q
```

Expected failure: returned supplier registry omits `lumbro` or copy hashes differ after editing only one API.

- [ ] Add `lumbro` and label `Lumbro` to the canonical order/labels. Preserve the empty/invalid flag behavior and do not enable production by default.
- [ ] Apply the same minimal edit to all three API copies; document `lumbro` as an accepted flag value without setting it.
- [ ] Re-run focused tests and confirm pass.
- [ ] Commit:

```powershell
git add -p -- mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py vercel_deploy/api/index.py mobiliti_saas/.env.example mobiliti_saas/web/.env.example mobiliti_saas/CLOUD_DEPLOY.md tests/test_quote_jobs_api.py
git diff --cached --check
git diff --cached
git commit -m "feat(api): exponer catalogo Lumbro"
```

## Task 9: Extend SQL supplier constraints without applying migrations

**Files:**

- Modify: `mobiliti_saas/supabase_setup/2026_07_multi_supplier_catalogs.sql`
- Modify: `mobiliti_saas/supabase_setup/create_tables.sql`
- Modify: `tests/test_catalog_migrations.py`

**Consumes:** Existing supplier CHECK constraints, array validation and RPC guards.

**Produces:** SQL text accepting exactly the five suppliers everywhere, while remaining unapplied locally and in production.

- [ ] Add failing SQL-text tests that enumerate every supplier allowlist occurrence and require `lumbro` in each, with no inconsistent four-supplier fragment remaining.

```python
def test_all_supplier_sql_allowlists_include_lumbro():
    for sql_path in SQL_FILES:
        text = sql_path.read_text(encoding="utf-8")
        assert "'cr-global','sonara','sunon','alma'" not in text
        assert text.count("'cr-global','sonara','sunon','alma','lumbro'") >= EXPECTED_COUNTS[sql_path.name]
```

- [ ] Run and observe failure on existing four-supplier constraints:

```powershell
python -m pytest tests/test_catalog_migrations.py -q
```

Expected failure: stale SQL fragment without `lumbro`.

- [ ] Extend every table CHECK, JSON/array validation and RPC guard consistently. Do not run `psql`, Supabase MCP migration calls or deployment commands.
- [ ] Re-run migration text tests and confirm pass.
- [ ] Commit:

```powershell
git add -p -- mobiliti_saas/supabase_setup/2026_07_multi_supplier_catalogs.sql mobiliti_saas/supabase_setup/create_tables.sql tests/test_catalog_migrations.py
git diff --cached --check
git diff --cached
git commit -m "feat(db): permitir proveedor Lumbro"
```

## Task 10: Add the Lumbro sidebar entry and truthful official-link label

**Files:**

- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/web/src/SupplierCatalogView.jsx`
- Modify: `tests/test_supplier_catalog_ui.py`

**Consumes:** Generic sidebar metadata and `product_url_match.status` from snapshot items.

**Produces:** New Lumbro navigation entry; `Ver producto` only for exact detail pages; `Ver catálogo Lumbro` for Lumbro category/general fallbacks; existing labels for other suppliers unchanged.

- [ ] Add failing static/UI contract tests for sidebar order, label, accessible link text and integer quantity input.

```python
def test_lumbro_fallback_link_is_explicit():
    component = SUPPLIER_VIEW.read_text(encoding="utf-8")
    assert 'supplier === "lumbro"' in component
    assert '"Ver catálogo Lumbro"' in component
    assert 'step="1"' in component
```

- [ ] Run and observe failure because the tab and label are absent:

```powershell
python -m pytest tests/test_supplier_catalog_ui.py -q
```

Expected failure: sidebar tuple mismatch and missing `Ver catálogo Lumbro`.

- [ ] Add Lumbro through the existing generic metadata, not a new component. Make `productLinkLabel(item, supplier)` supplier-aware while preserving `Ver catálogo general`/`Ver colección` semantics for ALMA and other suppliers.
- [ ] Ensure the external-link control has visible adjacent text and an accessible label explaining its destination.
- [ ] Preserve integer PZA quantity controls (`min=1`, `step=1`) and dimensions/specification rendering.
- [ ] Re-run tests and confirm pass.
- [ ] Commit:

```powershell
git add -p -- mobiliti_saas/web/src/main.jsx mobiliti_saas/web/src/SupplierCatalogView.jsx tests/test_supplier_catalog_ui.py
git diff --cached --check
git diff --cached
git commit -m "feat(web): agregar catalogo Lumbro"
```

## Task 11: Verify cart, API, worker and XLSX end to end

**Files:**

- Create: `tests/test_lumbro_catalog_e2e.py`
- Modify: `tests/test_quote_engine_lumbro.py`
- Modify: `tests/test_mobiliti_capacity.py`

**Consumes:** A deterministic Lumbro snapshot fixture, generic API/catalog cart, quote job payload and worker workbook generation.

**Produces:** Evidence that a Lumbro product carries code, description, dimensions, image/link/source and `price_net × integer quantity`, with IVA calculated once; automatic Lumbro electrification remains unchanged.

- [ ] Add an end-to-end failing test that selects Barcelona, adds quantity `2`, creates a quote job and inspects generated item/XLSX fields. Assert net subtotal `5648`, IVA `903.68`, and no second discount.
- [ ] Add/strengthen regression tests that retain the four automatic codes and quantity semantics in `LUMBRO_PRICE_ROWS`; do not edit production engine behavior.

```python
def test_lumbro_catalog_quote_uses_net_price_plus_iva(lumbro_snapshot):
    catalog = load_supplier_catalog_data(lumbro_snapshot)
    cart = build_supplier_cart_payload(
        [{"internal_id": "lumbro:barcelona", "quantity": "2", "add_on_option_ids": []}],
        catalog,
        "MXN",
        [],
    )
    line = cart["items"][0]
    assert line["unit_price"] == "2824.00"
    assert line["line_total"] == "5648.00"
    assert (Decimal(line["line_total"]) * Decimal(line["tax_rate"])).quantize(Decimal("0.01")) == Decimal("903.68")
    assert line["quantity"] == "2"
    assert "MULTICONTACTOS 2026.pdf" in line["source_reference"]
```

- [ ] Run and observe initial integration failure:

```powershell
python -m pytest tests/test_lumbro_catalog_e2e.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_capacity.py -q
```

Expected failure: fixture cannot traverse unregistered Lumbro path; pre-existing automatic tests must already pass.

- [ ] Fix only generic boundary defects revealed by the test. Do not special-case totals, workbook rows or automatic electrification for Lumbro.
- [ ] Inspect the generated XLSX with openpyxl for exact numeric values, PZA, source/code/description, image count and neutralized strings.
- [ ] Re-run and confirm all three files pass.
- [ ] Commit only tests plus any minimal generic boundary hunk actually required:

```powershell
git add -p -- tests/test_lumbro_catalog_e2e.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_capacity.py mobiliti_saas/quote_engine/supplier_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py mobiliti_saas/worker/quote_worker.py
git diff --cached --check
git diff --cached
git commit -m "test(catalog): cubrir flujo completo Lumbro"
```

## Task 12: Seed a recoverable local preview and validate it visually

**Files:**

- Create: `docs/superpowers/reports/2026-07-18-lumbro-local-verification.md`
- Runtime-only: existing local dev-store/snapshot files and `.superpowers/sdd/artifacts/lumbro-20260718/`

**Consumes:** Real audited build from the ignored five-file cache and the existing local app at `http://127.0.0.1:5173/`.

**Produces:** Recoverable local preview plus desktop/mobile screenshots and a written verification matrix; no remote writes.

- [ ] Establish the documentation red state before visual validation:

```powershell
if (Test-Path 'docs/superpowers/reports/2026-07-18-lumbro-local-verification.md') { exit 0 } else { Write-Error 'Lumbro local verification report is missing' }
```

Expected failure: PowerShell exits non-zero with `Lumbro local verification report is missing`.

- [ ] Locate the exact dev-store path through existing test/dev configuration. If it exists, make a timestamped byte-for-byte backup beside the artifacts before replacing local preview data; never delete the original.
- [ ] Seed only the local `lumbro` snapshot/assets using the same repository contract used in tests. Record source hash and item/variant/asset/coverage counts.
- [ ] Start the documented local API/web commands, then use the in-app browser/Playwright to verify:

  - sidebar opens Lumbro;
  - representative image is sharp and not broken;
  - code, description, dimensions and net price are visible;
  - fallback link visibly says `Ver catálogo Lumbro` and opens the official host;
  - quantity rejects decimals and accepts positive integers;
  - cart total and IVA are correct;
  - no console errors or horizontal overflow at desktop and mobile widths.

- [ ] Capture screenshots and browser-console evidence under `.superpowers/sdd/artifacts/lumbro-20260718/`; do not commit large/generated assets.
- [ ] Write the report with exact commands, counts, pass/fail results, artifact paths, backup path and the statement `Producción no modificada`.
- [ ] Re-run the `Test-Path` command above and confirm exit code `0`.
- [ ] Commit only the report:

```powershell
git add -- docs/superpowers/reports/2026-07-18-lumbro-local-verification.md
git diff --cached --check
git diff --cached
git commit -m "docs: verificar catalogo Lumbro en local"
```

## Task 13: Run security, parity, build and full regression gates

**Files:**

- Modify only if a gate finds a real defect: files already listed in Tasks 1–12
- Update: `docs/superpowers/reports/2026-07-18-lumbro-local-verification.md`

**Consumes:** Completed implementation and existing test/build tooling.

**Produces:** Final evidence that unsafe sources/URLs/formulas are rejected, generated copies match and all relevant suites/builds pass.

- [ ] Add the report gate field `Regresión integral: PENDIENTE`, then run the assertion below to establish the red state:

```powershell
if (Select-String -Quiet -LiteralPath 'docs/superpowers/reports/2026-07-18-lumbro-local-verification.md' -SimpleMatch 'Regresión integral: PASS') { exit 0 } else { Write-Error 'Full Lumbro regression is not recorded as PASS' }
```

Expected failure: PowerShell exits non-zero with `Full Lumbro regression is not recorded as PASS`.

- [ ] Run source/link safety and migration tests:

```powershell
python -m pytest tests/test_catalog_source_safety.py tests/test_lumbro_links.py tests/test_catalog_migrations.py -q
```

- [ ] Run all catalog/importer/repository/API/worker/UI/automatic-Lumbro tests:

```powershell
python -m pytest tests/test_catalog_source_config.py tests/test_catalog_importers_lumbro.py tests/test_lumbro_catalog_audit.py tests/test_catalog_sync_service.py tests/test_catalog_repository.py tests/test_supplier_catalog.py tests/test_supplier_catalog_ui.py tests/test_quote_jobs_api.py tests/test_quote_worker.py tests/test_lumbro_catalog_e2e.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_capacity.py -q
```

- [ ] Run the full Python suite:

```powershell
python -m pytest -q
```

- [ ] Verify API/domain copy parity and syntax:

```powershell
python -m compileall -q mobiliti_saas vercel_deploy scripts
python -c "from pathlib import Path; import hashlib; groups=[['mobiliti_saas/api/index.py','mobiliti_saas/web/api/index.py','vercel_deploy/api/index.py'],['mobiliti_saas/quote_engine/supplier_catalog.py','mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py']]; assert all(len({hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in g}) == 1 for g in groups)"
```

- [ ] Build the web app using the repository's pinned package manager command (inspect `mobiliti_saas/web/package.json`; use the existing lockfile, do not update dependencies). Expected result: exit code `0` and no TypeScript/Vite build errors.
- [ ] If a gate fails, add the smallest regression test, make the smallest fix and rerun the focused gate before rerunning all gates.
- [ ] Append exact outputs/counts to the local verification report. Then commit only the report and any reviewed fix hunks:
- [ ] Replace the pending gate field with `Regresión integral: PASS` only after every command above passes; re-run the assertion and confirm exit code `0`.

```powershell
git add -p -- docs/superpowers/reports/2026-07-18-lumbro-local-verification.md
git diff --cached --check
git diff --cached
git commit -m "test: cerrar regresion del catalogo Lumbro"
```

## Task 14: Update Obsidian and prepare the no-production handoff

**Files:**

- MCP note: `armado-caratula/Catalogos-Proveedores/13-Lumbro-2026-plan-aprobado.md`
- MCP note: `armado-caratula/Catalogos-Proveedores/07-Registro-de-cambios.md`
- Update: `docs/superpowers/reports/2026-07-18-lumbro-local-verification.md`

**Consumes:** Final commit IDs, coverage JSON, verification report, browser artifacts and exact source contract.

**Produces:** Obsidian record of implementation/verification state and a final handoff that distinguishes local completion from production deployment.

- [ ] Add `Obsidian final verificado: no` to the local report and establish the red state:

```powershell
if (Select-String -Quiet -LiteralPath 'docs/superpowers/reports/2026-07-18-lumbro-local-verification.md' -SimpleMatch 'Obsidian final verificado: sí') { exit 0 } else { Write-Error 'Final Obsidian verification is pending' }
```

Expected failure: PowerShell exits non-zero with `Final Obsidian verification is pending`.

- [ ] Re-read both Obsidian notes via MCP immediately before updating them; never overwrite newer human edits.
- [ ] Update the Lumbro note from `plan_aprobado_pendiente_implementacion` to the truthful terminal state, adding source hashes, snapshot/coverage counts, test commands/results, local URLs/artifacts, commit IDs and remaining `needs_review` rows.
- [ ] Append a dated, concise entry to `07-Registro-de-cambios.md` using wikilinks to the Lumbro note. Explicitly state whether SQL, snapshots, Storage, Vercel and Supabase remain untouched.
- [ ] Re-read the written notes and verify the statements match Git/test/browser evidence.
- [ ] Add the Obsidian note paths/status to the local verification report, replace the marker with `Obsidian final verificado: sí`, re-run the assertion and confirm exit code `0`, then commit that final report amendment:

```powershell
git add -p -- docs/superpowers/reports/2026-07-18-lumbro-local-verification.md
git diff --cached --check
git diff --cached
git commit -m "docs: cerrar bitacora Lumbro"
```

- [ ] Final handoff must report: exact local result, coverage/exclusions, tests/build/browser status, changed commits, backup/recovery status, production untouched and the separate authorization needed for migration/sync/deployment.

## Definition of Done

- [ ] Every valid commercial Lumbro row is imported/reconciled or appears with a concrete exclusion reason.
- [ ] Prices are the published net values; no 10 % is subtracted again; IVA is separate.
- [ ] The active interconnection sheet is exactly `2026`, and price provenance includes sheet/cell.
- [ ] Spec/catalog price columns never override commercial sources.
- [ ] Each displayed code, dimension, image and product link has explicit provenance; ambiguous identities are not quoteable.
- [ ] PZA quantity is a positive integer across UI, API, cart, worker and XLSX.
- [ ] Automatic Lumbro electrification tests remain green and its production mapping is unchanged.
- [ ] All three API copies and both supplier-domain copies are byte-identical.
- [ ] Local desktop/mobile preview has no broken images, console errors or horizontal overflow.
- [ ] Obsidian reflects the actual state; no migration, remote snapshot, Storage upload or production deploy occurred without separate authorization.
