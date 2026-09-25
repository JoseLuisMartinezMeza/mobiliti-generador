# Plan de implementación: imágenes acotadas y complementos separados

Fecha: 2026-07-25

## 1. Pruebas rojas

- Actualizar la prueba de proyección de proyecto para exigir fuentes independientes en lugar de un montaje.
- Agregar pruebas del compositor con principal y complementos en una misma fila.
- Agregar pruebas de validación de posiciones duplicadas o discontinuas.
- Cambiar la prueba de `Quotation` para exigir anclas contenidas, no extensiones originales sin normalizar.
- Agregar pruebas del procesador por bytes con PNG blanco y fallback seguro.

## 2. Modelo de imágenes

- Agregar una fuente de imagen inmutable y validada para complementos.
- Extender `CotizacionProduct` con fuentes de complementos.
- Extender `CotizacionProductImage` con posición dentro de la fila.
- Proyectar principal y complementos en orden sin llamar a `compose_product_montage`.

## 3. Geometría OOXML de Cotizacion

- Agrupar imágenes por fila y validar posiciones consecutivas.
- Calcular un rectángulo completo para una imagen única.
- Calcular un bloque principal y una cuadrícula derecha para múltiples imágenes.
- Generar una relación, parte de media y ancla OOXML por imagen.

## 4. Geometría OOXML de Quotation

- Resolver ancho de columna `Photo` y altura real de cada fila de producto.
- Resolver dimensiones de la imagen embebida desde su relación.
- Reemplazar la geometría de cada ancla de producto por una ancla contenida y centrada.
- Aplicar la misma geometría a imágenes nuevas agregadas por el proyecto.

## 5. Mejora visual

- Agregar `improve_product_image_bytes` al procesador existente.
- Producir PNG blanco y validar dimensiones/formato.
- Aplicar la mejora sólo a imágenes visibles de `Cotizacion`.
- Conservar bytes originales cuando la mejora falle.

## 6. Verificación

- Ejecutar pruebas focalizadas de proyecto, compositor, extensión de `Quotation` e imágenes.
- Ejecutar regresión del motor oficial.
- Generar un Excel de prueba y auditar ZIP, dibujos, anclas, imágenes y fórmulas.
- Registrar resultados en Obsidian.
