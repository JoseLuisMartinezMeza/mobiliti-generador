# Catálogo Lumbro — diseño aprobado

Fecha: 2026-07-18
Estado: aprobado para planificación
Alcance: catálogo independiente Lumbro, sin reemplazar la electrificación automática existente

## Objetivo

Añadir Lumbro como proveedor independiente en el catálogo web con el mismo flujo de consulta, carrito y cotización usado por CR Global, Sonara, Sunon y ALMA. Los precios proceden de SharePoint; la web oficial de Lumbro aporta fichas, imágenes y enlaces.

## Decisiones confirmadas

- SharePoint es la autoridad de precios.
- Los valores numéricos publicados se consideran precios netos MXN. No se aplica otro descuento; el IVA se calcula y muestra por separado.
- La web oficial `https://www.lumbromx.com` no sustituye los precios comerciales de SharePoint.
- La electrificación Lumbro que se agrega automáticamente a estaciones de trabajo y mesas de juntas permanece sin cambios.
- También se añade Lumbro como catálogo independiente en el menú.
- Un enlace individual solo se asigna con coincidencia exacta. Sin ficha exacta se enlaza la categoría oficial pertinente y, como último recurso, `https://www.lumbromx.com/category/all-products`.

## Fuentes permitidas

El adaptador trabaja únicamente con los archivos Lumbro comerciales descubiertos en SharePoint. Se excluyen órdenes de compra, cotizaciones de proyectos y documentos históricos ajenos a las listas de proveedor.

Fuentes iniciales:

1. Lista general de precios de multicontactos Lumbro disponible en `LISTAS DE PRECIOS PROVEEDORES/LUMBRO/LP`.
2. Lista de precios de nuevos productos Lumbro disponible en el árbol 2026.
3. `Precios Interconexión Sunón act.xlsx`, actualizado el 2026-07-09, para productos Lido, interconexión, jumpers, cajas de fusible y UP1.

Cada archivo se identifica mediante el objetivo exacto de Microsoft Graph y su SHA-256. Un archivo con el mismo nombre pero distinto identificador o hash no reemplaza silenciosamente una fuente aprobada.

## Enfoque técnico

Se reutiliza el sistema genérico de proveedores. No se crea una API, carrito ni vista exclusivos para Lumbro.

Cambios mínimos previstos:

- Registrar `lumbro` y su etiqueta en configuración, API, worker y contrato SQL.
- Añadir un adaptador Lumbro al servicio de sincronización.
- Añadir un manifiesto pequeño y determinista para vínculos oficiales exactos y fallbacks por categoría.
- Publicar snapshots con el contrato existente de `supplier_catalog`.
- Reutilizar `SupplierCatalogView`, carrito, reservas, tipos de cambio y generación XLSX.

No se realizará rastreo web durante una consulta del usuario ni durante la generación de una cotización. Las verificaciones de fichas e imágenes oficiales ocurren en la preparación/sincronización del catálogo y quedan materializadas en el snapshot.

## Modelo del catálogo

Cada variante Lumbro contiene, cuando la fuente lo permite:

- proveedor y marca `Lumbro`;
- modelo y nombre normalizados;
- SKU oficial verificado o estado `needs_review`;
- descripción y especificaciones técnicas;
- color y demás opciones como variantes seleccionables;
- dimensiones y longitud de cable;
- precio neto MXN y referencia exacta a archivo/página/fila;
- unidad `PZA` y cantidad entera;
- disponibilidad `unknown`, mostrada como “Disponibilidad por confirmar”, salvo que una fuente comercial aprobada declare existencia explícita;
- imagen oficial verificada o placeholder honesto;
- URL exacta de producto o fallback oficial etiquetado.

Los productos Lido/interconexión con configuraciones distintas permanecen como variantes separadas cuando cambian puertos, conexiones, color, cableado o precio.

## Precedencia y reconciliación

1. El Excel actualizado de interconexión prevalece para los productos que identifica inequívocamente.
2. La lista de nuevos productos prevalece sobre la lista general para el mismo modelo y configuración.
3. La lista general cubre el resto de los productos.
4. Dos registros solo se fusionan si modelo, configuración técnica y color son compatibles.
5. Un conflicto de precio, descripción o identidad queda marcado para revisión; no se elige por similitud difusa.

El valor de precio se conserva exactamente como neto. El importador no resta el 10 % mencionado en el encabezado del Excel y no incorpora IVA al precio unitario.

## Enlaces oficiales

El dominio permitido es `https://www.lumbromx.com`.

Resolución:

1. Ficha individual exacta, por ejemplo `/product-page/<slug>`, validada contra nombre y, cuando exista, SKU.
2. Categoría oficial exacta —empotrable, sobreponer, pasacable, split u otra categoría publicada— cuando no exista ficha individual verificable.
3. Catálogo general `https://www.lumbromx.com/category/all-products`.

La interfaz muestra “Ver producto” únicamente para una ficha exacta. Los fallbacks muestran “Ver catálogo Lumbro”. No se construyen URLs especulativas a partir del nombre.

## Imágenes

- Una imagen se presenta como oficial únicamente si procede de una ficha o categoría oficial con coincidencia exacta.
- Los bytes se validan, normalizan y almacenan como assets inmutables direccionados por contenido, reutilizando el flujo de imágenes de proveedores.
- No se enlazan imágenes remotas durante la cotización.
- Sin una coincidencia exacta se muestra el placeholder existente; no se usa una imagen de un modelo parecido.

## Experiencia de usuario

- Nueva entrada “Lumbro” en la navegación lateral.
- Búsqueda por SKU, modelo, descripción o atributo.
- Filtros genéricos de marca, colección y disponibilidad.
- Selector de variante para configuraciones o colores.
- Precio neto MXN, leyenda “más IVA”, dimensiones, especificaciones, disponibilidad y enlace oficial.
- Cantidad PZA entera y carrito común.
- Generación de cotización y XLSX mediante el flujo genérico.

La electrificación automática existente no consulta ni modifica el carrito Lumbro. Añadir manualmente un producto Lumbro es una acción independiente y deliberada.

## Seguridad y manejo de errores

- Validación pasiva de XLSX/PDF antes de parsear.
- Límites existentes de tamaño, páginas, imágenes, celdas y fórmulas.
- Referencias de origen exactas y texto neutralizado para Excel.
- HTTPS obligatorio y host oficial permitido para vínculos e imágenes.
- Sin precio inequívoco, el producto no puede añadirse al carrito.
- Sin código oficial, se conserva “Código por verificar”; nunca se inventa un SKU.
- Conflictos y filas ambiguas se reportan como revisión pendiente y no bloquean productos independientes válidos.

## Pruebas y aceptación

La implementación seguirá TDD y debe demostrar:

1. Parseo determinista de las tres familias de fuentes y precedencia correcta.
2. Precio neto sin descuento duplicado y con IVA separado.
3. Variantes y colores sin fusiones ambiguas.
4. Coincidencias exactas de SKU/enlace e imagen; fallback oficial correctamente etiquetado.
5. Rechazo de URL, archivo, moneda, precio o código inseguros/ambiguos.
6. Registro de `lumbro` en API, worker y configuración sin alterar otros proveedores.
7. Carrito con cantidades enteras y XLSX con precio, descripción, imagen y fuente correctos.
8. Electrificación automática anterior sin regresiones.
9. Vista local de escritorio y móvil sin imágenes rotas, errores de consola ni desbordamiento horizontal.
10. Conteo final auditado: todas las filas válidas de las fuentes autorizadas quedan importadas o aparecen en un reporte explícito de exclusión.

## Entrega y límites

- La primera entrega se valida en el dev-store y navegador local.
- Se generan manifiestos de cobertura para productos, precios, códigos, imágenes y enlaces.
- No se aplican migraciones, publicaciones de snapshots, cargas a Storage ni despliegues de producción sin autorización separada.
- No se modifica el historial de órdenes de compra ni las cotizaciones existentes.
