-- Supabase protege operaciones DELETE ejecutadas por service_role y rechaza
-- aquellas que no declaran un WHERE, incluso sobre tablas temporales.
-- Las columnas catalog son NOT NULL, así que esta condición conserva la
-- semántica de vaciar la tabla temporal sin desactivar esa protección.
DO $migration$
DECLARE
    v_signature REGPROCEDURE :=
        'public.saas_reserve_mixed_cart(integer,uuid,jsonb)'::REGPROCEDURE;
    v_definition TEXT;
    v_unscoped TEXT := concat(
        'DELETE',
        ' FROM pg_temp.mixed_reservation_lines;'
    );
    v_scoped TEXT :=
        'DELETE FROM pg_temp.mixed_reservation_lines WHERE catalog IS NOT NULL;';
BEGIN
    v_definition := pg_get_functiondef(v_signature);

    IF position(v_unscoped IN v_definition) > 0 THEN
        v_definition := replace(v_definition, v_unscoped, v_scoped);
    END IF;

    IF position(v_unscoped IN v_definition) > 0
       OR position(v_scoped IN v_definition) = 0 THEN
        RAISE EXCEPTION
            'saas_reserve_mixed_cart temp cleanup was not scoped';
    END IF;

    EXECUTE v_definition;
END
$migration$;

DO $migration$
DECLARE
    v_signature REGPROCEDURE :=
        'public.saas_release_mixed_cart(uuid)'::REGPROCEDURE;
    v_definition TEXT;
    v_unscoped TEXT := concat(
        'DELETE',
        ' FROM pg_temp.mixed_release_lines;'
    );
    v_scoped TEXT :=
        'DELETE FROM pg_temp.mixed_release_lines WHERE catalog IS NOT NULL;';
BEGIN
    v_definition := pg_get_functiondef(v_signature);

    IF position(v_unscoped IN v_definition) > 0 THEN
        v_definition := replace(v_definition, v_unscoped, v_scoped);
    END IF;

    IF position(v_unscoped IN v_definition) > 0
       OR position(v_scoped IN v_definition) = 0 THEN
        RAISE EXCEPTION
            'saas_release_mixed_cart temp cleanup was not scoped';
    END IF;

    EXECUTE v_definition;
END
$migration$;

REVOKE ALL ON FUNCTION
    saas_reserve_mixed_cart(INTEGER, UUID, JSONB)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION
    saas_reserve_mixed_cart(INTEGER, UUID, JSONB)
    TO service_role;

REVOKE ALL ON FUNCTION
    saas_release_mixed_cart(UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION
    saas_release_mixed_cart(UUID)
    TO service_role;
