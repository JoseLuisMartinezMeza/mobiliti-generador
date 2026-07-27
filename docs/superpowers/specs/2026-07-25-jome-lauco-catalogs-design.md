# Diseño: catálogos JOME y Lauco y preservación del vínculo USD/MXN

**Fecha:** 2026-07-25
**Estado:** aprobado por el usuario
**Alcance:** sincronización, catálogo, Proyecto, cotización mixta y libro Excel

## Objetivo

Integrar JOME y Lauco con el mismo recorrido funcional que ALMA, Sonara,
Sunon, Lumbro y CR Global, publicando costos canónicos en MXN y dejando que
las fórmulas oficiales del libro apliquen el margen comercial una sola vez.
La fórmula financiera vinculada de `Mobiliti!K6` y sus metadatos se conservan
sin introducir una tasa fija alternativa.

## Decisiones aprobadas

1. `Mobiliti!J6` continúa siendo el dato vinculado `USD/MXN`.
2. `Mobiliti!K6` continúa usando `=_FV(J6,"High")`.
3. Se acepta que Excel muestre temporalmente `#¡CAMPO!` cuando su proveedor de
   datos financieros no responda. La composición no debe eliminar ni
   reconstruir los metadatos oficiales de esas celdas.
4. Todos los costos JOME y Lauco son MXN desde origen.
5. Las etiquetas USD de JOME `MA02` y `MA03` son errores humanos. El importador
   normaliza esas filas a MXN, conserva la moneda declarada en procedencia y
   no multiplica el costo por un tipo de cambio.
6. El catálogo siempre publica costo proveedor, nunca precio de lista o venta.

## Fuentes y contratos

### JOME

Un catálogo visible `jome` combina dos fuentes XLSX:

- `Spec guide-Estructuras Jome-2026.xlsx`
  - hoja de costo: `COSTO ESTRUCTURAS 2026`
  - código B, descripción C, medida D, costo E, moneda declarada H
  - precio comercial I se ignora
- `Spec guide-Laminado-2026.xlsx`
  - hoja de costo: `COSTO LAMINADO 2026`
  - código B, descripción C, medida D, costo E, moneda declarada H
  - precio comercial I se ignora

Identidad canónica:
`subcatalogo:sistema:bloque:codigo:dimensiones:fila_fuente`.
Los códigos repetidos no se fusionan entre sistemas. Las variantes sin código
solo heredan el código dentro del bloque explícito que las contiene. Las
imágenes se enlazan por anclas de dibujo y pueden compartirse entre variantes
de una misma familia. Los recursos WDP no compatibles se ignoran de forma
acotada; PNG, JPEG y TIFF permanecen disponibles.

Proveedor escrito en `Mobiliti`: `Jome`.

### Lauco

Fuente oficial:
`Spec Guide Lauco-2026.xlsb`.

- hoja de costo: `COSTO-LAUCO-2026`
- código B, descripción C, medida D, opción E, costo F, moneda declarada G
- precio comercial K se ignora

El worker lee valores cacheados con `pyxlsb==1.0.10`; nunca ejecuta fórmulas.
Antes de abrir el archivo valida límites ZIP, rutas, compresión, MIME y
relaciones, y rechaza VBA, OLE, ActiveX, conexiones y vínculos externos. Las
imágenes se resuelven por las relaciones de hoja/dibujo/media del paquete.

`Tapiz Grado 1` y `Tapiz Grado 2` son opciones base. Las patas cromadas o
pintadas son complementos compatibles y no productos base independientes.
Los códigos duplicados materialmente distintos conservan identidades internas
separadas.

Proveedor escrito en `Mobiliti`: `Lauco Sofas`.

## Contrato de precios y conversión

Cada variante JOME y Lauco publica:

- `raw_cost` numérico positivo obtenido de E o F;
- `base_currency = "MXN"`;
- celda, hoja, archivo, hash y moneda declarada originales;
- marca de corrección de calidad cuando la moneda declarada no era MXN.

Si la cotización es MXN, el costo entra sin conversión. Si la cotización es
USD, el conversor general lo transforma una sola vez. El valor convertido se
congela en la línea del Proyecto y no vuelve a convertirse durante la
composición. Las fórmulas oficiales de W y X no se sustituyen.

## Integración funcional

- El registro backend pasa de cinco a siete proveedores genéricos.
- El catálogo mixto pasa de siete a nueve catálogos visibles.
- Se incorporan `jome` y `lauco` en sincronización, snapshots, búsqueda,
  reservas, Proyecto, reemplazos, complementos y generación de cotización.
- El frontend usa las etiquetas `JOME` y `Lauco`.
- Los catálogos nuevos se publican solo con variantes verificadas.
- El panel administrativo muestra el registro completo, incluido Lumbro.
- Una migración forward amplía restricciones y funciones SQL; no se reescribe
  el historial de migraciones.

## Comportamiento ante errores

- Cambio de estructura, hoja ausente, costo inválido o archivo inseguro:
  sincronización rechazada sin publicar un snapshot parcial.
- Imagen ausente: producto publicable con marcador de imagen pendiente, sin
  inventar una imagen.
- `#¡CAMPO!` en el dato financiero: no se trata como corrupción del libro y no
  autoriza a reemplazar `_FV`.
- Cualquier diferencia de precio entre costo y precio comercial se resuelve a
  favor de la columna de costo aprobada.

## Validación

Las pruebas deben demostrar:

- lectura de E/F y rechazo de I/K como costo;
- normalización auditable de MA02/MA03 a MXN sin conversión;
- variantes y complementos Lauco;
- identidades estables ante códigos duplicados;
- seguridad y extracción de imágenes XLSB;
- nueve catálogos en API, SQL y frontend;
- conversión única MXN→USD;
- preservación de `J6`, `K6`, `_FV`, metadatos financieros y fórmulas W/X;
- libro final válido y abrible.
