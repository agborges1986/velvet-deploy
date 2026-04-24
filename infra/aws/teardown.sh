#!/usr/bin/env bash
# =============================================================================
# teardown.sh — Limpieza de recursos AWS para Velvet On-Premise
# =============================================================================
# Elimina los recursos AWS creados por ec2_provision.sh:
#   - Termina la instancia EC2
#   - Elimina el Security Group asociado (opcional)
#   - Elimina el Key Pair (opcional)
#
# REQUIERE el flag --confirm para ejecutar la eliminación. Sin este flag,
# el script solo muestra un resumen de lo que se eliminaría (dry-run).
#
# Uso:
#   ./teardown.sh --instance-id <ID> [opciones] --confirm
#
# Parámetros:
#   --instance-id       (obligatorio) ID de la instancia EC2 a terminar
#   --security-group-id (opcional)    ID del Security Group a eliminar
#   --key-name          (opcional)    Nombre del Key Pair a eliminar
#   --region            (opcional)    Región de AWS (default: us-east-1)
#   --confirm                         Confirmar la eliminación de recursos
#
# Ejemplo:
#   ./teardown.sh \
#     --instance-id i-0abc123def456789 \
#     --security-group-id sg-0abc123def456789 \
#     --key-name velvet-key \
#     --region us-east-1 \
#     --confirm
#
# Requisitos:
#   - AWS CLI v2 instalado y configurado
#   - Permisos IAM: ec2:TerminateInstances, ec2:DeleteSecurityGroup,
#     ec2:DeleteKeyPair, ec2:DescribeInstances
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
REGION="us-east-1"
INSTANCE_ID=""
SECURITY_GROUP_ID=""
KEY_NAME=""
CONFIRM=false

# ─────────────────────────────────────────────────────────────────────────────
# Funciones auxiliares
# ─────────────────────────────────────────────────────────────────────────────

info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $*"
}

warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $*" >&2
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<EOF
Uso: $(basename "$0") --instance-id <ID> [opciones] --confirm

Parámetros:
  --instance-id       (obligatorio) ID de la instancia EC2 a terminar
  --security-group-id (opcional)    ID del Security Group a eliminar
  --key-name          (opcional)    Nombre del Key Pair a eliminar
  --region            (opcional)    Región de AWS (default: us-east-1)
  --confirm                         Confirmar la eliminación (sin este flag = dry-run)
  -h, --help                        Mostrar esta ayuda

Ejemplo:
  $(basename "$0") \\
    --instance-id i-0abc123def456789 \\
    --security-group-id sg-0abc123def456789 \\
    --key-name velvet-key \\
    --confirm
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
        --security-group-id)
            SECURITY_GROUP_ID="$2"
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
        --confirm)
            CONFIRM=true
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

# ─────────────────────────────────────────────────────────────────────────────
# Contadores para el resumen final
# ─────────────────────────────────────────────────────────────────────────────
RESOURCES_DELETED=0
RESOURCES_FAILED=0
SUMMARY=""

# ─────────────────────────────────────────────────────────────────────────────
# Mostrar resumen de recursos a eliminar
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================================="
echo " RECURSOS A ELIMINAR"
echo "============================================================================="
echo " Región:           $REGION"
echo " Instancia EC2:    $INSTANCE_ID"
if [[ -n "$SECURITY_GROUP_ID" ]]; then
echo " Security Group:   $SECURITY_GROUP_ID"
fi
if [[ -n "$KEY_NAME" ]]; then
echo " Key Pair:         $KEY_NAME"
fi
echo "============================================================================="
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Verificar flag --confirm
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$CONFIRM" != true ]]; then
    echo "⚠️  MODO DRY-RUN: No se eliminará ningún recurso."
    echo "   Para ejecutar la eliminación, agrega el flag --confirm"
    echo ""
    echo "   Ejemplo:"
    echo "   $(basename "$0") --instance-id $INSTANCE_ID"
    [[ -n "$SECURITY_GROUP_ID" ]] && echo "     --security-group-id $SECURITY_GROUP_ID"
    [[ -n "$KEY_NAME" ]] && echo "     --key-name $KEY_NAME"
    echo "     --region $REGION"
    echo "     --confirm"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Terminar la instancia EC2
# ─────────────────────────────────────────────────────────────────────────────
info "Terminando instancia EC2: $INSTANCE_ID..."

# Verificar que la instancia existe y obtener su estado actual
INSTANCE_STATE=$(aws ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text 2>/dev/null) || true

if [[ -z "$INSTANCE_STATE" || "$INSTANCE_STATE" == "None" ]]; then
    warn "La instancia $INSTANCE_ID no fue encontrada en la región $REGION."
    SUMMARY="${SUMMARY}\n  ✗ Instancia EC2 $INSTANCE_ID — no encontrada"
    RESOURCES_FAILED=$((RESOURCES_FAILED + 1))
elif [[ "$INSTANCE_STATE" == "terminated" ]]; then
    info "La instancia $INSTANCE_ID ya está terminada."
    SUMMARY="${SUMMARY}\n  ○ Instancia EC2 $INSTANCE_ID — ya terminada"
else
    info "Estado actual de la instancia: $INSTANCE_STATE"
    aws ec2 terminate-instances \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" > /dev/null 2>&1

    if [[ $? -eq 0 ]]; then
        info "Solicitud de terminación enviada. Esperando a que la instancia se termine..."
        aws ec2 wait instance-terminated \
            --region "$REGION" \
            --instance-ids "$INSTANCE_ID" 2>/dev/null || warn "Timeout esperando la terminación. La instancia puede tardar unos minutos más."

        info "Instancia $INSTANCE_ID terminada."
        SUMMARY="${SUMMARY}\n  ✓ Instancia EC2 $INSTANCE_ID — terminada"
        RESOURCES_DELETED=$((RESOURCES_DELETED + 1))
    else
        warn "No se pudo terminar la instancia $INSTANCE_ID."
        SUMMARY="${SUMMARY}\n  ✗ Instancia EC2 $INSTANCE_ID — error al terminar"
        RESOURCES_FAILED=$((RESOURCES_FAILED + 1))
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Eliminar Security Group (si se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "$SECURITY_GROUP_ID" ]]; then
    info "Eliminando Security Group: $SECURITY_GROUP_ID..."

    # Esperar un momento para que la instancia libere el Security Group
    # (las instancias terminadas pueden tardar en liberar las ENIs)
    info "Esperando 30 segundos para que la instancia libere el Security Group..."
    sleep 30

    # Intentar eliminar con reintentos (la instancia puede tardar en liberar)
    MAX_RETRIES=5
    RETRY_DELAY=15
    SG_DELETED=false

    for i in $(seq 1 $MAX_RETRIES); do
        if aws ec2 delete-security-group \
            --region "$REGION" \
            --group-id "$SECURITY_GROUP_ID" 2>/dev/null; then
            SG_DELETED=true
            break
        else
            if [[ $i -lt $MAX_RETRIES ]]; then
                info "Reintento $i/$MAX_RETRIES — esperando ${RETRY_DELAY}s para que se libere el Security Group..."
                sleep $RETRY_DELAY
            fi
        fi
    done

    if [[ "$SG_DELETED" == true ]]; then
        info "Security Group $SECURITY_GROUP_ID eliminado."
        SUMMARY="${SUMMARY}\n  ✓ Security Group $SECURITY_GROUP_ID — eliminado"
        RESOURCES_DELETED=$((RESOURCES_DELETED + 1))
    else
        warn "No se pudo eliminar el Security Group $SECURITY_GROUP_ID después de $MAX_RETRIES intentos."
        warn "Puede que aún esté asociado a la instancia. Inténtalo de nuevo en unos minutos."
        SUMMARY="${SUMMARY}\n  ✗ Security Group $SECURITY_GROUP_ID — error al eliminar"
        RESOURCES_FAILED=$((RESOURCES_FAILED + 1))
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Eliminar Key Pair (si se proporcionó)
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "$KEY_NAME" ]]; then
    info "Eliminando Key Pair: $KEY_NAME..."

    if aws ec2 delete-key-pair \
        --region "$REGION" \
        --key-name "$KEY_NAME" 2>/dev/null; then
        info "Key Pair '$KEY_NAME' eliminado."
        SUMMARY="${SUMMARY}\n  ✓ Key Pair '$KEY_NAME' — eliminado"
        RESOURCES_DELETED=$((RESOURCES_DELETED + 1))

        # Eliminar el archivo .pem local si existe
        if [[ -f "${KEY_NAME}.pem" ]]; then
            rm -f "${KEY_NAME}.pem"
            info "Archivo local ${KEY_NAME}.pem eliminado."
        fi
    else
        warn "No se pudo eliminar el Key Pair '$KEY_NAME'. Puede que no exista."
        SUMMARY="${SUMMARY}\n  ✗ Key Pair '$KEY_NAME' — error al eliminar"
        RESOURCES_FAILED=$((RESOURCES_FAILED + 1))
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Resumen final
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================================="
echo " RESUMEN DE LIMPIEZA"
echo "============================================================================="
echo " Región: $REGION"
echo ""
echo " Recursos procesados:"
echo -e "$SUMMARY"
echo ""
echo " Total eliminados: $RESOURCES_DELETED"
echo " Total con errores: $RESOURCES_FAILED"
echo "============================================================================="

# Código de salida: 0 si todo fue exitoso, 1 si hubo errores
if [[ $RESOURCES_FAILED -gt 0 ]]; then
    exit 1
fi
exit 0
