# Project Editor, Replacements, and Complements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the visible Cart workflow with a persistent Project editor that autosaves, supports repeated occurrences, single/all replacements, imported-code matching, and one-level complements with image previews.

**Architecture:** The existing mixed-cart state operations remain the only client model and are extended rather than duplicated. A small autosave state machine handles revisioned PATCH requests, while focused React components provide the Projects list, full editor, quick panel, and reusable catalog picker. The API created by the persistence plan remains the source of saved state and catalog search.

**Tech Stack:** React 19, Vite 7, JavaScript ES modules, Lucide React, pytest, Node-based pure-module tests, Playwright browser tests.

## Global Constraints

- All visible and accessible `Carrito` copy becomes `Proyecto`.
- Adding the same catalog product always creates a new occurrence with a unique `line_id`.
- `Reemplazar éste` operates by `line_id`; `Reemplazar todos` matches normalized provider/catalog plus official code.
- Imported lines participate when they have explicit provider and official code.
- A principal may have multiple direct complements; complements cannot have children.
- Replacing a principal removes its complements after an impact confirmation.
- Each complement chooses `Por unidad` or `Cantidad fija`.
- The same picker component serves add, replace-one, replace-all, and add-complement contexts.
- Do not add `localStorage` Project persistence or a second editor/model.
- Preserve the current section editor, order controls, imported-field editor, and catalog pages.
- Do not deploy production.

---

### Task 1: Occurrence identities and pure replacement/complement operations

**Files:**
- Modify: `mobiliti_saas/web/src/mixedCart.js`
- Test: `tests/test_project_model_ui.py`
- Modify: `tests/test_mixed_catalog_cart_ui.py`

**Interfaces:**
- Consumes: current `createMixedCartLine` inputs plus explicit `lineId`, `officialCode`, and `provider`.
- Produces: `replaceProjectLine`, `replaceAllProjectLines`, `addProjectComplement`, `removeProjectLineTree`, `projectComplements`, and Project serialization helpers.

- [ ] **Step 1: Write failing Node-backed behavior tests**

```python
# tests/test_project_model_ui.py
import json
import subprocess
from pathlib import Path

MODULE = Path("mobiliti_saas/web/src/mixedCart.js").resolve().as_uri()


def run_js(source):
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=f'import * as model from {json.dumps(MODULE)};\n{source}',
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_occurrences_replace_one_all_and_remove_principal_complements():
    result = run_js(r"""
      const base = {
        catalog: "sunon",
        identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
        officialCode: "CHAIR-1",
        provider: "Sunon",
        quantity: "2",
        quantityRules: {min: "1", step: "1", maxDecimals: 0, max: "1000000", integer: true},
        snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
          availability: "", configuration: "", warnings: []},
        sectionId: "section-1",
      };
      let lines = [
        model.createMixedCartLine({...base, lineId: "11111111-1111-4111-8111-111111111111"}),
        model.createMixedCartLine({...base, lineId: "22222222-2222-4222-8222-222222222222"}),
      ];
      lines = model.addProjectComplement(lines, lines[0].lineId, {
        ...base,
        lineId: "33333333-3333-4333-8333-333333333333",
        officialCode: "HEAD-1",
        quantity: "1",
      }, "per_parent_unit");
      const target = {
        ...base,
        catalog: "alma",
        identity: {internal_id: "alma:new", base_option_id: "", add_on_option_ids: []},
        officialCode: "NEW-1",
        provider: "ALMA",
        snapshot: {...base.snapshot, name: "New", code: "NEW-1"},
      };
      const one = model.replaceProjectLine(lines, lines[0].lineId, target);
      const all = model.replaceAllProjectLines(lines, {provider: "Sunon", officialCode: "CHAIR-1"}, target);
      console.log(JSON.stringify({
        unique: new Set(lines.map((line) => line.lineId)).size,
        oneCodes: one.lines.filter((line) => line.role === "principal").map((line) => line.officialCode),
        oneRemoved: one.removedComplementIds,
        allCodes: all.lines.filter((line) => line.role === "principal").map((line) => line.officialCode),
        allAffected: all.summary.affected,
      }));
    """)
    assert result == {
        "unique": 3,
        "oneCodes": ["NEW-1", "CHAIR-1"],
        "oneRemoved": ["33333333-3333-4333-8333-333333333333"],
        "allCodes": ["NEW-1", "NEW-1"],
        "allAffected": 2,
    }


def test_imported_line_matches_provider_and_official_code():
    result = run_js(r"""
      const imported = {
        kind: "imported", role: "principal",
        lineId: "11111111-1111-4111-8111-111111111111",
        officialCode: " OHE-405 ", provider: "Offiho",
      };
      console.log(JSON.stringify({
        matches: model.projectLineMatches(imported, {provider: " offiho ", officialCode: "OHE-405"}),
        missing: model.projectLineMatches({...imported, officialCode: ""}, {provider: "offiho", officialCode: "OHE-405"}),
      }));
    """)
    assert result == {"matches": True, "missing": False}


def test_project_serialization_round_trips_occurrence_graph():
    result = run_js(r"""
      const state = {
        quoteFields: {
          proyecto: "", cliente: "", correo: "", telefono: "",
          direccion: "", razon_social: "", quote_currency: "MXN", descuento: "40",
        },
        sections: [{id: "section-1", concept: "Recepción"}],
        lines: [model.createMixedCartLine({
          catalog: "sunon",
          identity: {internal_id: "sunon:chair", base_option_id: "", add_on_option_ids: []},
          officialCode: "CHAIR-1",
          provider: "Sunon",
          quantity: "1",
          quantityRules: {min: "1", step: "1", maxDecimals: 0,
            max: "1000000", integer: true},
          snapshot: {name: "Chair", code: "CHAIR-1", image_url: "", unit: "PZA",
            availability: "", configuration: "", warnings: []},
          sectionId: "section-1",
          lineId: "11111111-1111-4111-8111-111111111111",
        })],
      };
      const payload = model.serializeProject(state);
      const reopened = model.hydrateProject(payload);
      console.log(JSON.stringify({
        payloadLine: payload.lines[0].line_id,
        reopenedLine: reopened.lines[0].lineId,
        section: reopened.sections[0].concept,
      }));
    """)
    assert result == {
        "payloadLine": "11111111-1111-4111-8111-111111111111",
        "reopenedLine": "11111111-1111-4111-8111-111111111111",
        "section": "Recepción",
    }
```

- [ ] **Step 2: Run the tests and verify missing exports**

Run:

```powershell
python -m pytest tests/test_project_model_ui.py -q
```

Expected: Node fails because `addProjectComplement` and replacement exports do not exist.

- [ ] **Step 3: Extend the existing model without creating a parallel store**

Add these exact public signatures:

```javascript
export function createProjectLineId() {
  return globalThis.crypto.randomUUID();
}

export function projectMatchKey(provider, officialCode) {
  const cleanProvider = String(provider || "").normalize("NFKD")
    .replace(/\p{M}/gu, "").trim().toLocaleLowerCase().replace(/\s+/g, " ");
  const cleanCode = String(officialCode || "").trim().toUpperCase();
  return cleanProvider && cleanCode ? `${cleanProvider}\u0000${cleanCode}` : "";
}

export function projectLineMatches(line, selector) {
  return projectMatchKey(line.provider || line.catalog, line.officialCode)
    === projectMatchKey(selector.provider, selector.officialCode)
    && projectMatchKey(selector.provider, selector.officialCode) !== "";
}

export function projectComplements(lines, parentLineId) {
  return lines.filter((line) => line.role === "complement" && line.parentLineId === parentLineId)
    .sort((left, right) => left.position - right.position);
}
```

Change `createMixedCartLine` so `lineId` defaults to `createProjectLineId()`, `key`
equals `lineId` for compatibility, and `officialCode`, `provider`, `role`,
`parentLineId`, `quantityMode`, and `position` are copied. Change
`upsertMixedCartLine` to append every validated occurrence instead of combining
quantities.

Implement replacement atomically:

```javascript
export function replaceProjectLine(lines, lineId, target) {
  const current = lines.find((line) => line.lineId === lineId);
  if (!current) throw new Error("Producto del Proyecto no encontrado");
  const children = current.role === "principal"
    ? projectComplements(lines, lineId).map((line) => line.lineId)
    : [];
  const replacement = createMixedCartLine({
    ...target,
    lineId: current.lineId,
    quantity: current.quantity,
    sectionId: current.sectionId || "section-1",
    role: current.role,
    parentLineId: current.parentLineId || null,
    quantityMode: current.quantityMode || null,
    position: current.position,
  });
  const kept = lines.filter((line) => !children.includes(line.lineId));
  return {
    lines: kept.map((line) => line.lineId === lineId ? replacement : line),
    removedComplementIds: children,
  };
}

export function replaceAllProjectLines(lines, selector, target) {
  const matched = lines.filter((line) => projectLineMatches(line, selector));
  const ids = matched.map((line) => line.lineId);
  const parentById = new Map(lines.map((line) => [line.lineId, line]));
  const sectionIds = new Set(matched.map((line) => (
    line.sectionId
    || parentById.get(line.parentLineId)?.sectionId
  )).filter(Boolean));
  let result = [...lines];
  const removed = [];
  for (const lineId of ids) {
    if (!result.some((line) => line.lineId === lineId)) continue;
    const next = replaceProjectLine(result, lineId, target);
    result = next.lines;
    removed.push(...next.removedComplementIds);
  }
  return {
    lines: result,
    summary: {
      affected: ids.length,
      catalog: matched.filter((line) => line.kind !== "imported").length,
      imported: matched.filter((line) => line.kind === "imported").length,
      sections: sectionIds.size,
      removedComplements: removed.length,
      excludedUnlinked: lines.filter((line) => (
        !projectMatchKey(line.provider || line.catalog, line.officialCode)
      )).length,
    },
  };
}
```

`addProjectComplement` must reject a complement parent, validate the chosen quantity
with the target's quantity rules, assign `sectionId: null`, and append after existing
children.

Add `serializeProject({quoteFields, sections, lines})` and `hydrateProject(payload)`
to the same module. Serialization uses these exact mappings:

| UI | API payload |
|---|---|
| `section.id`, `section.concept`, array index | `section_id`, `concept`, `position` |
| `line.lineId`, `role`, `sectionId`, `parentLineId`, `position` | `line_id`, `role`, `section_id`, `parent_line_id`, `position` |
| `officialCode`, `snapshot` | `official_code`, `display_cache` |
| catalog `catalog`, `identity`, `quantityRules` | `source="catalog"`, `catalog`, `identity`, `quantity_rules_cache` |
| imported `importId`, `sourceRow`, `sourceCurrency` | `source="imported"`, `import_id`, `source_row`, `source_currency` |
| imported `edits.officialCode`, `edits.provider`, text and price fields | the corresponding snake-case imported fields |
| `imageAssetKey`, `sourceAssetKey` | `image_asset_key`, `source_asset_key` |
| `quantityMode` | `quantity_mode` |

`serializeProject` must emit only `schema_version`, `quote_fields`, `sections`, and
`lines`; it must not serialize prices or availability from catalog snapshots.
`hydrateProject` must call the existing line/section validation paths, preserve every
`line_id`, and reject unknown payload keys instead of silently dropping them.

- [ ] **Step 4: Run pure-model and legacy-cart tests**

Run:

```powershell
python -m pytest tests/test_project_model_ui.py tests/test_mixed_catalog_cart_ui.py -q
```

Expected: all tests pass; update legacy expectations from combined quantity to
independent occurrences.

- [ ] **Step 5: Commit Project state operations**

```powershell
git add mobiliti_saas/web/src/mixedCart.js tests/test_project_model_ui.py tests/test_mixed_catalog_cart_ui.py
git commit -m "feat: model project occurrences and complements"
```

### Task 2: Revisioned autosave state machine

**Files:**
- Create: `mobiliti_saas/web/src/projectAutosave.js`
- Create: `mobiliti_saas/web/src/useProjectAutosave.js`
- Test: `tests/test_project_autosave_ui.py`

**Interfaces:**
- Consumes: current Project, API `save(project, expectedRevision, operationId)`.
- Produces: pure `autosaveReducer` and React hook `useProjectAutosave`.

- [ ] **Step 1: Write failing reducer tests**

```python
# tests/test_project_autosave_ui.py
import json
from pathlib import Path

from test_project_model_ui import run_js

AUTOSAVE_MODULE = Path(
    "mobiliti_saas/web/src/projectAutosave.js"
).resolve().as_uri()


def test_autosave_never_claims_saved_before_server_confirmation():
    result = run_js(f"""
      const {{autosaveReducer, initialAutosaveState}} =
        await import({json.dumps(AUTOSAVE_MODULE)});
      let state = initialAutosaveState(3);
      state = autosaveReducer(state, {{type: "changed"}});
      const pending = state.status;
      state = autosaveReducer(state, {{type: "saving", operationId: "op-1"}});
      const saving = state.status;
      state = autosaveReducer(state, {{
        type: "failed", operationId: "op-1", message: "red"
      }});
      const failed = state.status;
      state = autosaveReducer(state, {{
        type: "saved", operationId: "op-1", revision: 4
      }});
      console.log(JSON.stringify({{
        pending, saving, failed, afterLateSuccess: state.status
      }}));
    """)
    assert result == {
        "pending": "pending",
        "saving": "saving",
        "failed": "pending",
        "afterLateSuccess": "pending",
    }
```

- [ ] **Step 2: Run the reducer test**

Run:

```powershell
python -m pytest tests/test_project_autosave_ui.py -q
```

Expected: fail because `projectAutosave.js` does not exist.

- [ ] **Step 3: Implement reducer and hook**

```javascript
// mobiliti_saas/web/src/projectAutosave.js
export const initialAutosaveState = (revision = 0) => ({
  status: "saved",
  revision,
  dirtyVersion: 0,
  operationId: "",
  message: "",
});

export function autosaveReducer(state, action) {
  if (action.type === "changed") {
    return {...state, status: "pending", dirtyVersion: state.dirtyVersion + 1, message: ""};
  }
  if (action.type === "saving") {
    return {...state, status: "saving", operationId: action.operationId,
      savingVersion: state.dirtyVersion};
  }
  if (action.type === "saved" && action.operationId === state.operationId
      && state.savingVersion === state.dirtyVersion) {
    return {...state, status: "saved", revision: action.revision, message: ""};
  }
  if (action.type === "conflict" && action.operationId === state.operationId) {
    return {...state, status: "conflict", currentProject: action.project};
  }
  if (action.type === "failed" && action.operationId === state.operationId) {
    return {...state, status: "pending", operationId: "", message: action.message};
  }
  return state;
}
```

Implement the hook with one operation ID per dirty version and reuse that ID for
network retries:

```javascript
// mobiliti_saas/web/src/useProjectAutosave.js
import {useEffect, useReducer, useRef} from "react";
import {autosaveReducer, initialAutosaveState} from "./projectAutosave.js";

export function useProjectAutosave({
  project,
  revision,
  changeVersion,
  saveProject,
  enabled = true,
}) {
  const [state, dispatch] = useReducer(autosaveReducer, revision, initialAutosaveState);
  const operationRef = useRef("");
  const retryTimerRef = useRef(null);

  useEffect(() => {
    if (!enabled || !project || changeVersion <= 0) return undefined;
    dispatch({type: "changed"});
    const snapshot = structuredClone(project);
    const operationId = crypto.randomUUID();
    operationRef.current = operationId;
    let cancelled = false;

    const attempt = async () => {
      if (cancelled || operationRef.current !== operationId) return;
      dispatch({type: "saving", operationId});
      try {
        const saved = await saveProject(snapshot, state.revision, operationId);
        if (!cancelled) {
          operationRef.current = "";
          dispatch({type: "saved", operationId, revision: saved.revision});
        }
      } catch (error) {
        if (cancelled) return;
        if (error?.status === 409 && error?.project) {
          operationRef.current = "";
          dispatch({type: "conflict", operationId, project: error.project});
          return;
        }
        dispatch({type: "failed", operationId, message: String(error?.message || error)});
        operationRef.current = operationId;
        retryTimerRef.current = window.setTimeout(attempt, 1500);
      }
    };

    const debounce = window.setTimeout(attempt, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(debounce);
      window.clearTimeout(retryTimerRef.current);
    };
  }, [changeVersion, enabled, saveProject]);

  return state;
}
```

`ProjectEditor` increments `changeVersion` only for a user mutation; loading or
reopening a Project starts at `0` and must not create a save. Add a fake-timer test
proving the first request begins only after 500 ms, a network
failure retries after 1500 ms with the same operation ID, and a later edit uses a new
operation ID. Never call `localStorage`, `sessionStorage`, or IndexedDB from this
hook.

- [ ] **Step 4: Run autosave tests**

Run:

```powershell
python -m pytest tests/test_project_autosave_ui.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit autosave**

```powershell
git add mobiliti_saas/web/src/projectAutosave.js mobiliti_saas/web/src/useProjectAutosave.js tests/test_project_autosave_ui.py
git commit -m "feat: autosave revisioned projects"
```

### Task 3: Projects list and lifecycle UI

**Files:**
- Create: `mobiliti_saas/web/src/ProjectsView.jsx`
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`
- Test: `tests/test_project_ui.py`

**Interfaces:**
- Consumes: `GET/POST /projects`, archive, restore, and duplicate routes.
- Produces: sidebar `Proyectos` view and `onOpenProject(projectId)`.

- [ ] **Step 1: Write failing source-contract tests**

```python
# tests/test_project_ui.py
from pathlib import Path


def test_projects_view_has_recoverable_lifecycle_and_no_delete_action():
    source = Path("mobiliti_saas/web/src/ProjectsView.jsx").read_text(encoding="utf-8")
    for copy in ("Proyectos activos", "Archivados", "Abrir", "Duplicar", "Archivar", "Restaurar"):
        assert copy in source
    assert "Eliminar" not in source
    assert 'method: "DELETE"' not in source


def test_sidebar_and_header_use_project_copy():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    assert '["proyectos", "Proyectos"' in source
    assert "Proyecto (" in source
    assert "Carrito (" not in source
```

- [ ] **Step 2: Run UI contract tests**

Run:

```powershell
python -m pytest tests/test_project_ui.py -q
```

Expected: fail because `ProjectsView.jsx` is absent and header still says Carrito.

- [ ] **Step 3: Implement ProjectsView and navigation**

Use this component contract:

```javascript
export default function ProjectsView({
  request,
  onOpenProject,
  activeProjectId,
}) {
  // fetch /projects?status=active and archived on mount and after lifecycle actions
}
```

Each card renders:

```jsx
<article className="project-card">
  <strong>{project.name}</strong>
  <small>Actualizado: {formatDate(project.updated_at)}</small>
  <span>{project.summary.principals} productos · {project.summary.complements} complementos</span>
  <button onClick={() => onOpenProject(project.id)}>Abrir</button>
  <button onClick={() => duplicate(project)}>Duplicar</button>
  <button onClick={() => archive(project)}>Archivar</button>
</article>
```

Archived cards replace archive with restore. Add `["proyectos", "Proyectos",
FolderKanban]` to the sidebar, change header copy to `Proyecto (N)`, and route
`view === "proyectos"` to this component.

- [ ] **Step 4: Run UI tests and Vite build**

Run:

```powershell
python -m pytest tests/test_project_ui.py -q
npm --prefix mobiliti_saas/web run build
```

Expected: tests pass and Vite reports a successful build.

- [ ] **Step 5: Commit Projects view**

```powershell
git add mobiliti_saas/web/src/ProjectsView.jsx mobiliti_saas/web/src/main.jsx mobiliti_saas/web/src/styles.css tests/test_project_ui.py
git commit -m "feat: add projects workspace"
```

### Task 4: Reusable catalog picker with image preview

**Files:**
- Create: `mobiliti_saas/web/src/ProductPickerDialog.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`
- Test: `tests/test_project_ui.py`

**Interfaces:**
- Consumes: `GET /catalogs/search`, `mode`, and optional replacement summary.
- Produces: `onConfirm(product)` with canonical identity and display snapshot only.

- [ ] **Step 1: Write failing picker contract tests**

```python
def test_product_picker_covers_all_contexts_and_previews_images():
    source = Path("mobiliti_saas/web/src/ProductPickerDialog.jsx").read_text(encoding="utf-8")
    for mode in ('add:', '"replace-one":', '"replace-all":', 'complement:'):
        assert mode in source
    for copy in (
        "Agregar al Proyecto", "Cambiar producto",
        "Cambiar todos los iguales", "Agregar complemento",
    ):
        assert copy in source
    assert 'alt={selected.snapshot.name}' in source
    assert "Sin imagen" in source
    assert "/catalogs/search" in source
```

- [ ] **Step 2: Run the picker test**

Run:

```powershell
python -m pytest tests/test_project_ui.py::test_product_picker_covers_all_contexts_and_previews_images -q
```

Expected: fail because the picker file is absent.

- [ ] **Step 3: Implement the one shared picker**

Use:

```javascript
export default function ProductPickerDialog({
  open,
  mode,
  request,
  impact,
  onCancel,
  onConfirm,
}) {
  const [query, setQuery] = useState("");
  const [supplier, setSupplier] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      const params = new URLSearchParams({
        q: query,
        supplier,
        offset: String(offset),
        limit: "20",
      });
      const response = await request(
        `/catalogs/search?${params.toString()}`,
        {signal: controller.signal},
      );
      setResults(response.items);
      setTotal(response.total);
      setSelected((current) => (
        current && response.items.some((item) => (
          item.catalog === current.catalog
          && item.official_code === current.official_code
        )) ? current : null
      ));
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query, supplier, offset, request]);
  if (!open) return null;
  const confirmLabel = {
    add: "Agregar al Proyecto",
    "replace-one": "Cambiar producto",
    "replace-all": "Cambiar todos los iguales",
    complement: "Agregar complemento",
  }[mode];
  return (
    <div role="dialog" aria-modal="true" aria-label="Seleccionar producto">
      <input aria-label="Buscar producto" value={query}
        onChange={(event) => { setQuery(event.target.value); setOffset(0); }} />
      <select aria-label="Proveedor" value={supplier}
        onChange={(event) => { setSupplier(event.target.value); setOffset(0); }} />
      <div role="listbox" aria-label="Resultados">
        {results.map((item) => (
          <button type="button" role="option"
            aria-selected={selected === item}
            key={`${item.catalog}:${item.official_code}`}
            onClick={() => setSelected(item)}>
            {item.official_code} · {item.snapshot.name}
          </button>
        ))}
      </div>
      {selected && (
        <section className="project-product-preview">
          {selected.snapshot.image_url
            ? <img src={selected.snapshot.image_url} alt={selected.snapshot.name} />
            : <span>Sin imagen</span>}
          <strong>{selected.snapshot.name}</strong>
          <span>{selected.official_code}</span>
          <span>{selected.snapshot.availability}</span>
        </section>
      )}
      <button type="button" onClick={onCancel}>Cancelar</button>
      <button type="button" disabled={!selected}
        onClick={() => onConfirm(selected)}>{confirmLabel}</button>
      <span>{offset + 1}–{Math.min(offset + results.length, total)} de {total}</span>
    </div>
  );
}
```

The dialog must render one result grid, a selected-product large preview, provider,
official code, configuration, availability, and warnings. `replace-all` additionally
renders:

```jsx
<p>
  {impact.affected} ocurrencias · {impact.imported} importadas ·
  {impact.sections} secciones · {impact.removedComplements} complementos retirados ·
  {impact.excludedUnlinked} sin proveedor/código excluidas
</p>
```

No client price field is accepted or returned by `onConfirm`.

- [ ] **Step 4: Run UI tests and build**

Run:

```powershell
python -m pytest tests/test_project_ui.py -q
npm --prefix mobiliti_saas/web run build
```

Expected: all pass.

- [ ] **Step 5: Commit the picker**

```powershell
git add mobiliti_saas/web/src/ProductPickerDialog.jsx mobiliti_saas/web/src/styles.css tests/test_project_ui.py
git commit -m "feat: add reusable project product picker"
```

### Task 5: Full Project editor and quick panel

**Files:**
- Create: `mobiliti_saas/web/src/ProjectEditor.jsx`
- Modify: `mobiliti_saas/web/src/MixedCartDrawer.jsx`
- Modify: `mobiliti_saas/web/src/main.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`
- Test: `tests/test_project_ui.py`
- Test: `tests/test_mixed_catalog_cart_ui.py`

**Interfaces:**
- Consumes: Task 1 model operations, Task 2 autosave, Task 4 picker.
- Produces: full-screen `ProjectEditor` and catalog-page quick panel using the same Project state.

- [ ] **Step 1: Write failing editor-copy and action tests**

```python
def test_project_editor_has_tabs_and_line_actions():
    source = Path("mobiliti_saas/web/src/ProjectEditor.jsx").read_text(encoding="utf-8")
    for copy in (
        "Productos", "Datos de cotización", "Cambiar producto",
        "Cambiar todos los iguales", "Agregar complemento",
        "Guardando", "Guardado", "Cambios pendientes",
    ):
        assert copy in source
    assert "parentLineId" in source
    assert "quantityMode" in source


def test_quick_panel_has_only_project_copy():
    source = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(encoding="utf-8")
    assert 'aria-label="Proyecto activo"' in source
    assert "<h2>Proyecto</h2>" in source
    assert "Carrito" not in source
```

- [ ] **Step 2: Run editor contract tests**

Run:

```powershell
python -m pytest tests/test_project_ui.py -k "editor or quick_panel" -q
```

Expected: fail because `ProjectEditor.jsx` is absent and drawer copy is old.

- [ ] **Step 3: Implement the editor by composing current controls**

Use this prop contract:

```javascript
export default function ProjectEditor({
  project,
  request,
  autosave,
  onProjectChange,
  onGenerateQuote,
}) {}
```

Move the existing section header, rename, merge, line order, move-section, quantity,
and imported-edit controls into the `Productos` tab. Move `CUSTOMER_FIELDS`, currency,
and discount into `Datos de cotización`.

For each principal:

```jsx
<button onClick={() => openPicker("replace-one", line)}>Cambiar producto</button>
<button onClick={() => openPicker("replace-all", line)}>Cambiar todos los iguales</button>
<button onClick={() => openPicker("complement", line)}>Agregar complemento</button>
```

Render direct children inside the principal card:

```jsx
<article className="project-complement">
  {child.snapshot.image_url
    ? <img src={child.snapshot.image_url} alt={child.snapshot.name} />
    : <span>Sin imagen</span>}
  <strong>+ {child.snapshot.name}</strong>
  <select value={child.quantityMode}>
    <option value="per_parent_unit">Por unidad</option>
    <option value="fixed_project">Cantidad fija</option>
  </select>
</article>
```

Before replacing a principal, confirm:

```javascript
window.confirm(`Este cambio retirará ${children.length} complemento(s). ¿Continuar?`)
```

The quick panel displays the active Project summary and an `Editar Proyecto` action;
it must not own a second copy of Project state.

- [ ] **Step 4: Run editor, legacy control, and build gates**

Run:

```powershell
python -m pytest tests/test_project_ui.py tests/test_mixed_catalog_cart_ui.py -q
npm --prefix mobiliti_saas/web run build
```

Expected: all tests and build pass.

- [ ] **Step 5: Commit the Project editor**

```powershell
git add mobiliti_saas/web/src/ProjectEditor.jsx mobiliti_saas/web/src/MixedCartDrawer.jsx mobiliti_saas/web/src/main.jsx mobiliti_saas/web/src/styles.css tests/test_project_ui.py tests/test_mixed_catalog_cart_ui.py
git commit -m "feat: edit products in persistent projects"
```

### Task 6: Imported official-code fields and durable Project import

**Files:**
- Modify: `mobiliti_saas/web/src/ImportedCartLineFields.jsx`
- Modify: `mobiliti_saas/web/src/importedCartLineDraft.js`
- Modify: `mobiliti_saas/web/src/mixedCart.js`
- Modify: `mobiliti_saas/web/src/main.jsx`
- Test: `tests/test_project_model_ui.py`
- Test: `tests/test_project_ui.py`
- Test: `tests/test_quotation_import.py`

**Interfaces:**
- Consumes: import preview fields and `POST /projects/{project}/imports/{job}`.
- Produces: explicit imported `officialCode`, `provider`, and linkage status in saved Projects.

- [ ] **Step 1: Write failing imported-field tests**

```python
def test_imported_editor_exposes_official_code_and_provider():
    source = Path("mobiliti_saas/web/src/ImportedCartLineFields.jsx").read_text(encoding="utf-8")
    assert 'name="officialCode"' in source
    assert "Código oficial" in source
    assert 'name="provider"' in source
    assert "Vinculado" in source
    assert "No vinculado" in source
```

Add a Node test asserting that `createImportedCartBundle` copies
`item.official_code` and `item.provider`, and that `projectLineMatches` returns true
after whitespace/case normalization.

- [ ] **Step 2: Run imported UI tests**

Run:

```powershell
python -m pytest tests/test_project_model_ui.py tests/test_project_ui.py -k "imported" -q
```

Expected: fail because `officialCode` is not part of imported drafts.

- [ ] **Step 3: Extend the existing imported draft contract**

Change:

```javascript
const IMPORTED_EDIT_FIELDS = new Set([
  "officialCode", "name", "description", "dimension", "unitPrice", "provider",
]);
```

`officialCode` allows empty text but rejects control characters and Excel formula
prefixes. Add the input:

```jsx
<label>
  Código oficial
  <input name="officialCode" value={draft.values.officialCode}
    onChange={(event) => handleChange("officialCode", event.target.value)}
    onBlur={() => handleBlur("officialCode")} />
</label>
```

When accepting an import preview into an active Project, call the promotion endpoint
first, replace preview URLs with returned asset keys, then autosave the Project.
Do not consume or delete the import job.

- [ ] **Step 4: Run import, model, and UI regression**

Run:

```powershell
python -m pytest tests/test_project_model_ui.py tests/test_project_ui.py tests/test_quotation_import.py -q
npm --prefix mobiliti_saas/web run build
```

Expected: all pass.

- [ ] **Step 5: Commit imported Project linkage**

```powershell
git add mobiliti_saas/web/src/ImportedCartLineFields.jsx mobiliti_saas/web/src/importedCartLineDraft.js mobiliti_saas/web/src/mixedCart.js mobiliti_saas/web/src/main.jsx tests/test_project_model_ui.py tests/test_project_ui.py tests/test_quotation_import.py
git commit -m "feat: link imported lines by official code"
```

### Task 7: Browser workflow and responsive regression

**Files:**
- Modify: `tests/test_mixed_catalog_browser_e2e.py`
- Modify: `mobiliti_saas/web/src/styles.css`
- Modify: `mobiliti_saas/README.md`

**Interfaces:**
- Consumes: completed Project UI and persistence API.
- Produces: a locally testable Project workflow before Excel integration.

- [ ] **Step 1: Write the browser scenario**

```python
PROJECT_ID = "99999999-9999-4999-8999-999999999999"


def test_project_survives_reload_and_supports_replacements_and_complements(
    vite_url, browser
):
    stub = ApiStub([])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(
        browser, {"width": 1440, "height": 1000}, stub, vite_url
    )
    page.goto(vite_url)
    page.get_by_role("button", name="Proyectos").click()
    page.get_by_role("button", name="Nuevo Proyecto").click()
    page.get_by_label("Nombre del Proyecto").fill("QA Proyecto persistente")
    page.get_by_role("button", name="Agregar producto").click()
    page.get_by_label("Buscar producto").fill("OLIVE")
    page.get_by_role("option", name=re.compile("OLIVE-II")).click()
    page.get_by_role("button", name="Agregar al Proyecto").click()
    page.get_by_role("button", name="Agregar producto").click()
    page.get_by_label("Buscar producto").fill("OLIVE")
    page.get_by_role("option", name=re.compile("OLIVE-II")).click()
    page.get_by_role("button", name="Agregar al Proyecto").click()
    assert page.get_by_text("OLIVE-II", exact=True).count() == 2
    page.get_by_role("button", name="Agregar complemento").first.click()
    page.get_by_label("Buscar producto").fill("HEAD-1")
    page.get_by_role("option", name=re.compile("HEAD-1")).click()
    page.get_by_role("button", name="Agregar complemento").last.click()
    page.get_by_label("Modo de cantidad").select_option("fixed_project")
    page.get_by_label("Cantidad del complemento").fill("2")
    page.get_by_text("Guardado").wait_for(state="visible")
    page.reload()
    page.get_by_role("button", name="Proyectos").click()
    page.get_by_text("QA Proyecto persistente").click()
    assert page.get_by_text("OLIVE-II", exact=True).count() == 2
    assert page.get_by_text("+ HEAD-1").count() == 1
    context.close()
```

Add a viewport assertion at `390 × 844` that `.project-editor` fills the viewport and
the page has no horizontal overflow.

- [ ] **Step 2: Run the browser test and observe the first real failure**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py -k "project_survives_reload" -q
```

Expected: fail because `ApiStub.enable_project_routes` and the Project UI contracts
do not exist.

- [ ] **Step 3: Finish responsive CSS and browser fixture wiring**

Extend the existing `ApiStub` with:

```python
def enable_project_routes(self, *, project_id):
    self.project_id = project_id
    self.saved_project = None
    self.project_revision = 0
```

Add deterministic dispatch branches for:

- `GET /projects?status=active`;
- `GET /projects?status=archived`;
- `POST /projects`;
- `GET /projects/{PROJECT_ID}`;
- `PATCH /projects/{PROJECT_ID}`;
- `GET /catalogs/search`.

The PATCH branch must assert `expected_revision == self.project_revision`, store a
deep copy of `request.post_data_json["payload"]`, increment the revision, and return
the stored Project. The search branch returns exactly two products: Sunon
`OLIVE-II` and ALMA `HEAD-1`, each with canonical identity and a one-pixel data-URL
preview. Add these routes to `KNOWN_API_REQUESTS` so the existing network guard still
rejects every unstubbed request.

Add:

```css
@media (max-width: 720px) {
  .project-editor {
    position: fixed;
    inset: 0;
    width: 100vw;
    height: 100dvh;
    overflow: auto;
    z-index: 40;
  }
  .project-line-actions {
    grid-template-columns: 1fr;
  }
}
```

Use the existing `vite_url`, `browser`, `new_page`, `SESSION`, and network guard in
the same test file; do not launch a second backend.

- [ ] **Step 4: Run browser, unit, and build gates**

Run:

```powershell
python -m pytest tests/test_mixed_catalog_browser_e2e.py tests/test_project_ui.py tests/test_project_model_ui.py -q
npm --prefix mobiliti_saas/web run build
```

Expected: all pass.

- [ ] **Step 5: Commit the editor milestone**

```powershell
git add tests/test_mixed_catalog_browser_e2e.py mobiliti_saas/web/src/styles.css mobiliti_saas/README.md
git commit -m "test: cover persistent project editing workflow"
```
