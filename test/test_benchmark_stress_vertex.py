#!/usr/bin/env python3
"""
test_benchmark_stress_vertex.py — Benchmark de Stress Test para Vertex AI

Genera métricas comparables con los benchmarks de Ollama on-premise:
  - Throughput (Tokens/s): tokens generados por segundo agregado bajo carga
  - Latencia Media: tiempo promedio de respuesta completa por solicitud
  - TTFT estimado: Time To First Token (estimado, Vertex predict no soporta streaming nativo)

Ejecuta pruebas con N usuarios concurrentes contra un endpoint de Vertex AI.

Uso:
    python test/test_benchmark_stress_vertex.py \
        --project arquitectura-ventas \
        --endpoint-id mg-endpoint-dc04b5c0-6512-4985-95c8-ab101ffa7e4f \
        --region us-central1 \
        --model velvet-14b \
        --users 10

    python test/test_benchmark_stress_vertex.py \
        --project arquitectura-ventas \
        --endpoint-id mg-endpoint-dc04b5c0-6512-4985-95c8-ab101ffa7e4f \
        --model velvet-14b \
        --users 15
"""

import argparse
import json
import os
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    """Resultado de una solicitud individual."""
    user_id: int
    success: bool
    ttft_s: float              # Time To First Token (estimado)
    total_latency_s: float     # Latencia total de la solicitud
    tokens_generated: int      # Tokens generados (estimado)
    tokens_per_second: float   # Tokens/s de esta solicitud
    response_text: str = ""
    error: str = ""


@dataclass
class BenchmarkResult:
    """Resultado consolidado del benchmark."""
    model: str
    hardware: str
    concurrent_users: int
    timestamp: str
    throughput_tokens_s: float
    latency_mean_s: float
    ttft_mean_s: float
    latency_p50_s: float
    latency_p90_s: float
    latency_p99_s: float
    ttft_p50_s: float
    ttft_p90_s: float
    ttft_p99_s: float
    total_tokens_generated: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_duration_s: float
    tokens_per_second_per_user: float
    requests: List[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Prompt de benchmark (idéntico al de Ollama para comparabilidad)
# ---------------------------------------------------------------------------

BENCHMARK_PROMPT = (
    "Descrivi in modo dettagliato i principali vantaggi dell'intelligenza "
    "artificiale applicata al servizio clienti nelle aziende di telecomunicazioni. "
    "Includi almeno 5 punti specifici con esempi pratici."
)

BENCHMARK_SYSTEM = (
    "Sei un consulente esperto di intelligenza artificiale e telecomunicazioni. "
    "Rispondi in modo strutturato e professionale in italiano."
)


# ---------------------------------------------------------------------------
# Función de solicitud a Vertex AI
# ---------------------------------------------------------------------------

def _get_dedicated_dns(project: str, region: str, endpoint_id: str) -> str:
    """Obtiene el DNS del dedicated endpoint vía gRPC (no afectado por proxy SSL)."""
    from google.cloud import aiplatform
    aiplatform.init(project=project, location=region)
    ep = aiplatform.Endpoint(endpoint_id)
    return ep.gca_resource.dedicated_endpoint_dns


def _get_credentials():
    """Obtiene y refresca credenciales ADC."""
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    return creds


def send_vertex_request(
    endpoint,
    prompt: str,
    system: str,
    timeout: int = 300,
    dedicated_dns: str = None,
    project: str = None,
    region: str = None,
    endpoint_id: str = None,
) -> RequestResult:
    """
    Envía una solicitud de predicción al endpoint de Vertex AI.

    Si dedicated_dns está configurado, usa REST directo con verify=False
    para evitar problemas con proxies corporativos SSL.
    Si no, usa el SDK nativo (endpoint.predict).

    Vertex AI predict no soporta streaming nativo como Ollama,
    así que TTFT se estima como la latencia total (peor caso).
    """
    import warnings

    instance = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 512,
            "temperature": 0.7,
            "top_p": 0.9,
        },
    }

    if system:
        instance["system"] = system

    start_time = time.time()

    try:
        if dedicated_dns:
            # REST directo para dedicated endpoints (bypass SSL proxy)
            import requests as req_lib

            creds = _get_credentials()
            url = (
                f"https://{dedicated_dns}/v1/"
                f"projects/{project}/locations/{region}/"
                f"endpoints/{endpoint_id}:predict"
            )
            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            }
            payload = {"instances": [instance]}

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                resp = req_lib.post(
                    url, json=payload, headers=headers,
                    verify=False, timeout=timeout,
                )

            total_latency = time.time() - start_time

            if resp.status_code != 200:
                return RequestResult(
                    user_id=0, success=False, ttft_s=0.0,
                    total_latency_s=total_latency, tokens_generated=0,
                    tokens_per_second=0.0,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )

            resp_json = resp.json()
            predictions = resp_json.get("predictions", [])
        else:
            # SDK nativo
            response = endpoint.predict(
                instances=[instance],
                timeout=timeout,
            )
            total_latency = time.time() - start_time
            predictions = response.predictions if hasattr(response, "predictions") else []

        # Extraer texto de la respuesta
        text = ""
        if predictions:
            pred = predictions[0]
            if isinstance(pred, str):
                text = pred
            elif isinstance(pred, dict):
                text = pred.get("generated_text", "")
                if isinstance(text, list) and text:
                    text = text[0] if isinstance(text[0], str) else str(text[0])
            elif isinstance(pred, list) and pred:
                first = pred[0]
                text = first.get("generated_text", "") if isinstance(first, dict) else str(first)

        # Estimar tokens (~5.5 chars por token para italiano/español con LLaMA tokenizer)
        tokens_generated = max(1, int(len(text) / 5.5)) if text else 0
        tps = tokens_generated / total_latency if total_latency > 0 and tokens_generated > 0 else 0

        # TTFT: Vertex predict no es streaming, estimamos como latencia total
        ttft = total_latency

        return RequestResult(
            user_id=0,
            success=True,
            ttft_s=ttft,
            total_latency_s=total_latency,
            tokens_generated=tokens_generated,
            tokens_per_second=tps,
            response_text=text[:200] if text else "",
        )

    except Exception as e:
        total_latency = time.time() - start_time
        return RequestResult(
            user_id=0,
            success=False,
            ttft_s=0.0,
            total_latency_s=total_latency,
            tokens_generated=0,
            tokens_per_second=0.0,
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Ejecución del benchmark
# ---------------------------------------------------------------------------

def run_benchmark(
    endpoint,
    model: str,
    num_users: int,
    hardware: str = "Vertex AI (GPU gestionada)",
    dedicated_dns: str = None,
    project: str = None,
    region: str = None,
    endpoint_id: str = None,
) -> BenchmarkResult:
    """
    Ejecuta el benchmark de stress con N usuarios concurrentes contra Vertex AI.
    """
    results: List[RequestResult] = []
    lock = threading.Lock()

    def worker(user_id: int):
        result = send_vertex_request(
            endpoint=endpoint,
            prompt=BENCHMARK_PROMPT,
            system=BENCHMARK_SYSTEM,
            dedicated_dns=dedicated_dns,
            project=project,
            region=region,
            endpoint_id=endpoint_id,
        )
        result.user_id = user_id
        with lock:
            results.append(result)
            status = "OK" if result.success else "FALLO"
            print(
                f"  [Usuario {user_id:3d}] {status} | "
                f"Latencia: {result.total_latency_s:.2f}s | "
                f"Tokens: {result.tokens_generated} | "
                f"TPS: {result.tokens_per_second:.1f}"
            )

    # --- Warm-up: cargar modelo en memoria antes de medir ---
    print(f"\n{'='*80}")
    print(f" BENCHMARK VERTEX AI: {model} | {num_users} usuarios concurrentes")
    print(f" Hardware: {hardware}")
    print(f"{'='*80}\n")

    print("  [WARM-UP] Cargando modelo en endpoint...")
    warmup_result = send_vertex_request(
        endpoint=endpoint, prompt="Hola", system="Responde brevemente.",
        dedicated_dns=dedicated_dns, project=project, region=region,
        endpoint_id=endpoint_id,
    )
    if warmup_result.success:
        print(f"  [WARM-UP] Endpoint listo. Latencia warm-up: {warmup_result.total_latency_s:.2f}s\n")
    else:
        print(f"  [WARM-UP] Advertencia: warm-up falló ({warmup_result.error}). "
              f"La primera solicitud puede incluir cold-start.\n")

    # --- Ejecutar solicitudes concurrentes ---
    threads = [threading.Thread(target=worker, args=(i + 1,)) for i in range(num_users)]

    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_duration = time.time() - start_time

    # --- Calcular métricas ---
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    if not successful:
        print("\n  ⚠️  TODAS LAS SOLICITUDES FALLARON")
        for f_req in failed:
            print(f"    Error: {f_req.error[:100]}")
        return BenchmarkResult(
            model=model, hardware=hardware, concurrent_users=num_users,
            timestamp=datetime.now(timezone.utc).isoformat(),
            throughput_tokens_s=0, latency_mean_s=0, ttft_mean_s=0,
            latency_p50_s=0, latency_p90_s=0, latency_p99_s=0,
            ttft_p50_s=0, ttft_p90_s=0, ttft_p99_s=0,
            total_tokens_generated=0, total_requests=num_users,
            successful_requests=0, failed_requests=len(failed),
            total_duration_s=total_duration, tokens_per_second_per_user=0,
        )

    latencies = sorted([r.total_latency_s for r in successful])
    ttfts = sorted([r.ttft_s for r in successful])
    total_tokens = sum(r.tokens_generated for r in successful)

    def percentile(data, p):
        """Calcula percentil con interpolación lineal (método estándar)."""
        n = len(data)
        if n == 1:
            return data[0]
        pos = p * (n - 1)
        lower_idx = int(pos)
        upper_idx = min(lower_idx + 1, n - 1)
        fraction = pos - lower_idx
        return data[lower_idx] + fraction * (data[upper_idx] - data[lower_idx])

    throughput = total_tokens / total_duration if total_duration > 0 else 0

    benchmark = BenchmarkResult(
        model=model,
        hardware=hardware,
        concurrent_users=num_users,
        timestamp=datetime.now(timezone.utc).isoformat(),
        throughput_tokens_s=round(throughput, 1),
        latency_mean_s=round(statistics.mean(latencies), 2),
        ttft_mean_s=round(statistics.mean(ttfts), 3),
        latency_p50_s=round(percentile(latencies, 0.5), 2),
        latency_p90_s=round(percentile(latencies, 0.9), 2),
        latency_p99_s=round(percentile(latencies, 0.99), 2),
        ttft_p50_s=round(percentile(ttfts, 0.5), 3),
        ttft_p90_s=round(percentile(ttfts, 0.9), 3),
        ttft_p99_s=round(percentile(ttfts, 0.99), 3),
        total_tokens_generated=total_tokens,
        total_requests=num_users,
        successful_requests=len(successful),
        failed_requests=len(failed),
        total_duration_s=round(total_duration, 2),
        tokens_per_second_per_user=round(
            statistics.mean([r.tokens_per_second for r in successful]), 1
        ),
        requests=[
            {
                "user_id": r.user_id,
                "success": r.success,
                "ttft_s": round(r.ttft_s, 3),
                "latency_s": round(r.total_latency_s, 2),
                "tokens": r.tokens_generated,
                "tps": round(r.tokens_per_second, 1),
            }
            for r in sorted(results, key=lambda x: x.user_id)
        ],
    )

    # --- Imprimir resumen ---
    print(f"\n{'='*80}")
    print(f" RESULTADOS: {model} | {hardware}")
    print(f"{'='*80}")
    print(f"")
    print(f"  ┌─────────────────────────────────────────────────────────────┐")
    print(f"  │ Ambiente (Hardware)     : {hardware:<35s}│")
    print(f"  │ Modelo                  : {model:<35s}│")
    print(f"  │ Usuarios concurrentes   : {num_users:<35d}│")
    print(f"  ├─────────────────────────────────────────────────────────────┤")
    print(f"  │ Throughput (Tokens/s)   : {benchmark.throughput_tokens_s:<35.1f}│")
    print(f"  │ Latencia Media          : {benchmark.latency_mean_s:<34.2f}s│")
    print(f"  │ TTFT (estimado*)        : {benchmark.ttft_mean_s:<34.3f}s│")
    print(f"  ├─────────────────────────────────────────────────────────────┤")
    print(f"  │ Latencia p50            : {benchmark.latency_p50_s:<34.2f}s│")
    print(f"  │ Latencia p90            : {benchmark.latency_p90_s:<34.2f}s│")
    print(f"  │ Latencia p99            : {benchmark.latency_p99_s:<34.2f}s│")
    print(f"  ├─────────────────────────────────────────────────────────────┤")
    print(f"  │ Tokens totales          : {benchmark.total_tokens_generated:<35d}│")
    print(f"  │ TPS por usuario (media) : {benchmark.tokens_per_second_per_user:<35.1f}│")
    print(f"  │ Solicitudes exitosas    : {benchmark.successful_requests}/{benchmark.total_requests}{'':<33s}│")
    print(f"  │ Duración total          : {benchmark.total_duration_s:<34.2f}s│")
    print(f"  └─────────────────────────────────────────────────────────────┘")
    print(f"")
    print(f"  * TTFT estimado = latencia total (Vertex predict API no soporta streaming)")
    print(f"")

    return benchmark


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark de Stress Test para Vertex AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", required=True, help="ID del proyecto GCP")
    parser.add_argument("--endpoint-id", required=True, help="ID del endpoint de Vertex AI")
    parser.add_argument("--region", default="us-central1", help="Región de GCP (default: us-central1)")
    parser.add_argument("--model", default="velvet-14b", help="Nombre del modelo (para etiquetas)")
    parser.add_argument("--users", type=int, default=10, help="Usuarios concurrentes (default: 10)")
    parser.add_argument("--hardware", default="Vertex AI (GPU gestionada)", help="Descripción del hardware")
    parser.add_argument("--output-dir", default="./results", help="Directorio de salida")
    return parser.parse_args()


def main():
    args = parse_args()

    # Importar y configurar Vertex AI
    try:
        from google.cloud import aiplatform
    except ImportError:
        print("Error: google-cloud-aiplatform no instalado. pip install google-cloud-aiplatform")
        sys.exit(1)

    print(f"Inicializando Vertex AI...")
    print(f"  Proyecto:    {args.project}")
    print(f"  Región:      {args.region}")
    print(f"  Endpoint ID: {args.endpoint_id}")

    aiplatform.init(project=args.project, location=args.region)
    endpoint = aiplatform.Endpoint(args.endpoint_id)

    # Verificar endpoint y detectar si es dedicated
    dedicated_dns = None
    try:
        print(f"  Endpoint:    {endpoint.display_name}")
        deployed = endpoint.gca_resource.deployed_models
        print(f"  Modelos:     {len(deployed)} desplegado(s)")

        # Detectar dedicated endpoint
        if endpoint.gca_resource.dedicated_endpoint_dns:
            dedicated_dns = endpoint.gca_resource.dedicated_endpoint_dns
            print(f"  Dedicated:   {dedicated_dns}")
            print(f"  Modo:        REST directo (bypass SSL proxy)")
        else:
            print(f"  Modo:        SDK nativo (endpoint.predict)")
    except Exception as e:
        print(f"Error al conectar con el endpoint: {e}")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Ejecutar benchmark
    result = run_benchmark(
        endpoint=endpoint,
        model=args.model,
        num_users=args.users,
        hardware=args.hardware,
        dedicated_dns=dedicated_dns,
        project=args.project,
        region=args.region,
        endpoint_id=args.endpoint_id,
    )

    # Guardar resultado
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"benchmark_vertex_{args.model}_{args.users}users_{ts}.json"
    filepath = os.path.join(args.output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    print(f"  Resultado guardado: {filepath}\n")


if __name__ == "__main__":
    main()
