-- Amplia los catalogos genericos a IDELIKA y Conceptos sin reescribir historial.
-- Se ejecuta despues de las migraciones de catalogos de 2026_07.

BEGIN;

ALTER TABLE saas_catalog_sources
    DROP CONSTRAINT IF EXISTS saas_catalog_sources_supplier_check;
ALTER TABLE saas_catalog_sources
    ADD CONSTRAINT saas_catalog_sources_supplier_check
    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'));

ALTER TABLE saas_catalog_snapshot_versions
    DROP CONSTRAINT IF EXISTS saas_catalog_snapshot_versions_supplier_check;
ALTER TABLE saas_catalog_snapshot_versions
    ADD CONSTRAINT saas_catalog_snapshot_versions_supplier_check
    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'));

ALTER TABLE saas_catalog_reservations
    DROP CONSTRAINT IF EXISTS saas_catalog_reservations_supplier_check;
ALTER TABLE saas_catalog_reservations
    ADD CONSTRAINT saas_catalog_reservations_supplier_check
    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'));

-- Las nueve fuentes comparten el mismo drive y elemento raiz. Conserva cualquier
-- configuracion previa y usa CR Global solo como semilla para las dos fuentes nuevas.
DO $$
DECLARE
    v_graph_drive_id TEXT;
    v_graph_root_item_id TEXT;
BEGIN
    SELECT graph_drive_id, graph_root_item_id
    INTO v_graph_drive_id, v_graph_root_item_id
    FROM saas_catalog_sources
    WHERE supplier = 'cr-global';

    IF v_graph_drive_id IS NULL OR v_graph_root_item_id IS NULL THEN
        RAISE EXCEPTION 'CR Global catalog source is required to provision IDELIKA/Conceptos';
    END IF;

    INSERT INTO saas_catalog_sources (
        supplier, label, adapter, graph_drive_id, graph_root_item_id, enabled
    )
    SELECT source.supplier, source.label, source.adapter,
           v_graph_drive_id, v_graph_root_item_id, TRUE
    FROM (VALUES
        ('idelika', 'IDÉLIKA', 'idelika'),
        ('conceptos', 'Conceptos', 'conceptos')
    ) AS source(supplier, label, adapter)
    ON CONFLICT (supplier) DO NOTHING;
END;
$$;

-- Conserva definiciones y controles ya auditados; solo amplia los conjuntos
-- cerrados de proveedores, el limite de sincronizacion y los grupos mixtos.
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
            $old$'cr-global','sonara','sunon','alma','lumbro','jome','lauco'$old$,
            $new$'cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'$new$
        );
        v_definition := replace(
            v_definition,
            'CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 7',
            'CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 9'
        );
        v_definition := replace(
            v_definition,
            'jsonb_array_length(p_groups) NOT BETWEEN 0 AND 9',
            'jsonb_array_length(p_groups) NOT BETWEEN 0 AND 11'
        );
        IF v_definition IS NOT DISTINCT FROM v_original THEN
            RAISE EXCEPTION 'IDELIKA/Conceptos migration could not update %', v_signature;
        END IF;
        EXECUTE v_definition;
    END LOOP;
END;
$$;

COMMIT;
