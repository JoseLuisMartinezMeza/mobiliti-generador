import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("apply_supabase_sql", Path("scripts/apply_supabase_sql.py"))
apply_supabase_sql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_supabase_sql)


def test_summarize_sql_detects_quote_jobs():
    summary = apply_supabase_sql.summarize_sql(
        """
        create table if not exists saas_quote_jobs (id uuid primary key);
        create index if not exists idx_quote_jobs_status on saas_quote_jobs(status);
        insert into storage.buckets (id) values ('quote-files');
        """
    )

    assert summary["create_table"] == 1
    assert summary["create_index"] == 1
    assert summary["insert"] == 1
    assert summary["storage_bucket"] is True
    assert summary["quote_jobs"] is True


def test_require_database_url_rejects_missing_and_placeholders():
    try:
        apply_supabase_sql.require_database_url({})
    except RuntimeError as exc:
        assert "DATABASE_URL" in str(exc)
    else:
        raise AssertionError("missing DATABASE_URL should fail")

    try:
        apply_supabase_sql.require_database_url({"DATABASE_URL": "postgresql://postgres:[YOUR_PASSWORD]@db.[PROJECT_REF].supabase.co/postgres"})
    except RuntimeError as exc:
        assert "placeholder" in str(exc)
    else:
        raise AssertionError("placeholder DATABASE_URL should fail")


def test_require_database_url_accepts_real_shape():
    assert apply_supabase_sql.require_database_url({"DATABASE_URL": "postgresql://user:pass@example.com/db"}).startswith("postgresql://")
