#!/usr/bin/env bash
# =============================================================================
# ec2_provision.sh — Aprovisionamiento de instancia EC2 para Velvet On-Premise
# =============================================================================
# Lanza una instancia EC2 compute-optimized (c6i.8xlarge — 32 vCPU, 64 GB RAM,
# sin GPU) configurada para ejecutar los modelos Velvet con Docker + Ollama.
#
# Uso:
#   ./ec2_provision.sh --key-name mi-clave [opciones]
#
# Parámetros:
#   --key-name    (obligatorio) Nombre del Key Pair de AWS para acceso SSH.
#                 Si no existe, el script lo crea y guarda el archivo .pem.
#   --ami-id      (opcional)    ID de la AMI. Por defecto: última Amazon Linux 2023.
#   --region      (opcional)    Región de AWS. Por defecto: us-east-1.
#   --my-ip       (opcional)    IP del operador para reglas SSH. Si no se
#                               proporciona, se detecta automáticamente.
#
# Ejemplo:
#   ./ec2_provision.sh --key-name velvet-key --region us-east-1
#
# Requisitos:
#   - AWS CLI v2 instalado y configurado (aws configure)
#   - Permisos IAM: ec2:RunInstances, ec2:CreateSecurityGroup,
#     ec2:AuthorizeSecurityGroupIngress, ec2:CreateKeyPair,
#     ec2:DescribeInstances, ec2:CreateTags, ec2:DescribeImages,
#     ec2:DescribeKeyPairs
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
REGION="us-east-1"
AMI_ID=""
KEY_NAME=""
MY_IP=""
INSTANCE_TYPE="c6i.8xlarge"
VOLUME_SIZE=100          # GB — almacenamiento gp3 para modelos GGUF y Docker
SG_NAME="velvet-onpremise-sg"
SG_DESCRIPTION="Security Group para simulación on-premise de Velvet"

# ─────────────────────────────────────────────────────────────────────────────
# Funciones auxiliares
# ─────────────────────────────────────────────────────────────────────────────

# Imprime un mensaje informativo con marca de tiempo
info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $*"
}

# Imprime un mensaje de error y termina con código 1
error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
    exit 1
}

# Muestra la ayuda del script
usage() {
    cat <<EOF
Uso: $(basename "$0") --key-name <nombre> [opciones]

Parámetros:
  --key-name    (obligatorio) Nombre del Key Pair de AWS para acceso SSH
  --ami-id      (opcional)    ID de la AMI (default: última Amazon Linux 2023)
  --region      (opcional)    Región de AWS (default: us-east-1)
  --my-ip       (opcional)    IP del operador para SSH (default: auto-detectar)
  -h, --help                  Mostrar esta ayuda

Ejemplo:
  $(basename "$0") --key-name velvet-key --region us-east-1
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
# Paso 2: Obtener AMI de Amazon Linux 2023 (si no se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -z "$AMI_ID" ]]; then
    info "Buscando la última AMI de Amazon Linux 2023 en $REGION..."
    AMI_ID=$(aws ec2 describe-images \
        --region "$REGION" \
        --owners amazon \
        --filters \
            "Name=name,Values=al2023-ami-2023.*-x86_64" \
            "Name=state,Values=available" \
            "Name=architecture,Values=x86_64" \
        --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
        --output text) || error "No se pudo obtener la AMI de Amazon Linux 2023."

    if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
        error "No se encontró una AMI de Amazon Linux 2023 en la región $REGION."
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
info "Creando Security Group '$SG_NAME' en $REGION..."

# Obtener VPC por defecto
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
    --output text) || error "No se pudo crear el Security Group. ¿Ya existe uno con el nombre '$SG_NAME'?"

info "Security Group creado: $SG_ID"

# Regla 1: SSH (puerto 22) solo desde la IP del operador
info "Configurando regla SSH (puerto 22) para IP $MY_IP..."
aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp \
    --port 22 \
    --cidr "${MY_IP}/32" || error "No se pudo agregar la regla SSH al Security Group."

# Regla 2: Puerto 8080 (Caddy reverse proxy) desde la IP del operador
# En producción, se puede ampliar a un rango de IPs autorizadas
info "Configurando regla para Caddy (puerto 8080) desde IP $MY_IP..."
aws ec2 authorize-security-group-ingress \
    --region "$REGION" \
    --group-id "$SG_ID" \
    --protocol tcp \
    --port 8080 \
    --cidr "${MY_IP}/32" || error "No se pudo agregar la regla del puerto 8080 al Security Group."

# NOTA: El puerto 11434 (Ollama) NO se abre al exterior.
# Solo es accesible dentro de la red interna de Docker en la instancia.
info "Puerto 11434 (Ollama) bloqueado desde el exterior — solo accesible vía Docker."

# Etiquetar el Security Group
aws ec2 create-tags \
    --region "$REGION" \
    --resources "$SG_ID" \
    --tags \
        Key=project,Value=velvet \
        Key=environment,Value=stress-test \
        Key=Name,Value="$SG_NAME" || info "Advertencia: no se pudieron etiquetar el Security Group."

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Lanzar instancia EC2
# ─────────────────────────────────────────────────────────────────────────────
info "Lanzando instancia EC2 ($INSTANCE_TYPE) en $REGION..."
info "  AMI:           $AMI_ID"
info "  Tipo:          $INSTANCE_TYPE (32 vCPU, 64 GB RAM, sin GPU)"
info "  Key Pair:      $KEY_NAME"
info "  Security Group: $SG_ID"
info "  Volumen EBS:   ${VOLUME_SIZE} GB gp3"

INSTANCE_ID=$(aws ec2 run-instances \
    --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --block-device-mappings "[{
        \"DeviceName\": \"/dev/xvda\",
        \"Ebs\": {
            \"VolumeSize\": $VOLUME_SIZE,
            \"VolumeType\": \"gp3\",
            \"DeleteOnTermination\": true
        }
    }]" \
    --tag-specifications "[{
        \"ResourceType\": \"instance\",
        \"Tags\": [
            {\"Key\": \"project\", \"Value\": \"velvet\"},
            {\"Key\": \"environment\", \"Value\": \"stress-test\"},
            {\"Key\": \"Name\", \"Value\": \"velvet-onpremise-sim\"}
        ]
    }]" \
    --query 'Instances[0].InstanceId' \
    --output text) || error "No se pudo lanzar la instancia EC2."

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
    --query 'Reservations[0].Instances[0].[PublicIpAddress, PrivateIpAddress]' \
    --output text) || error "No se pudo obtener la información de la instancia."

PUBLIC_IP=$(echo "$INSTANCE_INFO" | awk '{print $1}')
PRIVATE_IP=$(echo "$INSTANCE_INFO" | awk '{print $2}')

echo ""
echo "============================================================================="
echo " APROVISIONAMIENTO COMPLETADO"
echo "============================================================================="
echo " Instance ID:     $INSTANCE_ID"
echo " IP Pública:      $PUBLIC_IP"
echo " IP Privada:      $PRIVATE_IP"
echo " Región:          $REGION"
echo " Tipo:            $INSTANCE_TYPE"
echo " Security Group:  $SG_ID"
echo " Key Pair:        $KEY_NAME"
if [[ "$KEY_CREATED" == true ]]; then
echo " Archivo PEM:     ${KEY_NAME}.pem (NUEVO — guardar en lugar seguro)"
fi
echo " Volumen EBS:     ${VOLUME_SIZE} GB gp3"
echo "============================================================================="
echo ""
echo " Conectar por SSH:"
echo "   ssh -i ${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
echo ""
echo " Siguiente paso:"
echo "   ./ec2_setup.sh --instance-ip $PUBLIC_IP --key-file ${KEY_NAME}.pem --repo-url <URL_REPO>"
echo "============================================================================="
