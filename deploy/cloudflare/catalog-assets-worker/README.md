# Gateway de assets de catálogo

Worker mínimo de sólo lectura para `catalog-assets`, publicado únicamente mediante
`workers.dev`. El binding privado `CATALOG_ASSETS` no debe compartir bucket ni
credenciales con `quote-files`.

## Caché y validación

Workers Caching se sitúa delante del handler. La validación de metadatos y el
stream de cuerpo ocurre sólo durante un **fill/miss**, antes de devolver un 2xx
inmutable. Un **HIT sirve la representación ya validada** e inmutable por ese
fill/miss; no vuelve a invocar el Worker ni a validar R2.

`GET` y `HEAD` para la misma URL comparten una sola entrada. Un `HEAD` frío se
normaliza internamente a `GET` antes del handler para llenar la entrada con el
asset completo; el handler directo conserva `head()` para pruebas locales y
rutas no cacheadas, pero no modela ese HEAD frío de producción.

Sólo un 2xx validado usa `public, max-age=31536000, immutable`. Errores y
preflights siempre responden `Cache-Control: no-store`.

## Workers Free

El plan Free aplica su límite de CPU de 10 ms automáticamente. No se debe añadir
`[limits]`/`cpu_ms` a `wrangler.toml`: Cloudflare sólo admite configurar ese
campo en Workers Paid y rechaza el despliegue Free con el error `100328`.

Ejecutar las pruebas locales sin dependencias:

```powershell
npm test
```

No ejecutar despliegues ni autenticación desde este directorio sin completar los
gates operativos de la migración.
