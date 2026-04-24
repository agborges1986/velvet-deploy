#!/usr/bin/env bash
# =============================================================================
# ec2_setup.sh — Configuración post-lanzamiento de la VM EC2 para Velvet
# =============================================================================
# Conecta por SSH a la instancia EC2 aprovisionada y ejecuta la configuración
# inicial: instala Docker, Docker Compose, huggingface-cli, clona el
# repositorio del proyecto y configura las credenciales necesarias.
#
# Uso:
#   ./ec2_setup.sh --instance-ip <IP> --key-file <ruta.pem> --repo-url <URL> [opciones]
#
# Parámetros:
#   --instance-ip  (obligatorio) IP pública de la instancia EC2
#   --key-file     (obligatorio) Ruta al archivo .pem del Key Pair SSH
#   --repo-url     (obligatorio) URL del repositorio Git del proyecto
#   --env-file     (opcional)    Ruta al archivo .env local con credenciales
#                                (OLLAMA_AUTH_USER, OLLAMA_AUTH_HASH, HF_TOKEN)
#
# Ejemplo:
#   ./ec2_setup.sh \
#     --instance-ip 54.123.45.67 \
#     --key-file velvet-key.pem \
#     --repo-url https://github.com/org/velvet-project.git \
#     --env-file ./infra/.env
#
# Requisitos:
#   - Instancia EC2 en estado 'running' (lanzada con ec2_provision.sh)
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
SSH_USER="ec2-user"          # Usuario por defecto en Amazon Linux 2023
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
    --instance-ip 54.123.45.67 \\
    --key-file velvet-key.pem \\
    --repo-url https://github.com/org/velvet-project.git \\
    --env-file ./infra/.env
EOF
    exit 0
}

# Ejecuta un comando en la instancia remota vía SSH
remote_exec() {
    ssh $SSH_OPTS -i "$KEY_FILE" "${SSH_USER}@${INSTANCE_IP}" "$@"
}

# Copia un archivo local a la instancia remota vía SCP
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

# Verificar que el archivo .pem existe
[[ ! -f "$KEY_FILE" ]] && error "El archivo de clave SSH no existe: $KEY_FILE"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Verificar conectividad SSH
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando conectividad SSH con $INSTANCE_IP..."
remote_exec "echo 'Conexión SSH exitosa'" || error "No se pudo conectar por SSH a $INSTANCE_IP. Verifica la IP, el archivo .pem y las reglas del Security Group."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Instalar Docker Engine y Docker Compose plugin
# ─────────────────────────────────────────────────────────────────────────────
info "Instalando Docker Engine y Docker Compose en la instancia..."
remote_exec << 'REMOTE_DOCKER'
set -euo pipefail

echo ">>> Actualizando paquetes del sistema..."
sudo dnf update -y -q

echo ">>> Instalando Docker..."
sudo dnf install -y docker

echo ">>> Iniciando y habilitando el servicio Docker..."
sudo systemctl start docker
sudo systemctl enable docker

echo ">>> Agregando el usuario al grupo docker (evita usar sudo)..."
sudo usermod -aG docker $USER

echo ">>> Instalando Docker Compose plugin..."
# Crear directorio de plugins de Docker CLI si no existe
DOCKER_CLI_PLUGINS="${HOME}/.docker/cli-plugins"
mkdir -p "$DOCKER_CLI_PLUGINS"

# Descargar la última versión estable de Docker Compose
COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/')
echo ">>> Versión de Docker Compose: $COMPOSE_VERSION"
curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o "$DOCKER_CLI_PLUGINS/docker-compose"
chmod +x "$DOCKER_CLI_PLUGINS/docker-compose"

echo ">>> Docker y Docker Compose instalados correctamente."
REMOTE_DOCKER

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Instalar huggingface-cli para descarga de modelos
# ─────────────────────────────────────────────────────────────────────────────
info "Instalando huggingface-cli en la instancia..."
remote_exec << 'REMOTE_HF'
set -euo pipefail

echo ">>> Instalando pip y huggingface_hub..."
sudo dnf install -y python3-pip
pip3 install --user huggingface_hub[cli]

echo ">>> Verificando instalación de huggingface-cli..."
export PATH="$HOME/.local/bin:$PATH"
huggingface-cli version

echo ">>> huggingface-cli instalado correctamente."
REMOTE_HF

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Clonar el repositorio del proyecto
# ─────────────────────────────────────────────────────────────────────────────
info "Clonando el repositorio del proyecto en la instancia..."
remote_exec << REMOTE_CLONE
set -euo pipefail

echo ">>> Instalando git..."
sudo dnf install -y git

REPO_DIR="\$HOME/velvet-project"
if [ -d "\$REPO_DIR" ]; then
    echo ">>> El directorio del proyecto ya existe. Actualizando..."
    cd "\$REPO_DIR"
    git pull
else
    echo ">>> Clonando repositorio: $REPO_URL"
    git clone "$REPO_URL" "\$REPO_DIR"
fi

echo ">>> Repositorio clonado en \$REPO_DIR"
REMOTE_CLONE

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Copiar archivo .env con credenciales (si se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        error "El archivo .env no existe: $ENV_FILE"
    fi

    info "Copiando archivo .env a la instancia..."
    remote_copy "$ENV_FILE" "~/velvet-project/infra/.env"
    info "Archivo .env copiado a ~/velvet-project/infra/.env"
else
    info "No se proporcionó --env-file. Recuerda copiar el archivo .env manualmente:"
    info "  scp $SSH_OPTS -i $KEY_FILE ./infra/.env ${SSH_USER}@${INSTANCE_IP}:~/velvet-project/infra/.env"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 6: Configurar HF_TOKEN como variable de entorno
# ─────────────────────────────────────────────────────────────────────────────
info "Configurando HF_TOKEN en el perfil del usuario..."
remote_exec << 'REMOTE_HF_TOKEN'
set -euo pipefail

# Agregar HF_TOKEN al perfil si existe en el archivo .env
ENV_PATH="$HOME/velvet-project/infra/.env"
if [ -f "$ENV_PATH" ]; then
    HF_TOKEN_VALUE=$(grep '^HF_TOKEN=' "$ENV_PATH" | cut -d'=' -f2- || true)
    if [ -n "$HF_TOKEN_VALUE" ]; then
        # Agregar al .bashrc para que esté disponible en sesiones futuras
        if ! grep -q 'export HF_TOKEN=' "$HOME/.bashrc" 2>/dev/null; then
            echo "export HF_TOKEN=$HF_TOKEN_VALUE" >> "$HOME/.bashrc"
            echo ">>> HF_TOKEN configurado en .bashrc"
        else
            # Actualizar el valor existente
            sed -i "s|^export HF_TOKEN=.*|export HF_TOKEN=$HF_TOKEN_VALUE|" "$HOME/.bashrc"
            echo ">>> HF_TOKEN actualizado en .bashrc"
        fi
        export HF_TOKEN="$HF_TOKEN_VALUE"
    else
        echo ">>> ADVERTENCIA: HF_TOKEN no encontrado o vacío en .env"
        echo ">>> Configúralo manualmente: export HF_TOKEN=<tu_token>"
    fi
else
    echo ">>> ADVERTENCIA: Archivo .env no encontrado en $ENV_PATH"
    echo ">>> Configura HF_TOKEN manualmente: export HF_TOKEN=<tu_token>"
fi

# Agregar ~/.local/bin al PATH para huggingface-cli
if ! grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo ">>> PATH actualizado con ~/.local/bin"
fi
REMOTE_HF_TOKEN

# ─────────────────────────────────────────────────────────────────────────────
# Paso 7: Verificar que Docker está corriendo y Docker Compose responde
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando instalación de Docker y Docker Compose..."

# Necesitamos una nueva sesión SSH para que el grupo docker surta efecto
# Usamos 'sg docker' para ejecutar en el contexto del grupo sin cerrar sesión
remote_exec << 'REMOTE_VERIFY'
set -euo pipefail

echo ">>> Verificando servicio Docker..."
sudo systemctl is-active docker || { echo "ERROR: Docker no está corriendo"; exit 1; }

echo ">>> Verificando Docker CLI..."
sg docker -c "docker info" > /dev/null 2>&1 || sudo docker info > /dev/null 2>&1
echo "Docker está corriendo correctamente."

echo ">>> Verificando Docker Compose..."
export PATH="$HOME/.docker/cli-plugins:$HOME/.local/bin:$PATH"
docker compose version || "$HOME/.docker/cli-plugins/docker-compose" version
echo "Docker Compose está disponible."

echo ""
echo ">>> Todas las verificaciones pasaron correctamente."
REMOTE_VERIFY

# ─────────────────────────────────────────────────────────────────────────────
# Resumen final
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================================="
echo " CONFIGURACIÓN COMPLETADA"
echo "============================================================================="
echo " Instancia:       $INSTANCE_IP"
echo " Repositorio:     ~/velvet-project"
echo " Docker:          Instalado y corriendo"
echo " Docker Compose:  Instalado"
echo " huggingface-cli: Instalado"
if [[ -n "$ENV_FILE" ]]; then
echo " Archivo .env:    Copiado a ~/velvet-project/infra/.env"
else
echo " Archivo .env:    NO copiado (usa --env-file o cópialo manualmente)"
fi
echo "============================================================================="
echo ""
echo " Siguiente paso — iniciar el despliegue:"
echo "   ssh $SSH_OPTS -i $KEY_FILE ${SSH_USER}@${INSTANCE_IP}"
echo "   cd ~/velvet-project/infra"
echo "   docker compose up -d"
echo "============================================================================="
