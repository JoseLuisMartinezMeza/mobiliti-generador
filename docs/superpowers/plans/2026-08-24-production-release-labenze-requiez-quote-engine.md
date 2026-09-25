# Despliegue de producción Labenze/Requiez y motor de cotización

**Objetivo:** publicar el worktree aprobado `codex/offiho-catalog-20260709`, incluidos Labenze, Requiez y las correcciones del motor de cotización, manteniendo rollback verificable en cada servicio.

**Alcance mínimo:** código/API/web/worker, migración aditiva de Supabase, imágenes aprobadas en Supabase Storage, verificación del almacenamiento de cotizaciones en R2 y actualización de Vercel/Hetzner. Se excluyen salidas E2E, capturas, archivos de trabajo y eliminaciones históricas no relacionadas.

## 1. Gate local

- Confirmar branch, HEAD y ausencia de commits remotos nuevos.
- Validar que los bundles duplicados de API y motor sean idénticos.
- Ejecutar pruebas completas, build web y chequeos de migración/preflight.
- Revisar el diff que se va a indexar y comprobar que no contiene secretos.

## 2. Release Git

- Indexar sólo archivos aprobados de código, pruebas, migración y activos runtime pequeños.
- Crear un commit de release y empujar sin `--force` a `origin/codex/offiho-catalog-20260709`.
- Registrar SHA exacto para Vercel y Hetzner.

## 3. Supabase y catálogos

- Exportar el estado previo de fuentes/snapshots Labenze y Requiez a un respaldo local con timestamp.
- Aplicar una sola vez la migración aditiva `2026_08_labenze_requiez_catalogs.sql`.
- Verificar constraints, fuentes habilitadas y RPCs antes de iniciar sincronización.

## 4. Supabase Storage y Cloudflare R2

- Publicar los activos de catálogo content-addressed en `catalog-assets` de Supabase Storage; no sobrescribir claves con contenido diferente.
- Confirmar cantidad, hashes y acceso público de producción de las imágenes.
- Validar credenciales, bucket y CORS de R2 con `r2_doctor.py` sin imprimir secretos; comprobar la escritura y descarga mediante una cotización real.

## 5. Vercel y Hetzner

- Crear deployment de producción Vercel sin mover el dominio, validar health/API/web y luego promoverlo.
- Desplegar el worker Hetzner desde el SHA exacto mediante el script existente, conservando contenedor y release anteriores.
- Confirmar health, imagen/commit activo, R2 y sincronización de catálogos.

## 6. Verificación final

- Probar login, catálogos Labenze/Requiez, imágenes, carrito mixto y generación de una cotización pequeña.
- Descargar el XLSX y validar que el precio uniforme para productos repetidos conserva subtotales, que no se procesan imágenes de catálogos nativos y que las descripciones/notas cumplen las reglas aprobadas.
- Actualizar Obsidian con SHA, IDs de despliegue, conteos, resultados y procedimiento de rollback.
