"""
Clase base abstracta para adaptadores de backend y factory function.

Define la interfaz común que todos los adaptadores de backend deben
implementar, permitiendo ejecutar la misma suite de pruebas contra
diferentes entornos de inferencia (Ollama, Vertex AI).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from test.models import GenerateResponse


class BackendAdapter(ABC):
    """Clase abstracta que define la interfaz para adaptadores de backend."""

    @abstractmethod
    def generate(
        self,
        model: str,
        prompt: str,
        system: str,
        options: Dict[str, Any],
    ) -> GenerateResponse:
        """
        Envía una solicitud de generación al backend y retorna la respuesta.

        Args:
            model: Nombre del modelo a utilizar.
            prompt: Texto del prompt del usuario.
            system: Prompt de sistema para configurar el comportamiento.
            options: Opciones adicionales de generación (temperatura, top_p, etc.).

        Returns:
            GenerateResponse con el texto generado y métricas de rendimiento.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Verifica si el backend está disponible y listo para recibir solicitudes.

        Returns:
            True si el backend responde correctamente, False en caso contrario.
        """
        pass


def create_adapter(backend_name: str = "ollama", config: Any = None) -> BackendAdapter:
    """
    Factory function que crea el adaptador de backend correspondiente.

    Retorna OllamaAdapter por defecto cuando no se especifica backend.

    Args:
        backend_name: Nombre del backend ("ollama" o "vertex").
        config: Objeto de configuración del backend (OllamaConfig o VertexConfig).

    Returns:
        Instancia del BackendAdapter correspondiente.

    Raises:
        ValueError: Si el nombre del backend no es reconocido.
    """
    # Normalizar el nombre del backend
    backend_name = backend_name.lower().strip()

    if backend_name == "ollama":
        from test.adapters.ollama_adapter import OllamaAdapter
        from test.models import OllamaConfig

        # Usar configuración por defecto si no se proporciona
        if config is None:
            config = OllamaConfig()
        return OllamaAdapter(config)

    elif backend_name == "vertex":
        from test.adapters.vertex_adapter import VertexAdapter
        from test.models import VertexConfig

        # Usar configuración por defecto si no se proporciona
        if config is None:
            config = VertexConfig()
        return VertexAdapter(config)

    else:
        raise ValueError(
            f"Backend no reconocido: '{backend_name}'. "
            f"Los backends soportados son: 'ollama', 'vertex'."
        )
