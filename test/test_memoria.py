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


def call_velvet(adapter: BackendAdapter, model: str, prompt: str, system_msg: str) -> tuple:
    """
    Envía una solicitud de generación usando el adaptador de backend.

    Retorna una tupla (texto_respuesta, latencia_segundos).
    """
    try:
        response: GenerateResponse = adapter.generate(
            model=model,
            prompt=prompt,
            system=system_msg,
            options={
                "num_ctx": 32768,
                "temperature": 0.0,
                "stop": ["<|system|>", "<|end|>", "</query>", "<|user|>"]
            },
        )
        return response.text, response.latency
    except Exception as e:
        print(f"Error API: {e}")
        return "", 0.0


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
    exitos_t1, exitos_t2 = 0, 0
    # Recopilar tokens por segundo para el resultado estructurado
    tps_values = []

    print(f"Comenzando ejecución de {ITERACIONES} ciclos...\n")

    for i in range(1, ITERACIONES + 1):
        print(f"--- Iteración {i}/{ITERACIONES} ---")

        # Test 1: Needle-in-haystack (contexto largo)
        resp_t1, lat_t1 = call_velvet(adapter, model, prompt_test_1, "Responde solo con la plantilla completada.")
        tiempos_t1.append(lat_t1)
        print(f"  [T1] Respuesta:\n{resp_t1.strip()}")

        if "token-xz-2026" in resp_t1.lower() and "2029" in resp_t1.lower() and "srv-lima-platinum" in resp_t1.lower():
            exitos_t1 += 1
            print("  [T1] Resultado: EXITOSO")
        else:
            print("  [T1] Resultado: FALLO")

        # Test 2: Razonamiento sobre derogación
        resp_t2, lat_t2 = call_velvet(adapter, model, prompt_test_2, "Eres un analista jurídico. Responde solo con el dato oficial.")
        tiempos_t2.append(lat_t2)
        print(f"  [T2] Respuesta: {resp_t2.strip()}")

        if "16k" in resp_t2.lower() and "128k" not in resp_t2.lower():
            exitos_t2 += 1
            print("  [T2] Resultado: EXITOSO")
        else:
            print("  [T2] Resultado: FALLO")

        print(f"  > Latencias -> T1: {lat_t1:.2f}s | T2: {lat_t2:.2f}s\n")

    # --- Salida por consola original para compatibilidad ---
    print("\n" + "="*50)
    print("RESUMEN DE AUDITORÍA PARA GERENCIA")
    print("="*50)
    print(f"Test 1 (Contexto) Éxitos: {exitos_t1}/{ITERACIONES} | Latencia Media: {statistics.mean(tiempos_t1):.2f}s")
    print(f"Test 2 (Razonamiento) Éxitos: {exitos_t2}/{ITERACIONES} | Latencia Media: {statistics.mean(tiempos_t2):.2f}s")
    print("="*50)

    # --- Construir TestResult estructurado ---
    todas_latencias = tiempos_t1 + tiempos_t2
    todas_latencias_sorted = sorted(todas_latencias)
    total_exitos = exitos_t1 + exitos_t2
    total_iteraciones = ITERACIONES * 2  # Dos sub-tests por iteración

    # Calcular percentiles
    p50_idx = int(len(todas_latencias_sorted) * 0.5)
    p90_idx = int(len(todas_latencias_sorted) * 0.9)
    latency_p50 = todas_latencias_sorted[min(p50_idx, len(todas_latencias_sorted) - 1)]
    latency_p90 = todas_latencias_sorted[min(p90_idx, len(todas_latencias_sorted) - 1)]

    # Determinar nombre del backend desde el adaptador
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="memoria",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=ITERACIONES,
        success_rate=total_exitos / total_iteraciones if total_iteraciones > 0 else 0.0,
        latency_mean_s=statistics.mean(todas_latencias) if todas_latencias else 0.0,
        latency_p50_s=latency_p50,
        latency_p90_s=latency_p90,
        tokens_per_second=statistics.mean(tps_values) if tps_values else 0.0,
        max_ram_mb=0.0,
        details={
            "sub_tests": [
                {
                    "name": "contexto",
                    "success_rate": exitos_t1 / ITERACIONES if ITERACIONES > 0 else 0.0,
                    "latency_mean_s": statistics.mean(tiempos_t1) if tiempos_t1 else 0.0,
                },
                {
                    "name": "razonamiento",
                    "success_rate": exitos_t2 / ITERACIONES if ITERACIONES > 0 else 0.0,
                    "latency_mean_s": statistics.mean(tiempos_t2) if tiempos_t2 else 0.0,
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
