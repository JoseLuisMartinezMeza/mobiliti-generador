-- Este corte sólo se ejecuta tras el backfill certificado por la Tarea 6.
BEGIN;

DO $$
DECLARE
    v_batch public.saas_catalog_asset_cutover_batches%ROWTYPE;
    v_keyset_digest TEXT;
    v_manifest_digest TEXT;
    v_entry_count INTEGER;
    v_asset_count INTEGER;
BEGIN
    SELECT * INTO v_batch
    FROM public.saas_catalog_asset_cutover_batches
    WHERE status = 'verified'
      AND expected_count = 2214
      AND verified_count = 2214
      AND missing_count = 0
      AND failed_count = 0
      AND verified_at IS NOT NULL
    ORDER BY verified_at DESC, batch_id DESC
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'catalog asset R2 cutover manifest is not verified';
    END IF;

    SELECT COUNT(*)::INTEGER,
           encode(extensions.digest(convert_to(string_agg(e.object_name, E'\n' ORDER BY e.object_name), 'UTF8'), 'sha256'), 'hex'),
           encode(extensions.digest(convert_to(string_agg(
               e.object_name || '|' || e.sha256 || '|' || e.byte_size::TEXT || '|' || e.mime_type,
               E'\n' ORDER BY e.object_name
           ), 'UTF8'), 'sha256'), 'hex')
    INTO v_entry_count, v_keyset_digest, v_manifest_digest
    FROM public.saas_catalog_asset_cutover_entries e
    WHERE e.batch_id = v_batch.batch_id;

    IF v_entry_count IS DISTINCT FROM 2214
       OR v_keyset_digest IS DISTINCT FROM v_batch.keyset_digest
       OR v_manifest_digest IS DISTINCT FROM v_batch.manifest_digest THEN
        RAISE EXCEPTION 'catalog asset R2 cutover manifest is not verified';
    END IF;

    SELECT COUNT(*)::INTEGER INTO v_asset_count
    FROM public.saas_catalog_asset_cutover_entries AS entry
    JOIN public.saas_catalog_assets AS asset
      ON asset.object_name = entry.object_name
     AND asset.sha256 = entry.sha256
     AND asset.byte_size = entry.byte_size
     AND asset.mime_type = entry.mime_type
     AND asset.storage_provider = 'r2'
     AND asset.physical_bucket = 'catalog-assets'
     AND asset.verified_at IS NOT NULL
     AND asset.cutover_batch_id = v_batch.batch_id
    WHERE entry.batch_id = v_batch.batch_id;
    IF v_asset_count IS DISTINCT FROM 2214 THEN
        RAISE EXCEPTION 'catalog asset R2 cutover registry is not verified';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION saas_clone_catalog_candidate_with_asset(
    p_candidate_id UUID, p_reviewed_by INTEGER, p_asset_object_name TEXT, p_json_path TEXT[]
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate public.saas_catalog_snapshot_versions%ROWTYPE;
    v_new_id UUID := extensions.gen_random_uuid(); v_existing_item JSONB; v_new_item JSONB;
    v_new_payload JSONB; v_new_hash TEXT; v_approved_at TIMESTAMPTZ := NOW();
BEGIN
    IF p_candidate_id IS NULL OR p_reviewed_by IS NULL
       OR p_asset_object_name IS NULL OR p_json_path IS NULL
       OR p_asset_object_name !~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
       OR COALESCE(array_length(p_json_path, 1), 0) <> 2 OR COALESCE(array_lower(p_json_path, 1), 0) <> 1
       OR p_json_path[1] IS DISTINCT FROM 'items' OR p_json_path[2] IS NULL
       OR p_json_path[2] !~ '^(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'invalid catalog item asset target'; END IF;
    IF p_reviewed_by IS NULL OR NOT EXISTS (SELECT 1 FROM public.saas_usuarios WHERE id = p_reviewed_by AND activo IS TRUE AND es_admin IS TRUE)
    THEN RAISE EXCEPTION 'active admin reviewer is required'; END IF;
    PERFORM 1 FROM public.saas_catalog_assets
    WHERE object_name = p_asset_object_name AND storage_provider = 'r2'
      AND physical_bucket = 'catalog-assets' AND verified_at IS NOT NULL;
    IF NOT FOUND THEN RAISE EXCEPTION 'approved catalog asset does not exist'; END IF;
    SELECT * INTO v_candidate FROM public.saas_catalog_snapshot_versions WHERE id = p_candidate_id FOR UPDATE;
    IF NOT FOUND OR v_candidate.status <> 'candidate' OR v_candidate.sync_run_id IS NULL THEN RAISE EXCEPTION 'catalog candidate is not cloneable'; END IF;
    v_existing_item := v_candidate.payload #> p_json_path;
    IF v_existing_item IS NULL OR jsonb_typeof(v_existing_item) <> 'object' OR jsonb_typeof(v_existing_item -> 'attributes') <> 'object'
    THEN RAISE EXCEPTION 'catalog item asset target does not exist'; END IF;
    PERFORM 1 FROM public.saas_catalog_sync_runs WHERE id = v_candidate.sync_run_id AND candidate_version_id = v_candidate.id AND status = 'awaiting_approval' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'catalog sync run is not awaiting approval'; END IF;
    v_new_item := jsonb_set(jsonb_set(jsonb_set(v_existing_item, '{attributes,approved_asset}', jsonb_build_object(
        'bucket','catalog-assets','path',p_asset_object_name,'label','Imagen de referencia','approved',TRUE,
        'approved_by',p_reviewed_by,'approved_at',v_approved_at), TRUE), '{image_url}', '""'::JSONB, TRUE),
        '{image_kind}', '"generated_reference"'::JSONB, TRUE);
    v_new_payload := jsonb_set(v_candidate.payload, p_json_path, v_new_item, FALSE);
    IF v_new_payload IS NOT DISTINCT FROM v_candidate.payload
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','bucket']) <> 'catalog-assets'
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','path']) IS DISTINCT FROM p_asset_object_name
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','label']) <> 'Imagen de referencia'
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','approved']) <> 'true'
       OR v_new_payload #>> (p_json_path || ARRAY['image_kind']) <> 'generated_reference'
    THEN RAISE EXCEPTION 'catalog asset clone did not produce the approved payload'; END IF;
    v_new_hash := encode(extensions.digest(convert_to(v_new_payload::TEXT, 'UTF8'), 'sha256'), 'hex');
    v_new_payload := jsonb_set(v_new_payload, '{source_hash}', to_jsonb(v_new_hash), TRUE);
    INSERT INTO public.saas_catalog_snapshot_versions (id,supplier,source_hash,generated_at,status,payload,previous_snapshot_id,sync_run_id,base_published_version_id,reviewed_by,review_note,reviewed_at)
    VALUES (v_new_id,v_candidate.supplier,v_new_hash,v_candidate.generated_at,'candidate',v_new_payload,v_candidate.id,v_candidate.sync_run_id,v_candidate.base_published_version_id,p_reviewed_by,'Approved catalog asset ' || p_asset_object_name,v_approved_at);
    UPDATE public.saas_catalog_snapshot_versions SET status = 'superseded' WHERE id = v_candidate.id;
    UPDATE public.saas_catalog_sync_runs SET candidate_version_id = v_new_id, updated_at = NOW() WHERE id = v_candidate.sync_run_id;
    RETURN v_new_id;
END;
$$;

CREATE OR REPLACE FUNCTION saas_clone_catalog_candidate_with_image_metadata(
    p_candidate_id UUID, p_reviewed_by INTEGER, p_asset_object_name TEXT, p_json_path TEXT[],
    p_image_kind TEXT, p_image_label TEXT, p_image_references TEXT[]
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_candidate public.saas_catalog_snapshot_versions%ROWTYPE;
    v_new_id UUID := extensions.gen_random_uuid(); v_existing_item JSONB; v_new_item JSONB;
    v_new_payload JSONB; v_new_hash TEXT; v_approved_at TIMESTAMPTZ := NOW();
BEGIN
    IF p_candidate_id IS NULL OR p_reviewed_by IS NULL
       OR p_asset_object_name IS NULL OR p_json_path IS NULL
       OR p_image_kind IS NULL OR p_image_label IS NULL OR p_image_references IS NULL
       OR p_asset_object_name !~ '^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$'
       OR COALESCE(array_length(p_json_path, 1), 0) <> 2 OR COALESCE(array_lower(p_json_path, 1), 0) <> 1
       OR p_json_path[1] IS DISTINCT FROM 'items' OR p_json_path[2] IS NULL OR p_json_path[2] !~ '^(0|[1-9][0-9]*)$'
       OR p_image_kind NOT IN ('official','generated_reference') OR COALESCE(array_length(p_image_references, 1), 0) > 20
    THEN RAISE EXCEPTION 'invalid catalog image metadata'; END IF;
    IF p_image_kind = 'generated_reference' AND (NULLIF(BTRIM(p_image_label), '') IS NULL OR LENGTH(BTRIM(p_image_label)) > 300
       OR COALESCE(array_length(p_image_references, 1), 0) = 0 OR EXISTS (SELECT 1 FROM unnest(p_image_references) AS reference(url) WHERE url !~ '^https://[^[:space:]/]+(?:/[^[:space:]]*)?$'))
    THEN RAISE EXCEPTION 'generated catalog image requires label and HTTPS references'; END IF;
    IF p_reviewed_by IS NULL OR NOT EXISTS (SELECT 1 FROM public.saas_usuarios WHERE id = p_reviewed_by AND activo IS TRUE AND es_admin IS TRUE)
    THEN RAISE EXCEPTION 'active admin reviewer is required'; END IF;
    PERFORM 1 FROM public.saas_catalog_assets
    WHERE object_name = p_asset_object_name AND storage_provider = 'r2'
      AND physical_bucket = 'catalog-assets' AND verified_at IS NOT NULL;
    IF NOT FOUND THEN RAISE EXCEPTION 'approved catalog asset does not exist'; END IF;
    SELECT * INTO v_candidate FROM public.saas_catalog_snapshot_versions WHERE id = p_candidate_id FOR UPDATE;
    IF NOT FOUND OR v_candidate.status <> 'candidate' OR v_candidate.sync_run_id IS NULL THEN RAISE EXCEPTION 'catalog candidate is not cloneable'; END IF;
    v_existing_item := v_candidate.payload #> p_json_path;
    IF v_existing_item IS NULL OR jsonb_typeof(v_existing_item) <> 'object' OR jsonb_typeof(v_existing_item -> 'attributes') <> 'object'
    THEN RAISE EXCEPTION 'catalog item asset target does not exist'; END IF;
    PERFORM 1 FROM public.saas_catalog_sync_runs WHERE id = v_candidate.sync_run_id AND candidate_version_id = v_candidate.id AND status = 'awaiting_approval' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'catalog sync run is not awaiting approval'; END IF;
    v_new_item := jsonb_set(jsonb_set(jsonb_set(v_existing_item, '{attributes,approved_asset}', jsonb_build_object(
        'bucket','catalog-assets','path',p_asset_object_name,'label',CASE WHEN p_image_kind = 'generated_reference' THEN 'Imagen de referencia' ELSE 'Imagen oficial' END,
        'image_kind',p_image_kind,'source_label',NULLIF(BTRIM(p_image_label), ''),'references',to_jsonb(COALESCE(p_image_references, ARRAY[]::TEXT[])),
        'approved',TRUE,'approved_by',p_reviewed_by,'approved_at',v_approved_at), TRUE), '{image_url}', '""'::JSONB, TRUE), '{image_kind}', to_jsonb(p_image_kind), TRUE);
    v_new_payload := jsonb_set(v_candidate.payload, p_json_path, v_new_item, FALSE);
    IF v_new_payload IS NOT DISTINCT FROM v_candidate.payload
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','bucket']) <> 'catalog-assets'
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','path']) IS DISTINCT FROM p_asset_object_name
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','image_kind']) IS DISTINCT FROM p_image_kind
       OR v_new_payload #>> (p_json_path || ARRAY['attributes','approved_asset','approved']) <> 'true'
       OR v_new_payload #>> (p_json_path || ARRAY['image_kind']) IS DISTINCT FROM p_image_kind
    THEN RAISE EXCEPTION 'catalog image metadata clone did not produce the approved payload'; END IF;
    v_new_hash := encode(extensions.digest(convert_to(v_new_payload::TEXT, 'UTF8'), 'sha256'), 'hex');
    v_new_payload := jsonb_set(v_new_payload, '{source_hash}', to_jsonb(v_new_hash), TRUE);
    INSERT INTO public.saas_catalog_snapshot_versions (id,supplier,source_hash,generated_at,status,payload,previous_snapshot_id,sync_run_id,base_published_version_id,reviewed_by,review_note,reviewed_at)
    VALUES (v_new_id,v_candidate.supplier,v_new_hash,v_candidate.generated_at,'candidate',v_new_payload,v_candidate.id,v_candidate.sync_run_id,v_candidate.base_published_version_id,p_reviewed_by,'Approved catalog asset ' || p_asset_object_name,v_approved_at);
    UPDATE public.saas_catalog_snapshot_versions SET status = 'superseded' WHERE id = v_candidate.id;
    UPDATE public.saas_catalog_sync_runs SET candidate_version_id = v_new_id, updated_at = NOW() WHERE id = v_candidate.sync_run_id;
    RETURN v_new_id;
END;
$$;

REVOKE ALL ON FUNCTION saas_clone_catalog_candidate_with_asset(UUID, INTEGER, TEXT, TEXT[]) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION saas_clone_catalog_candidate_with_image_metadata(UUID, INTEGER, TEXT, TEXT[], TEXT, TEXT, TEXT[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION saas_clone_catalog_candidate_with_asset(UUID, INTEGER, TEXT, TEXT[]) TO service_role;
GRANT EXECUTE ON FUNCTION saas_clone_catalog_candidate_with_image_metadata(UUID, INTEGER, TEXT, TEXT[], TEXT, TEXT, TEXT[]) TO service_role;

COMMIT;
