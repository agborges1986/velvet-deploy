"""
Test de Eficiencia: "The Latency Gap"

Cuantifica la diferencia de rendimiento entre CPU (VM) y GPU (Vertex/H100)
en generación de texto, midiendo:
  1. Inferencia Unitaria: 20 prompts de ~1k tokens de entrada, genera 512 tokens.
     Métrica principal: TPS (Tokens Per Second).
  2. Cold Start: Tiempo desde la petición hasta que el modelo responde
     (sin warm-up previo). Crucial para arquitecturas serverless.

Soporta ejecución directa o vía test runner / pytest con BackendAdapter.
"""

import statistics
import time
from datetime import datetime, timezone

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes ---
ITERACIONES_INFERENCIA = 20
DEFAULT_MODEL = "velvet-legal"

# Prompt largo (~1k tokens de entrada) para medir inferencia sostenida.
# Texto denso sobre regulación financiera europea para generar contexto realista.
_PARRAFO_BASE = (
    "La regulación financiera europea ha experimentado transformaciones significativas "
    "desde la crisis de 2008. El marco regulatorio MiFID II, implementado en enero de 2018, "
    "introdujo requisitos de transparencia para los mercados de instrumentos financieros, "
    "incluyendo obligaciones de reporte de transacciones, límites de posiciones en derivados "
    "de materias primas y normas de gobernanza de productos. Paralelamente, el Reglamento "
    "General de Protección de Datos (GDPR) estableció un nuevo paradigma en la gestión de "
    "datos personales, con implicaciones directas para las entidades financieras que procesan "
    "información de clientes. La Directiva PSD2 abrió el sector bancario a terceros proveedores "
    "de servicios de pago, fomentando la innovación fintech mientras mantenía estándares de "
    "seguridad mediante la autenticación reforzada del cliente (SCA). El Banco Central Europeo, "
    "a través del Mecanismo Único de Supervisión (MUS), consolidó la supervisión prudencial "
    "de las entidades de crédito significativas de la zona euro, aplicando pruebas de estrés "
    "anuales y evaluaciones exhaustivas de la calidad de los activos. "
)

# Repetir para alcanzar ~1k tokens de entrada
PROMPT_LARGO = (_PARRAFO_BASE * 4) + (
    "\n\nInstrucción: Basándote en el texto anterior, elabora un análisis detallado de "
    "las interacciones entre MiFID II, GDPR y PSD2, identificando al menos 5 puntos de "
    "conflicto regulatorio y proponiendo soluciones de armonización. Estructura tu respuesta "
    "con encabezados numerados y ejemplos concretos para cada punto."
)

SYSTEM_INFERENCIA = (
    "Eres un analista regulatorio senior especializado en normativa financiera europea. "
    "Responde de forma estructurada, detallada y en español."
)

PROMPT_COLD_START = "¿Cuál es la capital de Francia? Responde en una sola palabra."
SYSTEM_COLD_START = "Responde de forma concisa."


def _call(
    adapter: BackendAdapter,
    model: str,
    prompt: str,
    system: str,
    num_predict: int = 512,
    num_ctx: int = 4096,
) -> GenerateResponse:
    """Envía una solicitud de generación con parámetros controlados."""
    try:
        return adapter.generate(
            model=model,
            prompt=prompt,
            system=system,
            options={
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": 0.7,
                "top_p": 0.9,
                "stop": ["<|system|>", "<|end|>"],
            },
        )
    except Exception as e:
        print(f"  [ERROR] {e}")
        return GenerateResponse(text="", latency=0.0, tokens_generated=0, tokens_per_second=0.0)


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test de eficiencia completo y retorna un TestResult.

    Fase 1: Inferencia unitaria (20 prompts largos, 512 tokens de salida).
    Fase 2: Cold start (sin warm-up, mide tiempo de primera respuesta).
    """
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    # ─── FASE 1: Inferencia Unitaria ─────────────────────────────────────
    print(f"{'='*60}")
    print(f" TEST: THE LATENCY GAP — Inferencia Unitaria")
    print(f" Modelo: {model} | Iteraciones: {ITERACIONES_INFERENCIA}")
    print(f" Prompt: ~1k tokens entrada | Generación: 512 tokens")
    print(f"{'='*60}\n")

    # Warm-up para que el modelo esté en memoria
    print("  Ejecutando warm-up...")
    warmup(adapter, model)

    latencias = []
    tps_lista = []
    tokens_lista = []

    for i in range(1, ITERACIONES_INFERENCIA + 1):
        resp = _call(adapter, model, PROMPT_LARGO, SYSTEM_INFERENCIA, num_predict=512)

        latencias.append(resp.latency)
        if resp.tokens_per_second > 0:
            tps_lista.append(resp.tokens_per_second)
        tokens_lista.append(resp.tokens_generated)

        print(
            f"  [{i:2d}/{ITERACIONES_INFERENCIA}] "
            f"Latencia: {resp.latency:6.2f}s | "
            f"Tokens: {resp.tokens_generated:4d} | "
            f"TPS: {resp.tokens_per_second:6.1f}"
        )

    # Estadísticas de inferencia
    lat_mean = statistics.mean(latencias) if latencias else 0.0
    lat_p50 = percentile(latencias, 0.5) if latencias else 0.0
    lat_p90 = percentile(latencias, 0.9) if latencias else 0.0
    tps_mean = statistics.mean(tps_lista) if tps_lista else 0.0
    tps_p50 = percentile(tps_lista, 0.5) if tps_lista else 0.0
    tokens_mean = statistics.mean(tokens_lista) if tokens_lista else 0.0

    print(f"\n  --- Resumen Inferencia Unitaria ---")
    print(f"  TPS medio:       {tps_mean:.1f} tokens/s")
    print(f"  TPS p50:         {tps_p50:.1f} tokens/s")
    print(f"  Latencia media:  {lat_mean:.2f}s")
    print(f"  Latencia p50:    {lat_p50:.2f}s")
    print(f"  Latencia p90:    {lat_p90:.2f}s")
    print(f"  Tokens medio:    {tokens_mean:.0f}")

    # ─── FASE 2: Cold Start ──────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f" TEST: THE LATENCY GAP — Cold Start")
    print(f"{'='*60}\n")
    print("  Nota: Esta métrica mide la latencia de la primera solicitud")
    print("  SIN warm-up previo. En un escenario real, incluye la carga")
    print("  del modelo en memoria (RAM o VRAM).\n")

    # Para medir cold start real, necesitaríamos reiniciar el servicio.
    # Como aproximación, medimos la latencia de una solicitud mínima
    # después del warm-up (que ya cargó el modelo) y la reportamos
    # como "hot start". El cold start real se documenta como N/A
    # y debe medirse manualmente reiniciando el contenedor.
    cold_start_time = time.time()
    resp_cold = _call(adapter, model, PROMPT_COLD_START, SYSTEM_COLD_START, num_predict=32, num_ctx=512)
    hot_start_latency = time.time() - cold_start_time

    print(f"  Hot start (modelo ya en memoria): {hot_start_latency:.3f}s")
    print(f"  Respuesta: {resp_cold.text.strip()[:100]}")
    print(f"\n  ⚠️  Para medir cold start real, reiniciar el servicio y")
    print(f"     medir la primera solicitud manualmente.")

    # ─── Construir TestResult ────────────────────────────────────────────
    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="latency_gap",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=ITERACIONES_INFERENCIA,
        success_rate=len(tps_lista) / ITERACIONES_INFERENCIA if ITERACIONES_INFERENCIA > 0 else 0.0,
        latency_mean_s=lat_mean,
        latency_p50_s=lat_p50,
        latency_p90_s=lat_p90,
        tokens_per_second=tps_mean,
        max_ram_mb=0.0,
        details={
            "test_type": "latency_gap",
            "prompt_input_tokens_approx": 1000,
            "generation_target_tokens": 512,
            "inferencia_unitaria": {
                "tps_mean": round(tps_mean, 1),
                "tps_p50": round(tps_p50, 1),
                "tps_min": round(min(tps_lista), 1) if tps_lista else 0.0,
                "tps_max": round(max(tps_lista), 1) if tps_lista else 0.0,
                "latency_mean_s": round(lat_mean, 2),
                "latency_p50_s": round(lat_p50, 2),
                "latency_p90_s": round(lat_p90, 2),
                "tokens_generated_mean": round(tokens_mean),
                "total_requests": ITERACIONES_INFERENCIA,
                "successful_requests": len(tps_lista),
            },
            "cold_start": {
                "hot_start_latency_s": round(hot_start_latency, 3),
                "note": "Cold start real requiere reinicio del servicio. Este valor es hot start.",
            },
        },
    )

    print(f"\n--- RESULTADO JSON ---")
    print(result.to_json())
    return result


# --- Ejecución standalone ---
if __name__ == "__main__":
    default_adapter = create_adapter("ollama")
    run_test(default_adapter, DEFAULT_MODEL)
