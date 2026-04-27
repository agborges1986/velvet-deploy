#!/bin/bash
# =============================================================================
# run_all_tests_ec2.sh — Suite completa de tests Velvet en EC2
# =============================================================================
# Ejecutar desde la shell de la EC2 (via EC2 Instance Connect, Session Manager
# o SSH directo).
#
# Uso:
#   bash run_all_tests_ec2.sh
#
# Prerequisitos:
#   - Stack Docker corriendo (docker compose up -d)
#   - Modelos velvet-2b-cpu-v1 y velvet-14b-cpu-v1 cargados en Ollama
# =============================================================================

set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.local/bin"

REPO_DIR="$HOME/velvet-project"
RESULTS_DIR="$REPO_DIR/results"
API_URL="http://localhost:8080"
AUTH_USER="admin"
AUTH_PASS="VelvetSecure2025!"
AUTH="${AUTH_USER}:${AUTH_PASS}"

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

paso() {
    echo ""
    echo -e "${GREEN}=============================================================================${NC}"
    echo -e "${GREEN} PASO $1: $2${NC}"
    echo -e "${GREEN}=============================================================================${NC}"
    echo ""
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

ok() {
    echo -e "${GREEN}[OK] $1${NC}"
}

# =============================================================================
paso "1" "Verificar estado del stack Docker"
# =============================================================================

cd "$REPO_DIR/infra"

if docker compose ps --format json 2>/dev/null | grep -q "running"; then
    ok "Stack Docker corriendo"
    docker compose ps
else
    warn "Stack Docker no está corriendo. Levantando..."
    docker compose up -d
    echo "Esperando 30s para que los servicios arranquen..."
    sleep 30
fi

# =============================================================================
paso "2" "Esperar a que Ollama esté healthy"
# =============================================================================

MAX_WAIT=120
WAITED=0
while true; do
    STATUS=$(docker inspect --format '{{.State.Health.Status}}' velvet-ollama 2>/dev/null || echo "not_found")
    if [ "$STATUS" = "healthy" ]; then
        ok "Ollama está healthy"
        break
    fi
    if [ $WAITED -ge $MAX_WAIT ]; then
        error "Ollama no alcanzó estado healthy en ${MAX_WAIT}s"
        echo "Estado actual: $STATUS"
        echo "Continuando de todas formas..."
        break
    fi
    echo "  Ollama estado: $STATUS — esperando... (${WAITED}s/${MAX_WAIT}s)"
    sleep 10
    WAITED=$((WAITED + 10))
done

echo ""
echo "Modelos disponibles:"
curl -s -u "$AUTH" "$API_URL/api/tags" | python3 -m json.tool 2>/dev/null || echo "(no se pudo listar modelos)"

# =============================================================================
paso "3" "Actualizar repositorio"
# =============================================================================

cd "$REPO_DIR"
git pull || warn "No se pudo actualizar el repo (puede estar sin remote o sin cambios)"

# =============================================================================
paso "4" "Instalar dependencias"
# =============================================================================

pip3 install --user requests psutil PyMuPDF hypothesis 2>&1 | tail -5

# =============================================================================
paso "5" "Crear directorio de resultados"
# =============================================================================

mkdir -p "$RESULTS_DIR"
ok "Directorio: $RESULTS_DIR"

# =============================================================================
paso "6" "Tests originales — Velvet 2B (numerico, seguridad, stress)"
# =============================================================================

cd "$REPO_DIR"
python3 -m test.runner \
    --backend ollama \
    --model velvet-2b-cpu-v1 \
    --tests numerico,seguridad,stress \
    --output-dir "$RESULTS_DIR/" || warn "Algunos tests de Velvet-2B fallaron"

# =============================================================================
paso "7" "Tests originales — Velvet 14B (numerico, seguridad, stress)"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-14b-cpu-v1 \
    --tests numerico,seguridad,stress \
    --output-dir "$RESULTS_DIR/" || warn "Algunos tests de Velvet-14B fallaron"

# =============================================================================
paso "8" "Latency Gap — Velvet 2B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-2b-cpu-v1 \
    --tests latency_gap \
    --output-dir "$RESULTS_DIR/" || warn "Latency Gap 2B falló"

# =============================================================================
paso "9" "Latency Gap — Velvet 14B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-14b-cpu-v1 \
    --tests latency_gap \
    --output-dir "$RESULTS_DIR/" || warn "Latency Gap 14B falló"

# =============================================================================
paso "10" "Context Pressure — Velvet 2B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-2b-cpu-v1 \
    --tests context_pressure \
    --output-dir "$RESULTS_DIR/" || warn "Context Pressure 2B falló"

# =============================================================================
paso "11" "Context Pressure — Velvet 14B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-14b-cpu-v1 \
    --tests context_pressure \
    --output-dir "$RESULTS_DIR/" || warn "Context Pressure 14B falló"

# =============================================================================
paso "12" "Quantization Fidelity — Velvet 2B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-2b-cpu-v1 \
    --tests quantization_fidelity \
    --output-dir "$RESULTS_DIR/" || warn "Quantization Fidelity 2B falló"

# =============================================================================
paso "13" "Quantization Fidelity — Velvet 14B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-14b-cpu-v1 \
    --tests quantization_fidelity \
    --output-dir "$RESULTS_DIR/" || warn "Quantization Fidelity 14B falló"

# =============================================================================
paso "14" "Concurrency Throughput — Velvet 2B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-2b-cpu-v1 \
    --tests concurrency_throughput \
    --output-dir "$RESULTS_DIR/" || warn "Concurrency Throughput 2B falló"

# =============================================================================
paso "15" "Concurrency Throughput — Velvet 14B"
# =============================================================================

python3 -m test.runner \
    --backend ollama \
    --model velvet-14b-cpu-v1 \
    --tests concurrency_throughput \
    --output-dir "$RESULTS_DIR/" || warn "Concurrency Throughput 14B falló"

# =============================================================================
paso "16" "Benchmark Stress Streaming — Velvet 2B (10 usuarios)"
# =============================================================================

python3 test/test_benchmark_stress.py \
    --url "$API_URL" \
    --model velvet-2b-cpu-v1 \
    --users 10 \
    --auth "$AUTH" \
    --hardware "EC2 c6i.8xlarge [32 vCPU, CPU-only]" \
    --output-dir "$RESULTS_DIR/" || warn "Benchmark Stress 2B falló"

# =============================================================================
paso "17" "Benchmark Stress Streaming — Velvet 14B (10 usuarios)"
# =============================================================================

python3 test/test_benchmark_stress.py \
    --url "$API_URL" \
    --model velvet-14b-cpu-v1 \
    --users 10 \
    --auth "$AUTH" \
    --hardware "EC2 c6i.8xlarge [32 vCPU, CPU-only]" \
    --output-dir "$RESULTS_DIR/" || warn "Benchmark Stress 14B falló"

# =============================================================================
paso "18" "Adherencia Lingüística — ambos modelos"
# =============================================================================

python3 test/test_idioma.py \
    --url "$API_URL" \
    --model all \
    --auth "$AUTH" \
    --output-dir "$RESULTS_DIR/" || warn "Test Idioma falló"

# =============================================================================
paso "19" "Verificar resultados generados"
# =============================================================================

echo "Archivos de resultados:"
ls -la "$RESULTS_DIR/"
echo ""
echo "Total de archivos JSON:"
find "$RESULTS_DIR/" -name "*.json" | wc -l

# =============================================================================
paso "20" "Generar reporte comparativo"
# =============================================================================

python3 -m test.compare \
    --results-dir "$RESULTS_DIR/" \
    --output "$RESULTS_DIR/REPORTE_COMPARATIVO.md" \
    --format markdown || warn "No se pudo generar reporte Markdown"

python3 -m test.compare \
    --results-dir "$RESULTS_DIR/" \
    --output "$RESULTS_DIR/REPORTE_COMPARATIVO.csv" \
    --format csv || warn "No se pudo generar reporte CSV"

# =============================================================================
paso "21" "SUITE COMPLETADA"
# =============================================================================

echo ""
echo "============================================================================="
echo " TODOS LOS TESTS COMPLETADOS"
echo "============================================================================="
echo ""
echo " Resultados en: $RESULTS_DIR/"
echo ""
echo " Para descargar desde tu máquina local:"
echo "   scp -i velvet-key.pem -r ec2-user@$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<IP>'):~/velvet-project/results/ ./results/"
echo ""
echo " Archivos generados:"
ls -1 "$RESULTS_DIR/"
echo ""
echo "============================================================================="
