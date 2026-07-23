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

En esta mÃ¡quina `npm.ps1` estÃ¡ bloqueado por la Execution Policy de PowerShell, por lo que se usÃ³ el ejecutable oficial `npm.cmd`.

## Alcance y riesgos residuales

- No hubo cambios de backend, despliegue, remote ni archivos ajenos.
- El editor usa los contratos reales de API y las reglas canÃ³nicas de serializaciÃ³n/hidrataciÃ³n.
- La resoluciÃ³n de conflicto es conservadora: bloquea nuevas ediciones y pide reabrir la versiÃ³n mÃ¡s reciente desde Proyectos.
