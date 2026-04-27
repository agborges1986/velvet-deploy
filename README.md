# Velvet Deploy

Infraestructura de despliegue y suite de pruebas para los modelos LLM **Velvet** (Almawave). Soporta dos entornos de ejecución: **on-premise** con Docker + Ollama en EC2, y **cloud** con Google Cloud Vertex AI.

## Arquitectura

```
                    ┌──────────────────────────────────────┐
                    │         On-Premise (EC2)              │
                    │                                      │
  Usuario ──8080──▶ │  Caddy (Basic Auth) ──11434──▶ Ollama │
                    │         ▲                            │
                    │         │ init (carga Modelfiles)    │
                    └──────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │         Cloud (Vertex AI)             │
                    │                                      │
  Usuario ──API──▶  │  Endpoint Vertex AI (HF DLC)         │
                    │  Scale-to-zero · GPU gestionada      │
                    └──────────────────────────────────────┘
```

**On-premise**: EC2 compute-optimized (c6i.8xlarge — 32 vCPU, 64 GB RAM), inferencia CPU-only con cuantización Q4_K_M. Caddy como reverse proxy con autenticación Basic Auth.

**Cloud**: Vertex AI con integración nativa de Hugging Face, GPU gestionada, scale-to-zero.

## Estructura del proyecto

```
infra/
├── aws/
│   ├── ec2_provision.sh          # Aprovisionamiento EC2 (instancia, SG, Key Pair)
│   ├── ec2_setup.sh              # Configuración post-lanzamiento (Docker, HF CLI, repo)
│   ├── teardown.sh               # Limpieza de recursos AWS
│   └── credentials.md            # Guía de gestión de credenciales AWS
├── conversion/
│   ├── convert_model.sh          # Pipeline HF → GGUF (descarga, conversión, cuantización)
│   └── generate_modelfile.py     # Generador de Modelfiles para Ollama
├── vertex/
│   ├── deploy_vertex.py          # Despliegue en Vertex AI desde Hugging Face
│   ├── undeploy_vertex.py        # Limpieza de recursos Vertex AI
│   ├── auth_setup.sh             # Configuración de autenticación GCP
│   └── credentials.md            # Guía de gestión de credenciales GCP
├── .env.example                  # Template de variables de entorno
├── Caddyfile                     # Configuración del reverse proxy
└── docker-compose.yml            # Stack Docker on-premise

test/
├── adapters/
│   ├── base.py                   # BackendAdapter (interfaz abstracta + factory)
│   ├── ollama_adapter.py         # Adaptador Ollama (HTTP + Basic Auth)
│   └── vertex_adapter.py         # Adaptador Vertex AI (SDK google-cloud-aiplatform)
├── models.py                     # Dataclasses (TestResult, GenerateResponse, configs)
├── runner.py                     # Orquestador CLI de tests
├── compare.py                    # Generador de reportes comparativos (Markdown/CSV)
├── conftest.py                   # Fixtures pytest + perfil Hypothesis
├── test_memoria.py               # Needle-in-haystack + razonamiento sobre derogación
├── test_numerico.py              # Cálculo financiero + extracción JSON estructurado
├── test_seguridad.py             # Privacidad PII (GDPR) + resistencia a inyección
├── test_stress.py                # Concurrencia (15 usuarios) + telemetría de hardware
├── test_latency_gap.py           # Inferencia unitaria (20×512 tokens) + cold start
├── test_context_pressure.py      # Degradación por tamaño de contexto (2k→32k)
├── test_quantization_fidelity.py # Calidad cuantización vs precisión completa
└── test_concurrency_throughput.py # Punto de saturación (1→5→10→20 usuarios)
```

## Requisitos previos

### On-premise (AWS)

- AWS CLI v2 configurado con permisos EC2 (ver `infra/aws/credentials.md`)
- Acceso SSH

### Vertex AI (GCP)

- `gcloud` CLI instalado y autenticado
- SDK `google-cloud-aiplatform` (`pip install google-cloud-aiplatform`)
- Permisos de Vertex AI (ver `infra/vertex/credentials.md`)

### Conversión de modelos

- Python 3.10+
- [llama.cpp](https://github.com/ggerganov/llama.cpp) compilado
- `huggingface-cli` (`pip install huggingface_hub[cli]`)
- Token de Hugging Face con acceso a los modelos Almawave

### Suite de pruebas

- Python 3.10+
- `requests`, `psutil`, `PyMuPDF` (`fitz`), `hypothesis`

## Despliegue on-premise (AWS + Docker)

### 1. Aprovisionar la instancia EC2

```bash
./infra/aws/ec2_provision.sh --key-name velvet-key --region us-east-1
```

Crea una instancia c6i.8xlarge con Security Group (puertos 22 y 8080) y volumen de 100 GB.

### 2. Preparar credenciales

```bash
cp infra/.env.example infra/.env
# Editar infra/.env con:
#   OLLAMA_AUTH_USER=admin
#   OLLAMA_AUTH_HASH=<hash generado con: caddy hash-password --plaintext "tu_contraseña">
#   HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
```

### 3. Configurar la instancia

```bash
./infra/aws/ec2_setup.sh \
  --instance-ip <IP_PUBLICA> \
  --key-file velvet-key.pem \
  --repo-url https://github.com/agborges1986/velvet-deploy.git \
  --env-file ./infra/.env
```

Instala Docker, Docker Compose, huggingface-cli y clona el repositorio.

### 4. Convertir modelos (en la instancia)

```bash
# Convertir modelo HF a GGUF cuantizado
./infra/conversion/convert_model.sh \
  --model "Almawave/Velvet-2B" \
  --quantization Q4_K_M \
  --output-dir ./models/

# Generar Modelfile para Ollama
python infra/conversion/generate_modelfile.py \
  --model velvet-2b \
  --gguf-path ./models/velvet-2b-cpu-v1-Q4_K_M.gguf \
  --output-dir ./infra/modelfiles/
```

### 5. Levantar el stack

```bash
cd infra
docker compose up -d
```

Servicios:
- **ollama**: servidor de inferencia (puerto 11434, solo red interna Docker)
- **caddy**: reverse proxy con Basic Auth (puerto 8080, único acceso externo)
- **init**: carga los Modelfiles en Ollama y termina

### 6. Verificar

```bash
curl -u admin:<contraseña> http://<IP>:8080/api/tags
```

## Despliegue en Vertex AI (GCP)

### 1. Configurar autenticación

```bash
./infra/vertex/auth_setup.sh --project mi-proyecto-gcp --region us-central1
```

### 2. Desplegar modelo

```bash
python infra/vertex/deploy_vertex.py \
  --model-id "Almawave/Velvet-2B" \
  --project mi-proyecto-gcp \
  --region us-central1 \
  --machine-type g2-standard-12 \
  --min-replicas 0 \
  --max-replicas 1
```

### 3. Limpieza

```bash
python infra/vertex/undeploy_vertex.py \
  --endpoint-id <ENDPOINT_ID> \
  --project mi-proyecto-gcp \
  --confirm
```

## Suite de pruebas

La suite evalúa los modelos Velvet en 8 categorías con múltiples iteraciones cada una:

| Test | Qué evalúa |
|------|------------|
| **memoria** | Needle-in-haystack en contexto largo + razonamiento sobre derogación de cláusulas |
| **numerico** | Precisión aritmética (interés compuesto) + generación de JSON estructurado válido |
| **seguridad** | Protección de datos PII (GDPR) + resistencia a inyección de prompts |
| **stress** | 15 usuarios concurrentes + telemetría de CPU/RAM |
| **latency_gap** | Inferencia unitaria (20×512 tokens) + medición de cold start |
| **context_pressure** | Degradación de rendimiento por tamaño de contexto (2k→32k tokens) |
| **quantization_fidelity** | Calidad de respuestas cuantizadas vs precisión completa |
| **concurrency_throughput** | Punto de saturación bajo carga creciente (1→5→10→20 usuarios) |

### Ejecutar con el runner

```bash
# Todos los tests contra Ollama
python -m test.runner --backend ollama --model velvet-2b-cpu-v1 --output-dir ./results/

# Tests específicos contra Vertex AI
python -m test.runner --backend vertex --model velvet-2b --tests memoria,seguridad

# Con pytest
pytest test/ --backend ollama --model velvet-2b-cpu-v1
```

### Generar reporte comparativo

```bash
# Markdown
python -m test.compare --results-dir ./results/ --output report.md --format markdown

# CSV
python -m test.compare --results-dir ./results/ --output report.csv --format csv
```

El reporte compara métricas lado a lado entre backends: tasa de éxito, latencia (media, p50, p90), tokens/s y RAM.

## Limpieza de recursos

### AWS

```bash
# Dry-run (muestra qué se eliminaría)
./infra/aws/teardown.sh --instance-id <ID> --security-group-id <SG_ID> --key-name velvet-key

# Ejecutar eliminación
./infra/aws/teardown.sh --instance-id <ID> --security-group-id <SG_ID> --key-name velvet-key --confirm
```

### Vertex AI

```bash
python infra/vertex/undeploy_vertex.py \
  --endpoint-id <ENDPOINT_ID> \
  --project mi-proyecto-gcp \
  --confirm
```

## Seguridad

- El puerto 11434 (Ollama) nunca se expone al exterior; solo es accesible dentro de la red Docker.
- Caddy maneja autenticación Basic Auth con hash bcrypt.
- Los scripts de teardown requieren `--confirm` para ejecutar (dry-run por defecto).
- Las credenciales se gestionan vía `.env` (excluido de git) y variables de entorno.
- Guías detalladas de credenciales en `infra/aws/credentials.md` e `infra/vertex/credentials.md`.

## Licencia

Uso interno.
