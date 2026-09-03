import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest


SETUP = Path("mobiliti_saas/supabase_setup")
CATALOG_MIGRATION = SETUP / "2026_07_multi_supplier_catalogs.sql"
JOME_LAUCO_MIGRATION = SETUP / "2026_07_jome_lauco_catalogs.sql"
IDELIKA_CONCEPTOS_MIGRATION = SETUP / "2026_08_idelika_conceptos_catalogs.sql"
OFFIHO_SNAPSHOT_MIGRATION = SETUP / "2026_08_offiho_stock_snapshot.sql"
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
ASSET_REGISTRY_MIGRATION = SETUP / "2026_09_catalog_asset_registry_r2.sql"
ASSET_REGISTRY_CUTOVER = SETUP / "2026_09_catalog_asset_registry_r2_cutover.sql"
PHYSICAL_QUOTE_LINE_LIMIT = 1_048_512
SQL_FILES = (BOOTSTRAP,)
EXPECTED_SUPPLIERS = (
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
    "idelika", "conceptos",
    "labenze", "requiez",
)
MIXED_CATALOG_COUNT = 13
MIXED_CATALOGS = ("tarkett", "offiho", *EXPECTED_SUPPLIERS)
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


def _unqualified_saas_relations(function):
    dml_pattern = re.compile(
        r"(?is)\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+"
        r"(?!public\.)(saas_[a-z0-9_]+)\b"
    )
    rowtype_pattern = re.compile(r"(?i)(?<!public\.)\bsaas_[a-z0-9_]+%ROWTYPE\b")
    return [*dml_pattern.findall(function), *rowtype_pattern.findall(function)]


def test_catalog_asset_registry_is_private_idempotent_and_service_role_only():
    sql = ASSET_REGISTRY_MIGRATION.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    for document in (sql, bootstrap):
        assert "CREATE TABLE IF NOT EXISTS saas_catalog_assets" in document
        assert "object_name TEXT PRIMARY KEY" in document
        assert "storage_provider TEXT NOT NULL CHECK (storage_provider IN ('supabase','r2'))" in document
        assert "physical_bucket TEXT NOT NULL" in document
        assert "sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$')" in document
        assert "byte_size BIGINT NOT NULL CHECK (byte_size > 0)" in document
        assert "mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp'))" in document
        assert "object_name ~ '^[0-9a-f]{64}[.](png|jpg|jpeg|webp)$'" in document
        assert "split_part(object_name, '.', 1) = sha256" in document
        assert "ENABLE ROW LEVEL SECURITY" in document
        assert "REVOKE ALL ON TABLE saas_catalog_assets FROM PUBLIC, anon, authenticated" in document
        assert "REVOKE ALL ON TABLE saas_catalog_assets FROM service_role" in document
        assert "GRANT SELECT ON TABLE saas_catalog_assets TO service_role" in document
        for operation in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert f"GRANT {operation} ON TABLE saas_catalog_assets TO service_role" not in document
        assert "saas_register_catalog_asset" in document
        assert "SECURITY DEFINER" in _function_sql(document, "saas_register_catalog_asset")
        assert "SET search_path = public, pg_temp" in _function_sql(document, "saas_register_catalog_asset")
        assert "current_user <> 'service_role'" not in _function_sql(document, "saas_register_catalog_asset")
        assert "Catalog asset registry conflict" in _function_sql(document, "saas_register_catalog_asset")
        signature = "saas_register_catalog_asset(TEXT, TEXT, TEXT, BIGINT, TEXT)"
        assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated" in document
        assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role" in document


def test_catalog_asset_registry_allows_verified_cross_provider_copy_without_repointing_row():
    """Rompe si el registro vuelve a tratar provider como metadata de identidad."""
    for document in (
        ASSET_REGISTRY_MIGRATION.read_text(encoding="utf-8"),
        BOOTSTRAP.read_text(encoding="utf-8"),
    ):
        function = _function_sql(document, "saas_register_catalog_asset")
        conflict_guard = function[function.index("IF v_existing.") : function.index("RETURN p_object_name;")]
        assert "v_existing.storage_provider IS DISTINCT FROM p_storage_provider" not in conflict_guard
        for required in (
            "v_existing.physical_bucket IS DISTINCT FROM p_physical_bucket",
            "v_existing.sha256 IS DISTINCT FROM v_sha256",
            "v_existing.byte_size IS DISTINCT FROM p_byte_size",
            "v_existing.mime_type IS DISTINCT FROM p_mime_type",
            "v_existing.verified_at IS NULL",
        ):
            assert required in conflict_guard


def test_catalog_asset_cutover_is_guarded_before_replacing_clone_rpcs():
    sql = ASSET_REGISTRY_CUTOVER.read_text(encoding="utf-8")
    guard = "catalog asset R2 cutover manifest is not verified"
    assert guard in sql
    assert sql.index(guard) < sql.index("CREATE OR REPLACE FUNCTION saas_clone_catalog_candidate_with_asset")
    assert "expected_count = 2214" in sql
    assert "verified_count = 2214" in sql
    assert "missing_count = 0" in sql and "failed_count = 0" in sql
    assert "manifest_digest" in sql and "keyset_digest" in sql
    for name in (
        "saas_clone_catalog_candidate_with_asset",
        "saas_clone_catalog_candidate_with_image_metadata",
    ):
        function = _function_sql(sql, name)
        assert "FROM public.saas_catalog_assets" in function
        assert "storage.objects" not in function
        assert "storage_provider = 'r2'" not in function
        assert "physical_bucket = 'catalog-assets'" in function
        assert "verified_at IS NOT NULL" in function
        assert "SECURITY DEFINER" in function
        assert "SET search_path = public, pg_temp" in function


def test_catalog_asset_cutover_is_pinned_to_the_certified_initial_batch():
    sql = ASSET_REGISTRY_CUTOVER.read_text(encoding="utf-8")
    guard = sql[: sql.index("CREATE OR REPLACE FUNCTION saas_clone_catalog_candidate_with_asset")]
    assert "batch_id = '470442fc-3dc3-5948-b0e4-1dd34c1fcd30'::UUID" in guard
    assert "keyset_digest = '93e30738942bc0c4b85d85d63239c82588ec1d163c5c3820ef2de01dc07caeb7'" in guard
    assert "manifest_digest = '72ecc6b84bfec9ba012a24dea9c5bcdf6d1beaad8d81c68eb4697f8e83e188ff'" in guard
    assert "ORDER BY verified_at" not in guard
    assert "LIMIT 1" not in guard


def test_bootstrap_and_forward_sql_keep_real_registry_and_clone_definitions_in_parity():
    registry = ASSET_REGISTRY_MIGRATION.read_text(encoding="utf-8")
    cutover = ASSET_REGISTRY_CUTOVER.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    assert _normalized_sql(_function_definition(registry, "saas_register_catalog_asset")) == _normalized_sql(
        _function_definition(bootstrap, "saas_register_catalog_asset")
    )
    for name in (
        "saas_clone_catalog_candidate_with_asset",
        "saas_clone_catalog_candidate_with_image_metadata",
    ):
        assert _normalized_sql(_function_definition(cutover, name)) == _normalized_sql(
            _function_definition(bootstrap, name)
        )


def test_catalog_asset_cutover_manifest_is_private_and_independently_verified():
    sql = ASSET_REGISTRY_MIGRATION.read_text(encoding="utf-8")
    cutover = ASSET_REGISTRY_CUTOVER.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    for table in (
        "saas_catalog_asset_cutover_batches",
        "saas_catalog_assets",
        "saas_catalog_asset_cutover_entries",
    ):
        assert _normalized_sql(_statement(sql, f"CREATE TABLE IF NOT EXISTS {table}")) == _normalized_sql(
            _statement(bootstrap, f"CREATE TABLE IF NOT EXISTS {table}")
        )
    for document in (sql, bootstrap):
        assert "CREATE TABLE IF NOT EXISTS saas_catalog_asset_cutover_entries" in document
        assert "PRIMARY KEY (batch_id, object_name)" in document
        assert "status IN ('pending','loading','verified','failed')" in document
        assert "REVOKE ALL ON TABLE saas_catalog_asset_cutover_entries FROM PUBLIC, anon, authenticated" in document
        for table in ("saas_catalog_asset_cutover_batches", "saas_catalog_asset_cutover_entries"):
            assert f"REVOKE ALL ON TABLE {table} FROM service_role" in document
            assert f"GRANT SELECT ON TABLE {table} TO service_role" in document
            service_grants = re.findall(
                rf"(?is)GRANT\s+([^;]+?)\s+ON\s+TABLE\s+{table}\s+TO\s+service_role\s*;",
                document,
            )
            assert service_grants
            assert all(
                re.search(r"\b(?:INSERT|UPDATE|DELETE|TRUNCATE|ALL)\b", grant, re.IGNORECASE) is None
                for grant in service_grants
            )
        for name in ("saas_start_catalog_asset_cutover_batch", "saas_add_catalog_asset_cutover_entry", "saas_finalize_catalog_asset_cutover_batch"):
            function = _function_sql(document, name)
            assert "SECURITY DEFINER" in function
            assert "public.saas_catalog_asset_cutover" in function
            assert " IS NULL" in function
        assert "ON CONFLICT DO NOTHING" in _function_sql(document, "saas_start_catalog_asset_cutover_batch")
        assert "ON CONFLICT DO NOTHING" in _function_sql(document, "saas_add_catalog_asset_cutover_entry")
        starter = _function_sql(document, "saas_start_catalog_asset_cutover_batch")
        assert "v_batch.manifest_digest IS DISTINCT FROM p_manifest_digest" in starter
        assert "v_batch.keyset_digest IS DISTINCT FROM p_keyset_digest" in starter
        assert "v_batch.expected_count IS DISTINCT FROM p_expected_count" in starter
        adder = _function_sql(document, "saas_add_catalog_asset_cutover_entry")
        assert "status='loading'" in re.sub(r"\s+", "", adder)
        assert "catalog asset cutover entry conflict" in adder
    finalizer = _function_sql(sql, "saas_finalize_catalog_asset_cutover_batch")
    assert "public.saas_catalog_asset_cutover_entries" in finalizer
    assert "public.saas_catalog_assets" in finalizer
    assert "expected_count <> 2214" in finalizer
    assert "extensions.digest" in finalizer
    assert "v_keyset IS DISTINCT FROM v_batch.keyset_digest" in finalizer
    assert "v_manifest IS DISTINCT FROM v_batch.manifest_digest" in finalizer
    assert "v_matches <> 2214" in finalizer
    assert "status='verified'" in re.sub(r"\s+", "", finalizer)
    assert "ON CONFLICT DO NOTHING" in _function_sql(sql, "saas_register_catalog_asset")
    assert "public.saas_catalog_assets%ROWTYPE" in _function_sql(sql, "saas_register_catalog_asset")
    assert "public.saas_catalog_asset_cutover_entries" in cutover
    assert "JOIN public.saas_catalog_assets" in cutover
    assert "cutover_batch_id = v_batch.batch_id" in cutover

    for name in (
        "saas_register_catalog_asset",
        "saas_start_catalog_asset_cutover_batch",
        "saas_add_catalog_asset_cutover_entry",
        "saas_finalize_catalog_asset_cutover_batch",
    ):
        assert _normalized_sql(_function_definition(sql, name)) == _normalized_sql(
            _function_definition(bootstrap, name)
        )

    for name in (
        "saas_clone_catalog_candidate_with_asset",
        "saas_clone_catalog_candidate_with_image_metadata",
    ):
        assert _normalized_sql(_function_definition(cutover, name)) == _normalized_sql(
            _function_definition(bootstrap, name)
        )


def test_catalog_asset_security_definers_qualify_registry_and_clone_relations():
    names = (
        "saas_register_catalog_asset",
        "saas_start_catalog_asset_cutover_batch",
        "saas_add_catalog_asset_cutover_entry",
        "saas_finalize_catalog_asset_cutover_batch",
        "saas_clone_catalog_candidate_with_asset",
        "saas_clone_catalog_candidate_with_image_metadata",
    )
    for document in (ASSET_REGISTRY_MIGRATION.read_text("utf-8"), ASSET_REGISTRY_CUTOVER.read_text("utf-8"), BOOTSTRAP.read_text("utf-8")):
        for name in names:
            if f"CREATE OR REPLACE FUNCTION {name}" not in document:
                continue
            function = _function_sql(document, name)
            assert "SECURITY DEFINER" in function and "SET search_path = public, pg_temp" in function
            assert _unqualified_saas_relations(function) == []
            assert re.search(r"(?<!extensions\.)\bgen_random_uuid\s*\(", function) is None
            assert re.search(r"(?<!extensions\.)\bdigest\s*\(", function) is None


def test_catalog_asset_rpcs_reject_null_inputs_explicitly():
    registry = ASSET_REGISTRY_MIGRATION.read_text("utf-8")
    cutover = ASSET_REGISTRY_CUTOVER.read_text("utf-8")
    bootstrap = BOOTSTRAP.read_text("utf-8")
    expected_null_guards = {
        "saas_register_catalog_asset": (
            "p_object_name IS NULL", "p_storage_provider IS NULL",
            "p_physical_bucket IS NULL", "p_byte_size IS NULL", "p_mime_type IS NULL",
        ),
        "saas_start_catalog_asset_cutover_batch": (
            "p_batch_id IS NULL", "p_expected_count IS NULL",
            "p_manifest_digest IS NULL", "p_keyset_digest IS NULL",
        ),
        "saas_add_catalog_asset_cutover_entry": (
            "p_batch_id IS NULL", "p_object_name IS NULL", "p_sha256 IS NULL",
            "p_byte_size IS NULL", "p_mime_type IS NULL",
        ),
        "saas_finalize_catalog_asset_cutover_batch": ("p_batch_id IS NULL",),
        "saas_clone_catalog_candidate_with_asset": (
            "p_candidate_id IS NULL", "p_reviewed_by IS NULL",
            "p_asset_object_name IS NULL", "p_json_path IS NULL",
        ),
        "saas_clone_catalog_candidate_with_image_metadata": (
            "p_candidate_id IS NULL", "p_reviewed_by IS NULL",
            "p_asset_object_name IS NULL", "p_json_path IS NULL",
            "p_image_kind IS NULL", "p_image_label IS NULL",
            "p_image_references IS NULL",
        ),
    }
    for document in (registry, cutover, bootstrap):
        for name, guards in expected_null_guards.items():
            if f"CREATE OR REPLACE FUNCTION {name}" not in document:
                continue
            function = _function_sql(document, name)
            for guard in guards:
                assert guard in function, f"{name} lacks {guard}"


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
    assert "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 13" in mixed
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


def test_final_supplier_sql_allowlists_include_idelika_and_conceptos():
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

        mixed = _function_definition(sql, "saas_reserve_mixed_cart")
        expected_catalogs = ",".join(f"'{catalog}'" for catalog in MIXED_CATALOGS)
        assert f"v_catalog NOT IN ({expected_catalogs})" in mixed
        assert len(MIXED_CATALOGS) == MIXED_CATALOG_COUNT


def test_supplier_allowlist_helper_handles_supplier_operands_and_ignores_false_positives():
    sql = """
        -- p_supplier IN ('cr-global', 'sonara', 'sunon', 'alma')
        /* p_supplier = ANY(ARRAY['cr-global','sonara','sunon','alma']::TEXT[]) */
        category IN ('cr-global', 'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco', 'idelika', 'conceptos', 'labenze', 'requiez')
        OR other_supplier IN ('cr-global', 'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco', 'idelika', 'conceptos', 'labenze', 'requiez')
        OR 'supplier IN (''cr-global'', ''sonara'', ''sunon'', ''alma'', ''lumbro'', ''jome'', ''lauco'', ''idelika'', ''conceptos'', ''labenze'', ''requiez'')' = 'example'
        p_supplier IN (
            'cr-global'::TEXT,
            'sonara',
            'sunon',
            'alma',
            'lumbro',
            'jome',
            'lauco',
            'idelika',
            'conceptos',
            'labenze',
            'requiez'
        )
        OR enabled_supplier.value NOT IN (
            'cr-global'::public.supplier_code,
            'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco', 'idelika', 'conceptos', 'labenze', 'requiez'
        )
        OR p_supplier = ANY(ARRAY[
            'cr-global', 'sonara', 'sunon', 'alma', 'lumbro', 'jome', 'lauco', 'idelika', 'conceptos', 'labenze', 'requiez'
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


def test_supplier_array_cardinality_guards_allow_nine_and_reject_invalid_inputs():
    for sql_path in SQL_FILES:
        sql = sql_path.read_text(encoding="utf-8")
        guards = _supplier_cardinality_guards(sql)

        assert len(guards) == 2
        assert guards == [(1, 11), (1, 11)]
        for function_name in (
            "saas_recover_stale_catalog_sync_runs",
            "saas_claim_next_catalog_sync",
        ):
            function_sql = _function_sql(sql, function_name)
            assert _supplier_cardinality_guards(function_sql) == [(1, 11)]
            assert "COUNT(DISTINCT value) FROM UNNEST(p_enabled_suppliers)" in function_sql
            assert _supplier_allowlists(function_sql) == [EXPECTED_SUPPLIERS]

        assert all(minimum <= len(EXPECTED_SUPPLIERS) <= maximum for minimum, maximum in guards)
        assert all(12 > maximum for _, maximum in guards)


def test_forward_idelika_conceptos_migration_widens_contracts_without_destructive_table_work():
    migration = IDELIKA_CONCEPTOS_MIGRATION.read_text(encoding="utf-8")
    constraints = (
        ("saas_catalog_sources", "saas_catalog_sources_supplier_check"),
        ("saas_catalog_snapshot_versions", "saas_catalog_snapshot_versions_supplier_check"),
        ("saas_catalog_reservations", "saas_catalog_reservations_supplier_check"),
    )

    for table, constraint in constraints:
        assert f"ALTER TABLE {table}\n    DROP CONSTRAINT IF EXISTS {constraint};" in migration
        assert (
            f"ALTER TABLE {table}\n    ADD CONSTRAINT {constraint}\n"
            "    CHECK (supplier IN ('cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'));"
        ) in migration

    for function_name in (
        "saas_recover_stale_catalog_sync_runs",
        "saas_claim_next_catalog_sync",
        "saas_catalog_reservation_summary",
        "saas_reserve_catalog_items",
        "saas_reserve_mixed_cart",
    ):
        assert f"public.{function_name}" in migration

    assert "CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 9" in migration
    assert "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 11" in migration
    assert "INSERT INTO saas_catalog_sources" in migration
    assert "('idelika', 'IDÉLIKA', 'idelika')" in migration
    assert "('conceptos', 'Conceptos', 'conceptos')" in migration
    assert "ON CONFLICT (supplier) DO NOTHING" in migration
    assert "WHERE supplier = 'cr-global'" in migration
    assert "DROP TABLE" not in migration.upper()
    assert "TRUNCATE" not in migration.upper()
    assert "DELETE FROM" not in migration.upper()


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


def test_offiho_snapshot_migration_only_widens_the_supplier_constraint():
    sql = OFFIHO_SNAPSHOT_MIGRATION.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql.lower())

    assert (
        "alter table saas_supplier_catalog_snapshots "
        "drop constraint if exists saas_supplier_catalog_snapshots_supplier_check"
    ) in normalized
    assert (
        "add constraint saas_supplier_catalog_snapshots_supplier_check "
        "check (supplier in ('tarkett', 'offiho'))"
    ) in normalized
    for destructive in ("drop table", "truncate", "delete from", " update "):
        assert destructive not in normalized


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


def test_cursor_bootstrap_keeps_local_promotion_cursor_optional_and_matches_bootstrap():
    migration = SETUP / "2026_08_catalog_sync_cursor_bootstrap.sql"
    stage = _function_definition(migration.read_text("utf-8"), "saas_stage_catalog_candidate")
    bootstrap = _function_definition(BOOTSTRAP.read_text("utf-8"), "saas_stage_catalog_candidate")

    assert "p_delta_link TEXT" in stage
    assert "p_delta_link IS NOT NULL AND (" in stage
    assert "COALESCE(p_delta_link, delta_link)" in stage
    assert "p_delta_link IS NOT NULL AND v_source.delta_link IS DISTINCT FROM p_delta_link" in stage
    assert stage == bootstrap


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


def test_bootstrap_builds_historic_catalog_schema_with_final_idelika_conceptos_state():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    jobs_migration = JOBS_RLS_MIGRATION.read_text(encoding="utf-8")

    assert bootstrap.count(jobs_migration) == 1
    legacy_snapshots = _statement(
        bootstrap,
        "CREATE TABLE IF NOT EXISTS saas_supplier_catalog_snapshots",
    )
    assert "supplier TEXT PRIMARY KEY CHECK (supplier IN ('tarkett', 'offiho'))" in legacy_snapshots
    assert "CREATE TABLE IF NOT EXISTS saas_catalog_sources" in bootstrap
    assert "CREATE OR REPLACE FUNCTION saas_reserve_mixed_cart" in bootstrap
    assert "'cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'" in bootstrap


def _local_postgres_test_context():
    dsn = os.environ.get("TASK6_LOCAL_POSTGRES_URL", "").strip()
    container = os.environ.get("TASK6_LOCAL_POSTGRES_CONTAINER", "").strip()
    if not dsn or not container:
        pytest.skip(
            "Certified local PostgreSQL validation is opt-in; "
            "set TASK6_LOCAL_POSTGRES_URL and TASK6_LOCAL_POSTGRES_CONTAINER"
        )
    parsed = urlsplit(dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != "127.0.0.1"
        or not parsed.username
        or not parsed.password
        or not parsed.path.removeprefix("/").startswith("test_")
        or not container.startswith("codex-task6-idelika-conceptos-pg-")
    ):
        raise AssertionError("Task 6 PostgreSQL target must be a local disposable container")
    return container, unquote(parsed.username), unquote(parsed.password), parsed.path.removeprefix("/")


def _container_psql(container, user, password, database, sql):
    result = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={password}", container,
            "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
            "-U", user, "-d", database,
            "-At", "-F", "|",
        ],
        input=sql.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout.decode("utf-8")


def _container_psql_failure(container, user, password, database, sql):
    result = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={password}", container,
            "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
            "-U", user, "-d", database,
        ],
        input=sql.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, result.stdout.decode("utf-8", errors="replace")
    return result.stderr.decode("utf-8", errors="replace")


def _start_container_psql(container, user, password, database, sql):
    process = subprocess.Popen(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={password}", container,
            "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
            "-U", user, "-d", database, "-At",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.write(sql.encode("utf-8"))
    process.stdin.close()
    return process


def _finish_container_psql(process):
    returncode = process.wait(timeout=30)
    assert process.stdout is not None and process.stderr is not None
    stdout = process.stdout.read().decode("utf-8", errors="replace")
    stderr = process.stderr.read().decode("utf-8", errors="replace")
    assert returncode == 0, stderr
    return stdout


_LOCAL_SUPABASE_STORAGE_SHIM = """
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE TABLE IF NOT EXISTS storage.buckets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    public BOOLEAN NOT NULL DEFAULT FALSE,
    file_size_limit BIGINT,
    allowed_mime_types TEXT[]
);
CREATE TABLE IF NOT EXISTS storage.objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id TEXT NOT NULL,
    name TEXT NOT NULL
);
ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
"""

_HISTORICAL_GENERIC_SUPPLIERS = (
    "'cr-global','sonara','sunon','alma','lumbro','jome','lauco'"
)
_FINAL_GENERIC_SUPPLIERS = (
    "'cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'"
)
_HISTORICAL_MIXED_CATALOGS = (
    "'tarkett','offiho','cr-global','sonara','sunon','alma','lumbro','jome','lauco'"
)
_FINAL_MIXED_CATALOGS = (
    "'tarkett','offiho','cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos'"
)


def _historical_task6_bootstrap(bootstrap):
    assert bootstrap.count(_FINAL_MIXED_CATALOGS) == 2
    assert bootstrap.count(_FINAL_GENERIC_SUPPLIERS) == 10
    assert bootstrap.count("CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 9") == 2
    assert bootstrap.count("jsonb_array_length(p_groups) NOT BETWEEN 0 AND 11") == 2

    historical = bootstrap.replace(_FINAL_MIXED_CATALOGS, _HISTORICAL_MIXED_CATALOGS)
    assert historical.count(_FINAL_GENERIC_SUPPLIERS) == 8
    historical = historical.replace(_FINAL_GENERIC_SUPPLIERS, _HISTORICAL_GENERIC_SUPPLIERS)
    historical = historical.replace(
        "CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 9",
        "CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 7",
    )
    historical = historical.replace(
        "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 11",
        "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 9",
    )

    assert historical.count(_FINAL_GENERIC_SUPPLIERS) == 0
    assert historical.count(_FINAL_MIXED_CATALOGS) == 0
    assert historical.count(_HISTORICAL_GENERIC_SUPPLIERS) == 10
    assert historical.count(_HISTORICAL_MIXED_CATALOGS) == 2
    assert historical.count("CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 7") == 2
    assert historical.count("jsonb_array_length(p_groups) NOT BETWEEN 0 AND 9") == 2
    return historical


def test_local_postgres_applies_task6_bootstrap_and_forward_migration():
    container, user, password, database = _local_postgres_test_context()
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    historical_bootstrap = _historical_task6_bootstrap(bootstrap)
    migration = IDELIKA_CONCEPTOS_MIGRATION.read_text(encoding="utf-8")

    _container_psql(container, user, password, database, _LOCAL_SUPABASE_STORAGE_SHIM)
    _container_psql(container, user, password, database, historical_bootstrap)

    historical_constraints = _container_psql(
        container,
        user,
        password,
        database,
        """
        SELECT conrelid::regclass::text || ':' || pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname IN (
            'saas_catalog_sources_supplier_check',
            'saas_catalog_snapshot_versions_supplier_check',
            'saas_catalog_reservations_supplier_check'
        )
        ORDER BY conrelid::regclass::text;
        """,
    )
    assert historical_constraints.count("'idelika'::text") == 0
    assert historical_constraints.count("'conceptos'::text") == 0

    historical_definitions = _container_psql(
        container,
        user,
        password,
        database,
        """
        SELECT oid::regprocedure::text || ':' || pg_get_functiondef(oid)
        FROM pg_proc
        WHERE oid = ANY (ARRAY[
            'public.saas_recover_stale_catalog_sync_runs(text[])'::regprocedure,
            'public.saas_claim_next_catalog_sync(text[])'::regprocedure,
            'public.saas_catalog_reservation_summary(text,integer)'::regprocedure,
            'public.saas_reserve_catalog_items(integer,uuid,text,jsonb)'::regprocedure,
            'public.saas_reserve_mixed_cart(integer,uuid,jsonb)'::regprocedure
        ])
        ORDER BY oid::regprocedure::text;
        """,
    )
    assert historical_definitions.count("CREATE OR REPLACE FUNCTION") == 5
    assert historical_definitions.count("'idelika'") == 0
    assert historical_definitions.count("'conceptos'") == 0
    assert historical_definitions.count("NOT BETWEEN 1 AND 7") == 2
    assert historical_definitions.count("NOT BETWEEN 0 AND 9") == 1

    _container_psql(
        container,
        user,
        password,
        database,
        """
        INSERT INTO saas_catalog_sources (
            supplier, label, adapter, graph_drive_id, graph_root_item_id
        ) VALUES (
            'cr-global', 'CR Global', 'cr_global', 'task6-drive', 'task6-root'
        );
        """,
    )
    _container_psql(container, user, password, database, migration)

    constraints = _container_psql(
        container,
        user,
        password,
        database,
        """
        SELECT conrelid::regclass::text || ':' || pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname IN (
            'saas_catalog_sources_supplier_check',
            'saas_catalog_snapshot_versions_supplier_check',
            'saas_catalog_reservations_supplier_check'
        )
        ORDER BY conrelid::regclass::text;
        """,
    )
    assert constraints.count("'idelika'::text") == 3
    assert constraints.count("'conceptos'::text") == 3

    definitions = _container_psql(
        container,
        user,
        password,
        database,
        """
        SELECT oid::regprocedure::text || ':' || pg_get_functiondef(oid)
        FROM pg_proc
        WHERE oid = ANY (ARRAY[
            'public.saas_recover_stale_catalog_sync_runs(text[])'::regprocedure,
            'public.saas_claim_next_catalog_sync(text[])'::regprocedure,
            'public.saas_catalog_reservation_summary(text,integer)'::regprocedure,
            'public.saas_reserve_catalog_items(integer,uuid,text,jsonb)'::regprocedure,
            'public.saas_reserve_mixed_cart(integer,uuid,jsonb)'::regprocedure
        ])
        ORDER BY oid::regprocedure::text;
        """,
    )
    assert definitions.count("CREATE OR REPLACE FUNCTION") == 5
    assert definitions.count("'idelika'") >= 5
    assert definitions.count("'conceptos'") >= 5
    assert definitions.count("NOT BETWEEN 1 AND 9") == 2
    assert definitions.count("NOT BETWEEN 0 AND 11") == 1

    provisioned_sources = _container_psql(
        container,
        user,
        password,
        database,
        """
        SELECT supplier || ':' || label || ':' || adapter || ':' ||
               graph_drive_id || ':' || graph_root_item_id
        FROM saas_catalog_sources
        WHERE supplier IN ('idelika', 'conceptos')
        ORDER BY supplier;
        """,
    )
    assert provisioned_sources.splitlines() == [
        "conceptos:Conceptos:conceptos:task6-drive:task6-root",
        "idelika:IDÉLIKA:idelika:task6-drive:task6-root",
    ]

    accepted = _container_psql(
        container,
        user,
        password,
        database,
        """
        SELECT 'sync:' || saas_recover_stale_catalog_sync_runs(
            ARRAY['cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos']
        );
        SELECT 'claim:' || COUNT(*) FROM saas_claim_next_catalog_sync(
            ARRAY['cr-global','sonara','sunon','alma','lumbro','jome','lauco','idelika','conceptos']
        );
        SELECT 'summary-empty:' || COUNT(*) FROM saas_catalog_reservation_summary('idelika', 1);

        INSERT INTO saas_usuarios (id, email, hashed_password)
        VALUES (1, 'task6-local@example.test', 'not-a-real-password');
        INSERT INTO saas_quote_jobs (id, usuario_id, status, input_path, template)
        VALUES
            ('11111111-1111-4111-8111-111111111111', 1, 'draft', 'task6/input-1', 'task6'),
            ('22222222-2222-4222-8222-222222222222', 1, 'draft', 'task6/input-2', 'task6');

        SELECT 'reserve:' || COUNT(*)
        FROM saas_reserve_catalog_items(
            1,
            '11111111-1111-4111-8111-111111111111'::uuid,
            'idelika',
            '[{"internal_id":"idelika:local","sku":"IDE-LOCAL","quantity":"1","stock":"2"}]'::jsonb
        );
        SELECT 'summary-idelika:' || COUNT(*) FROM saas_catalog_reservation_summary('idelika', 1);
        SELECT 'mixed:' || jsonb_array_length(saas_reserve_mixed_cart(
            1,
            '22222222-2222-4222-8222-222222222222'::uuid,
            (
                SELECT jsonb_agg(jsonb_build_object(
                    'catalog', catalog,
                    'items', jsonb_build_array(jsonb_build_object(
                        'identity', catalog || ':local',
                        'sku', 'LOCAL',
                        'quantity', '1',
                        'stock', '2'
                    ))
                ))
                FROM unnest(ARRAY[
                    'tarkett','offiho','cr-global','sonara','sunon','alma',
                    'lumbro','jome','lauco','idelika','conceptos'
                ]) AS catalogs(catalog)
            )
        ));
        """,
    )
    assert accepted.splitlines() == [
        "sync:0",
        "claim:0",
        "summary-empty:0",
        "reserve:1",
        "summary-idelika:1",
        "mixed:11",
    ]

    rejected = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={password}", container,
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
            "-U", user, "-d", database,
        ],
        input=b"SELECT saas_recover_stale_catalog_sync_runs(ARRAY['idelika','conceptos']);",
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 0, rejected.stderr.decode("utf-8", errors="replace")


def test_local_postgres_catalog_asset_registry_contract_is_opt_in():
    """Ejercita concurrencia, ACL, manifiesto y clones en PostgreSQL real opt-in."""
    container, user, password, database = _local_postgres_test_context()
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    registry = ASSET_REGISTRY_MIGRATION.read_text(encoding="utf-8")
    cutover = ASSET_REGISTRY_CUTOVER.read_text(encoding="utf-8")
    _container_psql(container, user, password, database, _LOCAL_SUPABASE_STORAGE_SHIM)
    _container_psql(container, user, password, database, bootstrap)
    _container_psql(container, user, password, database, registry)

    concurrent_object = "a" * 64 + ".png"
    first = _start_container_psql(container, user, password, database, f"""
        BEGIN;
        SET LOCAL ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{concurrent_object}', 'r2', 'catalog-assets', 123, 'image/png'
        );
        SELECT pg_sleep(2);
        COMMIT;
    """)
    time.sleep(0.5)
    second = _start_container_psql(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{concurrent_object}', 'r2', 'catalog-assets', 123, 'image/png'
        );
    """)
    assert concurrent_object in _finish_container_psql(first)
    assert concurrent_object in _finish_container_psql(second)
    concurrent_count = _container_psql(
        container, user, password, database,
        f"SELECT COUNT(*) FROM public.saas_catalog_assets WHERE object_name='{concurrent_object}';",
    )
    assert concurrent_count.strip() == "1"
    preserved_provider = _container_psql(
        container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{concurrent_object}', 'supabase', 'catalog-assets', 123, 'image/png'
        );
        RESET ROLE;
        SELECT storage_provider FROM public.saas_catalog_assets
        WHERE object_name='{concurrent_object}';
        """,
    ).splitlines()
    assert preserved_provider == [concurrent_object, "r2"]
    conflict = _container_psql_failure(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{concurrent_object}', 'r2', 'catalog-assets', 124, 'image/png'
        );
    """)
    assert "Catalog asset registry conflict" in conflict

    supabase_first_object = "b" * 64 + ".webp"
    supabase_first = _container_psql(
        container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{supabase_first_object}', 'supabase', 'catalog-assets', 456, 'image/webp'
        );
        SELECT public.saas_register_catalog_asset(
            '{supabase_first_object}', 'r2', 'catalog-assets', 456, 'image/webp'
        );
        RESET ROLE;
        SELECT storage_provider FROM public.saas_catalog_assets
        WHERE object_name='{supabase_first_object}';
        """,
    ).splitlines()
    assert supabase_first == [supabase_first_object, supabase_first_object, "supabase"]

    mime_conflict = _container_psql_failure(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{supabase_first_object}', 'r2', 'catalog-assets', 456, 'image/png'
        );
    """)
    assert "invalid catalog asset registry input" in mime_conflict
    bucket_conflict = _container_psql_failure(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            '{supabase_first_object}', 'r2', 'quote-files', 456, 'image/webp'
        );
    """)
    assert "invalid catalog asset registry input" in bucket_conflict

    roles_and_rls = _container_psql(container, user, password, database, """
        SELECT role_name || ':' || table_name || ':' ||
               has_table_privilege(role_name, 'public.' || table_name, 'SELECT')::TEXT || ':' ||
               has_table_privilege(role_name, 'public.' || table_name, 'INSERT')::TEXT || ':' ||
               has_table_privilege(role_name, 'public.' || table_name, 'UPDATE')::TEXT || ':' ||
               has_table_privilege(role_name, 'public.' || table_name, 'DELETE')::TEXT
        FROM unnest(ARRAY['anon','authenticated']) AS roles(role_name)
        CROSS JOIN unnest(ARRAY[
            'saas_catalog_assets',
            'saas_catalog_asset_cutover_batches',
            'saas_catalog_asset_cutover_entries'
        ]) AS tables(table_name)
        ORDER BY role_name, table_name;
        SELECT 'service:' || table_name || ':' ||
               has_table_privilege('service_role', 'public.' || table_name, 'SELECT')::TEXT || ':' ||
               has_table_privilege('service_role', 'public.' || table_name, 'INSERT')::TEXT || ':' ||
               has_table_privilege('service_role', 'public.' || table_name, 'UPDATE')::TEXT || ':' ||
               has_table_privilege('service_role', 'public.' || table_name, 'DELETE')::TEXT
        FROM unnest(ARRAY[
            'saas_catalog_assets',
            'saas_catalog_asset_cutover_batches',
            'saas_catalog_asset_cutover_entries'
        ]) AS tables(table_name)
        ORDER BY table_name;
        SELECT 'rls:' || COUNT(*) FILTER (WHERE relrowsecurity) || ':' ||
               COUNT(*) FILTER (WHERE NOT relrowsecurity)
        FROM pg_class
        WHERE oid = ANY(ARRAY[
            'public.saas_catalog_assets'::regclass,
            'public.saas_catalog_asset_cutover_batches'::regclass,
            'public.saas_catalog_asset_cutover_entries'::regclass
        ]);
        SELECT 'policies:' || COUNT(*) FROM pg_policies
        WHERE schemaname='public' AND tablename = ANY(ARRAY[
            'saas_catalog_assets',
            'saas_catalog_asset_cutover_batches',
            'saas_catalog_asset_cutover_entries'
        ]);
        SELECT 'public-table-acl:' || COUNT(*)
        FROM pg_class AS relation
        CROSS JOIN LATERAL aclexplode(
            COALESCE(relation.relacl, acldefault('r', relation.relowner))
        ) AS acl
        WHERE relation.oid = ANY(ARRAY[
            'public.saas_catalog_assets'::regclass,
            'public.saas_catalog_asset_cutover_batches'::regclass,
            'public.saas_catalog_asset_cutover_entries'::regclass
        ]) AND acl.grantee = 0;
    """)
    acl_lines = roles_and_rls.splitlines()
    assert len([line for line in acl_lines if line.endswith(":false:false:false:false")]) == 6
    assert len([line for line in acl_lines if line.startswith("service:") and line.endswith(":true:false:false:false")]) == 3
    assert "rls:3:0" in acl_lines
    assert "policies:0" in acl_lines
    assert "public-table-acl:0" in acl_lines

    def series_rows(prefix, count=2214):
        return f"""
            SELECT i,
                   encode(extensions.digest(convert_to('{prefix}-' || i::TEXT, 'UTF8'), 'sha256'), 'hex') AS sha256,
                   encode(extensions.digest(convert_to('{prefix}-' || i::TEXT, 'UTF8'), 'sha256'), 'hex') || '.png' AS object_name
            FROM generate_series(1, {count}) AS generated(i)
        """

    def start_batch(batch_id, prefix, count=2214, manifest="computed", keyset="computed"):
        rows = series_rows(prefix, count)
        manifest_sql = (
            "encode(extensions.digest(convert_to(string_agg(object_name || '|' || sha256 || '|' || i::TEXT || '|image/png', E'\\n' ORDER BY object_name), 'UTF8'), 'sha256'), 'hex')"
            if manifest == "computed" else f"'{manifest}'"
        )
        keyset_sql = (
            "encode(extensions.digest(convert_to(string_agg(object_name, E'\\n' ORDER BY object_name), 'UTF8'), 'sha256'), 'hex')"
            if keyset == "computed" else f"'{keyset}'"
        )
        return _container_psql(container, user, password, database, f"""
            SET ROLE service_role;
            WITH rows AS ({rows})
            SELECT public.saas_start_catalog_asset_cutover_batch(
                '{batch_id}'::UUID, 2214, {manifest_sql}, {keyset_sql}
            ) FROM rows;
        """)

    def load_batch(batch_id, prefix, count=2214):
        rows = series_rows(prefix, count)
        loaded = _container_psql(container, user, password, database, f"""
            SET ROLE service_role;
            WITH rows AS ({rows})
            SELECT COUNT(*) FROM (
                SELECT public.saas_add_catalog_asset_cutover_entry(
                    '{batch_id}'::UUID, object_name, sha256, i, 'image/png'
                ) FROM rows
            ) AS accepted;
        """)
        assert loaded.strip() == str(count)

    def register_series(prefix, count=2214):
        rows = series_rows(prefix, count)
        registered = _container_psql(container, user, password, database, f"""
            SET ROLE service_role;
            WITH rows AS ({rows})
            SELECT COUNT(*) FROM (
                SELECT public.saas_register_catalog_asset(
                    object_name, 'r2', 'catalog-assets', i, 'image/png'
                ) FROM rows
            ) AS accepted;
        """)
        assert registered.strip() == str(count)

    def finalize_failure(batch_id, expected_message):
        failure = _container_psql_failure(container, user, password, database, f"""
            SET ROLE service_role;
            SELECT public.saas_finalize_catalog_asset_cutover_batch('{batch_id}'::UUID);
        """)
        assert expected_message in failure

    token = uuid.uuid4().hex
    shared_prefix = f"task3-shared-{token}"
    missing_prefix = f"task3-registry-missing-{token}"
    manifest_batch = uuid.uuid4()
    start_batch(manifest_batch, shared_prefix, manifest="0" * 64)
    register_series(shared_prefix)
    load_batch(manifest_batch, shared_prefix)
    finalize_failure(manifest_batch, "catalog asset cutover manifest is not verified")

    keyset_batch = uuid.uuid4()
    start_batch(keyset_batch, shared_prefix, keyset="1" * 64)
    load_batch(keyset_batch, shared_prefix)
    finalize_failure(keyset_batch, "catalog asset cutover manifest is not verified")

    short_batch = uuid.uuid4()
    start_batch(short_batch, shared_prefix, count=2213)
    load_batch(short_batch, shared_prefix, count=2213)
    finalize_failure(short_batch, "catalog asset cutover manifest is not verified")

    registry_batch = uuid.uuid4()
    start_batch(registry_batch, missing_prefix)
    load_batch(registry_batch, missing_prefix)
    finalize_failure(registry_batch, "catalog asset cutover registry mismatch")

    verified_batch = uuid.uuid4()
    start_batch(verified_batch, shared_prefix)
    frozen = _container_psql_failure(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_start_catalog_asset_cutover_batch(
            '{verified_batch}'::UUID, 2214, '{'2' * 64}', '{'3' * 64}'
        );
    """)
    assert "catalog asset cutover batch conflict" in frozen
    load_batch(verified_batch, shared_prefix)
    finalized = _container_psql(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_finalize_catalog_asset_cutover_batch('{verified_batch}'::UUID);
    """)
    assert finalized.strip() == str(verified_batch)
    immutable = _container_psql_failure(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_add_catalog_asset_cutover_entry(
            '{verified_batch}'::UUID, '{'f' * 64}.png', '{'f' * 64}', 1, 'image/png'
        );
    """)
    assert "catalog asset cutover batch is not loading" in immutable

    wrong_verified_batch = _container_psql_failure(
        container, user, password, database, cutover
    )
    assert "catalog asset R2 cutover manifest is not verified" in wrong_verified_batch

    null_cases = (
        (
            "SELECT public.saas_register_catalog_asset(NULL, 'r2', 'catalog-assets', 1, 'image/png');",
            "invalid catalog asset registry input",
        ),
        (
            f"SELECT public.saas_start_catalog_asset_cutover_batch(NULL, 2214, '{'0' * 64}', '{'1' * 64}');",
            "invalid catalog asset cutover batch",
        ),
        (
            f"SELECT public.saas_add_catalog_asset_cutover_entry(NULL, '{'f' * 64}.png', '{'f' * 64}', 1, 'image/png');",
            "invalid catalog asset cutover entry",
        ),
        (
            "SELECT public.saas_finalize_catalog_asset_cutover_batch(NULL);",
            "invalid catalog asset cutover batch",
        ),
        (
            f"SELECT public.saas_clone_catalog_candidate_with_asset(NULL, 1, '{'f' * 64}.png', ARRAY['items','0']);",
            "invalid catalog item asset target",
        ),
        (
            f"SELECT public.saas_clone_catalog_candidate_with_image_metadata(extensions.gen_random_uuid(), 1, '{'f' * 64}.png', ARRAY['items','0'], 'official', NULL, ARRAY[]::TEXT[]);",
            "invalid catalog image metadata",
        ),
    )
    for statement, expected_error in null_cases:
        failure = _container_psql_failure(container, user, password, database, f"""
            SET ROLE service_role;
            {statement}
        """)
        assert expected_error in failure

    rpc_acl = _container_psql(container, user, password, database, """
        SELECT role_name || ':' || COUNT(*) FILTER (
            WHERE has_function_privilege(role_name, routine, 'EXECUTE')
        )
        FROM unnest(ARRAY['anon','authenticated','service_role']) AS roles(role_name)
        CROSS JOIN unnest(ARRAY[
            'public.saas_register_catalog_asset(text,text,text,bigint,text)',
            'public.saas_start_catalog_asset_cutover_batch(uuid,integer,text,text)',
            'public.saas_add_catalog_asset_cutover_entry(uuid,text,text,bigint,text)',
            'public.saas_finalize_catalog_asset_cutover_batch(uuid)',
            'public.saas_clone_catalog_candidate_with_asset(uuid,integer,text,text[])',
            'public.saas_clone_catalog_candidate_with_image_metadata(uuid,integer,text,text[],text,text,text[])'
        ]) AS routines(routine)
        GROUP BY role_name ORDER BY role_name;
        SELECT 'public-function-acl:' || COUNT(*)
        FROM pg_proc AS routine
        CROSS JOIN LATERAL aclexplode(
            COALESCE(routine.proacl, acldefault('f', routine.proowner))
        ) AS acl
        WHERE routine.oid = ANY(ARRAY[
            'public.saas_register_catalog_asset(text,text,text,bigint,text)'::regprocedure,
            'public.saas_start_catalog_asset_cutover_batch(uuid,integer,text,text)'::regprocedure,
            'public.saas_add_catalog_asset_cutover_entry(uuid,text,text,bigint,text)'::regprocedure,
            'public.saas_finalize_catalog_asset_cutover_batch(uuid)'::regprocedure,
            'public.saas_clone_catalog_candidate_with_asset(uuid,integer,text,text[])'::regprocedure,
            'public.saas_clone_catalog_candidate_with_image_metadata(uuid,integer,text,text[],text,text,text[])'::regprocedure
        ]) AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE';
    """)
    assert rpc_acl.splitlines() == [
        "anon:0", "authenticated:0", "service_role:6", "public-function-acl:0",
    ]

    admin_email = f"task3-{token}@example.test"
    run_asset, run_metadata = uuid.uuid4(), uuid.uuid4()
    candidate_asset, candidate_metadata = uuid.uuid4(), uuid.uuid4()
    asset_source_hash = token * 2
    metadata_source_hash = token[::-1] * 2
    object_sql = f"encode(extensions.digest(convert_to('{shared_prefix}-1', 'UTF8'), 'sha256'), 'hex') || '.png'"
    supabase_object_sql = f"encode(extensions.digest(convert_to('task3-supabase-{token}', 'UTF8'), 'sha256'), 'hex') || '.png'"
    clone_setup = f"""
        INSERT INTO public.saas_usuarios(email, hashed_password, es_admin, activo)
        VALUES ('{admin_email}', 'not-a-real-password', TRUE, TRUE);
        INSERT INTO public.saas_catalog_sources(
            supplier, label, adapter, graph_drive_id, graph_root_item_id
        ) VALUES ('requiez', 'Requiez', 'requiez', 'task3-drive', 'task3-root')
        ON CONFLICT (supplier) DO NOTHING;
        INSERT INTO public.saas_catalog_sync_runs(
            id, source_id, request_key, trigger_type, status, requested_by, metrics, started_at
        ) VALUES (
            '{run_asset}'::UUID, (SELECT id FROM public.saas_catalog_sources WHERE supplier='requiez'),
            '{uuid.uuid4()}'::UUID, 'manual', 'running',
            (SELECT id FROM public.saas_usuarios WHERE email='{admin_email}'), '{{}}'::JSONB, NOW()
        );
        INSERT INTO public.saas_catalog_snapshot_versions(
            id, supplier, source_hash, generated_at, status, payload, sync_run_id, base_published_version_id
        ) VALUES (
            '{candidate_asset}'::UUID, 'requiez', '{asset_source_hash}', '2026-08-31T12:00:00Z', 'candidate',
            jsonb_build_object('supplier','requiez','source_hash','{asset_source_hash}','generated_at','2026-08-31T12:00:00Z','items',jsonb_build_array(jsonb_build_object('internal_id','asset-item','attributes','{{}}'::JSONB))),
            '{run_asset}'::UUID, NULL
        );
        UPDATE public.saas_catalog_sync_runs
        SET status='awaiting_approval', candidate_version_id='{candidate_asset}'::UUID, finished_at=NOW(), updated_at=NOW()
        WHERE id='{run_asset}'::UUID;
        INSERT INTO public.saas_catalog_sync_runs(
            id, source_id, request_key, trigger_type, status, requested_by, metrics, started_at
        ) VALUES (
            '{run_metadata}'::UUID, (SELECT id FROM public.saas_catalog_sources WHERE supplier='requiez'),
            '{uuid.uuid4()}'::UUID, 'manual', 'running',
            (SELECT id FROM public.saas_usuarios WHERE email='{admin_email}'), '{{}}'::JSONB, NOW()
        );
        INSERT INTO public.saas_catalog_snapshot_versions(
            id, supplier, source_hash, generated_at, status, payload, sync_run_id, base_published_version_id
        ) VALUES (
            '{candidate_metadata}'::UUID, 'requiez', '{metadata_source_hash}', '2026-08-31T12:01:00Z', 'candidate',
            jsonb_build_object('supplier','requiez','source_hash','{metadata_source_hash}','generated_at','2026-08-31T12:01:00Z','items',jsonb_build_array(jsonb_build_object('internal_id','metadata-item','attributes','{{}}'::JSONB))),
            '{run_metadata}'::UUID, NULL
        );
        UPDATE public.saas_catalog_sync_runs
        SET status='awaiting_approval', candidate_version_id='{candidate_metadata}'::UUID, finished_at=NOW(), updated_at=NOW()
        WHERE id='{run_metadata}'::UUID;
        SET ROLE service_role;
        SELECT public.saas_register_catalog_asset(
            ({supabase_object_sql}), 'supabase', 'catalog-assets', 321, 'image/png'
        );
        RESET ROLE;
        SELECT 'storage:' || COUNT(*) FROM storage.objects WHERE bucket_id='catalog-assets' AND name=({object_sql});
    """
    assert "storage:0" in _container_psql(container, user, password, database, clone_setup).splitlines()
    cloned = _container_psql(container, user, password, database, f"""
        SET ROLE service_role;
        SELECT public.saas_clone_catalog_candidate_with_asset(
            '{candidate_asset}'::UUID,
            (SELECT id FROM public.saas_usuarios WHERE email='{admin_email}'),
            ({object_sql}), ARRAY['items','0']
        );
        SELECT public.saas_clone_catalog_candidate_with_image_metadata(
            '{candidate_metadata}'::UUID,
            (SELECT id FROM public.saas_usuarios WHERE email='{admin_email}'),
            ({supabase_object_sql}), ARRAY['items','0'], 'generated_reference',
            'Referencia Task 3', ARRAY['https://example.test/reference']
        );
    """)
    assert len(cloned.splitlines()) == 2
    clone_payloads = _container_psql(container, user, password, database, f"""
        SELECT run.id::TEXT || '|' ||
               version.payload #>> '{{items,0,attributes,approved_asset,bucket}}' || '|' ||
               version.payload #>> '{{items,0,attributes,approved_asset,path}}'
        FROM public.saas_catalog_sync_runs AS run
        JOIN public.saas_catalog_snapshot_versions AS version
          ON version.id = run.candidate_version_id
        WHERE run.id IN ('{run_asset}'::UUID, '{run_metadata}'::UUID)
        ORDER BY run.id;
    """)
    payload_lines = clone_payloads.splitlines()
    assert len(payload_lines) == 2
    expected_object = _container_psql(
        container, user, password, database, f"SELECT ({object_sql});"
    ).strip()
    expected_supabase_object = _container_psql(
        container, user, password, database, f"SELECT ({supabase_object_sql});"
    ).strip()
    assert any(line.endswith(f"|catalog-assets|{expected_object}") for line in payload_lines)
    assert any(line.endswith(f"|catalog-assets|{expected_supabase_object}") for line in payload_lines)
