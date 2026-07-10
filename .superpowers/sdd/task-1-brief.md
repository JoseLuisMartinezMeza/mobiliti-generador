### Task 1: Offline inventory, price, and product index

**Files:**
- Create: `scripts/requirements-offiho.txt`
- Create: `scripts/build_offiho_catalog.py`
- Create: `tests/test_offiho_catalog.py`
- Generate: `mobiliti_saas/quote_engine/data/offiho_catalog.json`

**Interfaces:**
- Produces: `parse_inventory_xls(path: Path) -> list[dict[str, Any]]`
- Produces: `extract_offiho_identity(inventory_key: str) -> OffihoIdentity`
- Produces: `parse_pdf_price_index(paths: Sequence[Path]) -> dict[str, Decimal]`
- Produces: `build_site_product_index(cache: dict[str, Any]) -> dict[str, dict[str, str]]`
- Produces: `build_catalog(inventory_path: Path, pdf_paths: Sequence[Path], cache_path: Path, output_path: Path) -> dict[str, Any]`
- Produces JSON items keyed by `inventory_key` for Task 2.

- [ ] **Step 1: Add the offline-only dependencies**

```text
xlrd>=2.0.1
pypdf>=6.0.0
```

- [ ] **Step 2: Write failing inventory and normalization tests**

```python
def test_parse_inventory_keeps_available_and_exhausted_rows(tmp_path):
    rows = parse_inventory_xls(FIXTURES / "offiho-small.xls")
    assert rows[0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"
    assert rows[0]["available_quantity"] == Decimal("252")
    assert rows[0]["unit_price"] == Decimal("7999")
    assert any(row["available_quantity"] == 0 for row in rows)


def test_extract_identity_separates_model_name_and_variant():
    identity = extract_offiho_identity("OHE-405 NEGRO ALUFSEN")
    assert identity.code == "OHE-405"
    assert identity.name == "ALUFSEN"
    assert identity.variant == "NEGRO"
```

- [ ] **Step 3: Run the focused tests and verify the expected failure**

Run: `python -m pytest tests/test_offiho_catalog.py -q`

Expected: collection/import failure because `scripts.build_offiho_catalog` does not exist.

- [ ] **Step 4: Implement deterministic `.xls` parsing and identity extraction**

```python
@dataclass(frozen=True)
class OffihoIdentity:
    code: str
    name: str
    variant: str


def parse_inventory_xls(path: Path) -> list[dict[str, Any]]:
    workbook = xlrd.open_workbook(path)
    sheet = workbook.sheet_by_name("Publicación")
    items = []
    for row in range(5, sheet.nrows):
        inventory_key = normalize_space(sheet.cell_value(row, 1)).upper()
        stock = decimal_value(sheet.cell_value(row, 2))
        if not inventory_key or stock is None:
            continue
        identity = extract_offiho_identity(inventory_key)
        items.append({
            "inventory_key": inventory_key,
            "code": identity.code,
            "name": identity.name,
            "variant": identity.variant,
            "unit": "PZA",
            "pieces_per_box": json_number(decimal_value(sheet.cell_value(row, 3)) or Decimal("1")),
            "available_quantity": json_number(stock),
            "unit_price": json_number(decimal_value(sheet.cell_value(row, 4)) or Decimal("0")),
            "price_source": "inventory" if decimal_value(sheet.cell_value(row, 4)) else "missing",
        })
    return items
```

- [ ] **Step 5: Write failing exact-PDF-price and official-page matching tests**

```python
def test_pdf_price_index_normalizes_compact_variant(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "extract_pdf_pages", lambda paths: ["ALUFSEN OHE-405 negro $ 7,999"])
    prices = parse_pdf_price_index([tmp_path / "prices.pdf"])
    assert prices["OHE-405 NEGRO"] == Decimal("7999")


def test_site_match_requires_expected_model_code():
    product = match_official_product(
        OffihoIdentity("OHE-405", "ALUFSEN", "NEGRO"),
        [{"codes": ["OHE-405"], "url": "https://www.offiho.com/directivos/alufsen", "image_url": "https://www.offiho.com/alufsen.jpg"}],
    )
    assert product["url"].endswith("/directivos/alufsen")
```

- [ ] **Step 6: Implement PDF and website indexing with cache**

Implement `extract_pdf_pages` with `pypdf.PdfReader`, strict code/variant regexes, an official-host allowlist, category discovery from links on `offiho.com`, `offiho.com/econosillas`, and `offihoblack.com`, and a `.cache/offiho-products.json` cache containing source timestamps and resolved code lists. Website matches must require the expected code in page text or metadata.

- [ ] **Step 7: Generate and validate the real catalog**

Run:

```powershell
python -m pip install -r scripts\requirements-offiho.txt
python scripts\build_offiho_catalog.py `
  --inventory-url https://www.offiho.com/existencias.xls `
  --pdf "LP OFFIHO®️ ECONO SILLAS®️ JUL2026.pdf" `
  --pdf "LP BLACK®️ & COLOS®️ JUL2026.pdf" `
  --output mobiliti_saas\quote_engine\data\offiho_catalog.json
```

Expected: `total=1206`, `source_row_count=1286`, `duplicate_row_count=80`, `unique_item_count=1206`, `out_of_stock=189`, `inventory_prices=778`, no duplicate `inventory_key`, and a printed coverage summary for PDF prices and official images.

- [ ] **Step 8: Run tests and commit only the indexer deliverable**

Run: `python -m pytest tests/test_offiho_catalog.py -q`

Expected: PASS.

Commit:

```powershell
git add scripts/requirements-offiho.txt scripts/build_offiho_catalog.py tests/test_offiho_catalog.py mobiliti_saas/quote_engine/data/offiho_catalog.json
git commit -m "Add indexed Offiho product catalog"
```

---
