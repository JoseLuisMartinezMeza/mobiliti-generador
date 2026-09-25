# Diseño: SPEC GUIDE de IDÉLIKA e integración local de IDÉLIKA y Conceptos

**Fecha:** 2026-08-02

**Estado:** diseño aprobado en conversación; pendiente de revisión documental final
**Alcance:** generación auditable del SPEC GUIDE de IDÉLIKA e integración local de los catálogos IDÉLIKA y Conceptos

## Objetivo

Incorporar dos proveedores al recorrido funcional genérico de catálogos del
cotizador sin alterar los catálogos existentes ni desplegar cambios a
producción:

1. Crear primero un libro `Spec guide-IDELIKA-2026.xlsx` a partir de los tres
   PDF oficiales de IDÉLIKA.
2. Validar visual y estructuralmente ese libro antes de usarlo como fuente.
3. Integrar IDÉLIKA y Conceptos como catálogos consultables, configurables,
   agregables a Proyecto y cotizables.
4. Conservar la semántica financiera del sistema: los importes publicados por
   los catálogos son costos de proveedor y el incremento comercial se aplica
   únicamente mediante las fórmulas oficiales del Excel de cotización.

## Decisiones aprobadas

1. La implementación se realiza solo en local. No se hará commit, push,
   despliegue ni mutación de producción.
2. IDÉLIKA y Conceptos usan MXN como moneda de origen.
3. En IDÉLIKA, cuando una publicación muestra dos importes para el mismo
   producto o variante, el menor se registra como costo interno y el mayor
   como precio de referencia.
4. En Conceptos, el costo se toma exclusivamente de la columna E de la hoja
   `Costo Sofas - Cdmx-Gdl-Qro`; el precio de venta de la columna G es solo
   referencia y nunca alimenta `price_net`.
5. Los productos de `School Series` sin precio publicado se registran como
   `Precio por confirmar`, permanecen cotizables y nunca se convierten en un
   costo numérico cero.
6. Las filas sin código oficial no reciben códigos inventados. Se les asigna
   una identidad técnica estable y se muestran con advertencia de código por
   verificar, conservando el mecanismo vigente de cotización por confirmar.
7. Las variantes solo se agrupan cuando el documento fuente demuestra que
   pertenecen al mismo producto o bloque. La similitud visual o textual por sí
   sola no autoriza una fusión.
8. Los catálogos existentes y el motor de generación de cotizaciones quedan
   intactos salvo por los puntos de extensión genéricos necesarios para
   registrar los dos proveedores nuevos.

## Fuentes oficiales

### IDÉLIKA

Carpeta SharePoint `LISTAS DE PRECIOS PROVEEDORES/IDELIKA`:

- `1 CATALOGO FABRICACION 2026B.pdf`
- `2 CATALOGO STOCK 2026.pdf`
- `4 SCHOOL SERIES 2026.pdf`

`TEQUILA LOVE.pdf` queda fuera de alcance porque no forma parte del conjunto
de productos solicitado para este SPEC GUIDE.

Cada fila del SPEC GUIDE conservará archivo, página, texto de precio original
y URL de procedencia para permitir auditoría.

### Conceptos

Fuente SharePoint:
`Spec guide - Conceptos - Sofas - CdMx - Gdl - Qro - 2021.xlsx`.

Hojas relevantes:

- `Costo Sofas - Cdmx-Gdl-Qro`: fuente canónica del costo.
- `Spec sofas - Cdmx-Gdl-Qro`: referencia comercial y contraste visual.

El importador debe soportar celdas combinadas y valores que continúan hacia
abajo dentro de un bloque comprobado, sin arrastrarlos a la siguiente familia.

## Fase 1: libro SPEC GUIDE de IDÉLIKA

### Archivo y ubicación

El artefacto se generará como:

`outputs/019f7907-1ecc-7001-b3f3-8eb209086fa8/Spec guide-IDELIKA-2026.xlsx`

Se usará `@oai/artifact-tool`; no se escribirá el libro mediante `openpyxl` o
scripts ad hoc. Todas las hojas se inspeccionarán, recalcularán, validarán y
renderizarán antes de aceptar el resultado.

### Hojas

1. `Consolidado`: una fila por producto o variante publicable.
2. `Fabricacion`: evidencia normalizada de `1 CATALOGO FABRICACION 2026B.pdf`.
3. `Stock`: evidencia normalizada de `2 CATALOGO STOCK 2026.pdf`.
4. `School Series`: evidencia normalizada de `4 SCHOOL SERIES 2026.pdf`.
5. `Fuentes_Reglas`: fuentes, reglas de precio, moneda, excepciones y fecha de
   extracción.

### Columnas canónicas

El consolidado incluye como mínimo:

| Columna | Uso |
|---|---|
| `Proveedor` | `IDÉLIKA` |
| `Subcatalogo` | `Fabricacion`, `Stock` o `School Series` |
| `Archivo_origen` | nombre exacto del PDF |
| `Pagina_origen` | página física del PDF |
| `Clave_estable` | identificador técnico reproducible |
| `SKU` | código oficial cuando exista |
| `Estado_codigo` | `oficial` o `por_verificar` |
| `Producto` | nombre comercial normalizado |
| `Familia` | agrupación demostrada por el documento |
| `Variante` | acabado, color, tamaño u opción explícita |
| `Material` | material publicado |
| `Medidas` | dimensiones de origen, sin reinterpretarlas |
| `Descripcion` | descripción limpia para catálogo |
| `Unidad` | unidad de venta |
| `Costo_MXN` | costo canónico; vacío cuando está pendiente |
| `Precio_referencia_MXN` | importe mayor o precio comercial de contraste |
| `Precio_original` | texto de precio tal como se publicó |
| `Estado_precio` | `confirmado` o `por_confirmar` |
| `Cotizable` | `Sí` para todos los productos aprobados |
| `Minimo_compra` | mínimo explícito, si existe |
| `Imagen_referencia` | vínculo o evidencia de la imagen oficial |
| `URL_fuente` | vínculo SharePoint del documento |
| `Identidad_hash` | huella estable de procedencia e identidad |
| `Notas` | advertencias no destructivas |

### Reglas de precios IDÉLIKA

- Dos importes aplicables al mismo producto o variante:
  `Costo_MXN = MIN(importe_1, importe_2)` y
  `Precio_referencia_MXN = MAX(importe_1, importe_2)`.
- Un solo importe inequívoco: se usa como costo; la referencia queda vacía
  salvo que el rótulo de origen indique expresamente que es precio de venta.
- Ningún importe: `Costo_MXN` vacío, `Estado_precio = por_confirmar` y
  `Cotizable = Sí`.
- Un texto ambiguo, ilegible o que parezca pertenecer a otra variante no se
  convierte automáticamente en precio. Se conserva en `Notas` y requiere
  revisión.
- El orden visual de los dos importes también se conserva en
  `Precio_original`; la regla del mínimo controla el costo incluso en pares
  invertidos como `$3,499 - $3,999`.

### Fórmulas y controles del libro

La hoja `Consolidado` incluirá fórmulas visibles para:

- total de filas publicables;
- costos confirmados y pendientes;
- códigos oficiales y por verificar;
- duplicados exactos de identidad;
- conflictos de una misma identidad con costos distintos;
- conteo por subcatálogo.

Las fórmulas se escribirán como fórmulas de Excel, no como resultados
hardcodeados. Las columnas monetarias usarán formato MXN; las tablas tendrán
filtros, paneles congelados, encabezados legibles y anchos moderados.

## Fase 2: contrato de sincronización

### Proveedores y adaptadores

Se añadirán dos adaptadores independientes:

- `idelika`: consume el SPEC GUIDE generado y validado.
- `conceptos`: consume directamente las hojas canónicas del XLSX oficial.

Ambos producirán el mismo contrato público usado por JOME, Lauco, ALMA y los
demás catálogos:

- `internal_id`
- `supplier`
- `product_key`
- `sku`
- `code_status`
- `brand`
- `collection`
- `name`
- `description`
- `unit`
- `availability_type`
- `stock`
- `lead_time`
- `base_price_options`
- `add_on_options`
- `base_currency`
- `price_net`
- `tax_rate`
- `attributes`
- `image_url`
- `image_kind`
- `product_url`
- `warnings`
- `source_reference`

### Identidad y variantes

IDÉLIKA usará una identidad basada en proveedor, subcatálogo, SKU o clave
estable, familia, variante y página de origen. Conceptos usará proveedor,
código normalizado y variante dentro del bloque de origen. La sincronización
debe ser determinista: la misma fuente produce las mismas identidades.

Los acabados, medidas o materiales con precios diferentes se publicarán como
opciones configurables cuando sean alternativas del mismo producto. Los
productos materialmente distintos permanecerán separados.

### Precio pendiente y cotización

Un producto con costo pendiente:

- aparece en búsqueda y filtros;
- puede agregarse al Proyecto;
- conserva `price_net = null` y `base_currency = MXN`;
- muestra `Precio por confirmar` y su advertencia;
- puede enviarse al generador de cotización;
- se representa con el mecanismo existente de precio por confirmar, nunca con
  `0`, ni se somete a una conversión o margen sobre cero.

## Fase 3: API, interfaz y Proyecto

La integración será aditiva en el registro genérico de fuentes y proveedores:

1. Registrar ambos orígenes en `catalog_sync/sources.json`.
2. Exponer `IDÉLIKA` y `Conceptos` en el catálogo y selector genérico.
3. Reutilizar búsqueda, filtros, paginación, vista previa, selección de
   configuraciones y acción `Agregar al Proyecto`.
4. Mostrar costo MXN para opciones confirmadas y `Por confirmar` para las
   pendientes.
5. Conservar proveedor, SKU, opción elegida, moneda y procedencia al guardar y
   reabrir el Proyecto.
6. Permitir que ambos proveedores participen en la cotización mixta sin crear
   un carrito separado ni una ruta especial del motor Excel.

No se creará un módulo paralelo de catálogo, Proyecto o cotización.

## Manejo de errores y publicación atómica

La construcción del snapshot debe terminar antes de reemplazar el catálogo
local publicado. Si el parser falla o el conjunto no supera las validaciones,
se conserva el snapshot local anterior.

Bloquean publicación:

- identidad duplicada con datos materiales incompatibles;
- precio numérico no positivo;
- moneda distinta de MXN sin una regla explícita aprobada;
- arrastre de código, imagen, descripción o precio fuera de su bloque;
- discrepancia Conceptos donde la columna G se intente usar como costo;
- ausencia de trazabilidad de archivo y fila o página.

No bloquean publicación, pero generan advertencia:

- precio ausente permitido de `School Series`;
- código oficial ausente con identidad técnica estable;
- imagen oficial ausente.

## Validación y pruebas

### SPEC GUIDE IDÉLIKA

- Inspeccionar rango, fórmulas y valores clave de cada hoja.
- Renderizar cada hoja y revisar encabezados, filtros, desbordes, formatos
  monetarios y legibilidad.
- Escanear errores de fórmula `#REF!`, `#DIV/0!`, `#VALUE!` y `#NAME?`.
- Verificar al menos un producto por PDF contra su página de origen.
- Verificar un par normal y el par invertido `$3,499 - $3,999`.
- Verificar que School Series produzca costo vacío, no cero, y siga cotizable.

### Importadores

- Snapshot determinista en dos ejecuciones consecutivas.
- IDÉLIKA publica el mínimo del par como costo y el mayor como referencia.
- Conceptos publica columna E como costo y conserva columna G solo como
  referencia.
- Las celdas combinadas de Conceptos se heredan únicamente dentro del bloque.
- Los productos sin código y sin precio conservan advertencias y trazabilidad.
- Las variantes explícitas no se pierden ni se duplican como productos falsos.

### Flujo funcional local

- Refrescar IDÉLIKA y Conceptos.
- Buscar y filtrar productos.
- Abrir vista previa y elegir una variante con precio.
- Agregar un producto confirmado y uno de School Series al mismo Proyecto.
- Guardar, cerrar y reabrir el Proyecto verificando las etiquetas elegidas.
- Generar una cotización mixta con productos de los nuevos proveedores y de un
  catálogo existente.
- Confirmar que el producto pendiente llega al Excel como `Por confirmar`.
- Ejecutar las pruebas existentes de los otros importadores como regresión.

## Criterios de aceptación

1. El SPEC GUIDE de IDÉLIKA existe, abre sin reparación y sus cinco hojas
   pasan validación estructural y visual.
2. Cada producto IDÉLIKA tiene procedencia de PDF y página.
3. IDÉLIKA usa el menor de los importes aplicables como costo MXN.
4. Conceptos usa exclusivamente la columna E de su hoja de costo.
5. School Series aparece como cotizable con precio por confirmar.
6. Las opciones de material, medida o acabado con costo propio son
   seleccionables y persisten en Proyecto.
7. Ambos catálogos participan en una cotización mixta local sin alterar los
   resultados de los catálogos existentes.
8. No hay commits, pushes, despliegues ni escrituras en producción.

## Fuera de alcance

- Publicación en producción.
- Commit o push de Git.
- Generación de imágenes nuevas mediante IA.
- Modificación de fórmulas comerciales de las plantillas de cotización.
- Corrección editorial del contenido oficial más allá de normalización y
  trazabilidad.
- Inclusión de `TEQUILA LOVE.pdf`.

## Secuencia posterior a la aprobación documental

1. Elaborar un plan de implementación verificable con tareas y pruebas.
2. Generar y validar el SPEC GUIDE de IDÉLIKA.
3. Implementar los dos adaptadores y el registro local.
4. Ejecutar pruebas unitarias, integración y recorrido E2E local.
5. Documentar resultados y evidencias en la bóveda de Obsidian.
