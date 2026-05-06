#!/usr/bin/env bash
# =============================================================================
# ec2_launch_from_ami.sh — Relanzar instancia Spot GPU desde AMI personalizada
# =============================================================================
# Lanza una nueva instancia Spot g5.2xlarge usando la AMI creada previamente
# con ec2_create_ami.sh. La instancia arranca ya configurada con todo el
# software necesario (drivers NVIDIA, Docker, proyecto, modelos).
#
# Flujo típico:
#   - La instancia Spot anterior fue interrumpida o terminada
#   - Relanzas desde la AMI sin repetir el setup (~2 min vs ~15 min)
#   - Continúas los benchmarks donde los dejaste
#
# Uso:
#   ./ec2_launch_from_ami.sh --ami-id <ID> --key-name <nombre> [opciones]
#
# Parámetros:
#   --ami-id      (obligatorio) ID de la AMI personalizada
#   --key-name    (obligatorio) Nombre del Key Pair de AWS
#   --region      (opcional)    Región de AWS (default: us-east-2)
#   --my-ip       (opcional)    IP del operador para SSH (default: auto-detectar)
#   --on-demand   (opcional)    Lanzar como On-Demand en vez de Spot
#   --max-price   (opcional)    Precio máximo Spot (default: $0.50/hr)
#
# Ejemplo:
#   ./ec2_launch_from_ami.sh --ami-id ami-0abc123 --key-name velvet-key
#
# Requisitos:
#   - AWS CLI v2 configurado
#   - AMI creada previamente con ec2_create_ami.sh
#   - Key Pair existente en la región
#   - Security Group velvet-gpu-benchmark-sg existente (creado por ec2_provision_gpu.sh)
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
VOLUME_SIZE=250
SG_NAME="velvet-gpu-benchmark-sg"
USE_SPOT=true
MAX_SPOT_PRICE="0.50"

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
Uso: $(basename "$0") --ami-id <ID> --key-name <nombre> [opciones]

Parámetros:
  --ami-id      (obligatorio) ID de la AMI personalizada
  --key-name    (obligatorio) Nombre del Key Pair de AWS
  --region      (opcional)    Región de AWS (default: us-east-2)
  --my-ip       (opcional)    IP del operador para SSH (default: auto-detectar)
  --on-demand   (opcional)    Lanzar como On-Demand (default: Spot)
  --max-price   (opcional)    Precio máximo Spot en USD/hr (default: 0.50)
  -h, --help                  Mostrar esta ayuda

Ejemplo:
  $(basename "$0") --ami-id ami-0abc123def --key-name velvet-key
EOF
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Parseo de argumentos
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ami-id)
            AMI_ID="$2"
            shift 2
            ;;
        --key-name)
            KEY_NAME="$2"
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

# Validar parámetros obligatorios
[[ -z "$AMI_ID" ]]   && error "El parámetro --ami-id es obligatorio."
[[ -z "$KEY_NAME" ]] && error "El parámetro --key-name es obligatorio."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Detectar IP del operador
# ─────────────────────────────────────────────────────────────────────────────
if [[ -z "$MY_IP" ]]; then
    info "Detectando IP pública del operador..."
    MY_IP=$(curl -s https://checkip.amazonaws.com) || error "No se pudo detectar la IP pública."
    MY_IP=$(echo "$MY_IP" | tr -d '[:space:]')
    info "IP detectada: $MY_IP"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Verificar que la AMI existe
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando AMI $AMI_ID en $REGION..."
AMI_STATE=$(aws ec2 describe-images \
    --region "$REGION" \
    --image-ids "$AMI_ID" \
    --query 'Images[0].State' \
    --output text 2>/dev/null) || error "No se encontró la AMI $AMI_ID en $REGION."

if [[ "$AMI_STATE" != "available" ]]; then
    error "La AMI está en estado '$AMI_STATE'. Debe estar 'available'."
fi
info "AMI verificada: $AMI_ID (disponible)"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Buscar Security Group existente o crear uno nuevo
# ─────────────────────────────────────────────────────────────────────────────
info "Buscando Security Group '$SG_NAME'..."
SG_ID=$(aws ec2 describe-security-groups \
    --region "$REGION" \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null) || true

if [[ -z "$SG_ID" || "$SG_ID" == "None" ]]; then
    info "Security Group no encontrado. Creando uno nuevo..."

    VPC_ID=$(aws ec2 describe-vpcs \
        --region "$REGION" \
        --filters "Name=isDefault,Values=true" \
        --query 'Vpcs[0].VpcId' \
        --output text) || error "No se pudo obtener la VPC por defecto."

    SG_ID=$(aws ec2 create-security-group \
        --region "$REGION" \
        --group-name "$SG_NAME" \
        --description "Security Group para benchmarks GPU de modelos LLM" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' \
        --output text) || error "No se pudo crear el Security Group."

    # SSH
    aws ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 22 \
        --cidr "${MY_IP}/32" || true

    # Caddy
    aws ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 8080 \
        --cidr "${MY_IP}/32" || true

    aws ec2 create-tags \
        --region "$REGION" \
        --resources "$SG_ID" \
        --tags \
            Key=project,Value=velvet \
            Key=environment,Value=gpu-benchmark \
            Key=Name,Value="$SG_NAME" || true

    info "Security Group creado: $SG_ID"
else
    info "Security Group encontrado: $SG_ID"

    # Actualizar regla SSH con la IP actual (puede haber cambiado)
    info "Actualizando regla SSH con IP actual ($MY_IP)..."
    # Intentar agregar la regla (falla silenciosamente si ya existe)
    aws ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 22 \
        --cidr "${MY_IP}/32" 2>/dev/null || info "  Regla SSH ya existe o IP ya autorizada."

    aws ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 8080 \
        --cidr "${MY_IP}/32" 2>/dev/null || info "  Regla 8080 ya existe o IP ya autorizada."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Lanzar instancia desde AMI
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$USE_SPOT" == true ]]; then
    info "Lanzando instancia SPOT $INSTANCE_TYPE desde AMI $AMI_ID..."
    info "  Precio máximo Spot: \$${MAX_SPOT_PRICE}/hr"
else
    info "Lanzando instancia ON-DEMAND $INSTANCE_TYPE desde AMI $AMI_ID..."
fi

# Construir comando
LAUNCH_CMD="aws ec2 run-instances \
    --region $REGION \
    --image-id $AMI_ID \
    --instance-type $INSTANCE_TYPE \
    --key-name $KEY_NAME \
    --security-group-ids $SG_ID \
    --block-device-mappings [{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":$VOLUME_SIZE,\"VolumeType\":\"gp3\",\"Iops\":3000,\"Throughput\":125,\"DeleteOnTermination\":true}}] \
    --tag-specifications [{\"ResourceType\":\"instance\",\"Tags\":[{\"Key\":\"project\",\"Value\":\"velvet\"},{\"Key\":\"environment\",\"Value\":\"gpu-benchmark\"},{\"Key\":\"Name\",\"Value\":\"velvet-gpu-benchmark-relaunch\"},{\"Key\":\"source-ami\",\"Value\":\"$AMI_ID\"}]}] \
    --query Instances[0].InstanceId \
    --output text"

if [[ "$USE_SPOT" == true ]]; then
    LAUNCH_CMD="$LAUNCH_CMD \
    --instance-market-options {\"MarketType\":\"spot\",\"SpotOptions\":{\"MaxPrice\":\"${MAX_SPOT_PRICE}\",\"SpotInstanceType\":\"one-time\",\"InstanceInterruptionBehavior\":\"terminate\"}}"
fi

INSTANCE_ID=$(eval "$LAUNCH_CMD") || error "No se pudo lanzar la instancia. Si es Spot, puede que no haya capacidad. Intenta con --on-demand."

info "Instancia lanzada: $INSTANCE_ID"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Esperar a que esté lista
# ─────────────────────────────────────────────────────────────────────────────
info "Esperando a que la instancia esté en estado 'running'..."
aws ec2 wait instance-running \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" || error "Timeout esperando estado 'running'."

info "Esperando status checks..."
aws ec2 wait instance-status-ok \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" || error "Timeout en status checks."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 6: Obtener IP y mostrar resumen
# ─────────────────────────────────────────────────────────────────────────────
PUBLIC_IP=$(aws ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text) || error "No se pudo obtener la IP pública."

echo ""
echo "============================================================================="
echo " INSTANCIA GPU RELANZADA DESDE AMI"
echo "============================================================================="
echo " Instance ID:     $INSTANCE_ID"
echo " IP Pública:      $PUBLIC_IP"
echo " Región:          $REGION"
echo " Tipo:            $INSTANCE_TYPE (8 vCPU, 32 GB RAM, 1x NVIDIA A10G 24 GB)"
echo " AMI origen:      $AMI_ID"
if [[ "$USE_SPOT" == true ]]; then
echo " Modo:            SPOT (precio máx: \$${MAX_SPOT_PRICE}/hr)"
else
echo " Modo:            ON-DEMAND (\$1.212/hr)"
fi
echo " Security Group:  $SG_ID"
echo "============================================================================="
echo ""
echo " La instancia ya tiene todo configurado (desde la AMI):"
echo "   ✓ Drivers NVIDIA + CUDA"
echo "   ✓ Docker + NVIDIA Container Toolkit"
echo "   ✓ Repositorio del proyecto"
echo "   ✓ Modelos previamente descargados"
echo ""
echo " Conectar y arrancar:"
echo "   ssh -i ${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
echo "   cd ~/velvet-project/infra"
echo "   docker compose -f docker-compose-gpu.yml up -d"
echo ""
echo " Verificar GPU:"
echo "   nvidia-smi"
echo "   docker exec velvet-ollama-gpu nvidia-smi"
echo "============================================================================="
