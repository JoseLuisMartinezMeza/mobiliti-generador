# GUIA COMPLETA: Distribucion del Mobiliti Generador a Clientes

> Esta guia explica paso a paso como funciona el proyecto, que se le entrega al cliente,
> y como tu (como admin) subes nuevas versiones.

---

## PARTE 1: QUE LE ENTREGAS AL CLIENTE

### Archivos que necesita el cliente

El cliente solo necesita **2 archivos** para usar el sistema:

```
Mobiliti_Generador.exe    <-- La aplicacion (160 MB aprox)
config.json               <-- Configuracion del backend
```

### Donde conseguir estos archivos

Despues de compilar, los archivos estan en:
```
mobiliti_saas/dist/
    Mobiliti_Generador.exe
    config.json          <-- se copia manualmente
```

### Como entregarlo

Opcion A - Carpeta ZIP (recomendada):
```
Mobiliti_Generador_v1.5.3.zip
    Mobiliti_Generador.exe
    config.json
```

Opcion B - Instalador (mas profesional):
- Usar Inno Setup o NSIS para crear un .exe instalador
- Pero para empezar, el ZIP es suficiente

---

## PARTE 2: COMO FUNCIONA PARA EL CLIENTE (FLUJO DE USO)

### Paso 1: Instalacion
1. Cliente descomprime el ZIP en su escritorio
2. Tiene una carpeta `Mobiliti_Generador/` con 2 archivos
3. Hace doble clic en `Mobiliti_Generador.exe`

### Paso 2: Primera vez - Login
```
+-----------------------------------+
|  Mobiliti - Generador             |
+-----------------------------------+
|                                   |
|  Email:    [proyectosjlmm@... ]  |
|  Password: [**************** ]   |
|                                   |
|  [        INICIAR SESION       ]  |
+-----------------------------------+
```
- Tu le das las credenciales (email + password)
- El cliente las guarda (el programa recuerda automaticamente)

### Paso 3: Verificacion de suscripcion
- El .exe se conecta a `https://verceldeploy-pied.vercel.app`
- Verifica que la suscripcion este activa
- Si esta vencida, muestra mensaje de error

### Paso 4: Uso del generador
```
+-----------------------------------+
|  Mobiliti - Generador de Cotizaciones |
+-----------------------------------+
|                                   |
|  Quotation del proveedor: [Browse]|
|  Template: [Formato 2026 GDL  v] |
|                                   |
|  Numero de cotizacion: [100-000] |
|  Proyecto: [Oficinas ACME      ] |
|  Cliente:  [ACME Corp          ] |
|                                   |
|  [     GENERAR COTIZACION      ]  |
+-----------------------------------+
```

### Paso 5: Auto-updater (deteccion de actualizacion)
Despues del login, automaticamente:
1. El programa consulta `/version` al backend
2. Compara version local (1.5.3) vs remota (1.5.3)
3. Si son iguales -> no hace nada
4. Si hay nueva version -> muestra dialogo:
```
+-----------------------------------+
|  Actualizacion disponible         |
+-----------------------------------+
|  Version: 1.5.3 -> 1.5.4          |
|                                   |
|  Cambios:                         |
|  - Fix: correccion de bug X       |
|  - Mejora: nuevo feature Y        |
|                                   |
|  [Actualizar] [Mas tarde] [Omitir]|
+-----------------------------------+
```

### Paso 6: Descarga y reemplazo (si el cliente acepta)
1. Descarga el nuevo .exe desde GitHub Releases
2. Guarda como `Mobiliti_Generador.exe.new`
3. Cierra el programa
4. Un script batch reemplaza el .exe viejo por el nuevo
5. Vuelve a abrir automaticamente

---

## PARTE 3: COMO TU (ADMIN) SUBES UNA NUEVA VERSION

### Resumen del proceso

```
1. Haces cambios en el codigo
2. Actualizas la version (1.5.3 -> 1.5.4)
3. Compilas el .exe
4. Subes el .exe a GitHub Releases
5. Actualizas el backend con la nueva version
6. Los clientes detectan la actualizacion automaticamente
```

### Paso a paso DETALLADO

#### Paso 1: Hacer cambios en el codigo
Editas los archivos que necesites:
```
-generar_cotizacion_v5_xlwings.py   (logica del generador)
-clasificador.py                     (clasificacion de productos)
-diccionario_categorias.json         (categorias)
```

#### Paso 2: Actualizar la version
Hay 3 lugares donde cambiar la version:

**A) version.txt (local del cliente):**
```
mobiliti_saas/cliente/version.txt
1.5.3 -> 1.5.4
```

**B) Backend - CURRENT_VERSION (2 archivos):**
```
vercel_deploy/api/index.py
mobiliti_saas/api/index.py

Cambiar:
  "version": "1.5.3" -> "1.5.4"
  "major": 1, "minor": 5, "patch": 3 -> "patch": 4
  "release_notes": "..." (describir cambios)
  "download_url": cambiar v1.5.3 a v1.5.4
```

#### Paso 3: Compilar el .exe
```bash
cd mobiliti_saas
python -c "from PyInstaller.__main__ import run; run(['Mobiliti_SaaS.spec', '--clean', '--noconfirm'])"
```

Resultado:
```
mobiliti_saas/dist/Mobiliti_Generador.exe   (160 MB)
```

#### Paso 4: Subir a GitHub Releases
1. Ve a https://github.com/REMOVED_PASSWORD/mobiliti-generador/releases
2. Click "Draft a new release"
3. Tag: `v1.5.4` -> "Create new tag"
4. Target: `feature/auto-updater`
5. Title: `v1.5.4`
6. Description:
   ```
   ## Cambios en v1.5.4
   - Fix: correccion de bug X
   - Mejora: nuevo feature Y
   ```
7. Arrastra `mobiliti_saas/dist/Mobiliti_Generador.exe`
8. Click "Publish release"

#### Paso 5: Actualizar el backend
```bash
cd vercel_deploy
vercel --prod
```

#### Paso 6: Los clientes se actualizan solos
- Cliente abre su Mobiliti_Generador.exe
- Se conecta al backend
- Detecta: local=1.5.3, remota=1.5.4
- Muestra dialogo de actualizacion
- Si acepta, se descarga y reemplaza automaticamente

---

## PARTE 4: AUTOMATIZACION CON SCRIPT DE RELEASE

Para simplificar los pasos 2-5, existe un script:

```bash
python scripts/release_version.py --version 1.5.4 --notes "Fix bug X. Mejora Y."
```

Este script:
1. Actualiza version.txt
2. Actualiza CURRENT_VERSION en ambos backends
3. Hace git commit
4. Crea git tag v1.5.4
5. Tu solo necesitas:
   - Compilar el .exe
   - Subirlo a GitHub Releases
   - Hacer vercel --prod

---

## PARTE 5: ESTRUCTURA COMPLETA DEL PROYECTO

```
ARMADO DE CARATULA/
|
|-- generar_cotizacion_v5_xlwings.py   <-- Logica principal (tu editas esto)
|-- clasificador.py                     <-- Clasificacion de productos
|-- diccionario_categorias.json         <-- Categorias
|-- LOGO.png                            <-- Logo corporativo
|-- Formato Cotizacion 2026 GDL.xlsx    <-- Template Excel
|
|-- mobiliti_saas/
|   |-- cliente/
|   |   |-- main_cliente.py            <-- GUI (interfaz grafica)
|   |   |-- updater.py                 <-- Auto-updater
|   |   |-- version.txt                <-- Version local (1.5.3)
|   |   |-- generar_cotizacion_v5_xlwings.py  <-- Copia local
|   |   |-- clasificador.py            <-- Copia local
|   |-- api/
|   |   |-- index.py                   <-- Backend async (alternativo)
|   |-- dist/
|   |   |-- Mobiliti_Generador.exe     <-- .exe compilado (SE ENTREGA)
|   |-- config.json                    <-- URL del backend (SE ENTREGA)
|   |-- Mobiliti_SaaS.spec             <-- Config de PyInstaller
|
|-- vercel_deploy/
|   |-- api/
|   |   |-- index.py                   <-- Backend PRODUCCION (sync)
|   |   |-- security_headers.py        <-- Headers de seguridad
|
|-- scripts/
|   |-- release_version.py             <-- Script de release
|   |-- rotate_jwt_secret.py           <-- Rotar JWT
|   |-- clean_git_history.sh           <-- Limpiar historial Git
|   |-- test_e2e_updater.py            <-- Test de updater
|
|-- docs/
|   |-- DISTRIBUCION_CLIENTE.md        <-- Esta guia
|   |-- ACTUALIZACIONES.md             <-- Guia del auto-updater
|   |-- SECURITY_AUDIT_REPORT.md       <-- Auditoria de seguridad
```

---

## PARTE 6: FLUJO COMPLETO (DIAGRAMA)

```
TU (ADMIN)                          CLIENTE
   |                                   |
   | 1. Editas codigo                  |
   v                                   |
Compilas .exe                         |
   |                                   |
   | 2. Subes a GitHub Releases        |
   v                                   |
GitHub almacena .exe                  |
   |                                   |
   | 3. Actualizas backend             |
   v                                   |
Vercel sirve /version                 |
   |                                   |
   | <-----------------------------    | 4. Cliente abre app
   |                                   |    - Consulta /version
   | ---------------------------->    |    - Detecta v1.5.4
   |                                   |    - Muestra dialogo
   | <-----------------------------    | 5. Cliente acepta
   |                                   |    - Descarga .exe nuevo
   | <-----------------------------    | 6. Batch reemplaza .exe
   |                                   |    - Se reinicia con v1.5.4
   v                                   v
 DONE                               CLIENTE ACTUALIZADO
```

---

## PARTE 7: PREGUNTAS FRECUENTES

### P: El cliente necesita instalar algo?
**R:** No. Solo necesita Windows con Excel instalado (para xlwings).

### P: El cliente necesita internet?
**R:** Si. Se conecta al backend de Vercel para verificar suscripcion y buscar actualizaciones.

### P: Que pasa si el cliente no actualiza?
**R:** Puede seguir usando la version vieja. La actualizacion es opcional (a menos que actives `force_update`).

### P: Como suspendo a un cliente?
**R:** En Supabase Dashboard, cambia el estado de su suscripcion a "suspendida". La proxima vez que abra el programa, no podra usarlo.

### P: Puedo tener diferentes versiones para diferentes clientes?
**R:** No facilmente. El auto-updater verifica una sola version global. Para versiones personalizadas, necesitarias un backend diferente por cliente.

### P: El .exe se puede copiar a otra computadora?
**R:** Si, pero necesita las credenciales de login. No es un mecanismo de proteccion fuerte, es una conveniencia.

---

*Ultima actualizacion: 2026-06-04*
