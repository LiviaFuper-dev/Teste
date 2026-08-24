#!/bin/bash
set -Eeuo pipefail

# deploy.sh — Deploy do Caveira Unificado para a VPS via rsync + Docker
#
# Uso (dentro do WSL):
#   bash deploy.sh
#
# O .env, o solutions.json e a pasta data ficam na VPS e nao sao apagados.

: "${VPS_USER:?Defina VPS_USER antes de executar o deploy.}"
: "${VPS_HOST:?Defina VPS_HOST antes de executar o deploy.}"
: "${VPS_PASS:?Defina VPS_PASS antes de executar o deploy.}"
: "${VPS_PATH:?Defina VPS_PATH antes de executar o deploy.}"

IMAGE_NAME="${IMAGE_NAME:-caveira-unificado:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-caveira-unificado}"
ROLLBACK_IMAGE="${IMAGE_NAME%:*}:rollback"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_PATH="${VPS_PATH%/}/data"
DATA_BACKUP="${VPS_PATH%/}/data-backup.tar.gz"

export SSHPASS="${VPS_PASS}"
SSH_CMD="sshpass -e ssh -o StrictHostKeyChecking=no ${VPS_USER}@${VPS_HOST}"
RSYNC_CMD="sshpass -e rsync -avz"

rollback_deploy() {
    echo "Falha na nova versao. Restaurando o container anterior..."
    ${SSH_CMD} "
        set -e
        docker rm -f '${CONTAINER_NAME}' >/dev/null 2>&1 || true
        if [ -f '${DATA_BACKUP}' ]; then
            failed_data_path='${DATA_PATH}-failed-'\$(date +%Y%m%d%H%M%S)
            mv '${DATA_PATH}' \"\${failed_data_path}\"
            mkdir -p '${DATA_PATH}'
            tar -xzf '${DATA_BACKUP}' -C '${DATA_PATH}'
        fi
        docker image inspect '${ROLLBACK_IMAGE}' >/dev/null
        docker run -d \
            --name '${CONTAINER_NAME}' \
            --restart unless-stopped \
            --env-file '${VPS_PATH%/}/.env' \
            -v '${VPS_PATH%/}/solutions.json:/app/solutions.json:ro' \
            -v '${DATA_PATH}:/app/data' \
            '${ROLLBACK_IMAGE}'
    "
    echo "Rollback concluido. A versao anterior voltou a funcionar."
}

echo "=== [1/4] Enviando arquivos para a VPS ==="
${RSYNC_CMD} \
    --delete \
    --exclude '.env' \
    --exclude 'solutions.json' \
    --exclude 'data/' \
    --exclude 'data-backup.tar.gz' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'deploy.sh' \
    -e "sshpass -e ssh -o StrictHostKeyChecking=no" \
    "${PROJECT_DIR}/" \
    "${VPS_USER}@${VPS_HOST}:${VPS_PATH%/}/"

echo "=== [2/4] Fazendo build da imagem na VPS ==="
${SSH_CMD} "
    set -e
    if [ ! -f '${VPS_PATH%/}/.env' ]; then
        echo 'Arquivo .env nao encontrado na VPS.' >&2
        exit 1
    fi
    if [ ! -f '${VPS_PATH%/}/solutions.json' ]; then
        echo 'Arquivo solutions.json nao encontrado na VPS.' >&2
        exit 1
    fi
    if docker image inspect '${IMAGE_NAME}' >/dev/null 2>&1; then
        docker tag '${IMAGE_NAME}' '${ROLLBACK_IMAGE}'
    fi
    cd '${VPS_PATH%/}'
    docker build -t '${IMAGE_NAME}' .
"

echo "=== [3/4] Parando container antigo e preservando os dados ==="
if ! ${SSH_CMD} "
    set -e
    mkdir -p '${DATA_PATH}'
    if docker container inspect '${CONTAINER_NAME}' >/dev/null 2>&1; then
        docker stop '${CONTAINER_NAME}'
        current_mount=\$(docker inspect -f '{{range .Mounts}}{{if eq .Destination \"/app/data\"}}{{.Source}}{{end}}{{end}}' '${CONTAINER_NAME}')
        if [ \"\${current_mount}\" != '${DATA_PATH}' ] && [ ! -f '${DATA_PATH}/.migration-complete' ]; then
            docker cp '${CONTAINER_NAME}:/app/data/.' '${DATA_PATH}/'
        fi
    fi
    touch '${DATA_PATH}/.migration-complete'
    tar -czf '${DATA_BACKUP}' -C '${DATA_PATH}' .
    if docker container inspect '${CONTAINER_NAME}' >/dev/null 2>&1; then
        docker rm '${CONTAINER_NAME}'
    fi
"; then
    echo "A preservacao dos dados falhou. O container anterior sera reiniciado." >&2
    ${SSH_CMD} "docker start '${CONTAINER_NAME}' >/dev/null 2>&1 || true"
    exit 1
fi

echo "=== [4/4] Subindo novo container ==="
if ! ${SSH_CMD} "docker run -d \
    --name '${CONTAINER_NAME}' \
    --restart unless-stopped \
    --env-file '${VPS_PATH%/}/.env' \
    -v '${VPS_PATH%/}/solutions.json:/app/solutions.json:ro' \
    -v '${DATA_PATH}:/app/data' \
    '${IMAGE_NAME}'"; then
    rollback_deploy
    exit 1
fi

sleep 8
CONTAINER_STATUS="$(${SSH_CMD} "docker inspect -f '{{.State.Status}}' '${CONTAINER_NAME}' 2>/dev/null || true")"
if [ "${CONTAINER_STATUS}" != "running" ]; then
    ${SSH_CMD} "docker logs --tail 100 '${CONTAINER_NAME}' 2>&1 || true"
    rollback_deploy
    exit 1
fi

echo ""
echo "Deploy concluido! Bot no ar e dados preservados."
