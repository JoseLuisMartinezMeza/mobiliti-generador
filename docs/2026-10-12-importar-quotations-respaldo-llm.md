# Importar Quotations no estándar, anexar a Proyectos y corrección de descuento y margen

El usuario autorizó el commit, el push, el despliegue en producción y la prueba de extremo a extremo el 12 de octubre de 2026.

## Cambios

- **Respaldo con Claude.** Un Excel o PDF que no sigue el formato `Quotation` se convierte con Haiku o Sonnet y continúa por el código existente. Se activa con `QUOTE_LLM_FALLBACK_MODEL` (`haiku` o `sonnet`) y requiere `ANTHROPIC_API_KEY`; vacío queda apagado y el comportamiento es el anterior. Cada conversión registra modelo, tokens y costo en los metadatos del trabajo (`llm_fallback`).
- **Importar otra Quotation.** El editor de Proyectos tiene una pestaña que anexa otra fuente al mismo Proyecto. La API combina las fuentes en una sola (`pdf_quotation_import/combinar.py`) y la web sólo agrega las filas nuevas; las líneas existentes conservan sus cambios. El borrador de importación se descarta al guardar.
- **Proveedor oficial.** Las líneas importadas eligen proveedor de la lista de la plantilla (`GET /proveedores-oficiales`).
- **Descuento de la carátula.** `Cotizacion!G` de cada partida usa el descuento que calcula `Mobiliti` (`AE`, que respeta el tope del proveedor) en lugar del solicitado para todas (`Mobiliti!$AD$14`). Conserva el redondeo a dos decimales de la plantilla.
- **Margen.** `Mobiliti!AN` marca `ERROR` cuando el margen no es calculable; antes un margen vacío por error daba `OK`.
- **Bundle Vercel.** `mobiliti_saas/web/pdf_quotation_import/` es copia del importador, con prueba de paridad. La API web agrega `PyMuPDF` y `anthropic`; el lock del worker agrega `anthropic==0.100.0` y sus 12 dependencias.

## Pruebas que estaban atrasadas

Ocho pruebas fallaban también en `4ef5eaca` (la rama publicada), sin relación con este cambio. Siete tenían expectativas anteriores a las plantillas V11–V13, al proveedor Santeco o a la revisión editorial del 30 de septiembre, y se actualizaron:

| Prueba | Motivo |
|---|---|
| `test_composer_handles_imported_goods_without_shipping_volume` | `Fletes!B66` usa los límites `E67`/`E68` desde V11 |
| `test_delivery_dropdown_and_mobiliti_reference_follow_dynamic_terms` | La lista de destinos llega a `A56` desde V13 |
| `test_active_engine_uses_authoritative_catalog_canonical_identity` | Tarkett se llama `Santeco` desde V12 |
| `test_vercel_keeps_static_catalog_assets_out_of_python_function_bundle` | `vercel.json` excluye también `dist/**` |
| `test_real_verified_lumbro_item_crosses_api_worker_and_xlsx_without_second_discount` | La redacción comercial conserva la nota normalizada |
| `test_dev_mode_import_preview_mixed_checkout_worker_and_xlsx` | `Mobiliti!Y` incluye Sonara y Santeco; el margen pasó de `AL` a `AN` |
| `test_proyecto_mixto_local_cubre_busqueda_configuracion_reemplazo_y_conversion` | La redacción comercial inicia con mayúscula |

`test_builder_conserva_spec_y_sidecar_con_nombres_logicos_oficiales` sigue fallando: el constructor Node de la guía IDÉLIKA escribe en esta máquina un panel congelado distinto al esperado (`IDELIKA_SPEC_PANE_EXISTING:Consolidado`). No se modificó.

## Validación local

- Suites de API, importador, proveedores, fórmulas, plantillas y bundle: 687 aprobadas.
- Copia limpia del commit `6122b56f`: 612 aprobadas; un fallo propio de la longitud de ruta de la carpeta temporal en Windows.
- Réplica de Vercel (Python 3.12 en Linux, sólo `mobiliti_saas/web/requirements.txt`): `pip check` limpio, la API importa y resuelve el importador desde el bundle; 358 aprobadas y dos fallos por herramientas ausentes en el contenedor (`node`, archivos SQL no copiados).
- Imagen Docker del worker construida desde la copia limpia: `pip check` limpio, importaciones correctas con sistema de archivos de sólo lectura, UID 10001 y sin capacidades.

## Publicación

- Rama `codex/importar-quotations-llm-20261012`, creada sobre `4ef5eaca`. Commits `6122b56f` (cambio) y `82e74d56` (ruta `/proveedores-oficiales` en `vercel.json`, detectada al probar el despliegue preparado).
- **Worker Hetzner:** `CURRENT` e imagen activos en `6122b56f9a892b9e0536e0ab47a941115aa7a6bd`; sano, sin reinicios, `isolated_jobs=true`. Respaldo conservado: `mobiliti-worker-backup-20261012064653-be14accc6eea` (imagen `3f198b1d`). El servidor no tiene credenciales de GitHub: el commit se entregó como bundle de git en `/opt/mobiliti-worker/releases/` y el despliegue lo leyó con una reescritura de URL limitada a ese comando.
- **Vercel:** `dpl_CDRhcsXxKaAVVf1NH4czcSgRZAyr`, preparado sin dominio, probado por su URL protegida y promovido a `web-lemon-one-45.vercel.app`. Función Python de 208.95 MB (antes 111.14 MB). Despliegue anterior: `dpl_FMZTb1M6Cwy4QcQjpHb4oGXRHUXL`.
- El respaldo con Claude queda **apagado en producción**: ninguno de los dos entornos tiene `ANTHROPIC_API_KEY` ni `QUOTE_LLM_FALLBACK_MODEL`.

## Verificación en producción

- Dominio productivo: `/health` correcto, `/proveedores-oficiales` responde desde la API (401 sin sesión) y el JavaScript publicado contiene la pestaña y el menú nuevos.
- Imagen productiva del worker, en un contenedor desechable del servidor: generó una cotización de 13 partidas; las 13 usan el descuento por partida y las 13 la fórmula nueva de margen. Recalculada en Excel: con 40 % solicitado la carátula muestra 40 % y el mismo precio final que `Mobiliti`; con 60 % (tope de Sunon Inc: 50 %) `Mobiliti` marca `ERROR` y la carátula muestra 50 %. Archivo: `output/respaldo-llm-20261011/v6-Cotizacion_imagen_produccion.xlsx`.
- Pendiente: recorrido por la interfaz con sesión iniciada y activación del respaldo con Claude.

## Observaciones fuera de este cambio

- Generar directamente («Nueva cotización») con la Quotation de fábrica ESSENTIA falla con «Más de una imagen Quotation para la misma fila», igual que con la imagen anterior del worker. Por Proyectos sí se importa.
- Con plantillas V11 o posteriores, `Fletes!B66` vale 0 cuando no hay volumen aunque el modo sea `MANUAL`; antes de V11 el factor manual tenía prioridad.
- El fondo blanco de algunas fotos se pierde al segmentar la imagen; no se modificó esa ruta.
