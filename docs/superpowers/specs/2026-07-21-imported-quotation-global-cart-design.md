# Importación editable de Quotation al carrito global — diseño aprobado

Fecha: 2026-07-21

Estado: aprobado para implementación

Proyecto: Mobiliti SaaS Cotizador

## Objetivo

Agregar a **Nueva cotización** una ruta opcional para cargar un archivo `.xlsx`, analizar su hoja `Quotation` y llevar sus productos al carrito global antes de generar el Excel final. El usuario podrá mezclar esos productos con Tarkett, Offiho, CR Global, Sonara, Sunon, ALMA y Lumbro, reorganizarlos en las secciones manuales existentes y editar únicamente los datos provenientes del archivo.

El motor actual de `Quotation → Mobiliti → Cotizacion` seguirá siendo la fuente de generación. Esta función agrega una etapa autoritativa de importación y revisión; no reemplaza el motor financiero ni crea un segundo generador.

## Decisiones aprobadas

- Se usará una importación validada por el servidor.
- Los productos importados entrarán al carrito global y podrán mezclarse con los siete catálogos.
- La moneda se detectará sólo cuando el archivo contenga evidencia explícita. Si no existe, el usuario deberá elegir USD, MXN o EUR.
- Los productos importados permitirán editar nombre, descripción, dimensiones, precio unitario y cantidad, además de orden, sección y eliminación.
- Las imágenes importadas se previsualizarán y conservarán, pero no podrán reemplazarse en esta entrega.
- Los productos de catálogo conservarán identidad, descripción, configuración y precio autoritativos; sus campos comerciales no se volverán editables.
- El backend reabrirá el archivo almacenado al cotizar y validará cada referencia de fila y cada edición permitida.
- El precio importado se convertirá una sola vez desde su moneda de origen hacia la moneda de cotización.

## Archivo de referencia validado

Se inspeccionó `CET PRUEBAS GENERADOR-Quotation Sheet - V1.xlsx` sin modificarlo.

- Hoja: `Quotation`.
- Encabezado: fila 7.
- Secciones: `SALA DE JUNTAS SECUNDARIO`, `MUESTRAS` y `CONCEJO`.
- Productos: 7.
- Imágenes: 7, ancladas a las filas de producto.
- Columnas útiles: nombre `B`, imagen `C`, descripción `D`, dimensión `E`, cantidad `G`, volumen `H`, precio unitario `J` y precio total `K`.
- El archivo no declara explícitamente la moneda.

El parser existente ya recupera categorías, nombres, descripciones, dimensiones, cantidades y precios. La implementación ampliará esa lectura con un manifiesto de importación, proveedor, moneda e imágenes de previsualización.

## Alternativas evaluadas

### Importación validada por el servidor

Aprobada. El servidor analiza el archivo almacenado, devuelve un manifiesto de previsualización y vuelve a validar las filas originales al cotizar. Conserva trazabilidad e imágenes y evita confiar en precios o productos inventados por el navegador.

### Analizar el Excel en el navegador

Descartada. Requeriría otra librería, duplicaría el parser, complicaría la extracción de imágenes y dejaría datos comerciales sensibles bajo control exclusivo del cliente.

### Regenerar un Excel temporal después de cada edición

Descartada. Produciría escrituras y archivos intermedios en cada cambio, aumentaría la latencia y mezclaría responsabilidades de edición con el motor final.

## Flujo de usuario

1. El usuario selecciona un `.xlsx` en **Nueva cotización**.
2. La interfaz mantiene **Generar cotización** para compatibilidad y añade **Previsualizar e importar al carrito**.
3. La ruta de previsualización usa la carga existente para crear un trabajo en estado borrador y almacenar el archivo.
4. El servidor analiza `Quotation` y devuelve secciones, productos, imágenes, moneda detectada y proveedor detectado.
5. Si la moneda no es explícita, la interfaz exige seleccionar USD, MXN o EUR antes de agregar productos.
6. El nombre del proveedor se toma de una señal explícita del archivo cuando existe —por ejemplo `A1`— y se muestra editable y requerido para las líneas importadas.
7. Al confirmar, los productos y secciones se agregan al carrito global y el drawer se abre como previsualización editable.
8. El usuario mezcla, ordena, mueve, elimina o edita líneas importadas y completa los datos comerciales del carrito.
9. Al cotizar, el backend valida catálogos e importación, congela tasas, crea un único `Quotation` intermedio y encola el motor existente.
10. Un resultado exitoso limpia el carrito y el borrador importado. Un error conserva el estado para corregir y reintentar.

La primera entrega admite un archivo importado por checkout. Puede coexistir con cualquier cantidad permitida de productos de catálogo. Importar otro archivo reemplazará únicamente las líneas importadas después de una confirmación explícita; no eliminará productos de catálogo.

## Experiencia en el carrito

El carrito conserva la lista global `mixedCart` y `mixedCartSections`. Las líneas de catálogo mantienen su forma actual. Una línea importada añade un discriminador y una referencia estable:

```json
{
  "kind": "imported",
  "key": "import:7b1d6d42-236a-4bc1-9aa8-8d9db793c30b:11",
  "importId": "7b1d6d42-236a-4bc1-9aa8-8d9db793c30b",
  "sourceRow": 11,
  "sectionId": "section-2",
  "sourceCurrency": "USD",
  "quantity": "1",
  "snapshot": {
    "name": "CAI63SW Alien Task Chair",
    "code": "CAI63SW",
    "image_url": "https://storage.invalid/signed/preview/11.png",
    "unit": "PZA",
    "availability": "Importado de CET PRUEBAS GENERADOR",
    "configuration": "630*565*1,000 mm",
    "warnings": []
  },
  "edits": {
    "name": "CAI63SW Alien Task Chair",
    "description": "Silla operativa con respaldo de malla y brazos ajustables.",
    "dimension": "630*565*1,000 mm",
    "unitPrice": "80.50",
    "provider": "SUNON TECHNOLOGY CO.,LTD."
  }
}
```

Cada tarjeta importada mostrará la etiqueta **Importado**, el archivo y la moneda de origen. Un panel **Editar datos** contendrá nombre, descripción, dimensiones y precio unitario. Cantidad, orden, sección y eliminación seguirán usando los controles actuales. Las líneas de catálogo no mostrarán este panel.

Las categorías del Excel se convierten en conceptos de sección:

- si el carrito sólo tiene su sección inicial vacía, las secciones importadas la reemplazan;
- si ya contiene productos, las secciones importadas se agregan al final;
- el usuario puede renombrar, unir o redistribuir las líneas con las operaciones existentes.

Todos los controles conservarán navegación por teclado, `aria-label`, foco visible, área táctil mínima de 44 × 44 px y diseño sin desplazamiento horizontal en móvil.

## API de previsualización

La carga inicial conserva `POST /cotizaciones/init-upload` y el mecanismo actual de upload. Se añade:

```text
POST /cotizaciones/{job_id}/import-preview
```

Precondiciones:

- el trabajo pertenece al usuario autenticado;
- la suscripción está activa;
- el trabajo continúa en `draft`;
- el archivo es `.xlsx`, existe en storage y cumple el límite vigente;
- contiene una hoja `Quotation` y entre 1 y 500 productos.

El endpoint lee el archivo con el parser compartido y devuelve:

```json
{
  "import_id": "7b1d6d42-236a-4bc1-9aa8-8d9db793c30b",
  "source_hash": "sha256",
  "original_filename": "CET PRUEBAS GENERADOR-Quotation Sheet - V1.xlsx",
  "provider": "SUNON TECHNOLOGY CO.,LTD.",
  "source_currency": null,
  "currency_status": "required",
  "sections": [
    {"id":"import-section-1","title":"SALA DE JUNTAS SECUNDARIO","item_keys":["import:7b1d6d42-236a-4bc1-9aa8-8d9db793c30b:9"]}
  ],
  "items": [
    {
      "key":"import:7b1d6d42-236a-4bc1-9aa8-8d9db793c30b:9",
      "source_row":9,
      "name":"DV74-2.380148 I-Varna II Conference Table para 8pax clone 1",
      "description":"Mesa rectangular para sala de juntas con caja de conexiones.",
      "dimension":"",
      "quantity":"1",
      "unit_price":"688.50",
      "image_url":"https://storage.invalid/signed/preview/9.png",
      "row_hash":"sha256"
    }
  ]
}
```

La respuesta nunca marca una moneda como detectada por el nombre del proveedor. Sólo acepta señales explícitas como columna de moneda, campos de cabecera reconocidos o metadata auditada. Si las filas contienen monedas explícitas distintas, se conservan por fila; el selector completa únicamente las filas sin moneda.

Las imágenes se extraen una vez durante la previsualización, se guardan como miniaturas dentro del prefijo del borrador y se exponen mediante URLs temporales. La limpieza normal del trabajo incluye esas miniaturas. No se envían imágenes grandes como base64 dentro del JSON.

## Manifiesto y seguridad

El servidor guarda un manifiesto JSON junto al archivo cargado, no dentro de campos grandes de la base de datos. La metadata del trabajo sólo conserva ruta, hash, conteos, proveedor y estado de moneda.

Cada producto se identifica por `import_id + source_row`. El manifiesto conserva:

- hash SHA-256 del archivo;
- hash de los valores originales de la fila;
- nombre, descripción, dimensión, cantidad y precio originales;
- moneda explícita o estado pendiente;
- referencia a la imagen extraída;
- categoría original.

El navegador sólo envía referencias y cambios permitidos. Antes de crear el trabajo final, el backend descarga de nuevo el archivo, comprueba propiedad, hash, fila y manifiesto, y rechaza:

- trabajos ajenos o que ya no estén en borrador;
- filas inexistentes, repetidas u omitidas de forma inconsistente;
- referencias que no correspondan al archivo;
- campos no permitidos;
- moneda sin resolver;
- cantidades no positivas o fuera de los límites del carrito;
- precios negativos, no finitos o con más de seis decimales;
- textos con controles, fórmulas inyectables o longitudes superiores a las allowlists existentes.

La eliminación es una omisión explícita de una fila importada; no modifica el archivo original. Los textos editados se neutralizan mediante `safe_excel_text` antes de escribirlos.

## Contrato del checkout mixto

`POST /catalogs/mixed-quote` amplía cada item con un discriminador. Los items de catálogo conservan el contrato actual. Los importados usan:

```json
{
  "kind":"imported",
  "import_id":"7b1d6d42-236a-4bc1-9aa8-8d9db793c30b",
  "source_row":11,
  "source_currency":"USD",
  "quantity":"2",
  "overrides":{
    "name":"CAI63SW Alien Task Chair",
    "description":"Descripción revisada",
    "dimension":"630*565*1,000 mm",
    "unit_price":"82.00",
    "provider":"SUNON TECHNOLOGY CO.,LTD."
  }
}
```

El payload congelado del worker conserva dos áreas independientes:

- `groups`: grupos autoritativos actuales de los siete catálogos para precios, inventario y reservas;
- `imported_source`: fuente, hash y líneas importadas ya validadas.

`sections.item_keys` puede intercalar claves de ambos orígenes. El índice final `canonical_key → item` permite que `create_mixed_catalog_quotation_workbook` escriba una sola secuencia sin convertir al archivo importado en un catálogo ficticio.

Las reservas sólo recorren `groups`; las líneas importadas no crean ni consumen inventario de catálogo. La metadata final registra el archivo fuente, hash, conteo importado, moneda original, campos editados y resumen de tasas.

## Moneda y descuento

El precio editado de una línea importada se interpreta siempre en su moneda de origen. En checkout:

```text
precio importado en moneda origen × tasa congelada = precio en moneda de cotización
```

Ese resultado se escribe en `Quotation!J` y sigue el recorrido existente:

```text
Quotation!J → Mobiliti!J → Mobiliti!X → Cotizacion!F
```

No se aplica otra multiplicación o división en `Mobiliti` o `Cotizacion`. Si moneda de origen y destino coinciden, la tasa congelada es `1.000000`.

El descuento general conserva el comportamiento ya aprobado: la primera fila de `Cotizacion` contiene el porcentaje editable y las demás filas lo referencian. Se aplica por igual a líneas importadas y de catálogo.

## Generación e imágenes

El adaptador importado normaliza cada línea al contrato que ya consume `write_catalog_quotation_item`, incluyendo proveedor, descripción, dimensión, cantidad, precio convertido y auditoría de moneda.

El `Quotation` intermedio:

- recorre las secciones manuales y su orden exacto;
- mezcla líneas importadas y de catálogo;
- inserta la imagen original de la fila importada;
- conserva la imagen oficial de las líneas de catálogo;
- registra referencia de archivo, fila y hash en los campos de auditoría;
- no modifica el workbook cargado por el usuario.

Si una fila importada no tiene imagen, la previsualización muestra **Sin imagen** y el motor final conserva la política de imágenes elegida por el usuario. La ausencia de miniatura no impide editar o importar la línea.

## Compatibilidad y alcance

No cambian:

- la generación directa existente desde `.xlsx` o `.pdf`;
- los endpoints individuales de catálogo;
- la autorización, precio, inventario y reservas de los catálogos;
- el formato oficial de `Mobiliti` y `Cotizacion`;
- la política de secciones manuales;
- el límite global de 32 secciones y 500 líneas;
- SharePoint, Supabase, Vercel o producción durante la implementación local.

Fuera de alcance en esta entrega:

- previsualización editable de PDF;
- reemplazo o recorte manual de imágenes;
- edición de precios o descripciones de productos de catálogo;
- más de un workbook importado por checkout;
- reconocimiento de estructuras que no tengan hoja `Quotation` compatible.

## Manejo de errores

- Un archivo sin `Quotation`, sin productos o con más de 500 productos muestra un error antes de tocar el carrito.
- Una moneda pendiente mantiene deshabilitada la confirmación de importación.
- Un error de parsing conserva el archivo en borrador para corregir la selección o volver a cargarlo.
- Un error al agregar al carrito no elimina líneas que ya existían.
- Un rechazo del checkout conserva productos, secciones, ediciones y formulario.
- Si el archivo o manifiesto cambia entre preview y checkout, se rechaza con un mensaje de que la fuente debe volver a importarse.
- Los errores no exponen rutas internas, hashes completos, tokens ni contenido sensible del workbook.

## Estrategia de pruebas

La implementación seguirá TDD y observará primero el fallo de cada comportamiento nuevo.

1. Parser: workbook de prueba con categorías, siete productos, imágenes, celdas formateadas hasta la fila 65536 y moneda ausente.
2. Moneda: detección explícita, selector obligatorio, monedas por fila, tasa uno y conversión única MXN/USD/EUR.
3. API preview: propiedad, estado draft, límites, manifiesto, miniaturas y archivos inválidos.
4. Carrito: creación de líneas importadas, edición completa, reemplazo confirmado, orden y combinación con líneas de catálogo.
5. Seguridad: filas ajenas, hashes alterados, campos adicionales, fórmulas inyectables, precios y cantidades inválidos.
6. Payload: `groups` de catálogo intactos, `imported_source` validado y secciones con cobertura exacta.
7. Workbook: orden mixto, proveedor correcto, imágenes, textos editados, fuente auditada, descuento y fórmulas sin reconversión.
8. E2E: cargar un fixture equivalente al archivo CET, importar, mezclar con un producto de catálogo, editar, generar y abrir el XLSX resultante.
9. Regresión: suites actuales de carrito mixto, worker, API, motor, capacidad de `Mobiliti` y build de Vite.

## Criterios de aceptación

1. Un `.xlsx` compatible puede previsualizarse sin encolar todavía la cotización final.
2. El ejemplo CET produce tres secciones, siete productos y siete imágenes.
3. La falta de moneda obliga a seleccionar USD, MXN o EUR.
4. Confirmar la importación agrega las líneas al carrito global y abre el drawer.
5. Una línea importada permite editar nombre, descripción, dimensión, precio y cantidad.
6. Una línea de catálogo no permite editar campos autoritativos.
7. Los productos importados y de catálogo pueden intercalarse dentro de la misma sección.
8. Orden, conceptos, cantidades y eliminaciones visibles coinciden con el Excel final.
9. El backend rechaza filas, trabajos, hashes o campos manipulados.
10. El precio importado se convierte exactamente una vez y conserva precio, moneda y tasa originales para auditoría.
11. El descuento general alcanza todas las líneas y las filas posteriores referencian la celda del primer producto.
12. Las imágenes importadas llegan desde sus filas originales y las faltantes no bloquean el flujo.
13. El archivo cargado permanece intacto.
14. La ruta directa actual sigue funcionando.
15. Las pruebas enfocadas, regresiones relevantes y build web terminan sin fallos antes de declarar la implementación completa.
