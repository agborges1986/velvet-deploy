"""
Test de Concurrencia y Throughput: "The Scaling Wall"

Simula cargas crecientes de usuarios concurrentes (1, 5, 10, 20 usuarios)
para identificar el punto exacto donde el rendimiento se degrada.

En la VM (CPU): Evalúa el context switching. Con 32 vCPUs, el Velvet-2B
aguantará bien, pero el 14B probablemente cause colas inmanejables.

En Vertex (GPU): Aprovecha el continuous batching. La GPU debería procesar
múltiples usuarios con penalización de latencia mínima.

Métricas por nivel de carga:
  - Throughput agregado (tokens/s totales del sistema)
  - Latencia media, p50, p90, p99 por usuario
  - TPS por usuario (velocidad individual)
  - Tasa de éxito
  - Punto de saturación (nivel donde TPS/usuario cae >50%)

Soporta ejecución directa o vía test runner / pytest con BackendAdapter.
"""

import statistics
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes ---
DEFAULT_MODEL = "velvet-legal"

# Niveles de concurrencia a evaluar
NIVELES_USUARIOS = [1, 5, 10, 20]

# Prompt estándar para todas las solicitudes (complejidad media, ~200 tokens salida)
PROMPT_CONCURRENCIA = (
    "Explica en 5 puntos las principales ventajas de la inteligencia artificial "
    "aplicada al servicio al cliente en empresas de telecomunicaciones. "
    "Incluye un ejemplo práctico para cada punto."
)
SYSTEM_CONCURRENCIA = (
    "Eres un consultor experto en IA y telecomunicaciones. "
    "Responde de forma estructurada y profesional en español."
)


def _ejecutar_nivel(
    adapter: BackendAdapter,
    model: str,
    num_usuarios: int,
) -> Dict:
    """
    Ejecuta un nivel de concurrencia con N usuarios simultáneos.

    Retorna un diccionario con métricas agregadas del nivel.
    """
    resultados = []
    lock = threading.Lock()

    def worker(user_id: int):
        """Hilo de trabajo para un usuario concurrente."""
        try:
            resp = adapter.generate(
                model=model,
                prompt=PROMPT_CONCURRENCIA,
                system=SYSTEM_CONCURRENCIA,
                options={
                    "num_ctx": 4096,
                    "num_predict": 256,
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
            )
            with lock:
                resultados.append({
                    "user_id": user_id,
                    "success": True,
                    "latency": resp.latency,
                    "tps": resp.tokens_per_second,
                    "tokens": resp.tokens_generated,
                })
        except Exception as e:
            with lock:
                resultados.append({
                    "user_id": user_id,
                    "success": False,
                    "latency": 0.0,
                    "tps": 0.0,
                    "tokens": 0,
                    "error": str(e),
                })

    # Lanzar todos los hilos simultáneamente
    hilos = [threading.Thread(target=worker, args=(i + 1,)) for i in range(num_usuarios)]

    start_time = time.time()
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()
    burst_duration = time.time() - start_time

    # Calcular métricas
    exitosos = [r for r in resultados if r["success"]]
    fallidos = [r for r in resultados if not r["success"]]

    if not exitosos:
        return {
            "num_usuarios": num_usuarios,
            "success": False,
            "throughput_tokens_s": 0.0,
            "latency_mean_s": 0.0,
            "latency_p50_s": 0.0,
            "latency_p90_s": 0.0,
            "latency_p99_s": 0.0,
            "tps_per_user_mean": 0.0,
            "total_tokens": 0,
            "burst_duration_s": round(burst_duration, 2),
            "successful": 0,
            "failed": len(fallidos),
            "success_rate": 0.0,
        }

    latencias = sorted([r["latency"] for r in exitosos])
    tps_list = [r["tps"] for r in exitosos if r["tps"] > 0]
    total_tokens = sum(r["tokens"] for r in exitosos)

    # Throughput agregado: tokens totales / duración del burst
    throughput = total_tokens / burst_duration if burst_duration > 0 else 0.0

    return {
        "num_usuarios": num_usuarios,
        "success": True,
        "throughput_tokens_s": round(throughput, 1),
        "latency_mean_s": round(statistics.mean(latencias), 2),
        "latency_p50_s": round(percentile(latencias, 0.5), 2),
        "latency_p90_s": round(percentile(latencias, 0.9), 2),
        "latency_p99_s": round(percentile(latencias, 0.99) if len(latencias) >= 4 else latencias[-1], 2),
        "tps_per_user_mean": round(statistics.mean(tps_list), 1) if tps_list else 0.0,
        "total_tokens": total_tokens,
        "burst_duration_s": round(burst_duration, 2),
        "successful": len(exitosos),
        "failed": len(fallidos),
        "success_rate": round(len(exitosos) / num_usuarios, 2),
    }


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test de concurrencia completo con niveles crecientes.

    Identifica el punto de saturación donde TPS/usuario cae >50%
    respecto al baseline (1 usuario).
    """
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    print(f"{'='*70}")
    print(f" TEST: CONCURRENCY THROUGHPUT — The Scaling Wall")
    print(f" Modelo: {model} | Backend: {backend_name}")
    print(f" Niveles: {NIVELES_USUARIOS} usuarios")
    print(f"{'='*70}\n")

    # Warm-up
    print("  Ejecutando warm-up...")
    warmup(adapter, model)

    resultados_niveles = []
    tps_baseline = None  # TPS/usuario con 1 usuario (baseline)
    punto_saturacion = None

    for num_usuarios in NIVELES_USUARIOS:
        print(f"\n  ─── Nivel: {num_usuarios} usuario(s) concurrente(s) ───")

        nivel = _ejecutar_nivel(adapter, model, num_usuarios)
        resultados_niveles.append(nivel)

        if nivel["success"]:
            # Registrar baseline
            if tps_baseline is None and nivel["tps_per_user_mean"] > 0:
                tps_baseline = nivel["tps_per_user_mean"]

            # Detectar punto de saturación
            if tps_baseline and tps_baseline > 0 and nivel["tps_per_user_mean"] > 0:
                degradacion = ((tps_baseline - nivel["tps_per_user_mean"]) / tps_baseline) * 100
            else:
                degradacion = 0.0

            if degradacion > 50 and punto_saturacion is None:
                punto_saturacion = num_usuarios

            nivel["degradation_pct"] = round(degradacion, 1)

            print(f"    Throughput: {nivel['throughput_tokens_s']:.1f} T/s | "
                  f"Lat media: {nivel['latency_mean_s']:.2f}s | "
                  f"TPS/user: {nivel['tps_per_user_mean']:.1f} | "
                  f"Éxito: {nivel['successful']}/{num_usuarios} | "
                  f"Δ TPS: {-degradacion:+.1f}%")
        else:
            nivel["degradation_pct"] = -100.0
            if punto_saturacion is None:
                punto_saturacion = num_usuarios
            print(f"    ❌ TODAS LAS SOLICITUDES FALLARON")

    # ─── Resumen ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f" RESUMEN: CONCURRENCY THROUGHPUT — {model} ({backend_name})")
    print(f"{'='*70}")
    print(f"\n  {'Usuarios':>8s} {'Throughput':>12s} {'Lat media':>10s} {'Lat p90':>8s} "
          f"{'TPS/user':>9s} {'Éxito':>7s} {'Δ TPS':>8s}")
    print(f"  {'─'*8} {'─'*12} {'─'*10} {'─'*8} {'─'*9} {'─'*7} {'─'*8}")

    for n in resultados_niveles:
        if n["success"]:
            print(
                f"  {n['num_usuarios']:>8d} {n['throughput_tokens_s']:>11.1f} "
                f"{n['latency_mean_s']:>9.2f}s {n['latency_p90_s']:>7.2f}s "
                f"{n['tps_per_user_mean']:>8.1f} {n['success_rate']:>6.0%} "
                f"{-n.get('degradation_pct', 0):>+7.1f}%"
            )
        else:
            print(f"  {n['num_usuarios']:>8d} {'FALLO':>12s}")

    if punto_saturacion:
        print(f"\n  ⚠️  Punto de saturación detectado: {punto_saturacion} usuarios")
        print(f"     (TPS/usuario cayó >50% respecto al baseline)")
    else:
        print(f"\n  ✅ No se detectó punto de saturación en los niveles evaluados")

    if tps_baseline:
        print(f"  📊 TPS/usuario baseline (1 user): {tps_baseline:.1f}")

    print()

    # ─── Construir TestResult ────────────────────────────────────────────
    # Métricas globales: del nivel más alto exitoso
    niveles_exitosos = [n for n in resultados_niveles if n["success"]]
    ultimo_exitoso = niveles_exitosos[-1] if niveles_exitosos else None

    all_latencies = [n["latency_mean_s"] for n in niveles_exitosos if n["latency_mean_s"] > 0]
    all_throughputs = [n["throughput_tokens_s"] for n in niveles_exitosos if n["throughput_tokens_s"] > 0]

    # El throughput más alto alcanzado es la métrica más relevante
    max_throughput = max(all_throughputs) if all_throughputs else 0.0

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="concurrency_throughput",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=sum(NIVELES_USUARIOS),
        success_rate=(
            statistics.mean([n["success_rate"] for n in niveles_exitosos])
            if niveles_exitosos else 0.0
        ),
        latency_mean_s=ultimo_exitoso["latency_mean_s"] if ultimo_exitoso else 0.0,
        latency_p50_s=ultimo_exitoso["latency_p50_s"] if ultimo_exitoso else 0.0,
        latency_p90_s=ultimo_exitoso["latency_p90_s"] if ultimo_exitoso else 0.0,
        tokens_per_second=max_throughput,
        max_ram_mb=0.0,
        details={
            "test_type": "concurrency_throughput",
            "niveles": resultados_niveles,
            "tps_baseline_1user": round(tps_baseline, 1) if tps_baseline else 0.0,
            "max_throughput_tokens_s": round(max_throughput, 1),
            "punto_saturacion_usuarios": punto_saturacion,
            "niveles_evaluados": NIVELES_USUARIOS,
        },
    )

    print(f"--- RESULTADO JSON ---")
    print(result.to_json())
    return result


# --- Ejecución standalone ---
if __name__ == "__main__":
    import argparse
    import os
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description="Test de Concurrencia y Throughput — Velvet")
    parser.add_argument("--backend", default="ollama", choices=["ollama", "vertex"],
                        help="Backend de inferencia (default: ollama)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Nombre del modelo")
    parser.add_argument("--vertex-project", default=os.environ.get("VERTEX_PROJECT", ""),
                        help="ID del proyecto GCP (o env VERTEX_PROJECT)")
    parser.add_argument("--vertex-endpoint-id", default=os.environ.get("VERTEX_ENDPOINT_ID", ""),
                        help="ID del endpoint de Vertex AI (o env VERTEX_ENDPOINT_ID)")
    parser.add_argument("--vertex-region", default=os.environ.get("VERTEX_REGION", "us-central1"),
                        help="Región de GCP (default: us-central1)")
    parser.add_argument("--output-dir", default="./results", help="Directorio de salida")
    args = parser.parse_args()

    config = None
    if args.backend == "vertex":
        from test.models import VertexConfig
        config = VertexConfig(
            project=args.vertex_project,
            region=args.vertex_region,
            endpoint_id=args.vertex_endpoint_id,
        )

    adapter = create_adapter(args.backend, config)
    result = run_test(adapter, args.model)

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(args.output_dir, f"concurrency_{args.backend}_{args.model}_{ts}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    print(f"\nResultado guardado: {filepath}")
