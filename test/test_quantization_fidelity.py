"""
Test de Calidad y Cuantización: "Quantization Fidelity"

Evalúa cuánto "conocimiento" se pierde al cuantizar el modelo para CPU
comparando las respuestas de ambos backends:
  - Vertex AI (BF16/FP16): Se usa como Ground Truth (máxima precisión).
  - Ollama (Q4_K_M): Versión cuantizada para CPU.

Métricas:
  - Similitud textual (Jaccard sobre tokens de palabras)
  - Consistencia semántica (¿ambos llegan a la misma conclusión?)
  - Longitud relativa de respuesta
  - Calidad percibida (heurísticas de estructura)

NOTA: Este test requiere que AMBOS backends estén disponibles.
Si solo hay un backend, el test se ejecuta en modo "single" y reporta
métricas de calidad sin comparación.

Soporta ejecución directa o vía test runner / pytest con BackendAdapter.
"""

import re
import statistics
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes ---
DEFAULT_MODEL = "velvet-legal"

# Prompts de evaluación: cubren diferentes capacidades cognitivas
PROMPTS_EVALUACION = [
    {
        "id": "razonamiento_logico",
        "prompt": (
            "Si todos los mamíferos son vertebrados, y todos los perros son mamíferos, "
            "¿son todos los perros vertebrados? Explica tu razonamiento paso a paso."
        ),
        "system": "Eres un profesor de lógica. Responde de forma estructurada.",
        "criterio": "vertebrados",  # Palabra clave esperada en la conclusión
    },
    {
        "id": "calculo_financiero",
        "prompt": (
            "Calcula el monto final de una inversión de $10,000 USD a una tasa de "
            "interés compuesto del 5% anual, al cabo de 3 años. Muestra cada paso."
        ),
        "system": "Eres un analista financiero preciso. Muestra tu razonamiento.",
        "criterio": "11576",  # $10,000 × 1.05³ = $11,576.25
    },
    {
        "id": "extraccion_datos",
        "prompt": (
            "Documento: La empresa TechCorp S.A., fundada en 2015, tiene sede en Madrid. "
            "Cuenta con 450 empleados y facturó 28.5 millones de euros en 2025. Su CEO "
            "es María García López.\n\n"
            "Extrae los siguientes datos en formato estructurado:\n"
            "- Razón social\n- Año de fundación\n- Sede\n- Empleados\n- Facturación\n- CEO"
        ),
        "system": "Eres un sistema de extracción de datos. Sé preciso.",
        "criterio": "TechCorp",
    },
    {
        "id": "resumen_complejo",
        "prompt": (
            "Resume en exactamente 3 puntos las ventajas de la inteligencia artificial "
            "en el sector salud, incluyendo un ejemplo concreto para cada punto."
        ),
        "system": "Eres un consultor de salud digital. Responde en español.",
        "criterio": None,  # Evaluación por estructura (3 puntos)
    },
    {
        "id": "traduccion_tecnica",
        "prompt": (
            "Traduce al italiano el siguiente texto técnico manteniendo la terminología "
            "especializada:\n\n"
            "'The transformer architecture uses self-attention mechanisms to process "
            "sequential data in parallel, enabling efficient training on large datasets. "
            "The key innovation is the multi-head attention layer that allows the model "
            "to attend to different representation subspaces simultaneously.'"
        ),
        "system": "Eres un traductor técnico especializado en IA. Traduce al italiano.",
        "criterio": "attenzione",  # Palabra clave en italiano
    },
]


def _tokenize_simple(text: str) -> set:
    """Tokenización simple: palabras en minúsculas, sin puntuación."""
    return set(re.findall(r'\b\w+\b', text.lower()))


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Calcula la similitud de Jaccard entre dos textos."""
    tokens_a = _tokenize_simple(text_a)
    tokens_b = _tokenize_simple(text_b)

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    interseccion = tokens_a & tokens_b
    union = tokens_a | tokens_b

    return len(interseccion) / len(union)


def _evaluar_estructura(text: str) -> float:
    """
    Evalúa la calidad estructural de una respuesta (0.0 a 1.0).

    Heurísticas:
    - ¿Tiene párrafos o puntos numerados?
    - ¿Longitud razonable (>100 chars)?
    - ¿No es repetitivo?
    """
    score = 0.0

    # Longitud mínima
    if len(text) > 100:
        score += 0.3
    elif len(text) > 50:
        score += 0.15

    # Estructura (puntos numerados, viñetas, párrafos)
    if re.search(r'[1-5]\.|[-•]', text):
        score += 0.3

    # Párrafos múltiples
    parrafos = [p.strip() for p in text.split('\n') if p.strip()]
    if len(parrafos) >= 2:
        score += 0.2

    # No repetitivo (ratio de palabras únicas)
    palabras = re.findall(r'\b\w+\b', text.lower())
    if palabras:
        ratio_unicas = len(set(palabras)) / len(palabras)
        if ratio_unicas > 0.4:
            score += 0.2

    return min(score, 1.0)


def _call(adapter: BackendAdapter, model: str, prompt: str, system: str) -> GenerateResponse:
    """Envía solicitud de generación."""
    try:
        return adapter.generate(
            model=model,
            prompt=prompt,
            system=system,
            options={
                "num_ctx": 4096,
                "num_predict": 512,
                "temperature": 0.0,  # Determinístico para comparabilidad
                "stop": ["<|system|>", "<|end|>"],
            },
        )
    except Exception as e:
        print(f"  [ERROR] {e}")
        return GenerateResponse(text="", latency=0.0, tokens_generated=0, tokens_per_second=0.0)


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test de fidelidad de cuantización.

    En modo single-backend: evalúa calidad de respuestas del backend disponible.
    En modo dual-backend: compara respuestas entre backends (requiere configuración
    manual de ambos adaptadores — ver ejecución standalone).
    """
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    print(f"{'='*70}")
    print(f" TEST: QUANTIZATION FIDELITY — Calidad de Respuestas")
    print(f" Modelo: {model} | Backend: {backend_name}")
    print(f" Prompts: {len(PROMPTS_EVALUACION)}")
    print(f"{'='*70}\n")

    # Warm-up
    print("  Ejecutando warm-up...")
    warmup(adapter, model)

    resultados_prompt = []
    latencias = []
    tps_lista = []

    for idx, prompt_cfg in enumerate(PROMPTS_EVALUACION, 1):
        pid = prompt_cfg["id"]
        print(f"\n  ─── Prompt {idx}/{len(PROMPTS_EVALUACION)}: {pid} ───")

        resp = _call(adapter, model, prompt_cfg["prompt"], prompt_cfg["system"])
        latencias.append(resp.latency)
        if resp.tokens_per_second > 0:
            tps_lista.append(resp.tokens_per_second)

        # Evaluar criterio de éxito
        criterio_ok = False
        if prompt_cfg["criterio"]:
            criterio_ok = prompt_cfg["criterio"].lower() in resp.text.lower()
        else:
            # Para prompts sin criterio textual, evaluar estructura
            criterio_ok = _evaluar_estructura(resp.text) >= 0.5

        # Evaluar calidad estructural
        calidad = _evaluar_estructura(resp.text)

        resultado = {
            "prompt_id": pid,
            "criterio_cumplido": criterio_ok,
            "calidad_estructura": round(calidad, 2),
            "longitud_chars": len(resp.text),
            "tokens_generated": resp.tokens_generated,
            "latency_s": round(resp.latency, 2),
            "tps": round(resp.tokens_per_second, 1),
            "preview": resp.text[:200].replace("\n", " "),
        }
        resultados_prompt.append(resultado)

        status = "✅" if criterio_ok else "❌"
        print(f"    {status} Criterio: {'OK' if criterio_ok else 'FALLO'} | "
              f"Calidad: {calidad:.0%} | Lat: {resp.latency:.2f}s | "
              f"TPS: {resp.tokens_per_second:.1f}")
        print(f"    Preview: {resp.text[:120].replace(chr(10), ' ')}")

    # ─── Resumen ─────────────────────────────────────────────────────────
    criterios_ok = sum(1 for r in resultados_prompt if r["criterio_cumplido"])
    calidad_media = statistics.mean([r["calidad_estructura"] for r in resultados_prompt])
    total = len(PROMPTS_EVALUACION)

    print(f"\n{'='*70}")
    print(f" RESUMEN: QUANTIZATION FIDELITY — {model} ({backend_name})")
    print(f"{'='*70}")
    print(f"  Criterios cumplidos:  {criterios_ok}/{total} ({criterios_ok/total:.0%})")
    print(f"  Calidad estructural:  {calidad_media:.0%}")
    print(f"  Latencia media:       {statistics.mean(latencias):.2f}s")
    print(f"  TPS medio:            {statistics.mean(tps_lista):.1f}" if tps_lista else "  TPS medio: N/A")
    print()

    # ─── Construir TestResult ────────────────────────────────────────────
    lat_mean = statistics.mean(latencias) if latencias else 0.0

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="quantization_fidelity",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=total,
        success_rate=criterios_ok / total if total > 0 else 0.0,
        latency_mean_s=lat_mean,
        latency_p50_s=percentile(latencias, 0.5) if len(latencias) >= 2 else lat_mean,
        latency_p90_s=percentile(latencias, 0.9) if len(latencias) >= 2 else lat_mean,
        tokens_per_second=statistics.mean(tps_lista) if tps_lista else 0.0,
        max_ram_mb=0.0,
        details={
            "test_type": "quantization_fidelity",
            "mode": "single_backend",
            "calidad_estructura_media": round(calidad_media, 2),
            "criterios_cumplidos": f"{criterios_ok}/{total}",
            "prompts": resultados_prompt,
        },
    )

    print(f"--- RESULTADO JSON ---")
    print(result.to_json())
    return result


def run_comparison(
    adapter_ground_truth: BackendAdapter,
    adapter_quantized: BackendAdapter,
    model_gt: str,
    model_q: str,
) -> Dict:
    """
    Ejecuta comparación dual entre Ground Truth (Vertex/FP16) y
    cuantizado (Ollama/Q4_K_M).

    Retorna un diccionario con métricas de similitud por prompt.
    Diseñado para ejecución standalone (no integrado en el runner).
    """
    print(f"{'='*70}")
    print(f" COMPARACIÓN: Ground Truth vs Cuantizado")
    print(f" GT: {model_gt} | Q: {model_q}")
    print(f"{'='*70}\n")

    warmup(adapter_ground_truth, model_gt)
    warmup(adapter_quantized, model_q)

    comparaciones = []

    for idx, prompt_cfg in enumerate(PROMPTS_EVALUACION, 1):
        pid = prompt_cfg["id"]
        print(f"\n  ─── Prompt {idx}: {pid} ───")

        resp_gt = _call(adapter_ground_truth, model_gt, prompt_cfg["prompt"], prompt_cfg["system"])
        resp_q = _call(adapter_quantized, model_q, prompt_cfg["prompt"], prompt_cfg["system"])

        # Similitud de Jaccard
        jaccard = _jaccard_similarity(resp_gt.text, resp_q.text)

        # Consistencia de criterio
        criterio = prompt_cfg["criterio"]
        if criterio:
            gt_ok = criterio.lower() in resp_gt.text.lower()
            q_ok = criterio.lower() in resp_q.text.lower()
            consistencia = gt_ok == q_ok
        else:
            consistencia = True

        # Ratio de longitud
        len_ratio = len(resp_q.text) / len(resp_gt.text) if len(resp_gt.text) > 0 else 0.0

        comp = {
            "prompt_id": pid,
            "jaccard_similarity": round(jaccard, 3),
            "criterio_consistente": consistencia,
            "longitud_ratio": round(len_ratio, 2),
            "gt_latency_s": round(resp_gt.latency, 2),
            "q_latency_s": round(resp_q.latency, 2),
            "gt_tps": round(resp_gt.tokens_per_second, 1),
            "q_tps": round(resp_q.tokens_per_second, 1),
        }
        comparaciones.append(comp)

        print(f"    Jaccard: {jaccard:.3f} | Consistente: {'✅' if consistencia else '❌'} | "
              f"Len ratio: {len_ratio:.2f}")

    # Resumen
    jaccard_mean = statistics.mean([c["jaccard_similarity"] for c in comparaciones])
    consistencia_total = sum(1 for c in comparaciones if c["criterio_consistente"])

    print(f"\n{'='*70}")
    print(f" RESUMEN COMPARACIÓN")
    print(f"{'='*70}")
    print(f"  Similitud Jaccard media: {jaccard_mean:.3f}")
    print(f"  Consistencia de criterio: {consistencia_total}/{len(comparaciones)}")
    print()

    return {
        "comparaciones": comparaciones,
        "jaccard_mean": round(jaccard_mean, 3),
        "consistencia": f"{consistencia_total}/{len(comparaciones)}",
    }


# --- Ejecución standalone ---
if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Test de Quantization Fidelity — Velvet")
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
    parser.add_argument("--compare", action="store_true",
                        help="Modo comparación: Vertex (GT) vs Ollama (Q4_K_M)")
    args = parser.parse_args()

    if args.compare:
        # Modo comparación: requiere ambos backends configurados
        print("Modo comparación: Vertex (GT) vs Ollama (Q4_K_M)")
        from test.models import VertexConfig
        gt_config = VertexConfig(
            project=args.vertex_project,
            region=args.vertex_region,
            endpoint_id=args.vertex_endpoint_id,
        )
        gt_adapter = create_adapter("vertex", gt_config)
        q_adapter = create_adapter("ollama")
        run_comparison(gt_adapter, q_adapter, "velvet-14b", "velvet-14b-cpu-v1")
    else:
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
        filepath = os.path.join(args.output_dir, f"quantization_{args.backend}_{args.model}_{ts}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(result.to_json())
        print(f"\nResultado guardado: {filepath}")
