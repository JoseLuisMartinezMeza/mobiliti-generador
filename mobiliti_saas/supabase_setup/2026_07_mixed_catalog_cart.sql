CREATE OR REPLACE FUNCTION saas_reserve_mixed_cart(
    p_usuario_id INTEGER,
    p_quote_job_id UUID,
    p_groups JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_group JSONB;
    v_item JSONB;
    v_catalog TEXT;
    v_identity TEXT;
    v_sku TEXT;
    v_quantity NUMERIC;
    v_stock NUMERIC;
    v_total_lines INTEGER := 0;
    v_result JSONB := '[]'::JSONB;
    v_seen_catalogs TEXT[] := ARRAY[]::TEXT[];
    v_row RECORD;
    v_reserved_before NUMERIC(20, 6);
    v_available_before NUMERIC(20, 6);
    v_reserved_by_others BOOLEAN;
    v_insufficient BOOLEAN;
BEGIN
    PERFORM 1
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id
      AND usuario_id = p_usuario_id
      AND status = 'draft'
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mixed quote job is invalid';
    END IF;

    IF p_groups IS NULL OR jsonb_typeof(p_groups) <> 'array'
       OR jsonb_array_length(p_groups) NOT BETWEEN 0 AND 7 THEN
        RAISE EXCEPTION 'mixed groups must be a bounded array';
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS mixed_reservation_lines (
        catalog TEXT NOT NULL,
        identity TEXT NOT NULL,
        sku TEXT NOT NULL,
        quantity NUMERIC(20, 6) NOT NULL,
        stock NUMERIC(20, 6) NOT NULL,
        PRIMARY KEY (catalog, identity)
    ) ON COMMIT DROP;

    DELETE FROM pg_temp.mixed_reservation_lines;

    FOR v_group IN SELECT value FROM jsonb_array_elements(p_groups)
    LOOP
        IF jsonb_typeof(v_group) <> 'object'
           OR NOT (v_group ?& ARRAY['catalog','items'])
           OR (v_group - ARRAY['catalog','items']::TEXT[]) <> '{}'::JSONB
           OR jsonb_typeof(v_group -> 'catalog') <> 'string'
           OR jsonb_typeof(v_group -> 'items') <> 'array' THEN
            RAISE EXCEPTION 'mixed group has invalid shape';
        END IF;

        v_catalog := btrim(v_group ->> 'catalog');
        IF v_catalog NOT IN ('tarkett','offiho','cr-global','sonara','sunon','alma','lumbro') THEN
            RAISE EXCEPTION 'mixed catalog is invalid';
        END IF;
        IF v_catalog = ANY(v_seen_catalogs) THEN
            RAISE EXCEPTION 'mixed catalog is duplicated';
        END IF;
        v_seen_catalogs := array_append(v_seen_catalogs, v_catalog);
        IF jsonb_array_length(v_group -> 'items') = 0 THEN
            RAISE EXCEPTION 'mixed reservation group is empty';
        END IF;
        v_total_lines := v_total_lines + jsonb_array_length(v_group -> 'items');
        IF v_total_lines > 500 THEN
            RAISE EXCEPTION 'mixed reservation line count is invalid';
        END IF;

        FOR v_item IN SELECT value FROM jsonb_array_elements(v_group -> 'items')
        LOOP
            IF jsonb_typeof(v_item) <> 'object'
               OR NOT (v_item ?& ARRAY['identity','quantity','sku','stock'])
               OR (v_item - ARRAY['identity','quantity','sku','stock']::TEXT[]) <> '{}'::JSONB
               OR jsonb_typeof(v_item -> 'identity') <> 'string'
               OR jsonb_typeof(v_item -> 'quantity') <> 'string'
               OR jsonb_typeof(v_item -> 'sku') <> 'string'
               OR jsonb_typeof(v_item -> 'stock') <> 'string' THEN
                RAISE EXCEPTION 'mixed reservation item has invalid shape';
            END IF;

            v_identity := btrim(v_item ->> 'identity');
            v_sku := btrim(v_item ->> 'sku');
            IF char_length(v_identity) NOT BETWEEN 1 AND 500
               OR v_identity ~ '[[:cntrl:]]'
               OR char_length(v_sku) > 500
               OR v_sku ~ '[[:cntrl:]]' THEN
                RAISE EXCEPTION 'mixed reservation identity is invalid';
            END IF;
            IF v_sku = '' AND v_catalog NOT IN ('sonara','lumbro') THEN
                RAISE EXCEPTION 'mixed reservation sku is invalid';
            END IF;
            IF (v_item ->> 'quantity') !~ '^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$'
               OR (v_item ->> 'stock') !~ '^(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?$' THEN
                RAISE EXCEPTION 'mixed reservation decimal is invalid';
            END IF;
            v_quantity := (v_item ->> 'quantity')::NUMERIC;
            v_stock := (v_item ->> 'stock')::NUMERIC;
            IF v_quantity <= 0 OR v_quantity > 1000000
               OR v_stock < 0 OR v_stock > 1000000000 THEN
                RAISE EXCEPTION 'mixed reservation decimal is out of range';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_temp.mixed_reservation_lines
                WHERE catalog = v_catalog AND identity = v_identity
            ) THEN
                RAISE EXCEPTION 'mixed reservation identity is duplicated';
            END IF;
            INSERT INTO pg_temp.mixed_reservation_lines
                (catalog, identity, sku, quantity, stock)
            VALUES (v_catalog, v_identity, v_sku, v_quantity, v_stock);
        END LOOP;
    END LOOP;

    IF EXISTS (SELECT 1 FROM saas_tarkett_reservations WHERE quote_job_id = p_quote_job_id)
       OR EXISTS (SELECT 1 FROM saas_offiho_reservations WHERE quote_job_id = p_quote_job_id)
       OR EXISTS (SELECT 1 FROM saas_catalog_reservations WHERE quote_job_id = p_quote_job_id) THEN
        RAISE EXCEPTION 'mixed quote job already has reservations';
    END IF;

    IF v_total_lines = 0 THEN
        RETURN '[]'::JSONB;
    END IF;

    FOR v_catalog, v_identity IN
        SELECT catalog, identity
        FROM pg_temp.mixed_reservation_lines
        ORDER BY catalog, identity
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(v_catalog || ':' || v_identity, 0)
        );
    END LOOP;

    FOR v_row IN
        SELECT catalog, identity, sku, quantity, stock
        FROM pg_temp.mixed_reservation_lines
        ORDER BY catalog, identity
    LOOP
        IF v_row.catalog = 'tarkett' THEN
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_tarkett_reservations
            WHERE product_code = v_row.identity AND status = 'active';
        ELSIF v_row.catalog = 'offiho' THEN
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_offiho_reservations
            WHERE product_code = v_row.identity AND status = 'active';
        ELSE
            SELECT COALESCE(SUM(quantity), 0),
                   COALESCE(BOOL_OR(usuario_id <> p_usuario_id), FALSE)
            INTO v_reserved_before, v_reserved_by_others
            FROM saas_catalog_reservations
            WHERE supplier = v_row.catalog
              AND internal_id = v_row.identity
              AND status = 'active';
        END IF;

        v_available_before := GREATEST(v_row.stock - v_reserved_before, 0);
        v_insufficient := v_row.quantity > v_available_before;

        IF v_row.catalog = 'tarkett' THEN
            INSERT INTO saas_tarkett_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_row.identity,
                 v_row.quantity, 'active', NOW(), NOW());
        ELSIF v_row.catalog = 'offiho' THEN
            INSERT INTO saas_offiho_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
            VALUES
                (gen_random_uuid(), p_usuario_id, p_quote_job_id, v_row.identity,
                 v_row.quantity, 'active', NOW(), NOW());
        ELSE
            INSERT INTO saas_catalog_reservations
                (supplier, internal_id, sku, quantity, usuario_id, quote_job_id,
                 status, created_at, updated_at)
            VALUES
                (v_row.catalog, v_row.identity, v_row.sku, v_row.quantity,
                 p_usuario_id, p_quote_job_id, 'active', NOW(), NOW());
        END IF;

        v_result := v_result || jsonb_build_array(jsonb_build_object(
            'catalog', v_row.catalog,
            'identity', v_row.identity,
            'reserved_before', to_char(
                v_reserved_before, 'FM999999999999999999999999990.000000'
            ),
            'available_before', to_char(
                v_available_before, 'FM999999999999999999999999990.000000'
            ),
            'insufficient', v_insufficient,
            'reserved_by_others', v_reserved_by_others
        ));
    END LOOP;

    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION saas_release_mixed_cart(p_quote_job_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_tarkett INTEGER;
    v_offiho INTEGER;
    v_supplier INTEGER;
    v_job_status TEXT;
    v_catalog TEXT;
    v_identity TEXT;
BEGIN
    SELECT status
    INTO v_job_status
    FROM saas_quote_jobs
    WHERE id = p_quote_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mixed quote job is invalid';
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS mixed_release_lines (
        catalog TEXT NOT NULL,
        identity TEXT NOT NULL,
        PRIMARY KEY (catalog, identity)
    ) ON COMMIT DROP;

    DELETE FROM pg_temp.mixed_release_lines;

    INSERT INTO pg_temp.mixed_release_lines (catalog, identity)
    SELECT 'tarkett', product_code
    FROM saas_tarkett_reservations
    WHERE quote_job_id = p_quote_job_id AND status = 'active'
    ON CONFLICT (catalog, identity) DO NOTHING;

    INSERT INTO pg_temp.mixed_release_lines (catalog, identity)
    SELECT 'offiho', product_code
    FROM saas_offiho_reservations
    WHERE quote_job_id = p_quote_job_id AND status = 'active'
    ON CONFLICT (catalog, identity) DO NOTHING;

    INSERT INTO pg_temp.mixed_release_lines (catalog, identity)
    SELECT supplier, internal_id
    FROM saas_catalog_reservations
    WHERE quote_job_id = p_quote_job_id AND status = 'active'
    ON CONFLICT (catalog, identity) DO NOTHING;

    FOR v_catalog, v_identity IN
        SELECT catalog, identity
        FROM pg_temp.mixed_release_lines
        ORDER BY catalog, identity
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(v_catalog || ':' || v_identity, 0)
        );
    END LOOP;

    IF v_job_status = 'draft' THEN
        UPDATE saas_quote_jobs
        SET status = 'failed',
            error_message = COALESCE(NULLIF(error_message, ''), 'mixed reservations released'),
            updated_at = NOW()
        WHERE id = p_quote_job_id;
    END IF;

    UPDATE saas_tarkett_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_tarkett = ROW_COUNT;

    UPDATE saas_offiho_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_offiho = ROW_COUNT;

    UPDATE saas_catalog_reservations
    SET status = 'released', updated_at = NOW()
    WHERE quote_job_id = p_quote_job_id AND status = 'active';
    GET DIAGNOSTICS v_supplier = ROW_COUNT;

    RETURN jsonb_build_object(
        'tarkett', v_tarkett, 'offiho', v_offiho, 'supplier', v_supplier
    );
END;
$$;

REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM anon;
REVOKE ALL ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_reserve_mixed_cart(INTEGER, UUID, JSONB) TO service_role;

REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM anon;
REVOKE ALL ON FUNCTION saas_release_mixed_cart(UUID) FROM authenticated;
GRANT EXECUTE ON FUNCTION saas_release_mixed_cart(UUID) TO service_role;
