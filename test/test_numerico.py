"""
Test numérico: cálculo financiero y extracción JSON estructurado.

Evalúa la precisión aritmética del modelo (interés compuesto) y su
capacidad para generar JSON válido con datos extraídos de un documento.
Ejecuta 10 iteraciones y reporta tasa de éxito y latencia.

Soporta ejecución directa (python test/test_numerico.py) con OllamaAdapter
por defecto, o vía test runner / pytest con BackendAdapter inyectado.
"""

import time
import json
import statistics
from datetime import datetime, timezone

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes del test ---
ITERACIONES = 10
# Nombre de modelo por defecto para ejecución standalone
DEFAULT_MODEL = "velvet-legal"


def call_velvet(adapter: BackendAdapter, model: str, prompt: str, system_msg: str) -> GenerateResponse:
    """
    Envía una solicitud de generación usando el adaptador de backend.

    Retorna el GenerateResponse completo (texto, latencia, tokens, TPS).
    """
    try:
        return adapter.generate(
            model=model,
            prompt=prompt,
            system=system_msg,
            options={
                "num_ctx": 4096,
                "temperature": 0.0,
                "stop": ["<|system|>", "<|end|>", "</query>", "<|user|>"]
            },
        )
    except Exception as e:
        print(f"Error API: {e}")
        return GenerateResponse(text="", latency=0.0, tokens_generated=0, tokens_per_second=0.0)


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test numérico completo y retorna un TestResult estructurado.

    Mantiene la lógica de evaluación original intacta y la salida por consola.
    """
    # --- Prompts originales ---
    prompt_math = """
Calcula el monto final de una inversión de $10,000 USD a una tasa de interés compuesto del 5% anual, al cabo de 2 años.
Muestra el cálculo paso a paso y al final escribe el resultado exacto.
(Fórmula: Monto = Capital * (1 + tasa)^tiempo)
"""

    texto_corporativo = """
Almawave S.p.A. es una empresa líder listada en la Bolsa de Milán.
Cuenta con las certificaciones ISO 9001, ISO 14001 y recientemente ISO/IEC 42001.
Ofrece al mercado las plataformas AI BOXES: Comprehension, Conversation, y Speech & Voice.
"""
    prompt_json = f"""
Documento:
{texto_corporativo}

Instrucción estricta: Extrae los datos del documento en formato JSON.
Las claves exactas deben ser "razon_social", "certificaciones" (como un array) y "plataformas" (como un array).
IMPORTANTE: Comienza tu respuesta directamente con la llave {{ y termina con la llave }}. NO uses bloques de código markdown (```json). NO agregues texto antes ni después.
"""

    tiempos_t3, tiempos_t4 = [], []
    tps_t3, tps_t4 = [], []
    exitos_t3, exitos_t4 = 0, 0

    # Warm-up: ejecutar una solicitud descartable para evitar cold-start
    print("Ejecutando warm-up del modelo...")
    warmup(adapter, model)

    print(f"--- INICIANDO AUDITORÍA DE PRECISIÓN LÓGICA (N={ITERACIONES}) ---")

    for i in range(1, ITERACIONES + 1):
        print(f"\n--- Iteración {i}/{ITERACIONES} ---")

        # Test 3: Cálculo financiero (interés compuesto)
        resp_t3 = call_velvet(adapter, model, prompt_math, "Eres un analista financiero preciso. Muestra tu razonamiento paso a paso.")
        tiempos_t3.append(resp_t3.latency)
        if resp_t3.tokens_per_second > 0:
            tps_t3.append(resp_t3.tokens_per_second)

        print(f"  [LOG T3] Respuesta cruda:\n{resp_t3.text.strip()}\n")

        r3_limpio = resp_t3.text.replace(",", "").replace(".", "")
        if "11025" in r3_limpio:
            exitos_t3 += 1
            print("  [LOG T3] ¡ÉXITO! Cálculo matemático correcto.")
        else:
            print("  [LOG T3] FALLO. Error aritmético o de razonamiento.")

        # Test 4: Extracción JSON estructurado
        resp_t4 = call_velvet(adapter, model, prompt_json, "Eres un sistema de backend. Tu salida es estrictamente un objeto JSON.")
        tiempos_t4.append(resp_t4.latency)
        if resp_t4.tokens_per_second > 0:
            tps_t4.append(resp_t4.tokens_per_second)

        respuesta_json = resp_t4.text.strip()
        if respuesta_json.startswith("```json"):
            respuesta_json = respuesta_json[7:]
        if respuesta_json.startswith("```"):
            respuesta_json = respuesta_json[3:]
        if respuesta_json.endswith("```"):
            respuesta_json = respuesta_json[:-3]

        print(f"  [LOG T4] Respuesta cruda:\n{respuesta_json.strip()}\n")

        try:
            data = json.loads(respuesta_json.strip())
            if "Almawave" in str(data.get("razon_social", "")):
                exitos_t4 += 1
                print("  [LOG T4] ¡ÉXITO! JSON válido y listo para automatización.")
            else:
                print("  [LOG T4] FALLO. JSON válido pero datos incorrectos.")
        except json.JSONDecodeError:
            print("  [LOG T4] FALLO CRÍTICO. El modelo inyectó basura textual y rompió el JSON.")

        print(f"  > Latencias -> T3 (Math): {resp_t3.latency:.2f}s | T4 (JSON): {resp_t4.latency:.2f}s")

    # --- Salida por consola original para compatibilidad ---
    print("\n" + "="*50)
    print("REPORTE EJECUTIVO - CATEGORÍA 2: PRECISIÓN Y LÓGICA")
    print("="*50)
    print(f"Test 3 (Cálculo Financiero) Éxitos: {exitos_t3}/{ITERACIONES} | Latencia Media: {statistics.mean(tiempos_t3):.2f}s")
    print(f"Test 4 (Estructura JSON)    Éxitos: {exitos_t4}/{ITERACIONES} | Latencia Media: {statistics.mean(tiempos_t4):.2f}s")
    print("="*50)

    # --- Construir TestResult estructurado ---
    # Calcular TPS promedio de todas las respuestas válidas
    all_tps = tps_t3 + tps_t4
    avg_tps = statistics.mean(all_tps) if all_tps else 0.0

    total_exitos = exitos_t3 + exitos_t4
    total_llamadas = ITERACIONES * 2

    # Percentiles calculados POR SUB-TEST (no mezclados)
    latency_p50_t3 = percentile(tiempos_t3, 0.5) if tiempos_t3 else 0.0
    latency_p90_t3 = percentile(tiempos_t3, 0.9) if tiempos_t3 else 0.0
    latency_p50_t4 = percentile(tiempos_t4, 0.5) if tiempos_t4 else 0.0
    latency_p90_t4 = percentile(tiempos_t4, 0.9) if tiempos_t4 else 0.0

    # Latencia media ponderada: promedio de las medias de cada sub-test
    mean_t3 = statistics.mean(tiempos_t3) if tiempos_t3 else 0.0
    mean_t4 = statistics.mean(tiempos_t4) if tiempos_t4 else 0.0
    latency_mean = (mean_t3 + mean_t4) / 2

    # Determinar nombre del backend desde el adaptador
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="numerico",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=ITERACIONES,
        success_rate=total_exitos / total_llamadas if total_llamadas > 0 else 0.0,
        latency_mean_s=latency_mean,
        latency_p50_s=max(latency_p50_t3, latency_p50_t4),
        latency_p90_s=max(latency_p90_t3, latency_p90_t4),
        tokens_per_second=avg_tps,
        max_ram_mb=0.0,
        details={
            "total_requests": total_llamadas,
            "sub_tests": [
                {
                    "name": "calculo_financiero",
                    "success_rate": exitos_t3 / ITERACIONES if ITERACIONES > 0 else 0.0,
                    "latency_mean_s": mean_t3,
                    "latency_p50_s": latency_p50_t3,
                    "latency_p90_s": latency_p90_t3,
                    "tokens_per_second": statistics.mean(tps_t3) if tps_t3 else 0.0,
                },
                {
                    "name": "estructura_json",
                    "success_rate": exitos_t4 / ITERACIONES if ITERACIONES > 0 else 0.0,
                    "latency_mean_s": mean_t4,
                    "latency_p50_s": latency_p50_t4,
                    "latency_p90_s": latency_p90_t4,
                    "tokens_per_second": statistics.mean(tps_t4) if tps_t4 else 0.0,
                },
            ]
        },
    )

    # Imprimir resultado JSON estructurado
    print("\n--- RESULTADO JSON ---")
    print(result.to_json())

    return result


# --- Ejecución standalone ---
if __name__ == "__main__":
    # Crear adaptador por defecto (Ollama) para ejecución directa
    default_adapter = create_adapter("ollama")
    run_test(default_adapter, DEFAULT_MODEL)
