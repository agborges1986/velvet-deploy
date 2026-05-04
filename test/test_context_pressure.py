"""
Test de Capacidad de Contexto: "Context Pressure"

Evalúa cómo se degrada el rendimiento a medida que crece la ventana
de contexto. El Velvet-14B soporta 128k tokens, pero llenar ese
contexto requiere mucha memoria para el KV Cache.

Métricas:
  - Prefill Time: Tiempo que tarda en procesar el contexto antes de generar.
  - TPS bajo carga de contexto: Velocidad de generación con contexto lleno.
  - Degradación: Ratio de TPS entre contexto mínimo y máximo.

Niveles de contexto: 2k, 4k, 8k, 16k, 32k tokens.
(64k y 128k solo si el hardware lo soporta — requiere timeout extendido)

Soporta ejecución directa o vía test runner / pytest con BackendAdapter.
"""

import statistics
import time
from datetime import datetime, timezone
from typing import List, Tuple

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes ---
ITERACIONES_POR_NIVEL = 5
DEFAULT_MODEL = "velvet-legal"

# Niveles de contexto a evaluar (en tokens aproximados).
# Cada "token" ≈ 4 caracteres en español/italiano con tokenizers LLaMA.
NIVELES_CONTEXTO = [
    {"label": "2k", "num_ctx": 2048, "chars_input": 6000},
    {"label": "4k", "num_ctx": 4096, "chars_input": 12000},
    {"label": "8k", "num_ctx": 8192, "chars_input": 24000},
    {"label": "16k", "num_ctx": 16384, "chars_input": 50000},
    {"label": "32k", "num_ctx": 32768, "chars_input": 100000},
]

# Bloque de texto base para construir contextos de tamaño variable.
# Texto denso sobre regulación financiera (~500 chars ≈ ~125 tokens).
_BLOQUE = (
    "La regulación financiera europea ha experimentado transformaciones significativas "
    "desde la crisis de 2008. El marco regulatorio MiFID II introdujo requisitos de "
    "transparencia para los mercados de instrumentos financieros. El GDPR estableció "
    "un nuevo paradigma en la gestión de datos personales. La Directiva PSD2 abrió el "
    "sector bancario a terceros proveedores de servicios de pago. El Banco Central "
    "Europeo consolidó la supervisión prudencial de las entidades de crédito. "
)

# Pregunta que se inserta al final del contexto (needle-in-haystack implícito)
_AGUJA = "[DATO CLAVE: El presupuesto aprobado para el proyecto Velvet es de 2.750.000 EUR] "
_PREGUNTA = (
    "\n\nInstrucción: Basándote en el documento anterior, responde: "
    "¿Cuál es el presupuesto aprobado para el proyecto Velvet? "
    "Responde solo la cifra exacta."
)

SYSTEM = "Eres un analista financiero. Extrae datos con precisión."


def _construir_contexto(chars_target: int) -> str:
    """Construye un contexto de texto del tamaño aproximado solicitado."""
    repeticiones = max(1, chars_target // len(_BLOQUE))
    texto = _BLOQUE * repeticiones

    # Insertar la aguja al 70% del texto
    pos_aguja = int(len(texto) * 0.7)
    texto = texto[:pos_aguja] + _AGUJA + texto[pos_aguja:]

    # Truncar al tamaño objetivo
    texto = texto[:chars_target]

    return texto + _PREGUNTA


def _call(
    adapter: BackendAdapter,
    model: str,
    prompt: str,
    num_ctx: int,
) -> GenerateResponse:
    """Envía solicitud con contexto controlado."""
    try:
        return adapter.generate(
            model=model,
            prompt=prompt,
            system=SYSTEM,
            options={
                "num_ctx": num_ctx,
                "num_predict": 64,
                "temperature": 0.0,
                "stop": ["<|system|>", "<|end|>"],
            },
        )
    except Exception as e:
        print(f"  [ERROR] {e}")
        return GenerateResponse(text="", latency=0.0, tokens_generated=0, tokens_per_second=0.0)


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test de presión de contexto y retorna un TestResult.

    Itera sobre niveles crecientes de contexto, midiendo latencia y TPS
    en cada nivel. Calcula la degradación relativa al nivel base (2k).
    """
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    print(f"{'='*70}")
    print(f" TEST: CONTEXT PRESSURE — Degradación por Tamaño de Contexto")
    print(f" Modelo: {model} | Niveles: {len(NIVELES_CONTEXTO)}")
    print(f" Iteraciones por nivel: {ITERACIONES_POR_NIVEL}")
    print(f"{'='*70}\n")

    # Warm-up
    print("  Ejecutando warm-up...")
    warmup(adapter, model)

    resultados_por_nivel = []
    tps_base = None  # TPS del primer nivel (2k) para calcular degradación

    for nivel in NIVELES_CONTEXTO:
        label = nivel["label"]
        num_ctx = nivel["num_ctx"]
        chars = nivel["chars_input"]

        print(f"\n  ─── Nivel: {label} (num_ctx={num_ctx}, ~{chars} chars) ───")

        contexto = _construir_contexto(chars)
        latencias = []
        tps_lista = []
        exitos = 0

        for i in range(1, ITERACIONES_POR_NIVEL + 1):
            resp = _call(adapter, model, contexto, num_ctx)

            latencias.append(resp.latency)
            if resp.tokens_per_second > 0:
                tps_lista.append(resp.tokens_per_second)

            # Verificar si encontró el dato clave
            if "2.750.000" in resp.text or "2750000" in resp.text.replace(".", "").replace(",", ""):
                exitos += 1
                status = "✅"
            else:
                status = "❌"

            print(
                f"    [{i}/{ITERACIONES_POR_NIVEL}] {status} "
                f"Latencia: {resp.latency:6.2f}s | "
                f"TPS: {resp.tokens_per_second:5.1f} | "
                f"Tokens: {resp.tokens_generated}"
            )

        # Estadísticas del nivel
        lat_mean = statistics.mean(latencias) if latencias else 0.0
        lat_p90 = percentile(latencias, 0.9) if len(latencias) >= 2 else (latencias[0] if latencias else 0.0)
        tps_mean = statistics.mean(tps_lista) if tps_lista else 0.0

        if tps_base is None and tps_mean > 0:
            tps_base = tps_mean

        degradacion = 0.0
        if tps_base and tps_base > 0 and tps_mean > 0:
            degradacion = ((tps_mean - tps_base) / tps_base) * 100

        nivel_result = {
            "label": label,
            "num_ctx": num_ctx,
            "chars_input": chars,
            "latency_mean_s": round(lat_mean, 2),
            "latency_p90_s": round(lat_p90, 2),
            "tps_mean": round(tps_mean, 1),
            "success_rate": exitos / ITERACIONES_POR_NIVEL,
            "degradation_pct": round(degradacion, 1),
            "iterations": ITERACIONES_POR_NIVEL,
        }
        resultados_por_nivel.append(nivel_result)

        print(f"    Resumen: Lat={lat_mean:.2f}s | TPS={tps_mean:.1f} | "
              f"Éxito={exitos}/{ITERACIONES_POR_NIVEL} | Δ={degradacion:+.1f}%")

    # ─── Resumen final ───────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f" RESUMEN: CONTEXT PRESSURE — {model}")
    print(f"{'='*70}")
    print(f"\n  {'Contexto':<10s} {'Latencia':>10s} {'TPS':>8s} {'Éxito':>8s} {'Degradación':>12s}")
    print(f"  {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*12}")
    for r in resultados_por_nivel:
        print(
            f"  {r['label']:<10s} {r['latency_mean_s']:>9.2f}s {r['tps_mean']:>7.1f} "
            f"{r['success_rate']:>7.0%} {r['degradation_pct']:>+11.1f}%"
        )
    print()

    # ─── Construir TestResult ────────────────────────────────────────────
    # Métricas globales: promedios ponderados de todos los niveles
    all_latencies = [r["latency_mean_s"] for r in resultados_por_nivel if r["latency_mean_s"] > 0]
    all_tps = [r["tps_mean"] for r in resultados_por_nivel if r["tps_mean"] > 0]
    all_success = [r["success_rate"] for r in resultados_por_nivel]

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="context_pressure",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=ITERACIONES_POR_NIVEL * len(NIVELES_CONTEXTO),
        success_rate=statistics.mean(all_success) if all_success else 0.0,
        latency_mean_s=statistics.mean(all_latencies) if all_latencies else 0.0,
        latency_p50_s=percentile(all_latencies, 0.5) if len(all_latencies) >= 2 else (all_latencies[0] if all_latencies else 0.0),
        latency_p90_s=percentile(all_latencies, 0.9) if len(all_latencies) >= 2 else (all_latencies[-1] if all_latencies else 0.0),
        tokens_per_second=statistics.mean(all_tps) if all_tps else 0.0,
        max_ram_mb=0.0,
        details={
            "test_type": "context_pressure",
            "niveles": resultados_por_nivel,
            "tps_base_2k": round(tps_base, 1) if tps_base else 0.0,
            "max_degradation_pct": min(r["degradation_pct"] for r in resultados_por_nivel) if resultados_por_nivel else 0.0,
        },
    )

    print(f"--- RESULTADO JSON ---")
    print(result.to_json())
    return result


# --- Ejecución standalone ---
if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Test de Context Pressure — Velvet")
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
    filepath = os.path.join(args.output_dir, f"context_pressure_{args.backend}_{args.model}_{ts}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    print(f"\nResultado guardado: {filepath}")
