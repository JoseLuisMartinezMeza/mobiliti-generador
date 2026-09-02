# Gateway de assets de catálogo

Worker mínimo de sólo lectura para `catalog-assets`, publicado únicamente mediante
`workers.dev`. El binding privado `CATALOG_ASSETS` no debe compartir bucket ni
credenciales con `quote-files`.

Ejecutar las pruebas locales sin dependencias:

```powershell
npm test
```

No ejecutar despliegues ni autenticación desde este directorio sin completar los
gates operativos de la migración.
