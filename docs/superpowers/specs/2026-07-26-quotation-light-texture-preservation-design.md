# Diseño: preservar texturas claras al limpiar imágenes de Quotation

**Fecha:** 2026-07-26
**Estado:** Aprobado por el usuario

## Objetivo

Corregir únicamente el procesamiento de imágenes importadas desde `Quotation` para
eliminar el fondo y la sombra de piso sin borrar cubiertas, asientos, patas, mallas ni
otras texturas claras del producto.

## Evidencia

La revisión de `output (18).xlsx` confirmó dos fallas distintas:

- la segunda limpieza por inundación interpreta superficies claras del producto como
  fondo y recorta asientos y cubiertas;
- el postprocesado binario de la máscara de `rembg` elimina antialias y detalles claros.

También se detectó que una fotografía compleja puede producir una máscara suave que
ocupa todo el cuadro. Esa máscara no debe utilizarse.

## Diseño aprobado

1. Las imágenes importadas se segmentan con máscara alfa suave
   (`post_process_mask=False`).
2. Cuando `remove_shadow=True`, no se aplica después la limpieza clara por inundación.
3. La máscara se rechaza si combina una caja casi de cuadro completo con una proporción
   anormalmente alta de alfa parcial.
4. Una máscara rechazada o un fallo de segmentación conserva la imagen original y sólo
   la aplana sobre blanco; la cotización no se bloquea.
5. La salida continúa siendo PNG opaco sobre blanco.

## Restricciones

- No modificar API, UI, catálogos, precios, fórmulas ni composición de Excel.
- No agregar modelos, servicios externos ni dependencias.
- No alterar la imagen original de la hoja `Quotation`.
- El cambio se limita a `mobiliti_saas/quote_engine/image_processing.py` y pruebas
  focalizadas del procesador.

## Aceptación

- Una superficie clara conectada al producto permanece visible.
- Una máscara segura elimina la sombra y conserva bordes suaves.
- Una máscara suave de cuadro completo activa el fallback conservador.
- Las imágenes reales representativas de `output (18).xlsx` muestran fondo blanco sin
  pérdidas visibles de cubierta, asiento o patas.
