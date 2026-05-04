#!/usr/bin/env python3
"""
chat_compare.py — Chat comparativo: misma conversación con dos modelos

Envía cada mensaje a ambos modelos simultáneamente y muestra las respuestas
lado a lado, permitiendo comparar calidad, velocidad y estilo.

Uso:
    # Comparar dos modelos en la misma instancia EC2
    python scripts/chat_compare.py --backend ollama \
        --base-url http://18.221.11.128:8080 \
        --auth-user admin --auth-password "VelvetSecure2025!" \
        --model-a gemma-4b-cpu-v1 \
        --model-b velvet-14b-cpu-v1

    # Comparar Vertex AI vs EC2/Ollama
    python scripts/chat_compare.py \
        --backend-a vertex \
        --backend-b ollama --base-url http://18.221.11.128:8080 \
        --auth-user admin --auth-password "VelvetSecure2025!" \
        --model-b velvet-14b-cpu-v1

    # Opciones
    python scripts/chat_compare.py --backend ollama \
        --base-url http://18.221.11.128:8080 \
        --auth-user admin --auth-password "VelvetSecure2025!" \
        --model-a gemma-4b-cpu-v1 --model-b velvet-14b-cpu-v1 \
        --temperature 0.3 --max-tokens 256
"""

import argparse
import json
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
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
DEFAULT_MODEL_A = "gemma-4b-cpu-v1"
DEFAULT_MODEL_B = "velvet-14b-cpu-v1"


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
        from google.cloud import aiplatform
        print("  Conectando con Vertex AI...")
        aiplatform.init(project=self.project, location=self.region)
        ep = aiplatform.Endpoint(self.endpoint_id)
        self._dns = ep.gca_resource.dedicated_endpoint_dns
        self._refresh_credentials()
        print(f"    Endpoint: {self.endpoint_id}")

    def _refresh_credentials(self) -> None:
        import google.auth
        import google.auth.transport.requests
        if self._credentials is None:
            self._credentials, _ = google.auth.default()
        auth_req = google.auth.transport.requests.Request()
        self._credentials.refresh(auth_req)

    def send(
        self, prompt: str, system: str, params: Dict[str, Any]
    ) -> Tuple[Optional[str], float, int, float, Optional[str]]:
        """Retorna (texto, latencia, tokens, tps, error)."""
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

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            start = time.time()
            resp = requests.post(
                url, json=payload,
                headers={
                    "Authorization": f"Bearer {self._credentials.token}",
                    "Content-Type": "application/json",
                },
                verify=False, timeout=300,
            )
            elapsed = time.time() - start

        if resp.status_code != 200:
            return None, elapsed, 0, 0, f"HTTP {resp.status_code}"

        text = _parse_vertex_predictions(resp.json())
        tokens = max(1, int(len(text) / 5.5)) if text else 0
        tps = tokens / elapsed if elapsed > 0 else 0
        return text, elapsed, tokens, tps, None

    @property
    def display_name(self) -> str:
        return "Vertex"

    @property
    def label(self) -> str:
        return "Vertex AI"


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
        print(f"  Conectando con Ollama ({self.model})...")
        try:
            resp = requests.get(
                f"{self.base_url}/api/tags", auth=self._auth, timeout=10
            )
            resp.raise_for_status()
            models = resp.json().get("models", [])
            names = [m["name"] for m in models]
            if not any(self.model in n for n in names):
                print(f"    ⚠  Modelo '{self.model}' no encontrado.")
            else:
                print(f"    Modelo '{self.model}' disponible.")
        except Exception as e:
            print(f"    ⚠  Error: {e}")

    def send(
        self, prompt: str, system: str, params: Dict[str, Any]
    ) -> Tuple[Optional[str], float, int, float, Optional[str]]:
        """Retorna (texto, latencia, tokens, tps, error)."""
        url = f"{self.base_url}/api/generate"
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        options: Dict[str, Any] = {}
        if "temperature" in params:
            options["temperature"] = params["temperature"]
        if "top_p" in params:
            options["top_p"] = params["top_p"]
        if "max_new_tokens" in params:
            options["num_predict"] = params["max_new_tokens"]
        if options:
            payload["options"] = options

        start = time.time()
        try:
            resp = requests.post(
                url, json=payload, auth=self._auth, timeout=300
            )
            elapsed = time.time() - start
        except requests.exceptions.ConnectionError as e:
            return None, 0, 0, 0, f"Conexión fallida: {e}"
        except requests.exceptions.Timeout:
            return None, 300, 0, 0, "Timeout (300s)"

        if resp.status_code != 200:
            return None, elapsed, 0, 0, f"HTTP {resp.status_code}"

        data = resp.json()
        text = data.get("response", "")
        tokens = data.get("eval_count", 0)
        eval_ns = data.get("eval_duration", 0)

        if tokens == 0 and text:
            tokens = max(1, int(len(text) / 5.5))

        if eval_ns > 0 and tokens > 0:
            tps = tokens / (eval_ns / 1e9)
        elif elapsed > 0 and tokens > 0:
            tps = tokens / elapsed
        else:
            tps = 0

        return text, elapsed, tokens, tps, None

    @property
    def display_name(self) -> str:
        name = self.model.split("-")[0]
        return name.capitalize()

    @property
    def label(self) -> str:
        return f"Ollama/{self.model}"


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════

def _parse_vertex_predictions(data: dict) -> str:
    predictions = data.get("predictions", [])
    if not predictions:
        return ""
    pred = predictions[0]
    if isinstance(pred, str):
        return pred
    if isinstance(pred, dict):
        return pred.get("generated_text", str(pred))
    return str(pred)


def print_separator() -> None:
    print("═" * 70)


def print_response(name: str, text: Optional[str], elapsed: float,
                   tokens: int, tps: float, error: Optional[str]) -> None:
    """Imprime la respuesta de un modelo con formato."""
    if error:
        print(f"\n  ┌─ {name}")
        print(f"  │  ⚠  {error}")
        print(f"  └─ [{elapsed:.1f}s]")
    else:
        print(f"\n  ┌─ {name}")
        # Indentar cada línea de la respuesta
        for line in (text or "").split("\n"):
            print(f"  │  {line}")
        print(f"  └─ [{elapsed:.1f}s | {tokens} tokens | {tps:.1f} t/s]")


# ═════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Chat comparativo: misma conversación con dos modelos"
    )

    # Modo simple: ambos modelos en el mismo backend Ollama
    p.add_argument("--backend", choices=["ollama", "vertex"], default=None,
                   help="Backend compartido para ambos modelos")

    # Modo avanzado: backends diferentes para A y B
    p.add_argument("--backend-a", choices=["ollama", "vertex"], default=None,
                   help="Backend para modelo A (override)")
    p.add_argument("--backend-b", choices=["ollama", "vertex"], default=None,
                   help="Backend para modelo B (override)")

    # Modelos
    p.add_argument("--model-a", default=DEFAULT_MODEL_A,
                   help=f"Modelo A (default: {DEFAULT_MODEL_A})")
    p.add_argument("--model-b", default=DEFAULT_MODEL_B,
                   help=f"Modelo B (default: {DEFAULT_MODEL_B})")

    # Ollama / EC2
    ol = p.add_argument_group("Ollama / EC2")
    ol.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL))
    ol.add_argument("--auth-user", default=os.environ.get("OLLAMA_AUTH_USER", ""))
    ol.add_argument("--auth-password", default=os.environ.get("OLLAMA_AUTH_PASSWORD", ""))

    # Vertex AI
    vx = p.add_argument_group("Vertex AI")
    vx.add_argument("--project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT))
    vx.add_argument("--region", default=os.environ.get("VERTEX_REGION", DEFAULT_VERTEX_REGION))
    vx.add_argument("--endpoint-id", default=os.environ.get("VERTEX_ENDPOINT_ID", DEFAULT_VERTEX_ENDPOINT))

    # Generación
    gen = p.add_argument_group("Generación")
    gen.add_argument("--system", default=DEFAULT_SYSTEM)
    gen.add_argument("--max-tokens", type=int, default=512)
    gen.add_argument("--temperature", type=float, default=0.7)
    gen.add_argument("--top-p", type=float, default=0.9)

    # Ejecución
    p.add_argument("--sequential", action="store_true",
                   help="Ejecutar modelos secuencialmente (default: paralelo)")
    p.add_argument("--verbose", "-v", action="store_true")

    return p


def create_backend(backend_type: str, args, model: str):
    """Crea un backend según el tipo."""
    if backend_type == "vertex":
        return VertexBackend(
            project=args.project,
            region=args.region,
            endpoint_id=args.endpoint_id,
            verbose=args.verbose,
        )
    else:
        return OllamaBackend(
            base_url=args.base_url,
            model=model,
            auth_user=args.auth_user,
            auth_password=args.auth_password,
            verbose=args.verbose,
        )


def main() -> None:
    args = build_parser().parse_args()

    # Resolver backends
    backend_a_type = args.backend_a or args.backend or "ollama"
    backend_b_type = args.backend_b or args.backend or "ollama"

    params: Dict[str, Any] = {
        "max_new_tokens": args.max_tokens,
        "temperature": args.temperature,
        "top_p": args.top_p,
    }

    # Crear backends
    backend_a = create_backend(backend_a_type, args, args.model_a)
    backend_b = create_backend(backend_b_type, args, args.model_b)

    # Conectar
    print()
    print_separator()
    print("  CHAT COMPARATIVO — Dos modelos, una conversación")
    print_separator()
    print()

    try:
        backend_a.connect()
        backend_b.connect()
    except Exception as e:
        print(f"\n  ⚠  Error al conectar: {e}")
        sys.exit(1)

    print()
    print(f"  Modelo A: {backend_a.display_name} ({backend_a.label})")
    print(f"  Modelo B: {backend_b.display_name} ({backend_b.label})")
    print(f"  System:   {args.system[:70]}...")
    print(f"  Params:   max_tokens={args.max_tokens}, temp={args.temperature}, top_p={args.top_p}")
    mode = "secuencial" if args.sequential else "paralelo"
    print(f"  Modo:     {mode}")
    print()
    print("─" * 70)
    print("  Escribe un mensaje y ambos modelos responderán.")
    print("  Comandos: /system, /temp, /tokens, /swap, /debug, salir")
    print("─" * 70)
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

        # ── Comandos ─────────────────────────────────────────────────
        if user_input.startswith("/system "):
            system = user_input[8:].strip()
            print(f"  → System prompt: {system[:70]}")
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

        if user_input == "/swap":
            backend_a, backend_b = backend_b, backend_a
            print(f"  → Modelos intercambiados: A={backend_a.display_name}, B={backend_b.display_name}")
            continue

        if user_input == "/debug":
            backend_a.verbose = not backend_a.verbose
            backend_b.verbose = backend_a.verbose
            print(f"  → Debug {'activado' if backend_a.verbose else 'desactivado'}")
            continue

        # ── Enviar a ambos modelos ───────────────────────────────────
        if args.sequential:
            # Secuencial: A primero, luego B
            result_a = backend_a.send(user_input, system, params)
            result_b = backend_b.send(user_input, system, params)
        else:
            # Paralelo: ambos al mismo tiempo
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_a = executor.submit(backend_a.send, user_input, system, params)
                future_b = executor.submit(backend_b.send, user_input, system, params)
                result_a = future_a.result()
                result_b = future_b.result()

        # Desempaquetar: (texto, latencia, tokens, tps, error)
        text_a, elapsed_a, tokens_a, tps_a, error_a = result_a
        text_b, elapsed_b, tokens_b, tps_b, error_b = result_b

        # Mostrar respuestas
        print_response(backend_a.display_name, text_a, elapsed_a, tokens_a, tps_a, error_a)
        print_response(backend_b.display_name, text_b, elapsed_b, tokens_b, tps_b, error_b)

        # Resumen comparativo
        if not error_a and not error_b:
            faster = backend_a.display_name if elapsed_a < elapsed_b else backend_b.display_name
            diff = abs(elapsed_a - elapsed_b)
            print(f"\n  ⚡ {faster} fue {diff:.1f}s más rápido")

        print()


if __name__ == "__main__":
    main()
