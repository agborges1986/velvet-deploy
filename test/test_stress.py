"""
Test de estrés: concurrencia de usuarios y telemetría de hardware.

Simula 15 usuarios concurrentes enviando solicitudes al modelo y
monitorea el uso de CPU y RAM del sistema durante la prueba.
Reporta latencia por usuario, tasa de éxito y métricas de hardware.

Soporta ejecución directa (python test/test_stress.py) con OllamaAdapter
por defecto, o vía test runner / pytest con BackendAdapter inyectado.
"""

import time
import threading
import statistics
import psutil
from datetime import datetime, timezone

from test.adapters import BackendAdapter, create_adapter
from test.models import GenerateResponse, TestResult
from test.stats_utils import percentile, warmup


# --- Constantes del test ---
NUM_USUARIOS_CONCURRENTES = 15
# Nombre de modelo por defecto para ejecución standalone
DEFAULT_MODEL = "velvet-legal"

PROMPT = "FRAGMENTO A: Límite 128k. FRAGMENTO B: Se deroga A, el límite es 16k. ¿Cuál es el límite oficial?"


def run_test(adapter: BackendAdapter, model: str) -> TestResult:
    """
    Ejecuta el test de estrés completo y retorna un TestResult estructurado.

    Mantiene la lógica de evaluación original intacta y la salida por consola.
    """
    # Variables compartidas entre hilos
    resultados = []  # lista de dicts con latencia, tps, éxito
    lock = threading.Lock()
    test_en_ejecucion = True
    # Métricas de hardware recopiladas por el monitor
    max_cpu = 0.0
    max_ram_mb = 0.0

    def peticion_usuario(id_usuario):
        """Envía una solicitud al modelo usando el adaptador de backend."""
        try:
            response: GenerateResponse = adapter.generate(
                model=model,
                prompt=PROMPT,
                system="Responde solo la cifra.",
                options={"num_ctx": 2048, "temperature": 0.0},
            )

            exito = "16k" in response.text.lower()
            with lock:
                resultados.append({
                    "latency": response.latency,
                    "tps": response.tokens_per_second,
                    "success": exito,
                })
                if exito:
                    print(f"  [Usuario {id_usuario}] ✅ OK en {response.latency:.2f}s")
                else:
                    print(f"  [Usuario {id_usuario}] ❌ FALLO")
        except Exception as e:
            with lock:
                resultados.append({
                    "latency": 0.0,
                    "tps": 0.0,
                    "success": False,
                })
            print(f"  [Usuario {id_usuario}] 🚨 ERROR: {e}")

    def monitor_hardware():
        """Monitorea el uso de CPU y RAM durante la ejecución del test."""
        nonlocal max_cpu, max_ram_mb
        print("\n  [TELEMETRÍA] Iniciando sensores de hardware...")
        while test_en_ejecucion:
            cpu_uso = psutil.cpu_percent(interval=1)
            ram_info = psutil.virtual_memory()
            ram_uso = ram_info.percent
            ram_used_mb = ram_info.used / (1024 * 1024)

            # Registrar máximos
            with lock:
                if cpu_uso > max_cpu:
                    max_cpu = cpu_uso
                if ram_used_mb > max_ram_mb:
                    max_ram_mb = ram_used_mb

            print(f"  [SYS] CPU Carga: {cpu_uso}% | RAM Uso: {ram_uso}%")
            time.sleep(2)

    print(f"--- INICIANDO TEST DE ESTRÉS: {NUM_USUARIOS_CONCURRENTES} USUARIOS SIMULTÁNEOS ---")

    # Warm-up: ejecutar una solicitud descartable para evitar cold-start
    print("Ejecutando warm-up del modelo...")
    warmup(adapter, model)

    # Iniciar monitor de hardware en hilo separado
    monitor_thread = threading.Thread(target=monitor_hardware)
    monitor_thread.start()

    # Crear hilos de usuarios concurrentes
    hilos_usuarios = []
    for i in range(NUM_USUARIOS_CONCURRENTES):
        t = threading.Thread(target=peticion_usuario, args=(i+1,))
        hilos_usuarios.append(t)

    # Lanzar todos los hilos simultáneamente
    start_test = time.time()
    for t in hilos_usuarios:
        t.start()

    # Esperar a que todos los usuarios terminen
    for t in hilos_usuarios:
        t.join()

    tiempo_total_operacion = time.time() - start_test

    # Detener monitor de hardware
    test_en_ejecucion = False
    monitor_thread.join()

    # --- Extraer datos de resultados (post-join, thread-safe) ---
    resultados_latencia = [r["latency"] for r in resultados if r["latency"] > 0]
    resultados_tps = [r["tps"] for r in resultados if r["tps"] > 0]
    exitos = sum(1 for r in resultados if r["success"])

    # --- Salida por consola original para compatibilidad ---
    print("\n" + "="*50)
    print("REPORTE DE CONCURRENCIA Y ESTRÉS")
    print("="*50)
    print(f"Usuarios simultáneos: {NUM_USUARIOS_CONCURRENTES}")
    print(f"Tasa de éxito: {exitos}/{NUM_USUARIOS_CONCURRENTES}")
    print(f"Tiempo total de la ráfaga: {tiempo_total_operacion:.2f}s")
    if resultados_latencia:
        print(f"Latencia promedio por usuario: {statistics.mean(resultados_latencia):.2f}s")
        print(f"Petición más rápida: {min(resultados_latencia):.2f}s")
        print(f"Petición más lenta: {max(resultados_latencia):.2f}s")
    print("="*50)

    # --- Construir TestResult estructurado ---
    # Percentiles con interpolación correcta
    latency_p50 = percentile(resultados_latencia, 0.5) if resultados_latencia else 0.0
    latency_p90 = percentile(resultados_latencia, 0.9) if resultados_latencia else 0.0
    avg_tps = statistics.mean(resultados_tps) if resultados_tps else 0.0

    # Determinar nombre del backend desde el adaptador
    backend_name = type(adapter).__name__.replace("Adapter", "").lower()

    result = TestResult(
        backend=backend_name,
        model=model,
        test_name="stress",
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=NUM_USUARIOS_CONCURRENTES,
        success_rate=exitos / NUM_USUARIOS_CONCURRENTES if NUM_USUARIOS_CONCURRENTES > 0 else 0.0,
        latency_mean_s=statistics.mean(resultados_latencia) if resultados_latencia else 0.0,
        latency_p50_s=latency_p50,
        latency_p90_s=latency_p90,
        tokens_per_second=avg_tps,
        max_ram_mb=max_ram_mb,
        details={
            "concurrent_users": NUM_USUARIOS_CONCURRENTES,
            "total_burst_time_s": tiempo_total_operacion,
            "max_cpu_percent": max_cpu,
            "max_ram_mb": max_ram_mb,
            "fastest_request_s": min(resultados_latencia) if resultados_latencia else 0.0,
            "slowest_request_s": max(resultados_latencia) if resultados_latencia else 0.0,
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
