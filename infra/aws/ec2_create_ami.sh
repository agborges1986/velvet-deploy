#!/usr/bin/env bash
# =============================================================================
# ec2_create_ami.sh — Crear AMI desde instancia GPU configurada
# =============================================================================
# Crea una AMI (Amazon Machine Image) a partir de la instancia EC2 GPU ya
# configurada con drivers NVIDIA, Docker, NVIDIA Container Toolkit y el
# repositorio del proyecto. Esto permite relanzar instancias Spot sin repetir
# el proceso de setup.
#
# Flujo recomendado:
#   1. Lanzar instancia con ec2_provision_gpu.sh
#   2. Configurar con ec2_setup_gpu.sh
#   3. (Opcional) Descargar modelos que usarás en los benchmarks
#   4. Ejecutar este script para crear la AMI
#   5. Terminar la instancia original
#   6. Relanzar desde la AMI con ec2_launch_from_ami.sh cuando necesites
#
# Uso:
#   ./ec2_create_ami.sh --instance-id <ID> [opciones]
#
# Parámetros:
#   --instance-id  (obligatorio) ID de la instancia EC2 a capturar
#   --ami-name     (opcional)    Nombre de la AMI (default: velvet-gpu-benchmark-<fecha>)
#   --region       (opcional)    Región de AWS (default: us-east-2)
#   --no-reboot    (opcional)    No reiniciar la instancia al crear la AMI
#                                (puede resultar en filesystem inconsistente)
#
# Ejemplo:
#   ./ec2_create_ami.sh --instance-id i-0abc123def456789
#   ./ec2_create_ami.sh --instance-id i-0abc123def456789 --no-reboot
#
# Requisitos:
#   - AWS CLI v2 configurado
#   - Permisos IAM: ec2:CreateImage, ec2:DescribeImages, ec2:CreateTags
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
REGION="us-east-2"
INSTANCE_ID=""
AMI_NAME=""
NO_REBOOT=false

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
Uso: $(basename "$0") --instance-id <ID> [opciones]

Parámetros:
  --instance-id  (obligatorio) ID de la instancia EC2 a capturar
  --ami-name     (opcional)    Nombre de la AMI (default: velvet-gpu-benchmark-<fecha>)
  --region       (opcional)    Región de AWS (default: us-east-2)
  --no-reboot    (opcional)    No reiniciar la instancia al crear la AMI
  -h, --help                   Mostrar esta ayuda

Ejemplo:
  $(basename "$0") --instance-id i-0abc123def456789
EOF
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Parseo de argumentos
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance-id)
            INSTANCE_ID="$2"
            shift 2
            ;;
        --ami-name)
            AMI_NAME="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --no-reboot)
            NO_REBOOT=true
            shift
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
if [[ -z "$INSTANCE_ID" ]]; then
    error "El parámetro --instance-id es obligatorio. Usa --help para ver las opciones."
fi

# Generar nombre de AMI si no se proporcionó
if [[ -z "$AMI_NAME" ]]; then
    AMI_NAME="velvet-gpu-benchmark-$(date '+%Y%m%d-%H%M%S')"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Verificar que la instancia existe y está corriendo
# ─────────────────────────────────────────────────────────────────────────────
info "Verificando instancia $INSTANCE_ID en $REGION..."

INSTANCE_STATE=$(aws ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text 2>/dev/null) || error "No se encontró la instancia $INSTANCE_ID en $REGION."

if [[ "$INSTANCE_STATE" != "running" && "$INSTANCE_STATE" != "stopped" ]]; then
    error "La instancia está en estado '$INSTANCE_STATE'. Debe estar 'running' o 'stopped'."
fi

info "Instancia encontrada en estado: $INSTANCE_STATE"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Crear la AMI
# ─────────────────────────────────────────────────────────────────────────────
REBOOT_FLAG=""
if [[ "$NO_REBOOT" == true ]]; then
    REBOOT_FLAG="--no-reboot"
    info "ADVERTENCIA: Creando AMI sin reiniciar. El filesystem puede quedar inconsistente."
fi

info "Creando AMI '$AMI_NAME' desde instancia $INSTANCE_ID..."
info "  Esto puede tomar varios minutos dependiendo del tamaño del volumen."

AMI_ID=$(aws ec2 create-image \
    --region "$REGION" \
    --instance-id "$INSTANCE_ID" \
    --name "$AMI_NAME" \
    --description "AMI para benchmarks GPU de LLMs con Ollama. Incluye: NVIDIA drivers, Docker, NVIDIA Container Toolkit, proyecto Velvet." \
    $REBOOT_FLAG \
    --query 'ImageId' \
    --output text) || error "No se pudo crear la AMI."

info "AMI en creación: $AMI_ID"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Etiquetar la AMI
# ─────────────────────────────────────────────────────────────────────────────
info "Etiquetando AMI..."
aws ec2 create-tags \
    --region "$REGION" \
    --resources "$AMI_ID" \
    --tags \
        Key=project,Value=velvet \
        Key=environment,Value=gpu-benchmark \
        Key=Name,Value="$AMI_NAME" \
        Key=instance-type,Value=g5.2xlarge \
        Key=gpu,Value="NVIDIA-A10G" \
        Key=source-instance,Value="$INSTANCE_ID" || info "Advertencia: no se pudieron etiquetar la AMI."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Esperar a que la AMI esté disponible
# ─────────────────────────────────────────────────────────────────────────────
info "Esperando a que la AMI $AMI_ID esté disponible..."
info "  (Esto puede tomar 5-15 minutos para un volumen de 250 GB)"

aws ec2 wait image-available \
    --region "$REGION" \
    --image-ids "$AMI_ID" || error "Timeout esperando que la AMI esté disponible."

info "AMI disponible y lista para usar."

# ─────────────────────────────────────────────────────────────────────────────
# Resumen
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================================="
echo " AMI CREADA EXITOSAMENTE"
echo "============================================================================="
echo " AMI ID:          $AMI_ID"
echo " Nombre:          $AMI_NAME"
echo " Región:          $REGION"
echo " Instancia origen: $INSTANCE_ID"
echo "============================================================================="
echo ""
echo " La AMI incluye:"
echo "   ✓ Drivers NVIDIA + CUDA"
echo "   ✓ Docker + Docker Compose"
echo "   ✓ NVIDIA Container Toolkit"
echo "   ✓ huggingface-cli"
echo "   ✓ Repositorio del proyecto"
echo "   ✓ Modelos descargados (si los descargaste antes de crear la AMI)"
echo ""
echo " Para lanzar una nueva instancia Spot desde esta AMI:"
echo "   ./ec2_launch_from_ami.sh --ami-id $AMI_ID --key-name <tu-key>"
echo ""
echo " IMPORTANTE: La AMI tiene un costo de almacenamiento (~\$0.05/GB/mes)."
echo "   Para 250 GB ≈ \$12.50/mes. Elimínala cuando ya no la necesites:"
echo "   aws ec2 deregister-image --region $REGION --image-id $AMI_ID"
echo "============================================================================="
