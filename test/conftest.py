"""
Configuración compartida de pytest para la suite de pruebas Velvet.

Define fixtures para crear adaptadores de backend y configurar
perfiles de Hypothesis para property-based testing.
"""

import os

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
    parser.addoption(
        "--vertex-project",
        action="store",
        default=os.environ.get("VERTEX_PROJECT", ""),
        help="ID del proyecto GCP (o env VERTEX_PROJECT)",
    )
    parser.addoption(
        "--vertex-endpoint-id",
        action="store",
        default=os.environ.get("VERTEX_ENDPOINT_ID", ""),
        help="ID numérico del endpoint de Vertex AI (o env VERTEX_ENDPOINT_ID)",
    )
    parser.addoption(
        "--vertex-region",
        action="store",
        default=os.environ.get("VERTEX_REGION", "us-central1"),
        help="Región de GCP (o env VERTEX_REGION). Default: us-central1",
    )


@pytest.fixture
def adapter(request: pytest.FixtureRequest) -> BackendAdapter:
    """Crea un BackendAdapter según el parámetro --backend de la CLI."""
    backend_name = request.config.getoption("--backend")
    config = None

    if backend_name == "vertex":
        from test.models import VertexConfig

        config = VertexConfig(
            project=request.config.getoption("--vertex-project"),
            region=request.config.getoption("--vertex-region"),
            endpoint_id=request.config.getoption("--vertex-endpoint-id"),
        )

    return create_adapter(backend_name, config)


@pytest.fixture
def model_name(request: pytest.FixtureRequest) -> str:
    """Retorna el nombre del modelo según el parámetro --model de la CLI."""
    return request.config.getoption("--model")
