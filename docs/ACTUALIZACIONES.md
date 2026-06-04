# Sistema de Actualizaciones Automáticas — Mobiliti Generador

## ¿Cómo funciona?

El sistema usa una arquitectura **Passive Update** con helper externo:

1. **Al iniciar sesión**, el cliente consulta `GET /version` en el backend Vercel
2. Si la versión remota es mayor que la local (leída de `version.txt`), muestra un diálogo Tkinter
3. El usuario puede elegir:
   - **Actualizar ahora** → descarga el nuevo `.exe`, se cierra, y un batch helper reemplaza el archivo y relanza la app
   - **Más tarde** → la app continúa normalmente
   - **Omitir esta versión** → guarda la preferencia en `config.json` y no vuelve a preguntar
4. Si es una **actualización forzosa** (`force_update: true`), no se puede omitir ni posponer

## Flujo de publicación de una nueva versión

### Paso 1: Preparar el release

```bash
# Asegurar que todo está commiteado
git status

# Ejecutar script de release (actualiza version.txt, backends, build y ZIP)
python scripts/release_version.py \
  --version 1.5.4 \
  --notes "Fix: corrección de bug X. Mejora: nueva categoría Y." \
  --download-url "https://github.com/TU_USUARIO/TU_REPO/releases/download/v1.5.4/Mobiliti_Generador.exe"
```

Parámetros:
| Parámetro | Descripción |
|-----------|-------------|
| `--version` | Nueva versión SemVer (ej: `1.5.4`) |
| `--notes` | Notas de release (aparecen en el diálogo del cliente) |
| `--download-url` | URL directa al `.exe` (GitHub Releases, S3, etc.) |
| `--force` | Marcar como actualización obligatoria |
| `--skip-build` | Solo actualizar versiones, sin compilar |

### Paso 2: Subir a GitHub Releases

```bash
# Crear tag
git tag -a v1.5.4 -m "Release v1.5.4"
git push origin v1.5.4

# Crear release con gh CLI
gh release create v1.5.4 \
  "mobiliti_saas/release/Mobiliti_Generador_Windows_v1.5.4.zip" \
  --title "Mobiliti Generador v1.5.4" \
  --notes "Fix: corrección de bug X. Mejora: nueva categoría Y."
```

**Importante:** GitHub Releases actúa como CDN gratuito para el `.exe`. El ZIP es para distribución manual; el auto-updater descarga solo el `.exe`.

### Paso 3: Deploy del backend

```bash
cd vercel_deploy  # o mobiliti_saas, dependiendo de cuál esté deployado
vercel --prod
```

### Paso 4: Probar

1. Ejecutar un cliente con versión anterior (modificar temporalmente `version.txt` a `1.5.2`)
2. Iniciar sesión
3. Verificar que aparece el diálogo de actualización
4. Aceptar y verificar que:
   - Se descarga a `%TEMP%\mobiliti_update\`
   - Se crea `update_helper.bat`
   - La app se cierra
   - El batch reemplaza el `.exe`
   - Se relanza la app con la nueva versión

## Estructura de archivos relacionados

| Archivo | Propósito |
|---------|-----------|
| `mobiliti_saas/cliente/updater.py` | Lógica de actualización (descarga, UI, batch helper) |
| `mobiliti_saas/cliente/version.txt` | Versión actual embebida en el `.exe` |
| `vercel_deploy/api/index.py` | Backend desplegado (endpoints `/version`, `/download/latest`) |
| `mobiliti_saas/api/index.py` | Backend alternativo (async, httpx) |
| `scripts/release_version.py` | Script de automatización de releases |
| `tests/test_updater.py` | Tests unitarios del parser de versiones |

## Endpoints API

### `GET /version`

Devuelve metadatos de la última versión:

```json
{
  "version": "1.5.3",
  "major": 1,
  "minor": 5,
  "patch": 3,
  "download_url": "https://github.com/.../Mobiliti_Generador.exe",
  "release_notes": "Fix: detección dinámica de columna Vol.",
  "release_date": "2026-06-04T10:00:00Z",
  "force_update": false,
  "min_version_required": null
}
```

### `GET /download/latest`

Devuelve la URL de descarga:

```json
{
  "url": "https://github.com/.../Mobiliti_Generador.exe"
}
```

## Configuración del cliente

El cliente lee la URL del API desde `config.json` (mismo archivo que usa para login):

```json
{
  "api_url": "https://verceldeploy-pied.vercel.app",
  "skip_version": "1.5.3"
}
```

El campo `skip_version` se agrega automáticamente cuando el usuario omite una actualización.

## Solución de problemas

### "No se pudo contactar al servidor de versiones"
- Verificar que `config.json` tiene la URL correcta del API
- Verificar conectividad a Internet
- Verificar que el backend está deployado en Vercel

### "Error al reemplazar el ejecutable"
- Windows puede bloquear el archivo si la app principal tarda en cerrar
- Solución: aumentar el `timeout /t` en `updater.py` (línea del batch helper)
- Asegurar que el antivirus no bloquee el batch temporal

### "La app no se reinicia después de actualizar"
- Verificar que la ruta del `.exe` no contiene espacios especiales sin comillas
- El batch helper se autodestruye después de ejecutar; verificar que no hay errores en la consola temporal

### La nueva versión no se detecta
- Verificar que `version.txt` tiene la versión correcta (sin prefijo `v`)
- Verificar que el backend devuelve `version` en el JSON
- Verificar que la versión local es menor que la remota (usar SemVer)

## Notas de seguridad

- Las descargas usan `urllib.request` con SSL por defecto
- No se verifica firma criptográfica de los binarios (overkill para este caso de uso)
- Si se necesita mayor seguridad, considerar agregar checksum SHA256 en el endpoint `/version` y verificarlo antes de reemplazar

## Versionado

Usamos **SemVer simplificado**: `MAJOR.MINOR.PATCH`

| Tipo | Cuándo subir | Ejemplo |
|------|-------------|---------|
| MAJOR | Cambio incompatible | `2.0.0` |
| MINOR | Nueva funcionalidad | `1.6.0` |
| PATCH | Fix de bug | `1.5.4` |

**Regla de negocio:**
- Cambio de `MAJOR` → actualización forzosa (`force_update: true`)
- Cambio de `MINOR` o `PATCH` → opcional
