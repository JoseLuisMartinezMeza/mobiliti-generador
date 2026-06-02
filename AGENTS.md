<!-- From: c:\Users\pepem\Downloads\ARMADO DE CARATULA\AGENTS.md -->
# AGENTS.md — Generador de Cotizaciones Mobiliti

> Este archivo describe la arquitectura, convenciones y flujo de trabajo del proyecto para agentes de código. El proyecto utiliza español como idioma principal en comentarios, documentación y salida al usuario.

---

## 1. Visión General del Proyecto

El **Generador de Cotizaciones Mobiliti** es una herramienta de automatización de Excel escrita en Python que produce cotizaciones comerciales a partir de dos entradas:

1. **Quotation del proveedor** (archivo `.xlsx`): contiene la lista de productos, imágenes, cantidades y precios en la hoja `Quotation`.
2. **Template de cotización** (archivo `.xlsx`): plantilla corporativa con las hojas `Cotizacion`, `Mobiliti`, `Fletes`, `Proveedores`, etc.

El script genera un archivo Excel final con:
- Hoja `Cotizacion` con encabezado personalizado, tabla de productos, totales y términos y condiciones.
- Hoja `Mobiliti` con productos clasificados por categoría y referencias a precios.
- Hoja `Quotation` copiada directamente del archivo fuente.
- Fórmulas vivas de Excel (no valores estáticos).
- Imágenes de productos extraídas y reinsertadas con escala proporcional.
- Logo corporativo (`LOGO.png`) en el encabezado.

Adicionalmente, el proyecto ahora incluye una **capa SaaS** (`mobiliti_saas/`) que agrega autenticación por suscripción, un backend serverless en Vercel y un cliente desktop empaquetado como `.exe`.

---

## 2. Stack Tecnológico y Dependencias

### 2.1 Generador Core (local)

| Tecnología | Versión conocida | Uso |
|---|---|---|
| Python | 3.14 | Lenguaje principal |
| `xlwings` | >=0.35.0 | Automatización nativa de Excel (Windows) |
| `openpyxl` | >=3.1.0 | Lectura de datos, extracción de imágenes vía XML |
| `rapidfuzz` | >=3.0.0 | Fuzzy matching para clasificación de productos (opcional) |
| `pytest` | >=9.0.0 | Tests unitarios |
| `Pillow` | >=10.0.0 | Manipulación de imágenes (entorno) |
| `wmi` | >=1.5.0 | Obtención de hardware ID (opcional, para SaaS) |

**Nota crítica:** `xlwings` requiere una instalación de Microsoft Excel en Windows y solo funciona en ese sistema operativo. El script lanza Excel de forma visible temporalmente (`visible=True` seguido de `Visible=False`) para evitar bugs conocidos de xlwings.

### 2.2 SaaS Backend (Vercel)

| Tecnología | Uso |
|---|---|
| `fastapi` | Framework API |
| `mangum` | Adaptador ASGI para Vercel serverless |
| `httpx` / `urllib.request` | Cliente HTTP para Supabase REST |
| `passlib[bcrypt]` / `bcrypt` | Hash de contraseñas |
| `python-jose[cryptography]` | JWT tokens |
| `python-multipart` | Form data |
| `pydantic` | Validación de datos |

### 2.3 SaaS Cliente Desktop

| Tecnología | Uso |
|---|---|
| `tkinter` | GUI nativa de Windows |
| `PyInstaller` | Empaquetado a `.exe` |
| `urllib.request` + `ssl` | Llamadas al backend (evita dependencias externas) |

### 2.4 Instalación de dependencias

Existe un archivo `requirements.txt` en la raíz para el generador core:

```bash
pip install -r requirements.txt
```

Para el backend SaaS (dentro de `mobiliti_saas/`):
```bash
pip install -r mobiliti_saas/requirements.txt
```

---

## 3. Estructura de Archivos

```
ARMADO DE CARATULA/
├── generar_cotizacion_v5_xlwings.py   # Script principal (~850 líneas) — USAR ESTE
├── clasificador.py                     # Clasificador de productos por diccionario
├── diccionario_categorias.json         # Diccionario de categorías Mobiliti
├── insertar_imagenes.py                # Helper para insertar imágenes en Cotizacion
├── test_clasificador.py                # Tests unitarios del clasificador
├── requirements.txt                    # Dependencias del generador core
├── LOGO.png                            # Logo corporativo para el encabezado
│
├── *.xlsx                              # Templates y archivos de entrada/salida
│   ├── Formato Cotización 2026 GDL (1).xlsx   # Template por defecto
│   ├── KIVO BRAVANTE-Quotation Sheet - V1.xlsx # Ejemplo de fuente
│   └── Cotizacion_*.xlsx               # Archivos generados (salida)
│
├── historial/                          # Versiones anteriores y scripts de diagnóstico
│   ├── generar_cotizacion.py           # v1 (openpyxl puro)
│   ├── generar_cotizacion_v2.py        # v2
│   ├── generar_cotizacion_v3.py        # v3
│   ├── generar_cotizacion_v4.py        # v4
│   ├── generar_cotizacion_xlwings.py   # Primera versión con xlwings
│   ├── generar_cotizacion_win32com.py  # Versión con win32com
│   ├── generar_cotizacion_INTERACTIVO.py # Wrapper interactivo (input por consola)
│   ├── diagnosticar_imagen.py          # Scripts de debug
│   ├── verificar_*.py                  # Scripts de verificación
│   └── Cotizacion_*.{xlsx,pdf,png}     # Archivos de prueba históricos
│
├── temp_images/                        # Imágenes temporales extraídas de Excel
│
├── mobiliti_saas/                      # Capa SaaS (backend + cliente desktop)
│   ├── api/
│   │   └── index.py                    # Backend FastAPI para Vercel (monolítico)
│   ├── backend/
│   │   ├── main.py                     # Backend alternativo (FastAPI + SQLAlchemy)
│   │   ├── database.py                 # Configuración de DB (SQLite/Postgres)
│   │   ├── models.py                   # Modelos SQLAlchemy (Usuario, Suscripcion, Sesion)
│   │   └── auth.py                     # JWT + bcrypt helpers
│   ├── cliente/
│   │   ├── main_cliente.py             # GUI Tkinter del cliente desktop
│   │   ├── entry_point.py              # Entry point para PyInstaller
│   │   ├── verificador.py              # Cliente de verificación de licencia (legacy)
│   │   ├── clasificador.py             # Copia local del clasificador
│   │   ├── generar_cotizacion_v5_xlwings.py # Copia local del generador
│   │   ├── insertar_imagenes.py        # Copia local del helper de imágenes
│   │   └── diccionario_categorias.json # Copia local del diccionario
│   ├── scripts/
│   │   └── build_cliente.py            # Script para compilar el .exe con PyInstaller
│   ├── supabase_setup/
│   │   ├── create_tables.sql           # SQL para crear tablas en Supabase
│   │   ├── TODO_EN_UNO_SQL.sql         # SQL completo con seed admin
│   │   └── seed_admin.py               # Crea usuario admin inicial
│   ├── config.json                     # URL del API (para el cliente)
│   ├── vercel.json                     # Configuración de Vercel
│   ├── requirements.txt                # Dependencias del backend SaaS
│   ├── Mobiliti_SaaS.spec              # Spec de PyInstaller
│   ├── dist/
│   │   └── Mobiliti_Generador.exe      # Ejecutable compilado
│   └── release/
│       └── Mobiliti_Generador.exe      # Copia de distribución
│
├── mobiliti_saas_vercel/               # Backend alternativo desplegado (httpx/async)
│   ├── api/index.py
│   ├── requirements.txt
│   └── vercel.json
│
└── vercel_deploy/                      # Backend alternativo desplegado (urllib/sync)
    ├── api/index.py
    ├── requirements.txt
    └── vercel.json
```

**Regla de oro:** Siempre usar `generar_cotizacion_v5_xlwings.py` como el script principal. Las versiones en `historial/` se conservan como referencia pero no deben modificarse ni ejecutarse para producción.

---

## 4. Arquitectura del Código

### 4.1 Script principal (`generar_cotizacion_v5_xlwings.py`)

El flujo de trabajo sigue 8 pasos numerados:

1. **Preparar template desprotegido**: remueve `sheetProtection` del XML interno vía `zipfile` para poder editar celdas protegidas.
2. **Extraer imágenes**: parsea el XML de dibujo (`drawing1.xml`) y relaciones del archivo fuente para mapear `fila → imagen` de forma precisa. Usa `openpyxl` como fallback.
3. **Leer items**: recorre la hoja `Quotation` del source detectando categorías (filas donde la columna A empieza con `-`) y productos (filas donde la columna A es numérica).
4. **Iniciar Excel**: crea una instancia de Excel mediante `xlwings.App(visible=True)` y luego la oculta.
5. **Abrir workbooks**: abre el template desprotegido y el archivo fuente.
6. **Llenar encabezado**: escribe datos del proyecto en `Cotizacion!B3:B12` e inserta `LOGO.png` en la posición fija del template (`top=3, left=1398, width=319, height=317`).
7. **Generar Mobiliti**: para cada categoría/producto leído, escribe fórmulas en la hoja `Mobiliti` referenciando la hoja `Quotation` copiada. Ajusta referencias de moneda (`C13`, `C48`, etc.) y clasifica productos en columna E usando `clasificador.py`.
8. **Generar Cotizacion**: inserta filas dinámicamente, copia formato nativo del template con `PasteSpecial(-4104)`, escribe fórmulas, inserta imágenes escaladas y centradas, agrega filas de totales (Subtotal, Flete 12%, Subtotal+ Flete, IVA 16%, Total), inserta 2 filas vacías y restaura el bloque de términos y condiciones desde una hoja temporal.

### 4.2 Clasificador (`clasificador.py`)

- `cargar_diccionario(path)`: carga `diccionario_categorias.json`.
- `clasificar_producto(nombre, diccionario)`: normaliza el texto (minúsculas, sin acentos, sin espacios extra) y aplica:
  1. **Match exacto por substring**: busca términos del diccionario contenidos en el nombre del producto, priorizando términos más largos.
  2. **Fuzzy matching** (si `rapidfuzz` está instalado): usa `process.extractOne` con umbral configurable (default 75).
  3. **Fallback**: devuelve `"OTRO"`.
- `normalizar_texto(texto)`: utilidad de normalización reutilizable.

### 4.3 Helper de imágenes (`insertar_imagenes.py`)

- `insertar_imagenes_cotizacion(ws_cot, items, image_map, start_row=16)`:
  - Elimina imágenes previas en la hoja `Cotizacion` (preservando el logo del encabezado).
  - Inserta imágenes escaladas proporcionalmente para que quepan dentro de la celda B (98% del tamaño).
  - Centra cada imagen horizontal y verticalmente en su celda.
  - Devuelve la cantidad de imágenes insertadas.

### 4.4 Diccionario de categorías (`diccionario_categorias.json`)

Define 13 categorías con listas de términos de búsqueda y configuración (`umbral_fuzzy`, `default_category`, etc.). Las categorías son:

`Silla`, `Mesas de Apoyo`, `Escritorios`, `Sillones`, `Mesas de Juntas`, `Librero - Locker - Gabinete`, `Archiveros Moviles y Fijos`, `Phonebooths`, `Multicontactos`, `Terminados`, `Bancos`, `Cocineta`, `Pizarrones`.

### 4.5 SaaS Backend (`mobiliti_saas/api/index.py`)

Backend monolítico (todo en un archivo) para evitar problemas de imports en Vercel serverless:

- **FastAPI** con `mangum` como handler ASGI.
- **Autenticación**: JWT (`python-jose`) + bcrypt. Tokens expiran en 60 minutos (o 24h en la versión local `backend/main.py`).
- **Base de datos**: Supabase REST API (PostgreSQL) usando `httpx` (async) o `urllib.request` (sync).
- **Tablas principales**:
  - `saas_usuarios`: email, hashed_password, nombre, empresa, es_admin, activo.
  - `saas_suscripciones`: usuario_id, estado (activa/vencida/suspendida/cancelada), plan, fecha_inicio, fecha_fin.
  - `saas_sesiones`: token_jwt, ip_address, user_agent, activa.
- **Endpoints protegidos**: verifican token Bearer y suscripción activa antes de responder.
- **Endpoints admin**: requieren `es_admin=True`. Permiten crear usuarios, suscripciones y suspender/activar.

### 4.6 SaaS Cliente Desktop (`mobiliti_saas/cliente/main_cliente.py`)

- **GUI Tkinter** con pantallas de login y generación de cotizaciones.
- **Verificación online**: en cada login y antes de cada generación se verifica la suscripción contra el backend.
- **Hardware ID**: opcionalmente envía un ID basado en WMI (`CPU-Motherboard-Disk`) para tracking.
- **Persistencia de credenciales**: guarda email/password en `credentials.json` junto al `.exe`.
- **Modo ejecutable**: cuando corre como `.exe`, usa `sys.executable` con `--generate` para lanzar un subproceso limpio que ejecuta el generador.
- **Recurso externo primero**: busca templates y scripts primero en el directorio del `.exe` (permite personalizaciones) y fallback al bundle interno de PyInstaller (`sys._MEIPASS`).

---

## 5. Convenciones de Código

- **Idioma**: español para todo lo relacionado con la lógica de negocio, comentarios, docstrings, mensajes de consola y nombres de funciones/variables.
- **Nombres de funciones**: `snake_case` en español (ej. `extraer_imagenes`, `clasificar_producto`, `generar_cotizacion`).
- **Constantes**: mayúsculas con guiones bajos (ej. `Q_HEADER_ROW = 7`).
- **Mensajes de progreso**: el script principal imprime pasos numerados `[N/8]` con prefijos `[OK]` y `[ADVERTENCIA]`.
- **Manejo de errores**: try/except amplio alrededor del flujo principal con `traceback.print_exc()` y limpieza en bloque `finally` (cerrar Excel, borrar directorio temporal).

---

## 6. Testing

### 6.1 Cómo ejecutar tests

```bash
python -m pytest test_clasificador.py -v
```

### 6.2 Cobertura de tests

`test_clasificador.py` contiene tests que verifican:

- Normalización de texto (acentos, espacios, saltos de línea).
- Clasificación de productos reales extraídos de quotations de clientes (KIVO, IZA Monterrey).
- Correcciones específicas (ej. `"SALA DE ESTAR"` debe clasificar como `Sillones`, no `Mesas de Juntas`).
- Robustez ante typos y variantes de escritura.
- Fallback para productos desconocidos y valores vacíos.
- Presencia de todas las categorías esperadas en el diccionario JSON.

**Todos los tests deben pasar antes de cualquier modificación al clasificador o al diccionario.**

---

## 7. Uso del Script Principal

### 7.1 Comando básico

```bash
python generar_cotizacion_v5_xlwings.py \
  --source "KIVO BRAVANTE-Quotation Sheet - V1.xlsx" \
  --template "Formato Cotización 2026 GDL (1).xlsx" \
  --output "Cotizacion_Final.xlsx" \
  --cotizacion "100-99999" \
  --proyecto "Oficinas Kivo Bravante" \
  --cliente "Cliente Ejemplo" \
  --correo "cliente@ejemplo.com" \
  --telefono "555-1234" \
  --direccion "Calle Falsa 123" \
  --razon_social "Empresa SA de CV"
```

### 7.2 Parámetros

| Parámetro | Default | Descripción |
|---|---|---|
| `--source` / `-s` | **requerido** | Archivo fuente con hoja `Quotation` |
| `--template` / `-t` | `Formato Cotización 2026 GDL (1).xlsx` | Plantilla corporativa |
| `--output` / `-o` | `Cotizacion_{proyecto}_{timestamp}.xlsx` | Archivo de salida |
| `--cotizacion` / `-n` | `100-00000` | Número de cotización |
| `--proyecto` / `-p` | `''` | Nombre del proyecto |
| `--cliente` / `-c` | `''` | Nombre del cliente |
| `--correo` / `-e` | `''` | Correo electrónico |
| `--telefono` / `-tel` | `''` | Teléfono |
| `--direccion` / `-d` | `''` | Dirección |
| `--razon_social` / `-r` | `''` | Razón social |

---

## 8. Arquitectura SaaS

### 8.1 Flujo de uso

```
1. Admin crea usuario + suscripcion via API/curl
2. Cliente recibe email/password
3. Cliente abre Mobiliti_Generador.exe
4. Cliente ingresa credenciales
5. Backend verifica suscripcion activa
6. Cliente puede generar cotizaciones
7. Cada generacion re-verifica suscripcion online
8. Admin puede suspender en cualquier momento
```

### 8.2 Despliegue del backend (Vercel)

```bash
cd mobiliti_saas
vercel env add SUPABASE_URL
vercel env add SUPABASE_SERVICE_KEY
vercel env add JWT_SECRET_KEY
vercel env add CORS_ORIGINS
vercel --prod
```

Variables requeridas:
- `SUPABASE_URL`: URL del proyecto Supabase (ej. `https://amarztcyhgtszmwazxgl.supabase.co`).
- `SUPABASE_SERVICE_KEY`: Service Role Key de Supabase.
- `JWT_SECRET_KEY`: Clave secreta larga para firmar tokens (¡cambiar en producción!).
- `CORS_ORIGINS`: `*` o dominios específicos.

### 8.3 Compilación del cliente desktop

```bash
cd mobiliti_saas
python scripts/build_cliente.py
```

Salida: `mobiliti_saas/dist/Mobiliti_Generador.exe` (~90-95 MB).
Archivos de distribución: `Mobiliti_Generador.exe` + `config.json`.

### 8.4 Endpoints API principales

| Endpoint | Método | Auth | Descripción |
|---|---|---|---|
| `/` | GET | No | Health check |
| `/health` | GET | No | Estado del servidor |
| `/login` | POST | No | Login email/password |
| `/verificar-sesion` | POST | Token | Re-verifica suscripción |
| `/generar-cotizacion` | POST | Token | Autoriza generación local |
| `/admin/usuarios` | GET | Admin | Listar usuarios |
| `/admin/usuarios` | POST | Admin | Crear usuario |
| `/admin/suscripciones` | GET | Admin | Listar suscripciones |
| `/admin/suscripciones` | POST | Admin | Crear suscripción |
| `/admin/suscripciones/{id}` | PATCH | Admin | Cambiar estado |

---

## 9. Consideraciones de Seguridad y Entorno

- **Windows obligatorio**: `xlwings` requiere Excel instalado y solo corre en Windows.
- **Proceso Excel persistente**: si el script falla o se interrumpe, Excel puede quedar abierto en segundo plano. El script ejecuta `taskkill /F /IM EXCEL.EXE` al inicio para mitigar esto.
- **Protección de hojas**: la hoja `Mobiliti` se vuelve a proteger al final con contraseña hardcodeada (`M0b1l1t$`). La hoja `Cotizacion` **no** se protege para permitir edición manual posterior.
- **Archivos temporales**: se crean en `%TEMP%` (template desprotegido) y en `tempfile.mkdtemp()` (imágenes extraídas). Ambos se limpian en el bloque `finally`.
- **Secretos hardcodeados**: la contraseña de protección de hoja y la JWT secret key aparecen en texto plano en el código. No son secretos de alto valor para el negocio local, pero evitar difundirlos innecesariamente.
- **Backend Vercel**: las funciones serverless tienen timeout de 10s (hobby) o 60s (pro). Los endpoints del backend son rápidos (<1s) porque solo hacen queries a Supabase. El generador de Excel **nunca** corre en Vercel; siempre es local.

---

## 10. Notas para el Mantenimiento

### Agregar una nueva categoría de producto
1. Editar `diccionario_categorias.json` y agregar la categoría con sus términos.
2. Agregar la categoría a la lista `Mobiliario` en `Fletes` (el script principal ya agrega Bancos, Cocineta, Pizarrones dinámicamente; para categorías adicionales modificar el bloque `ws_fletes.range('I16:I18')`).
3. Si se necesita más espacio en `Mobiliti`, extender las listas `section_cats`, `section_prod_starts` y `section_subtotals` en `generar_cotizacion_v5_xlwings.py`. El script copiará las fórmulas automáticamente a las nuevas secciones.
4. Ejecutar `pytest test_clasificador.py -v` y agregar tests para productos que representen la nueva categoría.

### Modificar fórmulas de totales o descuentos
- Las fórmulas de la hoja `Cotizacion` están en el Paso 8 del script principal.
- El descuento base se fija en la celda `G{descuento_row}` del primer producto (default `0.7` = 30 % de descuento).
- Los totales son: Subtotal (suma de `J`), Flete 12 %, Subtotal + Flete, IVA 16 %, Total.

### Cambiar posición del logo
- La posición está hardcodeada en `left=1398, top=3, width=319, height=317`. Si el template cambia de diseño, actualizar estas coordenadas.

### Aumentar capacidad de secciones en Mobiliti
- El template físico tiene **10 secciones** predefinidas.
- El script principal detecta automáticamente si se necesitan más secciones (hasta 13) y copia las fórmulas de la sección 1 a las nuevas secciones dinámicamente durante la ejecución.
- Las 13 categorías soportadas son: Silla, Mesas de Apoyo, Escritorios, Sillones, Mesas de Juntas, Librero - Locker - Gabinete, Archiveros Moviles y Fijos, Phonebooths, Multicontactos, Terminados, Bancos, Cocineta, Pizarrones.
- Si se necesitan más de 13 secciones, extender las listas `section_cats`, `section_prod_starts`, `section_subtotals` en el script principal.

### Actualizar el cliente SaaS después de cambios en el generador
- Si se modifica `generar_cotizacion_v5_xlwings.py`, `clasificador.py`, `insertar_imagenes.py` o `diccionario_categorias.json` en la raíz, copiar los archivos actualizados a `mobiliti_saas/cliente/` y recompilar con `scripts/build_cliente.py`.

---

## 11. Historial de Versiones (Contexto)

| Versión | Motor | Estado |
|---|---|---|
| v1 | `openpyxl` puro | Obsoleto (en `historial/`) |
| v2 | `openpyxl` puro | Obsoleto (en `historial/`) |
| v3 | `openpyxl` puro | Obsoleto (en `historial/`) |
| v4 | `openpyxl` puro | Obsoleto (en `historial/`) |
| win32com | `win32com.client` | Obsoleto (en `historial/`) |
| xlwings early | `xlwings` | Obsoleto (en `historial/`) |
| **v5** | **`xlwings`** | **Activo (core)** |
| **SaaS** | **FastAPI + Supabase + Tkinter** | **Activo (capa de suscripciones)** |

La migración a `xlwings` se hizo para lograr "pixel-perfect" fidelity: al usar Excel nativo para aplicar formato, copiar pegado especial y calcular fórmulas, el resultado visual es idéntico al template manual.

---

*Última actualización: 2026-05-30*
