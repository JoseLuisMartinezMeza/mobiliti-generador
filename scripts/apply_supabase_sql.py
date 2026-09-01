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

BOOTSTRAP_SENTINELS = (
    "create table if not exists saas_usuarios",
    "create table if not exists saas_catalog_assets",
    "create or replace function saas_register_catalog_asset",
)
REGISTRY_SENTINELS = (
    "create table if not exists saas_catalog_asset_cutover_batches",
    "create table if not exists saas_catalog_asset_cutover_entries",
    "create or replace function saas_finalize_catalog_asset_cutover_batch",
)
CUTOVER_STRUCTURE_SENTINELS = (
    "from public.saas_catalog_asset_cutover_batches",
    "catalog asset r2 cutover manifest is not verified",
    "create or replace function saas_clone_catalog_candidate_with_asset",
    "create or replace function saas_clone_catalog_candidate_with_image_metadata",
)
CUTOVER_PIN_SENTINELS = (
    PINNED_CUTOVER_BATCH,
    "93e30738942bc0c4b85d85d63239c82588ec1d163c5c3820ef2de01dc07caeb7",
    "72ecc6b84bfec9ba012a24dea9c5bcdf6d1beaad8d81c68eb4697f8e83e188ff",
    "expected_count = 2214",
    "verified_count = 2214",
)


def classify_sql_content(sql: str) -> frozenset[str]:
    """Clasifica SQL sensible mediante sentinelas estables, sin depender de su ruta."""
    normalized = " ".join(sql.lower().replace("\ufeff", "").split())
    roles: set[str] = set()
    is_bootstrap = all(marker in normalized for marker in BOOTSTRAP_SENTINELS)
    if is_bootstrap:
        roles.add("bootstrap")
    elif all(marker in normalized for marker in REGISTRY_SENTINELS):
        roles.add("registry")
    if all(marker in normalized for marker in CUTOVER_STRUCTURE_SENTINELS):
        if all(marker in normalized for marker in CUTOVER_PIN_SENTINELS):
            roles.add("cutover")
        else:
            roles.add("cutover_unpinned")
    return frozenset(roles)


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

    return files


def validate_sql_selection(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    documents: list[tuple[Path, str]],
) -> None:
    """Aplica las barreras A/B/bootstrap a rutas conocidas y a su contenido real."""
    roles: set[str] = set()
    known_paths = {
        BOOTSTRAP_SQL.resolve(): "bootstrap",
        REGISTRY_MIGRATION_SQL.resolve(): "registry",
        CUTOVER_MIGRATION_SQL.resolve(): "cutover",
    }
    for path, text in documents:
        known_role = known_paths.get(path.resolve())
        if known_role:
            roles.add(known_role)
        roles.update(classify_sql_content(text))

    # Detecta también sentinelas repartidas entre varios archivos seleccionados.
    roles.update(classify_sql_content("\n".join(text for _, text in documents)))

    if args.bootstrap_new_project:
        if roles != {"bootstrap"}:
            parser.error("--bootstrap-new-project sólo permite el bootstrap canónico aislado")
        return

    if "bootstrap" in roles:
        parser.error("el contenido de create_tables.sql sólo se permite con --bootstrap-new-project")
    if "cutover_unpinned" in roles:
        parser.error("el contenido de la migración B no conserva el batch y digests certificados")
    if "registry" in roles and "cutover" in roles:
        parser.error("las migraciones A y B nunca se aplican juntas, aunque estén copiadas o combinadas")
    if "cutover" in roles:
        if args.confirm_cutover_batch != PINNED_CUTOVER_BATCH:
            parser.error(
                "la migración B requiere --confirm-cutover-batch con el UUID certificado exacto"
            )
    elif args.confirm_cutover_batch:
        parser.error("--confirm-cutover-batch sólo se usa con la migración B")


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
