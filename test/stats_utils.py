"""
Utilidades estadísticas para la suite de pruebas Velvet.

Proporciona funciones de cálculo de percentiles con interpolación
correcta y warm-up de modelos para evitar cold-start en las métricas.
"""

import statistics
from typing import List

from test.adapters.base import BackendAdapter
from test.models import GenerateResponse


def percentile(data: List[float], p: float) -> float:
    """
    Calcula el percentil p de una lista de valores usando interpolación lineal.

    Implementa el método de interpolación lineal (equivalente a
    numpy.percentile con interpolation='linear'), que es el estándar
    en herramientas de benchmarking.

    Args:
        data: Lista de valores numéricos (no necesita estar ordenada).
        p: Percentil deseado entre 0.0 y 1.0 (ej: 0.5 para p50, 0.9 para p90).

    Returns:
        Valor del percentil calculado con interpolación.

    Raises:
        ValueError: Si la lista está vacía o p está fuera de rango.
    """
    if not data:
        raise ValueError("No se puede calcular percentil de una lista vacía")
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"Percentil debe estar entre 0.0 y 1.0, recibido: {p}")

    sorted_data = sorted(data)
    n = len(sorted_data)

    if n == 1:
        return sorted_data[0]

    # Posición virtual en el array ordenado (0-indexed)
    pos = p * (n - 1)
    lower_idx = int(pos)
    upper_idx = min(lower_idx + 1, n - 1)
    fraction = pos - lower_idx

    return sorted_data[lower_idx] + fraction * (sorted_data[upper_idx] - sorted_data[lower_idx])


def warmup(adapter: BackendAdapter, model: str, prompt: str = "Hola", system: str = "Responde brevemente.") -> None:
    """
    Ejecuta una solicitud de calentamiento para evitar cold-start.

    La primera solicitud a un modelo puede incluir la carga del modelo
    en memoria (KV cache), lo que infla la latencia. Esta función
    ejecuta una solicitud descartable antes de las mediciones reales.

    Args:
        adapter: Adaptador de backend a calentar.
        model: Nombre del modelo.
        prompt: Prompt simple para el warm-up.
        system: System prompt para el warm-up.
    """
    try:
        adapter.generate(
            model=model,
            prompt=prompt,
            system=system,
            options={"num_ctx": 512, "temperature": 0.0},
        )
        print("  [WARM-UP] Modelo cargado en memoria. Iniciando mediciones.\n")
    except Exception as e:
        print(f"  [WARM-UP] Advertencia: warm-up falló ({e}). La primera iteración puede incluir cold-start.\n")
