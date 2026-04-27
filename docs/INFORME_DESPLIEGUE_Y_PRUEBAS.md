# Informe de Despliegue y Pruebas de Performance — Modelos Velvet 2B & 14B

**Fecha:** Abril 2026
**Equipo:** Cloud Engineering — TIVIT Almaviva Group
**Versión:** 2.0

---

## Índice

1. [Resumen Ejecutivo](#1-resumen-ejecutivo)
2. [Arquitectura de los Ambientes](#2-arquitectura-de-los-ambientes)
3. [Fase 1: Despliegue del Ambiente AWS (On-Premise Simulado)](#3-fase-1-despliegue-del-ambiente-aws)
4. [Fase 2: Despliegue del Ambiente Vertex AI (Google Cloud)](#4-fase-2-despliegue-del-ambiente-vertex-ai)
5. [Fase 3: Batería de Pruebas de Performance](#5-fase-3-batería-de-pruebas-de-performance)
6. [Fase 4: Ejecución de las Pruebas](#6-fase-4-ejecución-de-las-pruebas)
7. [Fase 5: Generación de Reportes Comparativos](#7-fase-5-generación-de-reportes-comparativos)
8. [Métricas Esperadas y Referencia](#8-métricas-esperadas-y-referencia)
9. [Análisis de Costos (ROI)](#9-análisis-de-costos-roi)
10. [Limpieza de Recursos](#10-limpieza-de-recursos)
11. [Anexos](#11-anexos)

---

## 1. Resumen Ejecutivo

Este informe documenta el proceso completo de despliegue de los modelos **Velvet 2B** y **Velvet 14B** (Almawave) en dos ambientes de inferencia distintos, y la ejecución de una batería exhaustiva de pruebas de performance, precisión cognitiva y estrés.

### Ambientes Evaluados

| Ambiente | Hardware | Inferencia | Cuantización |
|----------|----------|------------|--------------|
| **AWS EC2 (On-Premise)** | c6i.8xlarge — 32 vCPU, 64 GB RAM, CPU-only | Ollama + Caddy (Docker) | Q4_K_M (GGUF) |
| **Vertex AI (GCP)** | GPU gestionada (g2-standard-12 / NVIDIA L4) | HF Deep Learning Container | BF16 / FP16 |

### Objetivo

Determinar el **punto de saturación**, la **precisión cognitiva** y la **rentabilidad (ROI)** de cada ambiente, generando métricas comparables con los benchmarks oficiales de Almawave (vLLM sobre Red Hat OpenShift AI con NVIDIA H100).

---

## 2. Arquitectura de los Ambientes

### Ambiente 1: AWS EC2 (On-Premise Simulado)

```
                    ┌──────────────────────────────────────┐
                    │         On-Premise (EC2)              │
                    │                                      │
  Usuario ──8080──▶ │  Caddy (Basic Auth) ──11434──▶ Ollama │
                    │         ▲                            │
                    │         │ init (carga Modelfiles)    │
                    └──────────────────────────────────────┘
```

- **Instancia:** EC2 c6i.8xlarge (32 vCPU Intel Xeon, 64 GB RAM)
- **Engine:** Ollama (CPU-only)
- **Cuantización:** Q4_K_M (GGUF) — reduce Velvet-2B a ~1.5 GB, Velvet-14B a ~8.5 GB
- **Proxy:** Caddy con Basic Auth (puerto 8080)
- **Configuración Ollama:**
  - `OLLAMA_NUM_PARALLEL=4` (solicitudes paralelas)
  - `OLLAMA_NUM_THREAD=8` (threads por solicitud: 8 × 4 = 32 cores)
  - `OLLAMA_KEEP_ALIVE=10m`
  - `mem_limit=20g` (modelo + KV cache + overhead)

### Ambiente 2: Vertex AI (Google Cloud)

```
                    ┌──────────────────────────────────────┐
                    │         Cloud (Vertex AI)             │
                    │                                      │
  Usuario ──API──▶  │  Endpoint Vertex AI (HF DLC)         │
                    │  Scale-to-zero · GPU gestionada      │
                    └──────────────────────────────────────┘
```

- **Máquina:** g2-standard-12 (NVIDIA L4 GPU, 48 GB RAM)
- **Engine:** Hugging Face Deep Learning Container (vía integración nativa)
- **Precisión:** BF16 / FP16 (sin cuantización)
- **Autoscaling:** min=0 (scale-to-zero), max=1
- **Autenticación:** Application Default Credentials (ADC)

---

## 3. Fase 1: Despliegue del Ambiente AWS

### Paso 1: Aprovisionar la instancia EC2

```bash
# Desde la máquina local con AWS CLI configurado
./infra/aws/ec2_provision.sh --key-name velvet-key --region us-east-1
```

Este script:
- Detecta la IP pública del operador
- Busca la última AMI de Amazon Linux 2023
- Crea un Key Pair (si no existe) y guarda `velvet-key.pem`
- Crea un Security Group con reglas para SSH (22) y Caddy (8080)
- Lanza una instancia c6i.8xlarge con volumen EBS de 100 GB gp3
- Espera a que pase los status checks

**Output esperado:**
```
=============================================================================
 APROVISIONAMIENTO COMPLETADO
=============================================================================
 Instance ID:     i-0abc123def456789
 IP Pública:      54.123.45.67
 Security Group:  sg-0abc123def456789
 Tipo:            c6i.8xlarge
=============================================================================
```

### Paso 2: Preparar credenciales

```bash
cp infra/.env.example infra/.env
```

Editar `infra/.env`:
```dotenv
OLLAMA_AUTH_USER=admin
OLLAMA_AUTH_HASH=<hash generado con: caddy hash-password --plaintext "VelvetSecure2025!">
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
VERTEX_PROJECT=mi-proyecto-gcp
VERTEX_REGION=us-central1
VERTEX_ENDPOINT_ID=<se obtiene después del despliegue en Vertex>
```

### Paso 3: Configurar la instancia

```bash
./infra/aws/ec2_setup.sh \
  --instance-ip <IP_PUBLICA> \
  --key-file velvet-key.pem \
  --repo-url https://github.com/agborges1986/velvet-deploy.git \
  --env-file ./infra/.env
```

Instala Docker, Docker Compose, huggingface-cli y clona el repositorio.

### Paso 4: Convertir modelos (en la instancia EC2)

```bash
ssh -i velvet-key.pem ec2-user@<IP_PUBLICA>
cd ~/velvet-project

# Convertir Velvet-2B
./infra/conversion/convert_model.sh \
  --model "Almawave/Velvet-2B" \
  --quantization Q4_K_M \
  --output-dir ./models/

# Generar Modelfile para Ollama
python3 infra/conversion/generate_modelfile.py \
  --model velvet-2b \
  --gguf-path ./models/velvet-2b-cpu-v1-Q4_K_M.gguf \
  --output-dir ./infra/modelfiles/

# Repetir para Velvet-14B
./infra/conversion/convert_model.sh \
  --model "Almawave/Velvet-14B" \
  --quantization Q4_K_M \
  --output-dir ./models/

python3 infra/conversion/generate_modelfile.py \
  --model velvet-14b \
  --gguf-path ./models/velvet-14b-cpu-v1-Q4_K_M.gguf \
  --output-dir ./infra/modelfiles/
```

### Paso 5: Levantar el stack Docker

```bash
cd ~/velvet-project/infra
docker compose up -d
```

### Paso 6: Verificar

```bash
# Verificar que Ollama responde
curl -u admin:VelvetSecure2025! http://localhost:8080/api/tags

# Verificar modelos cargados
curl -u admin:VelvetSecure2025! http://localhost:8080/api/ps
```

---

## 4. Fase 2: Despliegue del Ambiente Vertex AI

### Paso 1: Configurar autenticación GCP

```bash
./infra/vertex/auth_setup.sh --project mi-proyecto-gcp --region us-central1
```

Verifica gcloud CLI, autenticación, ADC, permisos y habilita la API de Vertex AI.

### Paso 2: Desplegar Velvet-2B

```bash
python infra/vertex/deploy_vertex.py \
  --model-id "Almawave/Velvet-2B" \
  --project mi-proyecto-gcp \
  --region us-central1 \
  --machine-type g2-standard-12 \
  --min-replicas 0 \
  --max-replicas 1
```

### Paso 3: Desplegar Velvet-14B

```bash
python infra/vertex/deploy_vertex.py \
  --model-id "Almawave/Velvet-14B" \
  --project mi-proyecto-gcp \
  --region us-central1 \
  --machine-type g2-standard-24 \
  --min-replicas 0 \
  --max-replicas 1
```

> **Nota:** Anotar los `ENDPOINT_ID` de cada despliegue para configurar las pruebas.

### Paso 4: Actualizar `.env` con los Endpoint IDs

```bash
# Actualizar infra/.env con el VERTEX_ENDPOINT_ID correspondiente
```

---

## 5. Fase 3: Batería de Pruebas de Performance

La suite evalúa los modelos en **7 categorías** con métricas comparables entre ambientes:

### 5.1 Tests Funcionales (via `test/runner.py`)

| # | Test | Qué Evalúa | Iteraciones | Métricas Clave |
|---|------|------------|-------------|----------------|
| T1 | **Memoria** (needle-in-haystack) | Extracción de datos en contexto largo + razonamiento sobre derogación | 10 × 2 sub-tests | Tasa de éxito, latencia media, p50, p90 |
| T2 | **Numérico** | Precisión aritmética (interés compuesto) + generación JSON estructurado | 10 × 2 sub-tests | Tasa de éxito, latencia media, TPS |
| T3 | **Seguridad** | Protección PII (GDPR) + resistencia a inyección de prompts | 10 × 2 sub-tests | Tasa de éxito, latencia media |
| T4 | **Stress** | 15 usuarios concurrentes + telemetría CPU/RAM | 15 solicitudes simultáneas | Tasa de éxito, p90, burst time, CPU/RAM |
| T5 | **Latency Gap** | Inferencia unitaria (20×512 tokens) + cold start | 20 prompts de ~1k tokens | TPS, latencia p50/p90, cold start |
| T6 | **Context Pressure** | Degradación por tamaño de contexto (2k→32k) | 5 niveles × 5 iteraciones | Prefill time, TPS por nivel, degradación % |
| T7 | **Quantization Fidelity** | Calidad de respuestas cuantizadas vs FP16 | 5 prompts cognitivos | Jaccard similarity, consistencia, calidad |
| T8 | **Concurrency Throughput** | Punto de saturación (1→5→10→20 usuarios) | 4 niveles de carga | Throughput agregado, TPS/usuario, saturación |

### 5.2 Benchmarks de Stress (via `test/test_benchmark_stress.py`)

| # | Test | Qué Evalúa | Usuarios | Métricas Clave |
|---|------|------------|----------|----------------|
| B1 | **Benchmark Ollama** | Throughput, latencia, TTFT con streaming | 10-50 | Throughput (T/s), TTFT, latencia p50/p90/p99 |
| B2 | **Benchmark Vertex** | Throughput, latencia, TTFT estimado | 10-50 | Throughput (T/s), latencia p50/p90/p99 |

### 5.3 Tests Especializados

| # | Test | Qué Evalúa | Métricas Clave |
|---|------|------------|----------------|
| I1 | **Idioma** (italiano vs español) | Adherencia lingüística, calidad de respuesta | Adherencia %, tokens, latencia por idioma |
| O1 | **Matriz de Optimización** | Configuración óptima de NUM_PARALLEL/NUM_THREAD | Throughput por configuración |

### 5.4 Test de Precisión Cognitiva (Referencia Almawave)

Basado en el informe de referencia, el test de precisión cognitiva evalúa:

- **Aderencia Lingüística:** Uso exclusivo de PT-BR (o idioma solicitado)
- **Chain-of-Thought (Raciocínio):** Desdobramiento lógico paso a paso
- **Precisión Numérica:** Cálculo exacto de juros sobre juros sin redondeos precipitados

**Prompt de referencia:**
```
Calcule os juros compostos de um investimento de R$ 12.550,50
a uma taxa de 1,85% a.m. por um período de 3 meses.
Responda estritamente em português e mantenha a precisão
decimal em todas as etapas.
```

**Resultado esperado correcto:** R$ 1.497,27 (Velvet 14B)
**Resultado incorrecto (Velvet 2B):** R$ 2.025,80

---

## 5.5 Detalle de los Tests Avanzados

### Test T5: The Latency Gap (Eficiencia CPU vs GPU)

**Archivo:** `test/test_latency_gap.py`

**Objetivo:** Cuantificar cuánto castiga el procesador (vCPU) la generación de texto frente a la GPU. Mide el "gap" real de rendimiento entre ambas arquitecturas.

**Fase 1 — Inferencia Unitaria:**
- Envía 20 prompts de ~1k tokens de entrada (texto denso sobre regulación financiera europea)
- Genera 512 tokens por solicitud
- Mide TPS (Tokens Per Second) con `eval_duration` de Ollama (preciso) o estimación para Vertex

**Fase 2 — Cold Start:**
- Mide el tiempo desde la petición hasta la primera respuesta
- Crucial para arquitecturas serverless o scale-to-zero (como Vertex con min_replicas=0)

**Expectativas:**

| Métrica | EC2 c6i.8xlarge (CPU) | Vertex AI (GPU L4) |
|---------|----------------------|-------------------|
| TPS Velvet-2B | ~8-15 TPS | >50 TPS |
| TPS Velvet-14B | ~3-8 TPS | >20 TPS |
| Cold Start | ~15-30s (carga modelo en RAM) | ~30-120s (scale-from-zero) |

**Ejecución:**
```bash
python -m test.runner --backend ollama --model velvet-14b-cpu-v1 --tests latency_gap
python -m test.runner --backend vertex --model velvet-14b --tests latency_gap
```

---

### Test T6: Context Pressure (VRAM vs RAM)

**Archivo:** `test/test_context_pressure.py`

**Objetivo:** Evaluar cómo se degrada el rendimiento a medida que crece la ventana de contexto. El Velvet-14B soporta 128k tokens, pero llenar ese contexto requiere mucha memoria para el KV Cache.

**Niveles de contexto evaluados:**

| Nivel | num_ctx | Chars entrada | Tokens aprox. |
|-------|---------|---------------|---------------|
| 2k | 2,048 | 6,000 | ~1,500 |
| 4k | 4,096 | 12,000 | ~3,000 |
| 8k | 8,192 | 24,000 | ~6,000 |
| 16k | 16,384 | 50,000 | ~12,500 |
| 32k | 32,768 | 100,000 | ~25,000 |

**Métricas por nivel:**
- **Prefill Time:** Tiempo que tarda en procesar el contexto inicial antes de generar
- **TPS bajo carga:** Velocidad de generación con contexto lleno
- **Degradación %:** Ratio de TPS entre contexto mínimo (2k) y el nivel actual
- **Tasa de éxito:** ¿Encuentra el dato insertado (needle-in-haystack)?

**Qué observar:**
- En la VM (RAM): La RAM de 64GB es suficiente para el modelo, pero el acceso a datos será lento. El bus de memoria es el cuello de botella real, no los núcleos de CPU.
- En Vertex (GPU): La GPU tiene HBM con ancho de banda de ~600 GB/s (L4). Mide en qué punto el KV Cache agota la VRAM.

**Ejecución:**
```bash
python -m test.runner --backend ollama --model velvet-14b-cpu-v1 --tests context_pressure
python -m test.runner --backend vertex --model velvet-14b --tests context_pressure
```

---

### Test T7: Quantization Fidelity (El Compromiso)

**Archivo:** `test/test_quantization_fidelity.py`

**Objetivo:** Medir cuánto "conocimiento" se pierde al cuantizar el modelo (Q4_K_M) para que corra en CPU, comparando contra la versión en precisión completa (BF16/FP16) en GPU.

**Prompts de evaluación (5 capacidades cognitivas):**

| # | Capacidad | Criterio de éxito |
|---|-----------|-------------------|
| 1 | Razonamiento lógico | Conclusión correcta (silogismo) |
| 2 | Cálculo financiero | Resultado numérico exacto ($11,576.25) |
| 3 | Extracción de datos | Datos correctos del documento |
| 4 | Resumen estructurado | 3 puntos con ejemplos |
| 5 | Traducción técnica | Terminología correcta en italiano |

**Métricas:**
- **Similitud Jaccard:** Overlap de vocabulario entre respuestas GT y cuantizada
- **Consistencia semántica:** ¿Ambos backends llegan a la misma conclusión?
- **Calidad estructural:** Heurísticas de formato (puntos, párrafos, no repetitivo)
- **Longitud relativa:** Ratio de longitud entre respuesta cuantizada y GT

**Modos de ejecución:**
```bash
# Modo single (evalúa calidad del backend disponible)
python -m test.runner --backend ollama --model velvet-14b-cpu-v1 --tests quantization_fidelity

# Modo comparación dual (requiere ambos backends)
python test/test_quantization_fidelity.py --compare
```

---

### Test T8: Concurrency Throughput (The Scaling Wall)

**Archivo:** `test/test_concurrency_throughput.py`

**Objetivo:** Identificar el punto exacto donde el rendimiento se degrada bajo carga concurrente creciente. Aquí se ve por qué la GPU cuesta lo que cuesta.

**Niveles de carga:**

| Nivel | Usuarios | Escenario |
|-------|----------|-----------|
| 1 | 1 | Baseline — rendimiento máximo por usuario |
| 2 | 5 | Carga baja — uso normal |
| 3 | 10 | Carga media — equipo pequeño |
| 4 | 20 | Carga alta — punto de saturación esperado en CPU |

**Métricas por nivel:**
- **Throughput agregado (T/s):** Tokens totales generados / duración del burst
- **TPS por usuario:** Velocidad individual de generación
- **Latencia p50/p90/p99:** Distribución de tiempos de respuesta
- **Degradación %:** Caída de TPS/usuario respecto al baseline
- **Punto de saturación:** Nivel donde TPS/usuario cae >50%

**Qué observar:**
- **VM (CPU):** El context switching de la CPU genera contención. Con `OLLAMA_NUM_PARALLEL=4`, las solicitudes extra se encolan. El "cuello de botella" aparece pronto por la latencia del bus de memoria RAM comparado con HBM.
- **Vertex (GPU):** El continuous batching permite procesar múltiples usuarios con penalización mínima. La GPU debería mantener TPS/usuario estable hasta ~20 usuarios.

**Ejecución:**
```bash
python -m test.runner --backend ollama --model velvet-14b-cpu-v1 --tests concurrency_throughput
python -m test.runner --backend vertex --model velvet-14b --tests concurrency_throughput
```

---

## 6. Fase 4: Ejecución de las Pruebas

### 6.1 Tests Funcionales — Ollama (AWS EC2)

```bash
# Todos los tests (incluyendo los 4 nuevos) contra Velvet-2B
python -m test.runner \
  --backend ollama \
  --model velvet-2b-cpu-v1 \
  --output-dir ./results/

# Todos los tests contra Velvet-14B
python -m test.runner \
  --backend ollama \
  --model velvet-14b-cpu-v1 \
  --output-dir ./results/

# Solo los 4 tests avanzados
python -m test.runner \
  --backend ollama \
  --model velvet-14b-cpu-v1 \
  --tests latency_gap,context_pressure,quantization_fidelity,concurrency_throughput \
  --output-dir ./results/
```

### 6.2 Tests Funcionales — Vertex AI (GCP)

```bash
# Todos los tests (incluyendo los 4 nuevos) contra Velvet-2B en Vertex
python -m test.runner \
  --backend vertex \
  --model velvet-2b \
  --vertex-project mi-proyecto-gcp \
  --vertex-endpoint-id <ENDPOINT_ID_2B> \
  --vertex-region us-central1 \
  --output-dir ./results/

# Todos los tests contra Velvet-14B en Vertex
python -m test.runner \
  --backend vertex \
  --model velvet-14b \
  --vertex-project mi-proyecto-gcp \
  --vertex-endpoint-id <ENDPOINT_ID_14B> \
  --vertex-region us-central1 \
  --output-dir ./results/

# Solo los 4 tests avanzados contra Vertex
python -m test.runner \
  --backend vertex \
  --model velvet-14b \
  --vertex-project mi-proyecto-gcp \
  --vertex-endpoint-id <ENDPOINT_ID_14B> \
  --tests latency_gap,context_pressure,quantization_fidelity,concurrency_throughput \
  --output-dir ./results/
```

### 6.2.1 Comparación Dual de Fidelidad (Vertex GT vs Ollama Q4_K_M)

```bash
# Requiere ambos backends configurados (variables de entorno)
export VERTEX_PROJECT=mi-proyecto-gcp
export VERTEX_ENDPOINT_ID=<ENDPOINT_ID_14B>
export VERTEX_REGION=us-central1

python test/test_quantization_fidelity.py --compare
```

### 6.3 Benchmarks de Stress — Ollama

```bash
# Benchmark Velvet-2B con escenarios de carga creciente
for USERS in 1 5 10 20 50; do
  python test/test_benchmark_stress.py \
    --url http://<IP_EC2>:8080 \
    --model velvet-2b-cpu-v1 \
    --users $USERS \
    --auth admin:VelvetSecure2025! \
    --hardware "EC2 c6i.8xlarge [32 vCPU, CPU-only]" \
    --output-dir ./results/
done

# Benchmark Velvet-14B con escenarios de carga creciente
for USERS in 1 5 10 20 50; do
  python test/test_benchmark_stress.py \
    --url http://<IP_EC2>:8080 \
    --model velvet-14b-cpu-v1 \
    --users $USERS \
    --auth admin:VelvetSecure2025! \
    --hardware "EC2 c6i.8xlarge [32 vCPU, CPU-only]" \
    --output-dir ./results/
done
```

### 6.4 Benchmarks de Stress — Vertex AI

```bash
# Benchmark Velvet-2B en Vertex
for USERS in 1 5 10 20 50; do
  python test/test_benchmark_stress_vertex.py \
    --project mi-proyecto-gcp \
    --endpoint-id <ENDPOINT_ID_2B> \
    --region us-central1 \
    --model velvet-2b \
    --users $USERS \
    --hardware "Vertex AI g2-standard-12 (NVIDIA L4)" \
    --output-dir ./results/
done

# Benchmark Velvet-14B en Vertex
for USERS in 1 5 10 20 50; do
  python test/test_benchmark_stress_vertex.py \
    --project mi-proyecto-gcp \
    --endpoint-id <ENDPOINT_ID_14B> \
    --region us-central1 \
    --model velvet-14b \
    --users $USERS \
    --hardware "Vertex AI g2-standard-24 (NVIDIA L4)" \
    --output-dir ./results/
done
```

### 6.5 Test de Adherencia Lingüística

```bash
# Italiano vs Español — ambos modelos en Ollama
python test/test_idioma.py \
  --url http://<IP_EC2>:8080 \
  --model all \
  --auth admin:VelvetSecure2025! \
  --output-dir ./results/
```

### 6.6 Matriz de Optimización (solo en EC2)

```bash
# Ejecutar en la instancia EC2 directamente
python3 test/test_optimization_matrix.py \
  --url http://localhost:8080 \
  --auth admin:VelvetSecure2025! \
  --compose-dir /home/ec2-user/velvet-project/infra \
  --output-dir ./results/optimization
```

### 6.7 Ejecución Remota Simplificada (Script All-in-One)

Para ejecutar la suite básica directamente en la EC2:

```bash
# Desde la máquina local
ssh -i velvet-key.pem ec2-user@<IP_EC2> 'bash -s' < run_tests_remote.sh
```

---

## 7. Fase 5: Generación de Reportes Comparativos

### Reporte Markdown (lado a lado Ollama vs Vertex)

```bash
python -m test.compare \
  --results-dir ./results/ \
  --output ./results/REPORTE_COMPARATIVO.md \
  --format markdown
```

### Reporte CSV (para análisis en Excel/Sheets)

```bash
python -m test.compare \
  --results-dir ./results/ \
  --output ./results/REPORTE_COMPARATIVO.csv \
  --format csv
```

El reporte compara métricas lado a lado entre backends:
- Tasa de éxito
- Latencia (media, p50, p90)
- Tokens/s
- RAM máxima
- Diferencia porcentual (Δ%) entre Ollama y Vertex

> **Nota sobre comparabilidad:** Las métricas de Tokens/s de Ollama usan `eval_duration` (tiempo real de generación), mientras que Vertex AI estima tokens con heurística (~5.5 chars/token para idiomas romance). Los valores de TPS entre backends no son directamente comparables.

---

## 8. Métricas Esperadas y Referencia

### 8.1 Referencia: Benchmarks Oficiales Almawave (vLLM + H100)

Datos del informe de referencia (Marzo 2026):

#### Velvet 2B sob Alta Carga

| Ambiente (Hardware) | Throughput (T/s) | Latencia Media | TTFT |
|---------------------|-----------------|----------------|------|
| NVIDIA H100 (Fatia mig-1g.12gb) [100 Usuarios] | **1.433** | **0,83s** | N/D |
| Intel Xeon 6767P [128 Usuarios] | 1.384 | N/D | **0,60s** (602ms) |
| Intel Xeon 6980P [128 Usuarios] | 1.208 | N/D | **0,52s** (520ms) |

#### Velvet 14B sob Alta Carga

| Ambiente (Hardware) | Throughput (T/s) | Latencia Media | TTFT |
|---------------------|-----------------|----------------|------|
| NVIDIA H100 (Fatia mig-4g.47gb) [100 Usuarios] | **3.404** | **0,20s** | N/D |
| Intel Xeon 6767P [128 Usuarios] | 439 | N/D | **1,76s** (1.767ms) |
| Intel Xeon 6980P [128 Usuarios] | 270 | N/D | **1,67s** (1.672ms) |

### 8.2 Expectativas para Nuestros Ambientes

| Métrica | EC2 c6i.8xlarge (CPU Q4_K_M) | Vertex AI (GPU BF16) |
|---------|------------------------------|----------------------|
| **Throughput (T/s)** — Velvet 2B | ~50-150 (10 users) | ~500-1000 (10 users) |
| **Throughput (T/s)** — Velvet 14B | ~15-50 (10 users) | ~200-500 (10 users) |
| **TTFT** — Velvet 2B | ~1-3s | ~0.2-0.5s |
| **TTFT** — Velvet 14B | ~3-10s | ~0.5-2s |
| **Latencia media** — single user | ~5-15s (2B), ~20-60s (14B) | ~1-3s (2B), ~3-8s (14B) |
| **Punto de saturación** | ~4-8 usuarios concurrentes | ~20-50 usuarios concurrentes |

### 8.3 Resultado del Test de Precisión Cognitiva (Referencia)

#### Velvet 2B — "Anatomy of a Failure"

1. **Falha de Instrução:** Ignoró la restricción de idioma (PT-BR) y respondió en inglés
2. **Atalho Lógico:** En vez de calcular mes a mes (Chain-of-Thought), aplicó fórmula simplificada
3. **Erro Aritmético:** Resultado incorrecto: R$ 2.025,80
4. **Alucinação:** Sugirió ejecutar un script en Python para "garantir a precisão"

#### Velvet 14B — "Anatomy of Success"

1. **Aderência 100%:** Respuesta íntegramente en Português (PT-BR)
2. **Desdobramento Mensal:** Cálculo estructurado paso a paso (juros sobre juros meses 1, 2 y 3)
3. **Manutenção Decimal:** Precisión rigurosa en todas las etapas intermediarias
4. **Resultado Exato:** R$ 1.497,27 ✅

> Procesó 670 tokens en 10,02s. El raciocínio correcto exige procesamiento metódico.

---

## 9. Análisis de Costos (ROI)

### Costo por Hora de Infraestructura

| Recurso | Costo/hora (estimado) | Costo/mes (24×7) |
|---------|----------------------|-------------------|
| EC2 c6i.8xlarge (on-demand) | ~$1.36/hr | ~$979/mes |
| EC2 c6i.8xlarge (reserved 1yr) | ~$0.86/hr | ~$619/mes |
| Vertex AI g2-standard-12 (L4) | ~$1.84/hr | ~$1,325/mes |
| Vertex AI g2-standard-24 (L4×2) | ~$3.68/hr | ~$2,650/mes |
| Vertex AI (scale-to-zero) | $0 cuando inactivo | Variable |

### Costo por 1K Tokens (estimado)

| Ambiente | Velvet 2B | Velvet 14B |
|----------|-----------|------------|
| EC2 (CPU) | ~$0.025/1K tokens | ~$0.090/1K tokens |
| Vertex AI (GPU) | ~$0.004/1K tokens | ~$0.010/1K tokens |

> **Conclusión de ROI:** Vertex AI es ~6-9x más eficiente en costo por token, pero tiene un costo base más alto. Para cargas constantes >8 horas/día, Vertex AI es más rentable. Para cargas esporádicas, EC2 con scale-to-zero manual puede ser más económico.

---

## 10. Limpieza de Recursos

### AWS

```bash
# Dry-run (muestra qué se eliminaría)
./infra/aws/teardown.sh \
  --instance-id <INSTANCE_ID> \
  --security-group-id <SG_ID> \
  --key-name velvet-key

# Ejecutar eliminación
./infra/aws/teardown.sh \
  --instance-id <INSTANCE_ID> \
  --security-group-id <SG_ID> \
  --key-name velvet-key \
  --confirm
```

### Vertex AI

```bash
# Eliminar endpoint Velvet-2B
python infra/vertex/undeploy_vertex.py \
  --endpoint-id <ENDPOINT_ID_2B> \
  --project mi-proyecto-gcp \
  --region us-central1 \
  --confirm

# Eliminar endpoint Velvet-14B
python infra/vertex/undeploy_vertex.py \
  --endpoint-id <ENDPOINT_ID_14B> \
  --project mi-proyecto-gcp \
  --region us-central1 \
  --confirm
```

---

## 11. Anexos

### A. Estructura de Archivos de Resultados

Cada test genera un archivo JSON con la siguiente estructura:

```json
{
  "backend": "ollama",
  "model": "velvet-2b-cpu-v1",
  "test_name": "numerico",
  "timestamp": "2026-04-27T15:30:00+00:00",
  "iterations": 10,
  "success_rate": 0.85,
  "latency_mean_s": 12.5,
  "latency_p50_s": 11.2,
  "latency_p90_s": 18.7,
  "tokens_per_second": 8.3,
  "max_ram_mb": 0.0,
  "details": { ... }
}
```

Los benchmarks de stress generan un formato extendido con:
- `throughput_tokens_s`: Throughput agregado
- `ttft_mean_s`: Time To First Token
- `latency_p99_s`: Percentil 99
- `requests`: Detalle por solicitud individual

### B. Nota sobre Comparabilidad de Métricas

| Métrica | Ollama | Vertex AI |
|---------|--------|-----------|
| **Tokens/s** | Preciso (usa `eval_duration` de Ollama) | Estimado (~5.5 chars/token para idiomas romance) |
| **TTFT** | Preciso (streaming habilitado) | Estimado (predict API no soporta streaming) |
| **Latencia** | Comparable | Comparable |
| **Tasa de éxito** | Comparable | Comparable |

### C. Configuración Óptima Recomendada (EC2)

Basado en la matriz de optimización y el documento de prioridades MLOps:

| Modelo | NUM_PARALLEL | NUM_THREAD | num_ctx | mem_limit |
|--------|-------------|------------|---------|-----------|
| Velvet-2B | 4 | 8 | 4096 | 8g |
| Velvet-14B | 2 | 16 | 4096 | 20g |

### D. Conclusión del Informe de Referencia Almawave

> **"A Ilusão da Velocidade (2B)":** O Velvet 2B é altamente eficiente em VRAM, mas apresenta um risco inaceitável de alucinações e desvios de compliance em tarefas lógicas, matemáticas ou financeiras.

> **"A Segurança Corporativa (14B)":** O Velvet 14B entrega precisão analítica irrefutável e total obediência a restrições complexas. Com o hardware adequado (H100), ele atende perfeitamente aos requisitos de latência (<0.20s).

> **Para ambientes corporativos de produção, onde decisões de negócios dependem da exatidão estrita dos dados gerados, o Velvet 14B é a única escolha robusta e justificada.**

---

*Informe generado por Cloud Engineering — TIVIT Almaviva Group*
*Contacto: engenharia.cloud@tivit.com*
