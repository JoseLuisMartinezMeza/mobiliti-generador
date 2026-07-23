# Project Quote Composition and Excel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an immutable quote from a saved Project revision so every component is a separate official `Mobiliti` row while each principal becomes one composed `Cotizacion` line with live price formulas, `+` descriptions, and a bounded image montage.

**Architecture:** A pure Project projection calculates physical component quantities and exact commercial price terms. The existing mixed-catalog resolver remains authoritative for catalog prices, currencies, stock, and images, but its payload order becomes occurrence-based through `line_id`. The worker passes the frozen Project context to the existing official OOXML engine; `Mobiliti` keeps one row per component and only the `Cotizacion` projection groups principals with their direct complements.

**Tech Stack:** Python 3.14, FastAPI, `Decimal`, Pillow, openpyxl for inspection only, the existing OOXML package/composer, pytest, Microsoft Excel COM validation on Windows.

## Global Constraints

- Input prices remain costs; official `Mobiliti` formulas remain the only markup engine.
- Each component is converted to the quote currency exactly once before entering `Mobiliti`.
- The commercial discount is applied once in `Cotizacion`, with later rows referencing the first product discount cell.
- `line_id` identifies an occurrence; `canonical_key` continues to identify the catalog product used for resolution and reservation.
- Duplicate canonical products are allowed when their occurrence `line_id` values differ.
- `Quotation` imported from the supplier remains byte-for-byte represented by the existing transplant path; do not rebuild its visible sheet.
- Protected template parts, hidden sheets, defined names, links, drawings, and fixed values remain governed by the official template contract.
- Keep `POST /catalogs/mixed-quote` working for legacy callers.
- Do not add the former 16-section, 33-product, or 32-section UI limits.
- Do not publish SharePoint changes or deploy production.

---

### Task 1: Pure Project-to-quote component projection

**Files:**
- Create: `mobiliti_saas/quote_engine/project_quote.py`
- Modify: `mobiliti_saas/quote_engine/__init__.py`
- Test: `tests/test_project_quote.py`

**Interfaces:**
- Consumes: a payload already returned by `normalize_project_payload`.
- Produces: `ProjectComponent`, `ProjectComposition`, `project_quote_projection(payload)`, and `project_context(payload, project_id, project_revision)`.

- [ ] **Step 1: Write failing quantity and composition tests**

```python
# tests/test_project_quote.py
from decimal import Decimal

from project_fixtures import valid_project_payload
from mobiliti_saas.quote_engine.project_quote import project_quote_projection


def test_project_projection_keeps_physical_rows_and_exact_price_ratios():
    payload = valid_project_payload()
    principal = payload["lines"][0]
    principal["quantity"] = "10"
    per_unit = payload["lines"][1]
    per_unit["quantity"] = "2"
    payload["lines"].append({
        **per_unit,
        "line_id": "44444444-4444-4444-8444-444444444444",
        "quantity_mode": "fixed_project",
        "quantity": "3",
        "official_code": "FIXED-1",
    })

    projection = project_quote_projection(payload)

    assert [item.physical_quantity for item in projection.components] == [
        Decimal("10"), Decimal("20"), Decimal("3")
    ]
    composition = projection.compositions[0]
    assert [
        (term.line_id, term.numerator, term.denominator)
        for term in composition.price_terms
    ] == [
        (principal["line_id"], Decimal("1"), Decimal("1")),
        (per_unit["line_id"], Decimal("2"), Decimal("1")),
        (
            "44444444-4444-4444-8444-444444444444",
            Decimal("3"),
            Decimal("10"),
        ),
    ]
```

- [ ] **Step 2: Run the test and verify the module is absent**

Run:

```powershell
python -m pytest tests/test_project_quote.py -q
```

Expected: collection fails with `ModuleNotFoundError` for `project_quote`.

- [ ] **Step 3: Implement the exact projection types and rules**

```python
# mobiliti_saas/quote_engine/project_quote.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Mapping

from .project_model import normalize_project_payload


@dataclass(frozen=True)
class ProjectPriceTerm:
    line_id: str
    numerator: Decimal
    denominator: Decimal


@dataclass(frozen=True)
class ProjectComponent:
    line_id: str
    principal_line_id: str
    section_id: str
    physical_quantity: Decimal
    role: str


@dataclass(frozen=True)
class ProjectComposition:
    principal_line_id: str
    section_id: str
    component_line_ids: tuple[str, ...]
    price_terms: tuple[ProjectPriceTerm, ...]


@dataclass(frozen=True)
class ProjectQuoteProjection:
    components: tuple[ProjectComponent, ...]
    compositions: tuple[ProjectComposition, ...]


def project_quote_projection(payload: Mapping[str, object]) -> ProjectQuoteProjection:
    checked = normalize_project_payload(dict(payload))
    by_parent: dict[str, list[dict]] = {}
    for line in checked["lines"]:
        if line["role"] == "complement":
            by_parent.setdefault(line["parent_line_id"], []).append(line)
    components: list[ProjectComponent] = []
    compositions: list[ProjectComposition] = []
    principals = sorted(
        (line for line in checked["lines"] if line["role"] == "principal"),
        key=lambda line: (
            next(section["position"] for section in checked["sections"]
                 if section["section_id"] == line["section_id"]),
            line["position"],
        ),
    )
    for principal in principals:
        principal_quantity = Decimal(principal["quantity"])
        children = sorted(
            by_parent.get(principal["line_id"], []),
            key=lambda line: line["position"],
        )
        terms = [ProjectPriceTerm(principal["line_id"], Decimal("1"), Decimal("1"))]
        ordered_ids = [principal["line_id"]]
        components.append(ProjectComponent(
            principal["line_id"], principal["line_id"], principal["section_id"],
            principal_quantity, "principal",
        ))
        for child in children:
            child_quantity = Decimal(child["quantity"])
            physical = (
                principal_quantity * child_quantity
                if child["quantity_mode"] == "per_parent_unit"
                else child_quantity
            )
            numerator, denominator = (
                (child_quantity, Decimal("1"))
                if child["quantity_mode"] == "per_parent_unit"
                else (child_quantity, principal_quantity)
            )
            ordered_ids.append(child["line_id"])
            terms.append(ProjectPriceTerm(child["line_id"], numerator, denominator))
            components.append(ProjectComponent(
                child["line_id"], principal["line_id"], principal["section_id"],
                physical, "complement",
            ))
        compositions.append(ProjectComposition(
            principal["line_id"], principal["section_id"],
            tuple(ordered_ids), tuple(terms),
        ))
    return ProjectQuoteProjection(tuple(components), tuple(compositions))


def project_context(payload, project_id: str, project_revision: int) -> dict:
    checked = normalize_project_payload(payload)
    projection = project_quote_projection(checked)
    canonical = json.dumps(
        checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "project_id": project_id,
        "project_revision": project_revision,
        "project_payload_hash": hashlib.sha256(canonical).hexdigest(),
        "normalized_project_payload": checked,
        "compositions": [
            {
                "principal_line_id": item.principal_line_id,
                "section_id": item.section_id,
                "component_line_ids": list(item.component_line_ids),
                "price_terms": [
                    {
                        "line_id": term.line_id,
                        "numerator": format(term.numerator, "f"),
                        "denominator": format(term.denominator, "f"),
                    }
                    for term in item.price_terms
                ],
            }
            for item in projection.compositions
        ],
    }
```

Export these public types and functions from `mobiliti_saas/quote_engine/__init__.py`.

- [ ] **Step 4: Run projection and Project validator tests**

Run:

```powershell
python -m pytest tests/test_project_quote.py tests/test_project_model.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the pure projection**

```powershell
git add mobiliti_saas/quote_engine/project_quote.py mobiliti_saas/quote_engine/__init__.py tests/test_project_quote.py
git commit -m "feat: project quote component projection"
```

### Task 2: Occurrence-based mixed payload with legacy compatibility

**Files:**
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py`
- Modify: `mobiliti_saas/quote_engine/quotation_sheets.py`
- Test: `tests/test_mixed_catalog_cart.py`
- Test: `tests/test_mixed_catalog_workbook.py`
- Test: `tests/test_quotation_data_sheet.py`

**Interfaces:**
- Consumes: optional browser `line_id`, either legacy `item_keys` sections or new `line_ids` sections, and optional `project_context`.
- Produces: a validated payload whose resolved items and presentation order are keyed by unique occurrence.

- [ ] **Step 1: Write failing duplicate-occurrence and legacy tests**

```python
def test_same_canonical_product_can_appear_twice_with_distinct_line_ids(catalogs, rates):
    rows = [
        {
            "line_id": "11111111-1111-4111-8111-111111111111",
            "catalog": "sunon",
            "internal_id": "sunon:olive",
            "quantity": "1",
        },
        {
            "line_id": "22222222-2222-4222-8222-222222222222",
            "catalog": "sunon",
            "internal_id": "sunon:olive",
            "quantity": "2",
        },
    ]
    payload = build_mixed_catalog_cart_payload(
        rows,
        catalogs=catalogs,
        rate_rows=rates,
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": "section-1",
            "title": "Recepción",
            "line_ids": [row["line_id"] for row in rows],
        }],
    )
    items = payload["groups"][0]["items"]
    assert [item["line_id"] for item in items] == [row["line_id"] for row in rows]
    assert items[0]["canonical_key"] == items[1]["canonical_key"]
    assert payload["sections"][0]["line_ids"] == [row["line_id"] for row in rows]


def test_legacy_request_without_line_ids_still_builds_payload(catalogs, rates):
    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "tarkett", "code": "25731726", "quantity": "1"}],
        catalogs=catalogs,
        rate_rows=rates,
        quote_currency="MXN",
        commercial_discount_percent="40",
    )
    assert payload["groups"][0]["items"][0]["line_id"] == "legacy-1"
```

Add a `quotation_data_rows` test asserting two equal `canonical_key` values create two
rows because `item_key` is now the distinct `line_id`.

- [ ] **Step 2: Run the occurrence tests**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py tests/test_quotation_data_sheet.py -k "occurrence or legacy_request" -q
```

Expected: duplicate canonical keys are rejected and the new section field is rejected.

- [ ] **Step 3: Separate occurrence identity from catalog identity**

Make these schema changes:

```python
MIXED_LINE_FIELDS = MIXED_LINE_FIELDS | {"line_id"}
MIXED_IMPORTED_LINE_FIELDS = MIXED_IMPORTED_LINE_FIELDS | {
    "line_id", "official_code", "image_asset_key", "source_asset_key"
}
MIXED_SECTION_FIELDS = frozenset({"id", "title", "line_ids"})
MIXED_PAYLOAD_FIELDS = frozenset({
    "source_type", "quote_currency", "created_at", "groups", "imported_source",
    "sections", "item_count", "auto_electrification_rate", "rate_summary",
    "project_context",
})
```

Change browser allowlists so `line_id` is optional for every catalog family. After
`preflight_mixed_catalog_items`, assign `legacy-{index}` only when a legacy row omits
it. Reject duplicate `line_id`, but never reject duplicate `mixed_cart_key`.

Extend `_normalize_imported_source` in the same way: preserve a supplied Project
`line_id`, assign `legacy-import-{index}` for a legacy imported item, and copy the
resolved occurrence ID into the normalized imported line. Validate uniqueness across
catalog and imported occurrences together.

`_common_line` and `_supplier_line` must copy `raw["line_id"]` into the resolved line
while preserving the existing `canonical_key`. `_normalize_presentation_sections`
must:

1. accept new `line_ids`;
2. accept legacy `item_keys` only at the request boundary;
3. map legacy keys to the one matching occurrence and reject ambiguous duplicate
   canonical keys;
4. emit only canonical `line_ids`;
5. compare flattened order as a list and as a set so no occurrence is missing or
   repeated.

Always emit `"project_context": None` for legacy calls. When supplied, validate the
exact fields produced by Task 1 and verify that every component and price term refers
to one payload `line_id`.

In `create_mixed_catalog_quotation_workbook`, replace:

```python
items_by_key = {item["canonical_key"]: item for item in all_items}
```

with:

```python
items_by_line_id = {item["line_id"]: item for item in all_items}
```

Write `line_id` into the existing hidden `Canonical Key` technical column so the
parser/`Quotation_Data` handoff uses occurrence identity. Keep the actual
`canonical_key` in payload metadata for catalog audit and reservation aggregation.

- [ ] **Step 4: Run mixed payload, workbook, and reservation regressions**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_workbook.py tests/test_quotation_data_sheet.py -q
```

Expected: all tests pass, including existing legacy mixed-cart requests and aggregated
reservations for duplicate canonical products.

- [ ] **Step 5: Commit occurrence-based payloads**

```powershell
git add mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/quote_engine/quotation_sheets.py tests/test_mixed_catalog_cart.py tests/test_mixed_catalog_workbook.py tests/test_quotation_data_sheet.py
git commit -m "feat: preserve project occurrences in mixed quotes"
```

### Task 3: Generate an immutable quote job from a saved Project revision

**Files:**
- Modify: `mobiliti_saas/web/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `vercel_deploy/api/index.py`
- Test: `tests/test_project_quote_api.py`
- Test: `tests/test_quote_jobs_api.py`

**Interfaces:**
- Consumes: `POST /projects/{project_id}/quote` with `expected_revision`.
- Produces: one immutable JSON source, one quote job, and metadata pinned to Project and template revisions.

- [ ] **Step 1: Write failing endpoint tests**

```python
# tests/test_project_quote_api.py
def test_project_quote_uses_saved_revision_and_does_not_mutate_project(project_client):
    client, headers, project, storage = project_client
    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )
    assert response.status_code == 202
    body = response.json()
    job = body["job"]
    frozen = json.loads(storage[job["input_path"]])
    assert job["metadata"]["project_id"] == project["id"]
    assert job["metadata"]["project_revision"] == project["revision"]
    assert frozen["project_context"]["project_revision"] == project["revision"]
    assert client.get(
        f"/projects/{project['id']}", headers=headers
    ).json()["project"]["revision"] == project["revision"]


def test_project_quote_rejects_stale_revision_before_creating_job(project_client):
    client, headers, project, _storage = project_client
    before = len(client.get("/cotizaciones", headers=headers).json())
    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"] + 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_revision_conflict"
    assert len(client.get("/cotizaciones", headers=headers).json()) == before
```

- [ ] **Step 2: Run endpoint tests**

Run:

```powershell
python -m pytest tests/test_project_quote_api.py -q
```

Expected: `404` because the Project quote route does not exist.

- [ ] **Step 3: Build and upload the frozen authoritative payload**

Add:

```python
@app.post("/projects/{project_id}/quote", status_code=202)
def quote_saved_project(
    project_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    project = db_get_project(_project_uuid(project_id), current_user["id"])
    if not project:
        raise HTTPException(404, "Proyecto no encontrado")
    expected = _nonnegative_int(body.get("expected_revision"), "expected_revision")
    if project["status"] != "active":
        raise HTTPException(409, "Restaura el Proyecto antes de cotizar")
    if project["revision"] != expected:
        raise HTTPException(409, detail={
            "code": "project_revision_conflict",
            "project": project,
        })
    payload = _build_saved_project_quote_payload(project, current_user["id"])
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    validate_quote_size(
        section_counts=[len(section["line_ids"]) for section in payload["sections"]],
        encoded_bytes=len(encoded),
    )
    quote_metadata = _validate_metadata({
        **project["payload"]["quote_fields"],
        "image_provider": "pillow",
        "template": "Formato Cotizacion 2026 GDL (1).xlsx",
    })
    contract_hash = load_template_contract(
        OFFICIAL_TEMPLATE_CONTRACT_PATH
    ).sha256
    job = _enqueue_mixed_payload(
        current_user=current_user,
        cart_payload=payload,
        template="Formato Cotizacion 2026 GDL (1).xlsx",
        import_job=None,
        import_source_bytes=_project_import_source_bytes(payload),
        metadata={
            **quote_metadata,
            "source_type": "mixed_catalog_cart",
            "original_filename": f"project-{project_id}-r{expected}.json",
            "input_extension": ".json",
            "project_id": project_id,
            "project_revision": expected,
            "project_payload_hash": payload["project_context"]["project_payload_hash"],
            "catalog_source_hashes": {
                group["catalog"]: group["catalog_source_hash"]
                for group in payload["groups"]
            },
            "template_contract_hash": contract_hash,
            "rate_summary": payload["rate_summary"],
        },
    )
    return {"mensaje": "Cotización del Proyecto en cola", "job": job}
```

`_build_saved_project_quote_payload` must:

1. normalize the saved payload and calculate Task 1 projection;
2. load the same seven authoritative catalog snapshots used by
   `/catalogs/mixed-quote`;
3. convert each catalog occurrence to the current browser-row contract with its
   projected physical quantity and `line_id`;
4. normalize imported occurrences from their controlled Project asset keys;
5. call `build_mixed_catalog_cart_payload` exactly once;
6. attach `project_context` before final validation;
7. pass `quote_fields` through the existing `_validate_metadata` so an editable
   Project may contain blanks but a generated quote may not;
8. reject unresolved references and capacity errors before creating a job.

Import `OFFICIAL_TEMPLATE_CONTRACT_PATH` from the existing engine module and
`load_template_contract` from `official_template.py`; never hardcode the template
hash.

Extract the current reserve/upload/queue/rollback transaction from
`mixed_catalog_quote` into:

```python
def _enqueue_mixed_payload(
    *,
    current_user: dict,
    cart_payload: dict,
    template: str,
    metadata: dict,
    import_job: dict | None,
    import_source_bytes: bytes | None,
) -> dict:
```

Both `/catalogs/mixed-quote` and `/projects/{id}/quote` must call this one helper.
Keep the existing order: create job, copy and hash-check imported source, reserve
stock, apply the reservation snapshot, revalidate the payload, upload JSON, consume
the import only for the legacy draft flow, then queue. On any exception run the
existing import restore, reservation release, job failure, and object cleanup path.
Project-owned import assets are copied into the job but are not consumed or changed.

Implement `_project_import_source_bytes` by collecting non-empty
`source_asset_key` values from imported lines in
`payload["project_context"]["normalized_project_payload"]`. Return `None` when there
are none, download and return the one controlled object when there is exactly one,
and reject more than one distinct imported Quotation with
`"El Proyecto contiene más de una Quotation de origen"`. Verify the downloaded
SHA-256 against the imported manifest before job creation. This preserves the current
single-Quotation workbook contract without silently choosing one source.

Use `mobiliti_saas/web/api/index.py` as the canonical API mirror, then copy that exact
file to `mobiliti_saas/api/index.py` and `vercel_deploy/api/index.py`. Verify all
three SHA-256 hashes before committing.

- [ ] **Step 4: Run endpoint, legacy route, and mirror parity tests**

Run:

```powershell
python -m pytest tests/test_project_quote_api.py tests/test_quote_jobs_api.py -k "project_quote or mixed_quote or deployable_api_copies" -q
```

Expected: all tests pass, legacy `/catalogs/mixed-quote` still returns its existing
contract, and all API hashes are identical.

- [ ] **Step 5: Commit the Project quote endpoint**

```powershell
git add mobiliti_saas/web/api/index.py mobiliti_saas/api/index.py vercel_deploy/api/index.py tests/test_project_quote_api.py tests/test_quote_jobs_api.py
git commit -m "feat: enqueue immutable project quote revisions"
```

### Task 4: Worker handoff of Project context

**Files:**
- Modify: `mobiliti_saas/worker/quote_worker.py`
- Modify: `mobiliti_saas/worker/README.md`
- Test: `tests/test_quote_worker.py`

**Interfaces:**
- Consumes: a validated mixed payload with optional `project_context`.
- Produces: official generator metadata carrying the same immutable context.

- [ ] **Step 1: Write failing worker handoff tests**

```python
def test_worker_passes_validated_project_context_to_official_engine(monkeypatch, tmp_path):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-project/input.json"
    payload = _valid_mixed_worker_payload()
    project = valid_project_payload()
    project["lines"] = project["lines"][:1]
    line_id = project["lines"][0]["line_id"]
    payload["groups"][0]["items"][0]["line_id"] = line_id
    payload["sections"][0] = {
        "id": "section-1", "title": "Recepción", "line_ids": [line_id]
    }
    payload["project_context"] = project_context(project, PROJECT_ID, 3)
    client.input_content = json.dumps(payload).encode("utf-8")
    seen = {}

    def fake_convert(_source, output, _payload):
        _write_minimal_parser_xlsx(output)

    def fake_generator(job, _input_path, output_path):
        seen.update(job["metadata"]["project_context"])
        output_path.write_bytes(b"output")

    monkeypatch.setattr(
        quote_worker, "_convert_mixed_catalog_cart_to_quotation", fake_convert
    )
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)
    quote_worker.process_job(client, {
        "id": "job-project",
        "usuario_id": 7,
        "input_path": client.claim_input_path,
        "metadata": {
            "source_type": "mixed_catalog_cart",
            "input_extension": ".json",
            "project_id": PROJECT_ID,
            "project_revision": 3,
        },
    })
    assert seen["project_id"] == payload["project_context"]["project_id"]
    assert seen["project_revision"] == payload["project_context"]["project_revision"]
    assert seen["compositions"] == payload["project_context"]["compositions"]
```

Add a rejection test for a component ID absent from the resolved mixed payload.

- [ ] **Step 2: Run the worker tests**

Run:

```powershell
python -m pytest tests/test_quote_worker.py -k "project_context" -q
```

Expected: context is absent from generator metadata or invalid references are not
rejected.

- [ ] **Step 3: Preserve the validated context through conversion**

After `validate_mixed_catalog_payload(payload)` and canonical row creation, add:

```python
project_context = deepcopy(payload.get("project_context"))
if project_context is not None:
    metadata["project_context"] = project_context
    metadata["project_id"] = project_context["project_id"]
    metadata["project_revision"] = project_context["project_revision"]
    metadata["project_payload_hash"] = project_context["project_payload_hash"]
```

Do not read current Project state in the worker. The downloaded JSON source is the
immutable authority. Do not reconstruct or recalculate exchange rates.

- [ ] **Step 4: Run worker and mixed conversion regressions**

Run:

```powershell
python -m pytest tests/test_quote_worker.py tests/test_mixed_catalog_workbook.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the worker handoff**

```powershell
git add mobiliti_saas/worker/quote_worker.py mobiliti_saas/worker/README.md tests/test_quote_worker.py
git commit -m "feat: hand off immutable project composition"
```

### Task 5: Exact live formulas for composed `Cotizacion` prices

**Files:**
- Modify: `mobiliti_saas/quote_engine/official_composer.py`
- Test: `tests/test_official_composer.py`

**Interfaces:**
- Consumes: one or more `CotizacionPriceTerm` values per visible product.
- Produces: a live `F` formula referencing official `Mobiliti!X` cells with exact numerator/denominator factors.

- [ ] **Step 1: Write failing formula tests**

```python
def test_composed_product_formula_uses_exact_mobiliti_terms():
    terms = (
        CotizacionPriceTerm(14, Decimal("1"), Decimal("1")),
        CotizacionPriceTerm(15, Decimal("2"), Decimal("1")),
        CotizacionPriceTerm(16, Decimal("3"), Decimal("10")),
    )
    formulas = CotizacionFormulaContract().product_formulas(
        price_terms=terms,
        target_row=17,
    )
    assert formulas == {
        "F": "=Mobiliti!X14+Mobiliti!X15*2+Mobiliti!X16*3/10",
        "I": "=F17-H17",
    }
```

Add a compatibility test showing a product created only with `mobiliti_row=14`
still produces `=Mobiliti!X14`.

- [ ] **Step 2: Run formula tests**

Run:

```powershell
python -m pytest tests/test_official_composer.py -k "composed_product_formula or formula_contract" -q
```

Expected: `CotizacionPriceTerm` is undefined.

- [ ] **Step 3: Add typed rational price terms**

```python
@dataclass(frozen=True)
class CotizacionPriceTerm:
    mobiliti_row: int
    numerator: Decimal = Decimal("1")
    denominator: Decimal = Decimal("1")

    def __post_init__(self):
        numerator = _decimal(self.numerator, "numerador")
        denominator = _decimal(self.denominator, "denominador")
        if not 1 <= self.mobiliti_row <= XLSX_MAX_ROWS:
            raise ValueError("Fila Mobiliti del término inválida")
        if numerator <= 0 or denominator <= 0:
            raise ValueError("Factor de precio compuesto inválido")
        object.__setattr__(self, "numerator", numerator)
        object.__setattr__(self, "denominator", denominator)
```

Add `price_terms: tuple[CotizacionPriceTerm, ...] = ()` to `CotizacionProduct`. In
`__post_init__`, normalize an empty tuple to
`(CotizacionPriceTerm(self.mobiliti_row),)` for existing callers. Reject duplicate
Mobiliti rows inside one composed product.

Change the formula contract to:

```python
def product_formulas(
    self,
    *,
    target_row: int,
    price_terms: tuple[CotizacionPriceTerm, ...],
) -> dict[str, str]:
    parts = []
    for term in price_terms:
        expression = f"Mobiliti!X{term.mobiliti_row}"
        if term.numerator != 1:
            expression += f"*{_excel_decimal(term.numerator)}"
        if term.denominator != 1:
            expression += f"/{_excel_decimal(term.denominator)}"
        parts.append(expression)
    return {"F": "=" + "+".join(parts), "I": f"=F{target_row}-H{target_row}"}
```

Validate the resulting formula tokens so operands are limited to the declared
`Mobiliti!X` rows and positive numeric constants, with only `+`, `*`, and `/`
operators. Keep the existing `I`, `H`, `J`, and first-discount-cell behavior.

- [ ] **Step 4: Run all official composer tests**

Run:

```powershell
python -m pytest tests/test_official_composer.py -q
```

Expected: all tests pass and existing one-row products retain their former formulas.

- [ ] **Step 5: Commit composed formulas**

```powershell
git add mobiliti_saas/quote_engine/official_composer.py tests/test_official_composer.py
git commit -m "feat: compose live cotizacion price formulas"
```

### Task 6: Bounded principal-and-thumbnail image montage

**Files:**
- Modify: `mobiliti_saas/quote_engine/image_processing.py`
- Test: `tests/test_image_processing.py`

**Interfaces:**
- Consumes: one optional principal image and ordered complement image bytes.
- Produces: one normalized PNG no larger than `1200 × 900` and 8 MB.

- [ ] **Step 1: Write failing montage layout tests**

```python
def test_product_montage_keeps_main_dominant_and_orders_thumbnails():
    main = solid_png((255, 0, 0), (800, 800))
    blue = solid_png((0, 0, 255), (300, 300))
    green = solid_png((0, 255, 0), (300, 300))
    payload = compose_product_montage(main, [blue, green])
    image = Image.open(BytesIO(payload)).convert("RGB")
    assert image.size == (1200, 900)
    assert image.getpixel((350, 450))[0] > 200
    assert image.getpixel((970, 250))[2] > 200
    assert image.getpixel((970, 650))[1] > 200
    assert len(payload) <= MAX_MONTAGE_BYTES


def test_product_montage_returns_none_when_no_valid_image_exists():
    assert compose_product_montage(None, []) is None
```

- [ ] **Step 2: Run montage tests**

Run:

```powershell
python -m pytest tests/test_image_processing.py -k "montage" -q
```

Expected: `compose_product_montage` is undefined.

- [ ] **Step 3: Implement deterministic bounded composition**

Add:

```python
MONTAGE_SIZE = (1200, 900)
MAX_MONTAGE_IMAGES = 9
MAX_MONTAGE_SOURCE_BYTES = 8 * 1024 * 1024
MAX_MONTAGE_BYTES = 8 * 1024 * 1024


def compose_product_montage(
    principal: bytes | None,
    complements: list[bytes],
) -> bytes | None:
    sources = [value for value in [principal, *complements] if value]
    if not sources:
        return None
    if len(sources) > MAX_MONTAGE_IMAGES:
        raise ValueError("La composición excede el máximo de imágenes")
    decoded = [_decode_bounded_montage_image(value) for value in sources]
    canvas = Image.new("RGB", MONTAGE_SIZE, "white")
    main_box = (40, 40, 880, 860) if len(decoded) > 1 else (40, 40, 1160, 860)
    _paste_contained(canvas, decoded[0], main_box)
    if len(decoded) > 1:
        available = 820 // (len(decoded) - 1)
        for index, image in enumerate(decoded[1:]):
            top = 40 + index * available
            _paste_contained(canvas, image, (920, top, 1160, top + available - 12))
    output = BytesIO()
    canvas.save(output, "PNG", optimize=True)
    payload = output.getvalue()
    if len(payload) > MAX_MONTAGE_BYTES:
        raise ValueError("La composición de imágenes excede el límite")
    return payload
```

`_decode_bounded_montage_image` must reject empty, oversized, unsupported, animated,
or decompression-bomb inputs using the same image limits already enforced by the
official composer. `_paste_contained` must preserve aspect ratio and never upscale
past the target box.

- [ ] **Step 4: Run image processing and composer image tests**

Run:

```powershell
python -m pytest tests/test_image_processing.py tests/test_official_composer.py -k "image or montage" -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit montage support**

```powershell
git add mobiliti_saas/quote_engine/image_processing.py tests/test_image_processing.py
git commit -m "feat: compose bounded project product images"
```

### Task 7: Project composition in the official engine

**Files:**
- Modify: `mobiliti_saas/quote_engine/engine.py`
- Modify: `mobiliti_saas/quote_engine/official_composer.py`
- Test: `tests/test_project_quote_engine.py`
- Test: `tests/test_mixed_quote_engine.py`

**Interfaces:**
- Consumes: `_OfficialPresentationLine` rows bound by occurrence `item_key` plus validated `metadata["project_context"]`.
- Produces: unchanged component rows in `Mobiliti` and grouped principals in `Cotizacion`.

- [ ] **Step 1: Write failing engine projection test**

```python
def test_official_engine_separates_mobiliti_and_composes_cotizacion(
    project_quote_fixture, official_template, tmp_path
):
    output = generate_project_quote(
        project_quote_fixture,
        official_template,
        tmp_path / "project.xlsx",
    )
    with ZipFile(output) as package:
        mobiliti = worksheet_xml(package, "Mobiliti")
        cotizacion = worksheet_xml(package, "Cotizacion")
    assert mobiliti_text_rows(mobiliti, "Código de Producto") == [
        "MAIN-1", "PER-1", "FIXED-1"
    ]
    assert cotizacion_product_names(cotizacion) == ["MAIN-1"]
    assert cotizacion_cell_text(cotizacion, "C17") == (
        "Principal\n+ Complemento por unidad\n+ Complemento fijo"
    )
    assert cotizacion_formula(cotizacion, "F17") == (
        "=Mobiliti!X14+Mobiliti!X15*2+Mobiliti!X16*3/10"
    )
```

Also assert the physical `Mobiliti` quantities are `10`, `20`, and `3`.

- [ ] **Step 2: Run the new engine test**

Run:

```powershell
python -m pytest tests/test_project_quote_engine.py -q
```

Expected: `Cotizacion` still contains one row per component.

- [ ] **Step 3: Add one Project-aware projection without changing `Mobiliti`**

Keep `_build_official_mobiliti` input unchanged. Add:

```python
def _project_cotizacion_sections(
    lines: Sequence[_OfficialPresentationLine],
    mobiliti: MobilitiSheetMutation,
    metadata: dict[str, Any],
) -> tuple[CotizacionSection, ...]:
    context = metadata.get("project_context")
    if context is None:
        return _legacy_cotizacion_sections(lines, mobiliti, metadata)
    by_key = {line.item_key: line for line in lines}
    target_by_key = dict(zip(
        (line.item_key for line in lines),
        mobiliti.row_map.item_rows,
        strict=True,
    ))
    # Iterate context["compositions"] in saved section/principal order.
```

For every composition:

- obtain the principal and children by exact `line_id`;
- build `description` as principal description followed by each non-empty child
  description prefixed with `+ `;
- set the visible quantity to the principal physical quantity;
- map every context price term to `CotizacionPriceTerm(target_row, numerator,
  denominator)`;
- create the montage from the principal `image_content` and ordered child images;
- use the principal `line_id` as the unique `CotizacionProduct.item_key`;
- use the principal name and dimensions;
- apply the current commercial discount once.

Reject unknown, repeated, missing, cross-section, or unconsumed component IDs. A
Project context must consume every frozen Project line exactly once. Existing
engine-generated Lumbro electrification accessories are not user-created Project
complements; keep their current `parent_item_key` behavior and render them through
the legacy independent commercial-line path immediately after their parent. This
preserves existing automatic-electrification totals without silently adding them to
the manual complement composition.

- [ ] **Step 4: Run Project and legacy engine regressions**

Run:

```powershell
python -m pytest tests/test_project_quote_engine.py tests/test_mixed_quote_engine.py tests/test_quote_engine_golden.py tests/test_quote_engine_lumbro.py -q
```

Expected: all tests pass; legacy jobs remain pixel/formula compatible and Project jobs
produce the two intended projections.

- [ ] **Step 5: Commit official Project composition**

```powershell
git add mobiliti_saas/quote_engine/engine.py mobiliti_saas/quote_engine/official_composer.py tests/test_project_quote_engine.py tests/test_mixed_quote_engine.py
git commit -m "feat: render project complements in official quote"
```

### Task 8: Workbook integrity, formula, and capacity acceptance

**Files:**
- Create: `tests/test_project_quote_acceptance.py`
- Modify: `tests/test_official_quote_stress.py`
- Modify: `tests/test_official_template_contract.py`
- Modify: `tests/test_quotation_sheet_transplant.py`
- Modify: `mobiliti_saas/README.md`
- Modify: `mobiliti_saas/worker/README.md`

**Interfaces:**
- Consumes: the complete persistent Project quote flow.
- Produces: validated XLSX files and a documented localhost acceptance procedure.

- [ ] **Step 1: Add the full acceptance matrix**

Create these four named tests:

- `test_project_quote_opens_without_repair_and_totals_equal_components`, parametrized
  with `quote_currency in ("MXN", "USD")`, generating one principal, one per-unit
  complement, and one fixed complement;
- `test_project_quote_preserves_original_quotation_and_template_contract`, generating
  from the checked-in import fixture and comparing every transplanted `Quotation`
  part plus the official contract audit;
- `test_project_quote_expands_past_16_sections_and_33_components`, generating 20
  sections and 700 physical component rows;
- `test_project_quote_rejects_only_after_physical_xlsx_limit`, calculating the maximum
  permitted component count from `XLSX_MAX_ROWS` and
  `MOBILITI_RESERVED_ROWS_AFTER_TOTAL`, accepting the boundary and rejecting
  boundary plus one before a job is created.

The first test must inspect exact formulas, calculate expected values with `Decimal`,
and verify:

```text
visible principal total
= principal component total
+ per-parent component total
+ fixed-project component total
```

The preservation test must compare the transplanted `Quotation` XML and related parts
against the normalized imported source, then run the existing template-contract hash
and relationship audits. The stress fixture must include at least 20 sections, 700
physical components, duplicate canonical products, imported lines, and complements.

- [ ] **Step 2: Run the focused acceptance tests**

Run:

```powershell
python -m pytest tests/test_project_quote_acceptance.py tests/test_official_quote_stress.py tests/test_official_template_contract.py tests/test_quotation_sheet_transplant.py -q
```

Expected: all tests pass and every generated file is a valid ZIP/OOXML package.

- [ ] **Step 3: Run the full local automated gate**

Run:

```powershell
python -m pytest -q
npm --prefix mobiliti_saas/web run build
```

Expected: the entire Python suite passes and Vite completes a production build.

- [ ] **Step 4: Validate with desktop Excel on Windows**

Use the existing Excel COM acceptance helper from
`tests/test_official_quote_stress.py` to open, recalculate, save, close, and reopen:

1. one catalog-only Project;
2. one imported-plus-catalog Project;
3. one Project with both complement quantity modes;
4. the 700-component stress Project.

Expected:

- Excel reports no repair dialog;
- there are no `#REF!`, unexpected `#VALUE!`, or broken external links in dynamic
  areas;
- `Mobiliti` formulas in populated and unused yellow rows match the official
  template contract;
- `Cotizacion` formulas reference the intended `Mobiliti!X` rows;
- the downloaded file remains openable after Excel saves it.

- [ ] **Step 5: Document localhost validation and commit**

Document:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev-start.ps1
```

and the manual workflow:

1. create a Project;
2. add the same code twice;
3. import a Quotation;
4. replace one occurrence;
5. replace all matching catalog/imported occurrences;
6. add per-unit and fixed complements;
7. reload and verify autosave;
8. generate and inspect both Excel sheets.

Then commit:

```powershell
git add tests/test_project_quote_acceptance.py tests/test_official_quote_stress.py tests/test_official_template_contract.py tests/test_quotation_sheet_transplant.py mobiliti_saas/README.md mobiliti_saas/worker/README.md
git commit -m "test: validate persistent project quote output"
```

## Final Local Gate

Run:

```powershell
python -m pytest tests/test_project_model.py tests/test_project_api.py tests/test_project_model_ui.py tests/test_project_ui.py tests/test_project_quote.py tests/test_project_quote_api.py tests/test_project_quote_engine.py tests/test_project_quote_acceptance.py -q
python -m pytest -q
npm --prefix mobiliti_saas/web run build
git diff --check
git status --short
```

Expected:

- all focused and full tests pass;
- Vite builds successfully;
- `git diff --check` reports no whitespace errors;
- only intentional implementation and pre-existing user files remain modified;
- no production deployment or SharePoint mutation has occurred.
