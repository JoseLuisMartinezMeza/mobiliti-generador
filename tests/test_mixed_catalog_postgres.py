import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


DATABASE_URL = os.environ.get("MIXED_CART_TEST_DATABASE_URL", "").strip()
ALLOW_DDL = os.environ.get("MIXED_CART_TEST_ALLOW_DDL", "").strip()
if not DATABASE_URL or ALLOW_DDL != "1":
    pytest.skip("Postgres mixed-cart test is opt-in", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict


MIGRATION = Path("mobiliti_saas/supabase_setup/2026_07_mixed_catalog_cart.sql")
TABLES = (
    "saas_quote_jobs",
    "saas_tarkett_reservations",
    "saas_offiho_reservations",
    "saas_catalog_reservations",
)


def _function_definition(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION\s+{re.escape(name)}\b[\s\S]*?\n\$\$;",
        sql,
        flags=re.IGNORECASE,
    )
    assert match, f"Funcion ausente: {name}"
    return match.group(0)


def _dsn_identity(dsn: str):
    values = conninfo_to_dict(dsn)
    return (
        values.get("host") or "",
        str(values.get("port") or "5432"),
        values.get("dbname") or "",
        values.get("user") or "",
    )


def _assert_disposable_dsn():
    target = _dsn_identity(DATABASE_URL)
    for name in ("DATABASE_URL", "POSTGRES_URL", "SUPABASE_DB_URL"):
        candidate = os.environ.get(name, "").strip()
        if candidate:
            assert _dsn_identity(candidate) != target, (
                f"MIXED_CART_TEST_DATABASE_URL coincide con {name}"
            )
    assert target[2].startswith("test_") or target[2].endswith("_test")


def _assert_public_tables_absent(cursor):
    for table in TABLES:
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        assert cursor.fetchone()[0] is None, "La base de prueba debe estar vacia"


def _transaction_call(connection, sql, params, returned, allow_commit=None):
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            result = cursor.fetchone()[0]
        returned.set()
        if allow_commit is not None:
            assert allow_commit.wait(10), "timeout esperando commit concurrente"
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        returned.set()
        raise


def test_mixed_cart_postgres_rpc_atomicity_release_and_security():
    _assert_disposable_dsn()
    sql = MIGRATION.read_text(encoding="utf-8")
    reserve_sql = _function_definition(sql, "saas_reserve_mixed_cart")
    release_sql = _function_definition(sql, "saas_release_mixed_cart")
    connection = psycopg.connect(DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            actual_database = cursor.fetchone()[0]
            assert actual_database.startswith("test_") or actual_database.endswith("_test")
            _assert_public_tables_absent(cursor)
            cursor.execute("SELECT gen_random_uuid()")
            assert cursor.fetchone()[0] is not None

            cursor.execute("""
                CREATE TEMP TABLE saas_quote_jobs (
                    id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, status TEXT NOT NULL,
                    error_message TEXT, updated_at TIMESTAMPTZ
                )
            """)
            cursor.execute("""
                CREATE TEMP TABLE saas_tarkett_reservations (
                    id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, quote_job_id UUID NOT NULL,
                    product_code TEXT NOT NULL, quantity NUMERIC NOT NULL, status TEXT NOT NULL,
                    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
                )
            """)
            cursor.execute("""
                CREATE TEMP TABLE saas_offiho_reservations (
                    id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, quote_job_id UUID NOT NULL,
                    product_code TEXT NOT NULL, quantity NUMERIC NOT NULL, status TEXT NOT NULL,
                    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
                )
            """)
            cursor.execute("""
                CREATE TEMP TABLE saas_catalog_reservations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), supplier TEXT NOT NULL,
                    internal_id TEXT NOT NULL, sku TEXT NOT NULL, quantity NUMERIC NOT NULL,
                    usuario_id INTEGER NOT NULL, quote_job_id UUID, status TEXT NOT NULL,
                    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
                )
            """)
            cursor.execute(reserve_sql)
            cursor.execute(release_sql)
            cursor.execute("""
                INSERT INTO saas_quote_jobs (id, usuario_id, status) VALUES
                ('11111111-1111-4111-8111-111111111111', 7, 'draft'),
                ('22222222-2222-4222-8222-222222222222', 7, 'draft'),
                ('33333333-3333-4333-8333-333333333333', 7, 'draft')
            """)

            cursor.execute(
                "SELECT saas_reserve_mixed_cart(7, %s::uuid, '[]'::jsonb)",
                ("11111111-1111-4111-8111-111111111111",),
            )
            assert cursor.fetchone()[0] == []
            cursor.execute("SAVEPOINT consecutive_call")
            cursor.execute(
                "SELECT saas_reserve_mixed_cart(7, %s::uuid, %s::jsonb)",
                (
                    "22222222-2222-4222-8222-222222222222",
                    '[{"catalog":"tarkett","items":[{"identity":"T-1","sku":"T-1","quantity":"1","stock":"5"}]}]',
                ),
            )
            assert cursor.fetchone()[0][0]["identity"] == "T-1"
            cursor.execute("ROLLBACK TO consecutive_call")

            cursor.execute("SAVEPOINT invalid_second_group")
            with pytest.raises(psycopg.Error):
                cursor.execute(
                    "SELECT saas_reserve_mixed_cart(7, %s::uuid, %s::jsonb)",
                    (
                        "22222222-2222-4222-8222-222222222222",
                        '[{"catalog":"tarkett","items":[{"identity":"T-1","sku":"T-1","quantity":"1","stock":"5"}]},{"catalog":"alma","items":[{"identity":"alma:desk","sku":"AL-1","quantity":"bad","stock":"5"}]}]',
                    ),
                )
            cursor.execute("ROLLBACK TO invalid_second_group")
            for table in TABLES[1:]:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                assert cursor.fetchone()[0] == 0

            payload = '[{"catalog":"tarkett","items":[{"identity":"T-1","sku":"T-1","quantity":"1","stock":"5"}]},{"catalog":"alma","items":[{"identity":"alma:desk","sku":"AL-1","quantity":"2","stock":"7"}]}]'
            cursor.execute(
                "SELECT saas_reserve_mixed_cart(7, %s::uuid, %s::jsonb)",
                ("33333333-3333-4333-8333-333333333333", payload),
            )
            snapshot = cursor.fetchone()[0]
            assert {(row["catalog"], row["identity"]) for row in snapshot} == {
                ("tarkett", "T-1"), ("alma", "alma:desk")
            }
            cursor.execute(
                "SELECT saas_release_mixed_cart(%s::uuid)",
                ("33333333-3333-4333-8333-333333333333",),
            )
            assert cursor.fetchone()[0] == {"tarkett": 1, "offiho": 0, "supplier": 1}
            for table in TABLES[1:]:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE status = 'active'")
                assert cursor.fetchone()[0] == 0

            for signature in (
                "saas_reserve_mixed_cart(integer,uuid,jsonb)",
                "saas_release_mixed_cart(uuid)",
            ):
                cursor.execute(
                    """
                    SELECT prosecdef, proconfig
                    FROM pg_proc
                    WHERE oid = to_regprocedure(%s)
                    """,
                    (signature,),
                )
                prosecdef, proconfig = cursor.fetchone()
                assert prosecdef is True
                assert "search_path=public, pg_temp" in proconfig
    finally:
        connection.rollback()
        connection.close()


def _prepare_concurrency_session(connection, reserve_sql, release_sql):
    with connection.cursor() as cursor:
        _assert_public_tables_absent(cursor)
        cursor.execute("""
            CREATE TEMP TABLE saas_quote_jobs (
                id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, status TEXT NOT NULL,
                error_message TEXT, updated_at TIMESTAMPTZ
            )
        """)
        cursor.execute("""
            CREATE TEMP TABLE saas_tarkett_reservations (
                id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, quote_job_id UUID NOT NULL,
                product_code TEXT NOT NULL, quantity NUMERIC NOT NULL, status TEXT NOT NULL,
                created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
            )
        """)
        cursor.execute("""
            CREATE TEMP TABLE saas_offiho_reservations (
                id UUID PRIMARY KEY, usuario_id INTEGER NOT NULL, quote_job_id UUID NOT NULL,
                product_code TEXT NOT NULL, quantity NUMERIC NOT NULL, status TEXT NOT NULL,
                created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
            )
        """)
        cursor.execute("""
            CREATE TEMP TABLE saas_catalog_reservations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), supplier TEXT NOT NULL,
                internal_id TEXT NOT NULL, sku TEXT NOT NULL, quantity NUMERIC NOT NULL,
                usuario_id INTEGER NOT NULL, quote_job_id UUID, status TEXT NOT NULL,
                created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
            )
        """)
        cursor.execute(
            re.sub(
                r"(CREATE OR REPLACE FUNCTION\s+)saas_reserve_mixed_cart",
                r"\1pg_temp.saas_reserve_mixed_cart",
                reserve_sql,
                count=1,
                flags=re.IGNORECASE,
            )
        )
        cursor.execute(
            re.sub(
                r"(CREATE OR REPLACE FUNCTION\s+)saas_release_mixed_cart",
                r"\1pg_temp.saas_release_mixed_cart",
                release_sql,
                count=1,
                flags=re.IGNORECASE,
            )
        )
        cursor.execute("""
            INSERT INTO saas_quote_jobs (id, usuario_id, status) VALUES
            ('44444444-4444-4444-8444-444444444444', 7, 'queued'),
            ('55555555-5555-4555-8555-555555555555', 7, 'draft'),
            ('66666666-6666-4666-8666-666666666666', 7, 'queued'),
            ('77777777-7777-4777-8777-777777777777', 7, 'draft')
        """)
        cursor.execute("""
            INSERT INTO saas_tarkett_reservations
                (id, usuario_id, quote_job_id, product_code, quantity, status)
            VALUES
                ('88888888-8888-4888-8888-888888888888', 7,
                 '44444444-4444-4444-8444-444444444444', 'T-CONCURRENT-A', 1, 'active'),
                ('99999999-9999-4999-8999-999999999999', 7,
                 '66666666-6666-4666-8666-666666666666', 'T-CONCURRENT-B', 1, 'active')
        """)


def test_mixed_cart_postgres_reserve_release_lock_each_identity_in_both_orders():
    _assert_disposable_dsn()
    sql = MIGRATION.read_text(encoding="utf-8")
    reserve_sql = _function_definition(sql, "saas_reserve_mixed_cart")
    release_sql = _function_definition(sql, "saas_release_mixed_cart")
    first = psycopg.connect(DATABASE_URL)
    second = psycopg.connect(DATABASE_URL)
    try:
        _prepare_concurrency_session(first, reserve_sql, release_sql)
        _prepare_concurrency_session(second, reserve_sql, release_sql)

        reserve_returned = threading.Event()
        release_returned = threading.Event()
        allow_reserve_commit = threading.Event()
        reserve_payload = '[{"catalog":"tarkett","items":[{"identity":"T-CONCURRENT-A","sku":"T-CONCURRENT-A","quantity":"2","stock":"5"}]}]'
        with ThreadPoolExecutor(max_workers=2) as pool:
            reserve = pool.submit(
                _transaction_call,
                first,
                "SELECT pg_temp.saas_reserve_mixed_cart(7, %s::uuid, %s::jsonb)",
                ("55555555-5555-4555-8555-555555555555", reserve_payload),
                reserve_returned,
                allow_reserve_commit,
            )
            assert reserve_returned.wait(10)
            release = pool.submit(
                _transaction_call,
                second,
                "SELECT pg_temp.saas_release_mixed_cart(%s::uuid)",
                ("44444444-4444-4444-8444-444444444444",),
                release_returned,
            )
            try:
                assert not release_returned.wait(0.5)
            finally:
                allow_reserve_commit.set()
            assert reserve.result()[0]["reserved_before"] == "1.000000"
            assert release.result()["tarkett"] == 1

        release_returned = threading.Event()
        reserve_returned = threading.Event()
        allow_release_commit = threading.Event()
        reserve_payload = '[{"catalog":"tarkett","items":[{"identity":"T-CONCURRENT-B","sku":"T-CONCURRENT-B","quantity":"2","stock":"5"}]}]'
        with ThreadPoolExecutor(max_workers=2) as pool:
            release = pool.submit(
                _transaction_call,
                first,
                "SELECT pg_temp.saas_release_mixed_cart(%s::uuid)",
                ("66666666-6666-4666-8666-666666666666",),
                release_returned,
                allow_release_commit,
            )
            assert release_returned.wait(10)
            reserve = pool.submit(
                _transaction_call,
                second,
                "SELECT pg_temp.saas_reserve_mixed_cart(7, %s::uuid, %s::jsonb)",
                ("77777777-7777-4777-8777-777777777777", reserve_payload),
                reserve_returned,
            )
            try:
                assert not reserve_returned.wait(0.5)
            finally:
                allow_release_commit.set()
            assert release.result()["tarkett"] == 1
            assert reserve.result()[0]["reserved_before"] == "1.000000"
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
