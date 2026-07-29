import re
from pathlib import Path


SETUP = Path("mobiliti_saas/supabase_setup")
CATALOG_MIGRATION = SETUP / "2026_07_multi_supplier_catalogs.sql"
JOME_LAUCO_MIGRATION = SETUP / "2026_07_jome_lauco_catalogs.sql"
JOBS_RLS_MIGRATION = SETUP / "2026_07_jobs_rls.sql"
BOOTSTRAP = SETUP / "create_tables.sql"
MIXED_MIGRATION = SETUP / "2026_07_mixed_catalog_cart.sql"
PHYSICAL_LIMITS_MIGRATION = SETUP / "2026_07_quote_physical_limits.sql"
MIXED_DECIMAL_REGEX_MIGRATION = (
    SETUP / "2026_07_fix_mixed_reservation_decimal_regex.sql"
)
MIXED_TEMP_CLEANUP_MIGRATION = (
    SETUP / "2026_07_scope_mixed_temp_cleanup.sql"
)
PHYSICAL_QUOTE_LINE_LIMIT = 1_048_512
SQL_FILES = (BOOTSTRAP,)
EXPECTED_SUPPLIERS = (
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
)
MIXED_CATALOG_COUNT = 9
SUPPLIER_ALLOWLIST_CONTEXTS = (
    ("catalog sources", "CREATE TABLE IF NOT EXISTS saas_catalog_sources"),
    ("catalog snapshots", "CREATE TABLE IF NOT EXISTS saas_catalog_snapshot_versions"),
    ("catalog reservations", "CREATE TABLE IF NOT EXISTS saas_catalog_reservations"),
    ("recover stale sync runs", "saas_recover_stale_catalog_sync_runs"),
    ("claim next sync", "saas_claim_next_catalog_sync"),
    ("reservation summary", "saas_catalog_reservation_summary"),
    ("reserve catalog items", "saas_reserve_catalog_items"),
)

CATALOG_TABLES = (
    "saas_catalog_sources",
    "saas_catalog_source_files",
    "saas_catalog_sync_runs",
    "saas_catalog_snapshot_versions",
    "saas_catalog_reservations",
    "saas_exchange_rates",
)


def _function_sql(sql, name):
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    end = sql.find("\nCREATE OR REPLACE FUNCTION ", start + 1)
    if end == -1:
        end = sql.find("\nALTER TABLE saas_catalog_sources ENABLE", start)
    if end == -1:
        end = len(sql)
    return sql[start:end]


def _function_definition(sql, name):
    start = sql.rindex(f"CREATE OR REPLACE FUNCTION {name}")
    end = sql.index("\n$$;", start) + len("\n$$;")
    return re.sub(r"\s+", " ", sql[start:end]).strip()


def test_jome_lauco_migration_replaces_both_reservation_rpcs_safely():
    migration = JOME_LAUCO_MIGRATION.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    for name in ("saas_reserve_catalog_items", "saas_reserve_mixed_cart"):
        current = _function_definition(bootstrap, name)
        assert f"public.{name}" in migration
        assert f"> {PHYSICAL_QUOTE_LINE_LIMIT}" in current
        assert "SECURITY DEFINER" in current
        assert "SET search_path = public, pg_temp" in current
        assert "FOR UPDATE" in current
        assert "pg_advisory_xact_lock" in current

    catalog = _function_definition(bootstrap, "saas_reserve_catalog_items")
    assert "jsonb_array_length(p_lines) = 0" in catalog
    assert "jsonb_array_length(p_lines) > 500" not in catalog
    assert "'jome','lauco'" in catalog
    assert "ORDER BY line ->> 'internal_id'" in catalog
    assert "INSERT INTO saas_catalog_reservations" in catalog
    assert "RETURN NEXT" in catalog

    mixed = _function_definition(bootstrap, "saas_reserve_mixed_cart")
    assert "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 9" in mixed
    assert "'jome','lauco'" in mixed
    assert "jsonb_array_length(v_group -> 'items') = 0" in mixed
    assert "IF v_total_lines = 0 THEN RETURN '[]'::JSONB; END IF;" in mixed
    assert "v_total_lines > 500" not in mixed
    assert "ORDER BY catalog, identity" in mixed
    assert "INSERT INTO saas_tarkett_reservations" in mixed
    assert "INSERT INTO saas_offiho_reservations" in mixed
    assert "INSERT INTO saas_catalog_reservations" in mixed

    signatures = (
        "saas_reserve_catalog_items(INTEGER, UUID, TEXT, JSONB)",
        "saas_reserve_mixed_cart(INTEGER, UUID, JSONB)",
    )
    for signature in signatures:
        assert f"REVOKE ALL ON FUNCTION {signature}" in bootstrap
        assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in bootstrap


def test_mixed_cart_rpcs_are_additive_atomic_and_service_role_only():
    for path in (MIXED_MIGRATION, BOOTSTRAP):
        sql = path.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", sql.lower())
        assert "add column if not exists attempt_token uuid" in normalized
        assert "add column if not exists lease_expires_at timestamptz" in normalized
        assert "alter column input_path drop not null" in normalized
        assert "idx_quote_jobs_processing_lease" in normalized
        assert "where status = 'processing'" in normalized
        for name in ("saas_reserve_mixed_cart", "saas_release_mixed_cart"):
            function = _function_sql(sql, name)
            assert "SECURITY DEFINER" in function
            assert "SET search_path = public, pg_temp" in function
        assert "pg_advisory_xact_lock" in normalized
        assert "order by catalog, identity" in normalized
        assert "pg_temp.mixed_reservation_lines" in normalized
        assert "to_char(" in normalized
        release = _function_sql(sql, "saas_release_mixed_cart").lower()
        assert "from saas_quote_jobs" in release
        assert "for update" in release
        assert "pg_advisory_xact_lock" in release
        assert "order by catalog, identity" in release
        assert "pg_temp.mixed_release_lines" in release
        assert "from saas_tarkett_reservations" in release
        assert "from saas_offiho_reservations" in release
        assert "from saas_catalog_reservations" in release
        assert release.index("order by catalog, identity") < release.index(
            "update saas_tarkett_reservations"
        )
        assert "status = 'failed'" in release
        assert "revoke all on function saas_reserve_mixed_cart" in normalized
        assert "revoke all on function saas_release_mixed_cart" in normalized
        assert "from public" in normalized
        assert "from anon" in normalized
        assert "from authenticated" in normalized
        assert "grant execute on function saas_reserve_mixed_cart" in normalized
        assert "grant execute on function saas_release_mixed_cart" in normalized
        assert "to service_role" in normalized
        assert "create temp table if not exists mixed_reservation_lines" in normalized
        assert "delete from pg_temp.mixed_reservation_lines" in normalized
        assert "drop table" not in normalized
        assert "truncate" not in normalized


def test_mixed_reservation_decimal_regex_survives_function_rewrites():
    migration = MIXED_DECIMAL_REGEX_MIGRATION.read_text(encoding="utf-8")
    normalized_migration = re.sub(r"\s+", " ", migration.lower())

    assert "saas_reserve_mixed_cart(integer, uuid, jsonb)" in normalized_migration
    assert "chr(92) || chr(92) || '.'" in normalized_migration
    assert "chr(92) || '.'" in normalized_migration
    assert "to service_role" in normalized_migration

    for path in (MIXED_MIGRATION, PHYSICAL_LIMITS_MIGRATION, BOOTSTRAP):
        mixed = _function_definition(
            path.read_text(encoding="utf-8"),
            "saas_reserve_mixed_cart",
        )
        assert "(?:[.][0-9]{1,6})?" in mixed
        assert "(?:\\.[0-9]{1,6})?" not in mixed


def test_mixed_reservation_temp_cleanup_is_scoped_for_database_guards():
    migration = MIXED_TEMP_CLEANUP_MIGRATION.read_text(encoding="utf-8")
    normalized_migration = re.sub(r"\s+", " ", migration.lower())

    for table_name in ("mixed_reservation_lines", "mixed_release_lines"):
        unscoped = f"delete from pg_temp.{table_name};"
        scoped = (
            f"delete from pg_temp.{table_name} "
            "where catalog is not null;"
        )
        assert unscoped not in normalized_migration
        assert scoped in normalized_migration

    assert "saas_reserve_mixed_cart(integer, uuid, jsonb)" in normalized_migration
    assert "saas_release_mixed_cart(uuid)" in normalized_migration
    assert "to service_role" in normalized_migration

    for path in (MIXED_MIGRATION, PHYSICAL_LIMITS_MIGRATION, BOOTSTRAP):
        normalized = re.sub(
            r"\s+",
            " ",
            path.read_text(encoding="utf-8").lower(),
        )
        assert "delete from pg_temp.mixed_reservation_lines;" not in normalized
        assert (
            "delete from pg_temp.mixed_reservation_lines "
            "where catalog is not null;"
        ) in normalized

    for path in (MIXED_MIGRATION, BOOTSTRAP):
        normalized = re.sub(
            r"\s+",
            " ",
            path.read_text(encoding="utf-8").lower(),
        )
        assert "delete from pg_temp.mixed_release_lines;" not in normalized
        assert (
            "delete from pg_temp.mixed_release_lines "
            "where catalog is not null;"
        ) in normalized


def _statement(sql, prefix):
    start = sql.index(prefix)
    return sql[start : sql.index(";", start) + 1]


def _without_sql_comments(sql):
    return re.sub(r"--[^\n]*(?:\n|$)|/\*.*?\*/", "", sql, flags=re.DOTALL)


def _sql_string_spans(sql):
    return [
        (match.start(), match.end())
        for match in re.finditer(r"'(?:''|[^'])*'", sql)
    ]


def _supplier_allowlists(sql):
    sql = _without_sql_comments(sql)
    string_spans = _sql_string_spans(sql)
    operand = r"(?:\bp_supplier\b|\b(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?(?:supplier|value)\b)"
    pattern = re.compile(
        rf"""(?isx)
        (?P<in_operand>{operand})\s+(?:NOT\s+)?IN\s*\((?P<in_values>[^)]*)\)
        |
        (?P<any_operand>{operand})\s*=\s*ANY\s*\(
        \s*ARRAY\s*\[(?P<any_values>[^\]]*)\]
        \s*(?:::\s*[A-Za-z_][A-Za-z0-9_.]*(?:\s*\[\s*\])?)?
        \s*\)
        """
    )
    allowlists = []
    for match in pattern.finditer(sql):
        if any(start <= match.start() < end for start, end in string_spans):
            continue
        values = tuple(
            re.findall(
                r"'((?:''|[^'])*)'",
                match.group("in_values") or match.group("any_values"),
            )
        )
        if "cr-global" in values:
            allowlists.append(values)
    return allowlists


def _supplier_allowlist_context_sql(sql):
    contexts = {}
    for label, anchor in SUPPLIER_ALLOWLIST_CONTEXTS:
        contexts[label] = (
            _statement(sql, anchor)
            if anchor.startswith("CREATE TABLE")
            else _function_sql(sql, anchor)
        )
    return contexts


def _normalized_sql(sql):
    return re.sub(r"\s+", "", _without_sql_comments(sql).lower())


def _supplier_cardinality_guards(sql):
    return [
        tuple(map(int, match))
        for match in re.findall(
            r"(?is)CARDINALITY\s*\(\s*p_enabled_suppliers\s*\)\s*NOT\s+BETWEEN\s*(\d+)\s+AND\s*(\d+)",
            _without_sql_comments(sql),
        )
    ]


def test_final_supplier_sql_allowlists_include_jome_and_lauco():
    for sql_path in SQL_FILES:
        sql = sql_path.read_text(encoding="utf-8")
        contexts = _supplier_allowlist_context_sql(sql)

        replacement_allowlists = 1 if sql_path == BOOTSTRAP else 0
        assert len(_supplier_allowlists(sql)) == (
            len(SUPPLIER_ALLOWLIST_CONTEXTS) + replacement_allowlists
        )
        assert tuple(contexts) == tuple(label for label, _ in SUPPLIER_ALLOWLIST_CONTEXTS)
        for label, context_sql in contexts.items():
            assert _supplier_allowlists(context_sql) == [EXPECTED_SUPPLIERS], label


def test_supplier_allowlist_helper_handles_supplier_operands_and_ignores_false_positives():
    sql = """
        -- p_supplier IN ('cr-global', 'sonara', 'sunon', 'alma')
        /* p_supplier = ANY(ARRAY['cr-global','sonara','sunon','alma']::TEXT[]) */
        category IN ('cr-global', 'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco')
        OR other_supplier IN ('cr-global', 'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco')
        OR 'supplier IN (''cr-global'', ''sonara'', ''sunon'', ''alma'', ''lumbro'', ''jome'', ''lauco'')' = 'example'
        p_supplier IN (
            'cr-global'::TEXT,
            'sonara',
            'sunon',
            'alma',
            'lumbro',
            'jome',
            'lauco'
        )
        OR enabled_supplier.value NOT IN (
            'cr-global'::public.supplier_code,
            'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco'
        )
        OR p_supplier = ANY(ARRAY[
            'cr-global', 'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco'
        ]::public.supplier_code[])
    """

    assert _supplier_allowlists(sql) == [
        EXPECTED_SUPPLIERS,
        EXPECTED_SUPPLIERS,
        EXPECTED_SUPPLIERS,
    ]


def test_no_stale_four_supplier_sequences_remain_after_normalizing_sql():
    stale_allowlist = re.compile(
        r"'cr-global','sonara','sunon','alma'(?=[\)\]])"
    )

    for sql_path in SQL_FILES:
        assert not stale_allowlist.search(_normalized_sql(sql_path.read_text(encoding="utf-8")))


def test_supplier_array_cardinality_guards_allow_seven_and_reject_invalid_inputs():
    for sql_path in SQL_FILES:
        sql = sql_path.read_text(encoding="utf-8")
        guards = _supplier_cardinality_guards(sql)

        assert len(guards) == 2
        assert guards == [(1, 7), (1, 7)]
        for function_name in (
            "saas_recover_stale_catalog_sync_runs",
            "saas_claim_next_catalog_sync",
        ):
            function_sql = _function_sql(sql, function_name)
            assert _supplier_cardinality_guards(function_sql) == [(1, 7)]
            assert "COUNT(DISTINCT value) FROM UNNEST(p_enabled_suppliers)" in function_sql
            assert _supplier_allowlists(function_sql) == [EXPECTED_SUPPLIERS]

        assert all(minimum <= len(EXPECTED_SUPPLIERS) <= maximum for minimum, maximum in guards)
        assert all(8 > maximum for _, maximum in guards)


def test_forward_jome_lauco_migration_widens_constraints_without_destructive_table_work():
    migration = JOME_LAUCO_MIGRATION.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    constraints = (
        ("saas_catalog_sources", "saas_catalog_sources_supplier_check"),
        ("saas_catalog_snapshot_versions", "saas_catalog_snapshot_versions_supplier_check"),
        ("saas_catalog_reservations", "saas_catalog_reservations_supplier_check"),
    )

    for table, constraint in constraints:
        assert f"ALTER TABLE {table}\n    DROP CONSTRAINT IF EXISTS {constraint};" in migration
        assert (
            f"ALTER TABLE {table}\n    ADD CONSTRAINT {constraint}\n"
            "    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco'));"
        ) in migration

    functions = (
        "saas_recover_stale_catalog_sync_runs",
        "saas_claim_next_catalog_sync",
        "saas_catalog_reservation_summary",
        "saas_reserve_catalog_items",
        "saas_reserve_mixed_cart",
    )
    assert migration.count("pg_get_functiondef") == 1
    for function_name in functions:
        current = _function_definition(bootstrap, function_name)
        assert f"public.{function_name}" in migration
        assert "SECURITY DEFINER" in current
        assert "SET search_path = public, pg_temp" in current

    assert "CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 7" in migration
    assert "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 9" in migration
    mixed = _function_definition(bootstrap, "saas_reserve_mixed_cart")
    assert f"v_total_lines > {PHYSICAL_QUOTE_LINE_LIMIT}" in mixed
    assert "pg_advisory_xact_lock" in mixed
    assert "FOR UPDATE" in mixed
    assert "DROP TABLE" not in migration.upper()
    assert "TRUNCATE" not in migration.upper()
    assert "DELETE FROM" not in migration.upper()


def test_catalog_migration_is_additive_and_enables_rls():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    for table in CATALOG_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"REVOKE ALL ON TABLE {table} FROM anon, authenticated" in sql
        assert f"REVOKE ALL ON TABLE {table} FROM service_role" in sql
        assert f"GRANT ALL ON TABLE {table} TO service_role" not in sql

    upper_sql = sql.upper()
    assert "DROP TABLE" not in upper_sql
    assert "TRUNCATE" not in upper_sql
    assert "DELETE FROM" not in upper_sql
    assert "supplier IN ('cr-global','sonara','sunon','alma','lumbro')" in sql
    assert "ALTER TABLE saas_supplier_catalog_snapshots" not in sql
    assert "saas_publish_catalog_snapshot" in sql
    assert "saas_reject_catalog_snapshot" in sql
    assert "saas_clone_catalog_candidate_with_asset" in sql


def test_catalog_migration_keeps_sources_private_and_assets_public():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    assert "'catalog-sources',\n    'catalog-sources',\n    FALSE,\n    67108864" in sql
    assert "'catalog-assets',\n    'catalog-assets',\n    TRUE,\n    8388608" in sql
    assert "bucket_id = 'catalog-sources'" in sql
    assert "bucket_id = 'catalog-assets'" in sql
    assert "TO service_role" in sql
    assert 'CREATE POLICY "catalog sources deny clients"' in sql
    assert "AS RESTRICTIVE FOR ALL TO anon, authenticated" in sql
    assert "bucket_id <> 'catalog-sources'" in sql


def test_catalog_versions_record_and_enforce_the_stale_base():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    publish = _function_sql(sql, "saas_publish_catalog_snapshot")
    clone = _function_sql(sql, "saas_clone_catalog_candidate_with_asset")

    assert "base_published_version_id UUID" in sql
    assert "REFERENCES saas_catalog_snapshot_versions(id) ON DELETE RESTRICT" in sql
    assert "NEW.base_published_version_id IS DISTINCT FROM OLD.base_published_version_id" in sql
    assert "NEW.base_published_version_id IS DISTINCT FROM v_published_version_id" in sql
    assert "v_candidate.base_published_version_id\n       IS DISTINCT FROM v_source.published_version_id" in publish
    assert "base_published_version_id, reviewed_by" in clone
    assert "v_candidate.base_published_version_id, p_reviewed_by" in clone


def test_catalog_rpcs_require_active_admins_without_email_authorization():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    for name in (
        "saas_publish_catalog_snapshot",
        "saas_reject_catalog_snapshot",
        "saas_clone_catalog_candidate_with_asset",
    ):
        function = _function_sql(sql, name)
        assert "p_reviewed_by IS NULL" in function
        assert "activo IS TRUE" in function
        assert "es_admin IS TRUE" in function
        assert "@mobiliti.mx" not in function

    clone = _function_sql(sql, "saas_clone_catalog_candidate_with_asset")
    assert "p_reviewed_by INTEGER" in clone
    assert "'approved_by', p_reviewed_by" in clone
    assert "'approved_at', v_approved_at" in clone
    assert "base_published_version_id, reviewed_by," in clone
    assert "review_note, reviewed_at" in clone


def test_catalog_clone_only_sets_image_metadata_on_an_existing_item():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    clone = _function_sql(sql, "saas_clone_catalog_candidate_with_asset")

    assert "COALESCE(array_length(p_json_path, 1), 0) <> 2" in clone
    assert "COALESCE(array_lower(p_json_path, 1), 0) <> 1" in clone
    assert "p_json_path[1] IS DISTINCT FROM 'items'" in clone
    assert "p_json_path[2] IS NULL" in clone
    assert "p_json_path[2] !~ '^(0|[1-9][0-9]*)$'" in clone
    assert "v_existing_item := v_candidate.payload #> p_json_path" in clone
    assert "jsonb_typeof(v_existing_item) <> 'object'" in clone
    assert "p_json_path || ARRAY['image']" not in clone
    assert "'{attributes,approved_asset}'" in clone
    assert "'{image_url}'" in clone
    assert "'{image_kind}'" in clone
    assert "'generated_reference'" in clone
    assert "v_new_payload IS NOT DISTINCT FROM v_candidate.payload" in clone
    assert "#>> (p_json_path || ARRAY['attributes','approved_asset','path'])" in clone
    assert "v_new_payload := jsonb_set(v_new_payload, '{source_hash}', to_jsonb(v_new_hash), TRUE)" in clone
    assert "v_new_id, v_candidate.supplier, v_new_hash, v_candidate.generated_at" in clone
    assert "v_new_hash, NOW(), 'candidate'" not in clone
    assert "base_published_version_id, reviewed_by" in clone


def test_catalog_reservations_preserve_audit_when_quote_job_is_deleted():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    reservations = _statement(sql, "CREATE TABLE IF NOT EXISTS saas_catalog_reservations")

    assert "quote_job_id UUID REFERENCES saas_quote_jobs(id) ON DELETE SET NULL" in reservations
    assert "quote_job_id UUID NOT NULL REFERENCES saas_quote_jobs(id) ON DELETE RESTRICT" not in reservations


def test_catalog_reservation_summary_is_atomic_and_service_role_only():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    summary = _function_sql(sql, "saas_catalog_reservation_summary")
    signature = "saas_catalog_reservation_summary(TEXT, INTEGER)"

    assert "RETURNS TABLE" in summary
    assert "SUM(reservations.quantity)::NUMERIC(18,6)" in summary
    assert "BOOL_OR(reservations.usuario_id <> p_usuario_id)" in summary
    assert "status = 'active'" in summary
    assert "GROUP BY reservations.internal_id" in summary
    assert "SECURITY DEFINER" in summary
    assert "SET search_path = public, pg_temp" in summary
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql
    assert "CREATE OR REPLACE FUNCTION saas_catalog_reservation_summary" in BOOTSTRAP.read_text("utf-8")


def test_catalog_reservation_insert_and_snapshot_are_one_locked_rpc():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    reserve = _function_sql(sql, "saas_reserve_catalog_items")
    signature = "saas_reserve_catalog_items(INTEGER, UUID, TEXT, JSONB)"

    assert "p_lines JSONB" in reserve
    assert "jsonb_array_length(p_lines) > 500" in reserve
    assert "FROM saas_quote_jobs" in reserve
    assert "job.status <> 'draft'" in reserve
    assert "FOR UPDATE" in reserve
    assert "pg_advisory_xact_lock" in reserve
    assert "ORDER BY line ->> 'internal_id'" in reserve
    assert "SUM(reservations.quantity)" in reserve
    assert "BOOL_OR(reservations.usuario_id <> p_usuario_id)" in reserve
    assert "INSERT INTO saas_catalog_reservations" in reserve
    assert "RETURN NEXT" in reserve
    assert "SECURITY DEFINER" in reserve
    assert "SET search_path = public, pg_temp" in reserve
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql
    assert "CREATE OR REPLACE FUNCTION saas_reserve_catalog_items" in BOOTSTRAP.read_text("utf-8")


def test_catalog_rpcs_preserve_payloads_and_lock_workflow_rows():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    assert sql.count("FOR UPDATE") >= 4
    assert "jsonb_set" in sql
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions" in sql
    assert "extensions.digest(" in sql
    assert "status = 'superseded'" in sql
    assert "published_version_id =" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql


def test_catalog_tables_use_explicit_least_privilege_grants():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    grants = {
        "saas_catalog_source_files": "SELECT",
        "saas_catalog_snapshot_versions": "SELECT",
        "saas_catalog_reservations": "SELECT, INSERT, UPDATE",
        "saas_exchange_rates": "SELECT",
    }

    for table, operations in grants.items():
        assert f"REVOKE ALL ON TABLE {table} FROM service_role" in sql
        assert f"GRANT {operations} ON TABLE {table} TO service_role" in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT TRUNCATE" not in sql

    clone_signature = "saas_clone_catalog_candidate_with_asset(UUID, INTEGER, TEXT, TEXT[])"
    assert f"REVOKE ALL ON FUNCTION {clone_signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {clone_signature} TO service_role" in sql


def test_catalog_source_files_persist_consistent_tombstones_with_narrow_writes():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    assert "is_deleted BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "deleted_at TIMESTAMPTZ" in sql
    assert "CONSTRAINT saas_catalog_sync_runs_id_source_unique UNIQUE (id, source_id)" in sql
    assert "deleted_sync_run_id UUID," in sql
    assert "CONSTRAINT saas_catalog_source_files_deleted_run_source_fkey" in sql
    assert "FOREIGN KEY (deleted_sync_run_id, source_id)" in sql
    assert "REFERENCES saas_catalog_sync_runs(id, source_id) ON DELETE RESTRICT" in sql
    assert "NOT is_deleted AND deleted_at IS NULL AND deleted_sync_run_id IS NULL" in sql
    assert "is_deleted AND deleted_at IS NOT NULL AND deleted_sync_run_id IS NOT NULL" in sql
    assert "idx_catalog_source_files_active_source_path" not in sql
    assert "UNIQUE (source_id, drive_item_id, e_tag)" not in sql
    drop_old = (
        "ALTER TABLE saas_catalog_source_files\n"
        "    DROP CONSTRAINT IF EXISTS "
        "saas_catalog_source_files_source_id_drive_item_id_e_tag_key;"
    )
    assert drop_old in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_source_files_active_identity" in sql
    assert "ON saas_catalog_source_files(source_id, drive_item_id, e_tag)" in sql
    assert "WHERE is_deleted IS FALSE" in sql
    assert sql.index(drop_old) < sql.index("CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_source_files_active_identity")
    assert "CREATE INDEX IF NOT EXISTS idx_catalog_source_files_latest" in sql
    assert "ON saas_catalog_source_files(source_id, drive_item_id, discovered_at DESC, id DESC)" in sql
    assert "Task 6 must use only the latest observation per source and drive item" in sql

    assert "GRANT SELECT, INSERT, UPDATE ON TABLE saas_catalog_source_files" not in sql
    insert = _statement(sql, "GRANT INSERT (source_id, drive_item_id")
    update = _statement(sql, "GRANT UPDATE (validation_status")
    for column in ("sha256", "e_tag", "private_object_path"):
        assert column in insert
    for column in (
        "validation_status",
        "validation_summary",
        "last_sync_run_id",
        "validated_at",
    ):
        assert column in update
    for rpc_only in ("is_deleted", "deleted_at", "deleted_sync_run_id"):
        assert rpc_only not in insert
        assert rpc_only not in update
    for immutable in ("sha256", "e_tag", "private_object_path", "source_id", "drive_item_id"):
        assert immutable not in update


def test_catalog_candidate_hash_uniqueness_is_scoped_to_immutable_base():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    assert "UNIQUE (supplier, source_hash)" not in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_snapshot_supplier_hash_base" in sql
    assert "supplier, source_hash, COALESCE(base_published_version_id, '00000000-0000-0000-0000-000000000000'::UUID)" in sql


def test_stage_catalog_candidate_is_atomic_locked_and_service_only():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    stage = _function_sql(sql, "saas_stage_catalog_candidate")

    assert "p_run_id UUID" in stage
    assert "p_source_hash TEXT" in stage
    assert "p_generated_at TIMESTAMPTZ" in stage
    assert "p_payload JSONB" in stage
    assert "p_metrics JSONB" in stage
    assert "p_delta_link TEXT" in stage
    assert "RETURNS UUID" in stage
    assert "SECURITY DEFINER" in stage
    assert "SET search_path = public, pg_temp" in stage
    assert "FROM saas_catalog_sync_runs" in stage and "FOR UPDATE" in stage
    assert "FROM saas_catalog_sources" in stage and "FOR UPDATE" in stage
    assert "v_run.status <> 'running'" in stage
    assert "v_run.candidate_version_id IS NOT NULL" in stage
    assert "p_source_hash !~ '^[0-9A-Fa-f]{64}$'" in stage
    assert "jsonb_typeof(p_payload -> 'items') IS DISTINCT FROM 'array'" in stage
    assert "jsonb_typeof(p_payload -> 'source_hash') IS DISTINCT FROM 'string'" in stage
    assert "p_payload ->> 'source_hash' !~ '^[0-9A-Fa-f]{64}$'" in stage
    assert "LOWER(p_payload ->> 'source_hash') IS DISTINCT FROM LOWER(p_source_hash)" in stage
    assert "jsonb_typeof(p_payload -> 'generated_at') IS DISTINCT FROM 'string'" in stage
    assert "v_payload_generated_at := (p_payload ->> 'generated_at')::TIMESTAMPTZ" in stage
    assert "v_payload_generated_at IS DISTINCT FROM p_generated_at" in stage
    assert "v_canonical_hash := LOWER(p_source_hash)" in stage
    assert "v_canonical_payload := jsonb_set(" in stage
    assert "p_payload, '{source_hash}', to_jsonb(v_canonical_hash), FALSE" in stage
    assert "p_payload ->> 'supplier' IS DISTINCT FROM v_source.supplier" in stage
    assert "NULLIF(BTRIM(p_delta_link), '') IS NULL" in stage
    assert "LENGTH(p_delta_link) > 8192" in stage
    assert "INSERT INTO saas_catalog_snapshot_versions" in stage
    assert "v_canonical_hash, p_generated_at" in stage
    assert "'candidate', v_canonical_payload" in stage
    assert "v_source.published_version_id" in stage
    assert "SET status = 'awaiting_approval'" in stage
    assert "candidate_version_id = v_candidate_id" in stage
    assert "UPDATE saas_catalog_sources" in stage
    assert "SET delta_link = p_delta_link" in stage
    signature = "saas_stage_catalog_candidate(UUID, TEXT, TIMESTAMPTZ, JSONB, JSONB, TEXT)"
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_stage_catalog_candidate_replay_is_idempotent_only_for_identical_commit():
    stage = _function_sql(CATALOG_MIGRATION.read_text("utf-8"), "saas_stage_catalog_candidate")
    assert "v_run.status = 'awaiting_approval'" in stage
    assert "v_run.candidate_version_id IS NOT NULL" in stage
    assert "FROM saas_catalog_snapshot_versions" in stage
    assert "v_candidate.sync_run_id IS DISTINCT FROM p_run_id" in stage
    assert "v_candidate.source_hash IS DISTINCT FROM v_canonical_hash" in stage
    assert "v_candidate.payload IS DISTINCT FROM v_canonical_payload" in stage
    assert "v_candidate.base_published_version_id IS DISTINCT FROM v_source.published_version_id" in stage
    assert "v_run.metrics IS DISTINCT FROM p_metrics" in stage
    assert "v_source.delta_link IS DISTINCT FROM p_delta_link" in stage
    assert "RETURN v_candidate.id" in stage
    assert "Catalog candidate replay conflict" in stage


def test_stage_catalog_candidate_requires_finite_iso_generated_at_and_stores_utc():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    stage = _function_sql(sql, "saas_stage_catalog_candidate")
    generated_at_pattern = (
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"
    )

    assert f"p_payload ->> 'generated_at' !~ '{generated_at_pattern}'" in stage
    for special in ("now", "today", "epoch", "infinity", "-infinity"):
        assert re.fullmatch(generated_at_pattern, special) is None
    for task3_value in (
        "2026-07-16T14:30:00Z",
        "2026-07-16T14:30:00.123456+00:00",
        "2026-07-16T08:30:00-06:00",
    ):
        assert re.fullmatch(generated_at_pattern, task3_value) is not None

    assert "OR NOT isfinite(p_generated_at)" in stage
    assert "v_payload_generated_at IS DISTINCT FROM p_generated_at" in stage
    assert "v_canonical_generated_at := to_char(" in stage
    assert "p_generated_at AT TIME ZONE 'UTC'" in stage
    assert "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'" in stage
    assert "v_canonical_payload := jsonb_set(" in stage
    assert "v_canonical_payload, '{generated_at}', to_jsonb(v_canonical_generated_at), FALSE" in stage


def test_catalog_allows_only_one_requested_or_running_run_per_source():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_sync_runs_active_source" in sql
    assert "ON saas_catalog_sync_runs(source_id)" in sql
    assert "WHERE status IN ('requested','running')" in sql


def test_start_catalog_sync_is_atomic_idempotent_and_service_only():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    start = _function_sql(sql, "saas_start_catalog_sync")
    transition = _function_sql(sql, "saas_enforce_catalog_sync_run_transition")
    assert "request_key UUID UNIQUE" not in sql
    assert "ALTER TABLE saas_catalog_sync_runs\n    ADD COLUMN IF NOT EXISTS request_key UUID;" in sql
    assert "DROP CONSTRAINT IF EXISTS saas_catalog_sync_runs_request_key_key;" in sql
    request_key_index = _statement(sql, "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_sync_runs_request_key")
    assert "ON saas_catalog_sync_runs(request_key)" in request_key_index
    assert "WHERE request_key IS NOT NULL" in request_key_index
    assert "p_source_id UUID" in start
    assert "p_trigger_type TEXT" in start
    assert "p_requested_by INTEGER" in start
    assert "p_request_key UUID" in start
    assert "RETURNS UUID" in start and "SECURITY DEFINER" in start
    assert "FROM saas_catalog_sources" in start and "FOR UPDATE" in start
    assert "request_key = p_request_key" in start
    assert "v_existing.status <> 'running'" in start
    assert "RETURN v_existing.id" in start
    assert "status IN ('requested','running')" in start
    assert "RETURN NULL" in start
    assert "'running'" in start and "p_request_key" in start
    assert "NEW.status = 'running'" in transition
    assert "NEW.request_key IS NOT NULL" in transition
    assert "NEW.started_at IS NOT NULL" in transition
    insert_grant = _statement(sql, "GRANT INSERT (source_id, trigger_type")
    assert "request_key" not in insert_grant and "started_at" not in insert_grant
    signature = "saas_start_catalog_sync(UUID, TEXT, INTEGER, UUID)"
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql
    active_index = _statement(sql, "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_sync_runs_active_source")
    assert "awaiting_approval" not in active_index


def test_finish_catalog_sync_no_changes_is_atomic_and_service_only():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    finish = _function_sql(sql, "saas_finish_catalog_sync_no_changes")

    assert "p_run_id UUID" in finish
    assert "p_metrics JSONB" in finish
    assert "p_delta_link TEXT" in finish
    assert "RETURNS UUID" in finish
    assert "SECURITY DEFINER" in finish
    assert "SET search_path = public, pg_temp" in finish
    assert "jsonb_typeof(p_metrics) <> 'object'" in finish
    assert "pg_column_size(p_metrics) > 1048576" in finish
    assert "NULLIF(BTRIM(p_delta_link), '') IS NULL" in finish
    assert "LENGTH(p_delta_link) > 8192" in finish
    assert "FROM saas_catalog_sync_runs" in finish
    assert "v_run.status <> 'running'" in finish
    assert "v_run.candidate_version_id IS NOT NULL" in finish
    assert "FROM saas_catalog_sources" in finish
    assert finish.count("FOR UPDATE") >= 2
    assert "SET delta_link = p_delta_link" in finish
    assert "SET status = 'no_changes'" in finish
    assert "metrics = p_metrics" in finish
    assert "finished_at = NOW()" in finish
    trigger = _function_sql(sql, "saas_enforce_catalog_sync_run_transition")
    assert "NEW.status IN ('no_changes','published','rejected')" in trigger
    assert "current_user = 'service_role'" in trigger
    signature = "saas_finish_catalog_sync_no_changes(UUID, JSONB, TEXT)"
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_claim_next_catalog_sync_is_atomic_due_and_manual_first():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    claim = _function_sql(sql, "saas_claim_next_catalog_sync")
    signature = "saas_claim_next_catalog_sync(TEXT[])"

    assert "DEFAULT INTERVAL '6 hours'" in sql
    assert "ALTER COLUMN sync_interval SET DEFAULT INTERVAL '6 hours'" in sql
    assert "RETURNS TABLE" in claim
    assert "p_enabled_suppliers TEXT[]" in claim
    assert "r.status = 'requested'" in claim
    assert "r.trigger_type = 'manual'" in claim
    assert claim.index("r.trigger_type = 'manual'") < claim.index("v_trigger_type := 'scheduled'")
    assert claim.count("SKIP LOCKED") >= 2
    assert "recent.requested_at > NOW() - s.sync_interval" in claim
    assert "active.status IN ('requested','running')" in claim
    assert "status = 'running', started_at = NOW()" in claim
    assert "gen_random_uuid()" in claim
    assert "SECURITY DEFINER" in claim
    assert "SET search_path = public, pg_temp" in claim
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_recover_stale_catalog_sync_runs_is_atomic_bounded_and_service_only():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    recover = _function_sql(sql, "saas_recover_stale_catalog_sync_runs")
    signature = "saas_recover_stale_catalog_sync_runs(TEXT[])"

    assert "p_enabled_suppliers TEXT[]" in recover
    assert "INTERVAL '45 minutes'" in recover
    assert "r.status = 'running'" in recover
    assert "s.enabled" in recover
    assert "s.supplier = ANY(p_enabled_suppliers)" in recover
    assert "r.started_at < NOW() - INTERVAL '45 minutes'" in recover
    assert "status = 'failed'" in recover
    assert "error_summary = 'lease_expired'" in recover
    assert "finished_at = NOW()" in recover
    assert "updated_at = NOW()" in recover
    assert "GET DIAGNOSTICS" in recover
    assert "SECURITY DEFINER" in recover
    assert "SET search_path = public, pg_temp" in recover
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_mark_catalog_source_file_deleted_locks_latest_observation():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    mark_deleted = _function_sql(sql, "saas_mark_catalog_source_file_deleted")

    assert "p_source_id UUID" in mark_deleted
    assert "p_drive_item_id TEXT" in mark_deleted
    assert "p_run_id UUID" in mark_deleted
    assert "RETURNS UUID" in mark_deleted
    assert "SECURITY DEFINER" in mark_deleted
    assert "SET search_path = public, pg_temp" in mark_deleted
    assert "FROM saas_catalog_sync_runs" in mark_deleted
    assert "v_run.status <> 'running'" in mark_deleted
    assert "v_run.source_id IS DISTINCT FROM p_source_id" in mark_deleted
    assert "FROM saas_catalog_sources" in mark_deleted
    assert "FROM saas_catalog_source_files" in mark_deleted
    assert "source_id = p_source_id" in mark_deleted
    assert "drive_item_id = p_drive_item_id" in mark_deleted
    assert "ORDER BY discovered_at DESC, id DESC" in mark_deleted
    assert "LIMIT 1" in mark_deleted
    assert mark_deleted.count("FOR UPDATE") >= 3
    assert "v_file.is_deleted" in mark_deleted
    assert "SET is_deleted = TRUE" in mark_deleted
    assert "deleted_at = NOW()" in mark_deleted
    assert "deleted_sync_run_id = v_run.id" in mark_deleted
    assert "last_sync_run_id = v_run.id" in mark_deleted
    signature = "saas_mark_catalog_source_file_deleted(UUID, TEXT, UUID)"
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_mark_catalog_source_file_deleted_replay_preserves_provenance():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    mark_deleted = _function_sql(sql, "saas_mark_catalog_source_file_deleted")

    missing_guard = "IF NOT FOUND THEN\n        RAISE EXCEPTION 'latest catalog source file is missing';"
    replay_guard = "IF v_file.is_deleted THEN\n        RETURN v_file.id;\n    END IF;"
    update = "UPDATE saas_catalog_source_files\n    SET is_deleted = TRUE"

    assert missing_guard in mark_deleted
    assert replay_guard in mark_deleted
    assert mark_deleted.index("ORDER BY discovered_at DESC, id DESC") < mark_deleted.index(replay_guard)
    assert mark_deleted.index(replay_guard) < mark_deleted.index(update)
    assert "latest catalog source file is missing or already deleted" not in mark_deleted


def test_auto_publish_verifies_only_stock_and_lead_time_changed():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    auto_publish = _function_sql(sql, "saas_auto_publish_catalog_snapshot")

    assert "p_candidate_id UUID" in auto_publish
    assert "RETURNS UUID" in auto_publish
    assert "SECURITY DEFINER" in auto_publish
    assert "SET search_path = public, pg_temp" in auto_publish
    assert "v_candidate.base_published_version_id IS NULL" in auto_publish
    assert "v_candidate.base_published_version_id IS DISTINCT FROM v_source.published_version_id" in auto_publish
    assert "status = 'awaiting_approval'" in auto_publish
    assert auto_publish.count("FOR UPDATE") >= 4
    assert "jsonb_typeof(v_candidate.payload -> 'items') <> 'array'" in auto_publish
    assert "jsonb_typeof(v_base.payload -> 'items') <> 'array'" in auto_publish
    assert "item - 'stock' - 'lead_time'" in auto_publish
    assert "COUNT(DISTINCT item ->> 'internal_id')" in auto_publish
    assert "v_candidate_ids IS DISTINCT FROM v_base_ids" in auto_publish
    assert "v_candidate_items IS DISTINCT FROM v_base_items" in auto_publish
    assert "v_candidate.payload - 'items' - 'source_hash' - 'generated_at'" in auto_publish
    assert "v_base.payload - 'items' - 'source_hash' - 'generated_at'" in auto_publish
    assert "v_candidate_top_level IS DISTINCT FROM v_base_top_level" in auto_publish
    assert "jsonb_build_object('stock', item -> 'stock', 'lead_time', item -> 'lead_time')" in auto_publish
    assert "v_candidate_mutable_items IS NOT DISTINCT FROM v_base_mutable_items" in auto_publish
    assert "automatic publication requires a stock or lead_time change" in auto_publish
    assert "reviewed_by = NULL" in auto_publish
    assert "review_note = 'system:auto-published stock/lead_time-only change'" in auto_publish
    signature = "saas_auto_publish_catalog_snapshot(UUID)"
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_exchange_rate_batch_validates_exactly_and_never_overwrites():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    rates = _function_sql(sql, "saas_insert_exchange_rates_if_absent")

    assert "p_rates JSONB" in rates
    assert "RETURNS INTEGER" in rates
    assert "SECURITY DEFINER" in rates
    assert "SET search_path = public, pg_temp" in rates
    assert "jsonb_typeof(p_rates) <> 'array'" in rates
    assert "jsonb_array_length(p_rates) = 0" in rates
    assert "jsonb_array_length(p_rates) > 1000" in rates
    for field in ("currency", "effective_date", "mxn_per_unit", "series_id", "source", "retrieved_at", "raw_hash"):
        assert f"'{field}'" in rates
    assert "jsonb_object_keys(v_rate)" in rates
    assert "jsonb_typeof(v_rate -> 'mxn_per_unit') <> 'string'" in rates
    assert "jsonb_typeof(v_rate -> 'mxn_per_unit') <> 'number'" not in rates
    assert "v_rate ->> 'mxn_per_unit' !~ '^(0|[1-9][0-9]{0,11})\\.[0-9]{6}$'" in rates
    assert "::NUMERIC(18,6)" in rates
    assert "WHEN 'USD' THEN 'SF43718'" in rates
    assert "WHEN 'EUR' THEN 'SF46410'" in rates
    assert "v_rate ->> 'source' <> 'BANXICO_SIE'" in rates
    assert "v_rate ->> 'raw_hash' !~ '^[0-9a-f]{64}$'" in rates
    assert "GROUP BY rate ->> 'currency', rate ->> 'effective_date'" in rates
    assert "HAVING COUNT(*) > 1" in rates
    assert "pg_advisory_xact_lock" in rates
    assert "'saas_exchange_rate:' || (v_rate ->> 'currency') || ':' || (v_rate ->> 'effective_date')" in rates
    assert "ORDER BY value ->> 'currency', value ->> 'effective_date'" in rates
    assert "FROM saas_exchange_rates" in rates and "FOR UPDATE" in rates
    assert "IS DISTINCT FROM" in rates
    assert "INSERT INTO saas_exchange_rates" in rates
    assert "UPDATE saas_exchange_rates" not in rates
    assert "ON CONFLICT" not in rates
    signature = "saas_insert_exchange_rates_if_absent(JSONB)"
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in sql
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in sql


def test_catalog_sources_hide_the_published_pointer_from_direct_writes():
    sql = CATALOG_MIGRATION.read_text("utf-8")

    assert "GRANT SELECT ON TABLE saas_catalog_sources TO service_role" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE saas_catalog_sources" not in sql
    assert "GRANT INSERT ON TABLE saas_catalog_sources" not in sql
    assert "GRANT UPDATE ON TABLE saas_catalog_sources" not in sql

    insert = _statement(sql, "GRANT INSERT (supplier")
    update = _statement(sql, "GRANT UPDATE (label")
    assert "published_version_id" not in insert
    assert "published_version_id" not in update
    for column in (
        "supplier",
        "label",
        "adapter",
        "graph_drive_id",
        "graph_root_item_id",
        "delta_link",
        "sync_interval",
        "enabled",
    ):
        assert column in insert
    for column in (
        "label",
        "adapter",
        "graph_drive_id",
        "graph_root_item_id",
        "sync_interval",
        "enabled",
        "updated_at",
    ):
        assert column in update
    assert "delta_link" not in update


def test_sync_runs_enforce_safe_direct_worker_transitions():
    sql = CATALOG_MIGRATION.read_text("utf-8")
    trigger = _function_sql(sql, "saas_enforce_catalog_sync_run_transition")

    assert "RETURNS TRIGGER" in trigger
    assert "SECURITY DEFINER" not in trigger
    assert "TG_OP = 'INSERT'" in trigger
    assert "NEW.status = 'requested'" in trigger
    assert "NEW.status = 'running'" in trigger
    assert "NEW.reviewed_by IS NOT NULL" in trigger
    assert "NEW.candidate_version_id IS NOT NULL" in trigger
    assert "NEW.id IS DISTINCT FROM OLD.id" in trigger
    assert "NEW.source_id IS DISTINCT FROM OLD.source_id" in trigger
    assert "NEW.trigger_type IS DISTINCT FROM OLD.trigger_type" in trigger
    assert "NEW.request_key IS DISTINCT FROM OLD.request_key" in trigger
    assert "NEW.requested_by IS DISTINCT FROM OLD.requested_by" in trigger
    assert "NEW.requested_at IS DISTINCT FROM OLD.requested_at" in trigger
    assert "OLD.status IN ('no_changes','published','rejected','failed')" in trigger
    assert "OLD.status = 'requested' AND NEW.status IN ('running','failed')" in trigger
    assert "OLD.status = 'running' AND NEW.status IN ('no_changes','awaiting_approval','failed')" in trigger
    assert "OLD.status = 'awaiting_approval' AND NEW.status IN ('published','rejected','failed')" in trigger
    assert "NEW.status IN ('no_changes','published','rejected')" in trigger
    assert "current_user = 'service_role'" in trigger
    assert "BEFORE INSERT OR UPDATE ON saas_catalog_sync_runs" in sql

    assert "GRANT SELECT ON TABLE saas_catalog_sync_runs TO service_role" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE saas_catalog_sync_runs" not in sql
    insert = _statement(sql, "GRANT INSERT (source_id, trigger_type")
    update = _statement(sql, "GRANT UPDATE (status, candidate_version_id")
    for forbidden in ("status", "candidate_version_id", "reviewed_by", "reviewed_at", "finished_at"):
        assert forbidden not in insert
    for forbidden in ("source_id", "trigger_type", "requested_by", "requested_at", "reviewed_by", "reviewed_at"):
        assert forbidden not in update


def test_jobs_rls_is_guarded_idempotent_and_non_destructive():
    sql = JOBS_RLS_MIGRATION.read_text("utf-8")
    upper_sql = sql.upper()

    assert "to_regclass('public.jobs')" in sql
    assert "to_regclass('public.saas_quote_jobs')" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE public.jobs FROM anon, authenticated" in sql
    assert "REVOKE ALL ON TABLE public.saas_quote_jobs FROM anon, authenticated" in sql
    assert "GRANT SELECT, UPDATE ON TABLE public.saas_quote_jobs TO anon" in sql
    assert "GRANT ALL ON TABLE public.jobs TO service_role" in sql
    assert "GRANT ALL ON TABLE public.saas_quote_jobs TO service_role" in sql
    for destructive in ("DROP ", "TRUNCATE", "DELETE FROM", "\nUPDATE "):
        assert destructive not in upper_sql


def test_bootstrap_builds_historic_catalog_schema_with_final_jome_lauco_state():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    jobs_migration = JOBS_RLS_MIGRATION.read_text(encoding="utf-8")

    assert bootstrap.count(jobs_migration) == 1
    legacy_snapshots = _statement(
        bootstrap,
        "CREATE TABLE IF NOT EXISTS saas_supplier_catalog_snapshots",
    )
    assert "supplier TEXT PRIMARY KEY CHECK (supplier IN ('tarkett'))" in legacy_snapshots
    assert "CREATE TABLE IF NOT EXISTS saas_catalog_sources" in bootstrap
    assert "CREATE OR REPLACE FUNCTION saas_reserve_mixed_cart" in bootstrap
    assert "'cr-global','sonara','sunon','alma','lumbro','jome','lauco'" in bootstrap
