# Optimización de egress Pro — cierre operativo

Fecha local: 2026-09-02 (evidencia de 2026-09-03 UTC).

## Resultado

Optimización en producción y validada por API, worker y descarga de Excel. No se afirma ahorro mensual facturado ni Gate 9 completado.

- Código productivo: `ea267c79d10481363169b073678353f035ef4bfd`.
- Rama y worktree: `codex/offiho-catalog-20260709`, `C:/Users/pepem/Downloads/ARMADO_DE_CARATULA_prod_git_worktree`.
- Supabase real: `hcdspekajlszcycecpml`; plan Pro contratado por el usuario y confirmado por MCP.
- Vercel: `dpl_F9zDvLuKNKrg5or3fj5yJnaTXJpk`, Production/READY. [Aplicación](https://web-lemon-one-45.vercel.app).
- Hetzner: CURRENT EA267; imagen `sha256:e2b3735e47b47c10efce2c607b6302e720483dfa34a3e8febec88d2cd3d7fb4c`, healthy.
- `CATALOG_SNAPSHOT_CACHE_ENABLED=true` en ambos entornos; sin compras ni nuevos servicios/buckets públicos.

## Qué cambió

La migración de imágenes a R2 no eliminaba el JSONB descargado desde Supabase. El desglose previo del 2 de septiembre era 99% PostgREST. Una búsqueda con `limit=1` podía cargar el catálogo entero en cada proceso nuevo; los 11 snapshots sumaban 8.36 MB. Offiho descargaba su snapshot antes de comparar el inventario horario.

Ahora los snapshots se reutilizan en memoria y en el R2 privado existente, comprimidos e inmutables por namespace/proveedor/revisión, con SHA-256 y límites de tamaño. Supabase sigue siendo la autoridad: metadatos antes/después evitan reutilizar datos despublicados, de un proveedor deshabilitado o de otra revisión. R2 fallido recurre a DB; errores DB no se ocultan. No se cachean permisos, suscripciones, reservas ni estados de trabajos.

Queued sigue cada 10 s y sólo selecciona ID; recuperación cada 60 s selecciona cinco campos de lease. Offiho 3600 s y Tarkett 21600 s no cambiaron. Se cubrió el wrapper aislado real de Hetzner, además de REST/PostgreSQL y empaquetado Vercel.

## Evidencia

| Sonda de proceso nuevo | JSON del snapshot | Gzip R2 | Metadata JSON total | Nuevas lecturas de payload DB |
|---|---:|---:|---:|---:|
| Jome | 1,069,413 B | 53,187 B | 936 B | 0 |
| Offiho | 1,654,130 B | 98,013 B | 627 B | 0 |
| Tarkett | 57,038 B | 5,386 B | 630 B | 0 |

Cada sonda: un hit R2 y uno en memoria; cero errores. Se prohibió el loader DB para comprobarlo. Los bytes son contenido, no factura; el llenado inicial sí tuvo lecturas DB y está conservado en los contadores.

- `pg_stat_statements` no reiniciado. Moderno queryid `3721246906022668202` permaneció en 4143 y legacy `6142222966051997748` permaneció en 1199 durante las sondas finales y el E2E.
- Ventana de 130.35 s: 13 consultas ligeras de cola y 2 de recuperación; cola vacía y sin nuevas descargas completas.
- Imágenes: HIT/ETag/hash válidos; `public, max-age=31536000, immutable`.
- Regresión afectada: 497 pruebas pasadas en 116.95 s, revisión global Approved; repetición focalizada 49/49 en 1.48 s. No es toda la suite histórica.
- Sonda operativa: 27 casos de reanudación offline y self-test global aprobados contra contratos reales de metadata y GET/PATCH.
- E2E: un proyecto y una cotización sintéticos. Job `3db2e457-cafd-4a67-87a3-28107e08f38e`, completed a las 01:02:23 UTC, generación 13.9 s. Un artículo Jome, cantidad 1, perfil GDL v18.
- Descargado desde R2: Excel de 17,641,715 B, SHA-256 `cd4040298eafc9e55a86f28f997c384a44391493fc8efcaab816ce8532eb26c4`; 14 hojas, fórmulas, CRC/XML, cantidad e imagen en B17 verificados. Sin recalcular en Excel ni recorrido de UI gráfica.
- Usuario técnico 22 desactivado, suscripción 21 suspendida. Input/temporales del único job se limpiaron conforme a excepción autorizada; Excel, registros y backups conservados. Sin cambios a datos reales.
- Dominio principal health 200 y búsqueda sin token 401. Alias secundario mismo deployment; conserva SSO y responde health 200 con la autenticación Vercel existente. Cero 5xx en muestra de 20 min posterior al E2E.

La primera sonda falló por un atributo inexistente del propio diagnóstico. La primera solicitud de cotización fue 400 por cuatro campos obligatorios vacíos del fixture; se repararon sólo esos campos del mismo proyecto, revisión 0→1. No se relajó ninguna validación ni se duplicó el proyecto/job. Los reportes fallidos se conservan.

## Rollback conservado

- Vercel anterior `dpl_8Ph7NLVMjKmkeztsH2n5EkAiJMm5`; si se revierte, contemplar ambos aliases. Preview independiente nunca se promovió a producción.
- Worker anterior `mobiliti-worker-backup-egress-20260903T004701-3b6711f091ee`, imagen `sha256:4ee2dbd9768d4f2ca6b49937b6d76d47a1006d4b2ade208c4924d1ac1d07e92a`.
- ENV backup `/etc/mobiliti-worker/backups/worker.env.egress-apply-20260903T004702376350Z.bak`, hash previo `3be784cb3df56ae3fb38f41afb5be3dc59f2f855c3833452c341c8bd8cf8ad93`. ENV activo root:0600, hash `da0fbb2e3957b616d7bf66bf8fec405d7c4ed2d13baa73ae6fcd6831336865d1`.
- Verificar cola, identidad y cambios concurrentes antes de revertir; preservar el contenedor actual. Sin reconstrucción, limpieza destructiva ni repetición del corte de uso único.

## Pendiente y ubicación de evidencia

Gate 9 y comparación de facturación durante uso representativo siguen pendientes. El panel Usage requiere iniciar sesión; los 4.995 GB anteriores son históricos del plan Free, no consumo actualizado Pro. No prometer egress 0 ni porcentaje mensual. Permisos y datos actuales siguen necesitando consultas; versiones nuevas requieren carga inicial. Vigilar también operaciones/crecimiento de R2; alerta de USD 1 no es un tope de gasto. Automatización anterior permanece PAUSED, sin duplicados.

- Informe detallado local: `output/optimizacion-egress-pro-20260902.md`.
- Contadores/sondas: `output/egress-live-evidence-20260903.json` y `output/egress-final-verification-20260903.json`.
- E2E: `output/gate8-egress-prod-202609030048-resume/report.json` y `quote.xlsx`.
- Obsidian MCP: `armado-caratula/Optimizacion-egress-Pro-2026-09-02.md` y plan 121 de migración.
