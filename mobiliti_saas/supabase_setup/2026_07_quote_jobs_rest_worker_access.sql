-- Restore the minimum PostgREST privileges required by the production worker.
-- Authorization remains enforced by the existing mobiliti_rest_* RLS policies.

DO $$
BEGIN
    IF to_regclass('public.saas_quote_jobs') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.saas_quote_jobs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'GRANT SELECT, UPDATE ON TABLE public.saas_quote_jobs TO anon';
        EXECUTE 'REVOKE ALL ON TABLE public.saas_quote_jobs FROM authenticated';
        EXECUTE 'GRANT ALL ON TABLE public.saas_quote_jobs TO service_role';
    END IF;
END;
$$;
