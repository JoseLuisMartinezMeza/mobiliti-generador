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
