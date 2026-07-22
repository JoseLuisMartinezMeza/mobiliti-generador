# Official Template Preservation and Dynamic Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generar cotizaciones desde la plantilla oficial sin reserializar sus partes protegidas, conservar una `Quotation` importada, escribir costos convertidos una sola vez y expandir `Mobiliti`/`Cotizacion` para cualquier cantidad que quepa técnicamente en XLSX.

**Architecture:** El generador producirá superficies mutables y después compondrá un paquete XLSX nuevo partiendo de los bytes oficiales. Un planificador puro creará un `MobilitiRowMap`; un traductor tokenizado clonará fórmulas y coordenadas oficiales; el compositor sólo reemplazará partes permitidas y auditará por hash todo lo demás antes de publicar.

**Tech Stack:** Python 3.12 del worker, `openpyxl>=3.1`, `zipfile`, `xml.etree.ElementTree`, `dataclasses`, `hashlib`, React 19, Vite 7, pytest y Playwright existente.

## Global Constraints

- La plantilla oficial tiene SHA-256 `e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989`.
- La plantilla oficial y la fuente importada nunca se modifican; cada salida se crea como archivo nuevo.
- Las partes fuera de la allowlist permanecen byte-idénticas.
- Una `Quotation` importada permanece visible y conserva su contrato OOXML; los productos de catálogo no se agregan dentro de ella.
- `Quotation_Data` contiene el orden canónico combinado y queda `veryHidden`.
- `Mobiliti!J` recibe siempre el costo congelado como valor numérico.
- Cada costo se convierte exactamente una vez; `Mobiliti!K6` y las fórmulas oficiales desde `W` no se reemplazan.
- No existen límites funcionales de 16 secciones, 33 productos por sección, 32 secciones o 500 líneas.
- El límite físico es 1,048,576 filas XLSX menos filas reservadas; un límite técnico se valida antes de generar y nunca causa truncamiento.
- No se agregan dependencias de runtime; se reutilizan `openpyxl`, la biblioteca estándar y React existentes.
- OpenPyXL puede leer fixtures y resultados, pero no puede guardar la plantilla oficial ni el paquete final.
- No se escribe en SharePoint, Supabase, Vercel, Storage remoto ni producción durante implementación y pruebas locales.
- No se ejecutan borrados permanentes ni comandos Git destructivos; todos los commits incluyen únicamente archivos de su tarea.

## File Structure

- Create `mobiliti_saas/quote_engine/official_template.py`: manifiesto, hash e inspección estructural de la plantilla promovida.
- Create `mobiliti_saas/quote_engine/ooxml_package.py`: lectura/escritura de paquetes, asignación de partes, allowlist y auditoría de hashes/relaciones.
- Create `mobiliti_saas/quote_engine/ooxml_worksheet.py`: edición XML de filas/celdas, merges, validaciones, formato condicional y dibujos sin guardar el workbook con OpenPyXL.
- Create `mobiliti_saas/quote_engine/ooxml_formula.py`: traducción tokenizada de referencias A1, rangos, nombres y `calcChain.xml`.
- Create `mobiliti_saas/quote_engine/mobiliti_layout.py`: cálculo puro de secciones, capacidades, filas, subtotales, total y mapa de coordenadas.
- Create `mobiliti_saas/quote_engine/quotation_sheets.py`: construcción de `Quotation_Data` y trasplante semánticamente fiel de `Quotation`.
- Create `mobiliti_saas/quote_engine/official_composer.py`: composición final desde la plantilla oficial y validación previa a publicación.
- Modify `mobiliti_saas/quote_engine/engine.py`: escribir sólo entradas autorizadas y delegar layout/composición.
- Modify `mobiliti_saas/quote_engine/mixed_catalog.py` and `quotation_import.py`: sustituir límites comerciales por límites físicos/de bytes y conservar trazabilidad.
- Modify mirrored modules under `mobiliti_saas/web/mobiliti_saas/quote_engine/`: mantener identidad byte a byte con los módulos compartidos que consume Vercel.
- Modify both API entrypoints, `mobiliti_saas/worker/quote_worker.py`, `MixedCartDrawer.jsx`, `mixedCart.js`, `main.jsx` and `styles.css`: transportar fuentes originales, manejar payloads grandes y renderizar secciones contraíbles.
- Create focused tests under `tests/` and reuse the official workbook fixture promoted under `mobiliti_saas/worker/templates/`.

---

### Task 1: Promote and Verify the Official Template

**Files:**
- Create: `mobiliti_saas/quote_engine/official_template.py`
- Create: `mobiliti_saas/worker/templates/formato-cotizacion-2026-oficial.contract.json`
- Create: `scripts/promote_official_quote_template.py`
- Create: `tests/test_official_template_contract.py`
- Create: `mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx`
- Modify: `mobiliti_saas/worker/Dockerfile:8-25`
- Modify: `mobiliti_saas/worker/quote_worker.py:614-627`

**Interfaces:**
- Produces: `TemplateContract`, `TemplateInspection`, `load_template_contract(path)`, `inspect_template(path)`, `verify_official_template(path, contract)`.
- Later tasks consume the verified template path and `mutable_parts`/`mutable_cells` declared by the contract JSON.

- [ ] **Step 1: Write the failing contract tests**

```python
def test_promoted_template_matches_official_contract():
    contract = load_template_contract(CONTRACT)
    result = verify_official_template(TEMPLATE, contract)
    assert result.sha256 == OFFICIAL_SHA256
    assert result.sheet_states == {
        "Cotizacion": "visible",
        "Mobiliti": "visible",
        "Estrategia Comercial ": "visible",
        "Fletes": "hidden",
        "Proveedores": "hidden",
        "SPEC LAMINADO JOME": "hidden",
        "SPEC-GUIDE-LUMBRO": "hidden",
        "SPEC-GUIDE ESTRUCTURAS": "hidden",
        "Spec Guide Estructura ": "hidden",
        "SPEC-GUIDE-CR GLOBAL": "hidden",
        "Meses Sin Intereses Tarjetas": "hidden",
    }
    assert result.defined_name_count == 29
    assert result.external_link_parts == 12
    assert result.spec_formula_count == 1314


def test_modified_template_fails_before_output(tmp_path):
    changed = tmp_path / "changed.xlsx"
    changed.write_bytes(TEMPLATE.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="Plantilla oficial incompatible"):
        verify_official_template(changed, load_template_contract(CONTRACT))
```

- [ ] **Step 2: Run the tests and verify the current template fails**

Run: `python -m pytest tests/test_official_template_contract.py -v`

Expected: FAIL because `official_template.py`, the contract and the promoted binary do not exist.

- [ ] **Step 3: Implement the immutable contract and promotion command**

```python
@dataclass(frozen=True)
class TemplateContract:
    sha256: str
    sheet_states: dict[str, str]
    defined_name_count: int
    external_link_parts: int
    spec_formula_count: int
    mutable_sheets: tuple[str, ...]
    mutable_cells: dict[str, tuple[str, ...]]
    addable_sheets: tuple[str, ...]
    mutable_drawing_regions: dict[str, tuple[str, ...]]
    protected_prefixes: tuple[str, ...]
    translated_parts: tuple[str, ...]


@dataclass(frozen=True)
class TemplateInspection:
    sha256: str
    sheet_states: dict[str, str]
    defined_name_count: int
    external_link_parts: int
    spec_formula_count: int


def verify_official_template(path: Path, contract: TemplateContract) -> TemplateInspection:
    inspection = inspect_template(path)
    mismatches = []
    if inspection.sha256 != contract.sha256:
        mismatches.append("sha256")
    if inspection.sheet_states != contract.sheet_states:
        mismatches.append("sheet_states")
    if inspection.defined_name_count != contract.defined_name_count:
        mismatches.append("defined_names")
    if inspection.external_link_parts != contract.external_link_parts:
        mismatches.append("external_links")
    if inspection.spec_formula_count != contract.spec_formula_count:
        mismatches.append("spec_formulas")
    if mismatches:
        raise ValueError(f"Plantilla oficial incompatible: {', '.join(mismatches)}")
    return inspection


def promote(source: Path, destination: Path, contract_path: Path) -> None:
    contract = load_template_contract(contract_path)
    verify_official_template(source, contract)
    if destination.exists():
        raise FileExistsError(f"Destino ya existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    verify_official_template(destination, contract)
```

Use this contract content; `inspect_template()` resolves sheet names to their current package part and sheet ID instead of hard-coding `sheetN.xml`:

```json
{
  "sha256": "e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989",
  "defined_name_count": 29,
  "external_link_parts": 12,
  "spec_formula_count": 1314,
  "sheet_states": {
    "Cotizacion": "visible",
    "Mobiliti": "visible",
    "Estrategia Comercial ": "visible",
    "Fletes": "hidden",
    "Proveedores": "hidden",
    "SPEC LAMINADO JOME": "hidden",
    "SPEC-GUIDE-LUMBRO": "hidden",
    "SPEC-GUIDE ESTRUCTURAS": "hidden",
    "Spec Guide Estructura ": "hidden",
    "SPEC-GUIDE-CR GLOBAL": "hidden",
    "Meses Sin Intereses Tarjetas": "hidden"
  },
  "mutable_sheets": ["Mobiliti", "Cotizacion"],
  "mutable_cells": {
    "Mobiliti": ["K4", "K8"],
    "Fletes": ["D19"],
    "Estrategia Comercial ": ["B7:C38", "D59", "B63:B64", "B68"]
  },
  "mutable_drawing_regions": {
    "Cotizacion": ["product-area"]
  },
  "addable_sheets": ["Quotation", "Quotation_Data"],
  "protected_prefixes": ["xl/externalLinks/", "xl/richData/"],
  "translated_parts": ["xl/calcChain.xml"]
}
```

- [ ] **Step 4: Promote the audited SharePoint copy and make the worker fail closed**

Run:

```powershell
python scripts/promote_official_quote_template.py --source "C:\Users\pepem\AppData\Local\Temp\mobiliti-template-audit-20260721\official-template-sharepoint.xlsx" --destination "mobiliti_saas\worker\templates\Formato Cotizacion 2026 Oficial.xlsx" --contract "mobiliti_saas\worker\templates\formato-cotizacion-2026-oficial.contract.json"
python -m pytest tests/test_official_template_contract.py tests/test_quote_worker.py::test_default_template_resolves_existing_template -v
```

Expected: promotion prints the official SHA-256 and both tests PASS. Update `TEMPLATE_PATH` and `_default_template()` to the exact promoted filename; remove the silent generated-workbook fallback from the worker path.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/quote_engine/official_template.py mobiliti_saas/worker/templates/formato-cotizacion-2026-oficial.contract.json "mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx" scripts/promote_official_quote_template.py tests/test_official_template_contract.py mobiliti_saas/worker/Dockerfile mobiliti_saas/worker/quote_worker.py
git commit -m "feat: promote official quote template"
```

### Task 2: Add Package-Level Mutation and Preservation Audits

**Files:**
- Create: `mobiliti_saas/quote_engine/ooxml_package.py`
- Create: `tests/ooxml_test_helpers.py`
- Create: `tests/test_ooxml_package.py`

**Interfaces:**
- Consumes: `TemplateContract.mutable_parts` from Task 1.
- Produces: `XlsxPackage.read()`, `XlsxPackage.write_new()`, `PackageMutation`, `PackageAudit`, `assert_package_preserved()`.

- [ ] **Step 1: Write failing tests for exact preservation and relationship validation**

```python
def test_write_new_changes_only_allowlisted_part(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    target = tmp_path / "target.xlsx"
    package = XlsxPackage.read(source)
    package.write_new(target, PackageMutation(replacements={"xl/worksheets/sheet1.xml": b"<changed/>"}))
    assert_package_preserved(
        source,
        target,
        allowed_parts={"xl/worksheets/sheet1.xml"},
    )
    assert part_bytes(target, "xl/styles.xml") == part_bytes(source, "xl/styles.xml")


def test_audit_rejects_dangling_internal_relationship(tmp_path):
    source = make_package_with_dangling_relationship(tmp_path / "bad.xlsx")
    with pytest.raises(ValueError, match="Relacion OOXML sin destino"):
        XlsxPackage.read(source).audit()
```

- [ ] **Step 2: Run the package tests and verify they fail**

Run: `python -m pytest tests/test_ooxml_package.py -v`

Expected: FAIL with import errors for `ooxml_package`.

- [ ] **Step 3: Implement a write-once package object**

```python
@dataclass(frozen=True)
class PackageMutation:
    replacements: Mapping[str, bytes] = field(default_factory=dict)
    additions: Mapping[str, bytes] = field(default_factory=dict)

    @property
    def allowed_parts(self) -> frozenset[str]:
        return frozenset((*self.replacements, *self.additions))


@dataclass(frozen=True)
class PackageAudit:
    changed_parts: frozenset[str]
    protected_hashes: Mapping[str, str]
    unexpected_changed_parts: frozenset[str] = frozenset()


@dataclass
class XlsxPackage:
    path: Path
    infos: Mapping[str, zipfile.ZipInfo]
    parts: Mapping[str, bytes]

    @classmethod
    def read(cls, path: Path) -> "XlsxPackage":
        with zipfile.ZipFile(path, "r") as archive:
            infos = {item.filename: item for item in archive.infolist()}
            parts = {name: archive.read(name) for name in infos}
        package = cls(path=path, infos=infos, parts=parts)
        package.audit()
        return package

    def write_new(self, output: Path, mutation: PackageMutation) -> None:
        if output.exists():
            raise FileExistsError(f"La salida ya existe: {output}")
        overlap = set(mutation.replacements) & set(mutation.additions)
        if overlap:
            raise ValueError(f"Partes duplicadas: {sorted(overlap)}")
        unknown = set(mutation.replacements) - set(self.parts)
        if unknown:
            raise ValueError(f"Reemplazos inexistentes: {sorted(unknown)}")
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as target:
            for name, info in self.infos.items():
                target.writestr(info, mutation.replacements.get(name, self.parts[name]))
            for name in sorted(mutation.additions):
                target.writestr(name, mutation.additions[name])
```

`audit()` must parse every internal `.rels`, resolve its target relative to its owner and reject missing destinations, duplicate ZIP names, absolute/traversal paths and duplicate relationship IDs.

- [ ] **Step 4: Add byte-hash comparison and run tests**

```python
def assert_package_preserved(source: Path, output: Path, allowed_parts: set[str]) -> PackageAudit:
    before = XlsxPackage.read(source)
    after = XlsxPackage.read(output)
    changed = {
        name for name in set(before.parts) | set(after.parts)
        if before.parts.get(name) != after.parts.get(name)
    }
    unexpected = changed - allowed_parts
    if unexpected:
        raise ValueError(f"Partes protegidas modificadas: {sorted(unexpected)}")
    return PackageAudit(changed_parts=frozenset(changed), protected_hashes=before.hashes(exclude=allowed_parts))
```

Run: `python -m pytest tests/test_ooxml_package.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/quote_engine/ooxml_package.py tests/ooxml_test_helpers.py tests/test_ooxml_package.py
git commit -m "feat: add xlsx package preservation layer"
```

### Task 3: Plan Dynamic Rows and Translate Official Formulas

**Files:**
- Create: `mobiliti_saas/quote_engine/mobiliti_layout.py`
- Create: `mobiliti_saas/quote_engine/ooxml_formula.py`
- Create: `tests/test_mobiliti_layout.py`
- Create: `tests/test_ooxml_formula.py`

**Interfaces:**
- Produces: `SectionNeed`, `SectionLayout`, `MobilitiRowMap`, `plan_mobiliti_layout(needs)`, `translate_formula()`, `translate_calc_chain()`.
- Later tasks use `MobilitiRowMap.item_rows`, `product_ranges`, `subtotal_rows`, `total_row`, `last_product_row`, `row_translation`.

- [ ] **Step 1: Write failing pure planner tests**

```python
@pytest.mark.parametrize(
    ("counts", "expected_sections", "expected_first_subtotal"),
    [([34], 16, 48), ([100], 16, 114), ([1] * 17, 17, 47), ([40] * 20, 20, 54)],
)
def test_layout_expands_without_business_caps(counts, expected_sections, expected_first_subtotal):
    layout = plan_mobiliti_layout([SectionNeed(f"s-{i}", f"S{i}", count) for i, count in enumerate(counts)])
    assert len(layout.sections) == expected_sections
    assert layout.sections[0].subtotal_row == expected_first_subtotal
    assert sum(section.item_count for section in layout.sections[:len(counts)]) == sum(counts)
    assert layout.total_row < 1_048_576


def test_layout_rejects_only_real_xlsx_overflow():
    with pytest.raises(ValueError, match="capacidad física de XLSX"):
        plan_mobiliti_layout([SectionNeed("huge", "Huge", 1_048_576)])
```

- [ ] **Step 2: Write failing formula token tests**

```python
def test_translate_formula_preserves_absolute_and_expands_section_range():
    formula = "=SUM(IFERROR(H14:H46,0))+$K$6+Mobiliti!W14"
    translated = translate_formula(
        formula,
        origin="H47",
        target="H114",
        range_overrides={"H81:H113": "H14:H113"},
    )
    assert translated == "=SUM(IFERROR(H14:H113,0))+$K$6+Mobiliti!W81"


def test_calc_chain_maps_moved_and_cloned_cells():
    result = translate_calc_chain(CALC_CHAIN_XML, sheet_id=2, coordinate_map={"W14": ["W14", "W47"]})
    assert calc_chain_coordinates(result, sheet_id=2) == {"W14", "W47"}
```

- [ ] **Step 3: Run tests and verify failures**

Run: `python -m pytest tests/test_mobiliti_layout.py tests/test_ooxml_formula.py -v`

Expected: FAIL because planner and translator modules are absent.

- [ ] **Step 4: Implement deterministic layout and token-based translation**

```python
@dataclass(frozen=True)
class SectionNeed:
    id: str
    title: str
    item_count: int


class FormulaTranslationError(ValueError):
    pass


@dataclass(frozen=True)
class SectionLayout:
    id: str
    title: str
    section_row: int
    product_start: int
    capacity: int
    item_count: int
    subtotal_row: int


@dataclass(frozen=True)
class MobilitiRowMap:
    sections: tuple[SectionLayout, ...]
    total_row: int
    last_product_row: int
    product_ranges: tuple[tuple[int, int], ...]
    subtotal_rows: tuple[int, ...]
    row_translation: Mapping[int, tuple[int, ...]]

    @classmethod
    def from_sections(cls, sections: Sequence[SectionLayout], total_row: int) -> "MobilitiRowMap":
        frozen = tuple(sections)
        return cls(
            sections=frozen,
            total_row=total_row,
            last_product_row=max(section.product_start + section.capacity - 1 for section in frozen),
            product_ranges=tuple((section.product_start, section.capacity) for section in frozen),
            subtotal_rows=tuple(section.subtotal_row for section in frozen),
            row_translation=build_row_translation(frozen, total_row),
        )


def build_row_translation(
    sections: Sequence[SectionLayout],
    total_row: int,
) -> Mapping[int, tuple[int, ...]]:
    product_rows = tuple(
        row
        for section in sections
        for row in range(section.product_start, section.product_start + section.capacity)
    )
    return {
        13: tuple(section.section_row for section in sections),
        14: product_rows,
        47: tuple(section.subtotal_row for section in sections),
        573: (total_row,),
    }


BASE_SECTION_COUNT = 16
BASE_PRODUCT_CAPACITY = 33
BASE_FIRST_SECTION_ROW = 13
BASE_BLOCK_HEIGHT = 35
XLSX_MAX_ROWS = 1_048_576
RESERVED_ROWS_AFTER_TOTAL = 64


def plan_mobiliti_layout(needs: Sequence[SectionNeed]) -> MobilitiRowMap:
    visible_count = max(BASE_SECTION_COUNT, len(needs))
    cursor = BASE_FIRST_SECTION_ROW
    sections = []
    for index in range(visible_count):
        need = needs[index] if index < len(needs) else SectionNeed(f"unused-{index}", "NOMBRE", 0)
        capacity = max(BASE_PRODUCT_CAPACITY, need.item_count)
        product_start = cursor + 1
        subtotal_row = product_start + capacity
        sections.append(SectionLayout(need.id, need.title, cursor, product_start, capacity, need.item_count, subtotal_row))
        cursor = subtotal_row + 1
    total_row = cursor
    if total_row + RESERVED_ROWS_AFTER_TOTAL > XLSX_MAX_ROWS:
        raise ValueError("La cotizacion excede la capacidad física de XLSX")
    return MobilitiRowMap.from_sections(sections, total_row=total_row)
```

Implement `translate_formula()` using `openpyxl.formula.tokenizer.Tokenizer` plus `Translator`. Only `OPERAND/RANGE` tokens may change; quoted strings are copied untouched. Any range token that cannot be parsed or mapped raises `FormulaTranslationError` with sheet/cell/formula context.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_mobiliti_layout.py tests/test_ooxml_formula.py -v`

Expected: PASS.

```bash
git add mobiliti_saas/quote_engine/mobiliti_layout.py mobiliti_saas/quote_engine/ooxml_formula.py tests/test_mobiliti_layout.py tests/test_ooxml_formula.py
git commit -m "feat: plan dynamic mobiliti rows"
```

### Task 4: Expand Mobiliti by Cloning Official Rows and Blocks

**Files:**
- Modify: `mobiliti_saas/quote_engine/engine.py:513-1316,2106-2292`
- Create: `mobiliti_saas/quote_engine/ooxml_worksheet.py`
- Modify: `tests/test_mobiliti_capacity.py`
- Modify: `tests/test_mobiliti_sharepoint_contract.py`

**Interfaces:**
- Consumes: official `Mobiliti` worksheet XML, `MobilitiRowMap` and `translate_formula()` from Task 3.
- Produces: `WorksheetEditor`, `MobilitiSheetMutation`, `apply_mobiliti_layout(editor, row_map)`, `capture_official_mobiliti_block()`, `relocate_official_auxiliary_rows()`, `clone_section_header()`, `clone_formula_row()`, `clone_subtotal_row()`, `clone_total_row()`, `translate_mobiliti_validations()` and `translate_mobiliti_conditional_formatting()`.

- [ ] **Step 1: Replace constant assertions with failing dynamic cases**

```python
def render_mobiliti_fixture(tmp_path, items):
    official_xml = part_bytes(TEMPLATE, official_part(TEMPLATE, "Mobiliti"))
    needs, writes = mobiliti_plan_from_items(items)
    mutation = build_mobiliti_sheet(official_xml, needs, writes)
    output = compose_single_sheet_fixture(TEMPLATE, mutation, tmp_path / "mobiliti.xlsx")
    return mutation.row_map, load_workbook(output, data_only=False, keep_links=True)["Mobiliti"]


def used_product_rows(row_map):
    return [
        row
        for section in row_map.sections
        for row in range(section.product_start, section.product_start + section.item_count)
    ]


@pytest.mark.parametrize("count", [34, 100])
def test_one_section_keeps_every_product_and_official_formulas(tmp_path, count):
    row_map, ws = render_mobiliti_fixture(tmp_path, _many_products(count))
    rows = used_product_rows(row_map)
    assert len(rows) == count
    assert rows == list(range(14, 14 + count))
    assert ws.cell(14 + count, 1).value == "Subtotales Sección 1"
    assert "#REF!" not in formulas_text(ws)


@pytest.mark.parametrize("section_count", [17, 20])
def test_more_than_sixteen_sections_clone_official_block(tmp_path, section_count):
    row_map, ws = render_mobiliti_fixture(tmp_path, _one_product_per_section(section_count))
    assert len(used_product_rows(row_map)) == section_count
    assert find_section_titles(ws)[-1] == f"Sección {section_count} - SECCION {section_count}"
    assert all_official_formula_columns_present(ws, used_product_rows(row_map))


def test_contaminated_product_inputs_are_cleared_without_clearing_formulas(tmp_path):
    _row_map, ws = render_mobiliti_fixture(tmp_path, _many_products(1))
    assert ws["D14"].value is not None
    assert all(ws.cell(15, col).value in (None, 0) for col in (4, 5, 6, 8, 10, 11, 16))
    assert formula_text(ws["W15"].value) == translated_official_formula("W14", "W15")
```

- [ ] **Step 2: Run focused tests and confirm the old 32/64 implementation fails**

Run: `python -m pytest tests/test_mobiliti_capacity.py -k "one_section_keeps or more_than_sixteen" -v`

Expected: FAIL because capacities are normalized to 64, section lists are fixed at 32 and formulas are rewritten manually.

- [ ] **Step 3: Replace fixed layout constants and manual formula writers**

```python
@dataclass(frozen=True)
class MobilitiCellWrite:
    coordinate: str
    kind: Literal["number", "text", "boolean"]
    value: Decimal | str | bool


@dataclass(frozen=True)
class MobilitiSheetMutation:
    xml: bytes
    row_map: MobilitiRowMap


def build_mobiliti_sheet(
    official_sheet_xml: bytes,
    needs: list[SectionNeed],
    cell_writes: Sequence[MobilitiCellWrite],
) -> MobilitiSheetMutation:
    row_map = plan_mobiliti_layout(needs)
    editor = WorksheetEditor.from_xml(official_sheet_xml)
    canonical = capture_official_mobiliti_block(editor, first_section_row=13, second_section_row=48, total_row=573)
    relocate_official_auxiliary_rows(editor, row_map, canonical)
    for section in row_map.sections:
        clone_section_header(editor, canonical.section_header, section.section_row, section.title)
        for target_row in range(section.product_start, section.product_start + section.capacity):
            clone_formula_row(editor, canonical.product_row, target_row, row_map)
        clone_subtotal_row(editor, canonical.subtotal_row, section, row_map)
    clone_total_row(editor, canonical.total_row, row_map.total_row, row_map)
    clear_mobiliti_input_cells(editor, row_map)
    apply_mobiliti_cell_writes(editor, cell_writes, row_map)
    translate_mobiliti_validations(editor, row_map)
    translate_mobiliti_conditional_formatting(editor, row_map)
    return MobilitiSheetMutation(xml=editor.to_xml(), row_map=row_map)
```

Task 4 delivers the pure OOXML `Mobiliti` component and its package-level tests. Do not force it into the active OpenPyXL path before `Quotation_Data`, `Cotizacion` composition and canonical worker inputs exist. Tasks 8 and 9 must route the active generator through this component and make `_ensure_mobiliti_formula_layout`, `_write_mobiliti_row_formulas`, `_normalize_mobiliti_row_formulas` and `_set_mobiliti_subtotal_formulas` unreachable; Task 12 removes those dead definitions after the full regression gate.

`WorksheetEditor` works on `ElementTree` nodes from the original worksheet part; it never calls `Workbook.save()`. `capture_official_mobiliti_block()` snapshots rows 13/14/47/48/49/82/573 plus merges, dimensions, validation `sqref`, conditional formatting, `extLst` and auxiliary rows 574:610. `relocate_official_auxiliary_rows()` moves that auxiliary XML after the new total. Every clone calls `translate_formula()` using the canonical source coordinate and row-map overrides; it copies style IDs and row properties without synthesizing formulas. Subtotal formulas replace only the canonical 33-row operand range with the section's actual `product_start:last_used_row`; total formulas replace the canonical 16 subtotal operands with `row_map.subtotal_rows`.

- [ ] **Step 4: Prove official formulas are the source**

Add assertions that `W14`, `X14`, the last used row, the last unused yellow row, every subtotal, the total row, validations and conditional formatting are translated from the official coordinates. Compare formula tokens rather than hard-coding a replacement financial formula.

Run:

```powershell
python -m pytest tests/test_mobiliti_capacity.py tests/test_mobiliti_sharepoint_contract.py -v
```

Expected: PASS for 34/100 products and 17/20 sections; no `#REF!`; unused rows retain blank-safe official formulas.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/quote_engine/engine.py mobiliti_saas/quote_engine/ooxml_worksheet.py tests/test_mobiliti_capacity.py tests/test_mobiliti_sharepoint_contract.py
git commit -m "feat: expand mobiliti from official blocks"
```

### Task 5: Create the Very-Hidden Canonical Quotation_Data Sheet

**Files:**
- Create: `mobiliti_saas/quote_engine/quotation_sheets.py`
- Create: `tests/test_quotation_data_sheet.py`
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py:509-518`
- Modify: `mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py`

**Interfaces:**
- Consumes: validated mixed payload lines and presentation sections.
- Produces: `QuotationDataRow`, `quotation_data_rows(payload)`, `build_quotation_data_sheet(rows) -> SheetAddition`.

- [ ] **Step 1: Write failing canonical-row and sheet-state tests**

```python
def test_quotation_data_contains_all_lines_in_user_order(mixed_payload):
    rows = quotation_data_rows(mixed_payload)
    assert [row.item_key for row in rows] == flatten_section_keys(mixed_payload["sections"])
    assert all(row.converted_cost == Decimal(line_cost(mixed_payload, row.item_key)) for row in rows)
    assert all(row.original_cost * row.frozen_rate == row.converted_cost for row in rows)


def test_quotation_data_is_very_hidden_and_has_no_urls(tmp_path, mixed_payload):
    output = build_package_with_quotation_data(tmp_path, mixed_payload)
    assert workbook_sheet_state(output, "Quotation_Data") == "veryHidden"
    values = worksheet_values(output, "Quotation_Data")
    assert len(values) == mixed_payload["item_count"] + 1
    assert not any("http://" in value or "https://" in value for value in string_values(values))
```

- [ ] **Step 2: Run tests and verify failures**

Run: `python -m pytest tests/test_quotation_data_sheet.py -v`

Expected: FAIL because the internal sheet builder does not exist.

- [ ] **Step 3: Implement canonical rows and inline-string XML**

```python
@dataclass(frozen=True)
class SheetAddition:
    name: str
    state: str
    xml: bytes
    parts: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class QuotationDataRow:
    item_key: str
    section_id: str
    section_title: str
    position: int
    origin: str
    source_row: int | None
    original_currency: str
    original_cost: Decimal
    frozen_rate: Decimal
    converted_cost: Decimal
    quantity: Decimal
    provider: str
    region: str
    source_hash: str
    row_hash: str


QUOTATION_DATA_HEADERS = tuple(field.name for field in fields(QuotationDataRow))


def build_quotation_data_sheet(rows: Sequence[QuotationDataRow]) -> SheetAddition:
    xml = inline_worksheet_xml(
        [QUOTATION_DATA_HEADERS, *(tuple(getattr(row, name) for name in QUOTATION_DATA_HEADERS) for row in rows)]
    )
    return SheetAddition(name="Quotation_Data", state="veryHidden", xml=xml)
```

All strings use `inlineStr`; decimals use numeric `<v>` values; formula cells, URLs, image bytes and temporary signed paths are forbidden by validation.

- [ ] **Step 4: Mirror the mixed module and run tests**

Apply the same narrow patch to the root and web-mirror `mixed_catalog.py`, then run:

```powershell
python -m pytest tests/test_quotation_data_sheet.py tests/test_mixed_catalog_cart.py::test_quote_engine_module_copies_are_byte_identical -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/quote_engine/quotation_sheets.py mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py tests/test_quotation_data_sheet.py
git commit -m "feat: add canonical quotation data sheet"
```

### Task 6: Transplant the Original Quotation with Its Dependencies

**Files:**
- Modify: `mobiliti_saas/quote_engine/quotation_sheets.py`
- Create: `tests/test_quotation_sheet_transplant.py`
- Modify: `tests/quotation_import_fixtures.py`

**Interfaces:**
- Consumes: source workbook bytes and `XlsxPackage` from Task 2.
- Produces: `transplant_quotation(source, destination_package) -> SheetAddition`, `StyleTableMerger`, `inline_source_shared_strings()`, `remap_source_styles()` and relationship-closure allocation.

- [ ] **Step 1: Build a source fixture and write the failing semantic-preservation test**

```python
def test_transplanted_quotation_preserves_semantic_signature(tmp_path):
    source = build_rich_quotation_fixture(
        tmp_path / "source.xlsx",
        formulas={"N9": "=G9*J9"},
        merges=["A1:N1", "B9:C9"],
        image_anchor="B9",
        print_area="A1:N40",
        hidden_rows=[12],
    )
    output = compose_with_original_quotation(source, tmp_path / "output.xlsx")
    assert quotation_signature(output) == quotation_signature(source)
    assert part_bytes(output, official_part("Fletes")) == part_bytes(OFFICIAL_TEMPLATE, official_part("Fletes"))


def test_catalog_only_output_has_no_fake_visible_quotation(tmp_path):
    output = compose_catalog_only_output(tmp_path / "catalog.xlsx")
    assert "Quotation" not in workbook_sheet_names(output)
    assert workbook_sheet_state(output, "Quotation_Data") == "veryHidden"
```

- [ ] **Step 2: Run tests and verify the old flat copy fails**

Run: `python -m pytest tests/test_quotation_sheet_transplant.py -v`

Expected: FAIL because `_copy_source_sheet` changes borders/fonts and drops unsupported drawing/configuration parts.

- [ ] **Step 3: Implement shared-string inlining and style merging**

```python
def inline_source_shared_strings(sheet_xml: bytes, shared_strings: Sequence[str]) -> bytes:
    root = ET.fromstring(sheet_xml)
    for cell in root.findall(f".//{{{SHEET_NS}}}c[@t='s']"):
        value = cell.find(f"{{{SHEET_NS}}}v")
        text = shared_strings[int(value.text)]
        cell.attrib["t"] = "inlineStr"
        cell.remove(value)
        inline = ET.SubElement(cell, qn("is"))
        node = ET.SubElement(inline, qn("t"))
        if text != text.strip():
            node.attrib[f"{{{XML_NS}}}space"] = "preserve"
        node.text = text
    return xml_bytes(root)


def remap_source_styles(sheet_xml: bytes, source_styles: bytes, target_styles: bytes) -> tuple[bytes, bytes]:
    merger = StyleTableMerger.from_xml(target_styles)
    style_map = merger.merge_referenced_styles(source_styles, referenced_style_ids(sheet_xml))
    return replace_style_ids(sheet_xml, style_map), merger.to_xml()
```

`StyleTableMerger` must deduplicate and append `numFmts`, `fonts`, `fills`, `borders`, `cellStyleXfs`, `cellXfs` and `cellStyles` in dependency order; it never renumbers an existing official record.

```python
@dataclass
class StyleTableMerger:
    root: ET.Element
    component_maps: dict[str, dict[bytes, int]]

    @classmethod
    def from_xml(cls, target_styles: bytes) -> "StyleTableMerger":
        root = ET.fromstring(target_styles)
        return cls(root, index_style_components(root))

    def merge_referenced_styles(self, source_styles: bytes, style_ids: set[int]) -> dict[int, int]:
        source = ET.fromstring(source_styles)
        mapping = {}
        for style_id in sorted(style_ids):
            source_xf = cell_xf(source, style_id)
            remapped_xf = remap_xf_components(source_xf, source, self.root, self.component_maps)
            mapping[style_id] = find_or_append_cell_xf(self.root, remapped_xf)
        return mapping

    def to_xml(self) -> bytes:
        refresh_style_counts(self.root)
        return xml_bytes(self.root)
```

- [ ] **Step 4: Copy the sheet relationship closure with allocated names**

```python
def transplant_quotation(source: Path, destination: XlsxPackage) -> SheetAddition:
    source_pkg = XlsxPackage.read(source)
    source_sheet = source_pkg.sheet_part("Quotation")
    closure = source_pkg.relationship_closure(source_sheet)
    allocation = destination.allocate_closure(closure, prefix="quotation_original")
    rewritten = rewrite_relationship_targets(closure, allocation)
    sheet_xml = inline_source_shared_strings(rewritten[source_sheet], source_pkg.shared_strings())
    sheet_xml, styles_xml = remap_source_styles(sheet_xml, source_pkg.parts["xl/styles.xml"], destination.parts["xl/styles.xml"])
    rewritten[allocation[source_sheet]] = sheet_xml
    rewritten["xl/styles.xml"] = styles_xml
    return SheetAddition(name="Quotation", state=source_pkg.sheet_state("Quotation"), xml=sheet_xml, parts=rewritten)
```

The relationship closure includes drawings, media, comments, VML, tables, hyperlinks and printer settings. External relationships remain external; internal targets are allocated under collision-free names. Local defined names for `Quotation` are copied with the new sheet index.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_quotation_sheet_transplant.py tests/test_xlsx_excel_sanitizer.py -v`

Expected: PASS; remove `_apply_quotation_borders`, `_apply_quotation_item_name_font`, `_patch_quotation_drawing_from_source` and `_normalize_quotation_sheet_view` from the active output path.

```bash
git add mobiliti_saas/quote_engine/quotation_sheets.py tests/test_quotation_sheet_transplant.py tests/quotation_import_fixtures.py mobiliti_saas/quote_engine/engine.py
git commit -m "feat: preserve original quotation package parts"
```

### Task 7: Enforce One-Time Cost Conversion and Preserve Official Pricing Formulas

**Files:**
- Modify: `mobiliti_saas/quote_engine/engine.py:1810-2050,2106-2267,2347-2373`
- Modify: `tests/test_mixed_quote_engine.py`
- Modify: `tests/test_quote_engine_lumbro.py`
- Modify: `tests/test_mobiliti_sharepoint_contract.py`

**Interfaces:**
- Consumes: converted costs from validated mixed/import payloads and official formula rows from Task 4.
- Produces: numeric `Mobiliti!J` inputs; `write_official_currency_selector(editor, quote_currency, delivery_place)` that never writes `K6`.

- [ ] **Step 1: Write failing conversion invariants**

```python
@pytest.mark.parametrize(
    ("original", "rate", "expected"),
    [("100.000000", "18.500000", Decimal("1850.00")), ("100.000000", "0.054054", Decimal("5.41")), ("100.000000", "1.000000", Decimal("100.00"))],
)
def test_mobiliti_j_is_frozen_cost_and_wx_remain_official(original, rate, expected):
    output = generate_mixed_fixture(original_cost=original, frozen_rate=rate)
    wb = load_workbook(output, data_only=False, keep_links=True)
    ws = wb["Mobiliti"]
    official = load_workbook(OFFICIAL_TEMPLATE, data_only=False, keep_links=True)["Mobiliti"]
    assert Decimal(str(ws["J14"].value)) == expected
    assert formula_shape(ws["W14"].value) == formula_shape(official["W14"].value)
    assert formula_shape(ws["X14"].value) == formula_shape(official["X14"].value)
    assert formula_text(ws["K6"].value) == formula_text(official["K6"].value)


def test_lumbro_accessory_cost_is_numeric_and_not_divided_by_k6():
    ws = generate_lumbro_accessory_sheet()
    accessory_row = find_product_row(ws, "LIDO.OP-INT")
    assert isinstance(ws.cell(accessory_row, 10).value, (int, float))
    assert "$K$6" not in str(ws.cell(accessory_row, 10).value)
```

- [ ] **Step 2: Run the tests and verify current W/X and K6 behavior fails**

Run: `python -m pytest tests/test_mixed_quote_engine.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_sharepoint_contract.py -k "frozen_cost or lumbro_accessory or official" -v`

Expected: FAIL because mixed mode writes `W:X=ROUND(J,2)`, accessories write formulas and `_write_mobiliti_settings` overwrites `K6`.

- [ ] **Step 3: Write only numeric costs and permitted selector cells**

```python
def _write_frozen_cost(editor: WorksheetEditor, row: int, item: QuoteItem) -> None:
    cost = Decimal(str(item.precio)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    editor.set_number(f"J{row}", cost)


def write_official_currency_selector(editor: WorksheetEditor, quote_currency: str, delivery_place: str) -> None:
    if quote_currency not in {"MXN", "USD", "EUR"}:
        raise ValueError("Moneda de cotizacion invalida")
    editor.set_boolean("K4", quote_currency != "MXN")
    editor.set_inline_string("K8", safe_excel_text(delivery_place))
```

Do not assign `J6`, `K6`, `W`, `X` or any formula column. The display currency/number format comes from metadata and `Cotizacion`; the cost cells are already in the selected currency.

- [ ] **Step 4: Convert Lumbro accessory costs in Python exactly once**

```python
def _lumbro_frozen_cost(price_ref: LumbroPriceRef | None, frozen_rate: Decimal) -> float:
    original = Decimal(str(price_ref.price_mxn if price_ref else 0))
    return float((original * frozen_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
```

Run: `python -m pytest tests/test_mixed_quote_engine.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_sharepoint_contract.py -v`

Expected: PASS and no formula under test performs a second currency conversion.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/quote_engine/engine.py tests/test_mixed_quote_engine.py tests/test_quote_engine_lumbro.py tests/test_mobiliti_sharepoint_contract.py
git commit -m "fix: preserve official pricing formulas"
```

### Task 8: Compose Cotizacion and Translate Only Dependent References

**Files:**
- Create: `mobiliti_saas/quote_engine/official_composer.py`
- Create: `tests/test_official_composer.py`
- Modify: `mobiliti_saas/quote_engine/engine.py:2075-2104,2328-2377,2517-2724,2973-3039`
- Modify: `tests/test_quote_engine_golden.py`

**Interfaces:**
- Consumes: official template, `MobilitiSheetMutation`, `CotizacionSheetMutation`, `MobilitiRowMap` and optional `SheetAddition` objects.
- Produces: `CotizacionSheetEditor`, `merge_cotizacion_product_images()`, `compose_official_quote(request: ComposeRequest) -> PackageAudit`, `build_allowlisted_mutation()` and `verify_output_contract()`.

- [ ] **Step 1: Write a failing protected-part audit test**

```python
def test_composer_changes_only_declared_parts(tmp_path, mixed_request):
    output = tmp_path / "quote.xlsx"
    audit = compose_official_quote(mixed_request.with_output(output))
    assert audit.unexpected_changed_parts == frozenset()
    assert audit.protected_hashes == protected_hashes(OFFICIAL_TEMPLATE, CONTRACT)
    assert workbook_sheet_state(output, "Fletes") == "hidden"
    assert "sheep" not in workbook_sheet_names(output)
    assert formula_count(output, SPEC_SHEETS) == 1314
    assert external_link_part_count(output) == 12
    assert defined_name_count(output) == 29
```

- [ ] **Step 2: Write failing dependent-reference tests**

```python
def test_dependent_references_follow_dynamic_row_map(tmp_path, request_20_sections):
    output = compose_request(tmp_path, request_20_sections)
    row_map = read_embedded_row_map(output)
    assert formula(output, "Fletes", "D19") == f"=Mobiliti!H{row_map.total_row}"
    assert mobiliti_range_end(formula(output, "Estrategia Comercial ", "B7")) == row_map.last_product_row
    assert formula(output, "Estrategia Comercial ", "D59") == f"=Cotizacion!H{cotizacion_total_row(output)}"
    assert no_other_cells_changed(OFFICIAL_TEMPLATE, output, sheet="Fletes", allowed={"D19"})


def test_cotizacion_clears_contamination_and_uses_first_discount_as_master(tmp_path, mixed_request):
    output = compose_request(tmp_path, mixed_request)
    product_rows = cotizacion_product_rows(output)
    first_discount = f"$G${product_rows[0]}"
    assert all(first_discount in formula(output, "Cotizacion", f"G{row}") for row in product_rows[1:])
    assert prior_quote_values(OFFICIAL_TEMPLATE).isdisjoint(cotizacion_visible_values(output))
    assert official_terms_signature(output) == official_terms_signature(OFFICIAL_TEMPLATE)
```

- [ ] **Step 3: Run tests and verify whole-workbook OpenPyXL save fails**

Run: `python -m pytest tests/test_official_composer.py -v`

Expected: FAIL because the current engine saves the whole workbook, deletes SPEC formulas/links and changes hidden states.

- [ ] **Step 4: Implement allowlisted composition**

```python
@dataclass(frozen=True)
class CotizacionSheetMutation:
    xml: bytes
    related_parts: Mapping[str, bytes]
    total_row: int


@dataclass(frozen=True)
class ComposeRequest:
    template: Path
    output: Path
    mobiliti: MobilitiSheetMutation
    cotizacion: CotizacionSheetMutation
    quotation: SheetAddition | None
    quotation_data: SheetAddition
    contract: TemplateContract


def compose_official_quote(request: ComposeRequest) -> PackageAudit:
    verify_official_template(request.template, request.contract)
    base = XlsxPackage.read(request.template)
    mutation = build_allowlisted_mutation(base, request)
    base.write_new(request.output, mutation)
    audit = assert_package_preserved(request.template, request.output, mutation.allowed_parts)
    verify_output_contract(request.output, request.contract, request.mobiliti.row_map)
    return audit
```

`CotizacionSheetEditor` clones the official product and total blocks directly in worksheet XML, clears only header/product data regions and translates the official formulas. `merge_cotizacion_product_images()` starts from the official drawing XML, preserves every static/header anchor and its WMF/media bytes, removes only anchors inside the contract's contaminated product region, then appends the new product anchors and media. `build_allowlisted_mutation()` takes those XML mutations; adds `Quotation`/`Quotation_Data`; patches workbook relationships/content types; translates `Fletes!D19`, the exact `Estrategia Comercial ` ranges, defined names whose rows moved and `calcChain.xml`. It rejects any attempted change to an undeclared cell, drawing region or part.

As part of this composition seam, replace the engine's active `Mobiliti` mutation with `build_mobiliti_sheet()` and assert that `_ensure_mobiliti_formula_layout`, `_write_mobiliti_row_formulas`, `_normalize_mobiliti_row_formulas` and `_set_mobiliti_subtotal_formulas` have no active caller. The functions remain physically present until Task 12 so this task does not combine routing changes with dead-code deletion.

- [ ] **Step 5: Run focused and golden tests, then commit**

Run:

```powershell
python -m pytest tests/test_official_composer.py tests/test_quote_engine_golden.py -v
```

Expected: PASS; official hidden sheets, external links, names, SPEC formulas, rich data and drawings survive.

```bash
git add mobiliti_saas/quote_engine/official_composer.py mobiliti_saas/quote_engine/engine.py tests/test_official_composer.py tests/test_quote_engine_golden.py
git commit -m "feat: compose quotes from official package"
```

### Task 9: Carry Original Imports and Canonical Rows Through the Worker

**Files:**
- Modify: `mobiliti_saas/worker/quote_worker.py:688-880,1300-1450`
- Modify: `mobiliti_saas/worker/online_quote_generator.py`
- Modify: `tests/test_quote_worker.py`
- Modify: `tests/test_mixed_catalog_quote_e2e.py`

**Interfaces:**
- Produces: `PreparedGeneratorInput(parser_source, original_quotation, quotation_data)` and passes explicit sources to `generate_quote()`.
- Consumes: `quotation_data_rows()` and `transplant_quotation()`.
- Finalizes the worker switch to `compose_official_quote()` and includes a regression assertion that no worker/generator path reaches the four legacy `Mobiliti` writers.

- [ ] **Step 1: Write a failing worker handoff test**

```python
def test_worker_passes_original_import_and_canonical_rows_to_generator(monkeypatch, mixed_job):
    captured = {}
    monkeypatch.setattr(quote_worker, "generate_quote", lambda **kwargs: captured.update(kwargs) or kwargs["output_path"])
    process_job(fake_client(mixed_job), mixed_job)
    assert captured["original_quotation_path"].name == "import-source.xlsx"
    assert len(captured["quotation_data_rows"]) == mixed_job["metadata"]["mixed_item_count"]
    assert captured["source_path"].name == "quotation_from_mixed_catalog.xlsx"
```

- [ ] **Step 2: Run the worker test and verify only one source path is currently passed**

Run: `python -m pytest tests/test_quote_worker.py -k "original_import_and_canonical" -v`

Expected: FAIL because `_prepare_generator_input()` returns only `Path`.

- [ ] **Step 3: Introduce the explicit prepared-input record**

```python
@dataclass(frozen=True)
class PreparedGeneratorInput:
    parser_source: Path
    original_quotation: Path | None
    quotation_data: tuple[QuotationDataRow, ...]


def _prepare_generator_input(
    job: dict,
    local_input: Path,
    tmp_dir: Path,
    *,
    client: SupabaseClient | PostgresClient | LocalDevClient | None = None,
) -> PreparedGeneratorInput:
    payload = _read_cart_payload(local_input)
    source_type = _json_job_source_type(job)
    if source_type != payload.get("source_type"):
        raise RuntimeError("source_type de metadata no coincide con JSON de entrada")
    imported_source_path = (
        _download_imported_source(client, payload, tmp_dir, job=job)
        if source_type == MIXED_CATALOG_CART_SOURCE_TYPE and payload.get("imported_source") is not None
        else None
    )
    converted_input = convert_validated_payload(source_type, payload, local_input, tmp_dir, imported_source_path)
    return PreparedGeneratorInput(
        parser_source=converted_input,
        original_quotation=imported_source_path,
        quotation_data=tuple(quotation_data_rows(payload)) if source_type == MIXED_CATALOG_CART_SOURCE_TYPE else (),
    )
```

Extract the existing validated converter dispatch into `convert_validated_payload(source_type, payload, local_input, tmp_dir, imported_source_path) -> Path`; it retains the exact source-type checks and metadata updates currently performed at lines 773-879.

Update `generate_quote()` and `online_quote_generator.py` to accept named arguments `original_quotation_path` and `quotation_data_rows`. Plain provider quotations pass their original source as `original_quotation_path`; catalog-only carts pass `None`.

- [ ] **Step 4: Run worker and mixed end-to-end tests**

Run: `python -m pytest tests/test_quote_worker.py tests/test_mixed_catalog_quote_e2e.py -v`

Expected: PASS; source hash verification still occurs before any output path is created.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/worker/quote_worker.py mobiliti_saas/worker/online_quote_generator.py tests/test_quote_worker.py tests/test_mixed_catalog_quote_e2e.py
git commit -m "feat: preserve imported quotation through worker"
```

### Task 10: Replace the 500-Line and 32-Section Business Caps

**Files:**
- Modify: `mobiliti_saas/quote_engine/mixed_catalog.py:59-69,174-184,431-452,678-750`
- Modify: `mobiliti_saas/quote_engine/quotation_import.py:24-35,65-234`
- Modify: mirrored modules under `mobiliti_saas/web/mobiliti_saas/quote_engine/`
- Modify: `mobiliti_saas/api/index.py:1054-1100,4185-4223,4574-4658`
- Modify: `mobiliti_saas/web/api/index.py` at the matching functions
- Modify: `tests/test_mixed_catalog_cart.py`
- Modify: `tests/test_quotation_import.py`
- Modify: `tests/test_quote_jobs_api.py`

**Interfaces:**
- Produces shared physical limits: `XLSX_MAX_ROWS=1_048_576`, `MAX_QUOTE_REQUEST_BYTES=25*1024*1024` and `required_mobiliti_rows(section_counts)` para validar la combinación real de líneas y secciones.
- APIs return explicit limit errors containing the actual byte/row reason; no message says “entre 1 y 500”.

- [ ] **Step 1: Write failing tests above the old limits**

```python
def test_mixed_payload_accepts_700_compact_lines(compact_catalog_rows, catalogs, rates):
    payload = build_mixed_catalog_cart_payload(compact_catalog_rows(700), catalogs, "MXN", rates, presentation_sections=sections_for(700, 20))
    assert payload["item_count"] == 700
    assert len(payload["sections"]) == 20


def test_import_manifest_accepts_1000_rows(import_workbook_bytes):
    manifest = build_import_manifest(import_workbook_bytes(1000), "large.xlsx")
    assert len(manifest["items"]) == 1000


def test_api_rejects_only_request_byte_limit(api_client, compact_request):
    response = api_client.post("/catalogs/mixed-quote", content=compact_request(bytes_over=MAX_QUOTE_REQUEST_BYTES))
    assert response.status_code == 413
    assert "bytes" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run focused tests and verify 500/32 rejections**

Run: `python -m pytest tests/test_mixed_catalog_cart.py tests/test_quotation_import.py tests/test_quote_jobs_api.py -k "700 or 1000 or byte_limit" -v`

Expected: FAIL at `MAX_MIXED_CATALOG_LINES=500`, `MAX_IMPORTED_LINES=500`, `MAX_MIXED_SECTIONS=32` and API reservation validation.

- [ ] **Step 3: Centralize physical limits and dynamic messages**

```python
XLSX_MAX_ROWS = 1_048_576
MOBILITI_FIRST_SECTION_ROW = 13
MOBILITI_BASE_SECTIONS = 16
MOBILITI_BASE_PRODUCTS = 33
MOBILITI_RESERVED_ROWS_AFTER_TOTAL = 64
MAX_QUOTE_REQUEST_BYTES = 25 * 1024 * 1024
MAX_MIXED_CATALOG_LINES = XLSX_MAX_ROWS - MOBILITI_RESERVED_ROWS_AFTER_TOTAL
MAX_IMPORTED_LINES = MAX_MIXED_CATALOG_LINES
MAX_MIXED_SECTIONS = (XLSX_MAX_ROWS - MOBILITI_RESERVED_ROWS_AFTER_TOTAL) // (MOBILITI_BASE_PRODUCTS + 2)


def required_mobiliti_rows(section_counts: Sequence[int]) -> int:
    visible = list(section_counts) + [0] * max(0, MOBILITI_BASE_SECTIONS - len(section_counts))
    return MOBILITI_FIRST_SECTION_ROW + sum(max(MOBILITI_BASE_PRODUCTS, count) + 2 for count in visible)


def validate_quote_size(*, section_counts: Sequence[int], encoded_bytes: int) -> None:
    if not section_counts or sum(section_counts) < 1:
        raise ValueError("La cotizacion debe contener al menos una linea")
    final_row = required_mobiliti_rows(section_counts) + MOBILITI_RESERVED_ROWS_AFTER_TOTAL
    if final_row > XLSX_MAX_ROWS:
        raise ValueError("La cotizacion excede la capacidad física de XLSX")
    if encoded_bytes > MAX_QUOTE_REQUEST_BYTES:
        raise ValueError(f"La cotizacion excede {MAX_QUOTE_REQUEST_BYTES} bytes")
```

Use this validator in import, payload, reservation and both API entrypoints. Keep image, ZIP bomb, text-cell, quantity and concurrency protections unchanged.

- [ ] **Step 4: Mirror modules and run tests**

Apply identical narrow patches to each root/web-mirror module pair, then run:

```powershell
python -m pytest tests/test_mixed_catalog_cart.py tests/test_quotation_import.py tests/test_quote_jobs_api.py -v
```

Expected: PASS; mirror-identity tests pass and no tested message contains the former 500/32 business cap.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/quote_engine/quotation_import.py mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py mobiliti_saas/web/mobiliti_saas/quote_engine/quotation_import.py mobiliti_saas/api/index.py mobiliti_saas/web/api/index.py tests/test_mixed_catalog_cart.py tests/test_quotation_import.py tests/test_quote_jobs_api.py
git commit -m "feat: size quotes by physical limits"
```

### Task 11: Keep Large Carts Responsive and Compact

**Files:**
- Modify: `mobiliti_saas/web/src/mixedCart.js:1-213,505-644`
- Modify: `mobiliti_saas/web/src/MixedCartDrawer.jsx:109-505`
- Modify: `mobiliti_saas/web/src/main.jsx` around mixed cart state/submission
- Modify: `mobiliti_saas/web/src/styles.css` mixed-cart rules
- Modify: `tests/test_mixed_catalog_cart_ui.py`
- Modify: `tests/test_mixed_catalog_browser_e2e.py`

**Interfaces:**
- Produces: `groupMixedCartLines(sections, lines)`, collapsed-section state keyed by section ID, and one compact request build per submission.
- Consumes: the physical section/line validator from the mirrored quote engine.

- [ ] **Step 1: Write failing grouping and UI contract tests**

```python
def test_drawer_groups_lines_once_and_supports_collapsed_sections():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert "groupMixedCartLines" in source
    assert "collapsedSectionIds" in source
    assert "aria-expanded" in source
    assert "lines.filter((line) => line.sectionId === section.id)" not in source


def test_browser_submits_700_lines_once(page, seeded_large_cart):
    page.goto(APP_URL)
    seed_cart(page, seeded_large_cart(lines=700, sections=20))
    page.get_by_role("button", name="Cotizar todos los catalogos").click()
    expect_request_count(page, "/catalogs/mixed-quote", 1)
    expect_last_request(page, lambda body: len(body["items"]) == 700 and len(body["sections"]) == 20)
```

- [ ] **Step 2: Run tests and verify the current O(sections × lines) render fails**

Run: `python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_mixed_catalog_browser_e2e.py -k "groups_lines_once or 700_lines_once" -v`

Expected: FAIL because each render filters all lines per section and every section is fully expanded.

- [ ] **Step 3: Add linear grouping and collapsed sections**

```javascript
export function groupMixedCartLines(sections, lines) {
  const grouped = new Map(sections.map((section) => [section.id, []]));
  for (const line of lines) {
    const bucket = grouped.get(line.sectionId);
    if (!bucket) throw new Error("Seccion de producto invalida");
    bucket.push(line);
  }
  return grouped;
}
```

In `MixedCartDrawer`, compute the map with `useMemo([sections, lines])`; render the header/count for every section, render line editors only when expanded, default sections with more than 50 products to collapsed, and preserve draft validity for hidden lines. Expansion buttons use `aria-expanded` and `aria-controls`.

- [ ] **Step 4: Build the payload once and run UI/build tests**

```javascript
const mixedRequest = useMemo(() => ({
  items: mixedCartLines.map(toMixedQuoteItem),
  sections: toMixedQuoteSections(mixedCartSections, mixedCartLines),
}), [mixedCartLines, mixedCartSections]);
```

Freeze the memoized snapshot at submit time; validation errors retain the cart. Run:

```powershell
python -m pytest tests/test_mixed_catalog_cart_ui.py tests/test_mixed_catalog_browser_e2e.py -v
npm --prefix mobiliti_saas/web run build
```

Expected: PASS; Vite build succeeds without a new dependency.

- [ ] **Step 5: Commit**

```bash
git add mobiliti_saas/web/src/mixedCart.js mobiliti_saas/web/src/MixedCartDrawer.jsx mobiliti_saas/web/src/main.jsx mobiliti_saas/web/src/styles.css tests/test_mixed_catalog_cart_ui.py tests/test_mixed_catalog_browser_e2e.py
git commit -m "feat: support large mixed carts"
```

### Task 12: End-to-End Stress, Formula Audit and Local Handoff

**Files:**
- Create: `tests/test_official_quote_stress.py`
- Modify: `tests/test_dev_saas_e2e.py`
- Modify: `tests/test_mixed_catalog_quote_e2e.py`
- Modify: `tests/test_quote_worker.py`
- Modify: `mobiliti_saas/quote_engine/engine.py` to remove now-unreachable legacy writers
- Modify: `mobiliti_saas/worker/README.md`
- Modify: `mobiliti_saas/README.md`

**Interfaces:**
- Consumes all previous tasks.
- Produces the final local acceptance report and a generator with no active fallback to the destructive legacy path.

- [ ] **Step 1: Write the complete stress matrix**

```python
@dataclass(frozen=True)
class QuoteShape:
    section_counts: tuple[int, ...]

    def __init__(self, section_counts: Sequence[int]):
        object.__setattr__(self, "section_counts", tuple(section_counts))

    @property
    def total_items(self) -> int:
        return sum(self.section_counts)


@pytest.mark.parametrize(
    "shape",
    [
        QuoteShape([34]),
        QuoteShape([100]),
        QuoteShape([1] * 17),
        QuoteShape([1] * 20),
        QuoteShape([40] * 20),
        QuoteShape([100] * 10),
    ],
)
def test_large_quotes_preserve_every_line_and_official_contract(tmp_path, shape):
    request = synthetic_mixed_request(shape, include_imported=True, catalogs=SEVEN_CATALOGS)
    output = run_local_worker_job(tmp_path, request)
    assert quotation_data_item_count(output) == shape.total_items
    assert mobiliti_written_item_count(output) == shape.total_items
    assert cotizacion_written_item_count(output) == shape.total_items
    assert not duplicated_item_keys(output)
    assert not formulas_containing(output, "#REF!")
    assert subtotals_cover_all_items(output)
    assert_package_preserved(OFFICIAL_TEMPLATE, output, OFFICIAL_ALLOWED_PARTS)
```

- [ ] **Step 2: Run the stress matrix**

Run: `python -m pytest tests/test_official_quote_stress.py -v`

Expected: all six shapes PASS. Any failure blocks the acceptance gate; the allowlist remains unchanged.

- [ ] **Step 3: Remove unreachable destructive functions**

Remove the active definitions and imports for `_sanitize_template_workbook`, `_default_template`, `_ensure_mobiliti_formula_layout`, `_ensure_mobiliti_capacity_legacy`, `_write_mobiliti_row_formulas`, `_normalize_mobiliti_row_formulas`, `_set_mobiliti_subtotal_formulas`, `_copy_source_sheet`, `_patch_quotation_drawing_from_source` and `_sanitize_output_xlsx_for_excel` only after `rg` proves no runtime or test caller remains. Replace tests of those internals with package-contract tests.

Run:

```powershell
rg -n "_sanitize_template_workbook|_ensure_mobiliti_formula_layout|_ensure_mobiliti_capacity_legacy|_write_mobiliti_row_formulas|_normalize_mobiliti_row_formulas|_set_mobiliti_subtotal_formulas|_copy_source_sheet|_patch_quotation_drawing_from_source|_sanitize_output_xlsx_for_excel" mobiliti_saas tests
```

Expected: no active code references; documentation/history references are acceptable.

- [ ] **Step 4: Run the full local verification gate**

Run:

```powershell
python -m pytest tests/test_official_template_contract.py tests/test_ooxml_package.py tests/test_ooxml_formula.py tests/test_mobiliti_layout.py tests/test_mobiliti_capacity.py tests/test_mobiliti_sharepoint_contract.py tests/test_quotation_data_sheet.py tests/test_quotation_sheet_transplant.py tests/test_official_composer.py tests/test_mixed_quote_engine.py tests/test_quote_engine_lumbro.py tests/test_mixed_catalog_cart.py tests/test_quotation_import.py tests/test_quote_jobs_api.py tests/test_quote_worker.py tests/test_mixed_catalog_quote_e2e.py tests/test_official_quote_stress.py -v
python -m pytest -q
npm --prefix mobiliti_saas/web run build
```

Expected: focused suite PASS, full pytest PASS, Vite build PASS. Open the generated 20-section/800-line workbook locally and render `Mobiliti`, `Cotizacion` and `Quotation`; compare official protected-part hashes and visually verify section boundaries, images and totals.

- [ ] **Step 5: Update local documentation and commit**

Document the official template hash, promotion command, allowlist, `Quotation_Data`, one-time conversion invariant, physical limits, stress command and explicit no-deploy state.

```bash
git add tests/test_official_quote_stress.py tests/test_dev_saas_e2e.py tests/test_mixed_catalog_quote_e2e.py tests/test_quote_worker.py mobiliti_saas/quote_engine/engine.py mobiliti_saas/worker/README.md mobiliti_saas/README.md
git commit -m "test: verify large official quote generation"
```

## Final Acceptance Gate

- The exact official template hash is verified before generation.
- Protected OOXML parts match the official bytes.
- `Quotation` preserves its semantic signature and catalog additions appear only in `Quotation_Data`, `Mobiliti` and `Cotizacion`.
- `Quotation_Data` is `veryHidden` and contains every line once, in user order.
- `Mobiliti!J` contains frozen numeric costs; `K6`, `W`, `X` and downstream official formulas preserve their formula shapes.
- 34/100 products in one section, 17/20 sections, 20×40 products and 1,000 mixed lines generate without omission or `#REF!`.
- Dependent references and `calcChain.xml` point to translated coordinates.
- The cart remains usable with 700 lines and submits one compact request.
- No SharePoint/production write or deployment occurs without a new explicit authorization.
