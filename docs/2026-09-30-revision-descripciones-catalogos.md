# Revisión de descripciones de los catálogos

Se revisaron los 4,218 registros de los 13 catálogos disponibles localmente. La corrección se aplica a la presentación de cotizaciones nuevas o regeneradas; los snapshots de proveedores, sus claves, precios e inventario se conservan. El usuario autorizó el despliegue y la prueba de extremo a extremo en producción.

## Cambios

- Offiho: se corrigen nombres incompletos, gramática y ruido extraído de PDF. Se rechazan especificaciones de fichas vecinas y cifras sin contexto. De 1,288 registros, 929 conservan detalles técnicos y 359 presentan únicamente identidad y datos seguros hasta recuperar una ficha fiable.
- Otros proveedores: 145 correcciones de textos exactos con referencia de origen, además de reglas acotadas de ortografía, puntuación y nombres. Una descripción nueva no hereda una corrección por similitud de SKU.
- ALMA: revisión de 551 textos únicos de 614 registros. Traducción de tipos de producto, componentes y materiales; protección de modelos, tapizados y acabados comerciales, lateralidad y distinción entre teca natural e imitación.
- Sunon: traducción de las 299 cláusulas presentes en los 124 textos únicos de 262 registros. Ocho registros con geometría o cantidad de puertas contradictorias omiten esas afirmaciones hasta confirmar la fuente.
- Presentación final: títulos comprensibles, oraciones puntuadas, acentos y condiciones separadas. Se omiten SKU duplicados, URL, hashes, encabezados y avisos administrativos. Se conservan garantías, restricciones, disponibilidad, precio pendiente, imágenes de referencia y pedidos mínimos respaldados por atributos.

La redacción se integra en el punto común de carritos individuales y mixtos, incluidos proyectos con trazabilidad canónica. El XLSX intermedio conserva el nombre y la descripción originales en `Quotation!B/D`, con la trazabilidad que corresponde al carrito. La hoja oculta `_Catalogo_textos` guarda por fila el nombre y la descripción revisados, con encabezados `fila`, `nombre` y `descripcion`. El lector transmite estos textos al motor únicamente para la presentación comercial. El clasificador y sus alias siguen usando la fuente original, para que una mejora editorial no cambie categorías, instalación ni fletes.

Las correcciones exactas se aplican antes de los sufijos administrativos de unidad, colección y fuente. La cotización final muestra la redacción revisada y conserva las fórmulas que vinculan `Cotizacion` con `Quotation`. La ruta de traducción general de cotizaciones importadas conserva su comportamiento anterior; los textos revisados del catálogo no reemplazan sus datos de origen ni el manifiesto canónico.

Los módulos y datos nuevos están duplicados en el bundle web y la prueba de paridad verifica su igualdad binaria.

## Evidencia y límites

- `output/descripciones-20260930/revision-descripciones.html`: comparación local completa, con búsqueda, filtro de proveedor y filtro de pendientes; no usa servicios externos.
- `output/descripciones-20260930/offiho-auditoria.json`: recorrido de los 1,288 artículos por el payload real del carrito, sin consultar información externa durante la generación.
- `output/descripciones-20260930/auditoria-proveedores.json`: comparación de los 2,930 registros restantes con motivos y referencias de los pendientes.
- Regeneración: `scripts/audit_offiho_descriptions.py`, `scripts/audit_supplier_descriptions.py` y el generador de la vista en `output/descripciones-20260930/crear_revision.py`.

Hay 933 registros con información incompleta, contradictoria o insuficiente en las fuentes locales. Este número incluye los 125 registros Tarkett sin ficha técnica, los 359 Offiho limitados a identidad y otros casos de medidas, materiales, longitudes o capacidades ausentes. Las alertas pertenecen a la auditoría y no agregan propiedades al producto. La revisión no certifica inventario ni documentación remota vigente.

## Validación local

Validación posterior a la separación final entre fuente y presentación:

- La suite principal de diez archivos pasó: 606 pruebas aprobadas y una omitida por ausencia de un archivo golden histórico. Incluye redacción, proveedores, Offiho, carritos mixtos, regresiones de traducción y paridad del bundle.
- Parser, clasificación y E2E: 17 aprobadas. Se comprueban cotización Offiho real, los 13 catálogos con y sin handoff canónico, títulos, cantidades, precios sintéticos, garantías, exclusiones, fórmulas y conservación del payload. Las regresiones VESPER y JOME verifican que corregir la presentación no modifica las categorías ni los costos.
- Proyectos: 20 aprobadas; se excluyó la prueba de segmentación de imágenes que ya había fallado por ausencia de `rembg` en el runtime. No se modificó esa ruta ni se instaló un modelo de imágenes.
- Después del último ajuste de duplicados, colores y conectores de configuración: 22 aprobadas de presentación y paridad del bundle.
- Se comparó el escritor anterior de `HEAD` contra el modo de fuente original para los 4,218 registros: cero diferencias. Evidencia: `output/descripciones-20260930/paridad-descripciones-fuente.json`; script `verificar_fuente.py` en esa misma carpeta.
- La hoja auxiliar se valida por encabezados, tipos, rango y unicidad de filas. Permanece oculta en el intermedio y no se copia a la cotización final. Las importaciones conservan también espacios, saltos y orden de sus textos.
- La vista HTML se regeneró tras el último ajuste y pasó validación sintáctica de JavaScript. `git diff --check` sin errores en los archivos de esta tarea.

El flujo completo API/worker pasó en MXN, USD y EUR (tres casos). Diez comprobaciones adicionales de identidad y carritos también pasaron. La aceptación de los 13 proveedores pasó con los PDF oficiales verificados de Labenze y Requiez: comprueba imágenes, trazabilidad y un enlace de `Cotizacion!C` a `Quotation!D` por cada producto. La columna `Description` original queda en E; `Trasformacion` en D contiene la redacción comercial. Evidencia: `output/descripciones-20260930/validacion-baseline/e2e-13-catalogos-presentacion.txt`. Son 14 casos únicos aprobados en este bloque.

Cuatro casos de imágenes importadas de esa suite requieren `rembg`, ausente en el runtime; este límite es independiente de la revisión de textos. Las fixtures aíslan la limpieza del almacenamiento simulado en memoria y mantienen la preservación de archivos reales. Las pasadas anteriores a esta arquitectura no se usan como evidencia de su preservación de cálculos.

Las pruebas utilizaron almacenamiento local aislado, conexiones externas bloqueadas y preservación de temporales. Los ejemplos XLSX de pruebas usan precios sintéticos y no deben enviarse como cotizaciones comerciales.

## Publicación y prueba en producción

El worker se publicó el 30 de septiembre de 2026 con el commit `69943048be682b74ac05eba5588fafa5f169f93a`, desde un checkout aislado que contiene únicamente los 38 archivos de esta tarea. Se verificaron cola vacía, imagen activa, salud y coincidencia de `CURRENT`. El contenedor anterior y el volumen de imágenes quedaron preservados. No fue necesario desplegar Vercel porque no cambiaron la API ni el contrato del payload.

La sesión del usuario en la aplicación generó dos proyectos internos, identificados como `PRUEBA E2E DESCRIPCIONES`, con contacto ficticio y sin enviar mensajes. Se seleccionaron productos y configuraciones desde los catálogos reales y se descargaron sus Excel desde la interfaz:

- `200-00037`: cinco artículos Offiho, seis piezas; lista en 32 segundos. Las 99 comprobaciones de textos, precios, cantidades, fórmulas y clasificación pasaron. Excel abrió el archivo sin reparación, recalculó y reabrió una copia nueva: 171 fórmulas y 203 celdas operativas verificadas, original intacto.
- `200-00038`: trece artículos, uno por catálogo, incluidas opciones de tapiz y productos Sunon/ALMA en USD; lista en 27 segundos. Excel abrió y recalculó una copia sin reparación ni errores en 435 fórmulas y 515 celdas operativas. El tipo de cambio congelado de 18.071 MXN/USD coincide con los metadatos sanitizados del trabajo. La lectura editorial encontró avisos administrativos y configuraciones que todavía requerían limpieza; esta primera pasada se conserva como evidencia del hallazgo, no como aprobación editorial final.

La revisión automática bloqueó exportar el snapshot interno completo por posible contenido sensible. La alternativa utiliza las selecciones observadas en la interfaz, los datos congelados del XLSX y metadatos sanitizados del trabajo de prueba; no se presenta como una copia completa del payload del servidor.

Los hallazgos reales de la primera pasada dieron lugar a regresiones específicas: se omiten avisos documentales de Sonara y Requiez, se evita reintroducir una ficha cruda como configuración de Conceptos, se normalizan las notas y conexiones de Lumbro, el color de Sunon, las garantías de CR Global y la opción de ALMA. Offiho aclara altura, brazos y cabecera sin perder material ni capacidad; Lauco mejora concordancia y conectores conservando cada cifra. Las unidades de títulos se normalizan sólo en Tarkett y Sonara. `tests/test_catalog_production_editorial_regressions.py` protege estos casos y la conservación de configuraciones distintas, advertencias comerciales y modelos. La comparación final de las 4,218 fuentes volvió a dar cero diferencias.
