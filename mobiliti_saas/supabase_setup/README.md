# Supabase setup

## Aplicar SQL desde CLI

Dry-run:

```powershell
python scripts\apply_supabase_sql.py --bootstrap-new-project
```

Aplicar contra Supabase:

```powershell
$env:DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres"
pip install psycopg[binary]
python scripts\apply_supabase_sql.py --bootstrap-new-project --apply
```

Este bootstrap y `create_tables.sql` son exclusivamente para una **base de
datos nueva**. El script no imprime `DATABASE_URL`. Sin `--apply` no toca la
base. Sin `--file` ni `--bootstrap-new-project` falla de forma segura.

## Aplicar manual

Copia `mobiliti_saas/supabase_setup/create_tables.sql` en Supabase SQL Editor
sólo al crear una base de datos nueva. Nunca lo ejecutes sobre un proyecto
existente.

## Orden de migraciones

### Registro y corte de `catalog-assets` en un proyecto existente

Nunca apliques las migraciones A+B juntas ni uses el bootstrap. El orden es:

1. A: `2026_09_catalog_asset_registry_r2.sql`.
2. Gate 7A completo; después Task 6 ejecuta y certifica Gate 6.
3. B: `2026_09_catalog_asset_registry_r2_cutover.sql`, sólo con confirmación
   exacta `470442fc-3dc3-5948-b0e4-1dd34c1fcd30`.

```powershell
python scripts\apply_supabase_sql.py --file mobiliti_saas\supabase_setup\2026_09_catalog_asset_registry_r2.sql --apply
# Gate 7A + Task 6 execute/certify Gate 6
python scripts\apply_supabase_sql.py --file mobiliti_saas\supabase_setup\2026_09_catalog_asset_registry_r2_cutover.sql --confirm-cutover-batch 470442fc-3dc3-5948-b0e4-1dd34c1fcd30 --apply
```

El dry-run es el mismo comando sin `--apply`. La migración B no sustituye la
certificación: el guard SQL vuelve a comprobar el batch, los digests y los
2,214 registros exactos.

Ejecuta `2026_07_projects.sql` después de `2026_06_quote_jobs.sql`. La
migración crea `saas_projects` con revisión, versión de esquema y controles de
acceso exclusivos para `service_role`.

Para habilitar el snapshot dinámico de Offiho en una instalación existente,
ejecuta `2026_08_offiho_stock_snapshot.sql` después de
`2026_07_supplier_catalog_snapshots.sql`. La migración solo amplía el `CHECK`
de proveedores permitidos de `tarkett` a `tarkett` y `offiho`; no reescribe
filas existentes.
