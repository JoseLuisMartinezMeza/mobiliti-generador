CREATE OR REPLACE FUNCTION saas_stage_catalog_candidate(
    p_run_id UUID,
    p_source_hash TEXT,
    p_generated_at TIMESTAMPTZ,
    p_payload JSONB,
    p_metrics JSONB,
    p_delta_link TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run saas_catalog_sync_runs%ROWTYPE;
    v_source saas_catalog_sources%ROWTYPE;
    v_candidate saas_catalog_snapshot_versions%ROWTYPE;
    v_candidate_id UUID := gen_random_uuid();
    v_canonical_hash TEXT;
    v_canonical_generated_at TEXT;
    v_canonical_payload JSONB;
    v_payload_generated_at TIMESTAMPTZ;
BEGIN
    IF p_run_id IS NULL
       OR p_generated_at IS NULL
       OR NOT isfinite(p_generated_at)
       OR p_source_hash IS NULL
       OR p_source_hash !~ '^[0-9A-Fa-f]{64}$'
       OR p_payload IS NULL
       OR jsonb_typeof(p_payload) <> 'object'
       OR jsonb_typeof(p_payload -> 'items') IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_payload -> 'items') > 100000
       OR pg_column_size(p_payload) > 104857600
       OR jsonb_typeof(p_payload -> 'source_hash') IS DISTINCT FROM 'string'
       OR p_payload ->> 'source_hash' !~ '^[0-9A-Fa-f]{64}$'
       OR LOWER(p_payload ->> 'source_hash') IS DISTINCT FROM LOWER(p_source_hash)
       OR jsonb_typeof(p_payload -> 'generated_at') IS DISTINCT FROM 'string'
       OR p_payload ->> 'generated_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
       OR p_metrics IS NULL
       OR jsonb_typeof(p_metrics) <> 'object'
       OR pg_column_size(p_metrics) > 1048576
       OR (
           p_delta_link IS NOT NULL AND (
               NULLIF(BTRIM(p_delta_link), '') IS NULL
               OR LENGTH(p_delta_link) > 8192
           )
       ) THEN
        RAISE EXCEPTION 'invalid catalog candidate input';
    END IF;

    BEGIN
        v_payload_generated_at := (p_payload ->> 'generated_at')::TIMESTAMPTZ;
    EXCEPTION
        WHEN invalid_datetime_format OR datetime_field_overflow THEN
            RAISE EXCEPTION 'invalid catalog generated_at';
    END;
    IF v_payload_generated_at IS DISTINCT FROM p_generated_at THEN
        RAISE EXCEPTION 'catalog generated_at does not match';
    END IF;

    v_canonical_hash := LOWER(p_source_hash);
    v_canonical_payload := jsonb_set(
        p_payload, '{source_hash}', to_jsonb(v_canonical_hash), FALSE
    );
    v_canonical_generated_at := to_char(
        p_generated_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
    );
    v_canonical_payload := jsonb_set(
        v_canonical_payload, '{generated_at}', to_jsonb(v_canonical_generated_at), FALSE
    );

    SELECT * INTO v_run
    FROM saas_catalog_sync_runs
    WHERE id = p_run_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog sync run is not stageable';
    END IF;

    SELECT * INTO v_source
    FROM saas_catalog_sources
    WHERE id = v_run.source_id
    FOR UPDATE;
    IF NOT FOUND
       OR p_payload ->> 'supplier' IS DISTINCT FROM v_source.supplier THEN
        RAISE EXCEPTION 'catalog candidate source does not match';
    END IF;

    IF v_run.status = 'awaiting_approval'
       AND v_run.candidate_version_id IS NOT NULL THEN
        SELECT * INTO v_candidate
        FROM saas_catalog_snapshot_versions
        WHERE id = v_run.candidate_version_id
        FOR UPDATE;
        IF NOT FOUND
           OR v_candidate.status <> 'candidate'
           OR v_candidate.sync_run_id IS DISTINCT FROM p_run_id
           OR v_candidate.supplier IS DISTINCT FROM v_source.supplier
           OR v_candidate.source_hash IS DISTINCT FROM v_canonical_hash
           OR v_candidate.generated_at IS DISTINCT FROM p_generated_at
           OR v_candidate.payload IS DISTINCT FROM v_canonical_payload
           OR v_candidate.base_published_version_id IS DISTINCT FROM v_source.published_version_id
           OR v_run.metrics IS DISTINCT FROM p_metrics
           OR (
               p_delta_link IS NOT NULL
               AND v_source.delta_link IS DISTINCT FROM p_delta_link
           ) THEN
            RAISE EXCEPTION 'Catalog candidate replay conflict';
        END IF;
        RETURN v_candidate.id;
    END IF;

    IF v_run.status <> 'running'
       OR v_run.candidate_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'catalog sync run is not stageable';
    END IF;

    INSERT INTO saas_catalog_snapshot_versions (
        id, supplier, source_hash, generated_at, status, payload,
        base_published_version_id, sync_run_id
    ) VALUES (
        v_candidate_id, v_source.supplier, v_canonical_hash, p_generated_at,
        'candidate', v_canonical_payload, v_source.published_version_id, v_run.id
    );

    UPDATE saas_catalog_sources
    SET delta_link = COALESCE(p_delta_link, delta_link),
        updated_at = NOW()
    WHERE id = v_source.id;

    UPDATE saas_catalog_sync_runs
    SET status = 'awaiting_approval',
        candidate_version_id = v_candidate_id,
        metrics = p_metrics,
        finished_at = NOW(),
        updated_at = NOW()
    WHERE id = v_run.id;

    RETURN v_candidate_id;
END;
$$;
