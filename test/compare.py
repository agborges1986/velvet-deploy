"""
Consolidador de resultados y generador de reportes comparativos.

Lee archivos JSON de resultados de la suite de pruebas Velvet, agrupa
por modelo y backend, calcula diferencias porcentuales entre entornos
y genera reportes lado a lado en formato Markdown o CSV.

Uso:
    python -m test.compare --results-dir ./results/ --output report.md --format markdown
    python -m test.compare --results-dir ./results/ --output report.csv --format csv
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from test.models import TestResult


# --- Configuración de logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- Métricas comparables ---
METRICAS_COMPARABLES = [
    ("success_rate", "Tasa de éxito"),
    ("latency_mean_s", "Latencia media (s)"),
    ("latency_p50_s", "Latencia p50 (s)"),
    ("latency_p90_s", "Latencia p90 (s)"),
    ("tokens_per_second", "Tokens/s"),
    ("max_ram_mb", "RAM máx. (MB)"),
]

# --- Backend de referencia para cálculo de diferencia porcentual ---
BACKEND_REFERENCIA = "ollama"
BACKEND_COMPARADO = "vertex"


def cargar_resultados(results_dir: str) -> list:
    """
    Lee todos los archivos JSON de resultados del directorio.

    Soporta tanto TestResult (del runner) como BenchmarkResult (de los
    benchmarks de stress). Los BenchmarkResult se convierten a TestResult
    para poder incluirlos en el reporte comparativo.

    Archivos corruptos se registran en el log y se excluyen.
    Retorna una lista de objetos TestResult válidos.
    """
    resultados = []

    if not os.path.isdir(results_dir):
        logger.error(f"El directorio '{results_dir}' no existe.")
        return resultados

    archivos_json = [
        f for f in os.listdir(results_dir) if f.endswith(".json")
    ]

    if not archivos_json:
        return resultados

    for nombre_archivo in sorted(archivos_json):
        ruta = os.path.join(results_dir, nombre_archivo)
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
            data = json.loads(contenido)

            # Detectar si es un BenchmarkResult (tiene 'hardware' y 'concurrent_users')
            if "hardware" in data and "concurrent_users" in data:
                resultado = _benchmark_to_testresult(data)
                if resultado:
                    resultados.append(resultado)
                    logger.debug(f"Cargado (benchmark): {nombre_archivo}")
                continue

            # Intentar cargar como TestResult estándar
            resultado = TestResult.from_json(contenido)
            resultados.append(resultado)
            logger.debug(f"Cargado: {nombre_archivo}")
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning(
                f"Archivo JSON corrupto, excluido del reporte: {nombre_archivo} ({e})"
            )
        except Exception as e:
            logger.warning(
                f"Error leyendo archivo, excluido del reporte: {nombre_archivo} ({e})"
            )

    return resultados


def _benchmark_to_testresult(data: dict) -> TestResult | None:
    """
    Convierte un BenchmarkResult (dict) a TestResult para el comparador.

    Mapea los campos del benchmark al formato estándar de TestResult.
    Retorna None si los datos no son válidos.
    """
    try:
        # Determinar backend desde el nombre del hardware
        hardware = data.get("hardware", "").lower()
        if "vertex" in hardware:
            backend = "vertex"
        else:
            backend = "ollama"

        return TestResult(
            backend=backend,
            model=data.get("model", "unknown"),
            test_name="benchmark_stress",
            timestamp=data.get("timestamp", ""),
            iterations=data.get("concurrent_users", 0),
            success_rate=(
                data.get("successful_requests", 0) / data.get("total_requests", 1)
                if data.get("total_requests", 0) > 0 else 0.0
            ),
            latency_mean_s=data.get("latency_mean_s", 0.0),
            latency_p50_s=data.get("latency_p50_s", 0.0),
            latency_p90_s=data.get("latency_p90_s", 0.0),
            tokens_per_second=data.get("throughput_tokens_s", 0.0),
            max_ram_mb=0.0,
            details={
                "source": "benchmark_stress",
                "hardware": data.get("hardware", ""),
                "concurrent_users": data.get("concurrent_users", 0),
                "ttft_mean_s": data.get("ttft_mean_s", 0.0),
                "tokens_per_second_per_user": data.get("tokens_per_second_per_user", 0.0),
                "total_tokens_generated": data.get("total_tokens_generated", 0),
                "total_duration_s": data.get("total_duration_s", 0.0),
            },
        )
    except Exception as e:
        logger.warning(f"Error convirtiendo BenchmarkResult a TestResult: {e}")
        return None


def agrupar_resultados(resultados: list) -> dict:
    """
    Agrupa resultados por modelo y backend.

    Retorna un diccionario con estructura:
    {
        modelo: {
            backend: {
                test_name: TestResult (el más reciente por timestamp)
            }
        }
    }
    """
    agrupados = defaultdict(lambda: defaultdict(dict))

    for resultado in resultados:
        modelo = resultado.model
        backend = resultado.backend
        test_name = resultado.test_name

        # Si ya existe un resultado para esta combinación, conservar el más reciente
        existente = agrupados[modelo][backend].get(test_name)
        if existente is None or resultado.timestamp > existente.timestamp:
            agrupados[modelo][backend][test_name] = resultado

    return dict(agrupados)


def calcular_diferencia_porcentual(valor_vertex: float, valor_ollama: float) -> float | None:
    """
    Calcula la diferencia porcentual entre dos valores.

    Fórmula: ((valor_vertex - valor_ollama) / valor_ollama) × 100
    Redondeado a 1 decimal.

    Retorna None si valor_ollama es 0 (división por cero).
    """
    if valor_ollama == 0:
        return None
    return round(((valor_vertex - valor_ollama) / valor_ollama) * 100, 1)


def _obtener_metrica(resultado: TestResult, nombre_metrica: str) -> float:
    """Obtiene el valor de una métrica de un TestResult por nombre de atributo."""
    return getattr(resultado, nombre_metrica, 0.0)


def generar_datos_comparacion(agrupados: dict) -> dict:
    """
    Genera la estructura de datos del reporte de comparación.

    Retorna un diccionario con el formato definido en el diseño:
    {
        "generated_at": "...",
        "models": [...],
        "backends": [...],
        "comparisons": [...],
        "missing_data": [...]
    }
    """
    modelos = sorted(agrupados.keys())
    # Recopilar todos los backends presentes
    todos_backends = set()
    for modelo_data in agrupados.values():
        todos_backends.update(modelo_data.keys())
    backends = sorted(todos_backends)

    # Recopilar todos los nombres de test presentes
    todos_tests = set()
    for modelo_data in agrupados.values():
        for backend_data in modelo_data.values():
            todos_tests.update(backend_data.keys())
    tests = sorted(todos_tests)

    comparaciones = []
    datos_faltantes = []

    for modelo in modelos:
        modelo_data = agrupados.get(modelo, {})

        # Detectar backends faltantes para este modelo
        for backend in [BACKEND_REFERENCIA, BACKEND_COMPARADO]:
            if backend not in modelo_data or not modelo_data[backend]:
                datos_faltantes.append({
                    "model": modelo,
                    "backend": backend,
                    "reason": f"Sin datos del backend '{backend}' para el modelo '{modelo}'",
                })

        for test_name in tests:
            entrada = {
                "model": modelo,
                "test_name": test_name,
            }

            # Obtener resultados de cada backend
            resultado_ollama = modelo_data.get(BACKEND_REFERENCIA, {}).get(test_name)
            resultado_vertex = modelo_data.get(BACKEND_COMPARADO, {}).get(test_name)

            # Agregar métricas de cada backend disponible
            if resultado_ollama:
                entrada[BACKEND_REFERENCIA] = {
                    nombre: _obtener_metrica(resultado_ollama, nombre)
                    for nombre, _ in METRICAS_COMPARABLES
                }

            if resultado_vertex:
                entrada[BACKEND_COMPARADO] = {
                    nombre: _obtener_metrica(resultado_vertex, nombre)
                    for nombre, _ in METRICAS_COMPARABLES
                }

            # Calcular diferencias porcentuales si ambos backends tienen datos
            if resultado_ollama and resultado_vertex:
                diff_pct = {}
                for nombre, _ in METRICAS_COMPARABLES:
                    val_ollama = _obtener_metrica(resultado_ollama, nombre)
                    val_vertex = _obtener_metrica(resultado_vertex, nombre)
                    diff = calcular_diferencia_porcentual(val_vertex, val_ollama)
                    if diff is not None:
                        diff_pct[nombre] = f"{diff:+.1f}%"
                    else:
                        diff_pct[nombre] = "N/A"
                entrada["diff_pct"] = diff_pct

            comparaciones.append(entrada)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": modelos,
        "backends": backends,
        "comparisons": comparaciones,
        "missing_data": datos_faltantes,
    }


def generar_reporte_markdown(datos: dict) -> str:
    """
    Genera un reporte comparativo en formato Markdown.

    Incluye tablas lado a lado con métricas y diferencias porcentuales.
    """
    lineas = []
    lineas.append("# Reporte Comparativo — Suite de Pruebas Velvet")
    lineas.append("")
    lineas.append(f"**Generado:** {datos['generated_at']}")
    lineas.append(f"**Modelos:** {', '.join(datos['models']) if datos['models'] else 'Ninguno'}")
    lineas.append(f"**Backends:** {', '.join(datos['backends']) if datos['backends'] else 'Ninguno'}")
    lineas.append("")

    # --- Nota sobre comparabilidad de métricas ---
    if BACKEND_REFERENCIA in datos["backends"] and BACKEND_COMPARADO in datos["backends"]:
        lineas.append("> **Nota sobre comparabilidad:** Las métricas de Tokens/s de Ollama")
        lineas.append("> usan `eval_duration` (tiempo real de generación), mientras que Vertex AI")
        lineas.append("> estima tokens con heurística (~5.5 chars/token). Los valores de Tokens/s")
        lineas.append("> entre backends no son directamente comparables.")
        lineas.append("")

    # --- Sección de datos faltantes ---
    if datos["missing_data"]:
        lineas.append("## ⚠️ Datos Faltantes")
        lineas.append("")
        for faltante in datos["missing_data"]:
            lineas.append(f"- **{faltante['model']}**: {faltante['reason']}")
        lineas.append("")

    # --- Tabla comparativa por modelo ---
    for modelo in datos["models"]:
        lineas.append(f"## Modelo: {modelo}")
        lineas.append("")

        # Filtrar comparaciones de este modelo
        comparaciones_modelo = [
            c for c in datos["comparisons"] if c["model"] == modelo
        ]

        if not comparaciones_modelo:
            lineas.append("_Sin datos disponibles para este modelo._")
            lineas.append("")
            continue

        # Determinar qué backends tienen datos
        tiene_ollama = any(BACKEND_REFERENCIA in c for c in comparaciones_modelo)
        tiene_vertex = any(BACKEND_COMPARADO in c for c in comparaciones_modelo)
        tiene_diff = any("diff_pct" in c for c in comparaciones_modelo)

        # Construir encabezado de tabla
        encabezado = "| Test | Métrica |"
        separador = "|------|---------|"
        if tiene_ollama:
            encabezado += " Ollama |"
            separador += "-------:|"
        if tiene_vertex:
            encabezado += " Vertex |"
            separador += "-------:|"
        if tiene_diff:
            encabezado += " Δ% |"
            separador += "----:|"

        lineas.append(encabezado)
        lineas.append(separador)

        for comp in comparaciones_modelo:
            test_name = comp["test_name"]
            primera_fila = True

            for nombre_metrica, etiqueta in METRICAS_COMPARABLES:
                # Columna de test (solo en la primera fila de cada test)
                col_test = test_name if primera_fila else ""
                primera_fila = False

                fila = f"| {col_test} | {etiqueta} |"

                if tiene_ollama:
                    val = comp.get(BACKEND_REFERENCIA, {}).get(nombre_metrica)
                    fila += f" {_formatear_valor(nombre_metrica, val)} |"

                if tiene_vertex:
                    val = comp.get(BACKEND_COMPARADO, {}).get(nombre_metrica)
                    fila += f" {_formatear_valor(nombre_metrica, val)} |"

                if tiene_diff:
                    diff = comp.get("diff_pct", {}).get(nombre_metrica, "—")
                    fila += f" {diff} |"

                lineas.append(fila)

        lineas.append("")

    return "\n".join(lineas)


def generar_reporte_csv(datos: dict) -> str:
    """
    Genera un reporte comparativo en formato CSV.

    Columnas: modelo, test, metrica, ollama, vertex, diff_pct
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Encabezado
    writer.writerow(["modelo", "test", "metrica", "ollama", "vertex", "diff_pct"])

    for comp in datos["comparisons"]:
        modelo = comp["model"]
        test_name = comp["test_name"]

        for nombre_metrica, etiqueta in METRICAS_COMPARABLES:
            val_ollama = comp.get(BACKEND_REFERENCIA, {}).get(nombre_metrica, "")
            val_vertex = comp.get(BACKEND_COMPARADO, {}).get(nombre_metrica, "")
            diff = comp.get("diff_pct", {}).get(nombre_metrica, "")

            writer.writerow([
                modelo,
                test_name,
                etiqueta,
                val_ollama if val_ollama != "" else "",
                val_vertex if val_vertex != "" else "",
                diff,
            ])

    # Agregar sección de datos faltantes si los hay
    if datos["missing_data"]:
        writer.writerow([])
        writer.writerow(["# Datos faltantes"])
        writer.writerow(["modelo", "backend", "razon"])
        for faltante in datos["missing_data"]:
            writer.writerow([
                faltante["model"],
                faltante["backend"],
                faltante["reason"],
            ])

    return output.getvalue()


def _formatear_valor(nombre_metrica: str, valor) -> str:
    """Formatea un valor numérico según el tipo de métrica."""
    if valor is None:
        return "—"

    if nombre_metrica == "success_rate":
        return f"{valor:.0%}"
    elif nombre_metrica in ("latency_mean_s", "latency_p50_s", "latency_p90_s"):
        return f"{valor:.2f}"
    elif nombre_metrica == "tokens_per_second":
        return f"{valor:.1f}"
    elif nombre_metrica == "max_ram_mb":
        return f"{valor:.0f}"
    else:
        return str(valor)


def main(args=None) -> int:
    """
    Punto de entrada principal del comparador de resultados.

    Retorna 0 si el reporte se generó correctamente, o 1 si hay errores.
    """
    # --- Parsear argumentos CLI ---
    parser = argparse.ArgumentParser(
        description="Consolidador de resultados y generador de reportes comparativos Velvet",
        prog="python -m test.compare",
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        required=True,
        help="Directorio con archivos JSON de resultados",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="report.md",
        help="Archivo de salida para el reporte (default: report.md)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="markdown",
        choices=["markdown", "csv"],
        help="Formato del reporte (default: markdown)",
    )

    parsed_args = parser.parse_args(args)

    # --- Cargar resultados ---
    logger.info(f"Leyendo resultados de '{parsed_args.results_dir}'...")
    resultados = cargar_resultados(parsed_args.results_dir)

    if not resultados:
        logger.error(
            f"No se encontraron archivos JSON válidos en '{parsed_args.results_dir}'. "
            "No se puede generar el reporte."
        )
        return 1

    logger.info(f"Se cargaron {len(resultados)} resultados válidos.")

    # --- Agrupar por modelo y backend ---
    agrupados = agrupar_resultados(resultados)

    # --- Generar datos de comparación ---
    datos = generar_datos_comparacion(agrupados)

    # --- Generar reporte en el formato solicitado ---
    if parsed_args.format == "markdown":
        contenido = generar_reporte_markdown(datos)
    else:
        contenido = generar_reporte_csv(datos)

    # --- Guardar reporte ---
    # Crear directorio de salida si no existe
    output_dir = os.path.dirname(parsed_args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(parsed_args.output, "w", encoding="utf-8") as f:
        f.write(contenido)

    logger.info(f"Reporte generado: {parsed_args.output} (formato: {parsed_args.format})")

    # --- Mostrar resumen de datos faltantes ---
    if datos["missing_data"]:
        logger.warning("Datos faltantes detectados:")
        for faltante in datos["missing_data"]:
            logger.warning(f"  - {faltante['model']}: {faltante['reason']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
