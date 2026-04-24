"""
Adaptador de backend para Ollama.

Implementa la interfaz BackendAdapter para enviar solicitudes de
generación al servidor Ollama a través del reverse proxy Caddy,
soportando autenticación básica HTTP.
"""

import time
from typing import Any, Dict, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth

from test.adapters.base import BackendAdapter
from test.models import GenerateResponse, OllamaConfig


class OllamaAdapter(BackendAdapter):
    """
    Adaptador para el backend Ollama.

    Envía solicitudes POST a la API de Ollama ({base_url}/api/generate)
    y soporta autenticación básica HTTP para el reverse proxy Caddy.
    Es el backend por defecto cuando no se especifica --backend.
    """

    def __init__(self, config: OllamaConfig) -> None:
        """
        Inicializa el adaptador con la configuración de Ollama.

        Args:
            config: Configuración de conexión al backend Ollama.
        """
        self.config = config
        self._auth = self._build_auth()

    def _build_auth(self) -> Optional[HTTPBasicAuth]:
        """
        Construye el objeto de autenticación HTTP básica si hay credenciales.

        Returns:
            HTTPBasicAuth si hay usuario y contraseña configurados, None en caso contrario.
        """
        if self.config.auth_user and self.config.auth_password:
            return HTTPBasicAuth(self.config.auth_user, self.config.auth_password)
        return None

    def generate(
        self,
        model: str,
        prompt: str,
        system: str,
        options: Dict[str, Any],
    ) -> GenerateResponse:
        """
        Envía una solicitud de generación a la API de Ollama.

        Realiza un POST a {base_url}/api/generate con el formato de payload
        esperado por Ollama, midiendo la latencia de la solicitud.

        Args:
            model: Nombre del modelo en Ollama (ej: "velvet-2b-cpu-v1").
            prompt: Texto del prompt del usuario.
            system: Prompt de sistema para configurar el comportamiento.
            options: Opciones de generación (temperatura, top_p, num_ctx, etc.).

        Returns:
            GenerateResponse con el texto generado y métricas de rendimiento.

        Raises:
            ConnectionError: Si no se puede conectar al servidor Ollama.
            TimeoutError: Si la solicitud excede el timeout configurado.
            RuntimeError: Si la respuesta del servidor indica un error.
        """
        url = f"{self.config.base_url}/api/generate"

        # Construir el payload en formato Ollama
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        # Agregar prompt de sistema si se proporciona
        if system:
            payload["system"] = system

        # Agregar opciones de generación si se proporcionan
        if options:
            payload["options"] = options

        # Medir latencia de la solicitud
        start_time = time.time()

        try:
            response = requests.post(
                url,
                json=payload,
                auth=self._auth,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"No se pudo conectar al servidor Ollama en {self.config.base_url}: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(
                f"Timeout al conectar con Ollama ({self.config.timeout}s): {e}"
            ) from e
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(
                f"Error HTTP del servidor Ollama: {response.status_code} - {response.text}"
            ) from e

        elapsed = time.time() - start_time

        # Parsear la respuesta de Ollama
        data = response.json()
        text, tokens_generated, tokens_per_second = self._parse_response(data, elapsed)

        return GenerateResponse(
            text=text,
            latency=elapsed,
            tokens_generated=tokens_generated,
            tokens_per_second=tokens_per_second,
        )

    def _parse_response(
        self, data: Dict[str, Any], elapsed: float
    ) -> Tuple[str, int, float]:
        """
        Extrae los campos relevantes de la respuesta de Ollama.

        Args:
            data: Diccionario con la respuesta JSON de Ollama.
            elapsed: Tiempo transcurrido en segundos.

        Returns:
            Tupla con (texto_generado, tokens_generados, tokens_por_segundo).
        """
        text = data.get("response", "")

        # Ollama reporta eval_count como número de tokens generados
        tokens_generated = data.get("eval_count", 0)

        # Calcular tokens por segundo
        if elapsed > 0 and tokens_generated > 0:
            tokens_per_second = tokens_generated / elapsed
        else:
            tokens_per_second = 0.0

        return text, tokens_generated, tokens_per_second

    def is_available(self) -> bool:
        """
        Verifica si el servidor Ollama está disponible.

        Realiza una solicitud GET al endpoint /api/tags para comprobar
        que el servidor responde correctamente.

        Returns:
            True si el servidor responde con código 200, False en caso contrario.
        """
        url = f"{self.config.base_url}/api/tags"

        try:
            response = requests.get(
                url,
                auth=self._auth,
                timeout=10,
            )
            return response.status_code == 200
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return False
