CREATE OR REPLACE FUNCTION saas_delete_quote_job(
    p_quote_job_id UUID,
    p_usuario_id INTEGER
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM 1
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id AND usuario_id = p_usuario_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    UPDATE saas_tarkett_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';

    UPDATE saas_offiho_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';

    UPDATE saas_catalog_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';

    DELETE FROM saas_quote_jobs
    WHERE id = p_quote_job_id AND usuario_id = p_usuario_id;

    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION saas_delete_quote_job(UUID, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_delete_quote_job(UUID, INTEGER) FROM anon;
REVOKE ALL ON FUNCTION saas_delete_quote_job(UUID, INTEGER) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_delete_quote_job(UUID, INTEGER) TO service_role;
