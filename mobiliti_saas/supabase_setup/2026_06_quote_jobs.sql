-- Mobiliti SaaS Web Cotizador
-- Ejecutar en Supabase SQL Editor despues de crear las tablas base.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'quote-files',
    'quote-files',
    FALSE,
    104857600,
    ARRAY[
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/pdf',
        'application/octet-stream'
    ]
)
ON CONFLICT (id) DO UPDATE SET
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

CREATE TABLE IF NOT EXISTS saas_quote_jobs (
    id UUID PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES saas_usuarios(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','queued','processing','completed','failed')),
    input_path TEXT NOT NULL,
    output_path TEXT,
    template TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_quote_jobs_usuario ON saas_quote_jobs(usuario_id);
CREATE INDEX IF NOT EXISTS idx_quote_jobs_status ON saas_quote_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_quote_jobs_updated ON saas_quote_jobs(updated_at DESC);
