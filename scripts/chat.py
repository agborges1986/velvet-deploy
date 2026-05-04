#!/usr/bin/env python3
"""
chat.py — Chat interactivo con modelos Velvet (Vertex AI / EC2 Ollama)

Soporta dos backends:
  - vertex:  Vertex AI dedicated endpoint (requiere ADC de GCP)
  - ollama:  Ollama vía Caddy reverse proxy en EC2 (Basic Auth)

Uso:
    # Vertex AI (por defecto)
    python scripts/chat.py
    python scripts/chat.py --backend vertex

    # EC2 / Ollama
    python scripts/chat.py --backend ollama --base-url http://18.221.11.128:8080
    python scripts/chat.py --backend ollama --base-url http://18.221.11.128:8080 \\
        --auth-user admin --auth-password "VelvetSecure2025!"

    # Opciones comunes
    python scripts/chat.py --backend ollama --model velvet-14b-cpu-v1 --temperature 0.3
    python scripts/chat.py --backend vertex --system "Sei un assistente italiano"
"""

import argparse
import json
import os
import sys
import time
import warnings
from typing import Any, Dict, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth


# ── Defaults ─────────────────────────────────────────────────────────────
DEFAULT_SYSTEM = "Eres un asistente útil y profesional. Responde en el idioma del usuario."

# Vertex AI
DEFAULT_VERTEX_PROJECT = "arquitectura-ventas"
DEFAULT_VERTEX_REGION = "us-central1"
DEFAULT_VERTEX_ENDPOINT = "mg-endpoint-c650fdf8-9183-43eb-a345-5ea8ebcc7ae1"

# Ollama / EC2
DEFAULT_OLLAMA_URL = "http://localhost:8080"
DEFAULT_OLLAMA_MODEL = "velvet-14b-cpu-v1"


# ═════════════════════════════════════════════════════════════════════════
# Backend: Vertex AI
# ═════════════════════════════════════════════════════════════════════════

class VertexBackend:
    """Chat backend para Vertex AI dedicated endpoints."""

    def __init__(self, project: str, region: str, endpoint_id: str, verbose: bool = False):
        self.project = project
        self.region = region
        self.endpoint_id = endpoint_id
        self.verbose = verbose
        self._credentials = None
        self._dns = None

    def connect(self) -> None:
        """Resuelve el DNS del dedicated endpoint y valida credenciales."""
        from google.cloud import aiplatform

        print("Conectando con Vertex AI...")
        aiplatform.init(project=self.project, location=self.region)
        ep = aiplatform.Endpoint(self.endpoint_id)
        self._dns = ep.gca_resource.dedicated_endpoint_dns
        self._refresh_credentials()
        print(f"  Endpoint: {self.endpoint_id}")
        print(f"  DNS:      {self._dns}")

    def _refresh_credentials(self) -> None:
        import google.auth
        import google.auth.transport.requests

        if self._credentials is None:
            self._credentials, _ = google.auth.default()
        auth_req = google.auth.transport.requests.Request()
        self._credentials.refresh(auth_req)

    def send(
        self, prompt: str, system: str, params: Dict[str, Any]
    ) -> Tuple[Optional[str], float, Optional[str]]:
        """Envía un mensaje y retorna (texto, latencia, error)."""
        self._refresh_credentials()

        url = (
            f"https://{self._dns}/v1/"
            f"projects/{self.project}/locations/{self.region}/"
            f"endpoints/{self.endpoint_id}:predict"
        )

        instance: Dict[str, Any] = {"inputs": prompt}
        if system:
            instance["system"] = system
        instance["parameters"] = params
        payload = {"instances": [instance]}

        if self.verbose:
            _print_request(url, payload)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            start = time.time()
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._credentials.token}",
                    "Content-Type": "application/json",
                },
                verify=False,
                timeout=300,
            )
            elapsed = time.time() - start

        if self.verbose:
            _print_response(resp, elapsed)

        if resp.status_code != 200:
            return None, elapsed, f"Error HTTP {resp.status_code}: {resp.text[:200]}"

        return _parse_vertex_predictions(resp.json()), elapsed, None

    @property
    def label(self) -> str:
        return "Vertex AI"

    @property
    def display_name(self) -> str:
        """Nombre que aparece en las respuestas del chat."""
        return "Velvet"


# ═════════════════════════════════════════════════════════════════════════
# Backend: Ollama (EC2 vía Caddy)
# ═════════════════════════════════════════════════════════════════════════

class OllamaBackend:
    """Chat backend para Ollama a través de Caddy reverse proxy."""

    def __init__(
        self,
        base_url: str,
        model: str,
        auth_user: str = "",
        auth_password: str = "",
        verbose: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.verbose = verbose
        self._auth: Optional[HTTPBasicAuth] = None
        if auth_user and auth_password:
            self._auth = HTTPBasicAuth(auth_user, auth_password)

    def connect(self) -> None:
        """Verifica conectividad con el servidor Ollama."""
        print(f"Conectando con Ollama en {self.base_url}...")
        try:
            resp = requests.get(
                f"{self.base_url}/api/tags", auth=self._auth, timeout=10
            )
            resp.raise_for_status()
            models = resp.json().get("models", [])
            names = [m["name"] for m in models]
            print(f"  Modelos disponibles: {', '.join(names)}")
            if not any(self.model in n for n in names):
                print(f"  ⚠  Modelo '{self.model}' no encontrado en el servidor.")
        except requests.exceptions.ConnectionError:
            print(f"  ⚠  No se pudo conectar a {self.base_url}")
        except requests.exceptions.HTTPError as e:
            print(f"  ⚠  Error HTTP: {e}")

    def send(
        self, prompt: str, system: str, params: Dict[str, Any]
    ) -> Tuple[Optional[str], float, Optional[str]]:
        """Envía un mensaje y retorna (texto, latencia, error)."""
        url = f"{self.base_url}/api/generate"

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        # Mapear parámetros al formato de opciones de Ollama
        options: Dict[str, Any] = {}
        if "temperature" in params:
            options["temperature"] = params["temperature"]
        if "top_p" in params:
            options["top_p"] = params["top_p"]
        if "max_new_tokens" in params:
            options["num_predict"] = params["max_new_tokens"]
        if options:
            payload["options"] = options

        if self.verbose:
            _print_request(url, payload)

        start = time.time()
        try:
            resp = requests.post(
                url, json=payload, auth=self._auth, timeout=300
            )
            elapsed = time.time() - start
        except requests.exceptions.ConnectionError as e:
            return None, 0, f"No se pudo conectar a {self.base_url}: {e}"
        except requests.exceptions.Timeout:
            return None, 300, "Timeout (300s)"

        if self.verbose:
            _print_response(resp, elapsed)

        if resp.status_code != 200:
            return None, elapsed, f"Error HTTP {resp.status_code}: {resp.text[:200]}"

        data = resp.json()
        text = data.get("response", "")
        tokens = data.get("eval_count", 0)
        eval_ns = data.get("eval_duration", 0)

        # Calcular t/s con eval_duration (más preciso que elapsed)
        if eval_ns > 0 and tokens > 0:
            tps = tokens / (eval_ns / 1e9)
        elif elapsed > 0 and tokens > 0:
            tps = tokens / elapsed
        else:
            tps = 0

        return text, elapsed, None

    @property
    def label(self) -> str:
        return f"Ollama ({self.base_url})"

    @property
    def display_name(self) -> str:
        """Nombre derivado del modelo activo (ej: gemma-4b-cpu-v1 → Gemma)."""
        name = self.model.split("-")[0]
        return name.capitalize()


# ═════════════════════════════════════════════════════════════════════════
# Helpers comunes
# ═════════════════════════════════════════════════════════════════════════

def _print_request(url: str, payload: dict) -> None:
    print(f"\n{'─'*60}")
    print(f"  REQUEST → POST {url}")
    print(f"{'─'*60}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"{'─'*60}")


def _print_response(resp: requests.Response, elapsed: float) -> None:
    print(f"\n{'─'*60}")
    print(f"  RESPONSE ← HTTP {resp.status_code} ({elapsed:.2f}s)")
    print(f"{'─'*60}")
    try:
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    except Exception:
        print(resp.text[:500])
    print(f"{'─'*60}")


def _parse_vertex_predictions(data: dict) -> str:
    """Extrae el texto generado de la respuesta de Vertex AI."""
    predictions = data.get("predictions", [])
    if not predictions:
        return ""
    pred = predictions[0]
    if isinstance(pred, str):
        return pred
    if isinstance(pred, dict):
        return pred.get("generated_text", str(pred))
    return str(pred)


# ═════════════════════════════════════════════════════════════════════════
# CLI y loop principal
# ═════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Chat interactivo con modelos Velvet (Vertex AI / EC2 Ollama)"
    )

    # Backend
    p.add_argument(
        "--backend", choices=["vertex", "ollama"], default="vertex",
        help="Backend de inferencia (default: vertex)",
    )

    # Vertex AI
    vx = p.add_argument_group("Vertex AI")
    vx.add_argument("--project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT))
    vx.add_argument("--region", default=os.environ.get("VERTEX_REGION", DEFAULT_VERTEX_REGION))
    vx.add_argument("--endpoint-id", default=os.environ.get("VERTEX_ENDPOINT_ID", DEFAULT_VERTEX_ENDPOINT))

    # Ollama / EC2
    ol = p.add_argument_group("Ollama / EC2")
    ol.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL),
                    help="URL del reverse proxy Caddy (default: http://localhost:8080)")
    ol.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
                    help="Nombre del modelo en Ollama (default: velvet-14b-cpu-v1)")
    ol.add_argument("--auth-user", default=os.environ.get("OLLAMA_AUTH_USER", ""),
                    help="Usuario Basic Auth para Caddy")
    ol.add_argument("--auth-password", default=os.environ.get("OLLAMA_AUTH_PASSWORD", ""),
                    help="Contraseña Basic Auth para Caddy")

    # Generación
    gen = p.add_argument_group("Generación")
    gen.add_argument("--system", default=DEFAULT_SYSTEM, help="System prompt")
    gen.add_argument("--max-tokens", type=int, default=512)
    gen.add_argument("--temperature", type=float, default=0.7)
    gen.add_argument("--top-p", type=float, default=0.9)

    # Debug
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Mostrar request/response JSON completo")

    return p


def main() -> None:
    args = build_parser().parse_args()

    params: Dict[str, Any] = {
        "max_new_tokens": args.max_tokens,
        "temperature": args.temperature,
        "top_p": args.top_p,
    }

    # Crear backend
    if args.backend == "vertex":
        backend = VertexBackend(
            project=args.project,
            region=args.region,
            endpoint_id=args.endpoint_id,
            verbose=args.verbose,
        )
    else:
        backend = OllamaBackend(
            base_url=args.base_url,
            model=args.model,
            auth_user=args.auth_user,
            auth_password=args.auth_password,
            verbose=args.verbose,
        )

    # Conectar
    try:
        backend.connect()
    except Exception as e:
        print(f"\n  ⚠  Error al conectar con {backend.label}: {e}")
        sys.exit(1)

    # Banner
    print()
    print(f"  Backend:  {backend.label}")
    print(f"  System:   {args.system[:80]}...")
    print(f"  Params:   max_tokens={args.max_tokens}, temp={args.temperature}, top_p={args.top_p}")
    if args.backend == "ollama":
        print(f"  Modelo:   {args.model}")
    print()
    print("─" * 60)
    print(f"  Chat con {backend.display_name}  |  Escribe 'salir' para terminar")
    print("  Comandos:")
    print("    /system <texto>  → cambiar system prompt")
    print("    /temp <valor>    → cambiar temperatura")
    print("    /tokens <valor>  → cambiar max tokens")
    print("    /model <nombre>  → cambiar modelo (solo ollama)")
    print("    /debug           → toggle request/response JSON")
    print("─" * 60)
    print()

    system = args.system

    while True:
        try:
            user_input = input("Tú: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nChao!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("salir", "exit", "quit", "q"):
            print("\nChao!")
            break

        # ── Comandos especiales ──────────────────────────────────────
        if user_input.startswith("/system "):
            system = user_input[8:].strip()
            print(f"  → System prompt actualizado: {system[:80]}")
            continue

        if user_input.startswith("/temp "):
            try:
                params["temperature"] = float(user_input[6:])
                print(f"  → Temperatura: {params['temperature']}")
            except ValueError:
                print("  → Valor inválido")
            continue

        if user_input.startswith("/tokens "):
            try:
                params["max_new_tokens"] = int(user_input[8:])
                print(f"  → Max tokens: {params['max_new_tokens']}")
            except ValueError:
                print("  → Valor inválido")
            continue

        if user_input.startswith("/model "):
            if args.backend == "ollama":
                backend.model = user_input[7:].strip()
                print(f"  → Modelo: {backend.model}")
            else:
                print("  → /model solo disponible con --backend ollama")
            continue

        if user_input == "/debug":
            backend.verbose = not backend.verbose
            print(f"  → Debug {'activado' if backend.verbose else 'desactivado'}")
            continue

        # ── Enviar mensaje ───────────────────────────────────────────
        text, elapsed, error = backend.send(user_input, system, params)

        if error:
            print(f"\n  ⚠  {error}\n")
        else:
            tokens_est = max(1, int(len(text) / 5.5)) if text else 0
            tps = tokens_est / elapsed if elapsed > 0 else 0
            print(f"\n{backend.display_name}: {text}")
            print(f"  [{elapsed:.1f}s | ~{tokens_est} tokens | ~{tps:.0f} t/s]\n")


if __name__ == "__main__":
    main()
