#!/usr/bin/env bash
# =============================================================================
# Script para limpiar credenciales del historial de Git usando BFG Repo-Cleaner
# =============================================================================
#
# IMPORTANTE:
#   - Este script REESCRIBE el historial de Git. Es irreversible.
#   - Todos los colaboradores deberan clonar el repo de nuevo.
#   - Hacer un backup del repo antes de ejecutar.
#
# Uso:
#   cd /ruta/al/proyecto
#   bash scripts/clean_git_history.sh
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BFG_JAR="${PROJECT_ROOT}/tools/bfg.jar"
REPO_URL="https://github.com/REMOVED_PASSWORD/mobiliti-generador"

echo "==================================================================="
echo "  LIMPIEZA DE HISTORIAL GIT - Mobiliti SaaS"
echo "==================================================================="
echo ""
echo "ADVERTENCIA: Este script reescribira el historial de Git."
echo "             Todos los colaboradores deberan clonar de nuevo."
echo ""
read -p "Estas seguro? Escribe 'SI' para continuar: " CONFIRM

if [ "$CONFIRM" != "SI" ]; then
    echo "Cancelado."
    exit 1
fi

# Verificar BFG
if [ ! -f "$BFG_JAR" ]; then
    echo "ERROR: No se encontro BFG en ${BFG_JAR}"
    echo "Descargando..."
    mkdir -p "${PROJECT_ROOT}/tools"
    curl -sL -o "$BFG_JAR" https://repo1.maven.org/maven2/com/madgag/bfg/1.14.0/bfg-1.14.0.jar
fi

cd "$PROJECT_ROOT"

# =============================================================================
# PASO 1: Crear archivo de patrones de texto a eliminar
# =============================================================================

cat > /tmp/bfg-text-patterns.txt << 'EOF'
***REMOVED***
***REMOVED***
***REMOVED***
EOF

echo ""
echo "[1/6] Patrones a eliminar:"
cat /tmp/bfg-text-patterns.txt
echo ""

# =============================================================================
# PASO 2: Crear copia espejo del repo
# =============================================================================

echo "[2/6] Creando copia espejo del repositorio..."
MIRROR_DIR="/tmp/mobiliti-repo-mirror"
rm -rf "$MIRROR_DIR"
git clone --mirror . "$MIRROR_DIR"
cd "$MIRROR_DIR"

# =============================================================================
# PASO 3: Ejecutar BFG para reemplazar texto sensible
# =============================================================================

echo "[3/6] Ejecutando BFG para reemplazar texto sensible..."
java -jar "$BFG_JAR" --replace-text /tmp/bfg-text-patterns.txt

# =============================================================================
# PASO 4: Limpiar archivos grandes o binarios con credenciales (opcional)
# =============================================================================

echo "[4/6] Limpiando archivos de credenciales antiguos del historial..."

# Eliminar archivos credentials.json del historial
java -jar "$BFG_JAR" --delete-files credentials.json

# Eliminar cache de historial
rm -rf .git/refs/original/
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# =============================================================================
# PASO 5: Verificar que se limpio
# =============================================================================

echo "[5/6] Verificando que no queda texto sensible..."
if git log --all -p | grep -i "REMOVED_PASSWORD" > /dev/null 2>&1; then
    echo "ERROR: Aun se encuentra texto sensible en el historial!"
    exit 1
fi

if git log --all -p | grep -i "M0b1l1t1_S4AS_S3cr3t" > /dev/null 2>&1; then
    echo "ERROR: Aun se encuentra JWT_SECRET_KEY en el historial!"
    exit 1
fi

echo "OK: No se encontro texto sensible en el historial."

# =============================================================================
# PASO 6: Push force al remoto
# =============================================================================

echo ""
echo "[6/6] Listo para hacer push force al remoto."
echo ""
echo "Comando a ejecutar:"
echo "  cd ${MIRROR_DIR}"
echo "  git push --force"
echo ""
read -p "Ejecutar push force ahora? Escribe 'SI' para continuar: " PUSH_CONFIRM

if [ "$PUSH_CONFIRM" == "SI" ]; then
    git push --force
    echo ""
    echo "==================================================================="
    echo "  LIMPIEZA COMPLETADA"
    echo "==================================================================="
    echo ""
    echo "IMPORTANTE:"
    echo "  - Todos los colaboradores deben clonar el repo de nuevo"
    echo "  - Cualquier fork o PR abierto quedara invalido"
    echo "  - Los backups locales del repo antiguo siguen teniendo los datos"
    echo ""
    echo "Siguiente paso: Cambiar la contrasena del admin en Supabase"
    echo "                (la contrasena anterior quedo expuesta)"
    echo ""
else
    echo "Push cancelado."
    echo "Para completar manualmente:"
    echo "  cd ${MIRROR_DIR}"
    echo "  git push --force"
fi

# Limpiar archivo temporal
rm -f /tmp/bfg-text-patterns.txt
