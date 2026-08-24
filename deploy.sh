#!/usr/bin/env bash
set -Eeuo pipefail

: "${VPS_PASS:?Defina VPS_PASS antes de executar o deploy.}"
: "${VPS_USER:?Defina VPS_USER antes de executar o deploy.}"
: "${VPS_HOST:?Defina VPS_HOST antes de executar o deploy.}"
: "${VPS_PATH:?Defina VPS_PATH antes de executar o deploy.}"

IMAGE_REPOSITORY="caveira-unificado"
CURRENT_IMAGE="${IMAGE_REPOSITORY}:latest"
CANDIDATE_IMAGE="${IMAGE_REPOSITORY}:candidate"
ROLLBACK_IMAGE="${IMAGE_REPOSITORY}:rollback"
CONTAINER_NAME="caveira-unificado"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_APP_PATH="${VPS_PATH%/}"
# Fica fora da pasta sincronizada; portanto, o rsync --delete nunca a alcanca.
REMOTE_DATA_PATH="${REMOTE_APP_PATH}-data"
REMOTE_DATA_BACKUP="${REMOTE_APP_PATH}-data-backup.tar.gz"

export SSHPASS="${VPS_PASS}"
SSH_CMD=(
    sshpass -e ssh
    -o StrictHostKeyChecking=no
    "${VPS_USER}@${VPS_HOST}"
)
RSYNC_SHELL="sshpass -e ssh -o StrictHostKeyChecking=no"

remote_run_container() {
    local image="$1"
    "${SSH_CMD[@]}" "docker run -d \
        --name '${CONTAINER_NAME}' \
        --restart unless-stopped \
        --env-file '${REMOTE_APP_PATH}/.env' \
        -v '${REMOTE_APP_PATH}/solutions.json:/app/solutions.json:ro' \
        -v '${REMOTE_DATA_PATH}:/app/data' \
        '${image}'"
}

rollback() {
    echo "Falha ao iniciar a nova versao. Tentando restaurar a imagem anterior..."
    "${SSH_CMD[@]}" "
        docker rm -f '${CONTAINER_NAME}' >/dev/null 2>&1 || true
        if [ -f '${REMOTE_DATA_BACKUP}' ]; then
            failed_data_path='${REMOTE_DATA_PATH}-failed-'\$(date +%Y%m%d%H%M%S)
            mv '${REMOTE_DATA_PATH}' \"\${failed_data_path}\"
            mkdir -p '${REMOTE_DATA_PATH}'
            tar -xzf '${REMOTE_DATA_BACKUP}' -C '${REMOTE_DATA_PATH}'
        fi
    "
    if "${SSH_CMD[@]}" "docker image inspect '${ROLLBACK_IMAGE}' >/dev/null 2>&1"; then
        remote_run_container "${ROLLBACK_IMAGE}"
        echo "Rollback concluido. A versao anterior voltou a funcionar."
    else
        echo "Nao existe imagem de rollback disponivel." >&2
    fi
}

echo "=== [1/6] Enviando arquivos para a VPS ==="
rsync -avz \
    --delete \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'deploy.sh' \
    -e "${RSYNC_SHELL}" \
    "${PROJECT_DIR}/" \
    "${VPS_USER}@${VPS_HOST}:${REMOTE_APP_PATH}/"

echo "=== [2/6] Preparando rollback e fazendo build ==="
"${SSH_CMD[@]}" "
    set -e
    if [ ! -f '${REMOTE_APP_PATH}/.env' ]; then
        echo 'Arquivo .env nao encontrado na VPS.' >&2
        exit 1
    fi
    if docker image inspect '${CURRENT_IMAGE}' >/dev/null 2>&1; then
        docker tag '${CURRENT_IMAGE}' '${ROLLBACK_IMAGE}'
    fi
    cd '${REMOTE_APP_PATH}'
    docker build -t '${CANDIDATE_IMAGE}' .
"

echo "=== [3/6] Parando o container atual ==="
"${SSH_CMD[@]}" "
    if docker container inspect '${CONTAINER_NAME}' >/dev/null 2>&1; then
        docker stop '${CONTAINER_NAME}'
    fi
"

echo "=== [4/6] Migrando e protegendo os dados ==="
if ! "${SSH_CMD[@]}" "
    set -e
    mkdir -p '${REMOTE_DATA_PATH}'
    if docker container inspect '${CONTAINER_NAME}' >/dev/null 2>&1; then
        current_mount=\$(docker inspect -f '{{range .Mounts}}{{if eq .Destination \"/app/data\"}}{{.Source}}{{end}}{{end}}' '${CONTAINER_NAME}')
        if [ \"\${current_mount}\" != '${REMOTE_DATA_PATH}' ] && [ ! -f '${REMOTE_DATA_PATH}/.migration-complete' ]; then
            docker cp '${CONTAINER_NAME}:/app/data/.' '${REMOTE_DATA_PATH}/'
        fi
    fi
    touch '${REMOTE_DATA_PATH}/.migration-complete'
    tar -czf '${REMOTE_DATA_BACKUP}' -C '${REMOTE_DATA_PATH}' .
"; then
    echo "A copia dos dados falhou. O container anterior sera reiniciado." >&2
    "${SSH_CMD[@]}" "docker start '${CONTAINER_NAME}' >/dev/null 2>&1 || true"
    exit 1
fi

echo "=== [5/6] Subindo a nova versao ==="
"${SSH_CMD[@]}" "docker rm '${CONTAINER_NAME}' >/dev/null 2>&1 || true"
if ! remote_run_container "${CANDIDATE_IMAGE}"; then
    rollback
    exit 1
fi

echo "=== [6/6] Validando o container ==="
sleep 8
container_status="$(
    "${SSH_CMD[@]}" "docker inspect -f '{{.State.Status}}' '${CONTAINER_NAME}' 2>/dev/null || true"
)"
if [ "${container_status}" != "running" ]; then
    "${SSH_CMD[@]}" "docker logs --tail 100 '${CONTAINER_NAME}' 2>&1 || true"
    rollback
    exit 1
fi

"${SSH_CMD[@]}" "docker tag '${CANDIDATE_IMAGE}' '${CURRENT_IMAGE}'"
echo "Deploy concluido. Container em execucao e dados persistentes preservados."
