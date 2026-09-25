# Carrito unificado y cotización mixta de catálogos — diseño aprobado

Fecha: 2026-07-19
Estado: aprobado para planificación
Proyecto: Mobiliti SaaS Cotizador

## Objetivo

Unificar en un solo carrito los productos de Tarkett, Offiho, CR Global, Sonara, Sunon, ALMA y Lumbro, conservarlos al navegar entre catálogos y generar una sola cotización Excel con todos los productos mezclados.

La entrega también corrige el bloqueo de Sonara: su moneda base confirmada por negocio es MXN, sus precios impresos deben conservarse y los artículos sin código verificado pueden cotizarse con una advertencia visible cuando el resto de sus datos comerciales sea válido.

## Decisiones aprobadas

- Los siete catálogos comparten un único carrito y un único formulario de cotización.
- Un checkout crea un solo folio, un solo trabajo asíncrono, un solo JSON congelado y un solo archivo Excel.
- No se fusionan archivos Excel finales. El worker crea una sola hoja intermedia `Quotation`, agrupada por proveedor, y ejecuta una vez el generador existente.
- Sonara usa MXN como moneda base mediante una regla de negocio auditable en el importador.
- La moneda final de la cotización es común para todas las líneas. Se conservan `MXN`, `USD` y `EUR`, con MXN como valor predeterminado.
- Cada grupo conserva su propia moneda original y su propio snapshot de conversión. El Excel recibe precios ya convertidos y no vuelve a aplicar tipo de cambio.
- Los descuentos son por línea: Tarkett y Offiho conservan el descuento comercial capturado, con 40 % como valor inicial; CR Global, Sonara, Sunon, ALMA y Lumbro usan sus precios netos con descuento 0.
- El IVA admitido en esta entrega es 16 %. Un artículo con otro tratamiento fiscal aborta el checkout antes de crear una cotización.
- Los endpoints actuales permanecen disponibles para compatibilidad, pero la interfaz unificada usa el nuevo endpoint mixto.
- No se añaden dependencias ni un segundo generador de Excel.

## Alternativas evaluadas

### Fusionar varios Excel terminados

Descartado. Duplicaría hojas, nombres definidos, fórmulas, imágenes, secciones de totales y bloques de términos. También dificultaría demostrar que subtotal, flete, IVA y total se calculan una sola vez.

### Reescribir todos los catálogos bajo un único adaptador

Descartado para este alcance. Tarkett, Offiho y los proveedores genéricos tienen reglas distintas de identidad, cantidad, inventario, configuración, imágenes y descuentos. Reemplazar los tres contratos existentes ampliaría el riesgo sin ser necesario para obtener un carrito común.

### Orquestador mixto aditivo

Aprobado. Reutiliza los constructores y validaciones existentes, normaliza sus resultados en un payload común y añade únicamente las extensiones necesarias al worker y al generador final.

## Arquitectura

El flujo queda dividido en cinco unidades:

1. `App` conserva el carrito global y presenta una sola bandeja de cotización.
2. Las vistas de catálogo conservan búsqueda, filtros, variantes y validación de cantidad, pero delegan agregar, actualizar y quitar líneas.
3. `POST /catalogs/mixed-quote` recarga los catálogos autoritativos, valida todas las líneas, congela precios y monedas y crea un solo trabajo.
4. El worker convierte `mixed_catalog_cart` en una sola hoja `Quotation` con secciones por proveedor.
5. El motor existente genera una sola `Cotizacion`, una sola `Mobiliti`, una sola `Quotation` y un solo bloque de totales.

Los constructores existentes siguen siendo la autoridad de cada familia:

- `build_tarkett_cart_payload` para Tarkett;
- `build_offiho_cart_payload` para Offiho;
- `build_supplier_cart_payload` para CR Global, Sonara, Sunon, ALMA y Lumbro.

## Corrección de Sonara

### Fuente y regla monetaria

La lista vigente `Lista de precios Sonara 2026.pdf`, ubicada en SharePoint bajo `LISTAS DE PRECIOS PROVEEDORES/SONARA`, muestra valores con `$` y declara que son más IVA, pero no contiene el texto literal `Moneda: MXN`. El importador actual interpreta esa ausencia como moneda desconocida, asigna `base_currency = "XXX"` y reemplaza el precio por cero.

El importador aplicará esta regla:

1. Si el documento declara únicamente `MXN`, usar MXN con estado `verified`.
2. Si no declara moneda, usar MXN con estado `business_override` y conservar el precio impreso.
3. Si declara USD, EUR o monedas contradictorias, fallar cerrado: moneda desconocida, precio cero y advertencia de revisión.

El snapshot conservará evidencia auditable equivalente a:

- `base_currency = "MXN"`;
- `attributes.source_currency_status = "business_override"` cuando aplique;
- una identificación estable de la regla `sonara_mxn_confirmed_2026-07-19`;
- referencias exactas al archivo y a la región de precio ya usadas por el importador.

### Códigos sin verificar

Sonara contiene artículos comerciales válidos cuyo PDF no ofrece un SKU inequívoco. Esos artículos mantienen:

- `code_status = "needs_review"`;
- SKU vacío, sin inventar códigos;
- advertencia `Código por verificar` en tarjeta, carrito, `Quotation` y `Cotizacion`.

Se permite agregarlos y cotizarlos únicamente si tienen moneda MXN conocida, precio positivo, IVA 16 %, cantidad válida y una identidad interna estable. La misma excepción seguirá limitada a Sonara y Lumbro; los demás proveedores conservan su regla actual.

### Publicación

Cambiar el importador no modifica el snapshot ya publicado. La implementación debe generar y validar un candidato nuevo de Sonara. Sin autorización separada no se publicará el candidato ni se desplegará a producción.

## Carrito global de la interfaz

`App` mantendrá un arreglo único de líneas discriminadas por catálogo. No se crea Context ni una nueva librería; se pasan estado y callbacks directamente a las tres vistas existentes.

Una línea contiene:

- clave global estable;
- catálogo de origen;
- identificador autoritativo;
- cantidad como texto decimal;
- configuración permitida, cuando exista;
- snapshot visual mínimo para nombre, imagen y advertencias.

Claves canónicas:

- `tarkett:<code>`;
- `offiho:<inventory_key>`;
- `<supplier>:<internal_id>|<base_option_id>|<add_on_option_ids ordenados>`.

El snapshot visual nunca es una autoridad de precio, stock, moneda, URL o imagen. El servidor reconstruye esos valores al cotizar.

### Comportamiento

- Agregar desde cualquier catálogo abre o actualiza la misma bandeja.
- Cambiar de catálogo conserva todas las líneas y el contador global.
- Las configuraciones distintas del mismo producto permanecen como líneas distintas.
- Agregar la misma clave canónica acumula cantidad conforme a las reglas del catálogo.
- Tarkett conserva su límite por existencia.
- Offiho conserva cantidades de hasta tres decimales y permite sobrestock con advertencia.
- Los proveedores genéricos conservan PZA entera y M² con hasta seis decimales.
- Los agotados y cantidades insuficientes conservan sus confirmaciones actuales.
- Un error de checkout conserva el carrito completo.
- Un checkout aceptado vacía el carrito después de recibir el único trabajo encolado.
- Logout y expiración de sesión vacían el carrito.
- Los filtros y configuradores continúan siendo estado local de cada vista.

El carrito persiste durante la navegación de la sesión React. Persistirlo tras recargar o cerrar el navegador queda fuera de alcance.

### Formulario único

La bandeja global contiene:

- proyecto;
- cliente;
- correo;
- teléfono;
- dirección;
- razón social;
- moneda final `MXN`, `USD` o `EUR`;
- descuento comercial para Tarkett y Offiho, inicialmente 40 %;
- template existente.

La interfaz identifica claramente que el descuento capturado no vuelve a descontar los precios netos de los cinco proveedores genéricos.

## API mixta

### Endpoint

Se añade `POST /catalogs/mixed-quote` antes de las rutas dinámicas de proveedor.

Entrada representativa:

```json
{
  "quote_currency": "MXN",
  "descuento": "40",
  "proyecto": "Proyecto mixto",
  "cliente": "Cliente",
  "correo": "cliente@example.com",
  "telefono": "3330000000",
  "direccion": "Guadalajara",
  "razon_social": "Cliente SA de CV",
  "items": [
    {"catalog": "tarkett", "code": "25731726", "quantity": "3.5"},
    {"catalog": "offiho", "inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": "1"},
    {"catalog": "sonara", "internal_id": "sonara:review-example", "quantity": "2"},
    {
      "catalog": "alma",
      "internal_id": "alma:example",
      "quantity": "1",
      "base_option_id": "base-example",
      "add_on_option_ids": ["addon-example"]
    }
  ]
}
```

El contrato acepta solo campos de metadata conocidos, catálogo, identidad, cantidad y configuración. Rechaza precios, monedas base, tasas, stock, nombres, imágenes, URLs y referencias enviados por el cliente.

### Límites

- entre 1 y 500 líneas totales;
- máximo siete grupos, uno por catálogo;
- claves canónicas únicas;
- límites más estrictos ya definidos por cada constructor;
- cantidades y configuraciones validadas por el constructor autoritativo.

### Normalización

El servidor agrupa las líneas, recarga cada catálogo vigente y ejecuta su constructor actual. Cualquier error se devuelve con el catálogo y la identidad afectados; no se crea una cotización parcial.

Todas las líneas se convierten en el servidor a `quote_currency`. Para cada grupo se congela:

- hash del catálogo;
- moneda base;
- moneda final;
- tasa de conversión;
- fuente de la tasa;
- fecha efectiva;
- fecha de recuperación.

Tarkett y Offiho declaran MXN dentro del sobre mixto. Sonara, CR Global y Lumbro usan MXN; Sunon y ALMA usan USD. Una tasa identidad vale `1.000000`.

### Payload congelado

El único archivo de entrada usa `source_type = "mixed_catalog_cart"`:

```json
{
  "source_type": "mixed_catalog_cart",
  "quote_currency": "MXN",
  "created_at": "2026-07-19T00:00:00Z",
  "groups": [
    {
      "catalog": "sonara",
      "label": "Sonara",
      "catalog_source_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "base_currency": "MXN",
      "quote_currency": "MXN",
      "exchange_rate": "1.000000",
      "rate_source": "identity",
      "rate_effective_date": "2026-07-19",
      "rate_retrieved_at": "",
      "items": []
    }
  ]
}
```

Cada línea normalizada conserva además proveedor, descuento, precio original, precio convertido, moneda original, tasa, referencia de fuente, modo de precio, política de electrificación, estado de código, advertencias, configuración e identidad para reserva.

El payload se guarda como `users/{usuario_id}/jobs/{job_id}/input.json`. Metadata del trabajo registra un solo folio, `source_type`, conteos por catálogo, conteo total, moneda final y resumen de tasas. La respuesta contiene un solo `job`.

## Descuentos, impuestos y precios

El precio unitario escrito en `Quotation` ya está convertido a la moneda final.

- Tarkett y Offiho conservan precio lista y el porcentaje capturado por línea.
- CR Global, Sonara, Sunon, ALMA y Lumbro usan precio neto y porcentaje 0.
- El motor no aplica una tasa global a precios ya convertidos.
- El motor no copia el descuento de la primera línea al resto.
- El subtotal, flete 12 %, IVA 16 % y total se calculan una sola vez sobre el conjunto completo.

La validación rechaza precio no positivo en los proveedores que actualmente requieren precio confirmado. Tarkett y Offiho mantienen su advertencia vigente para precios faltantes. Un tratamiento fiscal distinto de 16 % se rechaza con catálogo e identidad en el mensaje.

La electrificación Lumbro automática conserva su comportamiento anterior por línea: se aplica únicamente donde el flujo anterior la añadía. Los productos Lumbro agregados manualmente siguen siendo independientes y no suprimen accesorios automáticos.

## Reservas atómicas

La cotización mixta no debe dejar reservas parciales entre las tres familias de tablas.

Se añade una migración con dos RPC `SECURITY DEFINER`:

- `saas_reserve_mixed_cart(p_usuario_id, p_quote_job_id, p_groups)`;
- `saas_release_mixed_cart(p_quote_job_id)`.

`saas_reserve_mixed_cart`:

1. valida usuario, trabajo `draft`, catálogos y estructura estricta;
2. adquiere locks en un orden estable por catálogo e identidad;
3. valida que no existan reservas duplicadas para el trabajo;
4. calcula snapshots de disponibilidad con la semántica vigente;
5. inserta Tarkett, Offiho y proveedores genéricos dentro de una sola transacción;
6. devuelve un snapshot completo o no persiste ningún cambio.

`saas_release_mixed_cart` libera de forma atómica e idempotente todas las reservas activas asociadas al trabajo.

Ambas funciones fijan `search_path`, revocan ejecución de `PUBLIC`, `anon` y `authenticated`, y conceden ejecución únicamente a `service_role`. El modo DEV simula la misma atomicidad bajo un único lock y guarda el estado solo después de validar todos los grupos.

El orden del checkout es:

1. validar metadata, líneas, catálogos, precios, impuestos y tasas;
2. crear trabajo `draft`;
3. reservar todos los grupos mediante la RPC atómica;
4. aplicar snapshots de disponibilidad al payload;
5. subir un solo JSON;
6. cambiar el trabajo a `queued`;
7. despertar al worker.

Si falla storage o encolado, se libera el carrito mixto, se marca o limpia el trabajo conforme al patrón existente y se limpia el input parcial. Una falla de compensación queda registrada como `cleanup_pending` para recuperación.

## Worker y hoja `Quotation`

El worker reconoce `mixed_catalog_cart` sin modificar los tres `source_type` anteriores. Crea `quotation_from_mixed_catalog.xlsx` y después invoca una vez `generate_quote`.

La hoja conserva las columnas A–K existentes y añade:

- L: `Supplier`;
- M: `Discount Percent`;
- N: `Original Currency`;
- O: `Original Unit Price`;
- P: `Frozen Exchange Rate`;
- Q: `Source Reference`;
- R: `Price Mode`;
- S: `Auto Electrification`.

Cada proveedor comienza con una fila de categoría, por ejemplo `- Tarkett`, `- Offiho` o `- Sonara`. Los productos mantienen numeración global y el orden estable del carrito dentro del orden de navegación: Tarkett, Offiho, CR Global, Sonara, Sunon, ALMA y Lumbro.

La descripción conserva variante, configuración, materialidad, add-ons, código por verificar, imagen de referencia, disponibilidad, precio por confirmar y referencia exacta.

## Cambios mínimos del motor final

El parser amplía `QuoteItem` con los campos mixtos detectados por encabezado. Los archivos antiguos sin esas columnas conservan sus defaults actuales.

Para trabajos mixtos:

- `Mobiliti!F` usa el proveedor de cada producto;
- el descuento comercial usa la columna de cada línea;
- el modo de precio decide si la línea es lista o neta;
- la electrificación automática se decide por línea;
- `Mobiliti!J6` muestra `<quote_currency>/<quote_currency>`;
- `Mobiliti!K6` vale 1;
- `Cotizacion!B4` muestra la moneda final y un resumen compacto de tasas congeladas por proveedor;
- los textos externos pasan por `safe_excel_text`.

No se cambia la plantilla ni se crea un segundo motor de cotización.

## Imágenes

El adaptador mixto descarga cada imagen con la política de su línea:

- hosts oficiales Tarkett para líneas Tarkett;
- hosts oficiales Offiho para líneas Offiho;
- assets publicados y hosts configurados para proveedores genéricos.

Se mantienen HTTPS, allowlist, resolución a IP pública, inspección del peer conectado, límite de bytes, tipo MIME y timeout. Ninguna URL del navegador se usa como fuente. Las imágenes se anclan a la columna C de la fila correcta para que el generador existente las copie a `Cotizacion`.

## Manejo de errores

- Un catálogo o artículo inválido aborta todo el checkout.
- Una tasa ausente o vencida aborta todo el checkout.
- Un snapshot cambiado se revalida contra la versión vigente; nunca se confía en el precio visual almacenado.
- Un error indica catálogo e identidad sin revelar secretos, URLs firmadas ni contenido interno innecesario.
- La interfaz conserva el carrito si el servidor rechaza o falla el trabajo.
- La limpieza y retención liberan reservas de las tres familias.
- Retry reutiliza el payload congelado y no vuelve a fijar precios ni tasas.

## Seguridad

- Autenticación y suscripción activa protegen el endpoint.
- Los campos de entrada usan allowlists estrictas y límites de tamaño.
- Precios, tasas, stock, imágenes y URLs provienen exclusivamente del servidor.
- Los textos de catálogos se neutralizan contra fórmulas de Excel.
- Las tasas usan `Decimal`, precisión y redondeo existentes; no se usan flotantes para importes congelados.
- Las nuevas RPC son exclusivas de `service_role` y validan propiedad del trabajo.
- Los endpoints y payloads existentes no pierden validaciones.

## Pruebas y criterios de aceptación

La implementación seguirá TDD y debe demostrar:

1. Sonara sin declaración literal de moneda produce MXN, conserva precio y registra `business_override`.
2. Sonara con moneda extranjera o contradictoria falla cerrado.
3. Sonara `needs_review` con precio válido puede agregarse y conserva la advertencia; precio o moneda inválidos siguen bloqueados.
4. Agregar Tarkett, navegar a Sonara y después a ALMA conserva líneas y contador.
5. Configuraciones distintas generan claves y líneas distintas.
6. El único POST mixto no contiene precios, tasas, stock, URLs ni imágenes del navegador.
7. El servidor rechaza identidad, configuración, cantidad, impuesto o campo inesperado manipulados.
8. MXN, USD y EUR producen conversiones congeladas correctas por grupo y sin doble conversión.
9. El descuento se aplica únicamente a Tarkett y Offiho; los precios netos conservan descuento 0.
10. La RPC de reserva realiza rollback total ante falla en cualquier grupo y es segura bajo concurrencia.
11. Fallas de upload o encolado liberan todas las reservas y no dejan un trabajo utilizable parcial.
12. El worker genera una sola `Quotation` con secciones, proveedor, descuento, auditoría e imágenes correctas.
13. El Excel final contiene una sola `Cotizacion`, `Mobiliti` y `Quotation`, una sola tabla de totales y proveedor correcto por fila.
14. Subtotal, flete 12 %, IVA 16 % y total coinciden con los cálculos de referencia.
15. Variantes, configuraciones, advertencias e imágenes de varios proveedores llegan al Excel final.
16. Fórmulas inyectadas y URLs de imagen no permitidas quedan neutralizadas o rechazadas.
17. Error de checkout conserva el carrito; éxito, logout y expiración lo vacían.
18. Los tres endpoints antiguos, el generador tradicional y la electrificación automática no presentan regresiones.
19. El frontend compila y la prueba de navegador confirma carrito mixto en escritorio y móvil sin errores de consola ni desbordamiento horizontal.

La verificación incluye pruebas unitarias de importador y constructores, API/DEV store, migraciones SQL, worker, golden de `Quotation`, golden del Excel final, contratos estáticos de UI, build Vite y una prueba E2E del flujo completo.

## Compatibilidad y despliegue

- Los endpoints `/tarkett/quote`, `/offiho/quote` y `/catalogs/{supplier}/quote` permanecen sin cambios contractuales.
- Los `source_type` `tarkett_cart`, `offiho_cart` y `supplier_cart` continúan soportados.
- El cambio de base de datos es aditivo.
- La implementación se valida primero con DEV store, worker local y navegador local.
- La migración, publicación del nuevo snapshot Sonara, cargas a Storage y despliegues requieren autorización separada.
- No se modifican cotizaciones históricas ni reservas de trabajos existentes.

## Fuera de alcance

- Fusionar archivos Excel ya generados.
- Persistir el carrito tras recargar o cerrar el navegador.
- Editar manualmente precios, tasas, stock o impuestos desde la interfaz.
- Mezclar más de una moneda final dentro del mismo Excel.
- Soportar impuestos distintos de 16 % en una misma cotización.
- Cambiar la plantilla corporativa o el formato de subtotal, flete, IVA y total.
- Reemplazar los adaptadores, endpoints o tablas existentes.
- Publicar snapshots, migrar producción o desplegar sin autorización explícita.
