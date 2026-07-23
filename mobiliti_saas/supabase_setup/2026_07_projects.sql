-- Run this migration after 2026_06_quote_jobs.sql.

CREATE TABLE IF NOT EXISTS saas_projects (
    id UUID PRIMARY KEY,
    usuario_id BIGINT NOT NULL REFERENCES saas_usuarios(id),
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    payload JSONB NOT NULL,
    last_operation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_projects_user_status_updated
    ON saas_projects(usuario_id, status, updated_at DESC);

ALTER TABLE public.saas_projects ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.saas_projects FROM anon, authenticated;
GRANT ALL ON TABLE public.saas_projects TO service_role;
