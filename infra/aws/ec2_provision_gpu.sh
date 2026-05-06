#!/usr/bin/env bash
# =============================================================================
# ec2_provision_gpu.sh — Aprovisionamiento de instancia EC2 GPU (Spot) para
#                        benchmarks comparativos de LLMs con Ollama
# =============================================================================
# Lanza una instancia EC2 GPU Spot (g5.2xlarge — 8 vCPU, 32 GB RAM,
# 1x NVIDIA A10G 24 GB VRAM) en us-east-2 (Ohio) configurada para ejecutar
# modelos LLM de 2B a 24B con Docker + Ollama + NVIDIA Container Toolkit.
#
# Diferencias con ec2_provision.sh (CPU):
#   - Instancia g5.2xlarge con GPU NVIDIA A10G (24 GB VRAM)
#   - Lanzamiento como Spot Instance (~70% ahorro vs On-Demand)
#   - AMI: Deep Learning Base con drivers NVIDIA preinstalados
#   - Región: us-east-2 (Ohio) — mejor precio Spot para g5
#   - Volumen: 250 GB gp3 (espacio para múltiples modelos sin cuantizar)
#
# Uso:
#   ./ec2_provision_gpu.sh --key-name mi-clave [opciones]
#
# Parámetros:
#   --key-name    (obligatorio) Nombre del Key Pair de AWS para acceso SSH.
#                 Si no existe, el script lo crea y guarda el archivo .pem.
#   --ami-id      (opcional)    ID de la AMI. Por defecto: Deep Learning Base AMI
#                               (Amazon Linux 2) con drivers NVIDIA.
#   --region      (opcional)    Región de AWS. Por defecto: us-east-2.
#   --my-ip       (opcional)    IP del operador para reglas SSH. Si no se
#                               proporciona, se detecta automáticamente.
#   --on-demand   (opcional)    Lanzar como On-Demand en vez de Spot.
#   --max-price   (opcional)    Precio máximo Spot (default: $0.50/hr).
#
# Ejemplo:
#   ./ec2_provision_gpu.sh --key-name velvet-key
#   ./ec2_provision_gpu.sh --key-name velvet-key --on-demand
#
# Requisitos:
#   - AWS CLI v2 instalado y configurado (aws configure)
#   - Permisos IAM: ec2:RunInstances, ec2:CreateSecurityGroup,
#     ec2:AuthorizeSecurityGroupIngress, ec2:CreateKeyPair,
#     ec2:DescribeInstances, ec2:CreateTags, ec2:DescribeImages,
#     ec2:DescribeKeyPairs, ec2:RequestSpotInstances
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
REGION="us-east-2"
AMI_ID=""
KEY_NAME=""
MY_IP=""
INSTANCE_TYPE="g5.2xlarge"
VOLUME_SIZE=250              # GB — espacio para múltiples modelos GGUF/FP16
SG_NAME="velvet-gpu-benchmark-sg"
SG_DESCRIPTION="Security Group para benchmarks GPU de modelos LLM"
USE_SPOT=true
MAX_SPOT_PRICE="0.50"       # USD/hr — protección contra picos de precio Spot

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
Uso: $(basename "$0") --key-name <nombre> [opciones]

Parámetros:
  --key-name    (obligatorio) Nombre del Key Pair de AWS para acceso SSH
  --ami-id      (opcional)    ID de la AMI (default: Deep Learning Base AMI)
  --region      (opcional)    Región de AWS (default: us-east-2)
  --my-ip       (opcional)    IP del operador para SSH (default: auto-detectar)
  --on-demand   (opcional)    Lanzar como On-Demand (default: Spot)
  --max-price   (opcional)    Precio máximo Spot en USD/hr (default: 0.50)
  -h, --help                  Mostrar esta ayuda

Ejemplo:
  $(basename "$0") --key-name velvet-key
  $(basename "$0") --key-name velvet-key --on-demand
  $(basename "$0") --key-name velvet-key --max-price 0.40
EOF
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Parseo de argumentos
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key-name)
            KEY_NAME="$2"
            shift 2
            ;;
        --ami-id)
            AMI_ID="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --my-ip)
            MY_IP="$2"
            shift 2
            ;;
        --on-demand)
            USE_SPOT=false
            shift
            ;;
        --max-price)
            MAX_SPOT_PRICE="$2"
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

# Validar parámetro obligatorio
if [[ -z "$KEY_NAME" ]]; then
    error "El parámetro --key-name es obligatorio. Usa --help para ver las opciones."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Detectar IP del operador (si no se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -z "$MY_IP" ]]; then
    info "Detectando IP pública del operador..."
    MY_IP=$(curl -s https://checkip.amazonaws.com) || error "No se pudo detectar la IP pública. Usa --my-ip para especificarla manualmente."
    MY_IP=$(echo "$MY_IP" | tr -d '[:space:]')
    info "IP detectada: $MY_IP"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Obtener AMI de Deep Learning Base (si no se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -z "$AMI_ID" ]]; then
    # Prioridad 1: Deep Learning Base AMI con Amazon Linux 2023 (GCC 11+, NVIDIA drivers)
    info "Buscando Deep Learning Base AMI (Amazon Linux 2023) en $REGION..."
    AMI_ID=$(aws ec2 describe-images \
        --region "$REGION" \
        --owners amazon \
        --filters \
            "Name=name,Values=Deep Learning Base OSS Nvidia Driver AMI (Amazon Linux 2023)*" \
            "Name=state,Values=available" \
            "Name=architecture,Values=x86_64" \
        --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
        --output text) || true

    # Prioridad 2: Deep Learning AMI con PyTorch (Amazon Linux 2023)
    if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
        info "Buscando Deep Learning AMI PyTorch (Amazon Linux 2023)..."
        AMI_ID=$(aws ec2 describe-images \
            --region "$REGION" \
            --owners amazon \
            --filters \
                "Name=name,Values=Deep Learning AMI GPU PyTorch*Amazon Linux 2023*" \
                "Name=state,Values=available" \
                "Name=architecture,Values=x86_64" \
            --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
            --output text) || true
    fi

    # Prioridad 3: Deep Learning Base AMI con Amazon Linux 2 (GCC 7 — limitado)
    if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
        info "Buscando Deep Learning Base AMI (Amazon Linux 2)..."
        AMI_ID=$(aws ec2 describe-images \
            --region "$REGION" \
            --owners amazon \
            --filters \
                "Name=name,Values=Deep Learning Base OSS Nvidia Driver AMI (Amazon Linux 2)*" \
                "Name=state,Values=available" \
                "Name=architecture,Values=x86_64" \
            --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
            --output text) || true
    fi

    # Fallback final: Amazon Linux 2023 genérica (sin drivers NVIDIA preinstalados)
    if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
        info "ADVERTENCIA: No se encontró AMI con drivers NVIDIA. Usando Amazon Linux 2023 genérica."
        info "Será necesario instalar drivers NVIDIA manualmente con ec2_setup_gpu.sh."
        AMI_ID=$(aws ec2 describe-images \
            --region "$REGION" \
            --owners amazon \
            --filters \
                "Name=name,Values=al2023-ami-2023.*-x86_64" \
                "Name=state,Values=available" \
                "Name=architecture,Values=x86_64" \
            --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
            --output text) || error "No se pudo obtener ninguna AMI válida."
    fi

    if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
        error "No se encontró una AMI válida en la región $REGION."
    fi
    info "AMI seleccionada: $AMI_ID"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Crear o verificar Key Pair
# ─────────────────────────────────────────────────────────────────────────────
KEY_CREATED=false
if aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" > /dev/null 2>&1; then
    info "Key Pair '$KEY_NAME' ya existe en $REGION. Se usará el existente."
else
    info "Creando Key Pair '$KEY_NAME' en $REGION..."
    aws ec2 create-key-pair \
        --region "$REGION" \
        --key-name "$KEY_NAME" \
        --query 'KeyMaterial' \
        --output text > "${KEY_NAME}.pem" || error "No se pudo crear el Key Pair."

    chmod 400 "${KEY_NAME}.pem"
    KEY_CREATED=true
    info "Key Pair creado. Archivo privado guardado en: ${KEY_NAME}.pem"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Crear Security Group
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando si el Security Group '$SG_NAME' ya existe..."

SG_ID=$(aws ec2 describe-security-groups \
    --region "$REGION" \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null) || true

if [[ -n "$SG_ID" && "$SG_ID" != "None" ]]; then
    info "Security Group '$SG_NAME' ya existe: $SG_ID. Se reutilizará."
else
    info "Creando Security Group '$SG_NAME' en $REGION..."

    VPC_ID=$(aws ec2 describe-vpcs \
        --region "$REGION" \
        --filters "Name=isDefault,Values=true" \
        --query 'Vpcs[0].VpcId' \
        --output text) || error "No se pudo obtener la VPC por defecto."

    if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
        error "No se encontró una VPC por defecto en la región $REGION."
    fi

    SG_ID=$(aws ec2 create-security-group \
        --region "$REGION" \
        --group-name "$SG_NAME" \
        --description "$SG_DESCRIPTION" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' \
        --output text) || error "No se pudo crear el Security Group."

    info "Security Group creado: $SG_ID"

    # Regla SSH (puerto 22)
    info "Configurando regla SSH (puerto 22) para IP $MY_IP..."
    aws ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 22 \
        --cidr "${MY_IP}/32" || error "No se pudo agregar la regla SSH."

    # Regla Caddy (puerto 8080)
    info "Configurando regla para Caddy (puerto 8080) desde IP $MY_IP..."
    aws ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 8080 \
        --cidr "${MY_IP}/32" || error "No se pudo agregar la regla del puerto 8080."

    info "Puerto 11434 (Ollama) bloqueado desde el exterior — solo accesible vía Docker."

    # Etiquetar
    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$SG_ID" \
        --tags \
            Key=project,Value=velvet \
            Key=environment,Value=gpu-benchmark \
            Key=Name,Value="$SG_NAME" || info "Advertencia: no se pudieron etiquetar el Security Group."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Lanzar instancia EC2 (Spot o On-Demand)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$USE_SPOT" == true ]]; then
    info "Lanzando instancia EC2 SPOT ($INSTANCE_TYPE) en $REGION..."
    info "  Precio máximo Spot: \$${MAX_SPOT_PRICE}/hr"
    MARKET_OPTIONS="--instance-market-options {\"MarketType\":\"spot\",\"SpotOptions\":{\"MaxPrice\":\"${MAX_SPOT_PRICE}\",\"SpotInstanceType\":\"one-time\",\"InstanceInterruptionBehavior\":\"terminate\"}}"
else
    info "Lanzando instancia EC2 ON-DEMAND ($INSTANCE_TYPE) en $REGION..."
    MARKET_OPTIONS=""
fi

info "  AMI:           $AMI_ID"
info "  Tipo:          $INSTANCE_TYPE (8 vCPU, 32 GB RAM, 1x NVIDIA A10G 24 GB)"
info "  Key Pair:      $KEY_NAME"
info "  Security Group: $SG_ID"
info "  Volumen EBS:   ${VOLUME_SIZE} GB gp3"

# Construir comando de lanzamiento
LAUNCH_CMD="aws ec2 run-instances \
    --region $REGION \
    --image-id $AMI_ID \
    --instance-type $INSTANCE_TYPE \
    --key-name $KEY_NAME \
    --security-group-ids $SG_ID \
    --block-device-mappings [{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":$VOLUME_SIZE,\"VolumeType\":\"gp3\",\"Iops\":3000,\"Throughput\":125,\"DeleteOnTermination\":true}}] \
    --tag-specifications [{\"ResourceType\":\"instance\",\"Tags\":[{\"Key\":\"project\",\"Value\":\"velvet\"},{\"Key\":\"environment\",\"Value\":\"gpu-benchmark\"},{\"Key\":\"Name\",\"Value\":\"velvet-gpu-benchmark\"}]}] \
    --query Instances[0].InstanceId \
    --output text"

# Agregar opciones de Spot si corresponde
if [[ "$USE_SPOT" == true ]]; then
    LAUNCH_CMD="$LAUNCH_CMD \
    --instance-market-options {\"MarketType\":\"spot\",\"SpotOptions\":{\"MaxPrice\":\"${MAX_SPOT_PRICE}\",\"SpotInstanceType\":\"one-time\",\"InstanceInterruptionBehavior\":\"terminate\"}}"
fi

INSTANCE_ID=$(eval "$LAUNCH_CMD") || error "No se pudo lanzar la instancia EC2. Si es Spot, puede que no haya capacidad disponible. Intenta con --on-demand."

info "Instancia lanzada: $INSTANCE_ID"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 6: Esperar a que la instancia esté en estado 'running'
# ─────────────────────────────────────────────────────────────────────────────
info "Esperando a que la instancia $INSTANCE_ID esté en estado 'running'..."
aws ec2 wait instance-running \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" || error "Timeout esperando que la instancia entre en estado 'running'."

info "Instancia en estado 'running'."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 7: Esperar a que pasen los status checks
# ─────────────────────────────────────────────────────────────────────────────
info "Esperando a que la instancia $INSTANCE_ID pase los status checks..."
aws ec2 wait instance-status-ok \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" || error "Timeout esperando los status checks de la instancia."

info "Status checks completados exitosamente."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 8: Obtener IPs y mostrar resumen
# ─────────────────────────────────────────────────────────────────────────────
INSTANCE_INFO=$(aws ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].[PublicIpAddress, PrivateIpAddress, SpotInstanceRequestId]' \
    --output text) || error "No se pudo obtener la información de la instancia."

PUBLIC_IP=$(echo "$INSTANCE_INFO" | awk '{print $1}')
PRIVATE_IP=$(echo "$INSTANCE_INFO" | awk '{print $2}')
SPOT_REQUEST_ID=$(echo "$INSTANCE_INFO" | awk '{print $3}')

echo ""
echo "============================================================================="
echo " APROVISIONAMIENTO GPU COMPLETADO"
echo "============================================================================="
echo " Instance ID:     $INSTANCE_ID"
echo " IP Pública:      $PUBLIC_IP"
echo " IP Privada:      $PRIVATE_IP"
echo " Región:          $REGION"
echo " Tipo:            $INSTANCE_TYPE (8 vCPU, 32 GB RAM, 1x NVIDIA A10G 24 GB)"
if [[ "$USE_SPOT" == true ]]; then
echo " Modo:            SPOT (precio máx: \$${MAX_SPOT_PRICE}/hr)"
echo " Spot Request:    $SPOT_REQUEST_ID"
else
echo " Modo:            ON-DEMAND (\$1.212/hr)"
fi
echo " Security Group:  $SG_ID"
echo " Key Pair:        $KEY_NAME"
if [[ "$KEY_CREATED" == true ]]; then
echo " Archivo PEM:     ${KEY_NAME}.pem (NUEVO — guardar en lugar seguro)"
fi
echo " Volumen EBS:     ${VOLUME_SIZE} GB gp3"
echo " GPU:             NVIDIA A10G (24 GB GDDR6X, 600 GB/s bandwidth)"
echo "============================================================================="
echo ""
echo " ⚠️  IMPORTANTE (Spot Instance):"
echo "   - La instancia puede ser interrumpida con 2 min de aviso."
echo "   - Guarda resultados frecuentemente."
echo "   - Si se interrumpe, relanza con el mismo script."
echo ""
echo " Conectar por SSH:"
echo "   ssh -i ${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
echo ""
echo " Siguiente paso:"
echo "   ./ec2_setup_gpu.sh --instance-ip $PUBLIC_IP --key-file ${KEY_NAME}.pem --repo-url <URL_REPO>"
echo "============================================================================="
