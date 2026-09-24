# Plantillas V13 — oficial y CDMX

El usuario autorizó actualizar producción y verificar el flujo completo después
de la auditoría V13. Se adopta la revisión 4.0 del archivo de SharePoint
`F466574F-28B9-446A-958E-FAAC71061356`, modificada el 24 de septiembre de 2026
a las 02:38:11 UTC (23 de septiembre en México).

## Activos

- Oficial: `0bb1c9843438502d023ca24defccccbf2cd7ea0ae4aa0dc7b792b5d9c4005078`.
- CDMX: `bfd41335def60fd11a450b6115d5ba79631044336ec32421e91c7702e5d27ad3`.
- Los perfiles estables siguen siendo `official_2026_gdl` y `sunon_cdmx_v1c`.
- Activos, contratos y perfiles se sincronizan con el bundle web.

## Compatibilidad

V13 revisión 4 conserva los valores constantes, fórmulas, estilos de celda e
imágenes de las 12 hojas oficiales de V12. Sus bytes difieren por datos externos
guardados de moneda, cachés, metadatos y detalles menores de dibujo. Los datos
guardados del libro no sustituyen la política transaccional de tipos de cambio.

CDMX se reconstruye desde V13 con el constructor existente. Conserva su
carátula y su hoja auxiliar `Cantidades Lumbro `. Las hojas compartidas tienen
los mismos valores y fórmulas, salvo `Mobiliti!P8`, que apunta a `Cotizacion!D47`
en CDMX y a `D64` en el oficial. Esa diferencia enlaza el destino de entrega de
cada carátula; Excel también puede cuantizar dimensiones al guardar.

El constructor copia la validación oficial del destino en vez de reconstruir
su referencia entre hojas mediante `Validation.Add`, que Excel rechazaba.
Descombina y vuelve a combinar el destino para conservar la validación en
`D47`, como exige el compositor. La lista conserva `Fletes!$A$46:$A$56`.

## Evidencia y conservación

`output/plantillas-v13-20260923/` conserva la fuente de SharePoint, comparación,
candidatos, contratos, respaldos V12 y resultados de pruebas. Las generaciones
locales de ambos perfiles se abren y recalculan en Excel; cantidades 10/20/3 y
totales de complementos conciliados. Los intentos fallidos del constructor se
conservan como evidencia y no son activos productivos.

La prueba productiva usa una cuenta QA temporal, un proyecto propio y ambos
formatos, sin consumir la retención de la cuenta comercial. El informe final
de publicación y E2E se registra en ese directorio y en Obsidian mediante MCP.
La auditoría previa `2026-09-23-auditoria-v13-cdmx.md` describe el estado V12
anterior a esta actualización y conserva ese valor histórico.
