-- Amplía los catálogos genéricos a JOME y Lauco sin reescribir historial.
-- Se ejecuta después de las migraciones de catálogos y límites físicos de 2026_07.

BEGIN;

ALTER TABLE saas_catalog_sources
    DROP CONSTRAINT IF EXISTS saas_catalog_sources_supplier_check;
ALTER TABLE saas_catalog_sources
    ADD CONSTRAINT saas_catalog_sources_supplier_check
    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco'));

ALTER TABLE saas_catalog_snapshot_versions
    DROP CONSTRAINT IF EXISTS saas_catalog_snapshot_versions_supplier_check;
ALTER TABLE saas_catalog_snapshot_versions
    ADD CONSTRAINT saas_catalog_snapshot_versions_supplier_check
    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco'));

ALTER TABLE saas_catalog_reservations
    DROP CONSTRAINT IF EXISTS saas_catalog_reservations_supplier_check;
ALTER TABLE saas_catalog_reservations
    ADD CONSTRAINT saas_catalog_reservations_supplier_check
    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco'));

-- Conserva las definiciones, permisos, SECURITY DEFINER, límites y locks ya
-- auditados; solo amplía los conjuntos cerrados de proveedores y grupos mixtos.
DO $$
DECLARE
    v_signature REGPROCEDURE;
    v_original TEXT;
    v_definition TEXT;
BEGIN
    FOREACH v_signature IN ARRAY ARRAY[
        'public.saas_recover_stale_catalog_sync_runs(text[])'::REGPROCEDURE,
        'public.saas_claim_next_catalog_sync(text[])'::REGPROCEDURE,
        'public.saas_catalog_reservation_summary(text,integer)'::REGPROCEDURE,
        'public.saas_reserve_catalog_items(integer,uuid,text,jsonb)'::REGPROCEDURE,
        'public.saas_reserve_mixed_cart(integer,uuid,jsonb)'::REGPROCEDURE
    ]
    LOOP
        v_original := pg_get_functiondef(v_signature);
        v_definition := replace(
            v_original,
            $old$'cr-global','sonara','sunon','alma','lumbro'$old$,
            $new$'cr-global','sonara','sunon','alma','lumbro','jome','lauco'$new$
        );
        v_definition := replace(
            v_definition,
            'CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 5',
            'CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 7'
        );
        v_definition := replace(
            v_definition,
            'jsonb_array_length(p_groups) NOT BETWEEN 0 AND 7',
            'jsonb_array_length(p_groups) NOT BETWEEN 0 AND 9'
        );
        IF v_definition IS NOT DISTINCT FROM v_original THEN
            RAISE EXCEPTION 'JOME/Lauco migration could not update %', v_signature;
        END IF;
        EXECUTE v_definition;
    END LOOP;
END;
$$;

COMMIT;
