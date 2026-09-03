# Offiho Catalog, Cart, and Quotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `Offiho` ecommerce-style catalog below Tarkett that indexes official inventory, prices, links, and images; permits quoting exhausted products with warnings; and generates Mobiliti XLSX quotations asynchronously.

**Architecture:** Build the external catalog offline into a versioned JSON file, then serve it through an authenticated API with process caching and reservations. A small shared catalog-cart workbook adapter converts Tarkett and Offiho JSON jobs to the existing `Quotation` contract, while the current Mobiliti engine remains the only final workbook generator.

**Tech Stack:** Python 3, FastAPI, Supabase PostgreSQL/REST, Cloudflare R2, openpyxl, xlrd, pypdf, React/Vite, lucide-react, pytest, Vercel, Docker worker on Hetzner.

## Global Constraints

- Work only in `C:\Users\pepem\Downloads\ARMADO_DE_CARATULA_prod_git_worktree` on branch `codex/offiho-catalog-20260709`.
- Preserve all unrelated local changes; stage only files named by the active task.
- Inventory source of truth is `https://www.offiho.com/existencias.xls`.
- Include products with positive or zero stock; zero stock and over-stock quantities remain quotable.
- Use `Precio Lista 1` first, exact PDF code/variant price second, and never invent missing data.
- Scraping, PDF parsing, and catalog generation run offline, never in Vercel request handlers.
- API catalog cache uses absolute path plus mtime; frontend cache uses `source_hash` in `sessionStorage`.
- Quote jobs store small JSON inputs in the configured provider and are processed by the external worker.
- Preserve Tarkett behavior and run its regression tests before deployment.
- Do not write tokens, cookies, signed URLs, service keys, or environment values to code, JSON catalogs, tests, Git, or Obsidian.

---

### Task 1: Offline inventory, price, and product index

**Files:**
- Create: `scripts/requirements-offiho.txt`
- Create: `scripts/build_offiho_catalog.py`
- Create: `tests/test_offiho_catalog.py`
- Generate: `mobiliti_saas/quote_engine/data/offiho_catalog.json`

**Interfaces:**
- Produces: `parse_inventory_xls(path: Path) -> list[dict[str, Any]]`
- Produces: `extract_offiho_identity(inventory_key: str) -> OffihoIdentity`
- Produces: `parse_pdf_price_index(paths: Sequence[Path]) -> dict[str, Decimal]`
- Produces: `build_site_product_index(cache: dict[str, Any]) -> dict[str, dict[str, str]]`
- Produces: `build_catalog(inventory_path: Path, pdf_paths: Sequence[Path], cache_path: Path, output_path: Path) -> dict[str, Any]`
- Produces JSON items keyed by `inventory_key` for Task 2.

- [ ] **Step 1: Add the offline-only dependencies**

```text
xlrd>=2.0.1
pypdf>=6.0.0
```

- [ ] **Step 2: Write failing inventory and normalization tests**

```python
def test_parse_inventory_keeps_available_and_exhausted_rows(tmp_path):
    rows = parse_inventory_xls(FIXTURES / "offiho-small.xls")
    assert rows[0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"
    assert rows[0]["available_quantity"] == Decimal("252")
    assert rows[0]["unit_price"] == Decimal("7999")
    assert any(row["available_quantity"] == 0 for row in rows)


def test_extract_identity_separates_model_name_and_variant():
    identity = extract_offiho_identity("OHE-405 NEGRO ALUFSEN")
    assert identity.code == "OHE-405"
    assert identity.name == "ALUFSEN"
    assert identity.variant == "NEGRO"


def test_parse_inventory_removes_materially_identical_duplicate_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: duplicate_workbook())
    rows = parse_inventory_xls(tmp_path / "offiho-duplicates.xls")
    assert len(rows) == 2
```

- [ ] **Step 3: Run the focused tests and verify the expected failure**

Run: `python -m pytest tests/test_offiho_catalog.py -q`

Expected: collection/import failure because `scripts.build_offiho_catalog` does not exist.

- [ ] **Step 4: Implement deterministic `.xls` parsing and identity extraction**

```python
@dataclass(frozen=True)
class OffihoIdentity:
    code: str
    name: str
    variant: str


def parse_inventory_xls(path: Path) -> list[dict[str, Any]]:
    workbook = xlrd.open_workbook(path)
    sheet = workbook.sheet_by_name("Publicación")
    items = []
    for row in range(5, sheet.nrows):
        inventory_key = normalize_space(sheet.cell_value(row, 1)).upper()
        stock = decimal_value(sheet.cell_value(row, 2))
        if not inventory_key or stock is None:
            continue
        identity = extract_offiho_identity(inventory_key)
        items.append({
            "inventory_key": inventory_key,
            "code": identity.code,
            "name": identity.name,
            "variant": identity.variant,
            "unit": "PZA",
            "pieces_per_box": json_number(decimal_value(sheet.cell_value(row, 3)) or Decimal("1")),
            "available_quantity": json_number(stock),
            "unit_price": json_number(decimal_value(sheet.cell_value(row, 4)) or Decimal("0")),
            "price_source": "inventory" if decimal_value(sheet.cell_value(row, 4)) else "missing",
        })
    return items
```

- [ ] **Step 5: Write failing exact-PDF-price and official-page matching tests**

```python
def test_pdf_price_index_normalizes_compact_variant(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "extract_pdf_pages", lambda paths: ["ALUFSEN OHE-405 negro $ 7,999"])
    prices = parse_pdf_price_index([tmp_path / "prices.pdf"])
    assert prices["OHE-405 NEGRO"] == Decimal("7999")


def test_site_match_requires_expected_model_code():
    product = match_official_product(
        OffihoIdentity("OHE-405", "ALUFSEN", "NEGRO"),
        [{"codes": ["OHE-405"], "url": "https://www.offiho.com/directivos/alufsen", "image_url": "https://www.offiho.com/alufsen.jpg"}],
    )
    assert product["url"].endswith("/directivos/alufsen")
```

- [ ] **Step 6: Implement PDF and website indexing with cache**

Implement `extract_pdf_pages` with `pypdf.PdfReader`, strict code/variant regexes, an official-host allowlist, category discovery from links on `offiho.com`, `offiho.com/econosillas`, and `offihoblack.com`, and a `.cache/offiho-products.json` cache containing source timestamps and resolved code lists. Website matches must require the expected code in page text or metadata. Deduplicate only materially identical normalized inventory keys and fail explicitly when any substantive fields differ.

- [ ] **Step 7: Generate and validate the real catalog**

Run:

```powershell
python -m pip install -r scripts\requirements-offiho.txt
python scripts\build_offiho_catalog.py `
  --inventory-url https://www.offiho.com/existencias.xls `
  --pdf "LP OFFIHO®️ ECONO SILLAS®️ JUL2026.pdf" `
  --pdf "LP BLACK®️ & COLOS®️ JUL2026.pdf" `
  --output mobiliti_saas\quote_engine\data\offiho_catalog.json
```

Expected: `total=1206`, `source_row_count=1286`, `duplicate_row_count=80`, `unique_item_count=1206`, `out_of_stock=189`, `inventory_prices=778`, no duplicate `inventory_key`, and a printed coverage summary for PDF prices and official images.

- [ ] **Step 8: Run tests and commit only the indexer deliverable**

Run: `python -m pytest tests/test_offiho_catalog.py -q`

Expected: PASS.

Commit:

```powershell
git add scripts/requirements-offiho.txt scripts/build_offiho_catalog.py tests/test_offiho_catalog.py mobiliti_saas/quote_engine/data/offiho_catalog.json
git commit -m "Add indexed Offiho product catalog"
```

---

### Task 2: Runtime catalog validation and shared Quotation adapter

**Files:**
- Create: `mobiliti_saas/quote_engine/catalog_cart.py`
- Create: `mobiliti_saas/quote_engine/offiho_catalog.py`
- Modify: `mobiliti_saas/quote_engine/tarkett_catalog.py`
- Test: `tests/test_offiho_catalog.py`
- Test: `tests/test_tarkett_catalog.py`

**Interfaces:**
- Produces: `OffihoCatalogItem.from_dict(raw) -> OffihoCatalogItem`
- Produces: `load_offiho_catalog(path=None) -> dict[str, Any]`
- Produces: `build_offiho_cart_payload(raw_items, catalog=None) -> dict[str, Any]`
- Produces: `stock_status(quantity: Decimal, available: Decimal) -> str`
- Produces: `create_catalog_quotation_workbook(payload, output_path, *, source_type, category_label, image_dir=None) -> Path`
- Produces: `create_offiho_quotation_workbook(payload, output_path, image_dir=None) -> Path`

- [ ] **Step 1: Write failing cart-status tests**

```python
def fake_catalog(*, available_quantity: int, unit_price: int):
    item = OffihoCatalogItem(
        inventory_key="OHE-405 NEGRO ALUFSEN",
        code="OHE-405",
        name="ALUFSEN",
        variant="NEGRO",
        unit="PZA",
        pieces_per_box=Decimal("1"),
        available_quantity=Decimal(str(available_quantity)),
        unit_price=Decimal(str(unit_price)),
    )
    return {"source_hash": "hash", "items": [item], "by_inventory_key": {item.inventory_key: item}}


def test_offiho_cart_accepts_exhausted_and_overstock_lines():
    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 3}],
        catalog=fake_catalog(available_quantity=0, unit_price=7999),
    )
    assert payload["items"][0]["stock_status"] == "out_of_stock"
    assert payload["items"][0]["unit_price"] == 7999


def test_offiho_cart_rejects_non_positive_quantity():
    with pytest.raises(ValueError, match="Cantidad invalida"):
        build_offiho_cart_payload(
            [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 0}],
            catalog=fake_catalog(available_quantity=252, unit_price=7999),
        )
```

- [ ] **Step 2: Implement the Offiho dataclass, loader, and cart payload**

The payload must copy catalog-owned price, stock, URL, and image fields; client payloads may supply only `inventory_key` and `quantity`. Use `out_of_stock` for zero stock, `insufficient_stock` for quantity above positive stock, and `available` otherwise.

- [ ] **Step 3: Write failing workbook-warning tests**

```python
def test_offiho_workbook_writes_price_and_warning(tmp_path):
    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 1}],
        catalog=fake_catalog(available_quantity=0, unit_price=7999),
    )
    output = create_offiho_quotation_workbook(payload, tmp_path / "offiho.xlsx")
    wb = load_workbook(output)
    ws = wb["Quotation"]
    assert ws["J9"].value == 7999
    assert "ADVERTENCIA: PRODUCTO AGOTADO" in ws["D9"].value
    assert ws["D9"].fill.fgColor.rgb.endswith("FFF2CC")
```

- [ ] **Step 4: Implement the shared catalog workbook adapter**

The adapter writes the current headers, category row, image, quantity, price, URL, and warning description. It must validate HTTPS images, cap downloads at 8 MiB, require `image/*`, and use an official supplier user-agent. `create_tarkett_quotation_workbook` delegates to this adapter with `source_type="tarkett_cart"` and preserves price `0` and existing output.

- [ ] **Step 5: Verify final engine warning propagation**

Add a test that passes the Offiho temporary workbook to `generate_online_quote`, opens the final workbook, and asserts a `Cotizacion` description cell contains `ADVERTENCIA: PRODUCTO AGOTADO` while sheets `Cotizacion`, `Mobiliti`, and `Quotation` exist.

- [ ] **Step 6: Run focused regressions and commit**

Run:

```powershell
python -m pytest tests/test_offiho_catalog.py tests/test_tarkett_catalog.py tests/test_quote_worker.py -q
```

Expected: PASS.

Commit:

```powershell
git add mobiliti_saas/quote_engine/catalog_cart.py mobiliti_saas/quote_engine/offiho_catalog.py mobiliti_saas/quote_engine/tarkett_catalog.py tests/test_offiho_catalog.py tests/test_tarkett_catalog.py
git commit -m "Add Offiho cart workbook conversion"
```

---

### Task 3: Reservations, API cache, and quote endpoints

**Files:**
- Create: `mobiliti_saas/supabase_setup/2026_07_offiho_reservations.sql`
- Modify: `mobiliti_saas/supabase_setup/create_tables.sql`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Test: `tests/test_quote_jobs_api.py`

**Interfaces:**
- Produces: `GET /offiho/catalog`
- Produces: `POST /offiho/quote`
- Produces: `db_list_offiho_reservations(status="active")`
- Produces: `db_create_offiho_reservations(usuario_id, quote_job_id, lines)`
- Produces: `db_release_offiho_reservations(quote_job_id)`

- [ ] **Step 1: Write failing API tests**

```python
def _mock_offiho_catalog():
    item = OffihoCatalogItem(
        inventory_key="OHE-405 NEGRO ALUFSEN",
        code="OHE-405",
        name="ALUFSEN",
        variant="NEGRO",
        unit="PZA",
        pieces_per_box=Decimal("1"),
        available_quantity=Decimal("0"),
        unit_price=Decimal("7999"),
    )
    return {"source_hash": "hash", "generated_at": "2026-07-09T18:00:00Z", "items": [item], "by_inventory_key": {item.inventory_key: item}}


def _valid_offiho_body(quantity=1):
    return {
        "proyecto": "Proyecto Offiho",
        "cliente": "Cliente",
        "correo": "cliente@example.com",
        "telefono": "5551234567",
        "direccion": "Guadalajara",
        "razon_social": "Cliente SA de CV",
        "descuento": 40,
        "items": [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": quantity}],
    }


uploaded = {}


def test_offiho_catalog_returns_exhausted_items_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", _mock_offiho_catalog)
    monkeypatch.setattr(index, "db_list_offiho_reservations", lambda status="active": [
        {"usuario_id": 8, "product_code": "OHE-405 NEGRO ALUFSEN", "quantity": 2, "status": "active"}
    ])
    response = _client().get("/offiho/catalog", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json()["items"][0]["is_out_of_stock"] is True
    assert response.json()["items"][0]["reserved_by_others"] is True


def test_offiho_quote_accepts_exhausted_item(monkeypatch):
    _mock_user(monkeypatch)
    response = _client().post("/offiho/quote", headers=_auth_headers(), json=_valid_offiho_body(quantity=1))
    assert response.status_code == 200
    payload = json.loads(uploaded["content"].decode("utf-8"))
    assert payload["source_type"] == "offiho_cart"
    assert payload["items"][0]["stock_status"] == "out_of_stock"
```

- [ ] **Step 2: Add the database migration**

Create `saas_offiho_reservations` with UUID/text id, bigint user id, UUID/text quote job id compatible with the current schema, text `product_code`, numeric quantity, status, timestamps, foreign keys matching Tarkett, and indexes for `(product_code, status)`, `usuario_id`, and `quote_job_id`. Add the same idempotent DDL to `create_tables.sql`.

- [ ] **Step 3: Implement API imports, cache, reservation CRUD, and endpoints**

Add `OFFIHO_CATALOG_PATH`, `_OFFIHO_CATALOG_CACHE`, `_load_offiho_catalog_cached`, and `_offiho_catalog_response`. `POST /offiho/quote` mirrors the proven storage/job sequence but uses `source_type="offiho_cart"`, `original_filename="offiho-cart.json"`, catalog-owned prices, and Offiho reservations.

- [ ] **Step 4: Release Offiho reservations on delete and retention**

Call both `db_release_tarkett_reservations(job_id)` and `db_release_offiho_reservations(job_id)` in quote deletion and retention paths; each function is idempotent and affects only its table.

- [ ] **Step 5: Synchronize deployable API copies and verify equality**

After editing `mobiliti_saas/web/api/index.py`, copy it byte-for-byte to `mobiliti_saas/api/index.py` and `vercel_deploy/api/index.py`, then compare SHA-256 hashes.

- [ ] **Step 6: Run API tests and commit**

Run: `python -m pytest tests/test_quote_jobs_api.py -q`

Expected: PASS, including existing Tarkett cases.

Commit:

```powershell
git add mobiliti_saas/supabase_setup/2026_07_offiho_reservations.sql mobiliti_saas/supabase_setup/create_tables.sql mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py tests/test_quote_jobs_api.py
git commit -m "Add Offiho catalog quote API"
```

---

### Task 4: Worker dispatch for Offiho JSON jobs

**Files:**
- Modify: `mobiliti_saas/worker/quote_worker.py`
- Test: `tests/test_quote_worker.py`

**Interfaces:**
- Consumes: `source_type="offiho_cart"` JSON from Task 3.
- Consumes: `create_offiho_quotation_workbook` from Task 2.
- Produces: temporary `quotation_from_offiho.xlsx` passed to the existing generator.

- [ ] **Step 1: Write a failing worker dispatch test**

```python
def test_process_job_converts_offiho_json_before_generator(monkeypatch):
    seen = {}
    monkeypatch.setattr(quote_worker, "_convert_offiho_cart_to_quotation", fake_offiho_convert(seen))
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator(seen))
    quote_worker.process_job(fake_job(source_type="offiho_cart"), client=FakeClient())
    assert seen["converted_input"] == "input.json"
    assert seen["generator_input"] == "quotation_from_offiho.xlsx"
```

- [ ] **Step 2: Implement source-type dispatch**

Read the JSON once in `_prepare_generator_input`, dispatch Tarkett to `create_tarkett_quotation_workbook`, Offiho to `create_offiho_quotation_workbook`, and reject unknown JSON source types with a precise runtime error. Preserve `input_storage_provider` fallback behavior.

- [ ] **Step 3: Run worker regressions and commit**

Run: `python -m pytest tests/test_quote_worker.py tests/test_tarkett_catalog.py tests/test_offiho_catalog.py -q`

Expected: PASS.

Commit:

```powershell
git add mobiliti_saas/worker/quote_worker.py tests/test_quote_worker.py
git commit -m "Process Offiho cart jobs in worker"
```

---

### Task 5: Offiho sidebar tab, catalog, cart, and warnings

**Files:**
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`
- Modify: `mobiliti_saas/web/vercel.json`
- Test: `tests/test_web_ui_defaults.py`

**Interfaces:**
- Consumes: `GET /offiho/catalog` and `POST /offiho/quote`.
- Produces: sidebar view id `offiho` immediately after `tarkett`.
- Produces: cache key `mobiliti_offiho_catalog`.

- [ ] **Step 1: Add failing UI contract tests**

```python
def test_offiho_tab_catalog_cart_and_warning_contracts_are_present():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert '["offiho", "Offiho", Armchair]' in source
    assert "function OffihoView" in source
    assert 'request("/offiho/catalog")' in source
    assert 'request("/offiho/quote"' in source
    assert 'const OFFIHO_CATALOG_CACHE_KEY = "mobiliti_offiho_catalog";' in source
    assert "Agotado" in source
    assert "Stock insuficiente" in source
    assert "window.confirm" in source
```

- [ ] **Step 2: Implement the Offiho view in the requested sidebar position**

Add the `Armchair` lucide icon and `Offiho` nav entry directly below Tarkett. Build `OffihoView` with search, brand/category and availability filters, lazy official images, model/name/variant, price, stock, pieces per box, URL, reservation badge, quantity control, and add/remove action.

- [ ] **Step 3: Implement cart totals and non-blocking stock warnings**

Use `inventory_key` as the cart key. Show `Agotado` when stock is zero and `Stock insuficiente` when requested quantity exceeds positive stock. Keep controls enabled. Before POST, use one confirmation listing the number of warned lines; cancellation leaves the cart intact.

- [ ] **Step 4: Add focused CSS without changing Mobiliti's visual system**

Reuse the current catalog grid/cart dimensions and add only Offiho warning, price, stock, and filter modifiers. Use existing CSS variables, 8px-or-smaller radii, visible focus, no gradients, no nested cards, and one-column mobile layout.

- [ ] **Step 5: Add the Vercel rewrite and run build/tests**

Add `/offiho/:path*` to `mobiliti_saas/web/vercel.json`.

Run:

```powershell
python -m pytest tests/test_web_ui_defaults.py -q
Set-Location mobiliti_saas\web
npm.cmd run build
```

Expected: tests PASS and Vite build completes.

- [ ] **Step 6: Commit the frontend deliverable**

```powershell
git add mobiliti_saas/web/src/main.jsx mobiliti_saas/web/src/styles.css mobiliti_saas/web/vercel.json tests/test_web_ui_defaults.py
git commit -m "Add Offiho catalog and cart tab"
```

---

### Task 6: Package runtime data and verify locally

**Files:**
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py`
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/offiho_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/tarkett_catalog.py`
- Generate: `mobiliti_saas/web/mobiliti_saas/quote_engine/data/offiho_catalog.json`
- Test: `tests/test_quote_jobs_api.py`
- Test: `tests/test_web_ui_defaults.py`

**Interfaces:**
- Produces the package layout required by the Vercel web root.

- [ ] **Step 1: Copy runtime modules and catalog into the web package**

Copy the three quote-engine modules and Offiho JSON byte-for-byte from `mobiliti_saas/quote_engine`. Do not copy caches or source PDFs.

- [ ] **Step 2: Verify packaged equality and catalog load**

Compare SHA-256 hashes and run a Python import from the `mobiliti_saas/web` working directory. Expected visible catalog total is 1,206 with source audit metadata `1286/80/1206`, and both available and exhausted items load.

- [ ] **Step 3: Run the full focused suite**

```powershell
python -m py_compile scripts\build_offiho_catalog.py mobiliti_saas\quote_engine\catalog_cart.py mobiliti_saas\quote_engine\offiho_catalog.py mobiliti_saas\worker\quote_worker.py mobiliti_saas\web\api\index.py mobiliti_saas\api\index.py vercel_deploy\api\index.py
python -m pytest tests\test_offiho_catalog.py tests\test_tarkett_catalog.py tests\test_quote_jobs_api.py tests\test_quote_worker.py tests\test_web_ui_defaults.py -q
```

Expected: PASS.

- [ ] **Step 4: Run local E2E for available and exhausted products**

Start the local FastAPI dev backend, worker, and Vite app. Create one Offiho quote containing an available item and one containing an exhausted item. Verify both jobs reach `completed`; downloaded workbooks contain `Cotizacion`, `Mobiliti`, and `Quotation`; and the exhausted workbook contains the warning in both `Quotation` and `Cotizacion`.

- [ ] **Step 5: Commit packaged runtime files**

```powershell
git add mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py mobiliti_saas/web/mobiliti_saas/quote_engine/offiho_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/tarkett_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/data/offiho_catalog.json
git commit -m "Package Offiho catalog for Vercel"
```

---

### Task 7: Security, production rollout, and project record

**Files:**
- Modify: `armado-caratula/32-Cambio-catalogo-offiho-carrito-cotizacion.md` through Obsidian MCP only.

**Interfaces:**
- Consumes all local deliverables.
- Produces a Supabase migration, Vercel deployment, Hetzner worker deployment, and production E2E evidence.

- [ ] **Step 1: Run final repository checks**

Run:

```powershell
git diff --check
python -m pytest tests\test_offiho_catalog.py tests\test_tarkett_catalog.py tests\test_quote_jobs_api.py tests\test_quote_worker.py tests\test_web_ui_defaults.py tests\test_quote_engine_image_layout.py -q
Set-Location mobiliti_saas\web
npm.cmd run build
```

Scan added lines for token, password, JWT, private key, signed URL, and service-key patterns. Expected: no real secrets.

- [ ] **Step 2: Apply the Supabase migration**

Apply `2026_07_offiho_reservations.sql` to project `hcdspekajlszcycecpml`, then query `information_schema.tables` and indexes to confirm the table and three indexes exist.

- [ ] **Step 3: Deploy web/API to Vercel production**

Deploy from `mobiliti_saas/web`, confirm `Ready`, assign the stable alias `https://web-lemon-one-45.vercel.app`, and verify `/health` reports `storage_provider=r2` and `storage_configured=true`.

- [ ] **Step 4: Deploy the worker**

Push the current branch, deploy it to `/opt/mobiliti-worker/app` on Hetzner through the existing deployment command, rebuild/restart `mobiliti-worker`, and verify the container is healthy at the deployed commit.

- [ ] **Step 5: Execute production smoke tests**

Authenticate without logging credentials or tokens. Verify `GET /offiho/catalog` returns 1,206 unique visible products and preserves source audit metadata `1286/80/1206`, create an available and an exhausted quote, observe `queued -> processing -> completed`, download both XLSX files, verify required sheets and warning text, then create one Tarkett smoke quote to confirm regression safety.

- [ ] **Step 6: Update Obsidian and final status**

Append implementation files, catalog coverage, test counts, migration name, branch/commit, deployment result, job ids, and production verification to `armado-caratula/32-Cambio-catalogo-offiho-carrito-cotizacion.md`. Set `status=produccion_validada` and `production_verified=true`. Omit credentials, tokens, environment values, and signed URLs.

- [ ] **Step 7: Finalize the branch**

Commit any remaining scoped documentation/test evidence, push `codex/offiho-catalog-20260709`, and report exact verification results and any unmatched product coverage without claiming unsupported completeness.
