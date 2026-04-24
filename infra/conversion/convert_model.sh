#!/usr/bin/env bash
# =============================================================================
# convert_model.sh — Script principal de conversión HF → GGUF
#
# Descarga un modelo de Hugging Face, lo convierte al formato GGUF (F16)
# usando llama.cpp, aplica cuantización y verifica la integridad del archivo
# resultante ejecutando una inferencia de prueba con Ollama.
#
# Códigos de salida:
#   0 = Éxito
#   1 = Error de descarga o parámetros inválidos
#   2 = Error de conversión (HF→GGUF o cuantización)
#   3 = Error de verificación (inferencia de prueba fallida)
#
# Uso:
#   ./convert_model.sh \
#     --model "Almawave/Velvet-2B" \
#     --quantization "Q4_K_M" \
#     --output-dir "./models/"
#
# Cuantizaciones soportadas: Q4_0, Q4_K_M, Q5_K_M, Q8_0
# =============================================================================

set -euo pipefail

# ---- Colores para la salida por consola ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # Sin color

# ---- Funciones de logging ----

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') — $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') — $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') — $*"
}

# ---- Valores por defecto ----
MODEL=""
QUANTIZATION="Q4_K_M"
OUTPUT_DIR="./models"
VERSION="v1"

# Cuantizaciones válidas
VALID_QUANTIZATIONS=("Q4_0" "Q4_K_M" "Q5_K_M" "Q8_0")

# ---- Función de ayuda ----
mostrar_ayuda() {
    cat <<EOF
Uso: $(basename "$0") [opciones]

Convierte un modelo de Hugging Face al formato GGUF cuantizado para Ollama.

Opciones:
  --model         ID del repositorio en Hugging Face (ej: "Almawave/Velvet-2B") [requerido]
  --quantization  Nivel de cuantización (default: Q4_K_M)
                  Valores válidos: Q4_0, Q4_K_M, Q5_K_M, Q8_0
  --output-dir    Directorio de salida para el archivo GGUF (default: ./models)
  --version       Versión del modelo (default: v1)
  --help          Muestra esta ayuda

Ejemplos:
  $(basename "$0") --model "Almawave/Velvet-2B"
  $(basename "$0") --model "Almawave/Velvet-14B" --quantization Q8_0 --output-dir /opt/models
EOF
}

# ---- Parseo de argumentos ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            MODEL="$2"
            shift 2
            ;;
        --quantization)
            QUANTIZATION="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --help)
            mostrar_ayuda
            exit 0
            ;;
        *)
            log_error "Parámetro desconocido: $1"
            mostrar_ayuda
            exit 1
            ;;
    esac
done

# ---- Validación de parámetros ----

# Verificar que se proporcionó el modelo
if [[ -z "$MODEL" ]]; then
    log_error "El parámetro --model es obligatorio."
    mostrar_ayuda
    exit 1
fi

# Verificar que la cuantización es válida
cuantizacion_valida=false
for q in "${VALID_QUANTIZATIONS[@]}"; do
    if [[ "$q" == "$QUANTIZATION" ]]; then
        cuantizacion_valida=true
        break
    fi
done

if [[ "$cuantizacion_valida" == false ]]; then
    log_error "Cuantización no soportada: '$QUANTIZATION'"
    log_error "Cuantizaciones válidas: ${VALID_QUANTIZATIONS[*]}"
    exit 1
fi

# ---- Extraer el tamaño del modelo desde el nombre ----
# Busca patrones como "2B", "14B", "7b", "70b" en el nombre del modelo
extraer_tamano() {
    local modelo="$1"
    # Extraer el componente de tamaño (ej: "2B", "14B") del nombre del modelo
    local tamano
    tamano=$(echo "$modelo" | grep -oiE '[0-9]+[bB]' | tail -1 | tr '[:upper:]' '[:lower:]')
    if [[ -z "$tamano" ]]; then
        log_error "No se pudo extraer el tamaño del modelo desde el nombre: '$modelo'"
        log_error "El nombre del modelo debe contener un indicador de tamaño (ej: 2B, 14B)"
        exit 1
    fi
    echo "$tamano"
}

TAMANO=$(extraer_tamano "$MODEL")
log_info "Tamaño del modelo detectado: ${TAMANO}"

# ---- Construir nombres de archivos según convención ----
# Patrón: velvet-{tamaño}-cpu-{versión}-{cuantización}.gguf
NOMBRE_BASE="velvet-${TAMANO}-cpu-${VERSION}"
NOMBRE_GGUF_F16="${NOMBRE_BASE}-F16.gguf"
NOMBRE_GGUF_FINAL="${NOMBRE_BASE}-${QUANTIZATION}.gguf"

# Crear directorio de salida si no existe
mkdir -p "$OUTPUT_DIR"

# Directorio temporal para la descarga del modelo
DIR_DESCARGA=$(mktemp -d)
trap 'rm -rf "$DIR_DESCARGA"' EXIT

log_info "============================================"
log_info "Configuración de conversión:"
log_info "  Modelo HF:       $MODEL"
log_info "  Cuantización:    $QUANTIZATION"
log_info "  Versión:         $VERSION"
log_info "  Tamaño:          $TAMANO"
log_info "  Directorio:      $OUTPUT_DIR"
log_info "  Archivo final:   $NOMBRE_GGUF_FINAL"
log_info "============================================"

# =============================================================================
# PASO 1: Descargar el modelo desde Hugging Face
# =============================================================================
log_info "Paso 1/4 — Descargando modelo desde Hugging Face: $MODEL"

if ! command -v huggingface-cli &>/dev/null; then
    log_error "huggingface-cli no está instalado."
    log_error "Instálalo con: pip install huggingface_hub[cli]"
    exit 1
fi

if ! huggingface-cli download "$MODEL" --local-dir "$DIR_DESCARGA/model" 2>&1; then
    log_error "Fallo al descargar el modelo '$MODEL' desde Hugging Face."
    log_error "Verifica que el ID del modelo es correcto y que tienes acceso (HF_TOKEN si es gated)."
    exit 1
fi

log_info "Modelo descargado exitosamente en: $DIR_DESCARGA/model"

# =============================================================================
# PASO 2: Convertir de formato HF a GGUF (F16)
# =============================================================================
log_info "Paso 2/4 — Convirtiendo modelo HF a GGUF (F16)"

# Verificar que convert_hf_to_gguf.py está disponible
CONVERT_SCRIPT=""
if [[ -n "${LLAMA_CPP_DIR:-}" ]] && [[ -f "${LLAMA_CPP_DIR}/convert_hf_to_gguf.py" ]]; then
    CONVERT_SCRIPT="${LLAMA_CPP_DIR}/convert_hf_to_gguf.py"
elif command -v convert_hf_to_gguf.py &>/dev/null; then
    CONVERT_SCRIPT="convert_hf_to_gguf.py"
elif [[ -f "./llama.cpp/convert_hf_to_gguf.py" ]]; then
    CONVERT_SCRIPT="./llama.cpp/convert_hf_to_gguf.py"
else
    log_error "No se encontró convert_hf_to_gguf.py de llama.cpp."
    log_error "Opciones:"
    log_error "  1. Establece la variable LLAMA_CPP_DIR apuntando al directorio de llama.cpp"
    log_error "  2. Clona llama.cpp en el directorio actual: git clone https://github.com/ggerganov/llama.cpp"
    log_error "  3. Asegúrate de que convert_hf_to_gguf.py esté en el PATH"
    exit 2
fi

RUTA_GGUF_F16="${OUTPUT_DIR}/${NOMBRE_GGUF_F16}"

if ! python3 "$CONVERT_SCRIPT" \
    "$DIR_DESCARGA/model" \
    --outfile "$RUTA_GGUF_F16" \
    --outtype f16 2>&1; then
    log_error "Fallo en la conversión HF → GGUF (F16)."
    log_error "Verifica que el modelo descargado tiene un formato compatible con llama.cpp."
    # Limpiar archivos parciales
    rm -f "$RUTA_GGUF_F16"
    exit 2
fi

log_info "Conversión a GGUF F16 completada: $RUTA_GGUF_F16"

# =============================================================================
# PASO 3: Aplicar cuantización
# =============================================================================
log_info "Paso 3/4 — Aplicando cuantización ${QUANTIZATION}"

# Verificar que llama-quantize está disponible
QUANTIZE_BIN=""
if [[ -n "${LLAMA_CPP_DIR:-}" ]] && [[ -f "${LLAMA_CPP_DIR}/build/bin/llama-quantize" ]]; then
    QUANTIZE_BIN="${LLAMA_CPP_DIR}/build/bin/llama-quantize"
elif command -v llama-quantize &>/dev/null; then
    QUANTIZE_BIN="llama-quantize"
elif [[ -f "./llama.cpp/build/bin/llama-quantize" ]]; then
    QUANTIZE_BIN="./llama.cpp/build/bin/llama-quantize"
else
    log_error "No se encontró llama-quantize."
    log_error "Opciones:"
    log_error "  1. Establece LLAMA_CPP_DIR apuntando al directorio de llama.cpp compilado"
    log_error "  2. Compila llama.cpp: cd llama.cpp && cmake -B build && cmake --build build"
    log_error "  3. Asegúrate de que llama-quantize esté en el PATH"
    # Limpiar archivo F16 intermedio
    rm -f "$RUTA_GGUF_F16"
    exit 2
fi

RUTA_GGUF_FINAL="${OUTPUT_DIR}/${NOMBRE_GGUF_FINAL}"

if ! "$QUANTIZE_BIN" "$RUTA_GGUF_F16" "$RUTA_GGUF_FINAL" "$QUANTIZATION" 2>&1; then
    log_error "Fallo en la cuantización con nivel '${QUANTIZATION}'."
    log_error "Archivo de entrada: $RUTA_GGUF_F16"
    # Limpiar archivos parciales
    rm -f "$RUTA_GGUF_FINAL"
    exit 2
fi

log_info "Cuantización completada: $RUTA_GGUF_FINAL"

# Eliminar el archivo F16 intermedio para ahorrar espacio
log_info "Eliminando archivo F16 intermedio: $RUTA_GGUF_F16"
rm -f "$RUTA_GGUF_F16"

# =============================================================================
# PASO 4: Verificar integridad con inferencia de prueba
# =============================================================================
log_info "Paso 4/4 — Verificando integridad del GGUF con inferencia de prueba"

# Verificar que Ollama está disponible
if ! command -v ollama &>/dev/null; then
    log_error "Ollama no está instalado o no está en el PATH."
    log_error "Instálalo desde: https://ollama.com/download"
    exit 3
fi

# Crear un Modelfile temporal para la verificación
MODELFILE_TEMP=$(mktemp)
cat > "$MODELFILE_TEMP" <<EOF
FROM ${RUTA_GGUF_FINAL}
PARAMETER num_predict 10
EOF

# Nombre temporal del modelo para verificación
MODELO_TEMP="velvet-verify-$(date +%s)"

# Crear el modelo en Ollama
log_info "Creando modelo temporal '$MODELO_TEMP' en Ollama para verificación..."
if ! ollama create "$MODELO_TEMP" -f "$MODELFILE_TEMP" 2>&1; then
    log_error "Fallo al crear el modelo temporal en Ollama."
    log_error "Archivo GGUF: $RUTA_GGUF_FINAL"
    rm -f "$MODELFILE_TEMP"
    exit 3
fi

# Ejecutar inferencia de prueba
log_info "Ejecutando inferencia de prueba..."
RESPUESTA=$(ollama run "$MODELO_TEMP" "Hola, ¿funciona correctamente?" 2>&1) || true

# Limpiar modelo temporal
log_info "Limpiando modelo temporal de verificación..."
ollama rm "$MODELO_TEMP" 2>/dev/null || true
rm -f "$MODELFILE_TEMP"

# Verificar que se obtuvo una respuesta no vacía
if [[ -z "$RESPUESTA" ]]; then
    log_error "La inferencia de prueba no produjo respuesta."
    log_error "El archivo GGUF puede estar corrupto: $RUTA_GGUF_FINAL"
    exit 3
fi

log_info "Verificación exitosa. Respuesta de prueba recibida (${#RESPUESTA} caracteres)."

# =============================================================================
# Resumen final
# =============================================================================
TAMANO_ARCHIVO=$(du -h "$RUTA_GGUF_FINAL" | cut -f1)

log_info "============================================"
log_info "¡Conversión completada exitosamente!"
log_info "  Modelo origen:   $MODEL"
log_info "  Cuantización:    $QUANTIZATION"
log_info "  Archivo GGUF:    $RUTA_GGUF_FINAL"
log_info "  Tamaño:          $TAMANO_ARCHIVO"
log_info "============================================"

exit 0
