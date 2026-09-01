# Task 2 — `no_changes` sin Storage de assets

## Alcance

- `mobiliti_saas/worker/catalog_sync/service.py`
- `mobiliti_saas/worker/catalog_sync/repository.py`
- `tests/test_catalog_sync_service.py`
- `tests/test_catalog_repository.py`
- Este informe.

No se añadieron otros archivos. No hubo DDL, cambios remotos, despliegue, push, ni cambios de polling.

## TDD

### RED (antes de producción)

Comando:

```powershell
python -m pytest tests/test_catalog_sync_service.py -q -k "alma_sidecar_no_changes_does_not_store_assets_or_stage or sidecar_change_stores_only_asset_missing"; python -m pytest tests/test_catalog_repository.py -q -k "store_catalog_asset_conflict"
```

Resultado: código de salida 1.

- Servicio: 2 fallos. El sync idéntico registró `store_catalog_asset_if_absent` antes de `finish_no_changes`; un cambio con dos assets reintentó también la clave ya referenciada por el snapshot publicado.
- Repositorio: 5 fallos. El 409 descargaba el cuerpo mediante `GET /authenticated/catalog-assets/...`; por ello no pudo aceptar la metadata HEAD compatible y las pruebas detectaron que el método era GET en vez de HEAD.

### GREEN

Comando dirigido:

```powershell
python -m pytest tests/test_catalog_sync_service.py -q -k "alma_sidecar_no_changes_does_not_store_assets_or_stage or sidecar_change_stores_only_asset_missing"; python -m pytest tests/test_catalog_repository.py -q -k "store_catalog_asset_conflict"
```

Resultado: 2 passed y 5 passed.

Regresión proporcional:

```powershell
python -m pytest tests/test_catalog_sync_service.py tests/test_catalog_repository.py -q
```

Resultado: `269 passed in 6.10s`.

## Decisiones implementadas

1. Se valida el candidate, se calculan métricas de assets, se lee y valida el snapshot publicado, se preservan visuales curados, se recalcula/valida el candidate y se clasifica el diff antes de cualquier escritura de `catalog-assets`.
2. Si la identidad es igual, sólo se termina la corrida con `finish_no_changes`; no hay `store_catalog_asset_if_absent`, stage ni publicación. Al no invocarse storage de asset, tampoco se solicita un cuerpo de asset.
3. Con un cambio real, se compara cada `sha256.png` planeado contra las referencias `approved_asset` válidas del snapshot publicado y sólo se intenta almacenar la clave faltante. La escritura conserva `x-upsert: false`.
4. Tras 409, no se hace GET de objeto. Se usa `HEAD /storage/v1/object/catalog-assets/<nombre>` con respuesta vacía y se acepta exclusivamente si coinciden de forma exacta `x-amz-meta-sha256`, `Content-Length` y `Content-Type`. Las discrepancias y la metadata ausente fallan cerradas.
5. La subida nueva declara `x-amz-meta-sha256`; no se equipara ETag con SHA.

## Compatibilidad y riesgos residuales

- La recuperación idempotente de un 409 depende de que el proveedor preserve y exponga por HEAD `x-amz-meta-sha256`, además de tamaño y MIME. Un asset legado o proveedor que omita cualquiera de esos campos queda bloqueado de forma segura; no existe fallback de descarga de cuerpo. La futura registry de assets (Task 3) puede aportar una fuente persistente alternativa para legados, pero no se construyó especulativamente aquí.
- `catalog-sources` permanece privado y sin materializador/migración nueva; no se hizo trabajo especulativo en ese bucket.
- El polling se mantiene en 10 s: Gate 0 no confirmó SLA para 60 s.
- No se alteraron contratos de reintento/stage: el retry de `stage_candidate` sigue igual y las escrituras de assets siguen siendo put-if-absent.

## Validaciones antes del stage

- `python -m py_compile mobiliti_saas/worker/catalog_sync/service.py mobiliti_saas/worker/catalog_sync/repository.py`: salida 0.
- La regresión fresca tras aislar SHA/tamaño/MIME: `269 passed in 6.37s`.
- `git diff --check` global reportó whitespace exclusivamente en archivos ajenos preexistentes de otros Tasks (`task-1-brief.md`, `task-5-report.md`, `task-6-brief.md`, `task-7-brief.md`). No se modificaron ni se corrigieron para preservar su autoría. La comprobación se repetirá limitada a los cinco archivos de Task 2 tras el stage.
