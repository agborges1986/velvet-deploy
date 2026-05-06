#!/usr/bin/env bash
# =============================================================================
# ec2_deploy_models.sh — Descarga, convierte y despliega modelos Velvet en GPU
# =============================================================================
# Descarga los modelos Velvet desde Hugging Face, los convierte a formato GGUF
# con cuantización Q4_K_M usando llama.cpp, y los registra en Ollama.
#
# Prerequisitos:
#   - Instancia GPU configurada con ec2_setup_gpu.sh (incluye llama.cpp)
#   - HF_TOKEN configurado (para modelos gated de Almawave)
#   - Ollama corriendo con docker-compose-gpu.yml
#
# Uso:
#   ./ec2_deploy_models.sh --instance-ip <IP> --key-file <ruta.pem> [opciones]
#
# Parámetros:
#   --instance-ip  (obligatorio) IP pública de la instancia EC2
#   --key-file     (obligatorio) Ruta al archivo .pem del Key Pair SSH
#   --hf-token     (opcional)    Token de Hugging Face (o usa HF_TOKEN env var)
#   --models       (opcional)    Modelos a desplegar (default: velvet-2b,velvet-14b,gemma-4b)
#   --quantization (opcional)    Nivel de cuantización (default: q4_k_m)
#
# Ejemplo:
#   ./ec2_deploy_models.sh \
#     --instance-ip 18.188.203.92 \
#     --key-file velvet-key.pem \
#     --hf-token hf_xxxxx
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
INSTANCE_IP=""
KEY_FILE=""
HF_TOKEN="${HF_TOKEN:-}"
MODELS="velvet-2b,velvet-14b,gemma-4b"
QUANTIZATION="q4_k_m"
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
Uso: $(basename "$0") --instance-ip <IP> --key-file <ruta.pem> [opciones]

Parámetros:
  --instance-ip  (obligatorio) IP pública de la instancia EC2
  --key-file     (obligatorio) Ruta al archivo .pem del Key Pair SSH
  --hf-token     (opcional)    Token de Hugging Face
  --models       (opcional)    Modelos separados por coma (default: velvet-2b,velvet-14b,gemma-4b)
  --quantization (opcional)    Cuantización: q4_k_m, q5_k_m, q8_0, f16 (default: q4_k_m)
  -h, --help                   Mostrar esta ayuda

Ejemplo:
  $(basename "$0") --instance-ip 18.188.203.92 --key-file velvet-key.pem --hf-token hf_xxx
EOF
    exit 0
}

remote_exec() {
    ssh $SSH_OPTS -i "$KEY_FILE" "${SSH_USER}@${INSTANCE_IP}" "$@"
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
        --hf-token)
            HF_TOKEN="$2"
            shift 2
            ;;
        --models)
            MODELS="$2"
            shift 2
            ;;
        --quantization)
            QUANTIZATION="$2"
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

[[ -z "$INSTANCE_IP" ]] && error "El parámetro --instance-ip es obligatorio."
[[ -z "$KEY_FILE" ]]    && error "El parámetro --key-file es obligatorio."
[[ ! -f "$KEY_FILE" ]]  && error "El archivo de clave SSH no existe: $KEY_FILE"
[[ -z "$HF_TOKEN" ]]    && error "Se requiere --hf-token o la variable de entorno HF_TOKEN."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Verificar conectividad y prerequisitos
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando conectividad y prerequisitos..."
remote_exec "nvidia-smi --query-gpu=name --format=csv,noheader && echo GPU_OK" || error "No se detectó GPU."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Configurar HF_TOKEN en la instancia
# ─────────────────────────────────────────────────────────────────────────────
info "Configurando token de Hugging Face..."
remote_exec "mkdir -p ~/.cache/huggingface && echo '$HF_TOKEN' > ~/.cache/huggingface/token && export HF_TOKEN='$HF_TOKEN' && echo 'export HF_TOKEN=$HF_TOKEN' > ~/.hf_env && echo TOKEN_OK"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Descargar y convertir cada modelo
# ─────────────────────────────────────────────────────────────────────────────
IFS=',' read -ra MODEL_LIST <<< "$MODELS"

for model in "${MODEL_LIST[@]}"; do
    model=$(echo "$model" | xargs)  # trim whitespace

    case "$model" in
        velvet-2b)
            HF_REPO="Almawave/Velvet-2B"
            OLLAMA_NAME="velvet-2b-gpu-v1"
            NUM_CTX=4096
            NUM_PREDICT=512
            ;;
        velvet-14b)
            HF_REPO="Almawave/Velvet-14B"
            OLLAMA_NAME="velvet-14b-gpu-v1"
            NUM_CTX=4096
            NUM_PREDICT=1024
            ;;
        gemma-4b)
            # Gemma 4B se descarga directamente desde Ollama (ya tiene GGUF)
            info "[$model] Descargando gemma3:4b desde Ollama registry..."
            remote_exec "docker exec velvet-ollama-gpu ollama pull gemma3:4b"
            info "[$model] ✅ Listo"
            continue
            ;;
        *)
            info "[$model] Modelo no reconocido, saltando..."
            continue
            ;;
    esac

    info "[$model] Descargando desde $HF_REPO..."
    remote_exec "source ~/.hf_env && export PATH=\$HOME/.local/bin:\$PATH && hf download $HF_REPO --local-dir /home/ec2-user/models/${model}"

    info "[$model] Convirtiendo a GGUF (${QUANTIZATION})..."
    remote_exec "export PATH=\$HOME/.local/bin:\$PATH && python3 ~/llama.cpp/convert_hf_to_gguf.py /home/ec2-user/models/${model} --outfile /home/ec2-user/models/${model}-${QUANTIZATION}.gguf --outtype ${QUANTIZATION}"

    info "[$model] Creando modelo en Ollama como '${OLLAMA_NAME}'..."
    # Crear Modelfile dentro del contenedor
    remote_exec "docker exec velvet-ollama-gpu sh -c 'printf \"FROM /models/${model}-${QUANTIZATION}.gguf\n\nPARAMETER temperature 0.3\nPARAMETER top_p 0.9\nPARAMETER num_ctx ${NUM_CTX}\nPARAMETER num_predict ${NUM_PREDICT}\n\" > /tmp/${model}.Modelfile && ollama create ${OLLAMA_NAME} -f /tmp/${model}.Modelfile'"

    info "[$model] ✅ Modelo '${OLLAMA_NAME}' desplegado"
done

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Verificar modelos desplegados
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando modelos desplegados en Ollama..."
remote_exec "docker exec velvet-ollama-gpu ollama list"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Test rápido de inferencia GPU
# ─────────────────────────────────────────────────────────────────────────────
info "Ejecutando test rápido de inferencia..."
FIRST_MODEL="${MODEL_LIST[0]}"
case "$FIRST_MODEL" in
    velvet-2b)  TEST_MODEL="velvet-2b-gpu-v1" ;;
    velvet-14b) TEST_MODEL="velvet-14b-gpu-v1" ;;
    gemma-4b)   TEST_MODEL="gemma3:4b" ;;
    *)          TEST_MODEL="gemma3:4b" ;;
esac

remote_exec "docker exec velvet-ollama-gpu sh -c 'echo \"Hola, responde brevemente\" | ollama run ${TEST_MODEL} --verbose 2>&1' | grep -E 'eval rate|eval count|total duration'"

echo ""
echo "============================================================================="
echo " MODELOS DESPLEGADOS EXITOSAMENTE"
echo "============================================================================="
echo " Instancia:       $INSTANCE_IP"
echo " Modelos:"
for model in "${MODEL_LIST[@]}"; do
    model=$(echo "$model" | xargs)
    case "$model" in
        velvet-2b)  echo "   ✓ velvet-2b-gpu-v1 (Almawave/Velvet-2B, ${QUANTIZATION})" ;;
        velvet-14b) echo "   ✓ velvet-14b-gpu-v1 (Almawave/Velvet-14B, ${QUANTIZATION})" ;;
        gemma-4b)   echo "   ✓ gemma3:4b (Google, desde Ollama registry)" ;;
    esac
done
echo " Cuantización:    ${QUANTIZATION}"
echo " GPU:             NVIDIA A10G (24 GB VRAM)"
echo "============================================================================="
echo ""
echo " Para ejecutar benchmarks:"
echo "   ssh $SSH_OPTS -i $KEY_FILE ${SSH_USER}@${INSTANCE_IP}"
echo "   cd ~/velvet-project"
echo "   python -m pytest test/ --backend ollama --model velvet-2b-gpu-v1 -v"
echo "============================================================================="
