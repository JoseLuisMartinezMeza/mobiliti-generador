# Project Card Rename and Archived Delete Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Renombrar Proyectos desde sus tarjetas y permitir su eliminación definitiva sólo
después de archivarlos y confirmar el nombre exacto.

**Architecture:** El renombrado reutiliza el guardado optimista existente. La eliminación
añade un endpoint estrecho que verifica propietario, estado, revisión y nombre antes de
eliminar el registro. La UI sólo expone la acción en archivados y limpia el Proyecto activo
si corresponde.

**Tech Stack:** FastAPI, Supabase/PostgreSQL/DEV store, React, pytest.

---

### Task 1: Definir seguridad de eliminación con pruebas API en rojo

**Files:**
- Modify: `tests/test_project_api.py`

**Step 1: Write the failing tests**

- Eliminar un Proyecto archivado con revisión y nombre exactos.
- Rechazar eliminación de un Proyecto activo.
- Rechazar nombre incorrecto.
- Rechazar revisión obsoleta.
- No permitir eliminar Proyectos de otro usuario.

**Step 2: Run test to verify it fails**

Run:
`python -m pytest tests/test_project_api.py -q`

Expected: FAIL porque `DELETE /projects/{id}` aún no existe.

### Task 2: Implementar eliminación en backend y sincronizar espejos

**Files:**
- Modify: `vercel_deploy/api/index.py`
- Modify: `mobiliti_saas/api/index.py`
- Modify: `mobiliti_saas/web/api/index.py`

**Step 1: Add storage operation**

- Implementar eliminación condicionada en DEV, PostgreSQL y Supabase.
- No eliminar archivos o activos de imágenes compartidos.

**Step 2: Add endpoint**

- Validar campos permitidos.
- Resolver propietario y existencia.
- Verificar `archived`, revisión y nombre exacto.
- Eliminar y devolver confirmación estable.

**Step 3: Run API tests**

Run:
`python -m pytest tests/test_project_api.py -q`

Expected: PASS.

### Task 3: Definir UI de renombrado y eliminación con pruebas en rojo

**Files:**
- Modify: `tests/test_project_ui.py`

**Step 1: Write the failing tests**

- Verificar controles `Renombrar`, `Guardar` y `Cancelar`.
- Verificar confirmación por nombre exacto.
- Verificar llamada `DELETE` y que la acción sólo se renderice para archivados.

**Step 2: Run test to verify it fails**

Run:
`python -m pytest tests/test_project_ui.py -q`

Expected: FAIL por ausencia de los controles.

### Task 4: Implementar UI y limpieza de estado activo

**Files:**
- Modify: `mobiliti_saas/web/src/ProjectsView.jsx`
- Modify: `mobiliti_saas/web/src/App.jsx`
- Modify: `mobiliti_saas/web/src/styles.css`

**Step 1: Add inline rename**

- Añadir estado local por tarjeta, validación y guardado optimista por revisión.

**Step 2: Add archived-only delete**

- Mostrar la acción sólo en archivados.
- Solicitar el nombre exacto antes de invocar el endpoint.
- Recargar la lista y notificar a `App` después de éxito.

**Step 3: Clear active deleted project**

- Cancelar autoguardados pendientes y vaciar el estado del Proyecto si su id coincide.

**Step 4: Run UI tests and build**

Run:
`python -m pytest tests/test_project_ui.py -q`

Run:
`npm run build --prefix mobiliti_saas/web`

Expected: PASS.

### Task 5: Verificación integrada

**Files:**
- No production file changes expected.

**Step 1: Run focused suites**

Run:
`python -m pytest tests/test_project_api.py tests/test_project_ui.py -q`

Expected: PASS.

**Step 2: Smoke test local UI**

- Renombrar un Proyecto activo.
- Archivar, confirmar nombre exacto y eliminar.
- Confirmar que no queda en listas y que un Proyecto activo no ofrece eliminación.
