# Plantillas SharePoint V11 y CDMX — 8 de septiembre de 2026

Actualización local del generador y sus assets. Sin commit, push ni despliegue.

## Fuente y versiones

Fuente obtenida por MCP SharePoint: [Formato Cotización 2026 V11.xlsx](https://mobiliti11-my.sharepoint.com/:x:/r/personal/joel_meza_mobiliti_mx/_layouts/15/Doc.aspx?sourcedoc=%7B0F63DF46-17C9-4694-A99B-E258C8D118C0%7D).
Fecha de modificación reportada: `2026-09-09T00:42:51Z` (8 de septiembre en México).

| Activo | SHA-256 |
| --- | --- |
| Oficial anterior | `39f5cebd3cbe3e7356f4d4174161e8599bf7158e7b495a789c9fc04850928ee4` |
| SharePoint V11 y oficial actual | `5c27b9b65e6bea45a4bc71950537f700545d964511a7c01991a4f08d06d7c3f1` |
| CDMX anterior | `4babce296ecc9d6941264563644501bb00f50161cdce02d3504b0067788d6320` |
| CDMX actualizado | `baa12821fd301869a3c013b1014ab0b00eb18653fae152808a0f89f38a5d3810` |

La versión nominal V11 es posterior por fecha y contenido a la base local V18.
El oficial se copia byte por byte; CDMX se reconstruye desde esa base mediante
el builder existente y la referencia visual `Formato-Cotizacion-Unico - Sunon-Cdmx-V1C.xlsx`.
Los perfiles públicos conservan sus identificadores para no romper proyectos guardados.

## Comportamiento

- Se reconoce la geometría V11: productos desde fila 15, controles de proveedor
  hasta AN. Las variantes legacy, V17 y V18 conservan sus rutas.
- Las fórmulas de compra, volumen estimado, límite de descuento y margen objetivo
  se clonan de la primera fila oficial a todas las secciones.
- `P10` determina NACIONAL, IMPORTADO o MIXTO a partir de las categorías de los
  proveedores en K. Es fórmula viva; comportamiento elegido expresamente por el usuario.
- Fletes conserva los límites `E67:E68` del V11 y el guard cuando `B61=0`.
- La lista de entrega incorpora Quintana Roo y admite `A46:A56`.
- Se conservan las reglas de precio uniforme por mayor cantidad y composición de
  productos/complementos. La suma de muestra `Cotizacion!F17` de la fuente se
  sustituye por las referencias de los componentes reales de cada partida.
- En la salida, `E9` cubre 15:última fila real y no conserva un valor en caché
  obsoleto. El original archivado mantiene exactamente su fórmula 14:571.
- CDMX conserva logo, condiciones y subtotales propios. Se corrigen fecha,
  encabezado truncado, subtotal `#####`, alturas de separadores y etiqueta USD
  fija: la moneda queda indicada por las condiciones dinámicas de la cotización.

## Validación

- Motor de proyectos: 18 pruebas aprobadas.
- Contratos/perfiles/builder/expansión: 61 pruebas aprobadas; tras los ajustes
  visuales se verificaron nuevamente 55 y luego 31 pruebas relevantes.
- Precios, metadatos financieros y descuentos: 69 pruebas aprobadas y una
  expectativa anterior de flete actualizada al V11, verificada individualmente.
- E2E IZA REFORMA: generación nueva de ambos formatos, 33 productos, 9 secciones,
  33 imágenes procesadas por archivo; seis comprobaciones aprobadas. El caso COM
  de esa suite se omite por ser opt-in; Excel se comprueba por separado.
- API/worker local persistido: casos MXN y USD aprobados; preservación del importado
  y expansión de 700 componentes / 20 secciones / 698 partidas visibles comprobadas
  sobre los archivos recién producidos por la cola local.
- Excel nativo: apertura, cálculo de las hojas financieras, reconciliación entre
  componentes y cotización, exportación PDF y revisión visual. Total coincidente:
  **308254.324224**, mostrado como **308,254.32**. Se comprobaron los tres valores de P10.

Limitación explícita: `CalculateFullRebuild` no dejó el estado global en `xlDone`
tras 120 segundos; quedó `xlPending`. El cálculo dirigido produjo importes
numéricos y reconciliados. No se certifica el recálculo global de las hojas con
vínculos/datos externos. Las pruebas locales no equivalen a un despliegue productivo.

## Evidencia y recuperación

- Referencia, comparativa y respaldos: `output/template-update-20260908/`.
- Respaldo de ambos árboles de plantillas y módulos: subdirectorio `backup/`.
- Excel/PDF final de control: subdirectorio `delivery/`.
- Cotizaciones reales de prueba: subdirectorio `e2e/`.
- La nota 124 en la bóveda `armado-caratula` registra este corte vía MCP Obsidian.
- Supabase MCP confirmó `MOBILITI Cotizador` como `ACTIVE_HEALTHY`. No se cambió
  Supabase, Cloudflare/R2 ni la configuración de producción.

Los cambios y las bajas históricas preexistentes del worktree se conservaron.
