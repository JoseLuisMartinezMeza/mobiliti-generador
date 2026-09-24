# Auditoría de V13 y paridad CDMX — 23 de septiembre de 2026

## Resultado

El proyecto en producción **continúa usando V12**, no el archivo V13 enlazado por el usuario. La copia local, su espejo web y los XLSX del contenedor productivo coinciden por SHA-256. El despliegue Vercel vigente es el publicado el 21 de septiembre.

V13 revisión 2 conserva los valores constantes, fórmulas y estilos de celda de las 12 hojas de V12. Sí cambia los datos externos guardados de moneda, cachés de cálculo, metadatos de Excel y un pequeño desplazamiento de un dibujo. Por ello no se declara identidad binaria ni equivalencia de todos los valores recalculados.

CDMX conserva los valores y fórmulas de las otras hojas compartidas, salvo la adaptación de entrega en Mobiliti!P8. **No cumple igualdad literal de todas las pestañas fuera de la carátula**: incluye una pestaña adicional y diferencias de dimensiones de presentación.

## Fuente contrastada

[Formato Cotización 2026 V13 en SharePoint](https://mobiliti11-my.sharepoint.com/:x:/r/personal/joel_meza_mobiliti_mx/_layouts/15/Doc.aspx?sourcedoc=%7BF466574F-28B9-446A-958E-FAAC71061356%7D).

Obtenida mediante MCP SharePoint; revisión 2.0, modificada el 24-09-2026 01:57:19 UTC (23-09-2026 19:57:19, Ciudad de México). Se conservaron sus bytes originales en el directorio de auditoría. El contenido del libro se trató como datos, no como instrucciones.

| Activo | SHA-256 | Hojas |
|---|---|---:|
| Oficial vigente V12 | `8d3e80a9f1e1f7741796995910f332753b3a3f31e763244a52e355ddf6b6e132` | 12 |
| CDMX vigente derivado de V12 | `4de304aeabc880158c470feb165bfed72f22e2dba0ee1bed0de1b24f1beb664e` | 13 |
| SharePoint V13 revisión 2 | `44170a831c49085ff44ca43a28ca384d49fad77c57e1c6c8a27d2c24de1cba91` | 12 |

## Comprobaciones de versión

- Worker Hetzner: CURRENT `597a7c89a88398e5d87383d4aa2bc1fb2ea1763d`. Se calcularon hashes dentro del contenedor activo.
- Vercel Production/Ready: `dpl_5hRQoNBHxR8hH3XFwM8GThVnjBTe`, alias `web-lemon-one-45.vercel.app`. Coincide con el despliegue de SURA/complementos registrado el 21 de septiembre.
- Los archivos y contratos locales están sincronizados con el bundle web. La inspección de Vercel identifica el despliegue; la lectura directa de bytes productivos se efectuó en el worker, que realiza la generación.
- Perfiles estables: `official_2026_gdl` y `sunon_cdmx_v1c`. Sus nombres no son una prueba de revisión del formato.
- La última actualización de plantilla registrada en Git es `48c8aea`: V12 y proveedor Santeco. El commit posterior `597a7c8` corrige fórmulas importadas y copia de complementos, sin sustituir plantillas.

## V12 frente a V13

- Cero diferencias en valores constantes y fórmulas, resueltas las fórmulas compartidas y excluidos sus resultados en caché.
- Cero diferencias de estilos de celda resueltos en coordenadas compartidas; mismas combinaciones de celdas, alturas/atributos de filas, columnas y validaciones convencionales.
- Mismos 31 nombres definidos en cantidad y mismo inventario de 12 hojas; estados de visibilidad y dimensiones coincidentes.
- Imágenes binarias idénticas.
- Dieciocho partes ZIP cambian de bytes. Incluyen orden de combinaciones, cadena de cálculo, metadatos/cachés y datos externos enriquecidos de moneda.
- `xl/richData/rdrichvalue.xml` cambia ocho valores guardados de cotizaciones/fecha. No se adoptó ese caché como tasa transaccional.
- `xl/drawings/drawing3.xml` cambia un desplazamiento horizontal de 0 a 3174 EMU. El segundo dibujo con bytes distintos conserva su estructura XML equivalente.

## Oficial frente a CDMX vigente

- Carátula `Cotizacion`: 186 celdas con diferencias de contenido y 616 coordenadas compartidas con estilos diferentes, coherentes con su variante propia.
- `Mobiliti!P8`: oficial `=Cotizacion!$D$64`; CDMX `=Cotizacion!$D$47`. Es la referencia al selector de entrega de cada carátula. Homologarla sin adaptar el selector rompería esa relación.
- Resto de las hojas compartidas: ninguna diferencia de valores constantes o fórmulas; estilos de celda resueltos coincidentes.
- CDMX agrega `Cantidades Lumbro `, conserva 34 nombres definidos y presenta ajustes de filas/columnas producidos al reconstruirse con Excel. Ejemplos: Mobiliti fila 1, altura 15.95 frente a 15; Fletes columna A, ancho 26.85546875 frente a 26.81640625.
- Se distinguen así igualdad financiera, adaptación necesaria de la carátula e igualdad literal de presentación/pestañas. Esta última no se cumple actualmente.

## Validación y alcance

35 pruebas aprobadas: contratos oficial/CDMX, perfiles y paridad del bundle.

`python -m pytest tests/test_official_template_contract.py tests/test_cdmx_template_contract.py tests/test_template_profiles.py tests/test_vercel_quote_engine_bundle.py -q --basetemp C:\tmp\auditoria-v13-20260923-01 -p no:cacheprovider`

Esta tarea es una auditoría y actualización de contexto. No se sustituyeron los XLSX activos, no se desplegó V13, no se generaron nuevas cotizaciones comerciales y no se modificaron registros de clientes. No se ejecutó un recálculo de vínculos externos ni se certificó una nueva migración V13.

Para una actualización posterior: usar el V13 descargado como base, reconstruir CDMX preservando su carátula, decidir explícitamente el tratamiento de la hoja adicional y de la referencia P8, verificar paridad, generación y Excel, y publicar ambas variantes como una sola versión. Los históricos deben conservarse.

## Evidencia y memoria

Directorio: `C:/Users/pepem/Downloads/ARMADO_DE_CARATULA_prod_git_worktree/output/auditoria-v13-20260923/`.

- `SharePoint-V13-r2.xlsx` y `fuente-sharepoint.json`.
- `comparar.py`, `comparacion.json`, `comparar_presentacion.py` y `comparacion-presentacion.json`.
- La comparación inicial de combinaciones mostraba cambios de orden; la segunda comparación las ordena y confirma igualdad semántica.
- Obsidian registra ahora los cortes V12/Santeco, SURA/complementos y esta auditoría, con enlaces desde el índice.
