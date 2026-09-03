# Task 5 — Editor completo de Proyecto y panel rÃ¡pido

Fecha: 2026-07-23

## Resultado

Se implementÃ³ un editor de Proyecto de pantalla completa sobre el estado canÃ³nico existente de `App`. No se agregÃ³ un segundo store:

- `ProjectEditor.jsx` compone las operaciones de Task 1 para secciones, orden, movimiento, cantidades, ediciÃ³n importada, reemplazos y complementos.
- Las pestaÃ±as `Productos` y `Datos de cotizaciÃ³n` separan el contenido del Proyecto.
- Los productos principales contienen sus complementos directos, con modos `Por unidad` y `Cantidad fija`.
- El selector de Task 4 se usa en contextos `add`, `replace-one`, `replace-all` y `complement`.
- Los reemplazos que retirarÃ­an complementos requieren confirmaciÃ³n explÃ­cita.
- `MixedCartDrawer` funciona en runtime como panel rÃ¡pido de resumen y abre el mismo Proyecto en el editor.
- Abrir un Proyecto ejecuta `GET /projects/{id}` y `hydrateProject`.
- El autosave de Task 2 ejecuta `PATCH /projects/{id}` con `expected_revision` y `operation_id`; muestra estados pendiente, guardando, guardado y conflicto.
- La generaciÃ³n reutiliza el Ãºnico controller de cotizaciÃ³n y conserva el Proyecto persistente despuÃ©s de terminar.

## Evidencia TDD

RED inicial:

```text
python -m pytest tests/test_project_ui.py -k "editor or quick_panel or opens_hydrates" -q
5 failed
```

Los fallos correspondieron a `ProjectEditor.jsx` ausente, copy anterior del drawer y falta de integraciÃ³n GET/PATCH.

RED adicional:

```text
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "project_submit_reuses" -q
1 failed
```

El controller vaciaba las lÃ­neas al generar; se agregÃ³ `preserveProject: true` y se verificÃ³ que conserva lÃ­neas y secciones.

RED de estilos:

```text
python -m pytest tests/test_project_ui.py -k "layout_has" -q
1 failed
```

Se agregaron los estilos del editor, tarjetas, autosave, panel rÃ¡pido y layout mÃ³vil.

## VerificaciÃ³n

```text
python -m pytest tests/test_project_ui.py tests/test_project_model_ui.py tests/test_project_autosave_ui.py tests/test_mixed_catalog_cart_ui.py -q
102 passed

npm.cmd --prefix mobiliti_saas/web run build
vite build: PASS
```

## Remediacion del rereview independiente

Fecha: 2026-07-23

Se corrigieron los dos hallazgos Important restantes de
`.superpowers/sdd/project-editor-task-5-rereview.md`:

1. Importar una Quotation sin Proyecto activo ya no reemplaza secciones ni lineas.
   El borrador se conserva en `App`, se muestra un mensaje accionable y se navega a
   Proyectos. El borrador se adopta al crear un Proyecto o al abrir uno existente.
2. `Nuevo Proyecto` adopta el estado local completo en un unico `POST /projects`,
   incluida una importacion pendiente, y activa directamente la respuesta hidratada.
   Un guard con `useRef` impide POST duplicados durante clics concurrentes.

Evidencia RED:

```text
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "pending_import_draft_is_adopted" -q
1 failed: Missing JavaScript helper: projectStateWithImportDraft

python -m pytest tests/test_project_ui.py -k "blocked_import_is_parked" -q
1 failed: el flujo no estacionaba el borrador ni navegaba a Proyectos
```

Verificacion final:

```text
python -m pytest tests/test_project_ui.py tests/test_project_model_ui.py tests/test_project_autosave_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_project_api.py -q
124 passed in 18.60s

npm.cmd --prefix mobiliti_saas/web run build
vite build: PASS (1712 modules transformed)
```

## Remediacion de la revision de cierre

Fecha: 2026-07-23

Se corrigio el ultimo hallazgo Important de
`.superpowers/sdd/project-editor-task-5-closure-review.md`:

- La creacion usa una base vacia canonica cuando hay Proyecto activo y nunca copia sus
  lineas. Si existe una importacion pendiente, se aplica explicitamente sobre esa base
  vacia y solo la importacion forma parte del POST.
- El plan de creacion transporta por separado `projectState` y `submittedAdoption`.
  `ProjectsView` entrega esa identidad a la activacion unicamente tras confirmar el POST.
- La activacion limpia el borrador solo si sigue siendo exactamente el que se envio.
  Un fallo conserva el borrador, una creacion sin adopcion no lo limpia y una respuesta
  atrasada no puede borrar un borrador mas nuevo.

Evidencia RED:

```text
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "new_project_from_active or new_project_without_active or active_project_pending_import_creation or confirmed_creation_clears" -q
4 failed: faltaban plan explicito e identidad de adopcion enviada

python -m pytest tests/test_project_ui.py -k "draft_distinguishes or passes_submitted_adoption or failed_new_project_request" -q
2 failed, 1 passed: faltaba propagar la adopcion confirmada; el fallo ya evitaba activar
```

Verificacion final:

```text
python -m pytest tests/test_project_autosave_ui.py tests/test_project_ui.py tests/test_project_model_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_project_api.py -q
143 passed in 30.48s

npm.cmd --prefix mobiliti_saas/web run build
vite build: PASS (1712 modules transformed)
```

## Remediacion de la revision de aprobacion

Fecha: 2026-07-23

Se corrigieron los tres hallazgos Important de
`.superpowers/sdd/project-editor-task-5-approval-review.md`:

1. `Nuevo Proyecto` usa el estado vacio canonico cuando ya existe un Proyecto activo.
   Solo adopta lineas huerfanas o una importacion pendiente cuando no hay Proyecto
   activo; la accion separada `Duplicar` conserva su responsabilidad original.
2. El cambio de Proyecto es atomico: la identidad, lineas, secciones y datos anteriores
   permanecen visibles/propietarios hasta completar GET, hidratacion y validacion. Un
   fallo no desposee el estado anterior y el mismo destino puede reintentarse. Cada
   carga confirmada recibe una clave de instancia para sincronizar tambien una reapertura
   del mismo ID tras conflicto.
3. `canMutateProject` centraliza la regla para entradas externas. Catalogos e importacion
   se bloquean durante carga o conflicto 409 con la misma instruccion de reapertura, sin
   mutar lineas ni incrementar la version. El borrador importado se conserva.

Evidencia RED:

```text
python -m pytest tests/test_mixed_catalog_cart_ui.py -k "new_project_from_active or new_project_without_active or failed_project_switch or conflict_blocks_catalog or loading_target_blocks" -q
5 failed: faltaban seleccion de payload, carga atomica y regla central

python -m pytest tests/test_project_ui.py -k "draft_distinguishes or commits_identity_only or external_line_entries_share" -q
3 failed: App aun usaba los flujos anteriores
```

Verificacion final:

```text
python -m pytest tests/test_project_autosave_ui.py tests/test_project_ui.py tests/test_project_model_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_project_api.py -q
139 passed in 27.72s

npm.cmd --prefix mobiliti_saas/web run build
vite build: PASS (1712 modules transformed)
```

En esta mÃ¡quina `npm.ps1` estÃ¡ bloqueado por la Execution Policy de PowerShell, por lo que se usÃ³ el ejecutable oficial `npm.cmd`.

## Alcance y riesgos residuales

- No hubo cambios de backend, despliegue, remote ni archivos ajenos.
- El editor usa los contratos reales de API y las reglas canÃ³nicas de serializaciÃ³n/hidrataciÃ³n.
- La resoluciÃ³n de conflicto es conservadora: bloquea nuevas ediciones y pide reabrir la versiÃ³n mÃ¡s reciente desde Proyectos.

## RemediaciÃ³n del review independiente

Fecha: 2026-07-23

Se corrigieron los cuatro hallazgos Important de
`.superpowers/sdd/project-editor-task-5-review.md`:

1. Seleccionar un complemento abre un paso de configuraciÃ³n pendiente. El target del
   picker usa una regla segura documentada (`min=1`, `step=1`, entero), cantidad propia
   `1` y no hereda cantidad ni reglas del principal. Modo, cantidad e impacto se revisan
   antes de confirmar; el estado del Proyecto no cambia y no se autoguarda antes de esa
   confirmaciÃ³n.
2. La generaciÃ³n proyecta `per_parent_unit` mediante multiplicaciÃ³n decimal exacta
   `parent.quantity Ã— child.quantity`; `fixed_project` permanece intacto. La proyecciÃ³n
   es inmutable y lleva una marca efÃ­mera para impedir multiplicaciÃ³n doble.
3. Los targets de catÃ¡logo guardan `provider = selection.catalog`. La etiqueta humana
   se calcula sÃ³lo para presentaciÃ³n; CR Global conserva matching antes y despuÃ©s del
   ciclo serialize/hydrate.
4. Agregar desde un catÃ¡logo sin Proyecto activo se bloquea con mensaje accionable y
   navegaciÃ³n a Proyectos. `Nuevo Proyecto` hace `POST /projects` con un payload vacÃ­o
   serializado y validado por el contrato backend, y abre el ID creado.

TambiÃ©n se aplicaron los dos ajustes menores relacionados: el panel rÃ¡pido cerrado se
desmonta y las imÃ¡genes rotas del editor muestran `Sin imagen`.

Evidencia RED:

```text
python -m pytest tests/test_project_ui.py -k "picker_target or cr_global or complement_selection or quote_projection or catalog_add_requires or closed_quick" -q
6 failed

python -m pytest tests/test_project_ui.py -k "new_project_flow_posts" -q
1 failed

python -m pytest tests/test_project_ui.py -k "layout_has" -q
1 failed
```

VerificaciÃ³n final de remediaciÃ³n:

```text
python -m pytest tests/test_project_ui.py tests/test_project_model_ui.py tests/test_project_autosave_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_project_api.py -q
120 passed

npm.cmd --prefix mobiliti_saas/web run build
vite build: PASS
```

## Remediacion de la revision final

Fecha: 2026-07-23

Se corrigio el ultimo hallazgo Important de
`.superpowers/sdd/project-editor-task-5-final-review.md`:

- `useProjectAutosave` recibe una identidad explicita de Proyecto. Al cambiar de
  Proyecto sincroniza la revision cargada antes de programar un cambio sucio, por lo
  que el primer PATCH de un Proyecto en revision 7 usa `expected_revision: 7`.
- Una actualizacion del mismo Proyecto con cambios pendientes no reinicia la revision
  ni la operacion en vuelo.
- El PATCH usa el ID del snapshot, no una clausura del Proyecto activo, y una respuesta
  vieja de otro Proyecto no puede confirmar ni limpiar la adopcion actual.
- La importacion adoptada en un Proyecto existente permanece pendiente durante fallos
  de red y conflictos 409. Solo se limpia al confirmar el PATCH identificado que la
  contiene. La creacion sigue limpiandola unicamente despues del POST exitoso.

Evidencia RED:

```text
python -m pytest tests/test_project_autosave_ui.py -k "project_switch_synchronizes or same_dirty_project or explicit_project_identity" -q
3 failed: faltaban identidad y sincronizacion explicitas

python -m pytest tests/test_mixed_catalog_cart_ui.py -k "confirmed_adoption_save or failed_or_conflicted_adoption or stale_prior_project_save" -q
3 failed: faltaba el coordinador de persistencia de adopcion

python -m pytest tests/test_project_ui.py -k "existing_project_adoption_retains" -q
1 failed: el borrador se limpiaba antes del PATCH
```

Verificacion final:

```text
python -m pytest tests/test_project_autosave_ui.py tests/test_project_ui.py tests/test_project_model_ui.py tests/test_mixed_catalog_cart_ui.py tests/test_project_api.py -q
131 passed in 24.85s

npm.cmd --prefix mobiliti_saas/web run build
vite build: PASS (1712 modules transformed)
```
