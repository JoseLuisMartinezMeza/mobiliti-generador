# Diseño: imágenes acotadas y complementos separados

Fecha: 2026-07-25
Estado: aprobado por el usuario (opción A)

## Objetivo

Corregir el contrato visual de imágenes del generador oficial:

1. Las imágenes de productos en `Quotation` deben quedar centradas y contenidas dentro de la celda `Photo`.
2. Una línea de `Cotizacion` con complementos debe contener varios objetos de imagen OOXML independientes.
3. La imagen principal debe conservar mayor jerarquía visual que las miniaturas de complementos.
4. El flujo oficial debe poder mejorar imágenes importadas para `Cotizacion` reutilizando el procesador existente.

## Contrato visual

### Quotation

- Sólo se normalizan dibujos vinculados a filas de producto.
- Se conserva el contenido binario original de cada imagen.
- La imagen se centra, mantiene relación de aspecto y ocupa como máximo el área útil de la columna `Photo` y la altura de su fila.
- Logos, encabezados, pies y dibujos ajenos a productos permanecen intactos.

### Cotizacion

- Cada producto principal genera un objeto de imagen independiente.
- Cada complemento con imagen genera otro objeto independiente en la misma fila.
- Con una sola imagen, ésta se centra en todo el espacio disponible.
- Con complementos, la principal ocupa el bloque izquierdo dominante y las miniaturas se distribuyen en una cuadrícula en la banda derecha.
- Ninguna imagen sale del rectángulo visual de la celda.
- El orden de miniaturas coincide con el orden autoritativo de los complementos.
- Se elimina el montaje rasterizado como contrato de salida.

## Modelo y validación

- `CotizacionProduct` conserva su fuente principal y agrega una colección inmutable de fuentes de complementos.
- `CotizacionProductImage` declara una posición: `0` para principal y `1..N` para complementos.
- El compositor permite varias imágenes por fila únicamente si sus posiciones son únicas, consecutivas y comienzan en cero.
- Las filas de imágenes deben pertenecer a filas reales de producto.
- Se mantienen los límites existentes de formato, tamaño y descompresión de imágenes.

## Mejora de calidad

Se reutiliza `image_processing.py` y se añade una entrada por bytes para el flujo oficial:

- salida PNG;
- objeto aislado;
- fondo blanco;
- limpieza de residuo y sombra;
- escalado de alta calidad;
- fallback a la imagen original si el procesamiento falla o produce un resultado inválido.

La mejora se aplica a la proyección de `Cotizacion`. `Quotation` conserva el contenido original y sólo cambia su geometría de visualización.

## Criterios de aceptación

- Las anclas de producto en `Quotation` quedan dentro de la columna y fila correspondientes.
- Principal y complementos aparecen como objetos de imagen distintos en el XML.
- La principal es visualmente mayor que cada complemento.
- Las fórmulas, hojas ocultas, referencias y formato oficial no cambian.
- Un fallo de mejora visual no bloquea la generación ni reemplaza la imagen por datos corruptos.
