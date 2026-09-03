# Manual Mixed-Cart Sections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que el usuario ordene productos de cualquier catálogo, cierre y edite secciones de espacios de oficina, y genere un único Excel que respete esos cortes sin agrupar visualmente por proveedor.

**Architecture:** El frontend mantiene una lista global de líneas con `sectionId` y un arreglo ordenado de conceptos. La API conserva `groups` por catálogo para cálculos y reservas, añade `sections` para presentación y el adaptador `Quotation` recorre estas últimas. El motor final permanece sin cambios porque ya interpreta filas de categoría como secciones.

**Tech Stack:** React 18, JavaScript ES modules, FastAPI, Python 3, openpyxl, pytest, Vite.

## Global Constraints

- No añadir dependencias.
- No modificar el template oficial, SharePoint, Supabase, Vercel ni producción.
- Mantener `items` sin precio, moneda, stock, imagen, URL ni otra autoridad comercial del navegador.
- Mantener `groups` en orden canónico para tasas y reservas.
- Admitir como máximo 32 secciones y 500 líneas.
- Neutralizar conceptos con la protección de texto Excel existente.
- Conservar los cambios locales previos del usuario. Los archivos Python y pruebas afectadas ya tienen modificaciones ajenas a esta entrega; no se deben stagear ni confirmar en bloque.
- Usar TDD: cada cambio comienza con una prueba roja, luego implementación mínima y prueba verde.

---

### Task 1: Modelo puro de secciones y orden del carrito

**Files:**

- Modify: `mobiliti_saas/web/src/mixedCart.js`
- Modify: `tests/test_mixed_catalog_cart_ui.py`

**Interfaces:**

- Consumes: `mixedCartKey`, `createMixedCartLine`, `upsertMixedCartLine` y el arreglo global de líneas existente.
- Produces: `createInitialMixedCartSections()`, `mixedCartSectionLabel(section, index)`, `closeMixedCartSection(sections, lines)`, `renameMixedCartSection(sections, id, concept)`, `mergeMixedCartSection(sections, lines, id)`, `moveMixedCartLine(lines, key, direction)`, `moveMixedCartLineToSection(lines, sections, key, sectionId)`, `compactMixedCartSections(sections, lines)` y `toMixedQuoteSections(sections, lines)`.

- [ ] **Step 1: Ampliar el importador de pruebas JS y escribir casos rojos**

Agregar los nuevos nombres a `EXPORTS` y una prueba Node que exija el flujo completo:

```javascript
let sections = createInitialMixedCartSections();
let lines = [
  createMixedCartLine({...firstInput, sectionId: sections[0].id}),
  createMixedCartLine({...secondInput, sectionId: sections[0].id}),
];
sections = closeMixedCartSection(sections, lines);
sections = renameMixedCartSection(sections, sections[1].id, "Privados");
lines = [...lines, createMixedCartLine({...thirdInput, sectionId: sections[1].id})];
lines = moveMixedCartLine(lines, secondKey, "up");
lines = moveMixedCartLineToSection(lines, sections, firstKey, sections[1].id);
const payloadSections = toMixedQuoteSections(sections, lines);
```

La aserción debe comprobar etiquetas `1-Recepción` y `2-Privados`, orden exacto de `item_keys`, reasignación entre secciones, unión reversible, eliminación de secciones vacías y omisión de una última sección sin productos.

- [ ] **Step 2: Ejecutar la prueba y confirmar que falla por exports ausentes**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "manual_section or section_order" -q
```

Expected: FAIL porque las funciones de sección aún no existen.

- [ ] **Step 3: Implementar las funciones puras mínimas**

Añadir en `mixedCart.js`:

```javascript
export const MAX_MIXED_CART_SECTIONS = 32;
export const DEFAULT_MIXED_SECTION_CONCEPTS = Object.freeze([
  "Recepción", "Sala de estar", "Operativos", "Privados", "Sala de juntas",
  "Dirección", "Áreas comunes", "Capacitación", "Comedor", "Otro",
]);

function defaultSectionConcept(index) {
  return DEFAULT_MIXED_SECTION_CONCEPTS[index] || `Espacio ${index + 1}`;
}

export function createInitialMixedCartSections() {
  return [{ id: "section-1", concept: defaultSectionConcept(0) }];
}

export function mixedCartSectionLabel(section, index) {
  const concept = String(section?.concept || "").trim() || defaultSectionConcept(index);
  return `${index + 1}-${concept}`;
}
```

`createMixedCartLine` debe copiar `sectionId`, usando `section-1` como compatibilidad. `upsertMixedCartLine` conserva la sección de una línea existente y usa la sección recibida sólo al crear una nueva.

Las operaciones deben ser puras, rechazar claves/secciones inexistentes, limitar conceptos a 120 caracteres, mantener las líneas de una sección contiguas y devolver arreglos nuevos. `toMixedQuoteSections` devuelve sólo secciones con líneas:

```javascript
return sections.flatMap((section, index) => {
  const itemKeys = lines.filter((line) => line.sectionId === section.id).map((line) => line.key);
  if (!itemKeys.length) return [];
  const title = String(section.concept || "").trim() || defaultSectionConcept(index);
  return [{ id: section.id, title, item_keys: itemKeys }];
});
```

- [ ] **Step 4: Ejecutar las pruebas puras y la regresión de cantidades**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "manual_section or section_order or quantity or key" -q
```

Expected: PASS.

- [ ] **Step 5: Revisar el diff sin stagear archivos solapados**

Run:

```powershell
git diff --check -- mobiliti_saas/web/src/mixedCart.js tests/test_mixed_catalog_cart_ui.py
```

Expected: salida vacía. No ejecutar `git add` porque `tests/test_mixed_catalog_cart_ui.py` puede compartir contexto con trabajo local activo.

---

### Task 2: Contrato de secciones en el payload autoritativo

**Files:**

- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `tests/test_mixed_catalog_cart.py`

**Interfaces:**

- Consumes: claves de `mixed_cart_key`, grupos comerciales existentes y `presentation_sections` del navegador.
- Produces: payload congelado con `sections: list[{id, title, item_keys}]` validado junto con `groups`.

- [ ] **Step 1: Escribir pruebas rojas de orden, compatibilidad y manipulación**

Agregar casos que construyan líneas intercaladas y secciones explícitas:

```python
sections = [
    {"id": "section-1", "title": "Recepción", "item_keys": [offiho_key, alma_key]},
    {"id": "section-2", "title": "Privados", "item_keys": [sonara_key]},
]
payload = build_mixed_catalog_cart_payload(
    rows,
    catalogs=mixed_catalogs,
    rate_rows=rate_rows,
    quote_currency="MXN",
    commercial_discount_percent="40",
    presentation_sections=sections,
    today=date(2026, 7, 19),
)
assert payload["sections"] == sections
assert [group["catalog"] for group in payload["groups"]] == ["offiho", "sonara", "alma"]
```

Añadir parametrización para clave omitida, duplicada, desconocida, orden aplanado distinto, campo inesperado, ID duplicado, título vacío, más de 32 secciones y sección vacía. Añadir compatibilidad sin `presentation_sections` y exigir una sola sección `Recepción` en el orden recibido.

- [ ] **Step 2: Ejecutar los casos y observar el argumento desconocido**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py -k "presentation_section or legacy_single_section" -q
```

Expected: FAIL porque el constructor todavía no acepta `presentation_sections`.

- [ ] **Step 3: Implementar preflight único y normalización de secciones**

Añadir constantes:

```python
MAX_MIXED_SECTIONS = 32
MAX_MIXED_SECTION_TITLE = 120
MIXED_SECTION_FIELDS = frozenset({"id", "title", "item_keys"})
```

Validar primero las líneas en su orden recibido y rechazar claves duplicadas. Construir después `rows_by_catalog` sin volver a ejecutar el preflight. Implementar:

```python
def _normalize_presentation_sections(raw_sections, ordered_rows):
    ordered_keys = [mixed_cart_key(row) for row in ordered_rows]
    if raw_sections is None:
        return [{"id": "section-1", "title": "Recepción", "item_keys": ordered_keys}]
    if not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= MAX_MIXED_SECTIONS:
        raise ValueError("Secciones mixtas invalidas")
    # Validar shape exacto, IDs/títulos, listas no vacías e IDs únicos.
    # La concatenación final debe ser exactamente ordered_keys.
```

Agregar `presentation_sections: object | None = None` a `build_mixed_catalog_cart_payload`, insertar `"sections": normalized_sections` en el payload y ampliar el conjunto exacto superior del validador.

El validador congelado debe comprobar que todas las claves de `sections` cubren una sola vez las claves autoritativas de `groups`. No cambiar `MIXED_GROUP_FIELDS`, `MIXED_LINE_FIELDS` ni reservas.

- [ ] **Step 4: Sincronizar la copia web exacta**

Aplicar el mismo cambio funcional en `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`, preservando en ambas copias la excepción local `_trusted_dev_catalog_asset_path` ya presente.

- [ ] **Step 5: Ejecutar contrato y regresión de reservas/tasas**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py -q
```

Expected: PASS, incluidos grupos canónicos, reservas, descuentos, tasas y URLs locales verificadas.

- [ ] **Step 6: Comprobar paridad de las dos copias**

Run:

```powershell
python -c "from pathlib import Path; a=Path('mobiliti_saas/quote_engine/mixed_catalog.py').read_bytes(); b=Path('mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py').read_bytes(); assert a == b"
```

Expected: PASS.

---

### Task 3: `Quotation` ordenada por secciones manuales

**Files:**

- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `tests/test_mixed_catalog_workbook.py`
- Modify: `tests/test_mixed_quote_engine.py`
- Modify: `tests/test_mobiliti_capacity.py`

**Interfaces:**

- Consumes: payload validado con `groups` autoritativos y `sections` de presentación.
- Produces: una hoja `Quotation` con categorías por concepto y productos intercalados por proveedor; el motor genera títulos y subtotales existentes.

- [ ] **Step 1: Reemplazar la expectativa antigua con pruebas rojas de secciones**

Cambiar la prueba que espera `- Tarkett`, `- Sonara`, `- ALMA`. Construir dos secciones con orden manual y exigir:

```python
assert [ws.cell(row, 1).value for row in (8, 11)] == ["- Recepción", "- Privados"]
assert [ws.cell(row, 12).value for row in (9, 10, 12)] == ["ALMA", "Tarkett", "Sonara"]
assert [ws.cell(row, 1).value for row in (9, 10, 12)] == [1, 2, 3]
```

Añadir una prueba del motor final que compruebe `Mobiliti` con `Sección 1 - Recepción` y `Sección 2 - Privados`, y `Cotizacion` con las bandas `- Recepción` y `- Privados`, sin `Sección 1 - 1-Recepción`.

- [ ] **Step 2: Ejecutar las pruebas y confirmar la agrupación antigua**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_workbook.py tests/test_mixed_quote_engine.py tests/test_mobiliti_capacity.py -k "manual_section or provider_sections" -q
```

Expected: FAIL porque `create_mixed_catalog_quotation_workbook` todavía recorre `groups`.

- [ ] **Step 3: Cambiar sólo el recorrido de presentación**

Dentro del creador del workbook construir índices inmutables:

```python
groups_by_catalog = {group["catalog"]: group for group in payload["groups"]}
items_by_key = {
    item["canonical_key"]: item
    for group in payload["groups"]
    for item in group["items"]
}
```

Recorrer:

```python
for section in payload["sections"]:
    ws.cell(row, 1).value = "- " + safe_excel_text(section["title"])
    ws.cell(row, 1).font = Font(bold=True)
    row += 1
    for key in section["item_keys"]:
        item = items_by_key[key]
        group = groups_by_catalog[item["catalog"]]
        # Reutilizar sin cambios write_catalog_quotation_item y columnas L:S.
```

No modificar fórmulas del motor, descuentos, hashes, imágenes ni proveedor por fila.

- [ ] **Step 4: Ejecutar pruebas de workbook y motor**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_workbook.py tests/test_mixed_quote_engine.py tests/test_mobiliti_capacity.py -q
```

Expected: PASS.

- [ ] **Step 5: Volver a verificar paridad de módulos**

Run el comando de comparación binaria de Task 2. Expected: PASS.

---

### Task 4: API, sesión React y envío atómico de secciones

**Files:**

- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `tests/test_quote_jobs_api.py`
- Modify: `tests/test_mixed_catalog_cart_ui.py`

**Interfaces:**

- Consumes: helpers de Task 1 y constructor `presentation_sections` de Task 2.
- Produces: POST único con `items` y `sections`, payload almacenado con secciones y reset coordinado tras éxito/logout/expiración.

- [ ] **Step 1: Escribir pruebas rojas del endpoint y controlador**

En API enviar:

```python
body["sections"] = [{
    "id": "section-1",
    "title": "Recepción",
    "item_keys": ["tarkett:25731726"],
}]
```

Exigir HTTP 200, `uploaded_payload["sections"] == body["sections"]` y `job["metadata"]["mixed_section_count"] == 1`. Verificar que un orden inconsistente da 400 antes de crear trabajo.

En el harness JS proporcionar `sectionsRef` y `replaceSections`, cerrar una sección, agregar una segunda línea y exigir que el body capturado contenga `sections`; error conserva ambos estados y éxito los reinicia.

- [ ] **Step 2: Ejecutar pruebas rojas**

Run:

```powershell
python -m pytest tests/test_quote_jobs_api.py tests/test_mixed_catalog_cart_ui.py -k "section" -q
```

Expected: FAIL por campo superior no permitido y estado React inexistente.

- [ ] **Step 3: Ampliar las tres APIs con el mismo cambio estrecho**

Añadir `"sections"` a `MIXED_QUOTE_BODY_FIELDS` y llamar:

```python
cart_payload = build_mixed_catalog_cart_payload(
    preflight_items,
    catalogs=catalogs,
    rate_rows=rate_rows,
    quote_currency=str(body.get("quote_currency") or "MXN"),
    commercial_discount_percent=body.get("descuento", "40"),
    presentation_sections=body.get("sections"),
)
```

Agregar a metadata:

```python
"mixed_section_count": len(cart_payload["sections"]),
```

Las tres copias API deben conservar su paridad completa.

- [ ] **Step 4: Integrar secciones en `createMixedQuoteController` y `App`**

Crear `mixedCartSections`, `mixedCartSectionsRef` y `replaceMixedCartSections`. El controlador usa la última sección al agregar, expone `closeSection`, `renameSection`, `mergeSection`, `moveLine` y `moveLineToSection`, compacta después de quitar/mover y envía:

```javascript
body: JSON.stringify({
  ...getForm(),
  items: committedLines.map(toMixedQuoteItem),
  sections: toMixedQuoteSections(mixedSectionsRef.current, committedLines),
}),
```

`resetSession` y el camino de éxito ejecutan:

```javascript
replaceSections(createInitialMixedCartSections());
```

No persistir este estado en `localStorage` ni `sessionStorage`.

- [ ] **Step 5: Ejecutar pruebas API/UI y verificar paridad de API**

Run:

```powershell
python -m pytest tests/test_quote_jobs_api.py tests/test_mixed_catalog_cart_ui.py -q
python -c "from pathlib import Path; paths=[Path('mobiliti_saas/api/index.py'),Path('mobiliti_saas/web/api/index.py'),Path('vercel_deploy/api/index.py')]; assert len({p.read_bytes() for p in paths}) == 1"
```

Expected: PASS.

---

### Task 5: Bandeja accesible, responsive y verificación integral

**Files:**

- Modify: `mobiliti_saas/web/src/MixedCartDrawer.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`
- Modify: `tests/test_mixed_catalog_cart_ui.py`
- Modify: `tests/test_mixed_catalog_browser_e2e.py`
- Modify: `tests/test_mixed_catalog_quote_e2e.py`
- Update: `docs/superpowers/specs/2026-07-20-manual-cart-sections-design.md` only if validation reveals a contract correction.
- Update through MCP: `armado-caratula/36-Diseno-carrito-secciones-manuales.md`.

**Interfaces:**

- Consumes: `sections`, labels y callbacks puros de Tasks 1 y 4.
- Produces: UI de tarjetas de sección, controles accesibles, un XLSX final en orden manual y bitácora de verificación.

- [ ] **Step 1: Escribir pruebas estáticas y de navegador rojas**

Exigir en el drawer:

- input `Concepto de la sección N`;
- botones `Subir`, `Bajar`, `Unir con la anterior` y `Cerrar sección y abrir otra`;
- selector `Mover <producto> a otra sección`;
- `aria-live="polite"` para cambios de estructura;
- ningún request directo.

En navegador: mezclar tres proveedores en Recepción, cerrar, renombrar la segunda a Privados, mover/reordenar productos y comprobar el JSON del POST y ausencia de overflow a 1440×1000 y 390×844.

- [ ] **Step 2: Ejecutar UI/E2E y confirmar controles ausentes**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_mixed_catalog_browser_e2e.py -k "section" -q
```

Expected: FAIL por controles ausentes.

- [ ] **Step 3: Renderizar secciones y productos**

`MixedCartDrawer` recibe `sections`, agrupa visualmente `lines` por `sectionId` sin alterar el arreglo original y renderiza cada sección con este patrón semántico:

```jsx
<section className="mixed-cart-section" aria-labelledby={`mixed-section-${section.id}`}>
  <div className="mixed-cart-section-header">
    <span aria-hidden="true">{index + 1}-</span>
    <label>
      <span className="sr-only">Concepto de la sección {index + 1}</span>
      <input value={section.concept} onChange={...} onBlur={...} disabled={busy} />
    </label>
  </div>
  {/* líneas, orden, selector de sección y acción reversible */}
</section>
```

El último bloque muestra **Cerrar sección y abrir otra** sólo cuando contiene productos. Las acciones de estructura llaman callbacks del controlador; el drawer no conoce API ni modifica precios.

- [ ] **Step 4: Aplicar estilos desktop/móvil sin nueva librería**

Agregar `.mixed-cart-section`, `.mixed-cart-section-header`, `.mixed-cart-order-controls`, `.mixed-cart-move-section` y `.mixed-cart-section-actions`. Todos los botones e inputs de estructura usan `min-width` o `min-height: 44px`, `:focus-visible`, contraste existente y texto que no se corta. En `@media (max-width: 720px)` las líneas y acciones se apilan en una columna sin ancho fijo superior al viewport.

- [ ] **Step 5: Ejecutar UI, build y pipeline mixto**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_mixed_catalog_browser_e2e.py tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_workbook.py tests/test_quote_jobs_api.py tests/test_mixed_catalog_quote_e2e.py tests/test_mixed_quote_engine.py tests/test_mobiliti_capacity.py -q
Push-Location mobiliti_saas\web
npm.cmd run build
Pop-Location
```

Expected: todas las pruebas PASS y build Vite exitoso.

- [ ] **Step 6: Ejecutar verificación final de sintaxis y diff**

Run:

```powershell
python -m compileall -q mobiliti_saas tests
git diff --check
git status --short
```

Expected: compilación exitosa, sin errores de whitespace y sólo cambios conocidos. No stagear los archivos solapados con trabajo previo.

- [ ] **Step 7: Registrar la Iteración 4 en Obsidian**

Actualizar por MCP la nota `armado-caratula/36-Diseno-carrito-secciones-manuales.md` con archivos cambiados, pruebas ejecutadas, resultado del build, evidencia visual, estado local y confirmación explícita de que producción no fue modificada.
