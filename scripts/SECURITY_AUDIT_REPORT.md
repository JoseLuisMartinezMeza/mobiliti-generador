# INFORME DE AUDITORIA DE SEGURIDAD Y BASE DE DATOS
# Mobiliti SaaS - Generador de Cotizaciones
# Fecha: 2026-06-04
# Auditor: Cybersecurity Expert + PostgreSQL AI Expert

================================================================================
## RESUMEN EJECUTIVO
================================================================================

SEVERIDAD GENERAL: ALTA (3 criticas, 5 medias, 4 bajas)

Hallazgos criticos que requieren atencion inmediata:
1. Credenciales hardcodeadas en 8 archivos del repositorio
2. JWT_SECRET_KEY hardcodeado en codigo fuente
3. CORS permite cualquier origen con credentials=true

================================================================================
## SECCION 1: AUDITORIA DE CIBERSEGURIDAD
## Framework: NIST CSF 2.0 + MITRE ATT&CK
================================================================================

--------------------------------------------------------------------------------
[CRITICO] H-001: Credenciales hardcodeadas en repositorio
--------------------------------------------------------------------------------
CVSS Estimado: 9.8 (Critical)
MITRE ATT&CK: T1552.001 (Credentials In Files)

Archivos afectados:
  1. Mobiliti_Generador_Windows/credentials.json
     - Email: ***REMOVED***
     - Password: ***REMOVED*** (TEXTO PLANO)
     
  2. Mobiliti_Generador_Windows1/credentials.json
     - Email: ***REMOVED***
     
  3. Mobiliti_Generador_Windows34/credentials.json
     - Email: ***REMOVED***
     
  4. mobiliti_saas/supabase_setup/seed_admin.py (lineas 15-16)
     - ADMIN_EMAIL = "***REMOVED***"
     - ADMIN_PASSWORD = "***REMOVED***"
     - DB_URL con placeholder de contrasena
     
  5. mobiliti_saas/scripts/init_db.py (lineas 30-32)
     - Email + password hardcodeados para admin de prueba
     
  6. mobiliti_saas/supabase_setup/TODO_EN_UNO_SQL.sql (linea 119-123)
     - Admin email y bcrypt hash hardcodeados
     
  7. mobiliti_saas/supabase_setup/SQL_LIMPIO.sql (lineas similares)
     - Admin email y hash hardcodeados

IMPACTO:
- El repositorio es PUBLICO en GitHub
- Cualquier persona puede ver estas credenciales
- La contrasena parece ser reutilizada (tiene componentes personales: REMOVED_PASSWORD, 144267, mz, 2000)
- Acceso completo al admin del sistema SaaS

RECOMENDACION INMEDIATA:
1. Cambiar la contrasena del admin en Supabase AHORA
2. Eliminar estos archivos del historial de Git (git filter-repo o BFG)
3. Usar variables de entorno para TODAS las credenciales
4. El script seed_admin_secure.py es la unica implementacion segura

--------------------------------------------------------------------------------
[CRITICO] H-002: JWT_SECRET_KEY hardcodeado
--------------------------------------------------------------------------------
CVSS Estimado: 8.5 (High)
MITRE ATT&CK: T1552.001

Archivo: mobiliti_saas/backend/auth.py (linea 7)
  SECRET_KEY = "***REMOVED***"

IMPACTO:
- Cualquiera con acceso al codigo puede falsificar tokens JWT
- Puede impersonar a cualquier usuario, incluido admin
- El secret tiene entropia baja (es una frase legible con leet speak)

RECOMENDACION:
1. Mover a variable de entorno inmediatamente
2. Generar nuevo secret de 256 bits (32+ bytes aleatorios)
3. Invalidar todos los tokens existentes

NOTA: Los backends en Vercel (vercel_deploy/api/index.py y mobiliti_saas/api/index.py)
SI leen JWT_SECRET_KEY de variables de entorno. Solo el backend local (backend/auth.py)
tiene este problema.

--------------------------------------------------------------------------------
[CRITICO] H-003: CORS permite cualquier origen con credentials
--------------------------------------------------------------------------------
CVSS Estimado: 7.5 (High)
CWE-942: Permissive Cross-domain Policy with Untrusted Domains

Archivos:
  - vercel_deploy/api/index.py (lineas 211-217)
  - mobiliti_saas/api/index.py (lineas 190-196)

Codigo:
  allow_origins=_origins()  # default: "*"
  allow_credentials=True
  allow_methods=["*"]
  allow_headers=["*"]

IMPACTO:
- allow_origins="*" + allow_credentials=True es una combinacion peligrosa
- Permite que sitios maliciosos hagan requests autenticados en nombre del usuario
- Vulnerable a CSRF si el cliente no implementa proteccion adicional

RECOMENDACION:
1. Configurar CORS_ORIGINS con dominios especificos en produccion
2. Ejemplo: CORS_ORIGINS=https://mobiliti.app,https://admin.mobiliti.app
3. Nunca usar "*" con allow_credentials=True

--------------------------------------------------------------------------------
[MEDIO] H-004: Falta rate limiting
--------------------------------------------------------------------------------
CWE-770: Allocation of Resources Without Limits or Throttling

Endpoints sin proteccion:
  - POST /login (vulnerable a fuerza bruta)
  - POST /admin/usuarios (vulnerable a spam)
  - POST /admin/suscripciones
  - POST /generar-cotizacion

IMPACTO:
- Ataques de fuerza bruta contra passwords
- Consumo excesivo de recursos en Vercel (costos, suspension)

RECOMENDACION:
1. Implementar rate limiting con slowapi o similar
2. Max 5 intentos de login por IP cada 15 minutos
3. Max 100 requests por IP por hora

--------------------------------------------------------------------------------
[MEDIO] H-005: Falta validacion de entrada (type safety)
--------------------------------------------------------------------------------
CWE-20: Improper Input Validation

Los endpoints reciben `body: dict` en lugar de Pydantic models:
  - POST /login (body: dict)
  - POST /admin/usuarios (body: dict)
  - POST /admin/suscripciones (body: dict)
  - PATCH /admin/suscripciones/{id} (body: dict)
  - POST /generar-cotizacion (body: dict)

IMPACTO:
- Campos inesperados pueden causar errores o comportamiento no definido
- Posible inyeccion si los valores se usan en queries sin sanitizacion

RECOMENDACION:
1. Definir Pydantic models para cada endpoint
2. Validar email con EmailStr
3. Validar longitudes de strings
4. Sanitizar todos los inputs antes de enviar a Supabase

--------------------------------------------------------------------------------
[MEDIO] H-006: No hay headers de seguridad HTTP
--------------------------------------------------------------------------------
Faltan headers de seguridad en las respuestas FastAPI:
  - X-Content-Type-Options: nosniff
  - X-Frame-Options: DENY
  - Strict-Transport-Security (HSTS)
  - Content-Security-Policy

RECOMENDACION:
1. Agregar middleware de seguridad en FastAPI

--------------------------------------------------------------------------------
[MEDIO] H-007: URL de descarga no verificada (auto-updater)
--------------------------------------------------------------------------------
CWE-494: Download of Code Without Integrity Check

El updater descarga el .exe desde GitHub pero NO verifica:
  - Hash/SHA256 del archivo
  - Firma digital
  - Tamano esperado

Archivo: mobiliti_saas/cliente/updater.py

IMPACTO:
- Si la cuenta de GitHub es comprometida, se distribuiria malware
- El usuario no tiene forma de verificar la autenticidad del .exe

RECOMENDACION:
1. Publicar SHA256 del .exe junto con el release
2. Verificar el hash despues de descargar
3. Considerar firma de codigo (certificado de Windows)

--------------------------------------------------------------------------------
[BAJA] H-008: Version expuesta publicamente
--------------------------------------------------------------------------------
El endpoint GET /version es publico y revela:
  - Version exacta del software
  - URL de descarga
  - Notas de release
  - Fecha de release

IMPACTO:
- Reconocimiento para atacantes (saben que versiones son vulnerables)
- Bajo riesgo en este caso, pero considerar rate limiting

RECOMENDACION:
1. Agregar rate limiting
2. No es critico para este caso de uso

--------------------------------------------------------------------------------
[BAJA] H-009: Password strength no validada en registro
--------------------------------------------------------------------------------
No hay validacion de fortaleza de contrasena al crea
