# Task 6 — Migrador idempotente de `catalog-assets` desde el mirror local

Fecha: 2026-09-01

Base: `75e89ace855a7ab60e386ff62a653b57423b38fa`

Alcance del commit: script standalone, prueba focal y este reporte.

Estado: implementación local verificada; **Gate 6 live no ejecutado ni reclamado**.

## Resultado

Se creó `scripts/migrate_catalog_assets_to_r2.py` como herramienta standalone. No importa las APIs monolíticas ni `scripts/r2_doctor.py`, no descarga desde Supabase Storage y no ofrece override del directorio fuente. El origen queda fijado a `.mobiliti_dev_store/catalog-assets` dentro del repositorio.

La interfaz operacional exige siempre:

- `--manifest <archivo externo>`;
- `--expected-manifest-file-sha256 <sha256>` como trust anchor fuera del propio manifiesto;
- `--report <json>`;
- `--execute` para habilitar cualquier cliente o llamada de red;
- `--checkpoint <json>` opcional en execute; por omisión usa un archivo vecino `*.checkpoint.json` distinto del reporte.

Sin `--execute`, la herramienta sólo lee y valida el manifiesto y el mirror local. No consulta variables de entorno, no construye cliente R2/Supabase y no realiza HEAD, PUT, GET o RPC. El reporte dry-run siempre queda `certified=false`.

Las rutas de salida se validan antes de cualquier lectura/escritura operacional: reporte y checkpoint no pueden sobrescribir el manifiesto, coincidir entre sí ni ubicarse dentro del mirror fuente. Tanto checkpoint como reporte se escriben a temporal privado, se sincronizan y se publican con `os.replace`; ambos se conservan.

## Autoridad del manifiesto

El script fija el contrato productivo en:

- `schema_version=1`;
- bucket lógico exacto `catalog-assets`;
- 2,214 entries;
- 678,858,152 bytes;
- `image/png=1,568`, `image/webp=556`, `image/jpeg=90`;
- entry exacta con `object_name`, `sha256`, `byte_size`, `mime_type`.

Primero verifica el SHA-256 externo sobre los bytes exactos del archivo y sólo después decodifica JSON. Cada nombre debe ser 64 caracteres hexadecimales minúsculos más `.png`, `.jpg`, `.jpeg` o `.webp`, sin rutas; el stem debe ser idéntico al SHA declarado y la extensión fija el MIME. Se rechazan duplicados, tamaños no positivos, MIME incompatibles y cualquier divergencia de los agregados declarados.

Los dos digests se reproducen exactamente como Task 3/PostgreSQL:

1. `keyset_digest`: SHA-256 de `object_name` ordenados y unidos por `\n`.
2. `manifest_digest`: SHA-256 de filas ordenadas `object_name|sha256|byte_size|mime_type`, unidas por `\n`.

El keyset y los digests nunca se derivan del listado del directorio. El directorio aporta bytes a verificar, no autoridad lógica.

## Auditoría local fail-closed

Antes de leer configuración execute o construir clientes, las 2,214 entradas manifestadas se recorren completamente. Para cada archivo se comprueba:

- existencia;
- archivo regular y no symlink/reparse point;
- identidad entre `lstat`, descriptor abierto y `fstat`;
- estabilidad de identidad, tamaño y `mtime_ns` tras el streaming;
- tamaño exacto;
- SHA-256 completo;
- magic bytes de PNG, JPEG (`jpg`/`jpeg`) y WebP.

Un faltante, cambio TOCTOU, hash/tamaño/MIME/magic inválido detiene la ejecución antes de red.

Los objetos locales que no aparecen en el manifiesto se recorren exclusivamente como metadata de directorio: nombre y `lstat`, nunca apertura del cuerpo. Se reportan bajo `excluded_unmanifested` con count, bytes y digest determinista de `name|size`. No entran al keyset certificado y nunca se suben, listan remotamente o eliminan. Esto implementa el ruling para los 770 extras conocidos sin convertir su presencia en una falla por sí misma.

## Contrato R2

El modo execute lee sólo estas variables:

- `CATALOG_ASSET_R2_ENDPOINT_URL`;
- `CATALOG_ASSET_R2_ACCESS_KEY_ID`;
- `CATALOG_ASSET_R2_SECRET_ACCESS_KEY`;
- `CATALOG_ASSET_R2_REGION`;
- `CATALOG_ASSET_R2_BUCKET`;
- `SUPABASE_URL`;
- `SUPABASE_SERVICE_KEY`.

El bucket R2 debe ser exactamente `catalog-assets`. El cliente boto3 recibe endpoint, región y ambas credenciales explícitamente; no usa el credential chain AWS, `R2_*`, `quote-files`, dominio público ni API token. Los reintentos internos de botocore se deshabilitan para que el límite quede en la política propia y observable.

Para cada entry no completada en checkpoint:

1. HEAD exacto.
2. Si existe, exige `ContentLength`, `ContentType`, `Metadata.sha256` y `CacheControl: public, max-age=31536000, immutable`; cualquier mismatch falla y jamás intenta corregir con PUT.
3. Un 404 ejecuta PUT create-only con `IfNoneMatch=*` y headers/metadata exactos, seguido de HEAD exacto.
4. Un 412 de carrera ejecuta HEAD exacto y sólo acepta el objeto si coincide completamente.

No hay llamadas list/delete/copy, overwrite, ETag ni métodos de sync destructivo.

401/403 abren el circuito inmediatamente. 408, 429, 5xx y timeouts/errores transitorios reciben reintentos limitados con exponential backoff y jitter inyectables. Los demás 4xx fallan sin reintento. Los mismos límites cubren HEAD, PUT y GET, y todos los fallos públicos se reducen a códigos sanitizados.

Después de terminar HEAD/PUT para el keyset entero, el mismo proceso hace un GET streaming nuevo de **cada una** de las 2,214 entries, incluso al reanudar desde checkpoint. Cada GET vuelve a verificar headers, byte count y SHA-256 completo; el Body se cierra en éxito y error. Un solo fallo impide cualquier RPC.

## Registro Task 3 y reanudación

El UUID de batch es determinista (`UUIDv5`) y queda ligado al SHA del archivo externo, `manifest_digest` y `keyset_digest`. El checkpoint guarda esa unión, los nombres con HEAD/PUT completado y el estado RPC. Un checkpoint con otra autoridad, nombre ajeno o duplicado se rechaza.

Sólo después de los 2,214 GET completos se ejecuta el orden exacto:

1. `saas_start_catalog_asset_cutover_batch`;
2. 2,214 × `saas_add_catalog_asset_cutover_entry`;
3. 2,214 × `saas_register_catalog_asset` con `provider=r2` y `physical_bucket=catalog-assets`;
4. `saas_finalize_catalog_asset_cutover_batch`.

Los payloads coinciden con las firmas SQL de Task 3. Un error intermedio no llama finalize. Reejecutar un batch parcial repite las operaciones idempotentes con el mismo UUID; un checkpoint que ya registró finalize omite RPC, pero aún repite todos los GET de la ejecución actual. `certified=true` sólo se emite después de un finalize exitoso previamente persistido en el checkpoint o completado en la ejecución actual.

El cliente Supabase sólo llama `/rest/v1/rpc/<función>` y limita la respuesta; no llama endpoints de Supabase Storage ni obtiene cuerpos de assets.

## Reporte y redacción

El JSON final incluye:

- modo, timestamps inicial/final y `certified`;
- SHA del archivo manifiesto, keyset digest y manifest digest;
- expected/observed de count, bytes y MIME;
- count/bytes/digest de `excluded_unmanifested`;
- attempts, retries, HEAD, PUT, existing, created, 412 y full GET;
- estado/count RPC;
- códigos de fallo sanitizados.

Los escritores JSON eliminan defensivamente campos con credenciales, tokens, Authorization, headers, endpoints y excepciones crudas; también redaccionan URLs y strings Bearer. Los contadores parciales se conservan si execute falla, mientras `certified` permanece false.

## TDD RED → GREEN

RED inicial, antes de crear producción:

```text
python -B -m pytest -p no:cacheprovider tests/test_catalog_asset_r2_migration.py -q
ImportError: cannot import name 'migrate_catalog_assets_to_r2' from 'scripts'
1 error during collection
```

Primer GREEN después de implementar el contrato:

```text
38 passed, 1 skipped in 4.55s
```

El skip fue la creación real de symlink, no disponible en esta sesión Windows. Se añadió además una prueba determinista del atributo Windows reparse que no depende de privilegios y comprueba que el cuerpo jamás se abre.

RED de hardening de outputs/reporte:

```text
2 failed, 39 deselected
```

Los fallos demostraron que el escritor aún aceptaba un objeto Exception no serializable y que un reporte de fallo perdía los intentos parciales. GREEN específico:

```text
2 passed, 39 deselected
```

RED de aislamiento de rutas:

```text
1 failed, 41 deselected
```

La prueba demostró que `--report` podía coincidir con el manifiesto. Tras validar rutas antes de IO:

```text
1 passed, 41 deselected
```

GREEN focal previo al cierre del reporte:

```text
python -B -m pytest -p no:cacheprovider tests/test_catalog_asset_r2_migration.py -q
41 passed, 1 skipped in 4.66s
```

Verificación focal final fresca, incluyendo la prueba determinista de reparse y el aislamiento de outputs:

```text
python -B -m pytest -p no:cacheprovider tests/test_catalog_asset_r2_migration.py -q
42 passed, 1 skipped in 4.39s
```

La suite cubre además SHA externo, agregados/digests, duplicados, nombres/rutas, cuatro extensiones, missing/hash/size/magic, symlink/reparse, TOCTOU, extras no abiertos, HEAD exacto, 404 PUT+HEAD, 412+HEAD, mismatch sin PUT, 403 circuit breaker, 5xx/backoff, GET streaming/close/hash, barrera RPC, orden/payload/falla RPC, UUID/checkpoint/resume, configuración explícita, redacción y certificación.

## Regresión relevante y checks

Task 3/4, migraciones y repository:

```text
python -B -m pytest -p no:cacheprovider tests/test_catalog_migrations.py tests/test_catalog_repository.py -q
120 passed, 2 skipped in 0.97s
```

Los dos skips son integraciones PostgreSQL opt-in preexistentes sin DSN/contenedor local.

Compilación y whitespace/diff check:

```text
python -m py_compile scripts/migrate_catalog_assets_to_r2.py tests/test_catalog_asset_r2_migration.py
git diff --check -- scripts/migrate_catalog_assets_to_r2.py tests/test_catalog_asset_r2_migration.py
exit 0 / sin salida
```

## Límites y bloqueos live explícitos

No se creó manifiesto operacional en Git ni fuera de Git durante esta tarea. No se leyó ninguna credencial, no se construyó cliente real, no hubo llamadas live, upload, deploy, push, DDL, cambio de bucket/domain/CORS/cache, corte ni borrado.

Por ello **no se afirma Gate 6 live**. Para ejecutar y certificarlo todavía hacen falta, como gates externos independientes:

1. archivo manifiesto autoritativo exacto de 2,214 entries y su SHA-256 externo confirmado;
2. Task 3 aplicada y verificada live, incluyendo las RPC exactas y ausencia de conflictos incompatibles del registro;
3. sync/cargas administrativas congeladas y colas drenadas durante backfill/registro;
4. bucket R2 Standard dedicado `catalog-assets` y token temporal limitado a ese bucket;
5. endpoint/región/credenciales dedicadas y Supabase service role entregados sólo al entorno de ejecución;
6. ejecución dry-run real sobre el mirror y revisión del inventario de 770 extras;
7. execute completo con 2,214/2,214 GET+SHA, finalize exitoso y revisión del reporte/costo;
8. Gate 7 de domain/CORS/cache y los gates 8–9 de canary/corte/observación posteriores.

Los digests de inventario conocidos por diagnóstico read-only no sustituyen el archivo externo anclado. Supabase original y todos los objetos locales/R2 permanecen conservados; el migrador no tiene operación de eliminación.
