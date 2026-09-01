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
