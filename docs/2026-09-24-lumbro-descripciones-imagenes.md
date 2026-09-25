# Descripciones Lumbro y calidad de imágenes importadas

## Corrección de descripciones

La descripción de catálogo agregaba configuración, color y avisos administrativos al texto que luego utiliza la cotización. `catalog_cart._description_for_item` ahora omite, exclusivamente para Lumbro:

- La configuración vacía de significado `Standard` (también `Estándar`/`Estandar`).
- El segmento agregado `Color: ...`.
- Avisos de código por verificar, código oficial, color variable y variante no cotizable.

La descripción técnica, nombre del modelo, configuraciones reales, garantía, precio pendiente e imagen de referencia se conservan. Los avisos y atributos del catálogo permanecen en el payload original. El cambio se aplica al punto compartido por cotización directa de proveedor y proyectos mixtos, tanto principales como complementos. El espejo incluido en Vercel conserva paridad binaria.

Archivos: `mobiliti_saas/quote_engine/catalog_cart.py`, su espejo web y pruebas en `test_supplier_catalog.py` y `test_project_quote_engine.py`.

## Validación local

- 179 pruebas aprobadas de catálogo, proyectos y bundle en la ejecución conjunta. Los dos casos nuevos de proyecto fallaron por un dato inválido en la fixture (`needs_review` exige SKU vacío).
- Corregida la fixture, ambos casos generaron y reabrieron el XLSX correctamente: 2 aprobados en español e inglés. Se verificó texto limpio en principal y complementos, unión de descripciones, vínculo vivo `Cotizacion!C17 = Quotation!D9` y cantidades 10/20/3.
- Total: 181 casos distintos aprobados tras corregir la fixture. `git diff --check` aprobado.
- El lector openpyxl informa de extensiones de validación y WMF que no admite; se usa solo para lectura en estas comprobaciones, sin reserializar los libros generados.
- No se modificaron fórmulas, precios, imágenes ni plantillas.

## Diagnóstico de GEELY - PALMAS

Archivo analizado: `C:\Users\pepem\Downloads\Cotizacion_GEELY_-_PALMAS_200-00031.xlsx`. Análisis mediante ZIP/OOXML y dimensiones de imágenes, sin modificar la fuente. Evidencia detallada en `output/lumbro-imagenes-20260924/diagnostico-geely.json`.

- Hoja Quotation: 12 apariciones de imágenes de productos importados. Once miden **150 × 150 px** (7 imágenes únicas reutilizadas); la otra mide 600 × 600 px.
- Las 150 × 150 están en las filas 13, 14, 15, 21, 22, 23, 28, 31, 32, 34 y 35. Hay además seis imágenes de catálogo: tres de 258 × 272, dos de 314 × 214 y una de 640 × 640.
- En Cotizacion, las imágenes importadas se presentan después de limpieza de fondo y reescalado local. Por ejemplo, la imagen de 150 × 150 de `Quotation!B13` acaba como recurso de 550 × 886. Aumentar píxeles por interpolación no recupera detalle real.
- La ruta vigente `_improve_official_cotizacion_images` llama `improve_product_image_bytes` con `min_size=550` y `remove_shadow=True`; utiliza segmentación local y Lanczos. No invoca el proveedor generativo Dezgo en esta ruta. El proyecto tiene integración Dezgo/Flux en otras funciones, lo cual no significa que las imágenes de este flujo estén recibiendo superresolución por API.

## Opciones para mejorar calidad

1. Preferir la imagen original de mayor resolución del modelo y variante exactos. Para el tamaño de presentación observado, una fuente alrededor de 1000 px de ancho deja más margen que una miniatura de 150 px.
2. Si no hay original mejor, probar superresolución conservadora sobre las imágenes únicas y reutilizar el resultado por hash. Revisar patas, ruedas, conectores, tapizados y color antes de aceptarlo. No basta con comprobar dimensiones de salida.
3. Dezgo expone `/upscale` con Real-ESRGAN; permite aprovechar el proveedor ya conocido. Esto requiere una integración específica, no cambiar la URL del retoque Flux: el código actual rechaza endpoints no destinados al retoque en esa configuración. El precio publicado depende de la resolución de entrada, por ejemplo US$0.0005 a 320 × 320 o US$0.0051 a 1024 × 1024 por llamada. Stability AI también ofrece Conservative Upscale.

Fuentes consultadas el 24-09-2026:

- https://dev.dezgo.com/changelog/
- https://dev.dezgo.com/pricing/upscaling/
- https://docs.aws.amazon.com/bedrock/latest/userguide/stable-image-services.html

No se envió ninguna imagen a una API ni se generaron cargos de mejora. El usuario autorizó publicar la corrección de Lumbro el 24-09-2026. El resultado del despliegue y la comprobación productiva se registra en la nota 133 de Obsidian.
