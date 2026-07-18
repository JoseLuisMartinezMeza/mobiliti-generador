-- Guarded RLS hardening for legacy and repository job tables.

DO $$
BEGIN
    IF to_regclass('public.jobs') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'REVOKE ALL ON TABLE public.jobs FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON TABLE public.jobs TO service_role';
    END IF;

    IF to_regclass('public.saas_quote_jobs') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.saas_quote_jobs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'REVOKE ALL ON TABLE public.saas_quote_jobs FROM anon, authenticated';
        EXECUTE 'GRANT ALL ON TABLE public.saas_quote_jobs TO service_role';
    END IF;
END;
$$;
