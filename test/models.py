"""
Modelos de datos para la suite de pruebas Velvet.

Define las estructuras de datos utilizadas por los adaptadores de backend,
el runner de pruebas y el marco de comparación.
"""

from dataclasses import dataclass, field, asdict
from typing import Any
import json


# --- Respuesta de generación ---

@dataclass
class GenerateResponse:
    """Respuesta de una solicitud de generación a cualquier backend."""
    text: str                  # Texto generado por el modelo
    latency: float             # Latencia en segundos
    tokens_generated: int      # Número de tokens generados
    tokens_per_second: float   # Tokens por segundo


# --- Resultado de un test ---

@dataclass
class TestResult:
    """Resultado estructurado de la ejecución de un test contra un backend."""
    backend: str               # Nombre del backend (ollama | vertex)
    model: str                 # Nombre del modelo evaluado
    test_name: str             # Nombre del test ejecutado
    timestamp: str             # Marca de tiempo ISO 8601
    iterations: int            # Número de iteraciones ejecutadas
    success_rate: float        # Tasa de éxito (0.0 a 1.0)
    latency_mean_s: float      # Latencia media en segundos
    latency_p50_s: float       # Latencia percentil 50 en segundos
    latency_p90_s: float       # Latencia percentil 90 en segundos
    tokens_per_second: float   # Tokens por segundo promedio
    max_ram_mb: float          # Uso máximo de RAM en MB
    details: dict = field(default_factory=dict)  # Detalles adicionales del test

    def to_json(self) -> str:
        """Serializa el resultado a una cadena JSON."""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "TestResult":
        """Deserializa un resultado desde una cadena JSON."""
        data = json.loads(json_str)
        return cls(**data)


# --- Configuración de modelo ---

@dataclass
class ModelConfig:
    """Configuración de un modelo Velvet para despliegue."""
    name: str                  # Nombre del modelo (velvet-2b | velvet-14b)
    hf_repo: str               # Repositorio en Hugging Face
    num_ctx: int               # Tamaño de ventana de contexto
    num_predict: int           # Máximo de tokens a generar
    quantization: str          # Nivel de cuantización (Q4_K_M, Q4_0, etc.)
    version: str               # Versión del modelo
    ram_estimate_gb: float     # Estimación de RAM requerida en GB


# --- Configuración de backends ---

@dataclass
class OllamaConfig:
    """Configuración de conexión al backend Ollama (a través del reverse proxy)."""
    base_url: str = "http://localhost:8080"  # URL del reverse proxy Caddy
    auth_user: str = ""                      # Usuario para autenticación básica
    auth_password: str = ""                  # Contraseña para autenticación básica
    timeout: int = 120                       # Timeout en segundos


@dataclass
class VertexConfig:
    """Configuración de conexión al backend Vertex AI."""
    project: str = ""                        # ID del proyecto GCP
    region: str = "us-central1"              # Región de GCP
    endpoint_id: str = ""                    # ID del endpoint de Vertex AI
    timeout: int = 120                       # Timeout en segundos
