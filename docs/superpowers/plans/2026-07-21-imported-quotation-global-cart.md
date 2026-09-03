# Imported Quotation Global Cart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir previsualizar una hoja `Quotation`, importar sus productos editables al carrito global, mezclarlos con productos de catálogo y generar un único Excel validado por el servidor.

**Architecture:** Un módulo Python enfocado convierte el workbook almacenado en un manifiesto autoritativo con filas e imágenes. El frontend traduce ese manifiesto a líneas `kind: "imported"` dentro del carrito existente. El checkout mixto vuelve a validar archivo, manifiesto y overrides, congela las conversiones y entrega al worker un payload con `groups` de catálogo y `imported_source` separado.

**Tech Stack:** Python 3.14, FastAPI, openpyxl, Pillow, React 18, Vite, pytest y storage Supabase/R2 con fallback local de desarrollo.

## Global Constraints

- Conservar la generación directa existente para `.xlsx` y `.pdf`.
- La previsualización editable sólo aplica a `.xlsx` con hoja `Quotation` compatible.
- Admitir USD, MXN y EUR; una moneda no detectada debe seleccionarse explícitamente.
- Convertir cada precio una sola vez y conservar precio, moneda y tasa originales.
- Permitir editar únicamente nombre, descripción, dimensiones, precio unitario y cantidad de líneas importadas.
- Mantener autoritativos los datos de las siete familias de catálogo.
- Admitir un workbook importado, 32 secciones y 500 líneas totales por checkout.
- Preservar imágenes, fórmulas, descuentos, auditoría y flujo `Quotation → Mobiliti → Cotizacion`.
- No agregar dependencias nuevas: reutilizar openpyxl, Pillow, storage y utilidades existentes.
- Preservar cambios locales previos. Antes de cada commit, verificar que el índice contenga únicamente hunks de esta función; si un archivo comparte cambios previos inseparables, dejarlo sin commit y reportarlo.
- No modificar SharePoint, Supabase remoto, Vercel ni producción durante la implementación local.

---

## File Map

- Create `mobiliti_saas/quote_engine/quotation_import.py`: parser, manifiesto, hashes, moneda, proveedor, overrides y normalización de líneas importadas.
- Create `mobiliti_saas/web/mobiliti_saas/quote_engine/quotation_import.py`: espejo desplegable del módulo anterior.
- Modify `mobiliti_saas/quote_engine/mixed_catalog.py`: aceptar `imported_source`, combinar índices y validar secciones mixtas.
- Modify `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`: espejo desplegable exacto.
- Modify `mobiliti_saas/quote_engine/catalog_cart.py`: permitir imagen local validada y dimensiones importadas al escribir una fila.
- Modify `vercel_deploy/api/index.py`, `mobiliti_saas/api/index.py`, `mobiliti_saas/web/api/index.py`: endpoint preview, storage del manifiesto y checkout importado.
- Modify `mobiliti_saas/worker/quote_worker.py`: descargar el workbook importado del job final y entregarlo al constructor mixto.
- Modify `mobiliti_saas/web/src/mixedCart.js`: modelo `kind: imported`, edición, reemplazo e item de checkout.
- Create `mobiliti_saas/web/src/ImportedCartLineFields.jsx`: editor exclusivo de líneas importadas.
- Modify `mobiliti_saas/web/src/MixedCartDrawer.jsx`: badge y editor importado.
- Modify `mobiliti_saas/web/src/main.jsx`: flujo upload → preview → moneda → carrito.
- Modify `mobiliti_saas/web/src/styles.css`: estilos responsivos del editor.
- Create `tests/quotation_import_fixtures.py`: fixture reutilizable con tres secciones, siete productos y siete imágenes.
- Create `tests/test_quotation_import.py`: contrato unitario del manifiesto y overrides.
- Modify `tests/test_quote_jobs_api.py`: preview, seguridad, storage y checkout.
- Modify `tests/test_mixed_catalog_cart.py`, `tests/test_mixed_catalog_cart_ui.py`: modelo y UI del carrito.
- Modify `tests/test_mixed_catalog_workbook.py`, `tests/test_quote_worker.py`: payload, workbook e imágenes.
- Modify `tests/test_mixed_catalog_quote_e2e.py`, `tests/test_mixed_catalog_browser_e2e.py`: flujo integral y navegador.

---

### Task 1: Parsear Quotation y construir un manifiesto autoritativo

**Files:**
- Create: `mobiliti_saas/quote_engine/quotation_import.py`
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/quotation_import.py`
- Create: `tests/quotation_import_fixtures.py`
- Create: `tests/test_quotation_import.py`

**Interfaces:**
- Consumes: `parser.read_items(source_path)` y `images.extract_images(source_path)`.
- Produces: `build_import_manifest(source_bytes, import_id, original_filename) -> tuple[dict, dict[int, tuple[bytes, str]]]`.
- Produces: `normalize_imported_items(raw_items, manifest, source_currency, quote_currency, rate_rows, discount_percent) -> list[dict]`.
- Produces: `validate_import_manifest(manifest) -> dict`.

- [ ] **Step 1: Escribir el fixture y la prueba roja del manifiesto**

```python
from decimal import Decimal
from pathlib import Path

from PIL import Image
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from openpyxl.styles import PatternFill

def write_import_fixture(path: Path, *, currency: str | None = None) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quotation"
    for column, title in {1:"No.", 2:"Item Name", 3:"Photo", 4:"Description", 5:"Dimension", 7:"Q'ty", 8:"Vol.", 10:"Unit Price"}.items():
        sheet.cell(7, column, title)
    if currency:
        sheet.cell(7, 14, "Original Currency")
    rows = [
        (8, "category", "SALA DE JUNTAS SECUNDARIO"),
        (9, "product", "DV74 I-Varna II Conference Table"),
        (10, "category", "MUESTRAS"),
        (11, "product", "CAI63SW Alien Task Chair"),
        (12, "product", "CAL61KC Aulenti Task Chair"),
        (13, "product", "CAT60SC Altaes Task Chair"),
        (14, "product", "DL60 Single Seat Workstation"),
        (15, "product", "DL61 Double Seat Workstation"),
        (16, "category", "CONCEJO"),
        (17, "product", "DV74 I-Varna II Conference Table"),
    ]
    product_index = 0
    for row, kind, value in rows:
        if kind == "category":
            sheet.cell(row, 1, f"- {value}")
            continue
        product_index += 1
        sheet.cell(row, 1, product_index)
        sheet.cell(row, 2, value)
        sheet.cell(row, 4, f"Descripción {product_index}")
        sheet.cell(row, 5, f"{600 + product_index} x 600 mm")
        sheet.cell(row, 7, 1 if row != 14 else 2)
        sheet.cell(row, 8, Decimal("0.25"))
        sheet.cell(row, 10, Decimal("80.50") if row == 11 else Decimal("100.00"))
        if currency:
            sheet.cell(row, 14, currency)
        image_path = path.parent / f"fixture-{row}.png"
        Image.new("RGB", (80, 60), (20 * product_index, 80, 120)).save(image_path)
        sheet.add_image(WorkbookImage(str(image_path)), f"C{row}")
    sheet["A1"] = "SUNON TECHNOLOGY CO.,LTD."
    sheet.cell(65536, 14).fill = PatternFill("solid", fgColor="FFFFFF")
    workbook.save(path)
    workbook.close()
    return path

def test_build_import_manifest_preserves_sections_rows_images_and_requires_currency(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")

    manifest, image_map = build_import_manifest(
        source.read_bytes(),
        import_id="7b1d6d42-236a-4bc1-9aa8-8d9db793c30b",
        original_filename=source.name,
    )
    assert manifest["source_currency"] is None
    assert manifest["currency_status"] == "required"
    assert manifest["provider"] == "SUNON TECHNOLOGY CO.,LTD."
    assert [row["title"] for row in manifest["sections"]] == [
        "SALA DE JUNTAS SECUNDARIO", "MUESTRAS", "CONCEJO",
    ]
    assert len(manifest["items"]) == 7
    assert manifest["items"][0]["key"].endswith(":9")
    assert sorted(image_map) == [9, 11, 12, 13, 14, 15, 17]
```

- [ ] **Step 2: Ejecutar la prueba y comprobar que falla por módulo ausente**

Run: `python -m pytest tests/test_quotation_import.py::test_build_import_manifest_preserves_sections_rows_images_and_requires_currency -q`

Expected: FAIL con `ModuleNotFoundError: mobiliti_saas.quote_engine.quotation_import`.

- [ ] **Step 3: Implementar el manifiesto mínimo**

```python
ALLOWED_IMPORT_CURRENCIES = frozenset({"MXN", "USD", "EUR"})
MAX_IMPORTED_LINES = 500

def build_import_manifest(source_bytes, import_id, original_filename):
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    items, columns = read_items_from_bytes(source_bytes)
    products = [item for item in items if item.tipo == "producto"]
    if not 1 <= len(products) <= MAX_IMPORTED_LINES:
        raise ValueError("La quotation debe contener entre 1 y 500 productos")
    image_map = extract_images_from_bytes(source_bytes)
    provider = _provider_from_workbook_bytes(source_bytes)
    source_currency = _explicit_currency(products)
    sections = _manifest_sections(items, import_id)
    rows = [_manifest_item(item, import_id, source_hash) for item in products]
    manifest = {
        "schema_version": 1,
        "import_id": str(uuid.UUID(import_id)),
        "source_hash": source_hash,
        "original_filename": safe_filename(original_filename),
        "provider": provider,
        "source_currency": source_currency,
        "currency_status": "detected" if source_currency else "required",
        "columns": columns,
        "sections": sections,
        "items": rows,
    }
    return validate_import_manifest(manifest), {
        row: image for row, image in image_map.items() if row in {item.row for item in products}
    }
```

- [ ] **Step 4: Agregar pruebas rojas de moneda explícita, hashes y overrides**

```python
def test_normalize_imported_items_uses_selected_currency_and_allowed_overrides(import_manifest):
    rows = normalize_imported_items(
        [{
            "kind": "imported",
            "import_id": import_manifest["import_id"],
            "source_row": 11,
            "source_currency": "USD",
            "quantity": "2",
            "overrides": {
                "name": "Alien Task Chair revisada",
                "description": "Silla operativa revisada",
                "dimension": "630 x 565 x 1000 mm",
                "unit_price": "82.00",
                "provider": "Sunon",
            },
        }],
        import_manifest,
        source_currency="USD",
        quote_currency="MXN",
        rate_rows=[{"currency":"USD","mxn_per_unit":"18.50","effective_date":"2026-07-21"}],
        discount_percent="40",
    )
    assert rows[0]["original_unit_price"] == "82.000000"
    assert rows[0]["unit_price"] == "1517.00"
    assert rows[0]["frozen_exchange_rate"] == "18.500000"
    assert rows[0]["source_reference"].endswith("#Quotation!11")
```

- [ ] **Step 5: Implementar validación y normalización con Decimal**

```python
def normalize_imported_items(raw_items, manifest, source_currency, quote_currency, rate_rows, discount_percent):
    checked = validate_import_manifest(manifest)
    fallback_currency = _currency(source_currency, "Moneda de origen requerida")
    destination = _currency(quote_currency, "Moneda de cotizacion invalida")
    originals = {item["source_row"]: item for item in checked["items"]}
    normalized = []
    seen = set()
    for raw in raw_items:
        row = _source_row(raw)
        if row in seen or row not in originals:
            raise ValueError("Fila importada invalida")
        seen.add(row)
        original = originals[row]
        overrides = _import_overrides(raw.get("overrides"))
        currency = _currency(raw.get("source_currency") or original.get("source_currency") or fallback_currency, "Moneda de origen requerida")
        original_price = _money(overrides["unit_price"], "Precio importado invalido")
        rate = resolve_conversion_rate(currency, destination, rate_rows, date.today())
        normalized.append(_normalized_imported_line(raw, original, overrides, currency, destination, rate, discount_percent, checked))
    return normalized
```

- [ ] **Step 6: Ejecutar todas las pruebas del módulo**

Run: `python -m pytest tests/test_quotation_import.py -q`

Expected: PASS, incluyendo rechazo de fórmulas inyectables, filas duplicadas, precio negativo, moneda ausente y más de 500 líneas.

- [ ] **Step 7: Verificar que las dos copias sean idénticas y crear commit aislado**

Run: `python -c "from pathlib import Path; a=Path('mobiliti_saas/quote_engine/quotation_import.py').read_bytes(); b=Path('mobiliti_saas/web/mobiliti_saas/quote_engine/quotation_import.py').read_bytes(); assert a == b"`

Commit: `git commit -m "feat(import): parse quotation preview manifest"`

---

### Task 2: Exponer preview autenticado y almacenar manifiesto e imágenes

**Files:**
- Modify: `vercel_deploy/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `tests/test_quote_jobs_api.py`

**Interfaces:**
- Consumes: `build_import_manifest` de Task 1 y helpers `_storage_download_bytes`, `_storage_upload_bytes`, `_create_signed_download`.
- Produces: `POST /cotizaciones/{job_id}/import-preview`.
- Produces metadata: `import_manifest_path`, `import_preview_paths`, `import_source_hash`, `import_item_count`.

- [ ] **Step 1: Escribir la prueba roja del endpoint**

```python
def test_import_preview_returns_manifest_and_signed_images(monkeypatch, tmp_path):
    client, token, job_id = uploaded_draft_quote(monkeypatch, tmp_path, fixture="quotation-import.xlsx")
    response = client.post(
        f"/cotizaciones/{job_id}/import-preview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["import_id"] == job_id
    assert body["currency_status"] == "required"
    assert len(body["items"]) == 7
    assert body["items"][0]["image_url"].startswith("http")
```

- [ ] **Step 2: Verificar el fallo esperado**

Run: `python -m pytest tests/test_quote_jobs_api.py::test_import_preview_returns_manifest_and_signed_images -q`

Expected: FAIL con HTTP 404.

- [ ] **Step 3: Implementar el endpoint en la API canónica**

```python
import mimetypes

def _store_import_preview(job: dict, manifest: dict, image_map: dict[int, tuple[bytes, str]]) -> tuple[str, dict[int, str]]:
    prefix = f"users/{job['usuario_id']}/jobs/{job['id']}/preview"
    manifest_path = f"{prefix}/manifest.json"
    image_paths = {}
    for row, (content, suffix) in image_map.items():
        clean_suffix = suffix.lower() if suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
        image_path = f"{prefix}/row-{row}{clean_suffix}"
        _storage_upload_bytes(image_path, content, mimetypes.guess_type(image_path)[0] or "application/octet-stream")
        image_paths[row] = image_path
    stored = {**manifest, "preview_image_paths": {str(row): path for row, path in image_paths.items()}}
    _storage_upload_bytes(manifest_path, json.dumps(stored, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json")
    return manifest_path, image_paths

def _preview_response(manifest: dict, image_paths: dict[int, str]) -> dict:
    items = []
    for item in manifest["items"]:
        path = image_paths.get(item["source_row"])
        items.append({**item, "image_url": _create_signed_download(path) if path else ""})
    return {**manifest, "items": items}

@app.post("/cotizaciones/{job_id}/import-preview")
def quotation_import_preview(job_id: str, current_user: dict = Depends(get_current_user)):
    _require_active_subscription(current_user["id"])
    job = _quote_job_for_user(job_id, current_user["id"])
    if job["status"] != "draft" or Path(job["input_path"]).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=409, detail="La quotation no esta disponible para importar")
    source_bytes = _storage_download_bytes(job["input_path"])
    manifest, image_map = build_import_manifest(source_bytes, job_id, job["metadata"]["original_filename"])
    manifest_path, image_paths = _store_import_preview(job, manifest, image_map)
    metadata = {**job["metadata"], "import_manifest_path": manifest_path, "import_preview_paths": image_paths, "import_source_hash": manifest["source_hash"], "import_item_count": len(manifest["items"])}
    db_update_quote_job(job_id, {"metadata": metadata}, expected_status="draft")
    return _preview_response(manifest, image_paths)
```

- [ ] **Step 4: Agregar pruebas de autorización, estado, tipo y cleanup**

```python
@pytest.mark.parametrize("case,expected", [
    ("other-user", 403),
    ("queued", 409),
    ("pdf", 409),
    ("missing-quotation", 400),
    ("too-many-products", 400),
])
def test_import_preview_rejects_invalid_sources(case, expected, preview_case):
    response = preview_case(case)
    assert response.status_code == expected
```

- [ ] **Step 5: Copiar el cambio a las tres APIs y ejecutar la suite enfocada**

Run: `python -m pytest tests/test_quote_jobs_api.py -k "import_preview or init_upload or delete" -q`

Expected: PASS.

- [ ] **Step 6: Verificar paridad de APIs y commit aislado**

Run: `python -c "from pathlib import Path; files=[Path('vercel_deploy/api/index.py'),Path('mobiliti_saas/api/index.py'),Path('mobiliti_saas/web/api/index.py')]; assert len({p.read_bytes() for p in files}) == 1"`

Commit: `git commit -m "feat(api): preview uploaded quotations"`

---

### Task 3: Modelar líneas importadas dentro del carrito global

**Files:**
- Modify: `mobiliti_saas/web/src/mixedCart.js`
- Modify: `tests/test_mixed_catalog_cart.py`

**Interfaces:**
- Consumes: response de `/import-preview`.
- Produces: `createImportedCartBundle(preview, sourceCurrency, provider, currentSections) -> {lines, sections}`.
- Produces: `updateImportedCartLine(lines, key, edits) -> lines`.
- Extiende: `toMixedQuoteItem(line)` con `kind: "imported"`.

- [ ] **Step 1: Escribir prueba roja de importación y mezcla**

```python
def test_imported_preview_becomes_editable_global_cart_lines(run_mixed_cart_js):
    result = run_mixed_cart_js("createImportedCartBundle", PREVIEW, "USD", "Sunon", [{"id":"section-1","concept":"Recepción"}])
    assert len(result["lines"]) == 7
    assert result["lines"][0]["kind"] == "imported"
    assert result["lines"][0]["sectionId"] == result["sections"][0]["id"]
    assert result["lines"][0]["edits"]["unitPrice"] == "688.50"
```

- [ ] **Step 2: Ejecutar y comprobar el fallo por export ausente**

Run: `python -m pytest tests/test_mixed_catalog_cart.py -k imported_preview -q`

Expected: FAIL porque `createImportedCartBundle` no existe.

- [ ] **Step 3: Implementar el modelo mínimo**

```javascript
export function createImportedCartBundle(preview, sourceCurrency, provider, currentSections) {
  const currency = normalizedImportCurrency(sourceCurrency || preview.source_currency);
  const importedSections = importedPresentationSections(preview.sections, currentSections);
  const sectionByKey = new Map(importedSections.flatMap((section) => section.itemKeys.map((key) => [key, section.id])));
  const lines = preview.items.map((item) => createImportedCartLine({
    preview,
    item,
    sourceCurrency: item.source_currency || currency,
    provider,
    sectionId: sectionByKey.get(item.key),
  }));
  return { lines, sections: importedSections.map(({ itemKeys, ...section }) => section) };
}

export function updateImportedCartLine(lines, key, edits) {
  return lines.map((line) => line.key === key
    ? { ...line, edits: validateImportedEdits({ ...line.edits, ...edits }) }
    : line);
}
```

- [ ] **Step 4: Escribir y ejecutar pruebas rojas de checkout y reemplazo**

```python
def test_imported_line_checkout_contains_only_reference_and_allowed_overrides(run_mixed_cart_js):
    item = run_mixed_cart_js("toMixedQuoteItem", IMPORTED_LINE)
    assert set(item) == {"kind", "import_id", "source_row", "source_currency", "quantity", "overrides"}

def test_replacing_import_keeps_catalog_lines_and_removes_previous_import(run_mixed_cart_js):
    result = run_mixed_cart_js("replaceImportedCartBundle", [CATALOG_LINE, IMPORTED_LINE], SECTIONS, NEW_BUNDLE)
    assert [line["key"] for line in result["lines"]] == [CATALOG_LINE["key"], NEW_IMPORTED_LINE["key"]]
```

- [ ] **Step 5: Implementar checkout y reemplazo; ejecutar tests**

Run: `python -m pytest tests/test_mixed_catalog_cart.py -q`

Expected: PASS.

- [ ] **Step 6: Commit aislado**

Commit: `git commit -m "feat(cart): add imported quotation lines"`

---

### Task 4: Integrar el flujo de preview y el editor en React

**Files:**
- Create: `mobiliti_saas/web/src/ImportedCartLineFields.jsx`
- Modify: `mobiliti_saas/web/src/MixedCartDrawer.jsx`
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`
- Modify: `tests/test_mixed_catalog_cart_ui.py`
- Modify: `tests/test_web_ui_defaults.py`

**Interfaces:**
- Consumes: funciones de Task 3 y endpoint de Task 2.
- Produces callback: `onImportPreview(preview, {sourceCurrency, provider, quoteForm})`.
- Produces componente: `ImportedCartLineFields({line, busy, onChange})`.

- [ ] **Step 1: Escribir pruebas rojas de textos y controles**

```python
def test_quote_form_offers_preview_import_without_removing_direct_generation():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert "Previsualizar e importar al carrito" in source
    assert "Generar cotizacion" in source

def test_imported_cart_editor_exposes_only_approved_fields():
    source = Path("mobiliti_saas/web/src/ImportedCartLineFields.jsx").read_text(encoding="utf-8")
    for field in ("name", "description", "dimension", "unitPrice", "provider"):
        assert f'name="{field}"' in source
    assert 'name="image"' not in source
```

- [ ] **Step 2: Ejecutar y verificar el fallo por componente ausente**

Run: `python -m pytest tests/test_mixed_catalog_cart_ui.py -k imported -q`

Expected: FAIL porque no existe `ImportedCartLineFields.jsx`.

- [ ] **Step 3: Crear el editor importado**

```jsx
export default function ImportedCartLineFields({ line, busy, onChange }) {
  if (line.kind !== "imported") return null;
  return (
    <details className="imported-line-editor">
      <summary>Editar datos importados</summary>
      <label>Nombre<input name="name" disabled={busy} value={line.edits.name} onChange={(event) => onChange({ name: event.target.value })} /></label>
      <label>Descripción<textarea name="description" disabled={busy} value={line.edits.description} onChange={(event) => onChange({ description: event.target.value })} /></label>
      <label>Dimensiones<input name="dimension" disabled={busy} value={line.edits.dimension} onChange={(event) => onChange({ dimension: event.target.value })} /></label>
      <label>Precio unitario<input name="unitPrice" inputMode="decimal" disabled={busy} value={line.edits.unitPrice} onChange={(event) => onChange({ unitPrice: event.target.value })} /></label>
      <label>Proveedor<input name="provider" disabled={busy} value={line.edits.provider} onChange={(event) => onChange({ provider: event.target.value })} /></label>
    </details>
  );
}
```

- [ ] **Step 4: Integrar upload, preview, moneda y carrito**

```jsx
async function previewImport() {
  const draft = await uploadQuoteDraft(file, form.template);
  const preview = await request(`/cotizaciones/${draft.job_id}/import-preview`, { method: "POST" });
  setImportPreview(preview);
  setImportCurrency(preview.source_currency || "");
  setImportProvider(preview.provider || "");
}

function confirmImport() {
  if (!importCurrency || !importProvider.trim()) return;
  onImportPreview(importPreview, {
    sourceCurrency: importCurrency,
    provider: importProvider.trim(),
    quoteForm: form,
  });
  setImportPreview(null);
}
```

- [ ] **Step 5: Probar accesibilidad y comportamiento de líneas de catálogo**

Run: `python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_web_ui_defaults.py -q`

Expected: PASS; los controles tienen labels, las líneas de catálogo no muestran editor y la moneda pendiente deshabilita la confirmación.

- [ ] **Step 6: Compilar frontend**

Run: `npm.cmd run build`

Workdir: `mobiliti_saas/web`

Expected: exit 0 sin errores de Vite.

- [ ] **Step 7: Commit aislado si los archivos no contienen hunks previos inseparables**

Commit: `git commit -m "feat(web): import quotation into global cart"`

---

### Task 5: Validar items importados en el checkout mixto

**Files:**
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `vercel_deploy/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `tests/test_mixed_catalog_cart.py`
- Modify: `tests/test_quote_jobs_api.py`

**Interfaces:**
- Consumes: `normalize_imported_items` y manifiesto almacenado.
- Extiende: `build_mixed_catalog_cart_payload(raw_items, *, catalogs, rate_rows, quote_currency, commercial_discount_percent, presentation_sections=None, imported_source=None) -> dict`.
- Produce payload: `groups`, `imported_source`, `sections`, `rate_summary`.

- [ ] **Step 1: Escribir prueba roja de payload combinado**

```python
def test_mixed_payload_keeps_catalog_groups_and_imported_source_separate(catalogs, manifest, rates):
    payload = build_mixed_catalog_cart_payload(
        [CATALOG_ITEM],
        catalogs=catalogs,
        rate_rows=rates,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=MIXED_SECTIONS,
        imported_source={"manifest": manifest, "items": [IMPORTED_ITEM], "source_currency": "USD"},
    )
    assert payload["groups"][0]["catalog"] == "offiho"
    assert payload["imported_source"]["items"][0]["canonical_key"].startswith("import:")
    assert payload["sections"][0]["item_keys"] == [CATALOG_KEY, IMPORTED_KEY]
```

- [ ] **Step 2: Ejecutar y comprobar el fallo de firma**

Run: `python -m pytest tests/test_mixed_catalog_cart.py -k imported_source -q`

Expected: FAIL porque `build_mixed_catalog_cart_payload` no acepta `imported_source`.

- [ ] **Step 3: Implementar índice combinado sin alterar reservas**

```python
def _combined_items(groups, imported_source):
    catalog_items = [item for group in groups for item in group["items"]]
    imported_items = [] if imported_source is None else imported_source["items"]
    combined = catalog_items + imported_items
    index = {item["canonical_key"]: item for item in combined}
    if len(index) != len(combined):
        raise ValueError("Claves mixtas duplicadas")
    return combined, index
```

- [ ] **Step 4: Extender API para separar items y copiar la fuente al job final**

```python
catalog_items, imported_items = split_mixed_quote_items(raw_items)
manifest, import_job = _validated_import_source(current_user["id"], imported_items)
cart_payload = build_mixed_catalog_cart_payload(
    catalog_items,
    catalogs=catalogs,
    rate_rows=rate_rows,
    quote_currency=str(body.get("quote_currency") or "MXN"),
    commercial_discount_percent=body.get("descuento", "40"),
    presentation_sections=body.get("sections"),
    imported_source={
        "manifest": manifest,
        "items": imported_items,
        "source_currency": imported_items[0]["source_currency"] if imported_items else None,
    } if imported_items else None,
)
```

El checkout descarga y valida nuevamente la fuente, crea el job final y guarda una copia en `users/{user_id}/jobs/{final_job_id}/import-source.xlsx`. El payload sólo referencia esa ruta interna después de que la copia termine correctamente.

- [ ] **Step 5: Probar manipulación, moneda, límite y rollback**

```python
@pytest.mark.parametrize("mutation", [
    "other-user-import", "missing-row", "duplicate-row", "changed-hash",
    "unknown-field", "negative-price", "missing-currency", "second-import-id",
])
def test_mixed_quote_rejects_invalid_imported_items_without_creating_job(mutation, imported_quote_case):
    before = imported_quote_case.job_count()
    response = imported_quote_case.submit(mutation)
    assert response.status_code in {400, 403, 409}
    assert imported_quote_case.job_count() == before
```

- [ ] **Step 6: Ejecutar suites enfocadas y verificar copias**

Run: `python -m pytest tests/test_mixed_catalog_cart.py tests/test_quote_jobs_api.py -k "imported or mixed_quote" -q`

Expected: PASS.

Run: `python -c "from pathlib import Path; assert Path('mobiliti_saas/quote_engine/mixed_catalog.py').read_bytes() == Path('mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py').read_bytes()"`

- [ ] **Step 7: Commit aislado**

Commit: `git commit -m "feat(api): validate imported mixed cart lines"`

---

### Task 6: Generar Quotation mixto con imágenes importadas

**Files:**
- Modify: `mobiliti_saas/quote_engine/catalog_cart.py`
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/worker/quote_worker.py`
- Modify: `tests/test_mixed_catalog_workbook.py`
- Modify: `tests/test_quote_worker.py`

**Interfaces:**
- Extiende: `write_catalog_quotation_item(ws, *, row, index, item, source_type, images_root, text_transform, image_file_key=None, extra_description_parts=(), local_image_path=None) -> None`.
- Extiende: `create_mixed_catalog_quotation_workbook(payload, output_path, imported_source_path=None)`.
- Extiende worker: descargar `payload["imported_source"]["storage_path"]` y verificar SHA-256.

- [ ] **Step 1: Escribir prueba roja del workbook**

```python
def test_mixed_workbook_interleaves_catalog_and_imported_rows_with_original_image(tmp_path):
    output = create_mixed_catalog_quotation_workbook(
        MIXED_IMPORTED_PAYLOAD,
        tmp_path / "quotation.xlsx",
        imported_source_path=IMPORTED_SOURCE,
    )
    wb = load_workbook(output)
    ws = wb["Quotation"]
    assert ws["A8"].value == "- Recepción"
    assert ws["B9"].value == "Producto de catálogo"
    assert ws["B10"].value == "Alien Task Chair revisada"
    assert ws["E10"].value == "630 x 565 x 1000 mm"
    assert ws["J10"].value == 1517.0
    assert ws["N10"].value == "USD"
    assert ws["O10"].value == 82.0
    assert sorted(image.anchor._from.row + 1 for image in ws._images) == [9, 10]
```

- [ ] **Step 2: Ejecutar y comprobar el fallo por argumento ausente**

Run: `python -m pytest tests/test_mixed_catalog_workbook.py -k imported_rows -q`

Expected: FAIL porque `imported_source_path` no es aceptado.

- [ ] **Step 3: Añadir imagen local y dimensiones importadas**

```python
def write_catalog_quotation_item(
    ws, *, row, index, item, source_type, images_root, text_transform,
    image_file_key=None, extra_description_parts=(), local_image_path=None,
):
    dimensions = str((item.get("attributes") or {}).get("dimensions") or "").strip()
    ws.cell(row, 5).value = text_transform(
        dimensions if source_type in {"supplier_cart", "imported_quotation"} and dimensions else str(item.get("unit") or "")
    )
    if local_image_path is not None:
        add_local_catalog_image(ws, row, Path(local_image_path), images_root, image_file_key or f"imported-{row}")
    else:
        _add_catalog_image(ws, row, item.get("image_url"), images_root, str(item.get("code") or ""), source_type, destination_key=image_file_key)
```

- [ ] **Step 4: Hacer que el builder resuelva filas e imágenes importadas**

```python
imported_images, imported_temp = ({}, None)
if payload.get("imported_source"):
    imported_images, imported_temp = extract_images(imported_source_path)
items_by_key = {
    item["canonical_key"]: item
    for item in [
        *(item for group in payload["groups"] for item in group["items"]),
        *(payload.get("imported_source") or {}).get("items", []),
    ]
}
```

Para cada línea `catalog == "imported"`, el builder usa `source_row` para obtener `local_image_path` y `source_type="imported_quotation"`. El directorio temporal se limpia en `finally`.

- [ ] **Step 5: Extender worker con descarga y hash**

```python
def _download_imported_source(client, payload, tmp_dir):
    imported = payload.get("imported_source")
    if not imported:
        return None
    target = tmp_dir / "import-source.xlsx"
    client.storage_download_from_provider(imported["storage_path"], target, imported["storage_provider"])
    if hashlib.sha256(target.read_bytes()).hexdigest() != imported["source_hash"]:
        raise RuntimeError("La fuente importada cambio despues de validarse")
    return target
```

- [ ] **Step 6: Ejecutar pruebas de workbook y worker**

Run: `python -m pytest tests/test_mixed_catalog_workbook.py tests/test_quote_worker.py -k "imported or mixed" -q`

Expected: PASS, con imágenes, auditoría y rechazo de hash alterado.

- [ ] **Step 7: Verificar espejos y commit aislado**

Run: `python -c "from pathlib import Path; assert Path('mobiliti_saas/quote_engine/catalog_cart.py').read_bytes() == Path('mobiliti_saas/web/mobiliti_saas/quote_engine/catalog_cart.py').read_bytes(); assert Path('mobiliti_saas/quote_engine/mixed_catalog.py').read_bytes() == Path('mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py').read_bytes()"`

Commit: `git commit -m "feat(engine): render imported quotation products"`

---

### Task 7: Probar el Excel final y la conversión única

**Files:**
- Modify: `tests/test_mixed_quote_engine.py`
- Modify: `tests/test_mixed_catalog_quote_e2e.py`

**Interfaces:**
- Consumes: payload y workbook de Tasks 5 y 6.
- Verifica: `Quotation!J → Mobiliti!J → Mobiliti!X → Cotizacion!F` y descuento maestro.

- [ ] **Step 1: Escribir prueba E2E roja con producto importado y catálogo**

```python
@pytest.mark.parametrize("quote_currency,expected_import_price", [
    ("MXN", Decimal("1517.00")),
    ("USD", Decimal("82.00")),
    ("EUR", Decimal("75.44")),
])
def test_imported_and_catalog_items_generate_one_quote_with_single_conversion(quote_currency, expected_import_price, mixed_import_e2e):
    output = mixed_import_e2e.generate(quote_currency=quote_currency, discount="40")
    wb = load_workbook(output, data_only=False)
    quotation = wb["Quotation"]
    mobiliti = wb["Mobiliti"]
    cotizacion = wb["Cotizacion"]
    source_row = mixed_import_e2e.imported_source_row(quotation)
    mobiliti_row = mixed_import_e2e.mobiliti_row(mobiliti, source_row)
    cotizacion_row = mixed_import_e2e.cotizacion_row(cotizacion, source_row)
    assert Decimal(str(quotation.cell(source_row, 10).value)) == expected_import_price
    assert mobiliti.cell(mobiliti_row, 10).value == f"=Quotation!J{source_row}"
    assert "$K$6" not in str(cotizacion.cell(cotizacion_row, 6).value)
    assert cotizacion.cell(cotizacion_row, 7).value == f"=G${mixed_import_e2e.first_product_row(cotizacion)}"
```

- [ ] **Step 2: Ejecutar y observar el fallo antes de completar integración**

Run: `python -m pytest tests/test_mixed_catalog_quote_e2e.py -k imported_and_catalog -q`

Expected: FAIL hasta que Tasks 5 y 6 produzcan el contrato completo.

- [ ] **Step 3: Ajustar únicamente el adaptador importado hasta pasar**

No introducir fórmulas de tipo de cambio nuevas en `Mobiliti` o `Cotizacion`. El valor convertido debe quedar congelado en `Quotation!J`; las columnas `N`, `O` y `P` conservan moneda, precio original y tasa.

- [ ] **Step 4: Ejecutar pruebas de motor y E2E**

Run: `python -m pytest tests/test_mixed_quote_engine.py tests/test_mixed_catalog_quote_e2e.py -q`

Expected: PASS para MXN, USD y EUR, descuento maestro, imágenes, secciones y fórmulas vacías.

- [ ] **Step 5: Commit aislado si existe un cambio funcional adicional**

Commit: `git commit -m "test(import): cover mixed quotation generation"`

---

### Task 8: Verificar navegador, regresiones y archivo CET real

**Files:**
- Modify: `tests/test_mixed_catalog_browser_e2e.py`
- Modify: `tests/test_dev_saas_e2e.py`
- Update via MCP: `armado-caratula/37-Diseno-importacion-quotation-al-carrito.md`

**Interfaces:**
- Consumes: aplicación completa y `C:\Users\pepem\Downloads\CET PRUEBAS GENERADOR-Quotation Sheet - V1.xlsx` para validación manual local.
- Produce: evidencia final de pruebas, build, health, preview y XLSX.

- [ ] **Step 1: Escribir prueba de navegador para el recorrido visible**

```python
def test_browser_imports_uploaded_quotation_into_global_cart(browser, api, import_fixture):
    browser.login(api.user)
    browser.open_new_quote()
    browser.upload(import_fixture)
    browser.click("Previsualizar e importar al carrito")
    browser.select("Moneda de origen", "USD")
    browser.click("Agregar 7 productos al carrito")
    assert browser.cart_count() == 7
    browser.edit_imported_line("CAI63SW", unit_price="82.00", quantity="2")
    browser.add_catalog_product("offiho", "ALUFSEN")
    assert browser.cart_count() == 8
```

- [ ] **Step 2: Ejecutar navegador E2E**

Run: `python -m pytest tests/test_mixed_catalog_browser_e2e.py tests/test_dev_saas_e2e.py -q`

Expected: PASS sin errores de consola.

- [ ] **Step 3: Ejecutar regresiones focales completas**

Run: `python -m pytest tests/test_quotation_import.py tests/test_quote_jobs_api.py tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_cart_ui.py tests/test_mixed_catalog_workbook.py tests/test_quote_worker.py tests/test_mixed_quote_engine.py tests/test_mixed_catalog_quote_e2e.py tests/test_mobiliti_capacity.py -q`

Expected: exit 0 y cero fallos.

- [ ] **Step 4: Ejecutar compilación y chequeos estáticos**

Run: `python -m py_compile mobiliti_saas/quote_engine/quotation_import.py mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/quote_engine/catalog_cart.py mobiliti_saas/worker/quote_worker.py vercel_deploy/api/index.py`

Expected: exit 0.

Run: `npm.cmd run build`

Workdir: `mobiliti_saas/web`

Expected: exit 0.

Run: `git diff --check -- mobiliti_saas tests docs/superpowers`

Expected: sin errores en los archivos de esta función. Los avisos preexistentes fuera del alcance se reportan sin modificarlos.

- [ ] **Step 5: Validar localhost con el Excel CET real**

1. Abrir `http://127.0.0.1:5173/`.
2. Cargar `C:\Users\pepem\Downloads\CET PRUEBAS GENERADOR-Quotation Sheet - V1.xlsx`.
3. Confirmar 3 secciones, 7 productos y 7 imágenes.
4. Seleccionar USD como moneda de origen.
5. Editar un precio, una descripción y una cantidad.
6. Agregar un producto de catálogo, moverlo a una sección importada y cotizar en MXN.
7. Abrir el XLSX y verificar orden, imágenes, edición, precio convertido una vez y descuento maestro.

- [ ] **Step 6: Actualizar Obsidian con evidencia exacta**

Agregar a `armado-caratula/37-Diseno-importacion-quotation-al-carrito.md`:

```markdown
## Iteración final — Implementación local verificada

- Archivos principales modificados.
- Conteos exactos de pruebas aprobadas.
- Resultado de build y health local.
- Resultado del archivo CET real: 3 secciones, 7 productos y 7 imágenes.
- Estado de publicación: local, sin deploy ni cambios remotos.
```

- [ ] **Step 7: Crear commit final sólo con hunks propios que no se hayan podido aislar antes**

Commit: `git commit -m "feat: import editable quotations into global cart"`

Si los archivos comparten cambios locales previos inseparables, no crear este commit; conservar el trabajo local, listar archivos y explicar la razón.
