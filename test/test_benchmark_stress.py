#!/usr/bin/env python3
"""
test_benchmark_stress.py — Benchmark de Deployment y Stress Test para Velvet 2B & 14B

Genera métricas comparables con los benchmarks oficiales de Almawave:
  - Throughput (Tokens/s): tokens generados por segundo agregado bajo carga
  - Latencia Media: tiempo promedio de respuesta completa por solicitud
  - Latencia Inicial (TTFT): Time To First Token — tiempo hasta recibir el primer token

Ejecuta pruebas con N usuarios concurrentes usando streaming para medir TTFT.

Uso:
    python test/test_benchmark_stress.py \
        --url http://localhost:8080 \
        --model velvet-14b-cpu-v1 \
        --users 10 \
        --auth admin:VelvetSecure2025!

    python test/test_benchmark_stress.py \
        --url http://localhost:8080 \
        --model velvet-2b-cpu-v1 \
        --users 10 \
        --auth admin:VelvetSecure2025!
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
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Modelos de datos
# ---------------------------------------------------------------------------

@dataclass
class RequestResult:
    """Resultado de una solicitud individual."""
    user_id: int
    success: bool
    ttft_s: float              # Time To First Token (segundos)
    total_latency_s: float     # Latencia total de la solicitud
    tokens_generated: int      # Tokens generados (eval_count de Ollama)
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
    # Métricas principales (las del cuadro de Almawave)
    throughput_tokens_s: float       # Throughput agregado (tokens/s)
    latency_mean_s: float            # Latencia media
    ttft_mean_s: float               # TTFT medio (Time To First Token)
    # Métricas adicionales
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
    # Detalle por solicitud
    requests: List[dict] = field(default_factory=list)

    def to_json(self) -> str:
        """Serializa a JSON."""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Prompt de benchmark
# ---------------------------------------------------------------------------

# Prompt diseñado para generar ~200 tokens de respuesta, similar a los
# benchmarks de Almawave que usan prompts de complejidad media
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
# Función de solicitud con streaming (para medir TTFT)
# ---------------------------------------------------------------------------

def send_streaming_request(
    url: str,
    model: str,
    prompt: str,
    system: str,
    auth: Optional[Tuple[str, str]] = None,
    timeout: int = 300,
    num_ctx: int = 4096,
) -> RequestResult:
    """
    Envía una solicitud con streaming habilitado para medir TTFT.

    Usa streaming para detectar el momento exacto en que llega el primer
    token, y luego acumula todos los tokens para calcular el throughput.
    """
    import requests

    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
        "options": {
            "num_ctx": num_ctx,
            "temperature": 0.7,
            "top_p": 0.9,
        }
    }

    ttft = 0.0
    total_text = ""
    tokens_generated = 0
    eval_duration_ns = 0

    start_time = time.time()

    try:
        response = requests.post(
            f"{url}/api/generate",
            json=payload,
            auth=auth,
            timeout=timeout,
            stream=True,
        )
        response.raise_for_status()

        first_token_received = False

        for line in response.iter_lines():
            if not line:
                continue

            chunk = json.loads(line)

            # Detectar el primer token generado
            if not first_token_received and chunk.get("response", ""):
                ttft = time.time() - start_time
                first_token_received = True

            # Acumular texto
            total_text += chunk.get("response", "")

            # El último chunk contiene las métricas finales
            if chunk.get("done", False):
                tokens_generated = chunk.get("eval_count", 0)
                eval_duration_ns = chunk.get("eval_duration", 0)
                break

        total_latency = time.time() - start_time

        # Calcular tokens/s desde las métricas de Ollama si están disponibles
        if eval_duration_ns > 0:
            tps = tokens_generated / (eval_duration_ns / 1e9)
        elif total_latency > 0 and tokens_generated > 0:
            tps = tokens_generated / total_latency
        else:
            # Estimar tokens si Ollama no reportó eval_count
            tokens_generated = max(1, len(total_text) // 4)
            tps = tokens_generated / total_latency if total_latency > 0 else 0

        # Si no se detectó TTFT (respuesta vacía), usar latencia total
        if ttft == 0.0:
            ttft = total_latency

        return RequestResult(
            user_id=0,
            success=True,
            ttft_s=ttft,
            total_latency_s=total_latency,
            tokens_generated=tokens_generated,
            tokens_per_second=tps,
            response_text=total_text[:200],
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
    url: str,
    model: str,
    num_users: int,
    auth: Optional[Tuple[str, str]] = None,
    hardware: str = "CPU",
    num_ctx: int = 4096,
) -> BenchmarkResult:
    """
    Ejecuta el benchmark de stress con N usuarios concurrentes.

    Todos los usuarios envían solicitudes simultáneamente y se miden
    throughput, latencia y TTFT.
    """
    results: List[RequestResult] = []
    lock = threading.Lock()

    def worker(user_id: int):
        """Hilo de trabajo para un usuario concurrente."""
        result = send_streaming_request(
            url=url,
            model=model,
            prompt=BENCHMARK_PROMPT,
            system=BENCHMARK_SYSTEM,
            auth=auth,
            num_ctx=num_ctx,
        )
        result.user_id = user_id
        with lock:
            results.append(result)
            status = "OK" if result.success else "FALLO"
            print(
                f"  [Usuario {user_id:3d}] {status} | "
                f"TTFT: {result.ttft_s:.3f}s | "
                f"Latencia: {result.total_latency_s:.2f}s | "
                f"Tokens: {result.tokens_generated} | "
                f"TPS: {result.tokens_per_second:.1f}"
            )

    # --- Warm-up: cargar modelo en memoria antes de medir ---
    print(f"\n{'='*80}")
    print(f" BENCHMARK: {model} | {num_users} usuarios concurrentes | {hardware}")
    print(f"{'='*80}\n")

    print("  [WARM-UP] Cargando modelo en memoria...")
    warmup_result = send_streaming_request(
        url=url, model=model, prompt="Hola", system="Responde brevemente.",
        auth=auth, num_ctx=512,
    )
    if warmup_result.success:
        print(f"  [WARM-UP] Modelo cargado. Latencia warm-up: {warmup_result.total_latency_s:.2f}s\n")
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

    # Throughput agregado: total de tokens generados / duración total del burst
    throughput = total_tokens / total_duration if total_duration > 0 else 0

    benchmark = BenchmarkResult(
        model=model,
        hardware=hardware,
        concurrent_users=num_users,
        timestamp=datetime.now(timezone.utc).isoformat(),
        # Métricas principales
        throughput_tokens_s=round(throughput, 1),
        latency_mean_s=round(statistics.mean(latencies), 2),
        ttft_mean_s=round(statistics.mean(ttfts), 3),
        # Percentiles de latencia
        latency_p50_s=round(percentile(latencies, 0.5), 2),
        latency_p90_s=round(percentile(latencies, 0.9), 2),
        latency_p99_s=round(percentile(latencies, 0.99), 2),
        # Percentiles de TTFT
        ttft_p50_s=round(percentile(ttfts, 0.5), 3),
        ttft_p90_s=round(percentile(ttfts, 0.9), 3),
        ttft_p99_s=round(percentile(ttfts, 0.99), 3),
        # Contadores
        total_tokens_generated=total_tokens,
        total_requests=num_users,
        successful_requests=len(successful),
        failed_requests=len(failed),
        total_duration_s=round(total_duration, 2),
        tokens_per_second_per_user=round(
            statistics.mean([r.tokens_per_second for r in successful]), 1
        ),
        # Detalle (sin texto de respuesta para reducir tamaño)
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
    print(f"  │ Latencia Inicial (TTFT) : {benchmark.ttft_mean_s:<34.3f}s│")
    print(f"  ├─────────────────────────────────────────────────────────────┤")
    print(f"  │ Latencia p50            : {benchmark.latency_p50_s:<34.2f}s│")
    print(f"  │ Latencia p90            : {benchmark.latency_p90_s:<34.2f}s│")
    print(f"  │ Latencia p99            : {benchmark.latency_p99_s:<34.2f}s│")
    print(f"  │ TTFT p50                : {benchmark.ttft_p50_s:<34.3f}s│")
    print(f"  │ TTFT p90                : {benchmark.ttft_p90_s:<34.3f}s│")
    print(f"  │ TTFT p99                : {benchmark.ttft_p99_s:<34.3f}s│")
    print(f"  ├─────────────────────────────────────────────────────────────┤")
    print(f"  │ Tokens totales          : {benchmark.total_tokens_generated:<35d}│")
    print(f"  │ TPS por usuario (media) : {benchmark.tokens_per_second_per_user:<35.1f}│")
    print(f"  │   (velocidad generación del modelo por solicitud individual)   │")
    print(f"  │ Solicitudes exitosas    : {benchmark.successful_requests}/{benchmark.total_requests}{'':<33s}│")
    print(f"  │ Duración total          : {benchmark.total_duration_s:<34.2f}s│")
    print(f"  └─────────────────────────────────────────────────────────────┘")
    print(f"")
    print(f"  Nota: Throughput = tokens_totales / duración_burst (capacidad del sistema)")
    print(f"        TPS/usuario = media de tokens/latencia por solicitud (velocidad del modelo)")
    print(f"")

    return benchmark


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark de Deployment y Stress Test para Velvet 2B & 14B",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Benchmark Velvet-2B con 10 usuarios
  python test/test_benchmark_stress.py --url http://localhost:8080 --model velvet-2b-cpu-v1 --users 10 --auth admin:VelvetSecure2025!

  # Benchmark Velvet-14B con 10 usuarios
  python test/test_benchmark_stress.py --url http://localhost:8080 --model velvet-14b-cpu-v1 --users 10 --auth admin:VelvetSecure2025!

  # Benchmark completo (ambos modelos)
  python test/test_benchmark_stress.py --url http://localhost:8080 --model all --users 10 --auth admin:VelvetSecure2025!
        """,
    )
    parser.add_argument("--url", required=True, help="URL base de la API (ej: http://localhost:8080)")
    parser.add_argument("--model", required=True, help="Modelo a evaluar o 'all' para ambos")
    parser.add_argument("--users", type=int, default=10, help="Usuarios concurrentes (default: 10)")
    parser.add_argument("--auth", default=None, help="Credenciales user:password para Basic Auth")
    parser.add_argument("--hardware", default="EC2 c6i.8xlarge [32 vCPU, CPU-only]", help="Descripción del hardware")
    parser.add_argument("--output-dir", default="./results", help="Directorio de salida (default: ./results)")
    parser.add_argument("--num-ctx", type=int, default=4096, help="Ventana de contexto (default: 4096)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Parsear credenciales
    auth = None
    if args.auth:
        parts = args.auth.split(":", 1)
        if len(parts) == 2:
            auth = (parts[0], parts[1])

    # Crear directorio de salida
    os.makedirs(args.output_dir, exist_ok=True)

    # Determinar modelos a evaluar
    if args.model.lower() == "all":
        models = ["velvet-2b-cpu-v1", "velvet-14b-cpu-v1"]
    else:
        models = [args.model]

    all_results = []

    for model in models:
        result = run_benchmark(
            url=args.url,
            model=model,
            num_users=args.users,
            auth=auth,
            hardware=args.hardware,
            num_ctx=args.num_ctx,
        )
        all_results.append(result)

        # Guardar resultado individual
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"benchmark_{model}_{args.users}users_{ts}.json"
        filepath = os.path.join(args.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(result.to_json())
        print(f"  Resultado guardado: {filepath}\n")

    # --- Tabla comparativa final (estilo Almawave) ---
    if len(all_results) > 0:
        print(f"\n{'='*80}")
        print(f" Velvet 2B & 14B: Deployment e Stress Test")
        print(f" Usuarios concurrentes: {args.users} | Hardware: {args.hardware}")
        print(f"{'='*80}")
        print(f"")
        print(f"  {'Modelo':<25s} {'Throughput (T/s)':>18s} {'Latencia Media':>16s} {'TTFT':>16s}")
        print(f"  {'─'*25} {'─'*18} {'─'*16} {'─'*16}")
        for r in all_results:
            ttft_str = f"{r.ttft_mean_s:.2f}s ({r.ttft_mean_s*1000:.0f}ms)" if r.ttft_mean_s > 0 else "N/D"
            lat_str = f"{r.latency_mean_s:.2f}s" if r.latency_mean_s > 0 else "N/D"
            print(f"  {r.model:<25s} {r.throughput_tokens_s:>18.1f} {lat_str:>16s} {ttft_str:>16s}")
        print(f"")
        print(f"{'='*80}")


if __name__ == "__main__":
    main()
