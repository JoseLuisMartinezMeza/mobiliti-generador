# Preservación del formato oficial y capacidad dinámica — diseño aprobado

Fecha: 2026-07-21

Estado: diseño aprobado; pendiente de revisión final del documento antes del plan TDD

Proyecto: Mobiliti SaaS Cotizador

## Objetivo

Corregir la generación de cotizaciones para que el libro final parta del formato oficial de SharePoint y conserve sus fórmulas, formatos, valores fijos, nombres definidos, vínculos externos, dibujos y estados de hojas ocultas. El generador sólo podrá escribir datos variables en celdas autorizadas.

Todos los precios que ingresan desde una `Quotation` o desde los catálogos se consideran **costos**. El backend convierte cada costo una sola vez a la moneda seleccionada y el libro oficial aplica su propia lógica de aumento, precio de lista, descuento y precio comercial.

La capacidad debe crecer automáticamente. No habrá un límite funcional de 16 secciones, 33 productos por sección, 32 secciones ni 500 líneas. Una cotización grande, ya sea importada o armada desde el carrito, no podrá truncar ni omitir productos.

## Decisiones aprobadas

- La plantilla oficial de SharePoint es la única autoridad visual y financiera.
- La `Quotation` importada queda intacta en el resultado final.
- Los productos agregados desde catálogos no se insertan en la `Quotation` original.
- La secuencia combinada vive en `Quotation_Data`, una hoja nueva en estado `veryHidden`.
- `Mobiliti` y `Cotizacion` incluyen todos los productos en el orden y secciones definidos por el usuario.
- Cada costo se convierte una sola vez en el backend y se escribe como costo de entrada en `Mobiliti!J`.
- `Mobiliti!K6` y las fórmulas oficiales desde `W` no se reemplazan.
- Las secciones y filas de producto se expanden a demanda mediante clonación y traducción del propio formato oficial.
- No se fabrican fórmulas comerciales alternativas en Python.
- Una incompatibilidad real de plantilla debe fallar de forma explícita; nunca debe producir un libro parcialmente reescrito.

## Evidencia y causa raíz

El archivo oficial recuperado el 2026-07-21 tiene SHA-256:

```text
e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989
```

Su contrato incluye:

- 11 hojas;
- 8 hojas ocultas;
- `Mobiliti!A1:AW610` con 14,265 fórmulas;
- 29 nombres definidos;
- 12 partes OOXML de vínculos externos;
- 10 dibujos;
- 1,314 fórmulas en las cinco hojas SPEC/Spec Guide.

El resultado local auditado pierde los vínculos externos, baja a 21 nombres definidos, deja las hojas SPEC sin fórmulas, hace visible `Fletes`, agrega `sheep`, expande y reconstruye `Mobiliti`, y sustituye la `Quotation` original por una tabla técnica plana.

Las causas directas están en el motor actual:

- `_sanitize_template_workbook` elimina fórmulas y nombres;
- `_ensure_mobiliti_formula_layout`, `_ensure_mobiliti_capacity` y `_write_mobiliti_row_formulas` reconstruyen fórmulas y estilos;
- `_write_mobiliti_settings` reemplaza `K6`;
- el modo mixto reemplaza `W:X` por `ROUND(J,2)`, evitando el aumento oficial;
- `build_mixed_catalog_quotation` crea una `Quotation` técnica nueva;
- `_copy_source_sheet` vuelve a crear la hoja y no conserva todo su contrato OOXML.

## Alternativas evaluadas

### Seguir usando OpenPyXL sobre todo el libro

Descartada como solución final. Aunque se eliminen las escrituras indebidas, guardar el workbook vuelve a serializar todas las hojas y puede descartar extensiones, relaciones, fórmulas externas, dibujos o propiedades que OpenPyXL no representa completamente.

### Generar el libro y restaurar hojas al final

Descartada. Reinyectar hojas completas después de guardar puede desalinear IDs de estilos, relaciones, dibujos, nombres definidos y referencias entre hojas. Sería difícil demostrar preservación determinista.

### Composición quirúrgica del paquete XLSX

Aprobada. El `.xlsx` se trata como un paquete OOXML. Se parte de los bytes oficiales y sólo se reemplazan o agregan las partes declaradas en una allowlist. Las partes protegidas permanecen byte-idénticas.

## Arquitectura de generación

### 1. Plantilla oficial versionada

El worker usa una copia local promovida de forma controlada desde SharePoint. La plantilla se identifica por versión, hash y un manifiesto estructural. Una actualización futura del formato requiere una promoción explícita y nuevas pruebas de contrato.

No existe fallback silencioso a `_default_template`. Si la plantilla falta o no cumple el manifiesto, el trabajo falla antes de crear la salida.

### 2. Limpieza de la cotización contaminante

La limpieza no busca celdas por color ni elimina todas las constantes. Usa un manifiesto explícito de zonas variables.

En `Mobiliti` sólo son mutables:

- títulos de secciones;
- celdas de entrada de código, categoría, proveedor, cantidad, costo, M3 y región;
- selector oficial de moneda;
- lugar de entrega.

Las celdas de fórmulas, tablas auxiliares, valores fijos, validaciones y configuraciones no son mutables.

En `Cotizacion` se reemplazan únicamente el encabezado comercial y el bloque dinámico de productos/totales. El bloque de condiciones, estilos, dibujos y valores fijos se conserva desde la plantilla.

La limpieza se ejecuta sobre una copia de trabajo, nunca sobre el archivo de SharePoint.

### 3. Quotation original y datos internos

Si el usuario importó una `Quotation`, su hoja visible se incorpora preservando:

- XML de celdas y fórmulas;
- estilos y dimensiones;
- filas y columnas ocultas;
- combinaciones;
- imágenes, dibujos y relaciones;
- configuración de impresión;
- nombres locales de hoja.

Los productos agregados desde catálogos no modifican esa hoja.

`Quotation_Data` contiene la secuencia canónica de todo el carrito y queda `veryHidden`. Cada línea conserva:

- origen e identidad;
- sección y orden;
- fila original cuando aplica;
- costo y moneda originales;
- tasa congelada;
- costo convertido;
- cantidad, M3, proveedor y región;
- hashes y trazabilidad.

`Quotation_Data` no contiene URLs temporales ni imágenes duplicadas.

### 4. Escritura de Mobiliti

El layout dinámico produce un `RowMap` autoritativo:

```text
section_id -> section_row, product_start, product_count, subtotal_row
item_key   -> mobiliti_row
global     -> total_row, auxiliary_start
```

Los productos se escriben en las columnas de entrada permitidas. `Mobiliti!J` recibe siempre el costo congelado como valor numérico; `Quotation_Data` conserva su trazabilidad. Las fórmulas financieras de la fila se conservan o se clonan desde el formato oficial.

La cadena monetaria es:

```text
costo original × tasa congelada = costo en moneda de cotización
costo en moneda de cotización -> Mobiliti!J
Mobiliti!J -> fórmulas oficiales W:X:Y:... -> precio comercial
```

No existe una segunda conversión en `Mobiliti` ni en `Cotizacion`. Los costos de Lumbro/electrificación siguen la misma regla y no vuelven a dividirse por `K6`.

### 5. Escritura de Cotizacion

`Cotizacion` usa el orden y las secciones del carrito. Sus precios visibles referencian las celdas comerciales calculadas por `Mobiliti`, no los costos de `Quotation_Data`.

La generación dinámica conserva imágenes, descripción, cantidad, descuento maestro y totales. El descuento editable de la primera línea sigue siendo la fuente de las líneas posteriores, de acuerdo con el contrato ya aprobado.

## Expansión automática sin límite 16 × 33

### Planificación de capacidad

Antes de modificar el workbook se calculan:

- número real de secciones;
- cantidad de productos por sección;
- cantidad total de filas requeridas;
- posición final de subtotales, total global y áreas auxiliares.

El baseline conserva las 16 secciones y 33 filas por sección del formato. Sólo se agregan filas o bloques cuando la cotización lo necesita.

### Sección con más de 33 productos

Las filas adicionales se insertan antes del subtotal de esa sección. Cada fila nueva clona una fila canónica completa de producto, incluyendo:

- estilo y formato numérico;
- altura y bordes;
- fórmulas traducidas por desplazamiento;
- validaciones de proveedor y región;
- formato condicional;
- protección y metadatos de celda.

El subtotal se desplaza y sus rangos se amplían mediante traducción de referencias. La lógica de negocio de la fórmula no se reemplaza.

### Más de 16 secciones

Cada sección adicional clona un bloque canónico completo:

```text
encabezado de sección
33 filas iniciales de producto
subtotal de sección
```

Si esa nueva sección necesita más de 33 filas, se expande con el mismo algoritmo anterior.

### Traducción de fórmulas

La expansión usa un traductor de referencias A1 basado en tokens, no sustituciones globales de texto. El traductor ajusta:

- referencias relativas y absolutas;
- rangos;
- referencias entre hojas;
- fórmulas de subtotal y total;
- rangos de validación y formato condicional;
- referencias de `Fletes`, `Estrategia Comercial ` y `Cotizacion` al total dinámico.

La fuente de cada fórmula nueva es una fórmula existente del formato oficial. El motor no mantiene una segunda versión manual de las fórmulas financieras.

### Límites técnicos

Se eliminan los límites funcionales anteriores de 32 secciones y 500 líneas. La capacidad efectiva se calcula contra:

- máximo XLSX de 1,048,576 filas por hoja;
- filas reservadas por encabezados, subtotales, totales y auxiliares;
- tamaño máximo de upload configurado;
- memoria y tiempo disponibles del worker.

El worker ajusta lease, progreso y tiempo estimado de acuerdo con el número de líneas e imágenes. El algoritmo debe ser lineal respecto a filas y no debe reconstruir repetidamente el workbook completo.

Si una entrada alcanzara un límite técnico real de XLSX o del servicio, el sistema devolverá un error explícito antes de generar; nunca truncará productos. Superar 16 × 33 por sí solo no es un error.

## Carrito e importaciones grandes

El contrato de checkout usa referencias compactas. No repite imágenes, descripciones completas ni snapshots de origen cuando ya existen en el manifiesto del servidor.

Para mantener usable un carrito grande:

- las secciones se pueden contraer;
- el render de líneas es incremental o virtualizado sin agregar dependencias;
- mover, editar cantidad y eliminar no reconstruyen todo el carrito en cada pulsación;
- los conteos muestran líneas totales y por sección;
- la creación del payload se realiza una sola vez al cotizar.

Una importación grande mantiene su manifiesto en storage. El navegador envía claves, orden y overrides permitidos; el backend vuelve a validar las filas autoritativas antes de generar.

## Preservación del paquete

Fuera de una allowlist explícita, las partes del archivo oficial deben mantener su hash. La allowlist inicial contiene únicamente:

- `workbook.xml` y relaciones necesarias para agregar `Quotation_Data` y, cuando exista, `Quotation`;
- partes nuevas de esas hojas y sus dibujos;
- `Mobiliti`;
- `Cotizacion` y sus dibujos;
- las partes mínimas de estilos o tipos de contenido que sean necesarias para elementos nuevos.

Las siguientes partes están protegidas:

- hojas ocultas oficiales;
- vínculos externos;
- nombres definidos oficiales, salvo referencias que deban desplazarse por expansión;
- dibujos no pertenecientes a `Cotizacion` o `Quotation`;
- valores y fórmulas de hojas SPEC;
- estados de visibilidad;
- configuración de cálculo.

`Fletes` permanece oculta y `sheep` no aparece porque no pertenece al formato oficial.

## Manejo de errores y recuperación

- La fuente importada y la plantilla oficial nunca se modifican.
- Cada salida se crea en un archivo nuevo dentro del intento del job.
- El archivo sólo se publica cuando el paquete reabre y pasa las auditorías estructurales.
- Una falla conserva el carrito y el draft para reintentar.
- No se reutiliza una salida parcial.
- Los mensajes distinguen plantilla incompatible, límite XLSX real, fuente corrupta, fórmula no traducible e imagen inválida.

## Validación TDD

### Contrato de plantilla

- La plantilla promovida coincide con el hash/versionado esperado.
- Las ocho hojas oficiales continúan ocultas.
- Los nombres definidos y vínculos externos se conservan.
- Las cinco hojas SPEC conservan fórmulas, valores, estilos y dibujos.
- `K6`, `W14`, `X14` y las demás fórmulas protegidas coinciden con el formato oficial.

### Contaminación

- Los datos de la cotización previa no aparecen en el resultado.
- Las constantes y fórmulas que no están en la allowlist permanecen intactas.
- La limpieza nunca usa una regla global como “vaciar todas las fórmulas SPEC”.

### Quotation

- Una `Quotation` importada conserva fórmulas, estilos, merges, imágenes y print settings.
- Un producto agregado desde Offiho no aparece dentro de la `Quotation` original.
- Ese producto sí aparece en `Quotation_Data`, `Mobiliti` y `Cotizacion`.

### Costos y moneda

- MXN, USD y EUR se convierten exactamente una vez.
- `Mobiliti!J` contiene el costo congelado.
- `Mobiliti!W:X` conservan la fórmula oficial y no igualan el costo por una asignación del motor.
- Lumbro/electrificación no se reconvierten mediante `K6`.

### Capacidad

- 1 sección con 34 productos.
- 1 sección con 100 productos.
- 17 secciones con al menos un producto.
- 20 secciones con cantidades distintas.
- 20 secciones con más de 33 productos en alguna sección.
- mezcla grande de importados y los siete catálogos.
- ningún producto omitido o duplicado.
- subtotales y total global incluyen todas las filas.
- cero referencias `#REF!` nuevas.
- referencias dinámicas de `Fletes`, `Estrategia Comercial ` y `Cotizacion` correctas.

### Verificación final

- reapertura como ZIP y como workbook;
- auditoría OOXML de hashes y relaciones;
- inspección de fórmulas dirigida;
- render visual de `Mobiliti`, `Cotizacion` y `Quotation`;
- prueba end-to-end desde carrito y desde importación;
- prueba de estrés con miles de líneas sintéticas dentro de los límites reales de XLSX.

## Compatibilidad y despliegue

El trabajo se implementa y valida primero de forma local. No modifica SharePoint, Supabase, Vercel, Storage remoto ni producción durante desarrollo.

La promoción requiere:

1. template oficial versionado y respaldado;
2. suite dirigida y regresión completa;
3. comparación OOXML contra el formato oficial;
4. generación local de un carrito mixto grande;
5. validación visual del usuario;
6. autorización explícita para desplegar.

## Fuera de alcance

- editar la `Quotation` original para agregar líneas de catálogo;
- alterar fórmulas comerciales del formato oficial;
- limpiar o congelar vínculos externos sin una solicitud separada;
- cambiar precios o fórmulas directamente en SharePoint;
- desplegar automáticamente al aprobar esta especificación.
