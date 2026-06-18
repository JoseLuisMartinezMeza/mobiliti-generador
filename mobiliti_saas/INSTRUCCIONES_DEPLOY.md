# Instrucciones De Deploy Mobiliti SaaS

Esta version final usa:

- Web React/Vite: `mobiliti_saas/web`
- API Vercel: `vercel_deploy`
- Worker Docker/cloud: `mobiliti_saas/worker`
- Motor de cotizacion: `mobiliti_saas/quote_engine`
- `QUOTE_ENGINE=python`

No requiere Windows, Microsoft Excel ni `xlwings` para generar cotizaciones.
El cliente desktop y el generador Windows quedaron archivados en
`versiones historial/HISTORIAL DE VERSIONES/`.

La guia operativa actual es:

- `mobiliti_saas/CLOUD_DEPLOY.md` para produccion.
- `mobiliti_saas/DEV_LOCAL.md` para probar local.
- `scripts/saas_doctor.py` y `scripts/verify-saas.ps1` para verificaciones.

Antes de produccion:

1. Rotar cualquier clave compartida por chat.
2. Configurar Supabase y bucket privado `quote-files`.
3. Desplegar API desde `vercel_deploy`.
4. Desplegar web desde `mobiliti_saas/web`.
5. Desplegar worker Docker con `QUOTE_ENGINE=python`.
6. Ejecutar smoke test con una `Quotation.xlsx` real.
