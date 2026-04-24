# Paquete de adaptadores de backend para la suite de pruebas Velvet.
#
# Proporciona una capa de abstracción sobre los diferentes backends
# de inferencia (Ollama, Vertex AI) para ejecutar los mismos tests
# contra cualquier entorno.

from test.adapters.base import BackendAdapter, create_adapter

__all__ = ["BackendAdapter", "create_adapter"]
