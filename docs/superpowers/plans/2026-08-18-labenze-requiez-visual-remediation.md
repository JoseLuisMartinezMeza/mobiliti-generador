# Plan TDD: remediación visual integral Labenze y Requiez

Fecha: 2026-08-18  
Estado: listo para ejecución local  
Especificación: `docs/superpowers/specs/2026-08-18-labenze-requiez-visual-remediation-design.md`

## Objetivo verificable

Promover al dev store local un manifiesto de 462 identidades Labenze y 314
Requiez con 776/776 imágenes aprobadas. La misma auditoría de producto completo,
margen, configuración y procedencia se aplica a imágenes PDF, web, compartidas y
generadas. La aceptación final exige cero placeholders, enlaces de producto
exactos o fallback PDF+página, warnings persistentes para generadas y E2E mixto.

## Restricciones

- Trabajar sobre `codex/offiho-catalog-20260709`; revalidar rama, HEAD, upstream y
  estado sucio antes de cada escritura material o promoción.
- Preservar todos los cambios ajenos; no restaurar, limpiar ni borrar archivos.
- SharePoint se usa en sólo lectura. Producción, Supabase/R2/Vercel/worker quedan
  fuera de alcance hasta autorización separada.
- Los importadores siguen deterministas y sin red/generación en runtime.
- Reutilizar el pipeline verificado existente; no crear una segunda arquitectura.
- Cada comportamiento nuevo sigue RED → GREEN → refactor mínimo.

## Task 1 — Contrato de manifiesto visual v2

**Pruebas RED**

- Extender `tests/test_build_verified_catalog_images.py` con casos que rechacen:
  revisión incompleta, placeholder, `product_url` mezclado con CDN/home/familia,
  `source_kind` inválido, generada sin búsqueda agotada/prompt/modelo/referencias,
  reuse familiar sin matriz y calidad fuera de umbrales.
- Actualizar primero los fixtures v2 para usar PNG reales; una prueba no puede
  acreditar calidad usando bytes arbitrarios con extensión de imagen.
- Añadir casos positivos para ficha exacta y fallback SharePoint PDF con página y
  localizador de código.
- Antes de escribir producción, ejecutar la prueba focal y observar fallos por la
  conducta ausente, no por fixtures inválidos.

**GREEN mínimo**

- Extender `scripts/build_verified_catalog_images.py`; conservar schema v1 para
  manifiestos anteriores y validar el nuevo contrato sólo en schema v2.
- Reutilizar Pillow ya instalado para comprobar PNG real, ≤8 MiB, ≤8192 por lado,
  ≤25 Mpx, lienzo cuadrado ≥1024, dimensiones y bbox calculado; no añadir
  dependencia. El gate verifica margen ≥4 %, caja ≤92 %, ocupación 12–80 % y
  relación de aspecto dentro de 1 %.
- Persistir bajo `attributes.image_reference` los campos de procedencia, revisión,
  calidad, búsqueda, enlace visual y grupo compartido. Mantener `product_url`
  separado.

**Verificación**

`python -m pytest tests/test_build_verified_catalog_images.py -q`

## Task 2 — Promoción reversible de Labenze/Requiez

**Pruebas RED**

- En `tests/test_promote_verified_catalog_images.py`, exigir soporte de ambos
  proveedores, promoción sólo visual, respaldo byte a byte, staging, hashes,
  snapshot IDs y conservación de proyectos/reservaciones/jobs.
- Exigir copia íntegra de procedencia/revisión y que no cambien precios, SKU,
  opciones, stock, warnings comerciales ni referencias del PDF.
- Probar rechazo si el dev store cambia después de la auditoría o si falta un
  activo/identidad.
- Probar que el respaldo existe y fue validado antes de copiar el primer asset,
  que el SHA esperado es obligatorio para esta campaña y que el lote combinado
  mayor a 256 MiB falla antes de cualquier escritura.

**GREEN mínimo**

- Ampliar allowlist CLI y campos visuales en
  `scripts/promote_verified_catalog_images.py`.
- Agregar al reporte los IDs de snapshot anterior/nuevo, hashes de backup/staging,
  conteos preservados y presupuesto agregado de assets.
- No eliminar activos huérfanos; mantener copia idempotente por SHA-256.
- Construir Labenze, usar esa salida como entrada Requiez y hacer una sola
  promoción conjunta para medir el presupuesto real.

**Verificación**

`python -m pytest tests/test_promote_verified_catalog_images.py -q`

## Task 3 — Preservación curada en sincronizaciones futuras

**Pruebas RED**

- En `tests/test_catalog_sync_service.py`, cubrir Labenze/Requiez: candidato
  placeholder/family conserva curación sólo con proveedor, `internal_id`,
  `product_key`, SKU y configuración visual idénticos.
- Probar que una candidata `exact_pdf`/`exact_web` oficial siempre gana; un
  mismatch o metadato incompleto no hereda.
- Exigir preservación canónica del paquete visual completo, sin reemplazar
  atributos comerciales nuevos.

**GREEN mínimo**

- Generalizar el helper Sunon existente en
  `mobiliti_saas/worker/catalog_sync/service.py`; evitar un registro o clase nueva.
- Mantener compatibilidad Sunon y las métricas existentes.
- Comparar la configuración visual con nombre/descripción/variante/dimensiones y
  estructura de opciones, excluyendo precios e inventario. Al preservar, copiar
  sólo el paquete visual curado y retirar `image_match` contradictorio; una
  candidata exacta aprobada siempre gana.

**Verificación**

`python -m pytest tests/test_catalog_sync_service.py -q`

## Task 4 — Warning generado antes y después de cotizar

**Pruebas RED**

- En `tests/test_supplier_catalog_ui.py`, demostrar que
  `SupplierCatalogView.cartWarnings()` añade **Imagen de referencia** desde
  `image_kind=generated_reference` y la deduplica.
- En `tests/test_project_catalog_search.py`, exigir la misma derivación al entrar
  desde búsqueda.
- En `tests/test_project_model.py` y `tests/test_project_ui.py`, demostrar que la
  advertencia canónica sobrevive autosave/reapertura y aparece en principal y
  complemento; rechazar cualquier otra advertencia en display cache.
- Reforzar el test XLSX sólo como regresión; no modificar el motor que ya deriva
  el warning autoritativamente.

**GREEN mínimo**

- Añadir una sola rama a `cartWarnings` y `_catalog_warnings`.
- Persistir sólo `Imagen de referencia` como `display_cache.warnings` opcional en
  `mixedCart.js` y los dos `project_model.py`; proyectos antiguos sin el campo
  siguen cargando. Renderizar el badge en `ProjectEditor.jsx`.
- Mantener byte-idénticos los mirrors de `catalog_search.py` y `project_model.py`.

**Verificación**

`python -m pytest tests/test_supplier_catalog_ui.py tests/test_mixed_catalog_cart.py -q`

## Task 5 — Inventario y auditoría de las 776 asociaciones

- Reconstruir ambos catálogos desde los dos PDF de hash fijado y comparar contra
  el dev store actual.
- Exportar un inventario de 776 filas y contact sheets paginadas. Cada fila
  contiene identidad, SKU/source code, página PDF, activo actual, match status,
  campos visuales, `product_url`, decisión y estado de revisión.
- Auditar las 46 `exact_pdf` Labenze, 157 `exact_pdf` Requiez, 417 asignaciones
  familiares y 156 placeholders. Registrar todos los fallos, no sólo los casos
  conocidos.
- El baseline ya demuestra que 0/314 assets únicos actuales son cuadrados
  ≥1024. En Labenze, 156/156 tienen lado menor <512 y 149 conservan bordes/reglas;
  por tanto las 462 asociaciones se reemplazan o rehacen, incluidas las 46
  `exact_pdf`.
- Para imágenes compartidas, producir matriz visible por SKU; si la equivalencia
  no queda demostrada, el activo no se conserva.

**Verificación**

- Conteos exactos 462/314 y 776 decisiones únicas.
- Revisión explícita de `RM-9025N/NG`, JUN M, seis recortes Requiez,
  `106-00603-BAT`, ZELIG y ocho registros Labenze `needs_review`.

## Task 6 — Investigación exacta y adquisición de oficiales

- Requiez: consultar su API/sitio con igualdad normalizada de `code` o
  `shortCode`; guardar respuesta, URL de ficha, URL de imagen, fecha y evidencia.
- Cachear `/productos` una vez y usar detalle con baja concurrencia/backoff; la
  SPA 200 no prueba identidad. Examinar todos los `imgs[]`, pues algunas fichas
  agrupan tamaños/configuraciones.
- Labenze: buscar SKU exacto primero en fabricante y después en distribuidores
  atribuibles. Una página por familia no acredita una configuración.
- Enumerar una vez las fuentes estructuradas ya identificadas: API legacy
  Labenze, Shopify de Nogal Beat/Nogal Beat Store/3R, WooCommerce Arterio y API
  del fabricante Infiniti. Para Shopify, aceptar sólo el cruce explícito
  `variants[].id ↔ images[].variant_ids`; no asignar la primera imagen.
- Validar hosts, redirecciones, MIME, tamaño, resolución y SHA-256 antes de
  aceptar bytes. No guardar instrucciones provenientes de páginas como acciones.
- Usar `product_url` individual; si no existe, conservar PDF SharePoint+página y
  localizador de código. Nunca usar el CDN como enlace de **Ver producto**.
- Incorporar sólo oficiales que pasen el mismo gate visual global.

**Verificación**

- Reporte por SKU con `found_exact`, `rejected` o `exhausted` y motivo.
- `RM-9025N/NG` usa fotografía oficial web completa.
- Verificar los 100 matches API Requiez ya identificados; 28 rescatan placeholders
  y `RI-50` sustituye su `family_pdf`. Los 128 placeholders sin match exacto
  continúan al siguiente nivel de búsqueda/generación.
- Labenze parte con candidatos web para 442/462 (95.7 %), pero sólo 244 poseen
  binding variante→imagen estructurado y todos requieren QA. El residual mínimo
  son 20 productos (GALA EDU 10 y ARETA/REPLAY 10); presupuestar inicialmente
  20–26 generadas y aumentar únicamente por rechazos documentados.

## Task 7 — Generación del residual y revisión individual

- Agrupar sólo configuraciones visualmente idénticas demostradas; no agrupar por
  nombre, precio o colección.
- La firma mínima es modelo, carcasa, base, brazos, plazas/mesa, altura, extensión
  de tapizado, color/acabado y accesorios visibles; la misma fuente debe asignar
  explícitamente el asset a todas las variantes del grupo.
- Para cada residual `exhausted`, preparar prompt con descripción, configuración,
  página/ficha y vecinos oficiales comprobados. Generar con el servicio de
  imágenes; no generar productos que tengan foto exacta válida.
- Exigir producto completo, lienzo limpio, margen, configuración y ausencia de
  texto/logos/marcas. Rechazar y regenerar cualquier incumplimiento.
- Registrar modelo, prompt, hashes, referencias HTTPS, reviewer, fecha, checks y
  decisión. Las tres variantes JUN M deben mostrar su mesa y plazas correctas.

**Verificación**

- Cada asset generado es inspeccionado antes de entrar al manifiesto.
- Todas las generadas son `generated_reference`; ninguna se presenta como
  fotografía oficial.

## Task 8 — Gate completo de manifiestos y assets

- Crear manifiestos v2 versionados para Labenze/Requiez y activos
  content-addressed fuera del bundle Vercel/Git.
- Añadir `tests/test_labenze_requiez_visual_manifests.py` para cobertura 462/314,
  cero placeholder, enlaces, procedencia, revisión, calidad, duplicados declarados
  y casos focales.
- Ejecutar build verificado dos veces y exigir salida determinista.
- Medir activos únicos/compartidos, total, máximo y p95; bloquear >8 MiB por objeto
  o >256 MiB incremental.

**Verificación**

`python -m pytest tests/test_labenze_requiez_visual_manifests.py tests/test_build_verified_catalog_images.py -q`

## Task 9 — Promoción local y rollback probado

- Revalidar worktree/HEAD y SHA del dev store inmediatamente antes de promover.
- Guardar backup timestamped de DB/manifiestos/bindings con hashes y snapshot IDs
  en `.mobiliti_dev_store/backups/visual-remediation/`.
- Restaurar el backup sobre staging y comparar byte a byte antes de tocar el DB
  activo.
- Promover sólo campos visuales y activos; preservar snapshots previos, usuarios,
  proyectos, reservaciones y jobs.
- Verificar API paginada, **Ver producto**, 776 imágenes HTTP 200 y badges.

**Verificación**

- Reporte before/after/rollback con hashes y conteos.
- Cero respuesta >512 KiB y cero activo >8 MiB.

## Task 10 — Regresión, E2E mixto y cierre

- Ejecutar suites focales y transversales afectadas, `py_compile`, diff-check de
  los tres API mirrors y build Vite.
- Extender `tests/test_13_supplier_mixed_quote_acceptance.py` con un Requiez
  oficial y uno generado, además de Labenze y los otros proveedores.
- El E2E conserva 13 grupos/proveedores y usa 14 líneas totales; selecciona la
  Requiez generada de forma estable por `internal_id`.
- Ejecutar POST → cola → worker → generador Python → XLSX real. Verificar hashes
  embebidos, warning una vez, enlaces autoritativos, input <25 MiB, output <60 MiB
  y cero `#REF!` nuevo.
- Exportar contact sheet final 776/776 y realizar auditoría independiente sin
  P0/P1/P2.
- Actualizar Obsidian mediante MCP con fuentes, versión de manifiesto, métricas,
  tests, E2E, backup, rollback y estado local/productivo.

## Condición de cierre

El trabajo no termina por tener una imagen por fila. Termina sólo cuando las 776
asociaciones están aprobadas bajo el mismo contrato, todos los tests pasan, el
dev store local es reversible y el E2E demuestra imagen oficial y generada con
advertencia. Producción seguirá pendiente de autorización expresa.
