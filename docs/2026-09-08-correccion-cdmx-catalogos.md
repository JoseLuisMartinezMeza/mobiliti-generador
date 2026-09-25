# Corrección CDMX V11 y catálogos locales

El arranque anterior comprobó servicios, pero omitió probar catálogos. Los once snapshots locales estaban presentes; faltaba el índice `catalog_sources` exigido por la API. El script `scripts/prepare_local_catalog_sources.py`, invocado por `scripts/dev-start.ps1` antes del arranque, prepara ese índice únicamente cuando no existe y respalda el almacén. Conserva fuentes deshabilitadas y decisiones de publicación existentes.

CDMX ahora incorpora el selector de entrega V11 en D47, enlazado a Mobiliti!P8 y al cálculo de flete. La fila se desplaza con los productos. Su valor predeterminado es CDMX; se respetan destinos explícitos. La salida oficial también elimina el texto fijo Irapuato y sigue al selector. Se incorpora el aviso de autorización de Control Administrativo antes de las firmas. Se compararon las demás condiciones, estilos y alturas contra el original `Formato-Cotizacion-Unico - Sunon-Cdmx-V1C.xlsx`; pago 70/20/10, plazo 10–12 semanas, showroom y datos de CDMX se conservan.

Activo CDMX final y su copia web: SHA-256 `84371cd43cc944691c1a82a1a874c09125c4cde007b16ad640ef90a07dfa1dfe`. El activo oficial conserva su hash `5c27b9b65e6bea45a4bc71950537f700545d964511a7c01991a4f08d06d7c3f1`.

## Evidencia

- 13 catálogos HTTP correctos: once proveedores con 2,805 variantes, Tarkett 125 y Offiho 1,288. Labenze comprobado en navegador con sus 462 productos, imágenes y precios.
- Prueba web real: proyecto `QA CDMX V11 catálogos 2026-09-08`, producto Labenze 101-0220G, formato CDMX. Job final `7e9f4465-dcbf-468a-9476-76dcf996a1ce`, generación y descarga completadas.
- Excel nativo sobre la descarga final: NACIONAL, CDMX, Labenze, total 3,293.6112 MXN. Selector y estado de firmas presentes. PDF revisado.
- E2E IZA REFORMA: 33 productos y 33 imágenes, nueve secciones; 6 passed, 1 skipped, 269.48 s. El omitido es COM opt-in, comprobado por separado. Esta ejecución precede al último ajuste de color; el activo final fue comprobado nuevamente por web y Excel.
- Contratos y semántica: 52 aprobados inicialmente; la expectativa de merges del selector se actualizó y pasó. Builder, presentación y snapshots: 44 aprobados, más la prueba de rollback aprobada en una ruta corta tras exceder MAX_PATH. Activos finales y perfiles: 29 aprobados.
- Excel comprobó CDMX/Nuevo León/Quintana Roo: destino y flete cambian conjuntamente. Cálculos dirigidos comprobados; no se repitió la actualización global de vínculos externos pendiente del informe anterior.
- `git diff --check` limpio en los archivos de esta corrección. Se conservaron cambios previos ajenos en el worktree.

Evidencia en `output/cdmx-revision-20260908/`; entregables finales en `delivery-final/Cotizacion_CDMX_catalogo_web_verificada.xlsx` y `delivery-final/Cotizacion_CDMX_catalogo_web.pdf`.

Respaldo de datos: `.mobiliti_dev_store/db.before-catalog-sources-20260908-201931-862765.json`. Activos previos: `output/cdmx-revision-20260908/backup/`. Notas Obsidian 125 y 00-Home actualizadas; nota 124 marcada como corregida.

Local activo: web `http://127.0.0.1:5174/`, API `http://127.0.0.1:8000/`, worker Python. No se modificó producción. Crear una cotización nueva para utilizar el formato corregido.
