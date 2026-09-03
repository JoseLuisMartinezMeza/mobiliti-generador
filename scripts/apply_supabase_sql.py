"""
Apply Mobiliti Supabase SQL migrations using DATABASE_URL.

Safe defaults:
- dry-run unless --apply is passed
- never prints DATABASE_URL
- uses psycopg only when applying
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_DIR = ROOT / "mobiliti_saas" / "supabase_setup"
BOOTSTRAP_SQL = SETUP_DIR / "create_tables.sql"
REGISTRY_MIGRATION_SQL = SETUP_DIR / "2026_09_catalog_asset_registry_r2.sql"
CUTOVER_MIGRATION_SQL = SETUP_DIR / "2026_09_catalog_asset_registry_r2_cutover.sql"
PINNED_CUTOVER_BATCH = "470442fc-3dc3-5948-b0e4-1dd34c1fcd30"

CANONICAL_SQL_PATHS = {
    "bootstrap": BOOTSTRAP_SQL,
    "registry": REGISTRY_MIGRATION_SQL,
    "cutover": CUTOVER_MIGRATION_SQL,
}
CANONICAL_SQL_SHA256 = {
    "bootstrap": "2aa02145abc088bd865e9e3c0e0890b2e47c0bdc04ffcfbcc098a3aa8fd69502",
    "registry": "6cfe4f9d129d1e0409845c1c29666ed6217d684832312f3c0e4f4240b600159f",
    "cutover": "2648632327b5b4df8efe4097b8e2d181ec4fc6863727eec1ff12b31b8a12f5e7",
}


def canonical_sql_role(path: Path) -> str | None:
    """Acepta sólo el spelling absoluto o relativo documentado, nunca aliases."""
    for role, canonical in CANONICAL_SQL_PATHS.items():
        if path in (canonical, canonical.relative_to(ROOT)):
            return role
    return None


def sql_text_sha256(sql: str) -> str:
    normalized = sql.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_sql_documents(paths: list[Path]) -> list[tuple[Path, str]]:
    documents = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"SQL vacio: {path}")
        documents.append((path, text))
    return documents


def render_sql_documents(documents: list[tuple[Path, str]]) -> str:
    parts = [f"-- file: {path}\n{text}" for path, text in documents]
    return "\n\n".join(parts)


def load_sql(paths: list[Path]) -> str:
    return render_sql_documents(load_sql_documents(paths))


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
    if len(files) != 1:
        parser.error("--file acepta exactamente una migración canónica por ejecución")

    role = canonical_sql_role(files[0])
    if role not in ("registry", "cutover"):
        parser.error("--file sólo acepta la ruta canónica exacta de la migración A o B")
    if role == "cutover":
        if args.confirm_cutover_batch != PINNED_CUTOVER_BATCH:
            parser.error(
                "la migración B requiere --confirm-cutover-batch con el UUID certificado exacto"
            )
    elif args.confirm_cutover_batch:
        parser.error("--confirm-cutover-batch sólo se usa con la migración B")
    return [CANONICAL_SQL_PATHS[role]]


def validate_sql_selection(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    documents: list[tuple[Path, str]],
) -> None:
    """Verifica que el único documento sea el artefacto canónico intacto."""
    if len(documents) != 1:
        parser.error("se requiere exactamente un documento SQL canónico")
    path, text = documents[0]
    role = canonical_sql_role(path)
    expected_role = "bootstrap" if args.bootstrap_new_project else canonical_sql_role(
        list(args.file or [Path()])[0]
    )
    if (
        role is None
        or role != expected_role
        or path.is_symlink()
        or (not args.bootstrap_new_project and role not in ("registry", "cutover"))
    ):
        parser.error("la ruta SQL no es el artefacto canónico seleccionado")
    if sql_text_sha256(text) != CANONICAL_SQL_SHA256[role]:
        parser.error("el contenido SQL canónico fue modificado")
    if role == "cutover" and args.confirm_cutover_batch != PINNED_CUTOVER_BATCH:
        parser.error("la migración B no tiene la confirmación exacta del batch certificado")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = resolve_sql_paths(args, parser)
    documents = load_sql_documents(paths)
    validate_sql_selection(args, parser, documents)
    sql = render_sql_documents(documents)
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
