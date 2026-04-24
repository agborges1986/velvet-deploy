"""
Configuración compartida de pytest para la suite de pruebas Velvet.

Define fixtures para crear adaptadores de backend y configurar
perfiles de Hypothesis para property-based testing.
"""

import pytest
from hypothesis import settings, HealthCheck

from test.adapters import BackendAdapter, create_adapter


# --- Perfil de Hypothesis con max_examples=100 ---
settings.register_profile(
    "velvet",
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("velvet")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Agrega opciones CLI personalizadas para seleccionar backend y modelo."""
    parser.addoption(
        "--backend",
        action="store",
        default="ollama",
        help="Backend de inferencia a utilizar: ollama | vertex (default: ollama)",
    )
    parser.addoption(
        "--model",
        action="store",
        default="velvet-legal",
        help="Nombre del modelo a evaluar (default: velvet-legal)",
    )


@pytest.fixture
def adapter(request: pytest.FixtureRequest) -> BackendAdapter:
    """Crea un BackendAdapter según el parámetro --backend de la CLI."""
    backend_name = request.config.getoption("--backend")
    return create_adapter(backend_name)


@pytest.fixture
def model_name(request: pytest.FixtureRequest) -> str:
    """Retorna el nombre del modelo según el parámetro --model de la CLI."""
    return request.config.getoption("--model")
