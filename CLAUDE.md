# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Velvet Deploy is a testing and infrastructure framework for evaluating Almawave's Velvet LLM models across two deployment environments:
- **On-Premise**: AWS EC2 (c6i.8xlarge, CPU-only) with Docker + Ollama + Caddy reverse proxy
- **Cloud**: Google Cloud Vertex AI with managed GPU endpoints

The core value is a unified 8-test suite that runs identically against both backends via an adapter pattern.

## Architecture

### Backend Abstraction (critical to understand)

All tests depend on `test/adapters/base.py`, which defines `BackendAdapter` (abstract class) and the `create_adapter(backend_name, config)` factory. Two implementations exist: `OllamaAdapter` and `VertexAdapter`. Every test module receives an adapter instance — tests never call backends directly.

```
test/adapters/base.py          # Abstract interface + factory
test/adapters/ollama_adapter.py  # HTTP POST to {base_url}/api/generate with Basic Auth
test/adapters/vertex_adapter.py  # google-cloud-aiplatform SDK
```

### Data Models (`test/models.py`)

Strongly-typed dataclasses with JSON serialization:
- `GenerateResponse`: text, latency_s, tokens_generated, tokens_per_second
- `TestResult`: backend, model, test_name, timestamp, success_rate, latency_p50/p90, tokens_per_second, max_ram_mb, details
- `OllamaConfig`, `VertexConfig`: read credentials from environment variables

### Test Module Convention

Each `test/test_*.py` exposes `run_test(adapter: BackendAdapter, model: str) -> TestResult`. Modules are independently runnable (they instantiate a default adapter when run directly) and are also invokable via pytest fixtures or the `runner.py` orchestrator.

### Infrastructure Layout

```
infra/
├── aws/              # EC2 provisioning (ec2_provision.sh, ec2_setup.sh, teardown.sh)
├── vertex/           # Vertex AI deploy/undeploy scripts (Python)
├── conversion/       # HF → GGUF quantization pipeline
├── docker-compose.yml  # ollama + caddy + init services
├── Caddyfile         # Basic Auth reverse proxy: external 8080 → internal 11434
└── .env.example      # Required vars template
```

Port 11434 (Ollama) is never exposed externally; Caddy on port 8080 is the only external entry point.

### Docker Compose Startup Order

`init` service waits for `ollama` health check, then runs `ollama create` for each Modelfile in `/modelfiles/`. Caddy depends on `ollama`. The `.env` file must exist with `OLLAMA_AUTH_USER` and `OLLAMA_AUTH_HASH` before `docker compose up`.

## Common Commands

### Run Tests

```bash
# Full suite via runner (recommended)
python -m test.runner --backend ollama --model velvet-2b-cpu-v1 --output-dir ./results/

# Specific tests
python -m test.runner --backend vertex --model velvet-14b \
  --tests memoria,seguridad \
  --vertex-project my-project --vertex-endpoint-id 123456789 \
  --output-dir ./results/

# Single test via pytest
pytest test/test_memoria.py --backend ollama --model velvet-2b-cpu-v1

# Single test standalone (uses hardcoded defaults)
python test/test_memoria.py
```

### Generate Comparison Report

```bash
python -m test.compare --results-dir ./results/ --output report.md --format markdown
```

### Infrastructure

```bash
# Start on-premise stack (requires infra/.env)
cd infra && docker compose up -d

# Provision EC2
./infra/aws/ec2_provision.sh --key-name velvet-key --region us-east-1

# Teardown (--confirm required to actually delete)
./infra/aws/teardown.sh --instance-id i-xxx --security-group-id sg-xxx --key-name velvet-key --confirm

# Deploy to Vertex AI
python infra/vertex/deploy_vertex.py --model-id "Almawave/Velvet-2B" --project my-project --region us-central1

# Undeploy Vertex AI
python infra/vertex/undeploy_vertex.py --endpoint-id 123456789 --project my-project --confirm
```

### Model Conversion

```bash
# Download and quantize (on EC2)
./infra/conversion/convert_model.sh --model "Almawave/Velvet-2B" --quantization Q4_K_M --output-dir ./models/

# Generate Ollama Modelfile from GGUF
python infra/conversion/generate_modelfile.py --model velvet-2b \
  --gguf-path ./models/velvet-2b-cpu-v1-Q4_K_M.gguf --output-dir ./infra/modelfiles/
```

## Environment Variables

Copy `infra/.env.example` to `infra/.env` and populate:

```bash
OLLAMA_AUTH_USER=admin
OLLAMA_AUTH_HASH=<caddy hash-password output>
HF_TOKEN=hf_xxx          # Hugging Face token for gated Almawave models
VERTEX_PROJECT=...        # GCP project ID
VERTEX_REGION=us-central1
VERTEX_ENDPOINT_ID=...    # From deploy_vertex.py output
```

`OllamaConfig` and `VertexConfig` in `models.py` read these from environment at instantiation.

## Test Suite

| Test | What it measures |
|------|-----------------|
| `test_memoria` | Needle-in-haystack retrieval (long context PDFs) + clause derogation |
| `test_numerico` | Financial calculations + JSON parsing correctness |
| `test_seguridad` | PII masking (GDPR) + prompt injection resistance |
| `test_stress` | 15 concurrent users + CPU/RAM telemetry via psutil |
| `test_latency_gap` | Single-request latency distribution (20×512 tokens), cold-start detection |
| `test_context_pressure` | Latency degradation curve at 2k→4k→8k→16k→32k context |
| `test_quantization_fidelity` | Quality delta and latency speedup vs full-precision |
| `test_concurrency_throughput` | Saturation point under 1→5→10→20 concurrent users |

**Stats utilities** (`test/stats_utils.py`): `percentile()` uses linear interpolation (matches numpy default); `warmup()` runs one discarded request before measurements to avoid cold-start inflation.

**Hypothesis** (`test/conftest.py`): configured for 100 examples per property-based test.

## Extending the Project

### Adding a new test

1. Create `test/test_<name>.py` with `run_test(adapter, model) -> TestResult`
2. Add to `TESTS_DISPONIBLES` dict in `test/runner.py`
3. Optionally register in `test/conftest.py` for pytest fixture injection

### Adding a new backend

1. Implement `BackendAdapter` in `test/adapters/new_adapter.py`
2. Register in `create_adapter()` factory in `test/adapters/base.py`
3. Add config dataclass to `test/models.py` if needed — all existing tests work automatically
