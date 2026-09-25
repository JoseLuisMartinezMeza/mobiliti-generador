# Secciones manuales y orden editable del carrito mixto — diseño aprobado

Fecha: 2026-07-20
Estado: aprobado para implementación
Proyecto: Mobiliti SaaS Cotizador

## Objetivo

El carrito seguirá mezclando productos de Tarkett, Offiho, CR Global, Sonara, Sunon, ALMA y Lumbro, pero dejará de convertir cada proveedor en una sección del Excel. El usuario definirá los cortes, los conceptos y el orden de presentación antes de generar la cotización.

Esta especificación reemplaza únicamente la decisión “`Quotation` agrupada por proveedor” de `2026-07-19-mixed-catalog-cart-design.md`. Las reglas autoritativas de identidad, precio, moneda, descuento, inventario, reservas, imágenes e impuestos de ese diseño permanecen vigentes.

## Decisiones aprobadas

- El carrito es una sola secuencia ordenada; el proveedor no decide la presentación.
- La primera sección aparece como `1-Recepción`.
- **Cerrar sección y abrir otra** fija el corte actual y crea la siguiente sección.
- La numeración depende de la posición y se actualiza automáticamente.
- El concepto es editable; el número no se escribe manualmente.
- Los conceptos iniciales son Recepción, Sala de estar, Operativos, Privados, Sala de juntas, Dirección, Áreas comunes, Capacitación, Comedor y Otro. A partir de la sección once se usa `Espacio N`.
- Cada producto se puede subir o bajar dentro de su sección y mover explícitamente a cualquier sección existente.
- No se dependerá exclusivamente de arrastrar y soltar.
- Una sección puede unirse con la anterior para eliminar un corte sin perder productos.
- La última sección no necesita cerrarse para cotizar.
- Una sección final vacía no se exporta.
- Los grupos internos por catálogo se conservan para cálculos y reservas.
- El Excel reproduce el orden y los conceptos definidos por el usuario.

## Alternativas evaluadas

### Lista única con cortes manuales

Aprobada. Mantiene el modelo actual de carrito, añade un identificador de sección a cada línea y conserva un arreglo pequeño de conceptos. Es la menor modificación que permite ordenar, cortar y previsualizar el resultado.

### Carritos anidados por sección

Descartada. Aunque facilita dibujar cajas, vuelve a fragmentar el carrito y complica mover un producto entre secciones, acumular una clave existente y conservar el orden global.

### Editar secciones después de generar el Excel

Descartada. El usuario no podría validar el resultado antes de cotizar y el carrito dejaría de ser la fuente del orden final.

## Modelo de interfaz

`App` conserva dos estados coordinados:

1. `mixedCart`: arreglo global de líneas en el orden de presentación.
2. `mixedCartSections`: arreglo ordenado `{ id, concept }`.

Cada línea incorpora `sectionId`. Una línea existente conserva su sección cuando se vuelve a agregar desde un catálogo; sólo aumenta su cantidad. Una línea nueva se agrega a la última sección abierta.

El identificador es técnico y estable (`section-1`, `section-2`, etc.). La etiqueta visible se calcula como `<posición>-<concepto>`. La numeración no se almacena en el concepto, lo cual permite renumerar después de unir secciones.

### Operaciones

- `Cerrar sección y abrir otra`: exige al menos un producto en la última sección y crea el siguiente concepto predeterminado.
- `Editar concepto`: acepta hasta 120 caracteres visibles; al quedar vacío recupera el concepto predeterminado de esa posición.
- `Subir` y `Bajar`: intercambian líneas dentro de la misma sección.
- `Mover a sección`: retira la línea de su origen y la agrega al final de la sección destino.
- `Unir con la anterior`: reasigna las líneas de la sección actual a la anterior, elimina el corte y renumera la vista.
- `Quitar producto`: elimina también cualquier sección intermedia que haya quedado vacía; siempre conserva una última sección abierta.
- `Cotizar`: omite sólo la sección final vacía y conserva todas las demás en el orden visible.

Los controles tendrán etiqueta visible o `aria-label`, estado de foco, área mínima de 44 × 44 px y alternativa de teclado. En móvil las tarjetas se apilan sin desplazamiento horizontal.

## Contrato del navegador

Los objetos de `items` conservan su allowlist actual y no reciben precio, moneda, stock, imagen ni URL. La solicitud añade un arreglo superior `sections`:

```json
{
  "items": [
    {"catalog":"offiho","inventory_key":"OFF-1","quantity":"1"},
    {"catalog":"lumbro","internal_id":"lumbro:lisboa","quantity":"2","add_on_option_ids":[]},
    {"catalog":"alma","internal_id":"alma:mesa","quantity":"1","add_on_option_ids":[]}
  ],
  "sections": [
    {
      "id":"section-1",
      "title":"Recepción",
      "item_keys":["offiho:OFF-1","lumbro:[\"lumbro:lisboa\",\"\",[]]"]
    },
    {
      "id":"section-2",
      "title":"Sala de estar",
      "item_keys":["alma:[\"alma:mesa\",\"\",[]]"]
    }
  ]
}
```

`item_keys` usa exactamente las claves canónicas ya existentes. La concatenación de todas las listas debe coincidir con `items`, en el mismo orden y sin omisiones ni duplicados.

## Normalización del servidor

`build_mixed_catalog_cart_payload` recibe opcionalmente `presentation_sections`.

1. Valida y conserva el orden de las líneas del navegador.
2. Reagrupa copias por catálogo sólo para ejecutar los constructores autoritativos.
3. Construye los grupos comerciales en el orden canónico vigente.
4. Crea un índice seguro `canonical_key → línea normalizada`.
5. Valida las secciones y las incorpora al payload congelado.

El payload sigue conservando `groups` para tasas y reservas y añade:

```json
"sections": [
  {"id":"section-1","title":"Recepción","item_keys":["offiho:OFF-1"]}
]
```

Se admiten entre 1 y 32 secciones no vacías y hasta 500 líneas totales, límites ya compatibles con el motor `Mobiliti`. Los títulos se neutralizan con `safe_excel_text`. Si una llamada antigua omite `sections`, todas sus líneas forman una sola sección `Recepción` respetando el orden recibido; nunca se recupera la agrupación visual por proveedor.

## Generación del Excel

`create_mixed_catalog_quotation_workbook` deja de recorrer `groups` para presentar productos. Recorre `sections`, resuelve cada clave contra el índice autoritativo y escribe:

- una fila de categoría `- <concepto>`;
- sus productos en el orden indicado;
- el proveedor real de cada producto en la columna `Supplier`.

El motor existente interpreta esas categorías así:

- `Cotizacion` muestra la banda `- Recepción`, `- Sala de estar`, etc.;
- `Mobiliti` muestra `Sección 1 - Recepción`, `Sección 2 - Sala de estar`, etc.;
- los productos pueden mezclar proveedores dentro de cualquier sección;
- los subtotales, fórmulas amarillas, filas libres y formato del template permanecen intactos.

La numeración visible del carrito no se escribe dentro del concepto para evitar `Sección 1 - 1-Recepción`.

## API y compatibilidad

Las tres copias desplegables de la API aceptan el campo superior `sections` y lo entregan al constructor. La metadata del trabajo registra `mixed_section_count`, además de los conteos y hashes actuales.

No cambian:

- el endpoint ni la respuesta con un solo trabajo;
- las reservas por catálogo;
- la conversión y el resumen de tasas;
- las reglas de descuento por línea;
- los endpoints antiguos;
- el template oficial de SharePoint;
- la publicación de catálogos o imágenes.

## Manejo de errores

La solicitud se rechaza antes de crear el trabajo cuando:

- una sección usa campos inesperados;
- un identificador o concepto es inválido;
- una clave falta, se repite o no corresponde a una línea;
- el orden aplanado de las secciones difiere del orden de `items`;
- se exceden 32 secciones o 500 líneas.

Un rechazo conserva carrito, secciones, conceptos y formulario. Un trabajo aceptado, logout o expiración reinician carrito y secciones.

## Criterios de aceptación

1. Mezclar Offiho, CR Global y Lumbro dentro de `1-Recepción` produce una sola sección.
2. Cerrar la sección crea `2-Sala de estar` y los productos nuevos entran allí.
3. Editar el concepto cambia la banda de `Cotizacion` y el título de `Mobiliti`.
4. Subir, bajar y mover productos conserva todas las claves y cantidades.
5. Unir secciones conserva el orden y renumera las restantes.
6. El POST contiene `items` sin campos comerciales y `sections` con cobertura exacta.
7. El servidor rechaza omisiones, duplicados, orden inconsistente y campos manipulados.
8. El payload conserva `groups` canónicos para precio/reserva y `sections` independientes para presentación.
9. `Quotation` sigue el orden manual aunque los proveedores estén intercalados.
10. `Mobiliti` y `Cotizacion` muestran los conceptos manuales sin doble numeración.
11. Fórmulas, subtotales, descuento, IVA, imágenes y proveedor por fila no presentan regresiones.
12. La UI funciona por teclado, en escritorio y en móvil sin desbordamiento horizontal.
13. Las llamadas antiguas sin `sections` generan una sola sección Recepción.
14. No se modifica producción, SharePoint, Supabase ni Vercel durante esta implementación local.
