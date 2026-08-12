# Supabase setup

## Aplicar SQL desde CLI

Dry-run:

```powershell
python scripts\apply_supabase_sql.py
```

Aplicar contra Supabase:

```powershell
$env:DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres"
pip install psycopg[binary]
python scripts\apply_supabase_sql.py --apply
```

El script no imprime `DATABASE_URL`. Sin `--apply` no toca la base.

## Aplicar manual

Copia `mobiliti_saas/supabase_setup/create_tables.sql` en Supabase SQL Editor.

## Orden de migraciones

Ejecuta `2026_07_projects.sql` después de `2026_06_quote_jobs.sql`. La
migración crea `saas_projects` con revisión, versión de esquema y controles de
acceso exclusivos para `service_role`.

Para habilitar el snapshot dinámico de Offiho en una instalación existente,
ejecuta `2026_08_offiho_stock_snapshot.sql` después de
`2026_07_supplier_catalog_snapshots.sql`. La migración solo amplía el `CHECK`
de proveedores permitidos de `tarkett` a `tarkett` y `offiho`; no reescribe
filas existentes.
