# Sunon CDMX V1C Third Template Profile Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Agregar en local un tercer formato seleccionable basado en `Formato-Cotizacion-Unico - Sunon-Cdmx-V1C.xlsx`, conservando la misma lógica, datos, fórmulas vivas y referencias del flujo oficial. Las únicas diferencias funcionalmente permitidas son la presentación de `Cotizacion` y la hoja adicional `Cantidades Lumbro`.

**Architecture:** Introducir un registro cerrado de perfiles de plantilla. El perfil oficial conserva exactamente su activo, contrato y compositor actuales. El perfil `sunon_cdmx_v1c` resuelve a un activo y contrato propios, reutiliza la normalización canónica del motor y aplica un compositor de presentación CDMX solamente al final. La hoja `Quotation` importada y la representación canónica siguen siendo las fuentes de verdad; nunca se reutilizan los datos de muestra, tasas fijas ni valores precargados del archivo recibido.

**Tech Stack:** Python 3.14, FastAPI/Pydantic, React, openpyxl/OOXML, Microsoft Excel COM para validación local, pytest.

**Implementation note after workbook inspection:** the received CDMX workbook is
not a drop-in engine template because it lacks required technical sheets and
uses different row anchors. The approved B implementation therefore builds a
separate CDMX asset from the immutable official workbook, applies the CDMX
presentation to the existing official geometry, and keeps the allowlisted
official compositor. Profile awareness selects the matching asset and contract;
it does not introduce a second business-logic compositor. This preserves exact
functional parity while limiting differences to presentation and the additional
`Cantidades Lumbro ` surface.

---

## Restricciones aprobadas

1. Trabajo exclusivamente local.
2. No ejecutar `git commit`, `git push`, despliegues ni cambios de infraestructura.
3. No modificar el archivo oficial `mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx`.
4. No cambiar el comportamiento del perfil oficial.
5. No copiar los productos de muestra ni la tasa fija `/18` de la plantilla CDMX recibida.
6. Fallar antes de encolar o generar si el ID de plantilla no está registrado o si el activo/contrato no coincide.
7. Validar ambos formatos con exactamente el mismo input y los mismos datos de cotización.

## Criterios de éxito

- El selector muestra tres opciones y persiste un ID estable, no una ruta suministrada por el cliente.
- Los jobs antiguos o sin perfil continúan usando el formato oficial.
- El formato oficial conserva su SHA-256 previo y sus pruebas siguen verdes.
- El formato CDMX conserva las hojas técnicas y reglas de cálculo oficiales.
- `Cotizacion` CDMX muestra el diseño nuevo, con referencias vivas a datos canónicos.
- `Cantidades Lumbro` existe solo en el perfil CDMX y sus cantidades, costos y totales son referencias vivas, sin tasa hardcodeada.
- La `Quotation` de entrada sustituye cualquier hoja de muestra del activo CDMX.
- Ambos archivos abren en Excel sin reparación, recalculan y conservan sus relaciones OOXML e imágenes.
- La comparación E2E confirma las mismas secciones, productos, orden, códigos, cantidades, proveedores, dimensiones, categorías, costos y totales.

### Task 1: Congelar la plantilla oficial y crear pruebas rojas del registro

**Files:**
- Create: `tests/test_template_profiles.py`
- Modify: `tests/test_official_template_contract.py`
- Test: `tests/test_template_profiles.py`
- Test: `tests/test_official_template_contract.py`

**Step 1: Record the official asset fingerprint**

Calcular el SHA-256 actual de:

```text
mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx
```

Guardar el valor esperado en la prueba de inmutabilidad. La prueba debe fallar si el archivo cambia durante esta implementación.

**Step 2: Write the failing profile-registry tests**

Exigir:

- `official_2026_gdl` resuelve al activo y contrato oficiales actuales;
- aliases históricos (`Formato Cotizacion 2026 GDL (1).xlsx` y el valor vacío) resuelven al perfil oficial;
- `sunon_cdmx_v1c` resuelve a un activo/contrato independiente;
- IDs o nombres desconocidos fallan con un error explícito;
- ningún perfil acepta rutas arbitrarias enviadas por el navegador.

**Step 3: Run the focused tests and confirm RED**

```powershell
python -m pytest tests/test_template_profiles.py tests/test_official_template_contract.py -q
```

Expected: los casos CDMX/registro fallan porque el registro todavía no existe; las cuatro pruebas oficiales existentes siguen pasando.

### Task 2: Implementar el registro estable de perfiles

**Files:**
- Create: `mobiliti_saas/quote_engine/template_profiles.py`
- Create: `mobiliti_saas/web/mobiliti_saas/quote_engine/template_profiles.py`
- Modify: `mobiliti_saas/quote_engine/__init__.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/__init__.py`
- Test: `tests/test_template_profiles.py`

**Step 1: Add an immutable profile model**

Crear un `TemplateProfile` inmutable con:

- `id`;
- `display_name`;
- `template_path`;
- `contract_path`;
- `composer_variant`;
- aliases permitidos.

**Step 2: Add a closed resolver**

Implementar `resolve_template_profile(value)`:

- normaliza únicamente IDs/aliases conocidos;
- usa `official_2026_gdl` como fallback para jobs antiguos sin valor;
- rechaza rutas, `..`, separadores y cualquier valor desconocido;
- valida que el activo y contrato existan antes de devolver el perfil.

**Step 3: Keep canonical and web mirrors equivalent**

Copiar el módulo de forma equivalente al espejo web y agregar una prueba de paridad si ya existe el patrón en el repositorio.

**Step 4: Run the focused tests**

```powershell
python -m pytest tests/test_template_profiles.py -q
```

Expected: PASS.

### Task 3: Exponer y persistir el tercer formato sin alterar jobs antiguos

**Files:**
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/worker/quote_worker.py`
- Modify: `tests/test_quote_template_selection.py`
- Test: `tests/test_quote_template_selection.py`

**Step 1: Write failing API/worker tests**

Cubrir:

- UI/API envían y almacenan `official_2026_gdl` o `sunon_cdmx_v1c`;
- alias oficial histórico se canonicaliza;
- ID desconocido produce 4xx antes de crear el job;
- worker usa el perfil guardado por el job;
- job sin `template` usa el perfil oficial.

**Step 2: Update the UI selector**

Mostrar:

- `Formato Cotización 2026 GDL (1)` → `official_2026_gdl`;
- la opción corporativa histórica, conservando su comportamiento actual si todavía está soportada;
- `Formato Cotización Único - Sunon CDMX V1C` → `sunon_cdmx_v1c`.

No enviar nombres de archivo ni rutas locales.

**Step 3: Canonicalize at both API entrypoints**

Validar y persistir el ID estable en:

- carga directa;
- importación a Proyecto;
- generación desde Proyecto;
- reintentos de jobs existentes.

**Step 4: Resolve the job profile in the worker**

Sustituir la ruta fija del worker por la resolución del perfil del job. Mantener fallback oficial para registros antiguos.

**Step 5: Run the focused test**

```powershell
python -m pytest tests/test_quote_template_selection.py -q
```

Expected: PASS.

### Task 4: Construir el activo CDMX independiente y su contrato

**Files:**
- Create: `mobiliti_saas/worker/templates/Formato Cotizacion Sunon CDMX V1C.xlsx`
- Create: `mobiliti_saas/worker/templates/formato-cotizacion-sunon-cdmx-v1c.contract.json`
- Create: `scripts/build_sunon_cdmx_v1c_template.py`
- Create: `tests/test_cdmx_template_contract.py`
- Test: `tests/test_cdmx_template_contract.py`

**Step 1: Write contract tests before building**

Exigir:

- el activo CDMX existe separado del oficial;
- contiene todas las hojas técnicas requeridas por el contrato oficial;
- contiene `Cotizacion` y `Cantidades Lumbro`;
- no contiene una `Quotation` de muestra como fuente de verdad;
- no contiene fórmulas con divisores fijos `/18`;
- su contrato permite mutar únicamente las hojas declaradas para el perfil;
- las anclas de presentación CDMX existen.

**Step 2: Build, do not hand-edit, the hybrid asset**

El script debe:

- partir de una copia segura del activo oficial;
- transplantar únicamente la presentación de `Cotizacion` y `Cantidades Lumbro` desde el archivo recibido;
- conservar las hojas técnicas, nombres definidos y cálculos oficiales;
- limpiar datos de muestra;
- preservar imágenes, merges, dimensiones, estilos y relaciones necesarias;
- producir de forma determinista el activo local CDMX.

El script no se ejecutará en producción ni modificará el activo oficial.

**Step 3: Create the CDMX contract**

Declarar:

- firma propia del activo;
- hojas técnicas obligatorias;
- hojas mutables `Mobiliti`, `Cotizacion` y `Cantidades Lumbro`;
- hojas añadibles `Quotation` y `Quotation_Data` solo si el flujo canónico aún las requiere;
- anclas y firmas estructurales específicas.

**Step 4: Run contract tests**

```powershell
python -m pytest tests/test_cdmx_template_contract.py tests/test_official_template_contract.py -q
```

Expected: PASS y el hash oficial permanece idéntico.

### Task 5: Hacer el motor consciente del perfil

**Files:**
- Modify: `mobiliti_saas/quote_engine/engine.py`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/engine.py`
- Create: `tests/test_template_profile_engine.py`
- Test: `tests/test_template_profile_engine.py`
- Test: `tests/test_quote_engine_golden.py`

**Step 1: Write failing profile-dispatch tests**

Exigir:

- perfil oficial sigue invocando el compositor oficial sin cambios;
- perfil CDMX invoca el mismo compositor oficial allowlisted sobre el activo
  normalizado CDMX;
- activo y contrato siempre pertenecen al mismo perfil;
- la `Quotation` proporcionada reemplaza cualquier muestra;
- el motor rechaza mezclas de activo oficial con contrato CDMX.

**Step 2: Pass the resolved profile through the engine**

Eliminar la carga incondicional del contrato oficial. El motor empareja cada
activo registrado con su propio contrato. Las copias exactas del activo oficial
usadas por pruebas o herramientas internas continúan validándose con el contrato
oficial; ningún path proporcionado por el cliente se acepta.

**Step 3: Preserve the official path**

No cambiar las fórmulas, allowlist ni disposición del compositor oficial. No se
crea un segundo compositor de lógica de negocio: la selección de perfil cambia
únicamente el par activo/contrato.

**Step 4: Run golden regressions**

```powershell
python -m pytest tests/test_quote_engine_golden.py tests/test_official_template_contract.py -q
```

Expected: PASS.

### Task 6: Verificar `Cotizacion` y `Cantidades Lumbro` CDMX con referencias vivas

**Files:**
- Modify: `scripts/build_sunon_cdmx_v1c_template.py`
- Modify: `tests/test_cdmx_template_contract.py`
- Create: `tests/test_cdmx_template_semantics.py`
- Test: `tests/test_cdmx_template_contract.py`
- Test: `tests/test_cdmx_template_semantics.py`

**Step 1: Write failing semantic tests**

Para un fixture pequeño y uno con productos Lumbro, exigir:

- códigos y cantidades referencian la representación canónica/Mobiliti;
- descripción transformada, medida y costo conservan sus fuentes vivas;
- secciones, orden y subtotales coinciden con el perfil oficial;
- fórmulas de descuentos, IVA y totales son equivalentes;
- `Cantidades Lumbro` lista únicamente las ocurrencias Lumbro;
- cantidades y costos Lumbro son fórmulas, no constantes;
- no aparece `18`, `18.5` ni otra tasa fija como conversión;
- las imágenes quedan centradas y dentro de sus celdas;
- las filas vacías conservan el estilo del formato.

**Step 2: Build the CDMX presentation into official geometry**

El constructor local deriva el activo CDMX de la plantilla oficial inmutable y
aplica solamente la presentación recibida sobre los anchors oficiales. El motor
continúa usando la representación canónica y el compositor oficial.

**Step 3: Preserve dynamic capacity**

La capacidad dinámica existente del compositor oficial debe permanecer activa
sin un branch CDMX. Las filas nuevas heredan el arquetipo visual CDMX incluido
en el activo normalizado.

**Step 4: Build live Lumbro references**

Sanear la superficie `Cantidades Lumbro ` dentro del activo CDMX y construir sus
referencias con fórmulas hacia las hojas canónicas permitidas, sin datos de
muestra, vínculos externos ni tasa fija.

**Step 5: Run focused compositor tests**

```powershell
python -m pytest tests/test_cdmx_template_contract.py tests/test_cdmx_template_semantics.py -q
```

Expected: PASS.

### Task 7: Ejecutar E2E pareado con el mismo input

**Files:**
- Create: `tests/test_template_variant_e2e.py`
- Create: `artifacts/template-variant-e2e/README.md`
- Test: `tests/test_template_variant_e2e.py`

**Step 1: Use one stable real quotation**

Usar:

```text
C:\Users\pepem\Downloads\IZA REFORMA-Quotation Sheet - V1.xlsx
```

con exactamente el mismo payload de cliente, proyecto, descuento, moneda y opciones para ambos perfiles.

**Step 2: Generate both outputs**

Crear artefactos locales separados:

- `official-output.xlsx`;
- `sunon-cdmx-v1c-output.xlsx`.

No incorporarlos a Git.

**Step 3: Compare semantic output**

Assert igualdad de:

- cantidad y nombres de secciones;
- cantidad y orden de líneas;
- código;
- cantidad;
- proveedor;
- categoría;
- dimensiones;
- costo base;
- moneda y tasa registrada;
- subtotales y total final.

Permitir diferencias únicamente en:

- estructura/presentación de `Cotizacion`;
- presencia y contenido de `Cantidades Lumbro`.

**Step 4: Audit OOXML**

Verificar:

- ZIP íntegro;
- sin relaciones rotas;
- sin nombres definidos con destinos inexistentes;
- sin fórmulas `#REF!`;
- imágenes y drawing relationships válidos;
- la firma de la `Quotation` trasplantada coincide entre ambos outputs.

**Step 5: Open and recalculate in desktop Excel**

Para cada archivo:

- abrir con Excel COM;
- ejecutar `CalculateFullRebuild`;
- guardar en una copia de validación;
- cerrar y reabrir;
- confirmar que Excel no muestra reparación;
- comprobar errores de fórmula visibles en celdas pobladas.

**Step 6: Run the E2E test**

```powershell
python -m pytest tests/test_template_variant_e2e.py -q
```

Expected: PASS.

### Task 8: Regresión completa y evidencia

**Files:**
- Modify: `armado-caratula/61-Tercer-formato-Sunon-CDMX-V1C-diseno-2026-07-29.md` through Obsidian MCP only
- Create: `artifacts/template-variant-e2e/comparison.json`
- Create: `artifacts/template-variant-e2e/validation.md`

**Step 1: Run the regression matrix**

```powershell
python -m pytest tests/test_official_template_contract.py tests/test_quote_engine_golden.py -q
python -m pytest tests/test_template_profiles.py tests/test_quote_template_selection.py tests/test_cdmx_template_contract.py tests/test_cdmx_composer.py tests/test_template_variant_e2e.py -q
python -m pytest tests/test_project_quote_acceptance.py tests/test_official_quote_stress.py tests/test_quotation_sheet_transplant.py -q
```

**Step 2: Verify the official asset did not change**

Comparar el SHA-256 final con el valor congelado en Task 1.

**Step 3: Record evidence in Obsidian**

Agregar:

- archivos modificados/creados;
- hashes de ambos activos;
- comandos y resultados de tests;
- conteos E2E;
- diferencias permitidas observadas;
- resultado de apertura/recalculo en Excel;
- declaración explícita de que no hubo commit, push ni deploy;
- riesgos residuales o bloqueo, si existiera.

**Step 4: Report completion**

La entrega solo se considera completa si ambos perfiles generan un `.xlsx` válido con el mismo input y la plantilla oficial permanece byte a byte intacta.
