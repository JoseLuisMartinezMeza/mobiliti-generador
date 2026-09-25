# Task 3 — Registro neutral de assets R2

Estado: implementación local lista para revisión. No se ejecutó SQL live, no se creó rama Supabase ni se desplegó/pusheó nada.

## Alcance implementado

- Migración A aditiva y transaccional con `saas_catalog_assets`, el control de batch/manifiesto y el RPC idempotente `saas_register_catalog_asset`.
- RLS activado, sin policies para clientes, `REVOKE` a `PUBLIC`, `anon` y `authenticated`, y permisos mínimos de lectura/RPC para `service_role`.
- La migración B comprueba antes de sustituir RPCs un batch verificado exactamente de 2,214 assets, con digest de keyset y de manifiesto; sólo después reemplaza ambos clones para leer el registry R2 verificado.
- `create_tables.sql` contiene el estado bootstrap final sin el guard histórico de 2,214 assets.
- El repositorio del worker registra el asset sólo tras PUT exitoso o conflicto confirmado mediante `object/info`; un fallo del RPC de registro evita el stage posterior.
- Las tres copias API adjuntan `x-metadata` Base64, validan `object/info` cuando reciben 409 y sólo entonces registran; un 409 incompatible no registra.

## TDD y evidencia

- RED observado para migraciones inexistentes, ausencia de RPC post-PUT en el repository y aceptación API de cualquier 409.
- RED adicional observado para el guard incorrecto con `current_user` dentro de `SECURITY DEFINER`; se eliminó y el acceso queda gobernado por grants explícitos.
- `python -m pytest tests/test_catalog_migrations.py -q` → 42 passed, 1 skipped.
- `python -m pytest tests/test_catalog_repository.py -q` → 61 passed.
- `python -m pytest tests/test_quote_jobs_api.py -q -k "catalog_asset or catalog_admin"` → 28 passed, 272 deselected.
- `python -m py_compile mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py vercel_deploy/api/index.py mobiliti_saas/worker/catalog_sync/repository.py` → exit 0.
- `git diff --no-index` entre las tres copias API → sin diferencias.
- `git diff --check` limitado a los archivos Task 3 → sin errores.

## Riesgos y siguientes gates

## Corrección de review (commit `e42a297`)

- Se añadieron entries privadas por batch, RPCs de inicio/carga/finalización y `ON CONFLICT DO NOTHING` para el registro concurrente.
- RED/GREEN estructural: `python -m pytest tests/test_catalog_migrations.py -q -k "asset_registry or asset_cutover or cutover_manifest"` → 3 passed.
- Se añadió una prueba PostgreSQL opt-in de ACL/RLS; quedó skipped porque no había contenedor local certificado. La cobertura funcional de concurrencia y clones R2 sigue pendiente, por lo que esta corrección no declara el gate productivo satisfecho.
- Commit `ff94cdb` añadió el harness ACL opt-in. Commit final posterior califica de forma estática las relaciones de registry/clones bajo `SECURITY DEFINER`; su integración local permanece skipped sin un DSN/contenedor certificado.

- Gate 3 productivo no está cumplido: el backfill/manifiesto de los 2,214 assets y la ejecución live de los RPCs de clone quedan explícitamente para Tasks 6/8.
- Los productores actuales usan Supabase Storage y registran `storage_provider='supabase'`; el cutover de clones exige assets `r2` verificados. La migración B no puede aplicarse hasta que Task 6 haya cargado y certificado el batch R2.
- La suite completa `tests/test_quote_jobs_api.py` supera el límite interactivo de salida de esta tarea; se ejecutó el subconjunto relevante de catálogo. No hay resultado completo que reportar.

## Remate de review local (2026-08-31)

- RED nuevo observado con `python -m pytest tests/test_catalog_migrations.py -q -k "catalog_asset and not local_postgres"`: `3 failed, 2 passed`. Los fallos demostraron que el bootstrap aún tenía stubs de begin/add/finalize sin digests congelados, que los clones usaban `gen_random_uuid()` sin esquema y que faltaban guards NULL explícitos.
- GREEN posterior: `python -m pytest tests/test_catalog_migrations.py -q` → `45 passed, 2 skipped`. Los dos skips son las pruebas PostgreSQL opt-in porque no están configurados `TASK6_LOCAL_POSTGRES_URL` y `TASK6_LOCAL_POSTGRES_CONTAINER`; no se creó contenedor ni target alternativo.
- Migración A y bootstrap ahora comparten la misma definición normalizada de las tres tablas y de register/begin/add/finalize. El estado `loading` forma parte del CHECK, begin congela y compara count/digests en replay, add sólo acepta un batch loading e impide mismatches, y finalize recalcula count/keyset/manifest antes del join exacto con registry R2.
- Migración B recalcula nuevamente entries, digests y el join registry exacto —incluido `cutover_batch_id`— antes de reemplazar los clones. Ambos clones y el bootstrap comparten su definición final, usan sólo relaciones `public.saas_*`, `extensions.digest`/`extensions.gen_random_uuid`, `search_path` fijo y guards NULL explícitos.
- `service_role` conserva sólo SELECT directo sobre registry/batches/entries y EXECUTE en los RPC; no recibe DML directo. `PUBLIC`, `anon` y `authenticated` quedan revocados. Las pruebas estáticas detectan grants DML combinados, relaciones o `%ROWTYPE` sin esquema y primitivas criptográficas sin esquema.
- La integración PostgreSQL opt-in tiene cuerpo funcional para: dos registros concurrentes idénticos con una fila, mismatch bloqueado, ACL/RLS, fallos 2213/digest/keyset/registry, éxito de 2,214 entries, digests internos, inmutabilidad post-finalize y ambos clones con un asset presente sólo en registry R2 y ausente de `storage.objects`.
- Verificación adicional del cierre: repository `61 passed`; subset API `28 passed, 272 deselected`; `py_compile` de las tres APIs y worker con exit 0; las tres copias API tienen SHA-256 `2AEDB2DCC06F81DC40EC8E22AF2027BEDF59DE6D1331436D48332AAA6E701441` y `git diff --no-index` sin diferencias; diff-check limitado sin errores.
- Riesgo residual: el cuerpo PostgreSQL exhaustivo no se ejecutó en esta máquina por falta del DSN/contenedor local certificado. Gate 3 live sigue **no cumplido**; requiere el backfill real de 2,214 assets y la ejecución certificada/live posterior de ambos clones.
