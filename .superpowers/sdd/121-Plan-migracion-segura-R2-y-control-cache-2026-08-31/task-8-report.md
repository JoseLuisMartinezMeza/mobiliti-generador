# Task 8 — cierre local del review transversal previo a staging/canary

Fecha: 2026-09-01

Base revisada: `b632a19fbfc206038cb267e7a0a1854d3174f5e0`

Commits locales:

- `ab59f75` — rollback seguro del registro y cutover fijado.
- `64b1898` — runner A → Gate 7A/Gate 6 → B y documentación operativa.
- `85fc3b5` — contratos de búsqueda y aceptación alineados con v17.
- `88a0ece` — reporte y ledger del cierre transversal.
- `96641ac` — clasificación fail-closed del SQL sensible por contenido.
- `e50e387` — reporte del primer follow-up independiente.
- `8ebb5c4` — canonicalización y fingerprint del stream de tokens SQL.
- `79c7bca` — reporte del segundo follow-up independiente.
- `a309b51` — runner restringido a los tres archivos SQL canónicos.
- `este commit (HEAD en el handoff)` — reporte del tercer follow-up.

Estado operativo: **Gate 8 live no ejecutado**. No hubo deploy, push, DDL,
lectura/escritura live, cambio de secretos, mutación Cloudflare, borrado ni
cambio de engine/template.

## Resultado implementado

### Registro, rollback y clonación

`saas_register_catalog_asset` conserva la fila que ganó la primera inserción.
Una segunda verificación del mismo object name, hash derivado, bytes, MIME y
bucket puede declarar `supabase` o `r2` y retorna idempotentemente sin cambiar
`storage_provider`. Continúan fallando bucket, hash/nombre, bytes, MIME o
`verified_at` incompatibles.

Las dos RPC de clonación dejaron de hardcodear R2. Exigen una fila verificada y
`physical_bucket='catalog-assets'`, por lo que aceptan tanto un hash registrado
originalmente en Supabase como uno registrado en R2. No se restauró ninguna
dependencia de `storage.objects`; se conservaron SECURITY DEFINER, search_path,
RLS y grants service-role-only.

El finalizador de Task 6 no se debilitó: continúa exigiendo exactamente
`asset.storage_provider='r2'` al certificar los 2,214 objetos iniciales.

La migración B ya no elige el batch más reciente. Su DO guard fija:

- batch: `470442fc-3dc3-5948-b0e4-1dd34c1fcd30`;
- keyset: `93e30738942bc0c4b85d85d63239c82588ec1d163c5c3820ef2de01dc07caeb7`;
- manifest: `72ecc6b84bfec9ba012a24dea9c5bcdf6d1beaad8d81c68eb4697f8e83e188ff`;
- expected/verified: `2214/2214`, missing/failed `0/0`, `verified_at` no nulo.

El harness PostgreSQL opt-in cubre registro cross-provider preservando la fila
R2, conflicto por bytes, clones con filas Supabase y R2 y rechazo del cutover
cuando sólo existe otro batch verificado. En este host permaneció skip opt-in;
los contratos textuales y la paridad real A/bootstrap y B/bootstrap pasaron.

### Runner y runbook

`scripts/apply_supabase_sql.py` ya no tiene bootstrap implícito:

- sin `--file` ni `--bootstrap-new-project`, falla;
- `--bootstrap-new-project` es explícito y no se combina con `--file` ni con
  confirmación de cutover;
- `create_tables.sql` se rechaza mediante `--file`;
- A se permite como migración explícita;
- B requiere `--confirm-cutover-batch` con el UUID exacto;
- A+B se rechazan en una misma invocación;
- dry-run sigue siendo el default, no abre DB y no imprime DATABASE_URL.

Los dos primeros follow-ups probaron que clasificar SQL arbitrario por
substrings o por un lexer propio mantenía una superficie innecesaria. El diseño
vigente de `a309b51` elimina por completo parser, tokens y sentinelas:

- `--bootstrap-new-project` selecciona únicamente `create_tables.sql` canónico;
- `--file` acepta exactamente una ruta canónica: A o B;
- copias, hardlinks, symlinks, renombrados, quoted SQL, SQL arbitrario y uno o
  dos archivos A+B se rechazan por allowlist antes de consultar DATABASE_URL;
- cada contenido canónico se valida con SHA-256 hardcodeado sobre UTF-8 con
  CRLF/CR normalizado a LF;
- B continúa exigiendo la confirmación UUID exacta.

El archivo seleccionado se lee exactamente una vez; ese mismo texto se valida,
resume y, sólo con `--apply`, se entrega a psycopg. Dry-run sigue siendo default
y no se imprime SQL ni DATABASE_URL. SQL adicional queda fuera de este runner y
requiere un proceso separado o una ejecución manual revisada, sin flag bypass.

`CLOUD_DEPLOY.md` y `supabase_setup/README.md` distinguen base nueva de proyecto
existente y documentan A → Gate 7A → Task 6 execute/certify Gate 6 → B. El
bloque Vercel activo enumera todas las variables server-only de catálogo:
provider, public base, account, endpoint, access, secret, session opcional,
bucket exacto y región. También separa esas credenciales de quote R2 y ordena
deploy compatible Supabase, readiness, canary y cambio coordinado a R2.

### Contratos de búsqueda y v17

Los cuatro fallos Task 1 desactualizados se corrigieron sólo en tests:

- Idelika/Conceptos hace una búsqueda aislada por supplier y combina resultados
  para conservar assertions multi-supplier; no reabre búsqueda global.
- Las tres APIs reciben `Response` y supplier `labenze` explícito al verificar
  el bounded response helper.

Los cinco fallos iniciales de aceptación eran drift verificable, no defectos de
producción:

- commit `884b036` introdujo la superficie v17; el precio uniforme visible
  pasó de X a AA mientras X conserva la fórmula regional oficial;
- commit `ddcf0c2` introdujo guards Fletes para `B61=0` y `B67=0`;
- el template firmado v17 usa P4/P6 y el compositor agrega N18:N21 mediante
  `V17_FLETE_LOOKUP_ROWS`.

Sólo se actualizaron expectations. No se cambió engine ni template.

## Evidencia TDD RED → GREEN

### RED observado

1. Contratos SQL nuevos:
   `pytest test_catalog_migrations.py -k ...` → **3 failed, 1 passed**.
   Fallaron provider en conflicto, clones R2-only y selección latest de B.
2. Runner/docs:
   `pytest tests/test_apply_supabase_sql.py` → **4 failed, 3 passed**.
   `main` no aceptaba argv, bootstrap era implícito y docs incompletas.
3. Contratos existentes:
   Idelika/Labenze/Requiez → **4 failed, 19 passed**, exactamente supplier nulo
   y firma sin Response.
4. Acceptance inicial → **5 failed, 2 passed**: X frente a AA, guards Fletes y
   superficie Excel legacy. Al avanzar el mismo test aparecieron expectations
   encadenadas K/N legacy; se contrastaron contra template/commits antes de
   cambiar únicamente los tests.

### GREEN observado

- SQL focal: **4 passed**.
- Runner/docs/env: **8 passed**.
- Integración Idelika/Labenze/Requiez: **23 passed**, 2 warnings openpyxl.
- Migraciones + migrador: **116 passed, 3 skipped**.
- Suite transversal repository/sync/search/integration/apply/migrations:
  **615 passed, 3 skipped, 2 warnings**.
- Acceptance vigente quedó sin fallos en la regresión completa.
- `py_compile` de todos los Python modificados: exit 0.
- SQL parity, SECURITY DEFINER/search_path/RLS/grants y diff-check scoped: exit 0.

### Follow-up del review independiente (`88a0ece`)

- RED runner por copias/combinación: **3 failed, 9 passed**; bootstrap copiado,
  B copiada sin confirmación y A+B en un solo archivo eran aceptados.
- GREEN inicial del cierre por contenido: **12 passed**.
- RED adicional de fail-closed: **1 failed, 12 passed** al alterar el UUID en
  una B renombrada.
- GREEN final runner + migraciones relacionadas: **130 passed, 3 skipped** en
  5.42 s. Los skips son los harness PostgreSQL/Docker opt-in.
- `py_compile` de runner y tests modificados: exit 0; diff-check scoped: exit 0.
- El harness PostgreSQL opt-in ahora incluye también Supabase → R2 preservando
  la fila Supabase y rechazos de MIME/bucket; no se abrió una DB en este host.

### Segundo follow-up independiente (`e50e387`)

- RED principal del lexer: **6 failed, 13 passed**. Cubrió whitespace/comentario
  alrededor de `.`, UUID correcto sólo en comentario con UUID activo alterado,
  `missing_count=1`, `failed_count=1`, `verified_at IS NULL` y ruta B canónica
  asociada a contenido modificado.
- RED estructural adicional: **1 failed, 19 passed** para un `UPDATE public /*
  ... */ . saas_catalog_asset_cutover_batches SET cutover_applied_at` no
  canónico.
- Verificación final exacta:

```powershell
python -B -m pytest -p no:cacheprovider -q tests/test_apply_supabase_sql.py tests/test_catalog_migrations.py tests/test_catalog_asset_r2_migration.py tests/test_project_migrations.py
```

Resultado: **138 passed, 3 skipped in 5.88s**. Los tres skips siguen siendo
harness PostgreSQL/Docker opt-in. Verificaciones estáticas exactas:

```powershell
python -B -m py_compile scripts/apply_supabase_sql.py tests/test_apply_supabase_sql.py tests/test_catalog_migrations.py
git diff --check -- scripts/apply_supabase_sql.py tests/test_apply_supabase_sql.py
```

Ambas finalizaron con exit 0.

### Tercer follow-up independiente (`79c7bca`)

El enfoque lexer anterior quedó deliberadamente superseded y se retiró.

- RED allowlist/hash: **4 failed, 9 passed, 1 skipped**. El test matricial
  incluía quoted B/bootstrap/A+B, prefijo standard-conforming, dollar-quoted
  ajeno, SQL arbitrario, copias A/B/bootstrap, concatenación y hardlink; además
  fallaron las tres mutaciones de contenido canónico simuladas sin editar los
  SQL reales. El skip fue symlink sin privilegio en Windows.
- RED docs después del GREEN de código: **1 failed, 12 passed, 1 skipped** por
  faltar el límite explícito bootstrap/A/B y el proceso separado/manual.
- Verificación final exacta:

```powershell
python -B -m pytest -p no:cacheprovider -q tests/test_apply_supabase_sql.py tests/test_catalog_migrations.py tests/test_catalog_asset_r2_migration.py tests/test_project_migrations.py
```

Resultado: **130 passed, 4 skipped in 5.57s**. Tres skips son los harness
PostgreSQL/Docker opt-in y uno es el symlink no disponible en este Windows.

```powershell
python -B -m py_compile scripts/apply_supabase_sql.py tests/test_apply_supabase_sql.py tests/test_catalog_migrations.py
git diff --check -- scripts/apply_supabase_sql.py tests/test_apply_supabase_sql.py mobiliti_saas/CLOUD_DEPLOY.md mobiliti_saas/supabase_setup/README.md
```

Ambas verificaciones estáticas finalizaron con exit 0.

## Regresión completa y deuda residual

Comando:

```powershell
python -B -m pytest -p no:cacheprovider -q
```

Resultado fresco: **4004 passed, 26 skipped, 127 warnings, 22 failed, 2
errors**, 3799.06 s. La suite completa no quedó globalmente verde, pero ninguno
de los fallos pertenece a los archivos de producción modificados en este loop y
`test_project_quote_acceptance.py` quedó verde. No se expandió el alcance para
reescribir engine/template/UI.

Clasificación residual:

- Template/hash/semántica v17 o CDMX congelada: 13-supplier acceptance, tres
  builder CDMX, dos contratos CDMX, Mobiliti SharePoint y official stress.
- Expectations legacy de columnas/celdas: dos online quote y cuatro
  quote-engine golden.
- Browser/UI preexistente: tres browser E2E y mixed drawer.
- Contratos legacy de expansión Mobiliti: un fallo X global.
- E2E dev/lumbro: dos fallos.
- Mirror de quote engine Vercel ya divergente: un fallo de byte parity.
- Fixture local Idelika/Conceptos con fake repository que no implementa la
  interfaz actual: dos errores de setup.

Los skips incluyen los harness opt-in PostgreSQL/Docker. Los warnings son en su
mayoría openpyxl por Data Validation/WMF y cuatro deprecaciones Pillow
`getdata`; no se ocultaron.

## Riesgos y pendientes

- Gate 8 y todos los gates live siguen pendientes; este reporte no certifica
  producción.
- El SQL funcional real PostgreSQL de este fix no se ejecutó porque el harness
  es opt-in en este host; sí quedó preparado y cubierto textualmente.
- Antes de un cutover real se requiere el orden documentado, queues en cero,
  Gate 7A, certificación Task 6 del batch fijado, aplicación separada de B y
  readiness/canary con public base idéntica.
- La deuda residual de 22 fallos/2 errores debe resolverse como trabajo separado
  con autoridad explícita sobre template/engine/UI; no es seguro mezclarla con
  el rollback de catálogo.

## Preservación

Se stagearon sólo archivos del alcance y este reporte. Los cambios/eliminaciones
y untracked preexistentes del usuario permanecen intactos y fuera de commits.
No se creó backup porque ningún archivo de configuración Codex fue reemplazado
y no hubo operación destructiva.
