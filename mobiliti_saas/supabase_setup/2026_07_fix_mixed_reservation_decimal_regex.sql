-- Corrige una doble escapatoria introducida al reescribir la función con
-- pg_get_functiondef(). El patrón anterior rechazaba decimales canónicos
-- como 1.000000 y bloqueaba cualquier cotización mixta con reservas.
DO $migration$
DECLARE
    v_signature REGPROCEDURE :=
        'public.saas_reserve_mixed_cart(integer,uuid,jsonb)'::REGPROCEDURE;
    v_definition TEXT;
BEGIN
    v_definition := pg_get_functiondef(v_signature);

    -- Reemplaza primero dos barras y después una. La clase [.] evita que una
    -- futura reescritura de la función vuelva a duplicar el escape del punto.
    v_definition := replace(
        v_definition,
        chr(92) || chr(92) || '.',
        '[.]'
    );
    v_definition := replace(
        v_definition,
        chr(92) || '.',
        '[.]'
    );

    IF position(
        '^(?:0|[1-9][0-9]{0,6})(?:[.][0-9]{1,6})?$'
        IN v_definition
    ) = 0 OR position(
        '^(?:0|[1-9][0-9]{0,9})(?:[.][0-9]{1,6})?$'
        IN v_definition
    ) = 0 THEN
        RAISE EXCEPTION
            'saas_reserve_mixed_cart decimal patterns were not repaired';
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
