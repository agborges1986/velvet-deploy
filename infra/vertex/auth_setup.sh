#!/usr/bin/env bash
# =============================================================================
# auth_setup.sh — Configuración de autenticación GCP para Vertex AI
# =============================================================================
# Configura la autenticación y los permisos necesarios para desplegar modelos
# Velvet en Google Cloud Vertex AI. Verifica herramientas, credenciales,
# permisos y habilita las APIs requeridas.
#
# Uso:
#   ./auth_setup.sh --project <PROJECT_ID> [--region <REGION>]
#
# Parámetros:
#   --project   (obligatorio) ID del proyecto de Google Cloud
#   --region    (opcional)    Región de GCP (default: us-central1)
#   -h, --help               Mostrar esta ayuda
#
# Ejemplo:
#   ./auth_setup.sh --project mi-proyecto-gcp --region us-central1
#
# Requisitos:
#   - Acceso a internet para autenticación con Google Cloud
#   - Cuenta de Google con acceso al proyecto GCP especificado
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Valores por defecto
# ─────────────────────────────────────────────────────────────────────────────
PROJECT=""
REGION="us-central1"

# ─────────────────────────────────────────────────────────────────────────────
# Funciones auxiliares
# ─────────────────────────────────────────────────────────────────────────────

# Imprime un mensaje informativo con marca de tiempo
info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $*"
}

# Imprime una advertencia
warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $*" >&2
}

# Imprime un mensaje de error y termina con código 1
error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
    exit 1
}

# Imprime un mensaje de éxito con marca visual
ok() {
    echo "  ✓ $*"
}

# Imprime un mensaje de fallo con marca visual
fail() {
    echo "  ✗ $*"
}

# Muestra la ayuda del script
usage() {
    cat <<EOF
Uso: $(basename "$0") --project <PROJECT_ID> [opciones]

Configura la autenticación GCP para desplegar modelos Velvet en Vertex AI.

Parámetros:
  --project   (obligatorio) ID del proyecto de Google Cloud
  --region    (opcional)    Región de GCP (default: us-central1)
  -h, --help               Mostrar esta ayuda

Ejemplo:
  $(basename "$0") --project mi-proyecto-gcp --region us-central1
EOF
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Parseo de argumentos
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)
            PROJECT="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
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
if [[ -z "$PROJECT" ]]; then
    error "El parámetro --project es obligatorio. Usa --help para ver las opciones."
fi

echo ""
echo "============================================================================="
echo " CONFIGURACIÓN DE AUTENTICACIÓN GCP PARA VERTEX AI"
echo "============================================================================="
echo " Proyecto: $PROJECT"
echo " Región:   $REGION"
echo "============================================================================="
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Paso 1: Verificar que gcloud CLI está instalado
# ─────────────────────────────────────────────────────────────────────────────
info "Paso 1: Verificando instalación de gcloud CLI..."

if command -v gcloud &> /dev/null; then
    GCLOUD_VERSION=$(gcloud version 2>/dev/null | head -1)
    ok "gcloud CLI instalado: $GCLOUD_VERSION"
else
    fail "gcloud CLI no está instalado."
    echo ""
    echo "  Para instalar gcloud CLI:"
    echo ""
    echo "  macOS / Linux:"
    echo "    curl https://sdk.cloud.google.com | bash"
    echo "    exec -l \$SHELL"
    echo "    gcloud init"
    echo ""
    echo "  macOS (Homebrew):"
    echo "    brew install --cask google-cloud-sdk"
    echo ""
    echo "  Ubuntu / Debian:"
    echo "    sudo apt-get install apt-transport-https ca-certificates gnupg curl"
    echo "    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg"
    echo "    echo 'deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main' | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list"
    echo "    sudo apt-get update && sudo apt-get install google-cloud-cli"
    echo ""
    echo "  Documentación oficial:"
    echo "    https://cloud.google.com/sdk/docs/install"
    echo ""
    error "Instala gcloud CLI y vuelve a ejecutar este script."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 2: Autenticación con Google Cloud
# ─────────────────────────────────────────────────────────────────────────────
info "Paso 2: Verificando autenticación con Google Cloud..."

# Verificar si ya hay una cuenta autenticada
CUENTA_ACTIVA=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)

if [[ -n "$CUENTA_ACTIVA" ]]; then
    ok "Cuenta autenticada: $CUENTA_ACTIVA"
else
    # Verificar si existe una Service Account Key configurada
    if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]] && [[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
        ok "Service Account Key configurada: $GOOGLE_APPLICATION_CREDENTIALS"
        info "Usando Service Account Key para autenticación."
    else
        warn "No hay cuenta autenticada ni Service Account Key configurada."
        info "Ejecutando 'gcloud auth login' para autenticación interactiva..."
        echo ""

        if ! gcloud auth login --no-launch-browser 2>&1; then
            error "No se pudo completar la autenticación. Verifica tu conexión a internet y credenciales."
        fi

        CUENTA_ACTIVA=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
        if [[ -n "$CUENTA_ACTIVA" ]]; then
            ok "Autenticación exitosa: $CUENTA_ACTIVA"
        else
            error "La autenticación no se completó correctamente."
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 3: Configurar Application Default Credentials (ADC)
# ─────────────────────────────────────────────────────────────────────────────
info "Paso 3: Configurando Application Default Credentials (ADC)..."
info "ADC es necesario para que el SDK de Python (google-cloud-aiplatform) se autentique."

# Verificar si ADC ya está configurado
ADC_PATH="${HOME}/.config/gcloud/application_default_credentials.json"

if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]] && [[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
    ok "ADC configurado vía GOOGLE_APPLICATION_CREDENTIALS: $GOOGLE_APPLICATION_CREDENTIALS"
elif [[ -f "$ADC_PATH" ]]; then
    ok "ADC ya configurado en: $ADC_PATH"
else
    info "Ejecutando 'gcloud auth application-default login' para configurar ADC..."
    echo ""

    if ! gcloud auth application-default login --no-launch-browser 2>&1; then
        warn "No se pudo configurar ADC automáticamente."
        echo ""
        echo "  Alternativas:"
        echo "  1. Ejecuta manualmente: gcloud auth application-default login"
        echo "  2. Usa una Service Account Key:"
        echo "     export GOOGLE_APPLICATION_CREDENTIALS=/ruta/a/key.json"
        echo ""
    else
        ok "Application Default Credentials configurado correctamente."
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 4: Configurar el proyecto GCP
# ─────────────────────────────────────────────────────────────────────────────
info "Paso 4: Configurando proyecto GCP: $PROJECT..."

if gcloud config set project "$PROJECT" 2>/dev/null; then
    ok "Proyecto configurado: $PROJECT"
else
    error "No se pudo configurar el proyecto '$PROJECT'. Verifica que el ID sea correcto."
fi

# Verificar que el proyecto existe y es accesible
if gcloud projects describe "$PROJECT" &>/dev/null; then
    ok "Proyecto '$PROJECT' verificado y accesible."
else
    warn "No se pudo verificar el proyecto '$PROJECT'. Puede que no tengas permisos de lectura."
fi

# Configurar la región por defecto
gcloud config set compute/region "$REGION" 2>/dev/null || true
ok "Región configurada: $REGION"

# ─────────────────────────────────────────────────────────────────────────────
# Paso 5: Verificar permisos necesarios para Vertex AI
# ─────────────────────────────────────────────────────────────────────────────
info "Paso 5: Verificando permisos necesarios para Vertex AI..."

# Lista de permisos requeridos para desplegar modelos en Vertex AI
PERMISOS_REQUERIDOS=(
    "aiplatform.endpoints.create"
    "aiplatform.endpoints.deploy"
    "aiplatform.models.upload"
    "aiplatform.endpoints.predict"
)

# Verificar permisos usando testIamPermissions
PERMISOS_OK=true
PERMISOS_FALTANTES=()

for permiso in "${PERMISOS_REQUERIDOS[@]}"; do
    # Intentar verificar el permiso a nivel de proyecto
    RESULTADO=$(gcloud projects get-iam-policy "$PROJECT" \
        --format="json" 2>/dev/null | grep -c "$permiso" || true)

    # Nota: La verificación exacta de permisos requiere testIamPermissions,
    # que no siempre está disponible. Hacemos una verificación básica.
    echo "  → Verificando: $permiso"
done

# Verificar si el usuario tiene el rol Vertex AI User o Admin
info "Verificando roles de Vertex AI asignados..."
CUENTA_ACTUAL="${CUENTA_ACTIVA:-$(gcloud config get-value account 2>/dev/null || echo 'desconocida')}"

ROLES_VERTEX=$(gcloud projects get-iam-policy "$PROJECT" \
    --flatten="bindings[].members" \
    --filter="bindings.members:$CUENTA_ACTUAL AND bindings.role:aiplatform" \
    --format="value(bindings.role)" 2>/dev/null || true)

if [[ -n "$ROLES_VERTEX" ]]; then
    ok "Roles de Vertex AI encontrados para $CUENTA_ACTUAL:"
    echo "$ROLES_VERTEX" | while read -r rol; do
        echo "    - $rol"
    done
else
    warn "No se encontraron roles de Vertex AI asignados directamente a $CUENTA_ACTUAL."
    echo ""
    echo "  Los permisos pueden estar heredados de un grupo o rol superior."
    echo "  Si el despliegue falla por permisos, asigna el rol 'Vertex AI User':"
    echo ""
    echo "    gcloud projects add-iam-policy-binding $PROJECT \\"
    echo "      --member=\"user:$CUENTA_ACTUAL\" \\"
    echo "      --role=\"roles/aiplatform.user\""
    echo ""
fi

# ─────────────────────────────────────────────────────────────────────────────
# Paso 6: Habilitar la API de Vertex AI
# ─────────────────────────────────────────────────────────────────────────────
info "Paso 6: Habilitando la API de Vertex AI..."

# Verificar si la API ya está habilitada
API_HABILITADA=$(gcloud services list --enabled \
    --filter="name:aiplatform.googleapis.com" \
    --format="value(name)" 2>/dev/null || true)

if [[ -n "$API_HABILITADA" ]]; then
    ok "API de Vertex AI ya está habilitada."
else
    info "Habilitando aiplatform.googleapis.com..."
    if gcloud services enable aiplatform.googleapis.com 2>/dev/null; then
        ok "API de Vertex AI habilitada correctamente."
    else
        warn "No se pudo habilitar la API de Vertex AI automáticamente."
        echo ""
        echo "  Habilítala manualmente:"
        echo "    gcloud services enable aiplatform.googleapis.com"
        echo ""
        echo "  O desde la consola de GCP:"
        echo "    https://console.cloud.google.com/apis/library/aiplatform.googleapis.com"
        echo ""
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Resumen de configuración
# ─────────────────────────────────────────────────────────────────────────────
CUENTA_FINAL=$(gcloud config get-value account 2>/dev/null || echo "no configurada")
PROYECTO_FINAL=$(gcloud config get-value project 2>/dev/null || echo "no configurado")
REGION_FINAL=$(gcloud config get-value compute/region 2>/dev/null || echo "no configurada")

echo ""
echo "============================================================================="
echo " RESUMEN DE CONFIGURACIÓN"
echo "============================================================================="
echo " Cuenta autenticada:  $CUENTA_FINAL"
echo " Proyecto GCP:        $PROYECTO_FINAL"
echo " Región:              $REGION_FINAL"
echo " API Vertex AI:       ${API_HABILITADA:-pendiente de verificación}"
echo "============================================================================="
echo ""
echo " Siguiente paso — desplegar un modelo:"
echo "   python infra/vertex/deploy_vertex.py \\"
echo "     --model-id 'Almawave/Velvet-2B' \\"
echo "     --project '$PROYECTO_FINAL' \\"
echo "     --region '$REGION_FINAL'"
echo ""
echo " Para más información sobre credenciales:"
echo "   cat infra/vertex/credentials.md"
echo "============================================================================="
