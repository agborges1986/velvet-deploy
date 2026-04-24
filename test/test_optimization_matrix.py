#!/usr/bin/env python3
"""
test_optimization_matrix.py — Matriz de Optimización para Velvet 14B

Ejecuta una serie de tests variando NUM_PARALLEL, NUM_THREAD, num_ctx y
usuarios concurrentes para encontrar la configuración óptima de la
infraestructura EC2 c6i.8xlarge (32 vCPU / 16 cores físicos).

Requiere ejecutarse en la EC2 con acceso a docker compose.

Uso:
    python3 test/test_optimization_matrix.py \
        --url http://localhost:8080 \
        --auth admin:VelvetSecure2025! \
        --compose-dir /home/ec2-user/velvet-project/infra \
        --output-dir ./results/optimization
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuración de la matriz de tests
# ---------------------------------------------------------------------------

TESTS = [
    {"id": "T1", "num_parallel": 1,  "num_thread": 32, "users": 1, "num_ctx": 4096, "desc": "Baseline: 1 usuario, todos los cores"},
    {"id": "T2", "num_parallel": 2,  "num_thread": 16, "users": 2, "num_ctx": 4096, "desc": "2 paralelas x 16 threads"},
    {"id": "T3", "num_parallel": 4,  "num_thread": 8,  "users": 4, "num_ctx": 4096, "desc": "Config actual (referencia)"},
    {"id": "T4", "num_parallel": 8,  "num_thread": 4,  "users": 8, "num_ctx": 4096, "desc": "Alta concurrencia"},
    {"id": "T5", "num_parallel": 4,  "num_thread": 8,  "users": 8, "num_ctx": 4096, "desc": "Sobrecarga 2x sobre config actual"},
    {"id": "T6", "num_parallel": 4,  "num_thread": 8,  "users": 4, "num_ctx": 2048, "desc": "Contexto reducido"},
    {"id": "T7", "num_parallel": 4,  "num_thread": 8,  "users": 4, "num_ctx": 8192, "desc": "Contexto amplio"},
]

MODEL = "velvet-14b-cpu-v1"

PROMPT = (
    "Descrivi in modo dettagliato i principali vantaggi dell'intelligenza "
    "artificiale applicata al servizio clienti nelle aziende di telecomunicazioni. "
    "Includi almeno 5 punti specifici con esempi pratici."
)
SYSTEM = (
    "Sei un consulente esperto di intelligenza artificiale e telecomunicazioni. "
    "Rispondi in modo strutturato e professionale in italiano."
)


# ---------------------------------------------------------------------------
# Funciones de infraestructura
# ---------------------------------------------------------------------------

def update_ollama_config(compose_dir: str, num_parallel: int, num_thread: int) -> bool:
    """Actualiza las variables de entorno de Ollama en docker-compose.yml."""
    compose_file = os.path.join(compose_dir, "docker-compose.yml")

    with open(compose_file, "r") as f:
        content = f.read()

    # Reemplazar NUM_PARALLEL
    content = re.sub(
        r"OLLAMA_NUM_PARALLEL=\d+",
        f"OLLAMA_NUM_PARALLEL={num_parallel}",
        content,
    )
    # Reemplazar NUM_THREAD
    content = re.sub(
        r"OLLAMA_NUM_THREAD=\d+",
        f"OLLAMA_NUM_THREAD={num_thread}",
        content,
    )

    with open(compose_file, "w") as f:
        f.write(content)

    return True


def restart_ollama(compose_dir: str) -> bool:
    """Reinicia el stack Docker Compose y espera a que Ollama esté healthy."""
    print("    Reiniciando Ollama...")
    result = subprocess.run(
        ["docker", "compose", "down"],
        cwd=compose_dir,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=compose_dir,
        capture_output=True,
        text=True,
    )

    # Esperar a que Ollama esté healthy
    for i in range(60):
        check = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", "velvet-ollama"],
            capture_output=True,
            text=True,
        )
        if "healthy" in check.stdout:
            print("    Ollama healthy.")
            # Esperar 5s adicionales para estabilización
            time.sleep(5)
            return True
        time.sleep(5)

    print("    WARN: Ollama no alcanzó estado healthy en 5 minutos.")
    return False


# ---------------------------------------------------------------------------
# Función de benchmark (simplificada del test_benchmark_stress.py)
# ---------------------------------------------------------------------------

def run_benchmark(
    url: str,
    model: str,
    users: int,
    num_ctx: int,
    auth: Optional[Tuple[str, str]] = None,
) -> dict:
    """Ejecuta benchmark con N usuarios concurrentes."""
    import requests as req

    results = []
    lock = threading.Lock()

    def worker(uid):
        payload = {
            "model": model,
            "prompt": PROMPT,
            "system": SYSTEM,
            "stream": True,
            "options": {"num_ctx": num_ctx, "temperature": 0.7, "top_p": 0.9},
        }

        ttft = 0.0
        total_text = ""
        tokens = 0

        start = time.time()
        try:
            r = req.post(f"{url}/api/generate", json=payload, auth=auth, timeout=600, stream=True)
            r.raise_for_status()

            first_token = False
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if not first_token and chunk.get("response", ""):
                    ttft = time.time() - start
                    first_token = True
                total_text += chunk.get("response", "")
                if chunk.get("done", False):
                    tokens = chunk.get("eval_count", 0)
                    break

            latency = time.time() - start
            if tokens == 0 and total_text:
                tokens = max(1, len(total_text) // 4)
            tps = tokens / latency if latency > 0 and tokens > 0 else 0
            if ttft == 0:
                ttft = latency

            with lock:
                results.append({
                    "uid": uid, "success": True, "ttft": ttft,
                    "latency": latency, "tokens": tokens, "tps": tps,
                })
        except Exception as e:
            latency = time.time() - start
            with lock:
                results.append({
                    "uid": uid, "success": False, "ttft": 0,
                    "latency": latency, "tokens": 0, "tps": 0, "error": str(e),
                })

    threads = [threading.Thread(target=worker, args=(i+1,)) for i in range(users)]
    start_all = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_duration = time.time() - start_all

    ok = [r for r in results if r["success"]]
    if not ok:
        return {
            "success": False, "throughput": 0, "latency_mean": 0,
            "ttft_mean": 0, "latency_p50": 0, "latency_p90": 0,
            "ttft_p50": 0, "ttft_p90": 0, "total_tokens": 0,
            "total_duration": total_duration, "tps_per_user": 0,
            "successful": 0, "failed": len(results),
        }

    lats = sorted([r["latency"] for r in ok])
    ttfts = sorted([r["ttft"] for r in ok])
    total_tokens = sum(r["tokens"] for r in ok)

    def pct(data, p):
        """Calcula percentil con interpolación lineal (método estándar)."""
        n = len(data)
        if n == 1:
            return data[0]
        pos = p * (n - 1)
        lower_idx = int(pos)
        upper_idx = min(lower_idx + 1, n - 1)
        fraction = pos - lower_idx
        return data[lower_idx] + fraction * (data[upper_idx] - data[lower_idx])

    return {
        "success": True,
        "throughput": round(total_tokens / total_duration, 1) if total_duration > 0 else 0,
        "latency_mean": round(statistics.mean(lats), 2),
        "ttft_mean": round(statistics.mean(ttfts), 3),
        "latency_p50": round(pct(lats, 0.5), 2),
        "latency_p90": round(pct(lats, 0.9), 2),
        "ttft_p50": round(pct(ttfts, 0.5), 3),
        "ttft_p90": round(pct(ttfts, 0.9), 3),
        "total_tokens": total_tokens,
        "total_duration": round(total_duration, 2),
        "tps_per_user": round(statistics.mean([r["tps"] for r in ok]), 1),
        "successful": len(ok),
        "failed": len(results) - len(ok),
    }


# ---------------------------------------------------------------------------
# Generador de informe
# ---------------------------------------------------------------------------

def generate_report(all_results: list, output_dir: str) -> str:
    """Genera el informe Markdown con los resultados de la matriz."""

    lines = []
    lines.append("# Informe de Optimización — Velvet 14B en EC2 c6i.8xlarge")
    lines.append("")
    lines.append(f"**Fecha:** {datetime.now().strftime('%d de %B de %Y')}")
    lines.append(f"**Hardware:** EC2 c6i.8xlarge (32 vCPU / 16 cores físicos, 64 GB RAM)")
    lines.append(f"**Modelo:** {MODEL} (Q4_K_M, 8.5 GB)")
    lines.append(f"**Engine:** Ollama 0.21.2")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Matriz de Tests Ejecutados")
    lines.append("")
    lines.append("| Test | NUM_PARALLEL | NUM_THREAD | Usuarios | num_ctx | Descripción |")
    lines.append("|------|-------------|------------|----------|---------|-------------|")
    for t in TESTS:
        lines.append(f"| {t['id']} | {t['num_parallel']} | {t['num_thread']} | {t['users']} | {t['num_ctx']} | {t['desc']} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Tabla de resultados
    lines.append("## Resultados")
    lines.append("")
    lines.append("| Test | Throughput (T/s) | Latencia Media | TTFT | Lat p90 | TTFT p90 | TPS/usuario | Tokens |")
    lines.append("|------|-----------------|----------------|------|---------|----------|-------------|--------|")

    best_throughput = {"id": "", "val": 0}
    best_ttft = {"id": "", "val": 999999}
    best_latency = {"id": "", "val": 999999}
    best_tps_user = {"id": "", "val": 0}

    for r in all_results:
        t = r["test"]
        m = r["metrics"]
        if not m["success"]:
            lines.append(f"| {t['id']} | ❌ FALLO | — | — | — | — | — | — |")
            continue

        lines.append(
            f"| {t['id']} | **{m['throughput']}** | {m['latency_mean']}s | "
            f"{m['ttft_mean']}s | {m['latency_p90']}s | {m['ttft_p90']}s | "
            f"{m['tps_per_user']} | {m['total_tokens']} |"
        )

        if m["throughput"] > best_throughput["val"]:
            best_throughput = {"id": t["id"], "val": m["throughput"]}
        if m["ttft_mean"] < best_ttft["val"] and m["ttft_mean"] > 0:
            best_ttft = {"id": t["id"], "val": m["ttft_mean"]}
        if m["latency_mean"] < best_latency["val"] and m["latency_mean"] > 0:
            best_latency = {"id": t["id"], "val": m["latency_mean"]}
        if m["tps_per_user"] > best_tps_user["val"]:
            best_tps_user = {"id": t["id"], "val": m["tps_per_user"]}

    lines.append("")
    lines.append("---")
    lines.append("")

    # Mejores configuraciones
    lines.append("## Configuración Óptima por Métrica")
    lines.append("")
    lines.append(f"| Métrica | Mejor Test | Valor | Configuración |")
    lines.append(f"|---------|-----------|-------|---------------|")

    for label, best in [
        ("Mayor Throughput (T/s)", best_throughput),
        ("Menor TTFT", best_ttft),
        ("Menor Latencia Media", best_latency),
        ("Mayor TPS por Usuario", best_tps_user),
    ]:
        test_cfg = next((t for t in TESTS if t["id"] == best["id"]), None)
        if test_cfg:
            cfg_str = f"P={test_cfg['num_parallel']}, T={test_cfg['num_thread']}, U={test_cfg['users']}, ctx={test_cfg['num_ctx']}"
            val_str = f"{best['val']}s" if "TTFT" in label or "Latencia" in label else str(best['val'])
            lines.append(f"| {label} | **{best['id']}** | **{val_str}** | {cfg_str} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Análisis
    lines.append("## Análisis")
    lines.append("")

    # Comparar T1 vs T3 (baseline vs actual)
    t1 = next((r["metrics"] for r in all_results if r["test"]["id"] == "T1" and r["metrics"]["success"]), None)
    t3 = next((r["metrics"] for r in all_results if r["test"]["id"] == "T3" and r["metrics"]["success"]), None)

    if t1 and t3:
        lines.append("### Baseline (T1) vs Config Actual (T3)")
        lines.append("")
        lines.append(f"- T1 (1 usuario, 32 threads): {t1['tps_per_user']} TPS/usuario, TTFT {t1['ttft_mean']}s")
        lines.append(f"- T3 (4 usuarios, 8 threads): {t3['tps_per_user']} TPS/usuario, TTFT {t3['ttft_mean']}s")
        lines.append(f"- Throughput agregado: T1={t1['throughput']} T/s vs T3={t3['throughput']} T/s")
        if t3['throughput'] > 0:
            ratio = t3['throughput'] / t1['throughput'] if t1['throughput'] > 0 else 0
            lines.append(f"- La concurrencia de T3 genera **{ratio:.1f}x** más throughput agregado")
        lines.append("")

    # Comparar T3 vs T4 (actual vs alta concurrencia)
    t4 = next((r["metrics"] for r in all_results if r["test"]["id"] == "T4" and r["metrics"]["success"]), None)
    if t3 and t4:
        lines.append("### Config Actual (T3) vs Alta Concurrencia (T4)")
        lines.append("")
        lines.append(f"- T3 (P=4, T=8, 4 users): Throughput {t3['throughput']} T/s, TTFT {t3['ttft_mean']}s")
        lines.append(f"- T4 (P=8, T=4, 8 users): Throughput {t4['throughput']} T/s, TTFT {t4['ttft_mean']}s")
        if t4['throughput'] > t3['throughput']:
            lines.append(f"- Alta concurrencia mejora throughput en {((t4['throughput']/t3['throughput'])-1)*100:.0f}%")
        else:
            lines.append(f"- Alta concurrencia reduce throughput en {((1-t4['throughput']/t3['throughput']))*100:.0f}% (contención de CPU)")
        lines.append("")

    # Comparar T6 vs T3 vs T7 (contexto)
    t6 = next((r["metrics"] for r in all_results if r["test"]["id"] == "T6" and r["metrics"]["success"]), None)
    t7 = next((r["metrics"] for r in all_results if r["test"]["id"] == "T7" and r["metrics"]["success"]), None)
    if t6 and t3 and t7:
        lines.append("### Impacto de Ventana de Contexto (T6 vs T3 vs T7)")
        lines.append("")
        lines.append(f"- T6 (ctx=2048): Throughput {t6['throughput']} T/s, TTFT {t6['ttft_mean']}s")
        lines.append(f"- T3 (ctx=4096): Throughput {t3['throughput']} T/s, TTFT {t3['ttft_mean']}s")
        lines.append(f"- T7 (ctx=8192): Throughput {t7['throughput']} T/s, TTFT {t7['ttft_mean']}s")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Recomendación")
    lines.append("")
    lines.append(f"La configuración óptima para Velvet-14B en EC2 c6i.8xlarge es **{best_throughput['id']}** ")
    lines.append(f"si se prioriza throughput agregado, o **{best_tps_user['id']}** si se prioriza ")
    lines.append(f"rendimiento por usuario individual.")
    lines.append("")

    report_text = "\n".join(lines)

    report_path = os.path.join(output_dir, "INFORME_OPTIMIZACION.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Matriz de optimización para Velvet 14B")
    parser.add_argument("--url", required=True)
    parser.add_argument("--auth", default=None)
    parser.add_argument("--compose-dir", required=True, help="Ruta al directorio con docker-compose.yml")
    parser.add_argument("--output-dir", default="./results/optimization")
    args = parser.parse_args()

    auth = None
    if args.auth:
        parts = args.auth.split(":", 1)
        if len(parts) == 2:
            auth = (parts[0], parts[1])

    os.makedirs(args.output_dir, exist_ok=True)

    all_results = []

    print(f"\n{'='*80}")
    print(f" MATRIZ DE OPTIMIZACIÓN — {MODEL}")
    print(f" Tests: {len(TESTS)} | Hardware: EC2 c6i.8xlarge (32 vCPU)")
    print(f"{'='*80}\n")

    for i, test in enumerate(TESTS, 1):
        print(f"\n{'─'*80}")
        print(f" TEST {test['id']} ({i}/{len(TESTS)}): {test['desc']}")
        print(f" NUM_PARALLEL={test['num_parallel']}, NUM_THREAD={test['num_thread']}, "
              f"Users={test['users']}, num_ctx={test['num_ctx']}")
        print(f"{'─'*80}\n")

        # Actualizar config y reiniciar solo si cambió NUM_PARALLEL o NUM_THREAD
        prev = TESTS[i-2] if i > 1 else None
        if prev is None or prev["num_parallel"] != test["num_parallel"] or prev["num_thread"] != test["num_thread"]:
            print(f"    Actualizando config: P={test['num_parallel']}, T={test['num_thread']}")
            update_ollama_config(args.compose_dir, test["num_parallel"], test["num_thread"])
            restart_ollama(args.compose_dir)
        else:
            print(f"    Config sin cambios, reutilizando Ollama actual.")

        # Ejecutar benchmark
        print(f"    Ejecutando benchmark con {test['users']} usuarios...")
        metrics = run_benchmark(args.url, MODEL, test["users"], test["num_ctx"], auth)

        # Mostrar resultado
        if metrics["success"]:
            print(f"\n    ✅ Throughput: {metrics['throughput']} T/s | "
                  f"Latencia: {metrics['latency_mean']}s | "
                  f"TTFT: {metrics['ttft_mean']}s | "
                  f"TPS/user: {metrics['tps_per_user']}")
        else:
            print(f"\n    ❌ Test fallido")

        result = {"test": test, "metrics": metrics}
        all_results.append(result)

        # Guardar resultado individual
        result_path = os.path.join(args.output_dir, f"{test['id']}_result.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"    Guardado: {result_path}")

    # Restaurar config original (T3)
    print(f"\n{'─'*80}")
    print(f" Restaurando configuración original (P=4, T=8)...")
    update_ollama_config(args.compose_dir, 4, 8)
    restart_ollama(args.compose_dir)

    # Generar informe
    report_path = generate_report(all_results, args.output_dir)
    print(f"\n{'='*80}")
    print(f" MATRIZ COMPLETADA — Informe: {report_path}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
