#!/usr/bin/env bash
# =============================================================================
# ec2_setup_gpu.sh — Configuración post-lanzamiento de la VM EC2 GPU para
#                    benchmarks de LLMs con Ollama + NVIDIA
# =============================================================================
# Conecta por SSH a la instancia EC2 GPU aprovisionada y ejecuta la
# configuración: verifica drivers NVIDIA, instala Docker con soporte GPU,
# NVIDIA Container Toolkit, Ollama, y clona el repositorio del proyecto.
#
# Diferencias con ec2_setup.sh (CPU):
#   - Verifica/instala drivers NVIDIA y CUDA
#   - Instala NVIDIA Container Toolkit para Docker
#   - Configura Docker runtime con soporte GPU
#   - Verifica acceso a GPU desde contenedores
#
# Uso:
#   ./ec2_setup_gpu.sh --instance-ip <IP> --key-file <ruta.pem> --repo-url <URL> [opciones]
#
# Parámetros:
#   --instance-ip  (obligatorio) IP pública de la instancia EC2
#   --key-file     (obligatorio) Ruta al archivo .pem del Key Pair SSH
#   --repo-url     (obligatorio) URL del repositorio Git del proyecto
#   --env-file     (opcional)    Ruta al archivo .env local con credenciales
#
# Ejemplo:
#   ./ec2_setup_gpu.sh \
#     --instance-ip 3.15.45.67 \
#     --key-file velvet-key.pem \
#     --repo-url https://github.com/org/velvet-project.git \
#     --env-file ./infra/.env
#
# Requisitos:
#   - Instancia EC2 GPU en estado 'running' (lanzada con ec2_provision_gpu.sh)
#   - Archivo .pem con permisos 400
#   - Conectividad SSH a la instancia (puerto 22 abierto)
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
INSTANCE_IP=""
KEY_FILE=""
REPO_URL=""
ENV_FILE=""
SSH_USER="ec2-user"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=30"

# ─────────────────────────────────────────────────────────────────────────────
# Funciones auxiliares
# ─────────────────────────────────────────────────────────────────────────────

info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<EOF
Uso: $(basename "$0") --instance-ip <IP> --key-file <ruta.pem> --repo-url <URL> [opciones]

Parámetros:
  --instance-ip  (obligatorio) IP pública de la instancia EC2
  --key-file     (obligatorio) Ruta al archivo .pem del Key Pair SSH
  --repo-url     (obligatorio) URL del repositorio Git del proyecto
  --env-file     (opcional)    Ruta al archivo .env con credenciales
  -h, --help                   Mostrar esta ayuda

Ejemplo:
  $(basename "$0") \\
    --instance-ip 3.15.45.67 \\
    --key-file velvet-key.pem \\
    --repo-url https://github.com/org/velvet-project.git \\
    --env-file ./infra/.env
EOF
    exit 0
}

remote_exec() {
    ssh $SSH_OPTS -i "$KEY_FILE" "${SSH_USER}@${INSTANCE_IP}" "$@"
}

remote_copy() {
    scp $SSH_OPTS -i "$KEY_FILE" "$1" "${SSH_USER}@${INSTANCE_IP}:$2"
}

# ─────────────────────────────────────────────────────────────────────────────
# Parseo de argumentos
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance-ip)
            INSTANCE_IP="$2"
            shift 2
            ;;
        --key-file)
            KEY_FILE="$2"
            shift 2
            ;;
        --repo-url)
            REPO_URL="$2"
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            error "Parámetro desconocido: $1. Usa --help para ver las opciones."
            ;;
    esac
done

# Validar parámetros obligatorios
[[ -z "$INSTANCE_IP" ]] && error "El parámetro --instance-ip es obligatorio."
[[ -z "$KEY_FILE" ]]    && error "El parámetro --key-file es obligatorio."
[[ -z "$REPO_URL" ]]    && error "El parámetro --repo-url es obligatorio."
[[ ! -f "$KEY_FILE" ]]  && error "El archivo de clave SSH no existe: $KEY_FILE"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Verificar conectividad SSH
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando conectividad SSH con $INSTANCE_IP..."
remote_exec "echo 'Conexión SSH exitosa'" || error "No se pudo conectar por SSH a $INSTANCE_IP."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Verificar/Instalar drivers NVIDIA
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando drivers NVIDIA en la instancia..."
remote_exec << 'REMOTE_NVIDIA'
set -euo pipefail

if command -v nvidia-smi &> /dev/null; then
    echo ">>> Drivers NVIDIA ya instalados:"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
else
    echo ">>> Drivers NVIDIA no encontrados. Instalando..."

    # Instalar dependencias de compilación
    sudo dnf groupinstall -y "Development Tools"
    sudo dnf install -y kernel-devel kernel-headers dkms

    # Instalar drivers NVIDIA desde el repositorio de Amazon
    sudo dnf install -y nvidia-driver nvidia-driver-cuda

    # Verificar instalación
    if command -v nvidia-smi &> /dev/null; then
        echo ">>> Drivers NVIDIA instalados correctamente:"
        nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
    else
        echo "ERROR: No se pudieron instalar los drivers NVIDIA."
        echo "Puede ser necesario reiniciar la instancia y volver a ejecutar este script."
        exit 1
    fi
fi
REMOTE_NVIDIA

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Instalar Docker Engine
# ─────────────────────────────────────────────────────────────────────────────
info "Instalando Docker Engine..."
remote_exec << 'REMOTE_DOCKER'
set -euo pipefail

if command -v docker &> /dev/null; then
    echo ">>> Docker ya instalado: $(docker --version)"
else
    echo ">>> Instalando Docker..."
    sudo dnf install -y docker
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
    echo ">>> Docker instalado: $(docker --version)"
fi

# Asegurar que Docker está corriendo
sudo systemctl start docker

# Instalar Docker Compose plugin
DOCKER_CLI_PLUGINS="${HOME}/.docker/cli-plugins"
mkdir -p "$DOCKER_CLI_PLUGINS"

if [ ! -f "$DOCKER_CLI_PLUGINS/docker-compose" ]; then
    echo ">>> Instalando Docker Compose plugin..."
    COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/')
    curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
        -o "$DOCKER_CLI_PLUGINS/docker-compose"
    chmod +x "$DOCKER_CLI_PLUGINS/docker-compose"
    echo ">>> Docker Compose $COMPOSE_VERSION instalado."
else
    echo ">>> Docker Compose ya instalado."
fi
REMOTE_DOCKER

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Instalar NVIDIA Container Toolkit
# ─────────────────────────────────────────────────────────────────────────────
info "Instalando NVIDIA Container Toolkit..."
remote_exec << 'REMOTE_NVIDIA_DOCKER'
set -euo pipefail

echo ">>> Configurando repositorio de NVIDIA Container Toolkit..."
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | \
    sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo > /dev/null

echo ">>> Instalando nvidia-container-toolkit..."
sudo dnf install -y nvidia-container-toolkit

echo ">>> Configurando Docker runtime con soporte NVIDIA..."
sudo nvidia-ctk runtime configure --runtime=docker

echo ">>> Reiniciando Docker para aplicar configuración GPU..."
sudo systemctl restart docker

echo ">>> Verificando acceso a GPU desde Docker..."
# Esperar a que Docker esté listo
sleep 3
sudo docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi \
    || echo "ADVERTENCIA: No se pudo verificar GPU en Docker. Puede requerir reinicio."

echo ">>> NVIDIA Container Toolkit configurado correctamente."
REMOTE_NVIDIA_DOCKER

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Instalar huggingface-cli y herramientas de conversión GGUF
# ─────────────────────────────────────────────────────────────────────────────
info "Instalando huggingface-cli y herramientas de conversión..."
remote_exec << 'REMOTE_HF'
set -euo pipefail

sudo dnf install -y python3-pip cmake gcc-c++ git
pip3 install --user huggingface_hub[cli] gguf sentencepiece protobuf torch --extra-index-url https://download.pytorch.org/whl/cpu
export PATH="$HOME/.local/bin:$PATH"

# Clonar llama.cpp para usar el script de conversión
if [ ! -d "$HOME/llama.cpp" ]; then
    echo ">>> Clonando llama.cpp para conversión de modelos..."
    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$HOME/llama.cpp"
fi

# Instalar dependencias del script de conversión
pip3 install --user -r "$HOME/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt" 2>/dev/null || \
    pip3 install --user numpy sentencepiece protobuf transformers

echo ">>> Herramientas de conversión instaladas."
echo ">>> Para convertir un modelo:"
echo ">>>   python3 ~/llama.cpp/convert_hf_to_gguf.py /path/to/model --outtype q4_k_m"
REMOTE_HF

# ─────────────────────────────────────────────────────────────────────────────
# Paso 6: Clonar el repositorio del proyecto
# ─────────────────────────────────────────────────────────────────────────────
info "Clonando el repositorio del proyecto..."
remote_exec << REMOTE_CLONE
set -euo pipefail

sudo dnf install -y git

REPO_DIR="\$HOME/velvet-project"
if [ -d "\$REPO_DIR" ]; then
    echo ">>> Repositorio ya existe. Actualizando..."
    cd "\$REPO_DIR"
    git pull
else
    echo ">>> Clonando: $REPO_URL"
    git clone "$REPO_URL" "\$REPO_DIR"
fi
echo ">>> Repositorio en \$REPO_DIR"
REMOTE_CLONE

# ─────────────────────────────────────────────────────────────────────────────
# Paso 7: Copiar archivo .env (si se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        error "El archivo .env no existe: $ENV_FILE"
    fi
    info "Copiando archivo .env a la instancia..."
    remote_copy "$ENV_FILE" "~/velvet-project/infra/.env"
    info "Archivo .env copiado."
else
    info "No se proporcionó --env-file. Cópialo manualmente después."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 8: Configurar variables de entorno
# ─────────────────────────────────────────────────────────────────────────────
info "Configurando variables de entorno..."
remote_exec << 'REMOTE_ENV'
set -euo pipefail

# HF_TOKEN
ENV_PATH="$HOME/velvet-project/infra/.env"
if [ -f "$ENV_PATH" ]; then
    HF_TOKEN_VALUE=$(grep '^HF_TOKEN=' "$ENV_PATH" | cut -d'=' -f2- || true)
    if [ -n "$HF_TOKEN_VALUE" ]; then
        if ! grep -q 'export HF_TOKEN=' "$HOME/.bashrc" 2>/dev/null; then
            echo "export HF_TOKEN=$HF_TOKEN_VALUE" >> "$HOME/.bashrc"
        else
            sed -i "s|^export HF_TOKEN=.*|export HF_TOKEN=$HF_TOKEN_VALUE|" "$HOME/.bashrc"
        fi
        echo ">>> HF_TOKEN configurado."
    fi
fi

# PATH
if ! grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi
REMOTE_ENV

# ─────────────────────────────────────────────────────────────────────────────
# Paso 9: Verificación final
# ─────────────────────────────────────────────────────────────────────────────
info "Ejecutando verificación final..."
remote_exec << 'REMOTE_VERIFY'
set -euo pipefail

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " VERIFICACIÓN DEL SISTEMA GPU"
echo "═══════════════════════════════════════════════════════════════"

echo ""
echo ">>> GPU detectada:"
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv,noheader

echo ""
echo ">>> CUDA version:"
nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1

echo ""
echo ">>> Docker:"
sg docker -c "docker info" 2>/dev/null | grep -E "Server Version|Runtimes" || sudo docker info | grep -E "Server Version|Runtimes"

echo ""
echo ">>> Docker Compose:"
export PATH="$HOME/.docker/cli-plugins:$PATH"
docker compose version 2>/dev/null || "$HOME/.docker/cli-plugins/docker-compose" version

echo ""
echo ">>> Espacio en disco:"
df -h / | tail -1

echo ""
echo ">>> Memoria del sistema:"
free -h | grep Mem

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " SISTEMA LISTO PARA BENCHMARKS GPU"
echo "═══════════════════════════════════════════════════════════════"
REMOTE_VERIFY

# ─────────────────────────────────────────────────────────────────────────────
# Resumen final
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================================="
echo " CONFIGURACIÓN GPU COMPLETADA"
echo "============================================================================="
echo " Instancia:       $INSTANCE_IP"
echo " GPU:             NVIDIA A10G (24 GB VRAM)"
echo " Repositorio:     ~/velvet-project"
echo " Docker + GPU:    Configurado con NVIDIA Container Toolkit"
echo " Docker Compose:  Instalado"
echo " huggingface-cli: Instalado"
if [[ -n "$ENV_FILE" ]]; then
echo " Archivo .env:    Copiado"
fi
echo "============================================================================="
echo ""
echo " Siguiente paso — iniciar Ollama con GPU:"
echo "   ssh $SSH_OPTS -i $KEY_FILE ${SSH_USER}@${INSTANCE_IP}"
echo "   cd ~/velvet-project/infra"
echo "   docker compose -f docker-compose-gpu.yml up -d"
echo ""
echo " Verificar que Ollama usa la GPU:"
echo "   docker exec ollama nvidia-smi"
echo "   curl http://localhost:11434/api/tags"
echo "============================================================================="
