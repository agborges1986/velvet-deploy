#!/usr/bin/env python3
"""
test_idioma.py — Test de Adherencia Lingüística: Italiano vs Español

Envía los mismos prompts en italiano y español a los modelos Velvet,
evaluando:
  - Adherencia lingüística: ¿responde en el idioma solicitado?
  - Calidad de respuesta: ¿la respuesta es coherente y completa?
  - Latencia: ¿hay diferencia de rendimiento entre idiomas?

Uso:
    python test/test_idioma.py \
        --url http://localhost:8080 \
        --model velvet-14b-cpu-v1 \
        --auth admin:VelvetSecure2025!
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Prompts paralelos: mismo contenido en italiano y español
# ---------------------------------------------------------------------------

PROMPTS = [
    {
        "id": "economia",
        "italiano": {
            "prompt": "Spiega in 5 punti i principali tipi di domanda economica che genera lo Stato italiano.",
            "system": "Sei un economista esperto. Rispondi in italiano in modo strutturato.",
        },
        "espanol": {
            "prompt": "Explica en 5 puntos los principales tipos de demanda económica que genera el Estado italiano.",
            "system": "Eres un economista experto. Responde en español de forma estructurada.",
        },
    },
    {
        "id": "finanzas",
        "italiano": {
            "prompt": "Calcola gli interessi composti di un investimento di 10.000 EUR al tasso del 5% annuo per 2 anni. Mostra il calcolo passo a passo.",
            "system": "Sei un analista finanziario preciso. Rispondi in italiano mostrando ogni passaggio.",
        },
        "espanol": {
            "prompt": "Calcula los intereses compuestos de una inversión de 10.000 EUR a una tasa del 5% anual por 2 años. Muestra el cálculo paso a paso.",
            "system": "Eres un analista financiero preciso. Responde en español mostrando cada paso.",
        },
    },
    {
        "id": "tecnologia",
        "italiano": {
            "prompt": "Quali sono i 5 principali vantaggi dell'intelligenza artificiale nel settore bancario? Fornisci esempi concreti.",
            "system": "Sei un consulente tecnologico. Rispondi in italiano in modo dettagliato.",
        },
        "espanol": {
            "prompt": "¿Cuáles son las 5 principales ventajas de la inteligencia artificial en el sector bancario? Proporciona ejemplos concretos.",
            "system": "Eres un consultor tecnológico. Responde en español de forma detallada.",
        },
    },
    {
        "id": "legal",
        "italiano": {
            "prompt": "Riassumi i principali diritti dei consumatori secondo la normativa europea (GDPR e Direttiva sui diritti dei consumatori).",
            "system": "Sei un avvocato specializzato in diritto europeo. Rispondi in italiano.",
        },
        "espanol": {
            "prompt": "Resume los principales derechos de los consumidores según la normativa europea (GDPR y Directiva de derechos de los consumidores).",
            "system": "Eres un abogado especializado en derecho europeo. Responde en español.",
        },
    },
    {
        "id": "resumen",
        "italiano": {
            "prompt": "Scrivi un riassunto di 3 paragrafi sulla storia dell'Unione Europea, dalle origini fino ad oggi.",
            "system": "Sei uno storico esperto. Rispondi in italiano in modo chiaro e conciso.",
        },
        "espanol": {
            "prompt": "Escribe un resumen de 3 párrafos sobre la historia de la Unión Europea, desde sus orígenes hasta hoy.",
            "system": "Eres un historiador experto. Responde en español de forma clara y concisa.",
        },
    },
]


# ---------------------------------------------------------------------------
# Detección de idioma simple
# ---------------------------------------------------------------------------

# Palabras frecuentes exclusivas de cada idioma
MARCADORES_ITALIANO = [
    "è", "sono", "della", "delle", "degli", "nell", "attraverso",
    "inoltre", "può", "questo", "anche", "essere", "stato", "ogni",
    "perché", "quindi", "tuttavia", "oppure", "esempio", "tramite",
]
MARCADORES_ESPANOL = [
    "es", "son", "del", "los", "las", "además", "puede", "esto",
    "también", "ser", "estado", "cada", "porque", "por lo tanto",
    "sin embargo", "ejemplo", "mediante", "través", "permite", "como",
]
MARCADORES_INGLES = [
    "the", "is", "are", "was", "were", "have", "has", "been",
    "with", "that", "this", "from", "which", "through", "however",
    "therefore", "because", "also", "such", "would", "could",
]


def detectar_idioma(texto: str) -> str:
    """Detecta el idioma predominante del texto."""
    texto_lower = texto.lower()
    palabras = re.findall(r'\b\w+\b', texto_lower)

    if not palabras:
        return "vacio"

    conteo_it = sum(1 for p in palabras if p in MARCADORES_ITALIANO)
    conteo_es = sum(1 for p in palabras if p in MARCADORES_ESPANOL)
    conteo_en = sum(1 for p in palabras if p in MARCADORES_INGLES)

    total = len(palabras)
    ratio_it = conteo_it / total
    ratio_es = conteo_es / total
    ratio_en = conteo_en / total

    if ratio_it > ratio_es and ratio_it > ratio_en:
        return "italiano"
    elif ratio_es > ratio_it and ratio_es > ratio_en:
        return "espanol"
    elif ratio_en > ratio_it and ratio_en > ratio_es:
        return "ingles"
    elif ratio_it == ratio_es == ratio_en == 0:
        return "indeterminado"
    else:
        return "mixto"


# ---------------------------------------------------------------------------
# Envío de solicitud
# ---------------------------------------------------------------------------

def enviar_prompt(
    url: str,
    model: str,
    prompt: str,
    system: str,
    auth: Optional[Tuple[str, str]] = None,
) -> dict:
    """Envía un prompt y retorna la respuesta con métricas."""
    import requests

    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.3, "top_p": 0.9},
    }

    start = time.time()
    try:
        r = requests.post(f"{url}/api/generate", json=payload, auth=auth, timeout=300)
        latency = time.time() - start
        if r.status_code == 200:
            data = r.json()
            texto = data.get("response", "")
            tokens = data.get("eval_count", 0)
            return {
                "success": True,
                "text": texto,
                "tokens": tokens,
                "latency_s": round(latency, 2),
                "idioma_detectado": detectar_idioma(texto),
                "longitud_chars": len(texto),
            }
    except Exception as e:
        latency = time.time() - start
        return {
            "success": False,
            "text": "",
            "tokens": 0,
            "latency_s": round(latency, 2),
            "idioma_detectado": "error",
            "longitud_chars": 0,
            "error": str(e),
        }

    return {
        "success": False,
        "text": "",
        "tokens": 0,
        "latency_s": round(latency, 2),
        "idioma_detectado": "error",
        "longitud_chars": 0,
        "error": f"HTTP {r.status_code}",
    }


# ---------------------------------------------------------------------------
# Ejecución del test
# ---------------------------------------------------------------------------

def run_test(url: str, model: str, auth: Optional[Tuple[str, str]] = None) -> dict:
    """Ejecuta todos los prompts en ambos idiomas y genera el reporte."""

    resultados = []

    print(f"\n{'='*90}")
    print(f" TEST DE ADHERENCIA LINGÜÍSTICA: {model}")
    print(f" Prompts: {len(PROMPTS)} | Idiomas: Italiano, Español")
    print(f"{'='*90}\n")

    for i, prompt_pair in enumerate(PROMPTS, 1):
        pid = prompt_pair["id"]
        print(f"--- Prompt {i}/{len(PROMPTS)}: {pid.upper()} ---\n")

        resultado = {"prompt_id": pid}

        for idioma in ["italiano", "espanol"]:
            cfg = prompt_pair[idioma]
            idioma_label = "IT" if idioma == "italiano" else "ES"

            print(f"  [{idioma_label}] Enviando prompt...")
            resp = enviar_prompt(url, model, cfg["prompt"], cfg["system"], auth)

            # Evaluar adherencia
            idioma_esperado = idioma
            idioma_real = resp["idioma_detectado"]
            adherencia = idioma_real == idioma_esperado

            # Evaluar si la respuesta es sustancial (>50 chars)
            respuesta_util = resp["longitud_chars"] > 50

            resultado[idioma] = {
                "idioma_solicitado": idioma_esperado,
                "idioma_detectado": idioma_real,
                "adherencia": adherencia,
                "respuesta_util": respuesta_util,
                "tokens": resp["tokens"],
                "latency_s": resp["latency_s"],
                "longitud_chars": resp["longitud_chars"],
                "texto_preview": resp["text"][:150].replace("\n", " "),
            }

            status = "✅" if adherencia and respuesta_util else "⚠️" if respuesta_util else "❌"
            print(f"  [{idioma_label}] {status} Idioma: {idioma_real} | "
                  f"Tokens: {resp['tokens']} | Latencia: {resp['latency_s']}s | "
                  f"Chars: {resp['longitud_chars']}")
            print(f"  [{idioma_label}] Preview: {resp['text'][:120].replace(chr(10), ' ')}")
            print()

        resultados.append(resultado)

    # --- Calcular métricas agregadas ---
    it_adherencia = sum(1 for r in resultados if r.get("italiano", {}).get("adherencia", False))
    es_adherencia = sum(1 for r in resultados if r.get("espanol", {}).get("adherencia", False))
    it_util = sum(1 for r in resultados if r.get("italiano", {}).get("respuesta_util", False))
    es_util = sum(1 for r in resultados if r.get("espanol", {}).get("respuesta_util", False))

    it_latencias = [r["italiano"]["latency_s"] for r in resultados if "italiano" in r]
    es_latencias = [r["espanol"]["latency_s"] for r in resultados if "espanol" in r]
    it_tokens = [r["italiano"]["tokens"] for r in resultados if "italiano" in r]
    es_tokens = [r["espanol"]["tokens"] for r in resultados if "espanol" in r]

    total = len(PROMPTS)

    # --- Imprimir resumen ---
    print(f"{'='*90}")
    print(f" RESUMEN — {model}")
    print(f"{'='*90}")
    print()
    print(f"  {'Métrica':<35s} {'Italiano':>12s} {'Español':>12s}")
    print(f"  {'─'*35} {'─'*12} {'─'*12}")
    print(f"  {'Adherencia lingüística':<35s} {it_adherencia}/{total}{'':>8s} {es_adherencia}/{total}{'':>8s}")
    print(f"  {'Respuestas útiles (>50 chars)':<35s} {it_util}/{total}{'':>8s} {es_util}/{total}{'':>8s}")
    print(f"  {'Latencia media':<35s} {statistics.mean(it_latencias):>11.2f}s {statistics.mean(es_latencias):>11.2f}s")
    print(f"  {'Tokens generados (media)':<35s} {statistics.mean(it_tokens):>11.0f} {statistics.mean(es_tokens):>11.0f}")
    print(f"  {'Tokens generados (total)':<35s} {sum(it_tokens):>11d} {sum(es_tokens):>11d}")
    print()

    # Tabla detallada por prompt
    print(f"  {'Prompt':<12s} {'IT Idioma':<12s} {'IT Tokens':>10s} {'IT Lat':>8s} {'ES Idioma':<12s} {'ES Tokens':>10s} {'ES Lat':>8s}")
    print(f"  {'─'*12} {'─'*12} {'─'*10} {'─'*8} {'─'*12} {'─'*10} {'─'*8}")
    for r in resultados:
        it = r.get("italiano", {})
        es = r.get("espanol", {})
        print(f"  {r['prompt_id']:<12s} {it.get('idioma_detectado','?'):<12s} {it.get('tokens',0):>10d} {it.get('latency_s',0):>7.1f}s {es.get('idioma_detectado','?'):<12s} {es.get('tokens',0):>10d} {es.get('latency_s',0):>7.1f}s")

    print(f"\n{'='*90}\n")

    # --- Construir resultado JSON ---
    report = {
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_prompts": total,
        "resumen": {
            "italiano": {
                "adherencia": f"{it_adherencia}/{total}",
                "respuestas_utiles": f"{it_util}/{total}",
                "latencia_media_s": round(statistics.mean(it_latencias), 2),
                "tokens_media": round(statistics.mean(it_tokens)),
                "tokens_total": sum(it_tokens),
            },
            "espanol": {
                "adherencia": f"{es_adherencia}/{total}",
                "respuestas_utiles": f"{es_util}/{total}",
                "latencia_media_s": round(statistics.mean(es_latencias), 2),
                "tokens_media": round(statistics.mean(es_tokens)),
                "tokens_total": sum(es_tokens),
            },
        },
        "detalle": resultados,
    }

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Test de Adherencia Lingüística: Italiano vs Español para Velvet",
    )
    parser.add_argument("--url", required=True, help="URL base de la API")
    parser.add_argument("--model", required=True, help="Modelo a evaluar o 'all'")
    parser.add_argument("--auth", default=None, help="Credenciales user:password")
    parser.add_argument("--output-dir", default="./results", help="Directorio de salida")
    args = parser.parse_args()

    auth = None
    if args.auth:
        parts = args.auth.split(":", 1)
        if len(parts) == 2:
            auth = (parts[0], parts[1])

    os.makedirs(args.output_dir, exist_ok=True)

    models = ["velvet-2b-cpu-v1", "velvet-14b-cpu-v1"] if args.model.lower() == "all" else [args.model]

    for model in models:
        report = run_test(args.url, model, auth)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(args.output_dir, f"idioma_{model}_{ts}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  Resultado guardado: {filepath}\n")


if __name__ == "__main__":
    main()
