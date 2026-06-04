#!/usr/bin/env bash
# Script para crear el repositorio en GitHub y subir el código
# Requiere un token de acceso personal de GitHub con permiso 'repo'

REPO_OWNER="REMOVED_PASSWORD"
REPO_NAME="mobiliti-generador"
TOKEN="${GITHUB_TOKEN:-$1}"

if [ -z "$TOKEN" ]; then
    echo "Uso: GITHUB_TOKEN=ghp_xxx ./create_github_repo.sh"
    echo "   o: ./create_github_repo.sh ghp_xxx"
    exit 1
fi

echo "Creando repositorio ${REPO_OWNER}/${REPO_NAME}..."

# Crear repo via API
curl -s -X POST \
  -H "Authorization: token ${TOKEN}" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/user/repos \
  -d "{\"name\":\"${REPO_NAME}\",\"private\":false,\"auto_init\":true}" | \
  python -c "import sys,json; d=json.load(sys.stdin); print('URL:', d.get('html_url','ERROR'), '| Msg:', d.get('message','OK'))"

echo ""
echo "Configurando remote y push..."
cd "$(dirname "$0")/.."
git remote remove origin 2>/dev/null
git remote add origin "https://${TOKEN}@github.com/${REPO_OWNER}/${REPO_NAME}.git"

echo "Subiendo rama feature/auto-updater..."
git push -u origin feature/auto-updater

echo ""
echo "Hecho! Visita: https://github.com/${REPO_OWNER}/${REPO_NAME}"
