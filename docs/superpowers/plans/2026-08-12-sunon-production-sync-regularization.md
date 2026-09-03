# Plan: regularización productiva de SUNON

## Objetivo

Dejar SUNON sincronizando sus cinco XLSX autorizados desde OneDrive, sin perder las 17 imágenes de referencia ya aprobadas y con una primera publicación canary verificable y reversible.

## Restricciones globales

- Preservar todos los cambios locales ajenos y de Offiho.
- No incorporar el PDF de sillas, el `.cmdrw` ni la plantilla de pedidos.
- No exponer secretos en logs, archivos versionados ni respuestas.
- Toda mutación productiva debe tener lectura previa, validación posterior y rollback explícito.
- El botón de usuario **Refrescar** continúa siendo lectura; la sincronización la ejecuta el worker.

## Task 1 — Cursor Graph recuperable

- Escribir pruebas RED para el marcador bootstrap `manual://...` y para `DeltaExpiredError`.
- Hacer que el servicio trate únicamente el marcador bootstrap válido como ausencia de cursor.
- Reintentar una sola vez con `delta=None` cuando Graph responda 410.
- Evitar que futuras promociones escriban un pseudo-cursor.
- Verificación: pruebas focales de servicio/Graph/promoción y regresión completa del sync.

## Task 2 — Preservación visual SUNON

- Escribir una prueba RED con un item publicado `generated_reference` y candidato `placeholder`.
- Preservar sólo un `approved_asset` seguro y aprobado para el mismo `internal_id`/identidad estable.
- Rechazar metadatos inseguros, activos oficiales faltantes o referencias no aprobadas.
- Verificación: candidato conserva 17 referencias; los 245 activos oficiales siguen saliendo del importador.

## Task 3 — Configuración y despliegue del worker

- Confirmar el proyecto Supabase correcto y la configuración Graph productiva sin mostrar valores.
- Instalar/montar certificado y secretos mediante el mecanismo productivo existente.
- Desplegar la imagen que contiene Tasks 1–2.
- Activar `CATALOG_SYNC_ENABLED=true` manteniendo la allowlist vigente.
- Verificación: health del worker, fingerprint de imagen/commit y estado de configuración.

## Task 4 — Full crawl canary de los cinco archivos

- Respaldar el estado lógico de fuente, cursor y publicación.
- Sustituir atómicamente los identificadores placeholder de drive/raíz por los IDs canónicos ya verificados y reiniciar el cursor SUNON.
- Solicitar una sola corrida manual después de comprobar la configuración persistida.
- Comprobar 5/5 `source_files`, hashes actuales, 262 variantes, 149 modelos, 14,085 PZA y 262 activos aprobados.
- Si el candidato es material, mantenerlo sin publicar hasta completar la revisión.
- Rollback: desactivar el flag y restaurar cursor/publicación anterior sin borrar versiones.

## Task 5 — Publicación y cierre E2E

- Revisar y publicar el candidato canary con un admin activo.
- Verificar `published_version_id`, hash/frescura, API y vista SUNON.
- Confirmar que una segunda corrida sea `no_changes` y deje un delta Graph válido.
- Actualizar Obsidian con cambios, versión, pruebas, rollback y riesgos residuales.
