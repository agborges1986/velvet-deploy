"""
Adaptador de backend para Google Cloud Vertex AI.

Implementa la interfaz BackendAdapter para enviar solicitudes de
predicción a un endpoint de Vertex AI, utilizando el SDK
google-cloud-aiplatform y autenticación automática vía ADC
(Application Default Credentials).
"""

import time
from typing import Any, Dict, Tuple

from test.adapters.base import BackendAdapter
from test.models import GenerateResponse, VertexConfig


class VertexAdapter(BackendAdapter):
    """
    Adaptador para el backend Vertex AI.

    Usa el SDK google-cloud-aiplatform para enviar predicciones al
    endpoint de Vertex AI. La autenticación se maneja automáticamente
    mediante Application Default Credentials (ADC) de Google Cloud.
    """

    def __init__(self, config: VertexConfig) -> None:
        """
        Inicializa el adaptador con la configuración de Vertex AI.

        Args:
            config: Configuración de conexión al endpoint de Vertex AI.
        """
        self.config = config
        self._endpoint = None

    def _get_endpoint(self) -> Any:
        """
        Obtiene o crea la referencia al endpoint de Vertex AI.

        Inicializa el SDK de aiplatform y retorna el objeto Endpoint
        correspondiente al endpoint_id configurado. Usa lazy loading
        para evitar importar el SDK hasta que sea necesario.

        Returns:
            Objeto Endpoint del SDK de google-cloud-aiplatform.

        Raises:
            ImportError: Si el SDK google-cloud-aiplatform no está instalado.
            RuntimeError: Si la configuración del endpoint es inválida.
        """
        if self._endpoint is None:
            try:
                from google.cloud import aiplatform
            except ImportError as e:
                raise ImportError(
                    "El SDK google-cloud-aiplatform no está instalado. "
                    "Instálalo con: pip install google-cloud-aiplatform"
                ) from e

            # Inicializar el SDK con el proyecto y la región configurados
            aiplatform.init(
                project=self.config.project,
                location=self.config.region,
            )

            # Obtener referencia al endpoint desplegado
            self._endpoint = aiplatform.Endpoint(self.config.endpoint_id)

        return self._endpoint

    def generate(
        self,
        model: str,
        prompt: str,
        system: str,
        options: Dict[str, Any],
    ) -> GenerateResponse:
        """
        Envía una solicitud de predicción al endpoint de Vertex AI.

        Construye el payload de predicción, lo envía al endpoint y
        extrae los campos relevantes de la respuesta.

        Args:
            model: Nombre del modelo (usado como referencia, el endpoint
                   ya tiene el modelo desplegado).
            prompt: Texto del prompt del usuario.
            system: Prompt de sistema para configurar el comportamiento.
            options: Opciones de generación (temperatura, top_p, etc.).

        Returns:
            GenerateResponse con el texto generado y métricas de rendimiento.

        Raises:
            ConnectionError: Si no se puede conectar al endpoint de Vertex AI.
            TimeoutError: Si la solicitud excede el timeout configurado.
            RuntimeError: Si la respuesta del endpoint indica un error.
        """
        endpoint = self._get_endpoint()

        # Construir el payload de predicción para Vertex AI
        # Formato compatible con Hugging Face DLC en Vertex AI
        instance = self._build_instance(prompt, system, options)

        # Medir latencia de la solicitud
        start_time = time.time()

        try:
            response = endpoint.predict(
                instances=[instance],
                timeout=self.config.timeout,
            )
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e)

            # Clasificar el tipo de error
            if "timeout" in error_msg.lower() or "deadline" in error_msg.lower():
                raise TimeoutError(
                    f"Timeout al conectar con Vertex AI ({self.config.timeout}s): {e}"
                ) from e
            elif "connect" in error_msg.lower() or "unavailable" in error_msg.lower():
                raise ConnectionError(
                    f"No se pudo conectar al endpoint de Vertex AI "
                    f"({self.config.endpoint_id}): {e}"
                ) from e
            else:
                raise RuntimeError(
                    f"Error en la predicción de Vertex AI: {e}"
                ) from e

        elapsed = time.time() - start_time

        # Extraer campos de la respuesta
        text, tokens_generated, tokens_per_second = self._parse_response(
            response, elapsed
        )

        return GenerateResponse(
            text=text,
            latency=elapsed,
            tokens_generated=tokens_generated,
            tokens_per_second=tokens_per_second,
        )

    def _build_instance(
        self,
        prompt: str,
        system: str,
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Construye la instancia de predicción para Vertex AI.

        Formatea el prompt y las opciones en el formato esperado por
        el contenedor de Hugging Face DLC desplegado en Vertex AI.

        Args:
            prompt: Texto del prompt del usuario.
            system: Prompt de sistema.
            options: Opciones de generación.

        Returns:
            Diccionario con la instancia de predicción.
        """
        instance: Dict[str, Any] = {
            "inputs": prompt,
        }

        # Agregar prompt de sistema si se proporciona
        if system:
            instance["system"] = system

        # Mapear opciones de generación a parámetros de Vertex AI
        parameters: Dict[str, Any] = {}
        if "temperature" in options:
            parameters["temperature"] = options["temperature"]
        if "top_p" in options:
            parameters["top_p"] = options["top_p"]
        if "num_predict" in options:
            parameters["max_new_tokens"] = options["num_predict"]
        if "max_new_tokens" in options:
            parameters["max_new_tokens"] = options["max_new_tokens"]

        if parameters:
            instance["parameters"] = parameters

        return instance

    def _parse_response(
        self, response: Any, elapsed: float
    ) -> Tuple[str, int, float]:
        """
        Extrae text, tokens_generated, latency y tokens_per_second de la respuesta.

        Maneja diferentes formatos de respuesta del endpoint de Vertex AI,
        incluyendo respuestas de Hugging Face DLC.

        Nota sobre estimación de tokens: cuando el endpoint no reporta
        tokens generados, se estima usando ~5.5 caracteres por token,
        que es la ratio promedio para texto en italiano/español con
        tokenizers basados en LLaMA/SentencePiece. La ratio de 4 chars/token
        es más apropiada para inglés.

        Args:
            response: Objeto de respuesta del SDK de Vertex AI.
            elapsed: Tiempo transcurrido en segundos.

        Returns:
            Tupla con (texto_generado, tokens_generados, tokens_por_segundo).
        """
        text = ""
        tokens_generated = 0
        tokens_per_second = 0.0
        # Indica si los tokens fueron reportados por el endpoint o estimados
        tokens_estimated = False

        # La respuesta de Vertex AI contiene predictions como lista
        predictions = response.predictions if hasattr(response, "predictions") else []

        if predictions:
            prediction = predictions[0]

            # Extraer texto generado según el formato de la respuesta
            if isinstance(prediction, str):
                text = prediction
            elif isinstance(prediction, dict):
                # Formato típico de HF DLC: {"generated_text": "..."}
                text = prediction.get("generated_text", "")
                # Intentar obtener tokens generados de la respuesta
                tokens_generated = prediction.get("tokens_generated", 0)
                # Algunos modelos reportan detalles adicionales
                if "details" in prediction:
                    details = prediction["details"]
                    tokens_generated = details.get(
                        "generated_tokens", tokens_generated
                    )
            elif isinstance(prediction, list) and len(prediction) > 0:
                # Algunos modelos retornan lista de diccionarios
                first = prediction[0]
                if isinstance(first, dict):
                    text = first.get("generated_text", "")
                    tokens_generated = first.get("tokens_generated", 0)
                elif isinstance(first, str):
                    text = first

        # Estimar tokens generados si no se reportaron en la respuesta
        if tokens_generated == 0 and text:
            # Estimación para idiomas romance (italiano/español) con tokenizers
            # LLaMA/SentencePiece: ~5.5 caracteres por token en promedio.
            # Esto es más conservador que la ratio de 4 chars/token usada
            # comúnmente para inglés, y produce métricas de TPS más realistas.
            tokens_generated = max(1, int(len(text) / 5.5))
            tokens_estimated = True

        # Calcular tokens por segundo
        if elapsed > 0 and tokens_generated > 0:
            tokens_per_second = tokens_generated / elapsed

        return text, tokens_generated, tokens_per_second

    def is_available(self) -> bool:
        """
        Verifica si el endpoint de Vertex AI está disponible.

        Comprueba el estado del endpoint consultando su información
        a través del SDK de google-cloud-aiplatform.

        Returns:
            True si el endpoint existe y tiene modelos desplegados,
            False en caso contrario.
        """
        try:
            endpoint = self._get_endpoint()

            # Verificar que el endpoint tiene modelos desplegados
            # El atributo traffic_split indica los modelos activos
            deployed_models = endpoint.gca_resource.deployed_models
            return len(deployed_models) > 0

        except ImportError:
            # SDK no instalado
            return False
        except Exception:
            # Cualquier error de conexión o autenticación
            return False
