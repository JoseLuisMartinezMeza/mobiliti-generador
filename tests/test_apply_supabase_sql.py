import importlib.util
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


def test_sql_tokenizer_ignores_layout_comments_but_preserves_string_contents():
    left = "\ufeff SELECT '-- not a comment', public . asset, '/* literal */';"
    right = "select '-- not a comment', public/* ignored */.asset,'/* literal */'; -- ignored\n"

    assert apply_supabase_sql.tokenize_sql(left) == apply_supabase_sql.tokenize_sql(right)


PINNED_BATCH = "470442fc-3dc3-5948-b0e4-1dd34c1fcd30"
SETUP = Path("mobiliti_saas/supabase_setup")
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
        apply_supabase_sql.main(["--file", str(SETUP / "create_tables.sql")])
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


def test_runner_rejects_bootstrap_content_copied_to_another_path(tmp_path):
    copied_bootstrap = tmp_path / "harmless_name.sql"
    copied_bootstrap.write_text(
        (SETUP / "create_tables.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        apply_supabase_sql.main(["--file", str(copied_bootstrap)])

    assert error.value.code == 2


def test_runner_pins_cutover_content_copied_to_another_path(tmp_path, capsys):
    copied_cutover = tmp_path / "renamed.sql"
    copied_cutover.write_text(
        "-- copied for an audited run\r\n"
        + MIGRATION_B.read_text(encoding="utf-8").replace("\n", "\r\n"),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        apply_supabase_sql.main(["--file", str(copied_cutover)])
    with pytest.raises(SystemExit):
        apply_supabase_sql.main([
            "--file", str(copied_cutover), "--confirm-cutover-batch", "wrong",
        ])

    apply_supabase_sql.main([
        "--file", str(copied_cutover),
        "--confirm-cutover-batch", PINNED_BATCH,
    ])
    assert "Dry-run" in capsys.readouterr().out


def test_runner_accepts_cutover_with_comments_and_whitespace_between_tokens(tmp_path, capsys):
    formatted_cutover = tmp_path / "formatted-cutover.sql"
    formatted_cutover.write_text(
        MIGRATION_B.read_text(encoding="utf-8").replace(
            "public.saas_catalog_asset_cutover_batches",
            "public /* audited formatting */ .\n saas_catalog_asset_cutover_batches",
        ),
        encoding="utf-8",
    )

    apply_supabase_sql.main([
        "--file", str(formatted_cutover),
        "--confirm-cutover-batch", PINNED_BATCH,
    ])

    assert "Dry-run" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("AND missing_count = 0", "AND missing_count = 1"),
        ("AND failed_count = 0", "AND failed_count = 1"),
        ("AND verified_at IS NOT NULL", "AND verified_at IS NULL"),
    ),
)
def test_runner_rejects_cutover_with_any_certification_guard_changed(
    tmp_path, old, new,
):
    altered_cutover = tmp_path / "altered-guard.sql"
    original = MIGRATION_B.read_text(encoding="utf-8")
    assert old in original
    altered_cutover.write_text(original.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(SystemExit):
        apply_supabase_sql.main([
            "--file", str(altered_cutover),
            "--confirm-cutover-batch", PINNED_BATCH,
        ])


def test_runner_ignores_expected_uuid_when_it_only_appears_in_a_comment(tmp_path):
    altered_cutover = tmp_path / "comment-does-not-pin.sql"
    active_uuid = "11111111-1111-1111-1111-111111111111"
    altered_cutover.write_text(
        f"-- expected batch was {PINNED_BATCH}\n"
        + MIGRATION_B.read_text(encoding="utf-8").replace(PINNED_BATCH, active_uuid),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        apply_supabase_sql.main([
            "--file", str(altered_cutover),
            "--confirm-cutover-batch", PINNED_BATCH,
        ])


def test_runner_rejects_modified_content_even_when_document_uses_canonical_b_path():
    parser = apply_supabase_sql.build_parser()
    args = parser.parse_args([
        "--file", str(MIGRATION_B),
        "--confirm-cutover-batch", PINNED_BATCH,
    ])

    with pytest.raises(SystemExit):
        apply_supabase_sql.validate_sql_selection(
            args,
            parser,
            [(MIGRATION_B, "select current_date;\n")],
        )


def test_runner_rejects_structural_cutover_update_without_canonical_b(tmp_path):
    structural_cutover = tmp_path / "looks-arbitrary.sql"
    structural_cutover.write_text(
        """
        UPDATE public /* spacing cannot hide the target */ .
               saas_catalog_asset_cutover_batches
        SET cutover_applied_at = now()
        WHERE batch_id = '11111111-1111-1111-1111-111111111111';
        """,
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        apply_supabase_sql.main(["--file", str(structural_cutover)])


def test_runner_rejects_cutover_shaped_content_with_altered_pin(tmp_path):
    altered_cutover = tmp_path / "altered-cutover.sql"
    altered_cutover.write_text(
        MIGRATION_B.read_text(encoding="utf-8").replace(
            PINNED_BATCH,
            "11111111-1111-1111-1111-111111111111",
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        apply_supabase_sql.main(["--file", str(altered_cutover)])

    assert error.value.code == 2


def test_runner_rejects_a_and_b_combined_inside_one_file(tmp_path):
    combined = tmp_path / "combined.sql"
    combined.write_text(
        MIGRATION_A.read_text(encoding="utf-8")
        + "\n\n"
        + MIGRATION_B.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        apply_supabase_sql.main(["--file", str(combined)])

    assert error.value.code == 2


def test_runner_allows_copied_a_and_unrelated_sql(tmp_path, capsys):
    copied_registry = tmp_path / "registry-copy.sql"
    copied_registry.write_text(MIGRATION_A.read_text(encoding="utf-8"), encoding="utf-8")
    unrelated = tmp_path / "operator-maintenance.sql"
    unrelated.write_text("select current_date;\n", encoding="utf-8")

    apply_supabase_sql.main(["--file", str(copied_registry)])
    apply_supabase_sql.main(["--file", str(unrelated)])

    assert capsys.readouterr().out.count("Dry-run") == 2


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
