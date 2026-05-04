"""
Adaptador de backend para Google Cloud Vertex AI.

Implementa la interfaz BackendAdapter para enviar solicitudes de
predicción a un endpoint de Vertex AI.

Soporta dos modos de conexión:
  1. SDK nativo (endpoint.predict) — para entornos sin proxy SSL.
  2. REST directo al dedicated endpoint — para entornos corporativos
     con proxy SSL que intercepta certificados (verify=False).

El modo se selecciona automáticamente: si el endpoint es un dedicated
endpoint (nombre empieza con "mg-"), se usa REST directo.
"""

import json
import time
import warnings
from typing import Any, Dict, Tuple

from test.adapters.base import BackendAdapter
from test.models import GenerateResponse, VertexConfig


class VertexAdapter(BackendAdapter):
    """
    Adaptador para el backend Vertex AI.

    Usa REST directo para dedicated endpoints (Model Garden one-click
    deploy) o el SDK google-cloud-aiplatform para endpoints estándar.
    La autenticación se maneja vía ADC (Application Default Credentials).
    """

    def __init__(self, config: VertexConfig) -> None:
        """
        Inicializa el adaptador con la configuración de Vertex AI.

        Args:
            config: Configuración de conexión al endpoint de Vertex AI.
        """
        self.config = config
        self._endpoint = None
        self._credentials = None
        self._dedicated_dns = None
        self._project_number = None

    def _get_credentials(self) -> Any:
        """Obtiene y refresca las credenciales ADC de Google Cloud."""
        if self._credentials is None:
            import google.auth
            import google.auth.transport.requests

            self._credentials, _ = google.auth.default()

        # Refrescar si están expiradas
        import google.auth.transport.requests
        auth_req = google.auth.transport.requests.Request()
        self._credentials.refresh(auth_req)

        return self._credentials

    def _is_dedicated_endpoint(self) -> bool:
        """Determina si el endpoint es un dedicated endpoint de Model Garden."""
        return self.config.endpoint_id.startswith("mg-")

    def _get_dedicated_dns(self) -> str:
        """
        Obtiene el DNS del dedicated endpoint consultando la API de Vertex AI.

        Usa gRPC (que no pasa por el proxy HTTP) para consultar los
        metadatos del endpoint y extraer el dedicatedEndpointDns.

        Returns:
            DNS del dedicated endpoint.
        """
        if self._dedicated_dns is None:
            try:
                from google.cloud import aiplatform
                aiplatform.init(
                    project=self.config.project,
                    location=self.config.region,
                )
                ep = aiplatform.Endpoint(self.config.endpoint_id)
                # Acceder al recurso GCA para obtener el DNS dedicado
                gca = ep.gca_resource
                self._dedicated_dns = gca.dedicated_endpoint_dns
                self._project_number = gca.name.split("/")[1]
            except Exception:
                # Fallback: construir el DNS manualmente no es posible
                # sin el project number, así que re-raise
                raise

        return self._dedicated_dns

    def _get_endpoint(self) -> Any:
        """
        Obtiene o crea la referencia al endpoint de Vertex AI (SDK nativo).

        Solo se usa para endpoints NO dedicados.

        Returns:
            Objeto Endpoint del SDK de google-cloud-aiplatform.
        """
        if self._endpoint is None:
            try:
                from google.cloud import aiplatform
            except ImportError as e:
                raise ImportError(
                    "El SDK google-cloud-aiplatform no está instalado. "
                    "Instálalo con: pip install google-cloud-aiplatform"
                ) from e

            aiplatform.init(
                project=self.config.project,
                location=self.config.region,
            )
            self._endpoint = aiplatform.Endpoint(self.config.endpoint_id)

        return self._endpoint

    def _predict_rest(self, instance: Dict[str, Any]) -> Dict[str, Any]:
        """
        Envía una predicción vía REST directo al dedicated endpoint.

        Usa requests con verify=False para evitar problemas con proxies
        corporativos que interceptan SSL con certificados autofirmados.

        Args:
            instance: Diccionario con la instancia de predicción.

        Returns:
            Diccionario con la respuesta JSON del endpoint.
        """
        import requests

        creds = self._get_credentials()
        dns = self._get_dedicated_dns()

        url = (
            f"https://{dns}/v1/"
            f"projects/{self.config.project}/locations/{self.config.region}/"
            f"endpoints/{self.config.endpoint_id}:predict"
        )

        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        }

        payload = {"instances": [instance]}

        # Suprimir warnings de SSL para no ensuciar la salida de tests
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                verify=False,
                timeout=self.config.timeout,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Error en predicción REST (HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            )

        return resp.json()

    def generate(
        self,
        model: str,
        prompt: str,
        system: str,
        options: Dict[str, Any],
    ) -> GenerateResponse:
        """
        Envía una solicitud de predicción al endpoint de Vertex AI.

        Para dedicated endpoints usa REST directo (bypass SSL proxy).
        Para endpoints estándar usa el SDK nativo.

        Args:
            model: Nombre del modelo (referencia, el endpoint ya lo tiene).
            prompt: Texto del prompt del usuario.
            system: Prompt de sistema para configurar el comportamiento.
            options: Opciones de generación (temperatura, top_p, etc.).

        Returns:
            GenerateResponse con el texto generado y métricas de rendimiento.
        """
        instance = self._build_instance(prompt, system, options)
        start_time = time.time()

        try:
            if self._is_dedicated_endpoint():
                # REST directo para dedicated endpoints
                resp_json = self._predict_rest(instance)
                elapsed = time.time() - start_time
                text, tokens_generated, tokens_per_second = (
                    self._parse_rest_response(resp_json, elapsed)
                )
            else:
                # SDK nativo para endpoints estándar
                endpoint = self._get_endpoint()
                response = endpoint.predict(
                    instances=[instance],
                    timeout=self.config.timeout,
                )
                elapsed = time.time() - start_time
                text, tokens_generated, tokens_per_second = (
                    self._parse_response(response, elapsed)
                )
        except (TimeoutError, ConnectionError):
            raise
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e)

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
        """
        instance: Dict[str, Any] = {
            "inputs": prompt,
        }

        if system:
            instance["system"] = system

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

    def _parse_rest_response(
        self, resp_json: Dict[str, Any], elapsed: float
    ) -> Tuple[str, int, float]:
        """
        Extrae texto y métricas de la respuesta REST JSON.

        Args:
            resp_json: Respuesta JSON del endpoint REST.
            elapsed: Tiempo transcurrido en segundos.

        Returns:
            Tupla con (texto_generado, tokens_generados, tokens_por_segundo).
        """
        text = ""
        tokens_generated = 0

        predictions = resp_json.get("predictions", [])
        if predictions:
            prediction = predictions[0]
            if isinstance(prediction, str):
                text = prediction
            elif isinstance(prediction, dict):
                text = prediction.get("generated_text", "")
                tokens_generated = prediction.get("tokens_generated", 0)
                if "details" in prediction:
                    tokens_generated = prediction["details"].get(
                        "generated_tokens", tokens_generated
                    )
            elif isinstance(prediction, list) and prediction:
                first = prediction[0]
                if isinstance(first, dict):
                    text = first.get("generated_text", "")
                    tokens_generated = first.get("tokens_generated", 0)
                elif isinstance(first, str):
                    text = first

        # Estimar tokens si no reportados (~5.5 chars/token para italiano/español)
        if tokens_generated == 0 and text:
            tokens_generated = max(1, int(len(text) / 5.5))

        tokens_per_second = 0.0
        if elapsed > 0 and tokens_generated > 0:
            tokens_per_second = tokens_generated / elapsed

        return text, tokens_generated, tokens_per_second

    def _parse_response(
        self, response: Any, elapsed: float
    ) -> Tuple[str, int, float]:
        """
        Extrae texto y métricas de la respuesta del SDK nativo.

        Maneja diferentes formatos de respuesta del endpoint de Vertex AI,
        incluyendo respuestas de Hugging Face DLC.
        """
        text = ""
        tokens_generated = 0

        predictions = response.predictions if hasattr(response, "predictions") else []

        if predictions:
            prediction = predictions[0]

            if isinstance(prediction, str):
                text = prediction
            elif isinstance(prediction, dict):
                text = prediction.get("generated_text", "")
                tokens_generated = prediction.get("tokens_generated", 0)
                if "details" in prediction:
                    details = prediction["details"]
                    tokens_generated = details.get(
                        "generated_tokens", tokens_generated
                    )
            elif isinstance(prediction, list) and len(prediction) > 0:
                first = prediction[0]
                if isinstance(first, dict):
                    text = first.get("generated_text", "")
                    tokens_generated = first.get("tokens_generated", 0)
                elif isinstance(first, str):
                    text = first

        # Estimar tokens si no reportados (~5.5 chars/token para italiano/español)
        if tokens_generated == 0 and text:
            tokens_generated = max(1, int(len(text) / 5.5))

        tokens_per_second = 0.0
        if elapsed > 0 and tokens_generated > 0:
            tokens_per_second = tokens_generated / elapsed

        return text, tokens_generated, tokens_per_second

    def is_available(self) -> bool:
        """
        Verifica si el endpoint de Vertex AI está disponible.

        Para dedicated endpoints, envía un ping REST rápido.
        Para endpoints estándar, consulta los modelos desplegados vía SDK.

        Returns:
            True si el endpoint responde, False en caso contrario.
        """
        try:
            if self._is_dedicated_endpoint():
                # Para dedicated endpoints, verificar con una predicción
                # mínima vía REST (el SDK gRPC funciona para metadata)
                from google.cloud import aiplatform
                aiplatform.init(
                    project=self.config.project,
                    location=self.config.region,
                )
                ep = aiplatform.Endpoint(self.config.endpoint_id)
                deployed_models = ep.gca_resource.deployed_models
                return len(deployed_models) > 0
            else:
                endpoint = self._get_endpoint()
                deployed_models = endpoint.gca_resource.deployed_models
                return len(deployed_models) > 0

        except ImportError:
            return False
        except Exception:
            return False
