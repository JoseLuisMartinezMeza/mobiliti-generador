"""
Apply Mobiliti Supabase SQL migrations using DATABASE_URL.

Safe defaults:
- dry-run unless --apply is passed
- never prints DATABASE_URL
- uses psycopg only when applying
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_DIR = ROOT / "mobiliti_saas" / "supabase_setup"
BOOTSTRAP_SQL = SETUP_DIR / "create_tables.sql"
REGISTRY_MIGRATION_SQL = SETUP_DIR / "2026_09_catalog_asset_registry_r2.sql"
CUTOVER_MIGRATION_SQL = SETUP_DIR / "2026_09_catalog_asset_registry_r2_cutover.sql"
PINNED_CUTOVER_BATCH = "470442fc-3dc3-5948-b0e4-1dd34c1fcd30"


def load_sql(paths: list[Path]) -> str:
    parts = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"SQL vacio: {path}")
        parts.append(f"-- file: {path}\n{text}")
    return "\n\n".join(parts)


def summarize_sql(sql: str) -> dict:
    lowered = sql.lower()
    return {
        "chars": len(sql),
        "create_table": lowered.count("create table"),
        "create_index": lowered.count("create index"),
        "insert": lowered.count("insert into"),
        "storage_bucket": "storage.buckets" in lowered,
        "quote_jobs": "saas_quote_jobs" in lowered,
    }


def require_database_url(env: dict[str, str]) -> str:
    value = env.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("Falta DATABASE_URL")
    if "[YOUR_PASSWORD]" in value or "[PROJECT_REF]" in value:
        raise RuntimeError("DATABASE_URL contiene placeholder")
    return value


def apply_sql(database_url: str, sql: str) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Instala psycopg primero: pip install psycopg[binary]") from exc

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aplica SQL Mobiliti en Supabase")
    parser.add_argument("--file", action="append", type=Path, help="Migración SQL explícita")
    parser.add_argument(
        "--bootstrap-new-project",
        action="store_true",
        help="Usa create_tables.sql sólo para una base de datos nueva",
    )
    parser.add_argument(
        "--confirm-cutover-batch",
        help="UUID exacto requerido al seleccionar la migración de cutover",
    )
    parser.add_argument("--apply", action="store_true", help="Ejecuta SQL. Sin esto solo dry-run.")
    return parser


def resolve_sql_paths(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[Path]:
    files = list(args.file or [])
    if args.bootstrap_new_project:
        if files:
            parser.error("--bootstrap-new-project no se combina con --file")
        if args.confirm_cutover_batch:
            parser.error("el bootstrap no acepta confirmación de cutover")
        return [BOOTSTRAP_SQL]
    if not files:
        parser.error("elige --file o --bootstrap-new-project")

    resolved = [path.resolve() for path in files]
    bootstrap = BOOTSTRAP_SQL.resolve()
    registry = REGISTRY_MIGRATION_SQL.resolve()
    cutover = CUTOVER_MIGRATION_SQL.resolve()
    if bootstrap in resolved:
        parser.error("create_tables.sql sólo se permite con --bootstrap-new-project")

    includes_registry = registry in resolved
    includes_cutover = cutover in resolved
    if includes_registry and includes_cutover:
        parser.error("las migraciones A y B nunca se aplican juntas")
    if includes_cutover:
        if args.confirm_cutover_batch != PINNED_CUTOVER_BATCH:
            parser.error(
                "la migración B requiere --confirm-cutover-batch con el UUID certificado exacto"
            )
    elif args.confirm_cutover_batch:
        parser.error("--confirm-cutover-batch sólo se usa con la migración B")
    return files


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = resolve_sql_paths(args, parser)
    sql = load_sql(paths)
    summary = summarize_sql(sql)

    print("SQL listo:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if not args.apply:
        print("Dry-run. Usa --apply para ejecutar contra DATABASE_URL.")
        return

    database_url = require_database_url(os.environ)
    apply_sql(database_url, sql)
    print("Migracion aplicada.")


if __name__ == "__main__":
    main()
