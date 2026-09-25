import re
from pathlib import Path


MIGRATION = Path("mobiliti_saas/supabase_setup/2026_07_projects.sql")
BOOTSTRAP = Path("mobiliti_saas/supabase_setup/create_tables.sql")


def project_table_statement(sql):
    start = sql.index("CREATE TABLE IF NOT EXISTS saas_projects")
    return re.sub(r"\s+", " ", sql[start:sql.index(");", start) + 2]).strip()


def test_projects_migration_matches_bootstrap_and_is_service_role_only():
    migration = MIGRATION.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    assert project_table_statement(migration) == project_table_statement(bootstrap)
    for sql in (migration, bootstrap):
        normalized = re.sub(r"\s+", " ", sql.lower())
        assert "revision integer not null default 0 check (revision >= 0)" in normalized
        assert "status text not null default 'active' check (status in ('active', 'archived'))" in normalized
        assert "last_operation_id uuid" in normalized
        assert "alter table public.saas_projects enable row level security" in normalized
        assert "revoke all on table public.saas_projects from anon, authenticated" in normalized
        assert "grant all on table public.saas_projects to service_role" in normalized
        assert "delete cascade" not in project_table_statement(sql).lower()
