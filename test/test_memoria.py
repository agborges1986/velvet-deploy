"""
Test de memoria: needle-in-haystack y razonamiento sobre derogación.

Evalúa la capacidad del modelo para extraer datos insertados en un
contexto largo (PDFs) y razonar sobre cláusulas que se derogan.
Ejecuta 10 iteraciones y reporta tasa de éxito y latencia.

Soporta ejecución directa (python test/test_memoria.py) con OllamaAdapter
por defecto, o vía test runner / pytest con BackendAdapter inyectado.
"""

import fitz
import time
import os
import statistics
from datetime import datetime, timezone

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes del test ---
ITERACIONES = 10
BASE_PATH = "/opt/ai_models/velvet/test1/"
# Nombre de modelo por defecto para ejecución standalone
DEFAULT_MODEL = "velvet-legal"


def extract_text_from_pdfs(files):
    """Extrae texto de una lista de archivos PDF."""
    full_text = ""
    for file in files:
        path = os.path.join(BASE_PATH, file)
        try:
            doc = fitz.open(path)
            for page in doc:
                full_text += page.get_text()
        except Exception as e:
            print(f"Error leyendo {file}: {e}")
    return full_text


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
                "num_ctx": 32768,
                "temperature": 0.0,
                "stop": ["<|system|>", "<|end|>", "</query>", "<|user|>"]
            },
        )
    except Exception as e:
        print(f"Error API: {e}")
        return GenerateResponse(text="", latency=0.0, tokens_generated=0, tokens_per_second=0.0)


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test de memoria completo y retorna un TestResult estructurado.

    Mantiene la lógica de evaluación original intacta y la salida por consola.
    """
    archivos = [
        "Velvet _ Almawave_Página web.pdf",
        "AIWave cognitive platform _ Almawave.pdf",
        "Testes Velvet_v1.pdf"
    ]

    print("--- INICIANDO AUDITORÍA TÉCNICA (N=10) ---")
    print("Cargando documentos en memoria...")
    corpus_tecnico = extract_text_from_pdfs(archivos)
    total_len = len(corpus_tecnico)

    # Insertar agujas en posiciones específicas del corpus
    p10, p50, p90 = int(total_len * 0.1), int(total_len * 0.5), int(total_len * 0.9)
    aguja_1 = " [CONFIDENCIAL: Código de Seguridad TOKEN-XZ-2026] "
    aguja_2 = " [FECHA DE MANTENIMIENTO: 24 de diciembre de 2029] "
    aguja_3 = " [IDENTIFICADOR DE SERVIDOR: SRV-LIMA-PLATINUM] "

    texto_haystack = (corpus_tecnico[:p10] + aguja_1 +
                      corpus_tecnico[p10:p50] + aguja_2 +
                      corpus_tecnico[p50:p90] + aguja_3 +
                      corpus_tecnico[p90:])

    prompt_test_1 = f"""Documento:
{texto_haystack}

Instrucción: Eres un extractor de datos analítico. Busca en el texto y completa EXACTAMENTE la siguiente plantilla con los valores encontrados. No agregues texto extra ni explicaciones:
- Código de Seguridad:
- Fecha de Mantenimiento:
- Identificador de Servidor:
"""

    prompt_test_2 = """FRAGMENTO A: Velvet-14B tiene una ventana de contexto de 128k tokens.
FRAGMENTO B (Anexo): Se deroga el Fragmento A; la capacidad vigente para este servidor es de 16k tokens.

Pregunta: Según el anexo vigente, ¿cuál es la capacidad de contexto para este servidor? Responde solo la cifra exacta."""

    tiempos_t1, tiempos_t2 = [], []
    tps_t1, tps_t2 = [], []
    exitos_t1, exitos_t2 = 0, 0

    # Warm-up: ejecutar una solicitud descartable para evitar cold-start
    print("Ejecutando warm-up del modelo...")
    warmup(adapter, model)

    print(f"Comenzando ejecución de {ITERACIONES} ciclos...\n")

    for i in range(1, ITERACIONES + 1):
        print(f"--- Iteración {i}/{ITERACIONES} ---")

        # Test 1: Needle-in-haystack (contexto largo)
        resp_t1 = call_velvet(adapter, model, prompt_test_1, "Responde solo con la plantilla completada.")
        tiempos_t1.append(resp_t1.latency)
        if resp_t1.tokens_per_second > 0:
            tps_t1.append(resp_t1.tokens_per_second)
        print(f"  [T1] Respuesta:\n{resp_t1.text.strip()}")

        if "token-xz-2026" in resp_t1.text.lower() and "2029" in resp_t1.text.lower() and "srv-lima-platinum" in resp_t1.text.lower():
            exitos_t1 += 1
            print("  [T1] Resultado: EXITOSO")
        else:
            print("  [T1] Resultado: FALLO")

        # Test 2: Razonamiento sobre derogación
        resp_t2 = call_velvet(adapter, model, prompt_test_2, "Eres un analista jurídico. Responde solo con el dato oficial.")
        tiempos_t2.append(resp_t2.latency)
        if resp_t2.tokens_per_second > 0:
            tps_t2.append(resp_t2.tokens_per_second)
        print(f"  [T2] Respuesta: {resp_t2.text.strip()}")

        if "16k" in resp_t2.text.lower() and "128k" not in resp_t2.text.lower():
            exitos_t2 += 1
            print("  [T2] Resultado: EXITOSO")
        else:
            print("  [T2] Resultado: FALLO")

        print(f"  > Latencias -> T1: {resp_t1.latency:.2f}s | T2: {resp_t2.latency:.2f}s\n")

    # --- Salida por consola original para compatibilidad ---
    print("\n" + "="*50)
    print("RESUMEN DE AUDITORÍA PARA GERENCIA")
    print("="*50)
    print(f"Test 1 (Contexto) Éxitos: {exitos_t1}/{ITERACIONES} | Latencia Media: {statistics.mean(tiempos_t1):.2f}s")
    print(f"Test 2 (Razonamiento) Éxitos: {exitos_t2}/{ITERACIONES} | Latencia Media: {statistics.mean(tiempos_t2):.2f}s")
    print("="*50)

    # --- Construir TestResult estructurado ---
    # Calcular TPS promedio de todas las respuestas válidas
    all_tps = tps_t1 + tps_t2
    avg_tps = statistics.mean(all_tps) if all_tps else 0.0

    total_exitos = exitos_t1 + exitos_t2
    total_llamadas = ITERACIONES * 2  # Dos sub-tests por iteración

    # Percentiles calculados POR SUB-TEST (no mezclados) y luego
    # se reporta el peor caso (máximo) como métrica conservadora del test.
    # Esto evita mezclar distribuciones heterogéneas.
    latency_p50_t1 = percentile(tiempos_t1, 0.5) if tiempos_t1 else 0.0
    latency_p90_t1 = percentile(tiempos_t1, 0.9) if tiempos_t1 else 0.0
    latency_p50_t2 = percentile(tiempos_t2, 0.5) if tiempos_t2 else 0.0
    latency_p90_t2 = percentile(tiempos_t2, 0.9) if tiempos_t2 else 0.0

    # Latencia media ponderada: promedio de las medias de cada sub-test
    mean_t1 = statistics.mean(tiempos_t1) if tiempos_t1 else 0.0
    mean_t2 = statistics.mean(tiempos_t2) if tiempos_t2 else 0.0
    latency_mean = (mean_t1 + mean_t2) / 2

    # Determinar nombre del backend desde el adaptador
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="memoria",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=ITERACIONES,
        success_rate=total_exitos / total_llamadas if total_llamadas > 0 else 0.0,
        latency_mean_s=latency_mean,
        latency_p50_s=max(latency_p50_t1, latency_p50_t2),
        latency_p90_s=max(latency_p90_t1, latency_p90_t2),
        tokens_per_second=avg_tps,
        max_ram_mb=0.0,
        details={
            "total_requests": total_llamadas,
            "sub_tests": [
                {
                    "name": "contexto",
                    "success_rate": exitos_t1 / ITERACIONES if ITERACIONES > 0 else 0.0,
                    "latency_mean_s": mean_t1,
                    "latency_p50_s": latency_p50_t1,
                    "latency_p90_s": latency_p90_t1,
                    "tokens_per_second": statistics.mean(tps_t1) if tps_t1 else 0.0,
                },
                {
                    "name": "razonamiento",
                    "success_rate": exitos_t2 / ITERACIONES if ITERACIONES > 0 else 0.0,
                    "latency_mean_s": mean_t2,
                    "latency_p50_s": latency_p50_t2,
                    "latency_p90_s": latency_p90_t2,
                    "tokens_per_second": statistics.mean(tps_t2) if tps_t2 else 0.0,
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
    import argparse

    parser = argparse.ArgumentParser(description="Test de Memoria — Velvet")
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
    filepath = os.path.join(args.output_dir, f"memoria_{args.backend}_{args.model}_{ts}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(result.to_json())
    print(f"\nResultado guardado: {filepath}")
