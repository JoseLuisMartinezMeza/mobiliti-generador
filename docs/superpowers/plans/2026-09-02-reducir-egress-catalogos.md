# Reducción de egress de catálogos y worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Reducir las lecturas redundantes de Supabase sin cambiar resultados, permisos, frescura ni generación de cotizaciones.

**Architecture:** PostgreSQL conserva la autoridad y los punteros vigentes. Una caché compartida privada en el R2 de cotizaciones conserva snapshots inmutables comprimidos por namespace/proveedor/revisión, detrás de metadatos actuales de Supabase; memoria acotada evita lecturas R2 repetidas. El worker compara metadatos pequeños y separa recuperación de leases de consulta de cola.

**Tech Stack:** Python, FastAPI, urllib, boto3 ya instalado, PostgreSQL/PostgREST, Cloudflare R2, pytest.
**Spec:** output/diagnostico-egress-limite-20260902.md y solicitud del usuario del 2 de septiembre: “quiero que resuelve el temas del consumo del egree para que sea lo minimo uso de egree y funcional”. Pro confirmado por MCP.

**Estado operativo:** implementado y desplegado en `ea267c79d10481363169b073678353f035ef4bfd`; 497 regresiones y una cotización sintética descargada/verificada. Ver [cierre y rollback](../reports/2026-09-02-egress-pro-production.md). No repetir el corte ni las escrituras de prueba. Gate 9/facturación representativa siguen pendientes.

## Global Constraints

- Base ccd8288cc27dfe434a78f5ef2ecfa270ebff785c; trabajar en C:/Users/pepem/Downloads/ARMADO_DE_CARATULA_prod_git_worktree, rama codex/offiho-catalog-20260709. Es un worktree vinculado y la versión productiva más reciente comprobada.
- No borrar permanentemente archivos, objetos, contenedores ni worktrees; preservar cambios ajenos. La excepción anterior corresponde sólo a una prueba E2E identificada, no a cualquier limpieza.
- Usar apply_patch; stage/commit exclusivamente archivos de esta tarea. Ningún agente secundario despliega, accede a producción, imprime secretos ni despacha subagentes.
- Preservar las tres copias idénticas del API: mobiliti_saas/api/index.py, mobiliti_saas/web/api/index.py, vercel_deploy/api/index.py. No reestructurar el monolito.
- Sin dependencias nuevas, esquemas/RLS nuevos, compra de servicios, bucket público nuevo ni cambios al Cloudflare Worker público.
- No cachear autenticación, suscripciones, permisos, reservas ni estado de jobs. No cambiar precios, fórmulas, plantillas ni semántica de búsqueda.
- La caché de snapshots R2 sólo usa el bucket privado existente de cotizaciones y su cliente autenticado; nunca CATALOG_ASSET_BUCKET ni URLs públicas/pre-firmadas. Activación controlada por CATALOG_SNAPSHOT_CACHE_ENABLED, default false, y configuración R2 válida.
- Consultar metadatos autoritativos antes de reutilizar snapshots: despublicación, proveedor deshabilitado, versión nueva y revisión legacy nueva deben invalidar la reutilización. No servir obsoletos ante error de validación/autorización.
- Fallback a lectura DB si R2 está ausente, corrupto, denegado o falla, con logging acotado sin secretos. Un fallo de lectura DB se propaga; nunca convertirlo en snapshot vacío.
- Tests nuevos prioritariamente en memoria, sin red y sin tmp_path; para pruebas con archivos usar el runner de reciclaje verificado del controlador. No ejecutar una limpieza permanente por medio de pytest/openpyxl.
- Conservar el directorio SDD al finalizar: su borrado propuesto por la skill contradice la política global.
- Despliegue y medición los coordina la raíz después de revisión, pruebas y comprobación de que la cola no tiene trabajos en proceso.

### Task 1: Caché privada de snapshots e integración API

**Files:**
- Create mobiliti_saas/quote_engine/snapshot_cache.py.
- Incluir copia byte-idéntica en mobiliti_saas/web/mobiliti_saas/quote_engine/snapshot_cache.py: ese runtime Vercel está versionado, no se genera al desplegar. Verificar paridad/importación empaquetada y cualquier runtime equivalente realmente usado por vercel_deploy.
- Modify las tres copias API confirmadas por hash.
- Create tests/test_snapshot_cache.py y tests/test_api_snapshot_egress.py.
- Update sólo pruebas existentes de snapshots cuyo contrato de consulta cambia legítimamente.
- No modificar quote_worker.py (Task 2).

**Interfaces:**
- Producir SnapshotCache.load(*, namespace: str, supplier: str, revision: str, loader, validator, client_factory=None, bucket: str = "") -> dict | None.
- loader() obtiene el snapshot autoritativo cuando falta la caché; validator(row) comprueba identidad y revisión. No cachear None ni filas que no satisfacen validator; no ocultar errores de loader.
- Instancia con memoria LRU acotada a 32 snapshots y serialización UTF-8/gzip determinista. Evitar mutación compartida devolviendo copia o bytes deserializados; lock acotado para evitar doble carga concurrente del mismo proceso.
- Prefijo R2 interno `internal/catalog-snapshots/v1/`; keys derivadas con SHA-256 de namespace/supplier/revision, no paths de entrada. Máximo 32 MiB descomprimidos y 8 MiB comprimidos, lectura acotada y Body cerrado.
- Contadores observables de memory_hit/r2_hit/db_load/cache_error y bytes de contenido cargado desde loader, sin asumir bytes facturados.
- R2 GET/PUT autenticados; ContentType application/json, ContentEncoding gzip, CacheControl private,no-store. Integridad SHA-256 en metadata, envelope con identidad completa. No sobrescribir objetos existentes: usar IfNoneMatch="*" y aceptar sólo el conflicto de escritura concurrente como tal.
- API usa namespace Supabase URL normalizada (o hash de DATABASE_URL si sólo Postgres) y el R2_BUCKET privado mediante _r2_client.
- Legacy revisión formada por source_hash + updated_at (no sólo TTL ni hash). Supabase GET previo solicita únicamente supplier,source_hash,generated_at,updated_at. Loader condicionado por la revisión o comprobación final; si hay carrera de publicación no cachear una revisión bajo la clave equivocada y reintentar lectura de metadatos una vez.
- Moderno conserva validación de published/enabled/ID actual, no devuelve snapshot anterior por tenerlo en R2. Si precisa lectura metadata id/supplier/status, ésta nunca incluye payload.
- Upserts del API evitan bajar el payload previo y evitan devolverlo desde PostgREST innecesariamente; devolver contrato completo al consumidor combinando datos ya enviados y metadatos de respuesta.
- No cambiar TTL público ni cachear respuesta autenticada HTTP globalmente.

- [x] Escribir pruebas RED: segunda instancia del caché usa el mismo fake S3 realista sin segunda lectura DB; misma instancia hace memory hit; distinta revisión/namespace/proveedor no reutiliza; cambio legacy y despublicación se reflejan; corrupción/truncamiento/tamaño/403 de R2 recurre a DB; error DB se propaga; mutación del resultado no contamina; concurrencia sólo carga una vez; ninguna consulta metadata contiene payload; parámetros de R2 nunca son públicos; upsert no descarga/devuelve payload remoto redundante.
```python
def test_second_process_uses_private_cache_without_second_database_download():
    reads = []
    def load():
        reads.append(1)
        return {"supplier": "offiho", "source_hash": "v1", "payload": {"items": []}}
    kwargs = dict(namespace="https://example.supabase.co", supplier="offiho",
                  revision="v1:2026-09-02", loader=load,
                  validator=lambda row: row.get("source_hash") == "v1",
                  client_factory=lambda: fake_s3, bucket="quote-files")
    first = SnapshotCache().load(**kwargs)
    second = SnapshotCache().load(**kwargs)
    assert first == second
    assert len(reads) == 1
```
- [x] Ejecutar RED con `python -B -m pytest -p no:cacheprovider tests/test_snapshot_cache.py tests/test_api_snapshot_egress.py -q`; documentar motivo de fallo, no usar fallos por typo.
- [x] Implementar el flujo acotado sin introducir un servicio paralelo:
```python
metadata = read_current_metadata_without_payload()
revision = revision_from_metadata(metadata)
row = cache.load(namespace=namespace, supplier=supplier, revision=revision,
                 loader=read_exact_snapshot, validator=matches_metadata,
                 client_factory=private_r2_factory, bucket=private_bucket)
```
Los nombres read_current_metadata_without_payload/read_exact_snapshot/matches_metadata son el algoritmo local a concretar dentro de las funciones API existentes; la interfaz compartida exigida es SnapshotCache.load.
- [x] Ejecutar GREEN y regresiones de API/búsqueda, comprobar SHA idéntico en las tres copias; documentar bytes DB evitados con fake con carga conocida.
- [x] Commit sólo cambios propios y escribir task-1-report.md con RED/GREEN, comandos, hashes, riesgos. Sin deploy.

### Task 2: Worker sin descargas legacy horarias repetidas ni polling pesado

**Files:**
- Modify mobiliti_saas/worker/quote_worker.py.
- Modify mobiliti_saas/worker/render_web_worker.py sólo para aplicar la misma cadencia de recuperación y logging idle al wrapper aislado que arranca Docker en producción.
- Create tests/test_worker_egress.py.
- Modify tests/test_quote_worker.py únicamente para contratos legítimamente cambiados.

**Interfaces:**
- Consumir SnapshotCache de Task 1 con la misma revisión, namespace y prefijo. Memoria debe persistir entre nuevos SupabaseClient creados por run_once.
- Mantener catalog_snapshot_get(supplier) -> dict | None, catalog_snapshot_upsert(supplier,payload) -> dict.
- En backend REST con service key, leer sólo metadatos antes de SnapshotCache; conservar fallback autenticado de internal API cuando no hay service key y backend PostgreSQL/local.
- No desactivar ni espaciar Offiho 3600 s ni Tarkett 21600 s; las comprobaciones de inventario siguen ejecutándose. Snapshot sin cambios no se vuelve a bajar desde DB. Payload cambiado debe aplicarse en la siguiente comprobación.
- Evitar representación completa de payload en respuesta de upsert; mantener las garantías de éxito y el contrato consumido.
- fetch_next_job solicita sólo id: claim_job ya devuelve la fila completa autoritativa antes de process_job. Verificar cada consumidor antes de estrechar.
- recover_stale_jobs solicita id,status,attempt_token,lease_expires_at,updated_at, no payload ni metadata.
- En run_once, recuperar leases como máximo cada 60 segundos (primer ciclo siempre), consulta queued cada 10 segundos como hoy. No ralentizar la detección de jobs nuevos, no cambiar leases ni heartbeat.
- La misma garantía se exige en render_web_worker._has_pending_job/_run_once_isolated con WORKER_ISOLATE_JOBS=true, no sólo en quote_worker.run_once. Probar el entrypoint real con reloj controlado y sin subprocesos/red reales.
- Reducir respuestas del heartbeat sólo si los tests y consumidores prueban que los campos retornados necesarios se mantienen; no es obligatorio cambiarla si exige modificar contratos ajenos.
- Evitar logs por cada ciclo idle; registrar sólo cambios de estado o heartbeat agregado a intervalos, conservando errores útiles.

- [x] RED con respuestas HTTP/S3 en memoria: dos clientes nuevos y 24 comprobaciones metadata constantes causan una descarga, revisión distinta causa otra y el contenido/stock nuevo se observa; fallo de autorización no sirve caché obsoleta; upsert no transfiere respuesta pesada.
```python
def test_idle_cycles_keep_fast_queue_polling_but_bound_recovery(monkeypatch):
    # Reloj controlado: t=0,10,20,30,40,50,60; dependencias de red sustituidas.
    for second in (0,10,20,30,40,50,60):
        clock.value = second
        worker.run_once()
    assert queue_poll_times == [0,10,20,30,40,50,60]
    assert recovery_times == [0,60]
```
- [x] RED de proyección: queued sólo id, processing sólo campos de lease; claim conserva inputs, metadata, output path y usuario; test existente de recuperación/heartbeat pasa.
- [x] Ejecutar tests focalizados, implementar metadatos + caché persistente + cadencia de recuperación, volver a ejecutarlos GREEN.
- [x] Ejecutar regresiones worker bajo reciclaje, no cambiar DEV en producción, no modificar expiración/retención de clientes.
- [x] Commit sólo archivos propios, task-2-report.md con ahorro de contenido simulado (24x frente a 1x), pruebas y regresiones.

### Task 3: Verificación integrada, despliegue coordinado y documentación (raíz)

**Files:**
- output/optimizacion-egress-pro-20260902.md y docs/superpowers/reports/2026-09-02-egress-pro-production.md.
- Plan y ledger de esta implementación; contexto MCP Obsidian.
- Scripts operativos sólo si necesarios, revisados antes de ejecutar.

- [x] Revisión global del diff contra base ccd8288, no repetir reviews completos en cada corrección.
- [x] Regresión proporcional con runner que recicla únicamente temporales de su directorio validado. Conservar artefactos, reportar fallos reales y skips.
- [x] Verificar acceso privado y ausencia de exposición pública del bucket existente antes de activar CATALOG_SNAPSHOT_CACHE_ENABLED. No crear claves nuevas.
- [x] Capturar baseline de pg_stat_statements (IDs de SELECT payload modernos y legacy), versión/health, colas y plan Pro. No resetear estadísticas. El contador de facturación disponible es histórico y se dejó fechado; la UI exige nueva sesión.
- [ ] Comparar el contador Pro actualizado durante uso representativo (Gate 9 pendiente; automatización previa PAUSED). No extrapolar porcentaje de factura desde estas sondas.
- [x] Vercel preview sin cambiar aliases productivos; activar flag sólo en despliegue canary por override. Un proveedor pequeño y una segunda instancia/fresh process demuestran R2 hit sin nueva consulta de payload. Sumar bytes de prueba.
- [x] Worker nuevo construido desde commit exacto; comprobar cola vacía y reemplazar conservando contenedor, imagen y ENV anteriores. Cambiar sólo el flag de caché tras backup.
- [x] Promover Vercel y flag persistente, conservando release rollback; verificar health y jobs. No ejecutar scripts viejos de corte sin revisar.
- [x] Validar búsqueda, cambio de revisión offline y una cotización de prueba identificada con permisos ya dados, sin tocar datos reales. No confundir estado completed con validación de Excel/descarga.
- [x] Medir llamadas completas y metadata antes/después de sondas acotadas, comprobar R2 hit, worker real y ausencia de 4xx/5xx. La factura tiene retraso: no prometer egress cero o porcentaje mensual sin muestra.
- [x] Actualizar Obsidian por MCP con HEAD, config no secreta, baseline, resultados medidos, rollback y pendientes; mantener monitor PAUSED salvo instrucción de reanudarlo. No crear duplicado.
