# Project Persistence and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the server-side Project aggregate, durable imported assets, optimistic autosave API, and cross-catalog product search without changing quote generation yet.

**Architecture:** A new validated Project payload lives in `saas_projects` and is owned by one authenticated user. The three deployable FastAPI mirrors expose the same CRUD/search/import-promotion routes, while a focused `project_model.py` module owns graph and field validation. PostgreSQL/Supabase and the atomic local development store implement identical revision and idempotency semantics.

**Tech Stack:** Python 3.14, FastAPI, PostgreSQL/Supabase REST, JSON, pytest, Pillow-backed existing import previews.

## Global Constraints

- Projects are private per user; administrators do not receive implicit access to another user's Projects.
- Server persistence is authoritative; do not add offline or `localStorage` Project persistence.
- Archive and restore are allowed; do not add permanent Project deletion.
- Catalog prices, currencies, inventory, and images remain server-authoritative.
- Imported `official_code` and `provider` are explicit structured fields; never infer a code from the product name.
- Complement depth is exactly one; reject orphans, cycles, and complement parents.
- Keep the three API files byte-identical: `mobiliti_saas/web/api/index.py`, `mobiliti_saas/api/index.py`, and `vercel_deploy/api/index.py`.
- Preserve unrelated dirty-worktree changes and never use destructive Git cleanup.
- Use Spanish for business-facing validation messages.
- Do not modify SharePoint or deploy production.

---

### Task 1: Project payload validator

**Files:**
- Create: `mobiliti_saas/quote_engine/project_model.py`
- Modify: `mobiliti_saas/quote_engine/__init__.py`
- Create: `tests/project_fixtures.py`
- Test: `tests/test_project_model.py`

**Interfaces:**
- Consumes: plain decoded JSON objects.
- Produces: `normalize_project_payload(raw: object) -> dict`, `project_summary(payload: Mapping[str, object]) -> dict[str, int]`, `project_physical_line_count(payload: Mapping[str, object]) -> int`, and `normalized_match_key(provider: object, official_code: object) -> tuple[str, str] | None`.

- [ ] **Step 1: Write the failing validator tests**

```python
# tests/project_fixtures.py
def valid_project_payload():
    return {
        "schema_version": 1,
        "quote_fields": {
            "proyecto": "Oficinas",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "33",
            "direccion": "GDL",
            "razon_social": "Cliente SA",
            "quote_currency": "MXN",
            "descuento": "40",
        },
        "sections": [{"section_id": "section-1", "concept": "Recepción", "position": 0}],
        "lines": [
            {
                "line_id": "11111111-1111-4111-8111-111111111111",
                "role": "principal",
                "section_id": "section-1",
                "parent_line_id": None,
                "position": 0,
                "quantity": "10",
                "source": "catalog",
                "catalog": "sunon",
                "official_code": "CHAIR-1",
                "identity": {
                    "internal_id": "sunon:chair-1",
                    "base_option_id": "",
                    "add_on_option_ids": [],
                },
                "display_cache": {"name": "Silla", "code": "CHAIR-1", "image_url": ""},
            },
            {
                "line_id": "22222222-2222-4222-8222-222222222222",
                "role": "complement",
                "section_id": None,
                "parent_line_id": "11111111-1111-4111-8111-111111111111",
                "position": 0,
                "quantity": "1",
                "quantity_mode": "per_parent_unit",
                "source": "imported",
                "import_id": "33333333-3333-4333-8333-333333333333",
                "source_row": 14,
                "source_currency": "USD",
                "official_code": "HEAD-1",
                "provider": "Sunon",
                "name": "Cabecera",
                "description": "Cabecera",
                "dimension": "",
                "unit_price": "20.00",
                "image_asset_key": "",
                "source_asset_key": (
                    "projects/7/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/"
                    "sources/source.xlsx"
                ),
                "display_cache": {"name": "Cabecera", "code": "HEAD-1", "image_url": ""},
            },
        ],
    }
```

```python
# tests/test_project_model.py
from copy import deepcopy

import pytest

from project_fixtures import valid_project_payload
from mobiliti_saas.quote_engine.project_model import (
    normalize_project_payload,
    normalized_match_key,
    project_physical_line_count,
    project_summary,
)


def test_project_payload_accepts_one_level_and_counts_physical_rows():
    normalized = normalize_project_payload(valid_project_payload())
    assert normalized["lines"][1]["parent_line_id"] == normalized["lines"][0]["line_id"]
    assert project_summary(normalized) == {"sections": 1, "principals": 1, "complements": 1}
    assert project_physical_line_count(normalized) == 2


@pytest.mark.parametrize("mutation", ["duplicate", "orphan", "nested", "cycle"])
def test_project_payload_rejects_invalid_graph(mutation):
    payload = valid_project_payload()
    if mutation == "duplicate":
        payload["lines"][1]["line_id"] = payload["lines"][0]["line_id"]
    elif mutation == "orphan":
        payload["lines"][1]["parent_line_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif mutation == "nested":
        payload["lines"].append({
            **deepcopy(payload["lines"][1]),
            "line_id": "44444444-4444-4444-8444-444444444444",
            "parent_line_id": payload["lines"][1]["line_id"],
        })
    else:
        payload["lines"][0]["role"] = "complement"
        payload["lines"][0]["section_id"] = None
        payload["lines"][0]["parent_line_id"] = payload["lines"][1]["line_id"]
    with pytest.raises(ValueError):
        normalize_project_payload(payload)


def test_match_key_requires_both_provider_and_code():
    assert normalized_match_key("  CR Global ", " ab-12 ") == ("cr global", "AB-12")
    assert normalized_match_key("", "AB-12") is None
    assert normalized_match_key("CR Global", "") is None
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run:

```powershell
python -m pytest tests/test_project_model.py -q
```

Expected: collection fails with `ModuleNotFoundError: mobiliti_saas.quote_engine.project_model`.

- [ ] **Step 3: Implement the minimal validator**

```python
# mobiliti_saas/quote_engine/project_model.py
from __future__ import annotations

import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from typing import Mapping

PROJECT_SCHEMA_VERSION = 1
PROJECT_CURRENCIES = frozenset({"MXN", "USD", "EUR"})
PROJECT_ROLES = frozenset({"principal", "complement"})
COMPLEMENT_QUANTITY_MODES = frozenset({"per_parent_unit", "fixed_project"})
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value: object, field: str, *, required: bool = True, limit: int = 2000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} inválido")
    result = value.strip()
    if (required and not result) or len(result) > limit or CONTROL.search(result):
        raise ValueError(f"{field} inválido")
    return result


def _uuid(value: object, field: str) -> str:
    text = _text(value, field, limit=36)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise ValueError(f"{field} inválido") from exc
    if parsed.version != 4:
        raise ValueError(f"{field} inválido")
    return str(parsed)


def _positive_decimal(value: object, field: str) -> str:
    text = _text(value, field, limit=32)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} inválida") from exc
    if not number.is_finite() or number <= 0 or number > Decimal("1000000"):
        raise ValueError(f"{field} inválida")
    return format(number, "f")


def normalized_match_key(provider: object, official_code: object) -> tuple[str, str] | None:
    if not isinstance(provider, str) or not isinstance(official_code, str):
        return None
    clean_provider = " ".join(
        "".join(
            char for char in unicodedata.normalize("NFKD", provider)
            if not unicodedata.combining(char)
        ).casefold().split()
    )
    clean_code = official_code.strip().upper()
    return (clean_provider, clean_code) if clean_provider and clean_code else None


def normalize_project_payload(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "quote_fields", "sections", "lines"
    }:
        raise ValueError("Proyecto inválido")
    if raw["schema_version"] != PROJECT_SCHEMA_VERSION:
        raise ValueError("Versión de Proyecto no soportada")
    sections = _normalize_sections(raw["sections"])
    lines = _normalize_lines(raw["lines"], sections)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "quote_fields": _normalize_quote_fields(raw["quote_fields"]),
        "sections": sections,
        "lines": lines,
    }


def _normalize_sections(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Secciones inválidas")
    sections = []
    ids = set()
    positions = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"section_id", "concept", "position"}:
            raise ValueError("Sección inválida")
        section_id = _text(item["section_id"], "Sección", limit=64)
        position = item["position"]
        if section_id in ids or type(position) is not int or position < 0 or position in positions:
            raise ValueError("Sección duplicada")
        ids.add(section_id)
        positions.add(position)
        sections.append({
            "section_id": section_id,
            "concept": _text(item["concept"], "Concepto", limit=120),
            "position": position,
        })
    if sorted(positions) != list(range(len(sections))):
        raise ValueError("Orden de secciones inválido")
    return sorted(sections, key=lambda item: item["position"])


def _normalize_lines(raw: object, sections: list[dict]) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("Líneas inválidas")
    section_ids = {item["section_id"] for item in sections}
    ids = set()
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Línea inválida")
        line_id = _uuid(item.get("line_id"), "line_id")
        if line_id in ids:
            raise ValueError("line_id duplicado")
        ids.add(line_id)
        role = item.get("role")
        if role not in PROJECT_ROLES:
            raise ValueError("Rol de línea inválido")
        line = dict(item)
        line["line_id"] = line_id
        line["quantity"] = _positive_decimal(item.get("quantity"), "Cantidad")
        if role == "principal":
            if item.get("section_id") not in section_ids or item.get("parent_line_id") is not None:
                raise ValueError("Principal fuera de sección")
        else:
            if item.get("section_id") is not None or item.get("quantity_mode") not in COMPLEMENT_QUANTITY_MODES:
                raise ValueError("Complemento inválido")
        normalized.append(line)
    by_id = {item["line_id"]: item for item in normalized}
    for item in normalized:
        if item["role"] == "complement":
            parent = by_id.get(item.get("parent_line_id"))
            if not parent or parent["role"] != "principal":
                raise ValueError("Padre de complemento inválido")
    return normalized


def _normalize_quote_fields(raw: object) -> dict:
    required = (
        "proyecto", "cliente", "correo", "telefono", "direccion", "razon_social",
        "quote_currency", "descuento",
    )
    if not isinstance(raw, dict) or set(raw) != set(required):
        raise ValueError("Datos de cotización inválidos")
    result = {
        field: _text(raw[field], field, required=False, limit=500)
        for field in required
    }
    result["quote_currency"] = result["quote_currency"].upper()
    if result["quote_currency"] not in PROJECT_CURRENCIES:
        raise ValueError("Moneda inválida")
    try:
        discount = Decimal(result["descuento"])
    except InvalidOperation as exc:
        raise ValueError("Descuento inválido") from exc
    if not discount.is_finite() or not Decimal("0") <= discount <= Decimal("100"):
        raise ValueError("Descuento inválido")
    return result


def project_summary(payload: Mapping[str, object]) -> dict[str, int]:
    lines = payload["lines"]
    return {
        "sections": len(payload["sections"]),
        "principals": sum(item["role"] == "principal" for item in lines),
        "complements": sum(item["role"] == "complement" for item in lines),
    }


def project_physical_line_count(payload: Mapping[str, object]) -> int:
    return len(payload["lines"])
```

Before appending a normalized line, enforce these exact source contracts:

```python
COMMON_LINE_FIELDS = {
    "line_id", "role", "section_id", "parent_line_id", "position", "quantity",
    "source", "official_code", "display_cache", "quantity_mode",
}
CATALOG_LINE_FIELDS = COMMON_LINE_FIELDS | {
    "catalog", "identity", "quantity_rules_cache",
}
IMPORTED_LINE_FIELDS = COMMON_LINE_FIELDS | {
    "import_id", "source_row", "source_currency", "provider", "name",
    "description", "dimension", "unit_price", "image_asset_key",
    "source_asset_key",
}
```

For a catalog line, `source == "catalog"`, `catalog` is one of the seven mixed
catalogs, and `identity` must contain exactly the key family accepted by
`preflight_mixed_catalog_items`. For an imported line, `source == "imported"`,
`import_id` is UUID v4, `source_row` is a positive integer, currency is allowed,
`unit_price` is finite and nonnegative, and non-empty asset keys must match
`projects/{integer_user}/{uuid_project}/(sources|images)/{safe_name}` with no `..`,
backslash, URL scheme, or control character. `display_cache` is restricted to
`name`, `code`, and `image_url`; it is never copied into pricing fields.

Require `position` to be a nonnegative integer. Principal positions must be
contiguous within each section, and complement positions must be contiguous within
each parent. A principal must not contain `quantity_mode`; a complement must contain
it. Add parametrized tests for an unexpected field, a foreign asset key, duplicate
positions, a missing catalog identity field, a non-finite imported price, and an
Excel-formula-prefixed imported text.

Export the four public functions and constants from `mobiliti_saas/quote_engine/__init__.py`.

- [ ] **Step 4: Run the validator tests**

Run:

```powershell
python -m pytest tests/test_project_model.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the validator**

```powershell
git add mobiliti_saas/quote_engine/project_model.py mobiliti_saas/quote_engine/__init__.py tests/project_fixtures.py tests/test_project_model.py
git commit -m "feat: validate persistent project payloads"
```

### Task 2: PostgreSQL schema and bootstrap parity

**Files:**
- Create: `mobiliti_saas/supabase_setup/2026_07_projects.sql`
- Modify: `mobiliti_saas/supabase_setup/create_tables.sql`
- Modify: `mobiliti_saas/supabase_setup/README.md`
- Test: `tests/test_project_migrations.py`

**Interfaces:**
- Consumes: `saas_usuarios(id)`.
- Produces: table `saas_projects` and revision-safe storage columns used by Task 3.

- [ ] **Step 1: Write migration contract tests**

```python
# tests/test_project_migrations.py
import re
from pathlib import Path

MIGRATION = Path("mobiliti_saas/supabase_setup/2026_07_projects.sql")
BOOTSTRAP = Path("mobiliti_saas/supabase_setup/create_tables.sql")


def project_table_statement(sql):
    start = sql.index("CREATE TABLE IF NOT EXISTS saas_projects")
    return re.sub(r"\s+", " ", sql[start:sql.index(");", start) + 2]).strip()


def test_projects_migration_matches_bootstrap_and_is_service_role_only():
    migration = MIGRATION.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    assert project_table_statement(migration) == project_table_statement(bootstrap)
    for sql in (migration, bootstrap):
        normalized = re.sub(r"\s+", " ", sql.lower())
        assert "revision integer not null default 0 check (revision >= 0)" in normalized
        assert "status text not null default 'active' check (status in ('active', 'archived'))" in normalized
        assert "last_operation_id uuid" in normalized
        assert "alter table public.saas_projects enable row level security" in normalized
        assert "revoke all on table public.saas_projects from anon, authenticated" in normalized
        assert "grant all on table public.saas_projects to service_role" in normalized
        assert "delete cascade" not in project_table_statement(sql).lower()
```

- [ ] **Step 2: Run the test and verify the migration is absent**

Run:

```powershell
python -m pytest tests/test_project_migrations.py -q
```

Expected: fail because `2026_07_projects.sql` does not exist.

- [ ] **Step 3: Add the table to the migration and bootstrap**

Use this exact SQL in both files:

```sql
CREATE TABLE IF NOT EXISTS saas_projects (
    id UUID PRIMARY KEY,
    usuario_id BIGINT NOT NULL REFERENCES saas_usuarios(id),
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    payload JSONB NOT NULL,
    last_operation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_projects_user_status_updated
    ON saas_projects(usuario_id, status, updated_at DESC);

ALTER TABLE public.saas_projects ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.saas_projects FROM anon, authenticated;
GRANT ALL ON TABLE public.saas_projects TO service_role;
```

Document that the migration runs after `2026_06_quote_jobs.sql`.

- [ ] **Step 4: Run migration and catalog regression tests**

Run:

```powershell
python -m pytest tests/test_project_migrations.py tests/test_catalog_migrations.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the schema**

```powershell
git add mobiliti_saas/supabase_setup/2026_07_projects.sql mobiliti_saas/supabase_setup/create_tables.sql mobiliti_saas/supabase_setup/README.md tests/test_project_migrations.py
git commit -m "feat: add persistent projects schema"
```

### Task 3: Project repository in all storage modes

**Files:**
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Test: `tests/test_project_api.py`
- Test: `tests/test_quote_jobs_api.py`

**Interfaces:**
- Consumes: normalized payloads from `normalize_project_payload`.
- Produces: `db_create_project`, `db_get_project`, `db_list_projects`, `db_save_project`, and `db_set_project_status`.

- [ ] **Step 1: Write failing repository tests**

```python
# tests/test_project_api.py
from copy import deepcopy

from mobiliti_saas.web.api import index
from project_fixtures import valid_project_payload


def test_dev_project_save_is_revision_safe_and_idempotent(monkeypatch):
    state = {"projects": []}
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: deepcopy(state))
    monkeypatch.setattr(index, "_dev_save", lambda data: (state.clear(), state.update(deepcopy(data))))
    created = index.db_create_project(7, "Oficinas", valid_project_payload())
    saved = index.db_save_project(
        created["id"], 7, "Oficinas GDL", valid_project_payload(),
        expected_revision=0,
        operation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    retried = index.db_save_project(
        created["id"], 7, "Oficinas GDL", valid_project_payload(),
        expected_revision=0,
        operation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    stale = index.db_save_project(
        created["id"], 7, "Viejo", valid_project_payload(),
        expected_revision=0,
        operation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    assert saved["revision"] == 1
    assert retried == saved
    assert stale == {}
    assert index.db_get_project(created["id"], 8) is None
```

- [ ] **Step 2: Run the repository test**

Run:

```powershell
python -m pytest tests/test_project_api.py::test_dev_project_save_is_revision_safe_and_idempotent -q
```

Expected: fail because `db_create_project` is undefined.

- [ ] **Step 3: Implement repository functions and local-store defaults**

Add `"projects": []` to `_dev_load()` initialization and add these exact public
signatures:

- `db_create_project(usuario_id: int, name: str, payload: dict) -> dict`
- `db_get_project(project_id: str, usuario_id: int) -> dict | None`
- `db_list_projects(usuario_id: int, status: str) -> list[dict]`
- `db_save_project(project_id: str, usuario_id: int, name: str, payload: dict, *,
  expected_revision: int, operation_id: str) -> dict`
- `db_set_project_status(project_id: str, usuario_id: int, status: str, *,
  expected_revision: int, operation_id: str) -> dict`

Every returned row must contain `id`, `usuario_id`, `name`, `status`, `revision`,
`schema_version`, `payload`, `last_operation_id`, `created_at`, `updated_at`, and
`archived_at`, with a computed `summary = project_summary(payload)`.

The DEV branch must perform this exact sequence while holding the existing
development-store lock:

1. load the complete store;
2. find by both `id` and `usuario_id`;
3. return the current row immediately when `last_operation_id == operation_id`;
4. return `{}` when the owned row exists but `revision != expected_revision`;
5. deep-copy the normalized payload, increment revision once, set the operation ID
   and timestamps, and save once;
6. return a deep copy of the saved row.

`db_set_project_status` additionally sets `archived_at` to the current UTC timestamp
for `archived`, clears it for `active`, and rejects every other status.

The PostgreSQL update must use:

```sql
UPDATE saas_projects
SET name = %s,
    payload = %s,
    revision = revision + 1,
    last_operation_id = %s,
    updated_at = %s
WHERE id = %s
  AND usuario_id = %s
  AND revision = %s
RETURNING *
```

The Supabase REST branch must filter on `id`, `usuario_id`, and `revision`. Before
returning conflict, re-read the row and return it only when `last_operation_id`
equals the retry's operation ID.

Edit `mobiliti_saas/web/api/index.py` as the canonical mirror, then run:

```powershell
Copy-Item -LiteralPath 'mobiliti_saas/web/api/index.py' -Destination 'mobiliti_saas/api/index.py' -Force
Copy-Item -LiteralPath 'mobiliti_saas/web/api/index.py' -Destination 'vercel_deploy/api/index.py' -Force
```

Immediately run the SHA-256 parity test before making any further API edit.

- [ ] **Step 4: Run repository and mirror tests**

Run:

```powershell
python -m pytest tests/test_project_api.py tests/test_quote_jobs_api.py::test_deployable_api_copies_have_identical_sha256 -q
```

Expected: all tests pass and the API hashes are identical.

- [ ] **Step 5: Commit repository support**

```powershell
git add mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py tests/test_project_api.py tests/test_quote_jobs_api.py
git commit -m "feat: persist revisioned projects"
```

### Task 4: Project CRUD, archive, restore, and duplicate routes

**Files:**
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Test: `tests/test_project_api.py`

**Interfaces:**
- Consumes: repository functions from Task 3 and JWT user ownership.
- Produces: `/projects` routes with consistent `409` conflict responses.

- [ ] **Step 1: Write failing route tests**

```python
def test_project_routes_are_user_scoped_and_archive_without_delete(monkeypatch):
    client = _project_client(monkeypatch, user_id=7)
    created = client.post(
        "/projects",
        headers=_auth_headers(7),
        json={"name": "Oficinas", "payload": valid_project_payload()},
    )
    assert created.status_code == 201
    project = created.json()["project"]
    assert client.get(f"/projects/{project['id']}", headers=_auth_headers(8)).status_code == 404
    archived = client.post(
        f"/projects/{project['id']}/archive",
        headers=_auth_headers(7),
        json={
            "expected_revision": project["revision"],
            "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    )
    assert archived.status_code == 200
    assert archived.json()["project"]["status"] == "archived"
    assert all("DELETE" not in route.methods for route in index.app.routes if route.path.startswith("/projects"))


def test_project_patch_returns_current_revision_on_conflict(monkeypatch):
    client, project = _created_project(monkeypatch)
    response = client.patch(
        f"/projects/{project['id']}",
        headers=_auth_headers(7),
        json={
            "name": "Cambio",
            "payload": valid_project_payload(),
            "expected_revision": 99,
            "operation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_revision_conflict"
    assert response.json()["detail"]["project"]["revision"] == 0
```

- [ ] **Step 2: Run the new route tests**

Run:

```powershell
python -m pytest tests/test_project_api.py -k "routes or conflict" -q
```

Expected: fail with `404` because `/projects` routes do not exist.

- [ ] **Step 3: Add exact route contracts**

Add:

```python
@app.post("/projects", status_code=201)
def projects_create(body: dict, current_user: dict = Depends(get_current_user)):
    _require_active_subscription(current_user["id"])
    unexpected = set(body) - {"name", "payload"}
    if unexpected:
        raise HTTPException(400, f"Campo de Proyecto no permitido: {min(unexpected)}")
    name = _project_name(body.get("name"))
    payload = normalize_project_payload(body.get("payload"))
    return {"project": db_create_project(current_user["id"], name, payload)}


@app.get("/projects")
def projects_list(status: str = "active", current_user: dict = Depends(get_current_user)):
    if status not in {"active", "archived"}:
        raise HTTPException(400, "Estado de Proyecto inválido")
    return {"projects": db_list_projects(current_user["id"], status)}


@app.get("/projects/{project_id}")
def projects_get(project_id: str, current_user: dict = Depends(get_current_user)):
    project = db_get_project(_project_uuid(project_id), current_user["id"])
    if not project:
        raise HTTPException(404, "Proyecto no encontrado")
    return {"project": project}
```

`PATCH`, archive, restore, and duplicate must call the Task 3 repository and return
this exact conflict shape:

```python
raise HTTPException(
    409,
    detail={"code": "project_revision_conflict", "project": current},
)
```

Duplicate creates a new UUID, name `"{old_name} (copia)"`, revision `0`, status
`active`, and a deep-copied normalized payload.

- [ ] **Step 4: Run all Project API tests and API parity**

Run:

```powershell
python -m pytest tests/test_project_api.py tests/test_quote_jobs_api.py::test_deployable_api_copies_have_identical_sha256 -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Project routes**

```powershell
git add mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py tests/test_project_api.py
git commit -m "feat: expose project lifecycle api"
```

### Task 5: Cross-catalog Project picker search

**Files:**
- Create: `mobiliti_saas/quote_engine/catalog_search.py`
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Test: `tests/test_project_catalog_search.py`
- Test: `tests/test_project_api.py`

**Interfaces:**
- Consumes: the existing seven catalog loaders and their published snapshots.
- Produces: `search_catalog_products(catalogs, query, supplier, offset, limit) -> dict` and `GET /catalogs/search`.

- [ ] **Step 1: Write failing normalized-search tests**

```python
# tests/test_project_catalog_search.py
from mobiliti_saas.quote_engine.catalog_search import search_catalog_products


def test_search_returns_canonical_references_without_commercial_client_fields():
    result = search_catalog_products(
        {
            "sunon": {"items": [{
                "internal_id": "sunon:olive",
                "sku": "OLIVE-II",
                "name": "Olive II Chair",
                "image_url": "https://assets.example/olive.png",
                "price_net": "100",
                "base_currency": "USD",
            }]}
        },
        query="olive",
        supplier=None,
        offset=0,
        limit=20,
    )
    assert result["total"] == 1
    assert result["items"][0]["catalog"] == "sunon"
    assert result["items"][0]["official_code"] == "OLIVE-II"
    assert result["items"][0]["identity"]["internal_id"] == "sunon:olive"
    assert "price_net" not in result["items"][0]
    assert "base_currency" not in result["items"][0]
```

- [ ] **Step 2: Run the search test**

Run:

```powershell
python -m pytest tests/test_project_catalog_search.py -q
```

Expected: fail because `catalog_search.py` does not exist.

- [ ] **Step 3: Implement the focused search adapter**

```python
# mobiliti_saas/quote_engine/catalog_search.py
from __future__ import annotations

import unicodedata


def _fold(value: object) -> str:
    return " ".join(
        "".join(
            char for char in unicodedata.normalize("NFKD", str(value or ""))
            if not unicodedata.combining(char)
        ).casefold().split()
    )


def _catalog_identity(catalog: str, raw: dict) -> dict:
    if catalog == "tarkett":
        return {"code": str(raw.get("code") or "").strip()}
    if catalog == "offiho":
        return {
            "inventory_key": str(
                raw.get("inventory_key") or raw.get("code") or ""
            ).strip()
        }
    return {
        "internal_id": str(raw.get("internal_id") or "").strip(),
        "base_option_id": "",
        "add_on_option_ids": [],
    }


def _availability_label(raw: dict) -> str:
    if raw.get("is_out_of_stock"):
        return "Agotado"
    if raw.get("availability_type") == "made_to_order":
        return str(raw.get("lead_time") or "Fabricación por confirmar")
    stock = raw.get("available_quantity", raw.get("stock"))
    if stock is not None:
        return f"Existencia: {stock}"
    return "Disponibilidad por confirmar"


def search_catalog_products(catalogs, *, query, supplier, offset, limit):
    needle = _fold(query)
    rows = []
    for catalog, snapshot in catalogs.items():
        if supplier and catalog != supplier:
            continue
        for raw in snapshot.get("items", []):
            code = str(raw.get("code") or raw.get("sku") or raw.get("internal_id") or "").strip()
            haystack = _fold(f"{code} {raw.get('name', '')} {raw.get('description', '')}")
            if needle and needle not in haystack:
                continue
            identity = _catalog_identity(catalog, raw)
            rows.append({
                "catalog": catalog,
                "official_code": code,
                "identity": identity,
                "snapshot": {
                    "name": str(raw.get("name") or code),
                    "code": code,
                    "image_url": str(raw.get("image_url") or ""),
                    "availability": _availability_label(raw),
                    "configuration": "",
                    "warnings": [str(value) for value in raw.get("warnings", [])],
                },
            })
    rows.sort(key=lambda item: (item["catalog"], item["official_code"], item["snapshot"]["name"]))
    return {"items": rows[offset:offset + limit], "total": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None}
```

Before returning a result, pass
`{"catalog": catalog, **identity, "quantity": "1"}` through
`preflight_mixed_catalog_items` so a selector can never emit an identity that the
mixed resolver rejects. Do not include price, currency, stock counts, product URLs,
or source-reference URLs in the search response; `snapshot.image_url` remains the
only URL because it is required for the visual preview.

Expose:

```text
GET /catalogs/search?q=&supplier=&offset=0&limit=20
```

with `limit` constrained to `1..50`.

- [ ] **Step 4: Run search, API, and mirror tests**

Run:

```powershell
python -m pytest tests/test_project_catalog_search.py tests/test_project_api.py -k "catalog_search" -q
python -m pytest tests/test_quote_jobs_api.py::test_deployable_api_copies_have_identical_sha256 -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit catalog search**

```powershell
git add mobiliti_saas/quote_engine/catalog_search.py mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py tests/test_project_catalog_search.py tests/test_project_api.py
git commit -m "feat: search catalogs for project editing"
```

### Task 6: Promote imported Quotation resources into a Project

**Files:**
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Modify: `mobiliti_saas/quote_engine/quotation_import.py`
- Test: `tests/test_project_import_assets.py`
- Test: `tests/test_quotation_import.py`

**Interfaces:**
- Consumes: one owned draft import job with a validated manifest and preview paths.
- Produces: `POST /projects/{project_id}/imports/{job_id}` and durable `projects/{user}/{project}/...` object keys.

- [ ] **Step 1: Write the failing promotion test**

```python
# tests/test_project_import_assets.py
def test_import_promotion_copies_source_manifest_and_images_without_consuming_job(
    monkeypatch, tmp_path
):
    client, token, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    prefix = f"projects/7/{project['id']}/"
    assert body["source_asset_key"].startswith(prefix + "sources/")
    assert all(path.startswith(prefix + "images/") for path in body["image_asset_keys"].values())
    assert job["status"] == "draft"
    assert storage[body["source_asset_key"]] == storage[job["input_path"]]
```

- [ ] **Step 2: Run the promotion test**

Run:

```powershell
python -m pytest tests/test_project_import_assets.py -q
```

Expected: `404` because the promotion route does not exist.

- [ ] **Step 3: Implement bounded copy and structured code fields**

Extend import manifest items with:

```python
{
    "official_code": str(raw_code or "").strip(),
    "provider": str(provider or "").strip(),
}
```

Do not derive `official_code` from `name`.

The route must:

```python
project = db_get_project(project_id, current_user["id"])
if not project:
    raise HTTPException(404, "Fuente importada no encontrada")
manifest, job, source = _validated_import_source(
    current_user["id"], [{"import_id": job_id}]
)
prefix = f"projects/{current_user['id']}/{project_id}"
source_key = f"{prefix}/sources/{manifest['source_hash']}.xlsx"
_storage_upload_bytes(
    source_key,
    source,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
preview_paths = _quote_job_metadata(job)["import_preview_paths"]
image_asset_keys = {}
for row_text, preview_path in sorted(preview_paths.items(), key=lambda item: int(item[0])):
    content = _storage_download_bytes(preview_path)
    image_key = (
        f"{prefix}/images/{manifest['source_hash'][:16]}-row-{int(row_text)}.png"
    )
    _storage_upload_bytes(image_key, content, "image/png")
    image_asset_keys[row_text] = image_key
```

Before copying, reject any source over the existing 25 MB limit and any image over
the existing per-image limit. Return exactly
`{"source_asset_key": source_key, "image_asset_keys": image_asset_keys,
"manifest": manifest}`. Do not call `_consume_import_draft`; promotion preserves the
draft import job.

- [ ] **Step 4: Run import and API parity tests**

Run:

```powershell
python -m pytest tests/test_project_import_assets.py tests/test_quotation_import.py tests/test_quote_jobs_api.py::test_deployable_api_copies_have_identical_sha256 -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit durable imported assets**

```powershell
git add mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py mobiliti_saas/quote_engine/quotation_import.py tests/test_project_import_assets.py tests/test_quotation_import.py
git commit -m "feat: preserve imported quotation assets in projects"
```

### Task 7: Persistence/API regression gate

**Files:**
- Modify: `mobiliti_saas/README.md`
- Modify: `mobiliti_saas/worker/README.md`
- Test: `tests/test_project_api.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: a documented, independently usable Project CRUD API ready for the web editor.

- [ ] **Step 1: Add one end-to-end API persistence test**

```python
def test_project_survives_new_client_session(monkeypatch):
    first = _project_client(monkeypatch, user_id=7)
    created = first.post(
        "/projects",
        headers=_auth_headers(7),
        json={"name": "Persistente", "payload": valid_project_payload()},
    ).json()["project"]
    second = index.TestClient(index.app)
    reopened = second.get(f"/projects/{created['id']}", headers=_auth_headers(7))
    assert reopened.status_code == 200
    assert reopened.json()["project"]["payload"] == valid_project_payload()
```

- [ ] **Step 2: Run the focused full gate before documentation**

Run:

```powershell
python -m pytest tests/test_project_model.py tests/test_project_migrations.py tests/test_project_api.py tests/test_project_catalog_search.py tests/test_project_import_assets.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Document exact local endpoints and non-goals**

Add to both READMEs:

```markdown
## Proyectos persistentes

Los Proyectos se guardan por usuario mediante `/projects`, usan revisión optimista y
se archivan sin eliminación permanente. `GET /catalogs/search` alimenta el selector
unificado. Importar una Quotation a un Proyecto promueve su fuente e imágenes a
recursos durables del Proyecto. Esta fase no cambia todavía el motor XLSX.
```

- [ ] **Step 4: Run API mirrors and broader regression**

Run:

```powershell
python -m pytest tests/test_quote_jobs_api.py tests/test_catalog_migrations.py tests/test_quotation_import.py -q
npm --prefix mobiliti_saas/web run build
```

Expected: pytest passes and Vite finishes with `built in`.

- [ ] **Step 5: Commit the persistence/API milestone**

```powershell
git add mobiliti_saas/README.md mobiliti_saas/worker/README.md tests/test_project_api.py
git commit -m "docs: describe persistent project api"
```
