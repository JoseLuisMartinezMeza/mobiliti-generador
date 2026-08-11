# IDÉLIKA and Conceptos Local Catalogs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crear primero un SPEC GUIDE 2026 auditable de IDÉLIKA y, a partir de ese artefacto y del libro oficial de Conceptos, integrar ambos proveedores al catálogo local para búsqueda, configuración, Proyecto y cotización mixta sin alterar los catálogos existentes.

**Architecture:** IDÉLIKA usa un pipeline local de dos etapas: los tres PDF oficiales se convierten en filas de evidencia normalizadas, se materializan y validan en un único SPEC GUIDE con `@oai/artifact-tool`, y solamente después ese SPEC se transforma al snapshot canónico. Conceptos se importa directamente desde sus hojas oficiales, vinculando por código y bloque las columnas de costo y referencia. Ambos adaptadores terminan en el contrato existente de `CatalogSnapshotBuild`; el registro, búsqueda, Proyecto, cotización mixta y selector web se amplían mediante sus puntos genéricos actuales.

**Tech Stack:** Python 3.14, pytest, pypdf, Node.js, `@oai/artifact-tool`, React/Vite, SQL de Supabase ejecutado solo contra una base local de prueba.

## Global Constraints

- [ ] Trabajar exclusivamente en local. No escribir en SharePoint, Supabase remoto, R2, Vercel, Hetzner ni producción.
- [ ] No ejecutar `git add`, `git commit`, `git push`, despliegues ni migraciones remotas durante este plan.
- [ ] Conservar todos los cambios preexistentes del worktree, incluidos los de `mobiliti_saas/web/src/SupplierCatalogView.jsx`, `tests/test_supplier_catalog_ui.py`, `.superpowers/`, plantillas y artefactos históricos. Antes de editar un archivo ya modificado, revisar `git diff -- <archivo>` y hacer una modificación mínima que no reescriba trabajo ajeno.
- [ ] No inventar SKU oficiales. Cuando la fuente no lo publique, usar una identidad técnica estable, conservar `SKU` vacío y mostrar `Código por verificar`.
- [ ] IDÉLIKA: moneda de origen MXN; de un par de precios, el menor es costo y el mayor es referencia. School Series sin precio queda `Precio por confirmar`, pero cotizable.
- [ ] Conceptos: moneda de origen MXN; costo desde columna E de `Costo Sofas - Cdmx-Gdl-Qro`; columna G es referencia, nunca costo.
- [ ] La creación del SPEC GUIDE final de IDÉLIKA debe usar `@oai/artifact-tool`; `openpyxl` no se usará para escribir ese archivo.
- [ ] No cambiar fórmulas, formatos, reglas de precio ni comportamiento de CR Global, Sonara, Sunon, ALMA, Lumbro, JOME, Lauco, Offiho o Tarkett.
- [ ] Cada tarea termina en un checkpoint local sin commit: pruebas específicas, `git diff --check` y revisión de `git status --short`.

---

## Task 1: Freeze official inputs and executable source contracts

**Files:**
- Modify: `mobiliti_saas/worker/catalog_sync/sources.json`
- Modify: `tests/test_catalog_source_config.py`
- Modify: `tests/test_catalog_source_safety.py`
- Create: `tests/test_idelika_source_contract.py`
- Create: `tests/test_conceptos_source_contract.py`

- [x] **Step 1: Add failing configuration tests**

  Assert that the source registry contains exactly these new documents and no `TEQUILA LOVE.pdf` entry:

  - `idelika/fabricacion` → Graph item `01DHXXN7YJMCJUVPBWNJEJPJIH7B4OTAUR`, PDF.
  - `idelika/stock` → Graph item `01DHXXN7YASXKBZPOLSBHIX2N2T3PB4G2R`, PDF.
  - `idelika/school-series` → Graph item `01DHXXN7YTQLPUZXRUN5E3J62UE2JQUWNC`, PDF.
  - `conceptos/sofas` → Graph item `01DHXXN76XWGQOWSKX2RDL5YG6GTS355BO`, XLSX.

  Update the expected supplier set from seven to nine and the expected configured document count from 21 to 25.

- [x] **Step 2: Run the focused tests and confirm the red state**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_catalog_source_config.py tests/test_catalog_source_safety.py tests/test_idelika_source_contract.py tests/test_conceptos_source_contract.py -q
  ```

  Expected: failures because `idelika` and `conceptos` are absent from `sources.json`.

- [x] **Step 3: Add the four source entries**

  Use the same drive ID already used by the existing supplier sources. Set explicit supplier, logical kind, extension, MIME type and expected filename. Keep the three IDÉLIKA PDFs as official traceability inputs; the generated SPEC is a derived local artifact, not a new SharePoint source.

- [x] **Step 4: Re-run the focused tests**

  Expected: all four test files pass and `TEQUILA LOVE.pdf` remains excluded.

- [x] **Step 5: Local checkpoint without commit**

  ```powershell
  git diff --check -- mobiliti_saas/worker/catalog_sync/sources.json tests/test_catalog_source_config.py tests/test_catalog_source_safety.py tests/test_idelika_source_contract.py tests/test_conceptos_source_contract.py
  git status --short
  ```

---

## Task 2: Parse IDÉLIKA PDFs into auditable evidence rows

**Files:**
- Create: `mobiliti_saas/worker/catalog_sync/importers/idelika.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Create: `tests/test_idelika_catalog_importer.py`

- [ ] **Step 1: Write failing parser tests with synthetic PDF-page text fixtures**

  Cover:

  - Fabricación and Stock rows with paired prices: `Costo_MXN = min(precios)` and `Precio_referencia_MXN = max(precios)`.
  - School Series rows without price: `Estado_precio = precio_por_confirmar`, `Cotizable = true`.
  - Published code retained as `SKU`; missing code remains blank and receives a deterministic `Clave_estable`/`Identidad_hash`.
  - Repeated page headers and footers ignored.
  - Variants grouped only when code, product block and source layout prove a shared identity.
  - Source filename and 1-based page number preserved on every evidence row.

- [ ] **Step 2: Run the tests and confirm the red state**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_idelika_catalog_importer.py -q
  ```

  Expected: import error because the IDÉLIKA adapter does not exist.

- [ ] **Step 3: Implement the narrow parser contract**

  Add immutable evidence rows and pure functions:

  ```python
  @dataclass(frozen=True)
  class IdelikaEvidenceRow:
      subcatalog: str
      source_file: str
      source_page: int
      stable_key: str
      sku: str | None
      product: str
      family: str | None
      variant: str | None
      material: str | None
      dimensions: str | None
      description: str
      unit: str
      cost_mxn: Decimal | None
      reference_price_mxn: Decimal | None
      original_price_text: str | None
      price_status: str
      quotable: bool
      minimum_order: Decimal | None
      source_url: str
      identity_hash: str
      notes: str | None

  def extract_idelika_rows(documents) -> tuple[IdelikaEvidenceRow, ...]: ...
  ```

  Read PDF text with `pypdf`, normalize whitespace without erasing source evidence, use `Decimal`, and reject ambiguous monetary rows instead of guessing. Esta tarea termina en filas de evidencia; el constructor del snapshot se incorpora solamente en Task 4, después de que el SPEC exista y haya sido validado.

- [ ] **Step 4: Run focused tests**

  Expected: parser tests pass, including deterministic identity across two runs.

- [ ] **Step 5: Local checkpoint without commit**

  Run `git diff --check` for the three files and inspect `git status --short`.

---

## Task 3: Generate and validate the IDÉLIKA SPEC GUIDE 2026 workbook

**Files:**
- Create: `mobiliti_saas/worker/catalog_sync/tools/build_idelika_spec_guide.mjs`
- Create: `mobiliti_saas/worker/catalog_sync/tools/build_idelika_spec_guide.py`
- Create: `tests/test_idelika_spec_guide.py`
- Generate locally: `outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/Spec guide-IDELIKA-2026.xlsx`

- [ ] **Step 1: Write failing workbook-contract tests**

  Validate the generated workbook as an XLSX package and require sheets:

  - `Consolidado`
  - `Fabricacion`
  - `Stock`
  - `School Series`
  - `Fuentes_Reglas`

  Require the approved `Consolidado` columns in exact order, visible formulas for row counts/duplicate keys/price conflicts, filters, frozen header, MXN number formats and a non-empty source URL/page for every data row.

- [ ] **Step 2: Run the contract test and confirm the red state**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_idelika_spec_guide.py -q
  ```

  Expected: failure because the builder and workbook do not exist.

- [ ] **Step 3: Implement the Python-to-artifact-tool bridge**

  `build_idelika_spec_guide.py` must:

  1. call `extract_idelika_rows` on the three downloaded PDFs;
  2. serialize normalized evidence to a temporary JSON input;
  3. invoke the bundled Node runtime and `build_idelika_spec_guide.mjs`;
  4. return the requested output path only after package validation succeeds.

  The JavaScript builder must load:

  `file:///C:/Users/pepem/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs`

  and create the workbook through `@oai/artifact-tool`. Do not write it with `openpyxl`.

- [ ] **Step 4: Build the workbook from the real local PDFs**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' mobiliti_saas/worker/catalog_sync/tools/build_idelika_spec_guide.py --fabricacion 'outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/sources/1 CATALOGO FABRICACION 2026B.pdf' --stock 'outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/sources/2 CATALOGO STOCK 2026.pdf' --school 'outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/sources/4 SCHOOL SERIES 2026.pdf' --output 'outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/Spec guide-IDELIKA-2026.xlsx'
  ```

- [ ] **Step 5: Recalculate, inspect and render every sheet**

  Use the artifact-tool inspection/render APIs to:

  - confirm formulas have no `#REF!`, `#VALUE!`, `#N/A` or broken ranges;
  - render a representative range from each sheet;
  - visually inspect headers, clipping, currency formats, filters and freeze panes;
  - write a machine-readable validation summary beside the generated workbook.

- [ ] **Step 6: Run the workbook-contract tests**

  Expected: all tests pass and the output is deterministic for the same inputs.

- [ ] **Step 7: Local checkpoint without commit**

  Inspect the rendered evidence and run `git diff --check`. Do not stage the generated workbook.

---

## Task 4: Make the IDÉLIKA snapshot consume only the validated SPEC

**Files:**
- Modify: `mobiliti_saas/worker/catalog_sync/importers/idelika.py`
- Modify: `mobiliti_saas/worker/catalog_sync/tools/build_idelika_spec_guide.py`
- Modify: `tests/test_idelika_catalog_importer.py`
- Modify: `tests/test_idelika_spec_guide.py`

- [ ] **Step 1: Add failing two-stage pipeline tests**

  Assert that `build_idelika_snapshot_with_assets(documents)` first produces/validates the SPEC in a local build directory and then reads the validated normalized rows. Tampering with source page, cost, identity hash or validation summary must stop publication with a stable error code.

- [ ] **Step 2: Run the focused tests and confirm failure**

  Expected: failure because `build_idelika_snapshot_with_assets` and the validated-SPEC loader do not exist yet.

- [ ] **Step 3: Implement the two-stage builder**

  Add:

  ```python
  def build_idelika_spec_guide(rows, output_path: Path) -> Path: ...
  def load_validated_idelika_spec(path: Path) -> tuple[IdelikaEvidenceRow, ...]: ...
  def build_idelika_snapshot_with_assets(documents) -> CatalogSnapshotBuild: ...
  ```

  Map each row to the existing supplier-catalog contract with:

  - `supplier = "idelika"`
  - `source_currency = "MXN"`
  - cost as the only numeric quotation base
  - reference price retained in metadata only
  - `missing_code` warning when SKU is absent
  - `price_pending` warning for School Series without cost, while keeping `quotable = true`
  - source references including PDF filename, page and Graph URL.

- [ ] **Step 4: Run the focused tests**

  Expected: parser, SPEC and snapshot tests all pass.

- [ ] **Step 5: Local checkpoint without commit**

  Run `git diff --check` and inspect only the IDÉLIKA diff.

---

## Task 5: Import Conceptos with block-aware costs and variants

**Files:**
- Create: `mobiliti_saas/worker/catalog_sync/importers/conceptos.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Create: `tests/test_conceptos_catalog_importer.py`

- [ ] **Step 1: Write failing importer tests**

  Cover both official sheets with headers on row 8:

  - `Spec sofas - Cdmx-Gdl-Qro`: columns A:F.
  - `Costo Sofas - Cdmx-Gdl-Qro`: columns A:H.

  Assert that code and image may carry down only inside a proven merged/product block; description, unit and dimensions remain attached to the correct row; column E is the cost; column G is reference metadata; currency is always MXN; distinct measures/materials remain selectable variants; blank decorative rows are ignored.

- [ ] **Step 2: Run the test and confirm the red state**

  Expected: import error because the Conceptos adapter does not exist.

- [ ] **Step 3: Implement the importer**

  Add:

  ```python
  def parse_conceptos_rows(files) -> tuple[dict, ...]: ...
  def build_conceptos_snapshot(files) -> dict: ...
  def build_conceptos_snapshot_with_assets(files) -> CatalogSnapshotBuild: ...
  ```

  Reuse the OOXML safety and image helpers from `importers/common.py`. Join cost/spec rows by normalized published code plus proven row/block identity, never by description alone. Reject conflicting non-empty costs for the same exact variant rather than silently choosing one.

- [ ] **Step 4: Run focused tests**

  Expected: all Conceptos tests pass, including image binding and deterministic variant IDs.

- [ ] **Step 5: Local checkpoint without commit**

  Run `git diff --check` and inspect the importer diff.

---

## Task 6: Register both suppliers through sync, repository and local database contracts

**Files:**
- Modify: `mobiliti_saas/worker/catalog_sync/service.py`
- Modify: `mobiliti_saas/worker/catalog_sync/repository.py`
- Modify: `mobiliti_saas/worker/catalog_sync/importers/__init__.py`
- Create: `mobiliti_saas/supabase_setup/2026_08_idelika_conceptos_catalogs.sql`
- Modify: `mobiliti_saas/supabase_setup/create_tables.sql`
- Modify: `tests/test_catalog_sync_service.py`
- Modify: `tests/test_catalog_migrations.py`
- Modify: `tests/test_dev_start_configuration.py`
- Create: `tests/test_idelika_conceptos_registry.py`
- Create: `tests/test_idelika_conceptos_registration_integration.py`

- [ ] **Step 1: Add failing registry and migration tests**

  Require nine synchronized suppliers and eleven mixed catalogs total (Tarkett, Offiho plus the nine generic suppliers). The source-loader declarations `_ADAPTERS` and `_FIRST_WAVE_ALLOWLIST` were completed in Task 1; here require the executable `ADAPTERS` keys `idelika` and `conceptos`, correct source builders, local enabled-supplier strings, constraints and RPC allowlists.

- [ ] **Step 2: Run focused tests and confirm the red state**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_catalog_sync_service.py tests/test_catalog_migrations.py tests/test_dev_start_configuration.py tests/test_idelika_conceptos_registry.py tests/test_idelika_conceptos_registration_integration.py -q
  ```

  Expected: failures at all hardcoded seven-supplier allowlists.

- [ ] **Step 3: Extend registries minimally**

  Add `idelika` and `conceptos` to `_SUPPLIERS`, `_SYNC_SUPPLIERS` and executable `ADAPTERS`. Do not modify the source-loader declarations already closed in Task 1, do not reorder existing suppliers and append the new pair.

- [ ] **Step 4: Add a forward-only local migration**

  `2026_08_idelika_conceptos_catalogs.sql` must widen supplier checks for `saas_catalog_sources`, `saas_catalog_snapshot_versions`, `saas_catalog_reservations` and the catalog/mixed-catalog RPC allowlists. Mirror the current JOME/Lauco migration structure; do not rewrite or delete prior migrations.

- [ ] **Step 5: Update local bootstrap/dev configuration**

  Extend `create_tables.sql` and local `CATALOG_ENABLED_SUPPLIERS` defaults to include the pair. Apply the new migration only to the disposable/local test database used by the suite.

- [ ] **Step 6: Re-run focused tests**

  Expected: all registry, migration and dev-start tests pass.

- [ ] **Step 7: Local checkpoint without commit**

  Run `git diff --check`, inspect SQL diffs and verify no remote database command was executed.

---

## Task 7: Extend quote domain, mixed search and Project validation

**Files:**
- Modify: `mobiliti_saas/quote_engine/supplier_catalog.py`
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/quote_engine/engine.py`
- Modify: `mobiliti_saas/quote_engine/catalog_search.py`
- Modify: `mobiliti_saas/quote_engine/project_model.py`
- Modify: `tests/test_supplier_catalog.py`
- Modify: `tests/test_project_catalog_search.py`
- Modify: `tests/test_mixed_catalog_cart.py`
- Modify: `tests/test_mixed_catalog_quote_e2e.py`
- Create: `tests/test_idelika_conceptos_quote_integration.py`

- [ ] **Step 1: Add failing domain tests**

  Assert:

  - labels `IDÉLIKA` and `Conceptos`;
  - expected source currency MXN;
  - both appear after Lauco in generic/mixed order;
  - items can be searched, configured, added to a Proyecto and quoted in MXN or converted exactly once to USD;
  - IDÉLIKA `Precio por confirmar` rows remain quotable without fabricating a numeric price;
  - reference/list prices never replace cost in the quote payload.

- [ ] **Step 2: Run focused tests and confirm the red state**

  Expected: supplier validation rejects both names.

- [ ] **Step 3: Extend existing constants only**

  Append both suppliers to `ALLOWED_SUPPLIERS`, `SUPPLIER_LABELS`, `REVIEW_QUOTABLE_SUPPLIERS`, `EXPECTED_SUPPLIER_BASE_CURRENCY`, both copies of `MIXED_CATALOG_ORDER`, labels, source types and source currencies. Keep generic search and project validation data-driven.

- [ ] **Step 4: Re-run focused tests**

  Expected: domain and mixed quote tests pass without regressions for existing suppliers.

- [ ] **Step 5: Local checkpoint without commit**

  Run `git diff --check` and inspect changes for accidental formula/price logic edits.

---

## Task 8: Expose both catalogs in local admin, navigation and Project picker

**Files:**
- Modify: `mobiliti_saas/web/src/CatalogAdminPanel.jsx`
- Modify: `mobiliti_saas/web/src/productPicker.js`
- Modify only if routing requires it: `mobiliti_saas/web/src/main.jsx`
- Modify narrowly after reviewing existing user diff: `mobiliti_saas/web/src/SupplierCatalogView.jsx`
- Modify narrowly after reviewing existing user diff: `tests/test_supplier_catalog_ui.py`
- Modify: `tests/test_mixed_catalog_cart_ui.py`
- Create: `tests/test_idelika_conceptos_ui.py`

- [ ] **Step 1: Inspect dirty files before editing**

  ```powershell
  git diff -- mobiliti_saas/web/src/SupplierCatalogView.jsx tests/test_supplier_catalog_ui.py
  ```

  Preserve every unrelated existing change.

- [ ] **Step 2: Add failing UI contract tests**

  Require both options in catalog navigation/admin and `CATALOG_OPTIONS`. In the Project picker, require image, code/warning, configuration labels, availability and price label for numeric cost; pending-price items must show `Precio por confirmar` while remaining selectable.

- [ ] **Step 3: Run tests and confirm the red state**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_supplier_catalog_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_idelika_conceptos_ui.py -q
  ```

- [ ] **Step 4: Append the two UI options**

  Reuse `productPriceLabel` and the existing configuration renderer. Do not add provider-specific UI forks unless a failing contract proves one is necessary. Ensure IDÉLIKA and Conceptos price/configuration data use the same generic picker flow as ALMA/Lauco.

- [ ] **Step 5: Build the web client**

  ```powershell
  npm --prefix mobiliti_saas/web run build
  ```

  Expected: Vite build succeeds with no unresolved imports.

- [ ] **Step 6: Re-run UI tests and checkpoint locally**

  Expected: focused tests pass; `git diff --check` is clean; no production URL is contacted.

---

## Task 9: Run local sync and end-to-end quotation evidence

**Files:**
- Create: `tests/test_idelika_conceptos_local_e2e.py`
- Modify: `tests/test_mixed_catalog_browser_e2e.py`
- Create locally: `outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/idelika-conceptos-e2e-report.md`

- [ ] **Step 1: Add a failing local E2E test**

  Exercise the actual source-to-snapshot path using the local source files, a disposable repository/database and fake local asset storage. Then create one Proyecto containing:

  - one priced IDÉLIKA item;
  - one School Series price-pending item;
  - one configurable Conceptos item;
  - one unchanged existing-catalog control item.

  Verify search, configuration selection, quantity update, replacement, mixed quote payload and one-time MXN/USD conversion semantics.

- [ ] **Step 2: Run the E2E test and resolve only integration defects**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_idelika_conceptos_local_e2e.py tests/test_mixed_catalog_browser_e2e.py -q
  ```

  Expected after implementation: both pass without network access.

- [ ] **Step 3: Run the complete relevant regression suite**

  ```powershell
  & 'C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_catalog_source_config.py tests/test_catalog_source_safety.py tests/test_catalog_sync_service.py tests/test_catalog_migrations.py tests/test_supplier_catalog.py tests/test_project_catalog_search.py tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_quote_e2e.py tests/test_supplier_catalog_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_idelika_catalog_importer.py tests/test_idelika_spec_guide.py tests/test_conceptos_catalog_importer.py tests/test_idelika_conceptos_registry.py tests/test_idelika_conceptos_registration_integration.py tests/test_idelika_conceptos_quote_integration.py tests/test_idelika_conceptos_ui.py tests/test_idelika_conceptos_local_e2e.py -q
  ```

- [ ] **Step 4: Produce the local evidence report**

  Record source hashes, parsed/product/variant counts, pending-price counts, duplicate/conflict counts, SPEC validation results, test commands, durations and output paths. Explicitly state that no production write, commit, push or deploy occurred.

- [ ] **Step 5: Final local checkpoint without commit**

  ```powershell
  git diff --check
  git status --short
  ```

  Review the final diff against the approved specification and confirm no existing catalog fixture or quote formula changed.

---

## Task 10: Update the Obsidian implementation log and hand off for production approval

**External note (Obsidian MCP only):**
- Update: `armado-caratula/Catalogos-Proveedores/15-IDELIKA-Conceptos-diseno-e-integracion-local-2026-08-02.md`

- [ ] **Step 1: Append implementation evidence**

  Add the executed task list, exact changed files, SPEC path, local E2E report path, counts, test results and any unresolved ambiguity. Do not paste secrets or full binary/source contents.

- [ ] **Step 2: State the release boundary**

  Mark the work as “validado localmente, pendiente de aprobación explícita para producción”. Production synchronization, remote migration, R2 publication, Vercel/worker deployment, commit and push are a separate future authorization.

- [ ] **Step 3: Final user handoff**

  Provide clickable local links to the SPEC, validation report and key code changes; summarize passed/failed checks and ask for a separate production decision only after the user has reviewed the local result.
