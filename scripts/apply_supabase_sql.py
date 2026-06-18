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
DEFAULT_SQL = ROOT / "mobiliti_saas" / "supabase_setup" / "create_tables.sql"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Aplica SQL Mobiliti en Supabase")
    parser.add_argument("--file", action="append", type=Path, help="SQL file. Default: create_tables.sql")
    parser.add_argument("--apply", action="store_true", help="Ejecuta SQL. Sin esto solo dry-run.")
    args = parser.parse_args()

    paths = args.file or [DEFAULT_SQL]
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
