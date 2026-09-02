# Task 2 — reporte de implementación

Estado: DONE_WITH_CONCERNS

## Ronda 1 de revisión

- RED: `python -B .codex/egress_safe_pytest.py tests/test_worker_egress.py -q`
  produjo 1 fallo esperado: PostgreSQL generaba `SELECT *` pese a pedir
  `select=id` o la proyección de lease.
- GREEN: el mismo comando produjo `6 passed in 0.65s`.
- Regresión adicional: `python -B .codex/egress_safe_pytest.py tests/test_quote_worker.py -q -k "postgres_client_threads_attempt or catalog_snapshot or fetch_next_job or recover_stale_jobs or nonisolated_worker"`
  produjo `9 passed, 98 deselected in 0.81s`.

`PostgresClient.rest` ahora transforma `select` únicamente con una lista
interna permitida de columnas de `saas_quote_jobs`; sin `select` (o con `*`)
conserva `SELECT *`. El claim sigue recibiendo la fila completa desde PATCH.

## RED → GREEN

- RED: `python -B .codex/egress_safe_pytest.py tests/test_worker_egress.py -q`
  produjo 5 fallos esperados: caché inexistente (24 payloads en vez de 1),
  fallo de autorización, respuesta pesada de upsert, proyecciones `*`, y
  recuperación stale en cada ciclo.
- GREEN: el mismo comando produjo `5 passed in 0.69s`.
- Regresión focalizada: `python -B .codex/egress_safe_pytest.py tests/test_worker_egress.py tests/test_quote_worker.py -q -k "catalog_snapshot or fetch_next_job or recover_stale_jobs or nonisolated_worker"`
  produjo `9 passed, 103 deselected in 0.83s`.

Todas las pruebas usan HTTP/R2 en memoria y no usan red ni `tmp_path`.
El runner dejó sus scratch preservados y no recicló archivos/directorios
(`recycled_files=0`, `recycled_directories=0`, `blocked=0`).

## Resultado

- `SnapshotCache` de la revisión Task 1 se comparte a nivel de módulo entre
  clientes nuevos. Con metadatos constantes, 24 lecturas simuladas hacen una
  lectura completa de payload (ahorro simulado 24x); una revisión/hash nueva
  realiza una segunda lectura y expone el stock nuevo.
- La ruta REST con service key lee metadatos antes y después del hit/carga;
  una ausencia, revisión distinta o autorización fallida no devuelve una
  entrada residente obsoleta. Flag `CATALOG_SNAPSHOT_CACHE_ENABLED` permanece
  apagado por defecto y conserva la lectura directa anterior.
- El upsert pide/devuelve únicamente `supplier,source_hash,generated_at,updated_at`.
- `queued` pide sólo `id`; `processing` pide sólo los cinco campos de lease;
  claim conserva la fila completa autoritativa.
- `run_once` mantiene el sondeo queued por ciclo y limita recuperación stale
  a una vez cada 60 s, incluido el primer ciclo. Offiho/Tarkett no cambiaron
  sus intervalos de 3600/21600 s.
- Las tres APIs y los archivos compartidos no fueron modificados.

## Excepción / riesgo residual

La corrida completa de `tests/test_quote_worker.py` excedió dos veces la
ventana de ejecución de la consola tras aproximadamente 58 puntos sin un
resultado final. La regresión focalizada que cubre los contratos tocados pasó;
la raíz debe ejecutar la suite completa en su harness antes de despliegue.
No hay cambios de API, secretos, producción, retención ni borrados.
