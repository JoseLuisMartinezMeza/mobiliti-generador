# Formatos V12 y proveedor Santeco

Fuente: SharePoint `D3D4CE21-EE69-41E3-959C-1D8EE803254A`, archivo
Formato Cotización 2026 V12.xlsx, revisión 7, modificada el 14 de septiembre
de 2026 a las 01:23:11 UTC (13 de septiembre en México).

El usuario autorizó publicar ambos formatos y sustituir Tarkett MX por Santeco.
El catálogo mantiene la marca Tarkett; su proveedor financiero se normaliza a
Santeco. No se cambian los códigos, existencias ni precios del catálogo.

## Activos

- Oficial: `8d3e80a9f1e1f7741796995910f332753b3a3f31e763244a52e355ddf6b6e132`.
- CDMX derivado mediante el constructor existente:
  `4de304aeabc880158c470feb165bfed72f22e2dba0ee1bed0de1b24f1beb664e`.
- Fuente, contratos y perfiles mantienen paridad con el bundle web.
- Se conservan las 12 hojas, 31 nombres definidos, 12 partes de vínculos
  externos y 1,314 fórmulas SPEC. No cambia la geometría financiera del V11.
- V12 incorpora Santeco en Proveedores y en la fórmula Y de Mobiliti.
  Cotizacion F17:F36 enlaza AA por partida. La repetición de Sonara en Z se
  conserva tal como aparece en la fuente, sin reinterpretar la regla comercial.

## Validación y operación

Las pruebas cubren contratos, propagación de Santeco, generación mixta,
cantidades, monedas y paridad del bundle. La expectativa histórica de AD en
la prueba mixta se actualiza a `MIN($E$5,ALfila)`, fórmula comprobada tanto en
el respaldo V11 como en V12; no se modifica ese cálculo en producción.

La prueba productiva usa una cuenta QA independiente, dos productos y ambos
formatos; evita la cuenta comercial, cuyo historial ya alcanza el límite de
cinco cotizaciones. Los archivos QA se conservan y la cuenta se desactiva
después de probar. No se modifica la retención productiva.

Se conserva `excludeFiles: {public/catalog-assets/**,dist/**}` en Vercel para
que los estáticos no se vuelvan a incluir en el paquete Python. La actualización
no requiere replicar catálogos en Supabase ni en R2. Publicar una sola versión
de Vercel y promover el mismo artefacto evita compilaciones duplicadas.

Respaldos y evidencia detallada:
`output/plantillas-v12-20260913/` y `output/release-v12-20260914/`.
