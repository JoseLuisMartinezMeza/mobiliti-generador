# Task 1 — Caché privada de snapshots

Estado: DONE.

## Implementación

- Se añadió `SnapshotCache`: LRU en memoria de 32 entradas, lock de proceso, copias aisladas, envelope con identidad completa y gzip UTF-8 determinista.
- Las claves R2 usan `internal/catalog-snapshots/v1/<sha256>.json.gz`; no se derivan de entradas como paths. Se limita la lectura a 8 MiB comprimidos y 32 MiB descomprimidos, se cierra `Body` y se comprueba SHA-256 de metadata.
- La caché sólo se activa con `CATALOG_SNAPSHOT_CACHE_ENABLED=true`, namespace de BD y R2 válido. Usa exclusivamente `R2_BUCKET`, rechaza `CATALOG_ASSET_BUCKET`, y escribe con `IfNoneMatch="*"`, `application/json`, `gzip`, `private,no-store`.
- Los catálogos modernos consultan metadatos publicados sin payload antes de cada reutilización y reintentan una carrera de publicación una vez. Los legacy usan `source_hash + updated_at`; sus upserts limitan su respuesta a metadata y recomponen el contrato localmente.
- Se preservó la paridad byte a byte de las tres APIs y se agregó la copia exacta en `mobiliti_saas/web/mobiliti_saas/quote_engine/` (no hay runtime análogo de motor en `vercel_deploy/`).

## TDD y validación

- RED: `python -B -m pytest -p no:cacheprovider tests/test_snapshot_cache.py tests/test_api_snapshot_egress.py -q` → `ModuleNotFoundError` esperado para el módulo inexistente.
- GREEN inicial: `python -B -m pytest -p no:cacheprovider tests/test_snapshot_cache.py -q` → `13 passed`.
- RED legacy: `python -B -m pytest -p no:cacheprovider tests/test_api_snapshot_egress.py -q` → atributo `_load_legacy_snapshot_cached` inexistente.
- RED runtime web: mismo comando → `FileNotFoundError` esperado para la copia empaquetada inexistente.
- GREEN final: `python -B -m pytest -p no:cacheprovider tests/test_snapshot_cache.py tests/test_api_snapshot_egress.py -q` → `20 passed in 1.15s`.
- Regresión segura: `python -B .codex/egress_safe_pytest.py tests/test_quote_jobs_api.py::test_deployable_api_copies_have_identical_sha256 tests/test_quote_jobs_api.py::test_internal_tarkett_catalog_reads_and_updates_snapshot tests/test_quote_jobs_api.py::test_internal_offiho_catalog_reads_and_updates_snapshot tests/test_quote_jobs_api.py::test_offiho_catalog_prefers_dynamic_database_snapshot tests/test_quote_jobs_api.py::test_offiho_catalog_fresh_query_bypasses_server_cache -q` → `5 passed`, 0 borrados bloqueados/reciclados.
- `py_compile` y `git diff --check` pasan. SHA-256 final de las tres APIs: `D8A7D9103C24CD0109EA3A23A4491B6EAE0B333BA1AB7E7D18F0245D40135C8A`.

## Riesgos residuales

- No hubo acceso de red, secretos, producción ni despliegue. La medición real de egress y el canary quedan para coordinación de raíz.
- Los contadores reflejan bytes de contenido cargado desde DB, no bytes facturados por proveedores.

## Revisión ronda 1

- Se añadió una lectura autoritativa final tras cada `SnapshotCache.load` moderno y legacy. Si la identidad cambió, el resultado se descarta y se repite una sola vez bajo la nueva revisión; nunca se acepta la clave inmutable previa como vigente.
- En modo de caché privada, metadata legacy ausente o inválida ahora provoca un error controlado y no puede caer al cache TTL residente de Tarkett u Offiho.
- RED: `python -B -m pytest -p no:cacheprovider tests/test_api_snapshot_egress.py -q` → 4 fallos esperados (dos carreras y dos fallbacks TTL).
- GREEN: `python -B -m pytest -p no:cacheprovider tests/test_snapshot_cache.py tests/test_api_snapshot_egress.py -q` → `24 passed in 1.18s`.
- Regresión segura repetida de cinco flujos API → `5 passed in 1.73s`; sin borrados bloqueados ni reciclados. SHA API final: `18833459AAF340D09AF5DD5D6B5847F5C04047ADA9AC65DD349759E7BB431C2A`.

## Revisión ronda 2 — flag OFF

- Se restituyó el orden previo en la ruta sin caché privada: pointer publicado, fingerprint de storage, cache residente y sólo entonces descarga del payload en un miss. La ruta privada y su revalidación final no cambiaron.
- RED: `python -B -m pytest -p no:cacheprovider tests/test_api_snapshot_egress.py -q` → 2 fallos esperados: misma revisión descargaba dos veces, tanto con flag false como con R2 inválido.
- GREEN: suite de caché/API → `29 passed in 1.40s`; regresión segura de cinco flujos API → `5 passed in 2.21s`, sin borrados bloqueados ni reciclados.
- Se cubren revisión igual, R2 no configurado, cambio de versión, cambio de fingerprint y despublicación con catálogo residente. SHA API final: `3A1849B5CD3D7B5C6AC0C13AA37EEB633D02DFE84C40C0E76003751C782C319D`.
