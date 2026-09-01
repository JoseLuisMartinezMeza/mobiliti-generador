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

CANONICAL_SQL_FINGERPRINTS = {
    "bootstrap": "87b18231146b888f5de1e595e37c99e902b35d06068b58c52ad7f08eccc3139e",
    "registry": "ee87c269bcc65d73c34b8b6a007a370502a74beb22f93dbab2744a5773d07288",
    "cutover": "77427d72daa998e6813c7713cf4a3b54f574b528677ccf97d07c70c773f587fc",
}


def tokenize_sql(sql: str) -> tuple[str, ...]:
    """Tokeniza SQL ignorando BOM, comentarios y whitespace fuera de strings."""
    tokens: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "\ufeff" or char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            index += 2
            depth = 1
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            continue
        if char in ("'", '"'):
            quote = char
            start = index
            index += 1
            while index < length:
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                if quote == "'" and sql[index] == "\\" and index + 1 < length:
                    index += 2
                else:
                    index += 1
            tokens.append(sql[start:index])
            continue
        if char == "$":
            delimiter_end = sql.find("$", index + 1)
            if delimiter_end >= 0:
                candidate = sql[index : delimiter_end + 1]
                tag = candidate[1:-1]
                if not tag or (tag[0].isalpha() or tag[0] == "_") and all(
                    part.isalnum() or part == "_" for part in tag
                ):
                    tokens.append(candidate)
                    index = delimiter_end + 1
                    continue
        if char.isalnum() or char == "_":
            start = index
            index += 1
            while index < length and (sql[index].isalnum() or sql[index] in "_$"):
                index += 1
            tokens.append(sql[start:index].lower())
            continue
        three = sql[index : index + 3]
        two = sql[index : index + 2]
        if three in ("->>", "#>>"):
            tokens.append(three)
            index += 3
        elif two in ("::", ":=", "<>", "<=", ">=", "!=", "||", "->", "#>", "=>"):
            tokens.append(two)
            index += 2
        else:
            tokens.append(char)
            index += 1
    return tuple(tokens)


def sql_token_fingerprint(tokens: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for token in tokens:
        encoded = token.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _contains_token_sequence(tokens: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    if not sequence or len(sequence) > len(tokens):
        return False
    width = len(sequence)
    return any(
        tokens[index : index + width] == sequence
        for index in range(len(tokens) - width + 1)
    )


BOOTSTRAP_SIGNATURES = tuple(tokenize_sql(signature) for signature in (
        "create table if not exists saas_usuarios",
        "create table if not exists saas_catalog_assets",
        "create or replace function saas_register_catalog_asset",
    ))
REGISTRY_SIGNATURES = tuple(tokenize_sql(signature) for signature in (
        "create table if not exists saas_catalog_asset_cutover_batches",
        "create table if not exists saas_catalog_asset_cutover_entries",
        "create or replace function saas_finalize_catalog_asset_cutover_batch",
    ))
CUTOVER_SIGNATURES = tuple(tokenize_sql(signature) for signature in (
        "create or replace function saas_clone_catalog_candidate_with_asset",
        "create or replace function saas_clone_catalog_candidate_with_image_metadata",
        "update public.saas_catalog_asset_cutover_batches set cutover_applied_at",
    ))


def classify_sql_tokens(tokens: tuple[str, ...]) -> frozenset[str]:
    """Clasifica contratos sensibles y marca cualquier variante no canónica."""
    fingerprint = sql_token_fingerprint(tokens)
    roles: set[str] = set()
    is_bootstrap = all(
        _contains_token_sequence(tokens, item) for item in BOOTSTRAP_SIGNATURES
    )
    if is_bootstrap:
        role = (
            "bootstrap"
            if fingerprint == CANONICAL_SQL_FINGERPRINTS["bootstrap"]
            else "bootstrap_modified"
        )
        return frozenset((role,))

    if all(_contains_token_sequence(tokens, item) for item in REGISTRY_SIGNATURES):
        roles.add(
            "registry"
            if fingerprint == CANONICAL_SQL_FINGERPRINTS["registry"]
            else "registry_modified"
        )
    if any(_contains_token_sequence(tokens, item) for item in CUTOVER_SIGNATURES):
        roles.add(
            "cutover"
            if fingerprint == CANONICAL_SQL_FINGERPRINTS["cutover"]
            else "cutover_unpinned"
        )
    return frozenset(roles)


def classify_sql_content(sql: str) -> frozenset[str]:
    return classify_sql_tokens(tokenize_sql(sql))


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
        tokens = tokenize_sql(text)
        fingerprint = sql_token_fingerprint(tokens)
        known_role = known_paths.get(path.resolve())
        if known_role:
            if fingerprint == CANONICAL_SQL_FINGERPRINTS[known_role]:
                roles.add(known_role)
            else:
                roles.add({
                    "bootstrap": "bootstrap_modified",
                    "registry": "registry_modified",
                    "cutover": "cutover_unpinned",
                }[known_role])
        roles.update(classify_sql_tokens(tokens))

    # Detecta también sentinelas repartidas entre varios archivos seleccionados.
    roles.update(classify_sql_content("\n".join(text for _, text in documents)))

    if args.bootstrap_new_project:
        if roles != {"bootstrap"}:
            parser.error("--bootstrap-new-project sólo permite el bootstrap canónico aislado")
        return

    has_registry = bool(roles & {"registry", "registry_modified"})
    has_cutover = bool(roles & {"cutover", "cutover_unpinned"})
    if has_registry and has_cutover:
        parser.error("las migraciones A y B nunca se aplican juntas, aunque estén copiadas o combinadas")
    if roles & {"bootstrap", "bootstrap_modified"}:
        parser.error("el contenido de create_tables.sql sólo se permite con --bootstrap-new-project")
    if "registry_modified" in roles:
        parser.error("el contenido de la migración A no coincide con su secuencia canónica")
    if "cutover_unpinned" in roles:
        parser.error("el contenido de la migración B no coincide con el cutover certificado canónico")
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
