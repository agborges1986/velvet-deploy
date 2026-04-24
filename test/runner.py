"""
Orquestador de ejecución de tests para la suite Velvet.

Acepta parámetros CLI para seleccionar backend, modelo, directorio de salida
y subconjunto de tests a ejecutar. Verifica disponibilidad del backend antes
de ejecutar, guarda resultados JSON individuales por test y muestra un resumen
al finalizar.

Uso:
    python -m test.runner --backend ollama --model velvet-2b-cpu-v1 --output-dir ./results/
    python -m test.runner --backend vertex --model velvet-14b --tests memoria,seguridad
"""

import argparse
import os
import sys
import json
from datetime import datetime, timezone

from test.adapters import BackendAdapter, create_adapter
from test.models import TestResult


# --- Tests disponibles y sus módulos correspondientes ---
TESTS_DISPONIBLES = {
    "memoria": "test.test_memoria",
    "numerico": "test.test_numerico",
    "seguridad": "test.test_seguridad",
    "stress": "test.test_stress",
}


def _generar_timestamp() -> str:
    """Genera un timestamp compacto para nombres de archivo."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _guardar_resultado(result: TestResult, output_dir: str, timestamp: str) -> str:
    """
    Guarda el resultado de un test como archivo JSON en el directorio de salida.

    Nomenclatura: {backend}_{modelo}_{test}_{timestamp}.json

    Retorna la ruta del archivo guardado.
    """
    # Sanitizar nombre del modelo para uso en nombre de archivo
    modelo_safe = result.model.replace("/", "_").replace("\\", "_")
    nombre_archivo = f"{result.backend}_{modelo_safe}_{result.test_name}_{timestamp}.json"
    ruta_archivo = os.path.join(output_dir, nombre_archivo)

    with open(ruta_archivo, "w", encoding="utf-8") as f:
        f.write(result.to_json())

    return ruta_archivo


def _crear_resultado_fallido(
    backend: str, model: str, test_name: str, motivo: str
) -> TestResult:
    """
    Crea un TestResult marcado como fallido con success_rate: 0.

    Se usa cuando el backend no está disponible o hay un error previo
    a la ejecución del test.
    """
    return TestResult(
        backend=backend,
        model=model,
        test_name=test_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        iterations=0,
        success_rate=0.0,
        latency_mean_s=0.0,
        latency_p50_s=0.0,
        latency_p90_s=0.0,
        tokens_per_second=0.0,
        max_ram_mb=0.0,
        details={"error": motivo},
    )


def _importar_test(nombre_test: str):
    """
    Importa dinámicamente el módulo de test y retorna su función run_test.

    Raises:
        ImportError: Si el módulo no se puede importar.
        AttributeError: Si el módulo no tiene la función run_test.
    """
    import importlib

    nombre_modulo = TESTS_DISPONIBLES[nombre_test]
    modulo = importlib.import_module(nombre_modulo)
    return modulo.run_test


def _imprimir_resumen(resultados: list, backend: str, model: str) -> None:
    """Imprime un resumen consolidado de todos los tests ejecutados."""
    print("\n" + "=" * 60)
    print("RESUMEN DE EJECUCIÓN")
    print("=" * 60)
    print(f"  Backend: {backend}")
    print(f"  Modelo:  {model}")
    print(f"  Tests ejecutados: {len(resultados)}")
    print("-" * 60)

    for resultado in resultados:
        # Determinar estado visual
        error = resultado.details.get("error")
        if error:
            estado = f"❌ FALLIDO ({error})"
        elif resultado.success_rate >= 0.8:
            estado = f"✅ {resultado.success_rate:.0%}"
        elif resultado.success_rate >= 0.5:
            estado = f"⚠️  {resultado.success_rate:.0%}"
        else:
            estado = f"❌ {resultado.success_rate:.0%}"

        latencia_info = ""
        if resultado.latency_mean_s > 0:
            latencia_info = f" | Latencia media: {resultado.latency_mean_s:.2f}s"

        print(f"  {resultado.test_name:<12} {estado}{latencia_info}")

    # Calcular tasa de éxito global (excluyendo tests con error de backend)
    tests_validos = [r for r in resultados if not r.details.get("error")]
    if tests_validos:
        tasa_global = sum(r.success_rate for r in tests_validos) / len(tests_validos)
        print("-" * 60)
        print(f"  Tasa de éxito global: {tasa_global:.0%}")

    print("=" * 60)


def main(args=None) -> int:
    """
    Punto de entrada principal del runner de tests.

    Retorna 0 si todos los tests se ejecutaron (independientemente del resultado),
    o 1 si hubo un error de configuración o el backend no está disponible.
    """
    # --- Parsear argumentos CLI ---
    parser = argparse.ArgumentParser(
        description="Orquestador de ejecución de tests para modelos Velvet",
        prog="python -m test.runner",
    )
    parser.add_argument(
        "--backend",
        type=str,
        default="ollama",
        choices=["ollama", "vertex"],
        help="Backend de inferencia a utilizar (default: ollama)",
    )
    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="Nombre del modelo a evaluar (ej: velvet-2b-cpu-v1)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./results/",
        help="Directorio para guardar resultados JSON (default: ./results/)",
    )
    parser.add_argument(
        "--tests",
        type=str,
        default=None,
        help="Subconjunto de tests a ejecutar, separados por coma (ej: memoria,numerico,seguridad,stress). Por defecto ejecuta todos.",
    )

    parsed_args = parser.parse_args(args)

    # --- Determinar qué tests ejecutar ---
    if parsed_args.tests:
        nombres_tests = [t.strip() for t in parsed_args.tests.split(",")]
        # Validar que todos los tests solicitados existen
        for nombre in nombres_tests:
            if nombre not in TESTS_DISPONIBLES:
                print(
                    f"Error: Test '{nombre}' no reconocido. "
                    f"Tests disponibles: {', '.join(TESTS_DISPONIBLES.keys())}"
                )
                return 1
    else:
        nombres_tests = list(TESTS_DISPONIBLES.keys())

    # --- Crear directorio de salida si no existe ---
    os.makedirs(parsed_args.output_dir, exist_ok=True)

    # --- Crear adaptador de backend ---
    print(f"Configurando backend '{parsed_args.backend}'...")
    try:
        adapter: BackendAdapter = create_adapter(parsed_args.backend)
    except ValueError as e:
        print(f"Error al crear adaptador: {e}")
        return 1

    # --- Verificar disponibilidad del backend ---
    print(f"Verificando disponibilidad del backend '{parsed_args.backend}'...")
    if not adapter.is_available():
        motivo = "backend no disponible"
        print(f"Error: {motivo}. No se pueden ejecutar los tests.")

        # Generar resultados fallidos para todos los tests solicitados
        timestamp = _generar_timestamp()
        resultados = []
        for nombre_test in nombres_tests:
            resultado = _crear_resultado_fallido(
                backend=parsed_args.backend,
                model=parsed_args.model,
                test_name=nombre_test,
                motivo=motivo,
            )
            ruta = _guardar_resultado(resultado, parsed_args.output_dir, timestamp)
            print(f"  Resultado guardado: {ruta}")
            resultados.append(resultado)

        _imprimir_resumen(resultados, parsed_args.backend, parsed_args.model)
        return 1

    print(f"Backend '{parsed_args.backend}' disponible. Iniciando tests...\n")

    # --- Ejecutar tests ---
    timestamp = _generar_timestamp()
    resultados = []

    for nombre_test in nombres_tests:
        print(f"\n{'='*60}")
        print(f"EJECUTANDO TEST: {nombre_test.upper()}")
        print(f"{'='*60}")

        try:
            # Importar y ejecutar la función run_test del módulo correspondiente
            fn_run_test = _importar_test(nombre_test)
            resultado = fn_run_test(adapter, parsed_args.model)
        except Exception as e:
            print(f"Error ejecutando test '{nombre_test}': {e}")
            resultado = _crear_resultado_fallido(
                backend=parsed_args.backend,
                model=parsed_args.model,
                test_name=nombre_test,
                motivo=str(e),
            )

        # Guardar resultado JSON
        ruta = _guardar_resultado(resultado, parsed_args.output_dir, timestamp)
        print(f"\nResultado guardado: {ruta}")
        resultados.append(resultado)

    # --- Imprimir resumen final ---
    _imprimir_resumen(resultados, parsed_args.backend, parsed_args.model)

    return 0


if __name__ == "__main__":
    sys.exit(main())
