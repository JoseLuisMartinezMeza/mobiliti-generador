import importlib.util
import os
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("apply_supabase_sql", Path("scripts/apply_supabase_sql.py"))
apply_supabase_sql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_supabase_sql)


def test_summarize_sql_detects_quote_jobs():
    summary = apply_supabase_sql.summarize_sql(
        """
        create table if not exists saas_quote_jobs (id uuid primary key);
        create index if not exists idx_quote_jobs_status on saas_quote_jobs(status);
        insert into storage.buckets (id) values ('quote-files');
        """
    )

    assert summary["create_table"] == 1
    assert summary["create_index"] == 1
    assert summary["insert"] == 1
    assert summary["storage_bucket"] is True
    assert summary["quote_jobs"] is True


def test_require_database_url_rejects_missing_and_placeholders():
    try:
        apply_supabase_sql.require_database_url({})
    except RuntimeError as exc:
        assert "DATABASE_URL" in str(exc)
    else:
        raise AssertionError("missing DATABASE_URL should fail")

    try:
        apply_supabase_sql.require_database_url({"DATABASE_URL": "postgresql://postgres:[YOUR_PASSWORD]@db.[PROJECT_REF].supabase.co/postgres"})
    except RuntimeError as exc:
        assert "placeholder" in str(exc)
    else:
        raise AssertionError("placeholder DATABASE_URL should fail")


def test_require_database_url_accepts_real_shape():
    assert apply_supabase_sql.require_database_url({"DATABASE_URL": "postgresql://user:pass@example.com/db"}).startswith("postgresql://")


PINNED_BATCH = "470442fc-3dc3-5948-b0e4-1dd34c1fcd30"
SETUP = Path("mobiliti_saas/supabase_setup")
BOOTSTRAP = SETUP / "create_tables.sql"
MIGRATION_A = SETUP / "2026_09_catalog_asset_registry_r2.sql"
MIGRATION_B = SETUP / "2026_09_catalog_asset_registry_r2_cutover.sql"


def test_runner_refuses_implicit_bootstrap_and_requires_an_explicit_mode(capsys):
    with pytest.raises(SystemExit) as error:
        apply_supabase_sql.main([])
    assert error.value.code == 2
    assert "DATABASE_URL" not in capsys.readouterr().out


def test_runner_bootstrap_is_explicit_dry_run_and_cannot_be_disguised_as_file(capsys):
    apply_supabase_sql.main(["--bootstrap-new-project"])
    assert "Dry-run" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        apply_supabase_sql.main(["--file", str(BOOTSTRAP)])
    with pytest.raises(SystemExit):
        apply_supabase_sql.main([
            "--bootstrap-new-project", "--file", str(MIGRATION_A)
        ])


def test_runner_allows_migration_a_but_pins_and_isolates_migration_b(capsys):
    apply_supabase_sql.main(["--file", str(MIGRATION_A)])
    assert "Dry-run" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        apply_supabase_sql.main(["--file", str(MIGRATION_B)])
    with pytest.raises(SystemExit):
        apply_supabase_sql.main([
            "--file", str(MIGRATION_B), "--confirm-cutover-batch", "wrong"
        ])
    with pytest.raises(SystemExit):
        apply_supabase_sql.main([
            "--file", str(MIGRATION_A), "--file", str(MIGRATION_B),
            "--confirm-cutover-batch", PINNED_BATCH,
        ])

    apply_supabase_sql.main([
        "--file", str(MIGRATION_B), "--confirm-cutover-batch", PINNED_BATCH,
    ])
    assert "Dry-run" in capsys.readouterr().out


def test_runner_rejects_every_noncanonical_file_path_before_database(tmp_path, monkeypatch):
    sql_a = MIGRATION_A.read_text(encoding="utf-8")
    sql_b = MIGRATION_B.read_text(encoding="utf-8")
    sql_bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    quoted_a = tmp_path / "quoted-a.sql"
    quoted_a.write_text(f"SELECT $sql${sql_a}$sql$;", encoding="utf-8")
    quoted_b = tmp_path / "quoted-b.sql"
    quoted_b.write_text(f"SELECT $sql${sql_b}$sql$;", encoding="utf-8")
    quoted_bootstrap = tmp_path / "quoted-bootstrap.sql"
    quoted_bootstrap.write_text(
        "SELECT '" + sql_bootstrap.replace("'", "''") + "';",
        encoding="utf-8",
    )
    standard_prefix = tmp_path / "standard-prefix.sql"
    standard_prefix.write_text(
        "SELECT E'" + sql_b.replace("\\", "\\\\").replace("'", "\\'") + "';",
        encoding="utf-8",
    )
    dollar_quoted = tmp_path / "dollar-quoted.sql"
    dollar_quoted.write_text("SELECT $body$unrelated SQL$body$;", encoding="utf-8")
    arbitrary = tmp_path / "operator-maintenance.sql"
    arbitrary.write_text("SELECT current_date;", encoding="utf-8")
    copied_a = tmp_path / "copied-a.sql"
    copied_a.write_text(sql_a, encoding="utf-8")
    copied_b = tmp_path / "copied-b.sql"
    copied_b.write_text(sql_b, encoding="utf-8")
    copied_bootstrap = tmp_path / "copied-bootstrap.sql"
    copied_bootstrap.write_text(sql_bootstrap, encoding="utf-8")
    combined = tmp_path / "combined.sql"
    combined.write_text(f"SELECT $sql${sql_a}\n{sql_b}$sql$;", encoding="utf-8")
    hardlink = tmp_path / "hardlink-a.sql"
    os.link(MIGRATION_A, hardlink)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        apply_supabase_sql,
        "apply_sql",
        lambda *_: pytest.fail("no debe alcanzar la base de datos"),
    )
    for path in (
        quoted_a, quoted_b, quoted_bootstrap, standard_prefix, dollar_quoted,
        arbitrary, copied_a, copied_b, copied_bootstrap, combined, hardlink,
    ):
        with pytest.raises(SystemExit):
            apply_supabase_sql.main(["--file", str(path), "--apply"])

    with pytest.raises(SystemExit):
        apply_supabase_sql.main(["--file", str(quoted_a), "--file", str(quoted_b)])


def test_runner_rejects_symlink_to_canonical_migration_when_windows_allows_it(tmp_path):
    link = tmp_path / "linked-a.sql"
    try:
        link.symlink_to(MIGRATION_A.resolve())
    except OSError as exc:
        pytest.skip(f"symlink no disponible en este Windows: {exc}")

    with pytest.raises(SystemExit):
        apply_supabase_sql.main(["--file", str(link)])


@pytest.mark.parametrize(
    ("path", "argv"),
    (
        (BOOTSTRAP, ["--bootstrap-new-project"]),
        (MIGRATION_A, ["--file", str(MIGRATION_A)]),
        (
            MIGRATION_B,
            ["--file", str(MIGRATION_B), "--confirm-cutover-batch", PINNED_BATCH],
        ),
    ),
)
def test_runner_rejects_mutated_content_at_a_canonical_path(path, argv):
    parser = apply_supabase_sql.build_parser()
    args = parser.parse_args(argv)
    mutated = path.read_text(encoding="utf-8") + "\n-- changed\n"

    with pytest.raises(SystemExit):
        apply_supabase_sql.validate_sql_selection(args, parser, [(path, mutated)])


def test_runner_content_hash_normalizes_only_line_endings():
    parser = apply_supabase_sql.build_parser()
    args = parser.parse_args(["--file", str(MIGRATION_A)])
    crlf = MIGRATION_A.read_text(encoding="utf-8").replace("\n", "\r\n")

    apply_supabase_sql.validate_sql_selection(args, parser, [(MIGRATION_A, crlf)])


def test_runner_apply_never_prints_database_url(monkeypatch, capsys):
    secret_url = "postgresql://operator:secret@private.example.test/database"
    applied = []
    monkeypatch.setenv("DATABASE_URL", secret_url)
    monkeypatch.setattr(
        apply_supabase_sql,
        "apply_sql",
        lambda database_url, sql: applied.append((database_url, len(sql))),
    )

    apply_supabase_sql.main(["--file", str(MIGRATION_A), "--apply"])

    assert applied and applied[0][0] == secret_url
    captured = capsys.readouterr()
    assert secret_url not in captured.out
    assert secret_url not in captured.err

def test_catalog_deploy_docs_define_existing_project_order_and_all_server_env_names():
    cloud = Path("mobiliti_saas/CLOUD_DEPLOY.md").read_text(encoding="utf-8")
    setup = Path("mobiliti_saas/supabase_setup/README.md").read_text(encoding="utf-8")
    combined = cloud + "\n" + setup
    assert "create_tables.sql" in combined and "base de datos nueva" in combined.lower()
    assert "2026_09_catalog_asset_registry_r2.sql" in combined
    assert "2026_09_catalog_asset_registry_r2_cutover.sql" in combined
    assert PINNED_BATCH in combined
    assert "Gate 7A" in combined and "Gate 6" in combined
    for name in (
        "CATALOG_ASSET_STORAGE_PROVIDER",
        "CATALOG_ASSET_PUBLIC_BASE_URL",
        "CATALOG_ASSET_R2_ACCOUNT_ID",
        "CATALOG_ASSET_R2_ENDPOINT_URL",
        "CATALOG_ASSET_R2_ACCESS_KEY_ID",
        "CATALOG_ASSET_R2_SECRET_ACCESS_KEY",
        "CATALOG_ASSET_R2_SESSION_TOKEN",
        "CATALOG_ASSET_R2_BUCKET",
        "CATALOG_ASSET_R2_REGION",
    ):
        assert name in cloud
    assert "server-only" in cloud.lower()
    for document in (cloud, setup):
        assert "ejecuta únicamente el bootstrap, A y B canónicos" in document
        assert "SQL adicional" in document
        assert "proceso separado" in document
        assert "manual revisada" in document
