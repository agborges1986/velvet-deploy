#!/usr/bin/env python3
"""
generate_modelfile.py — Generador de Modelfiles para Ollama

Genera archivos Modelfile optimizados para CPU según el modelo Velvet
especificado. Cada Modelfile incluye la ruta al archivo GGUF, parámetros
de inferencia ajustados al tamaño del modelo y un prompt de sistema en español.

Uso como módulo:
    from generate_modelfile import generate_modelfile
    ruta = generate_modelfile("velvet-2b", "./models/velvet-2b-cpu-v1-Q4_K_M.gguf", "v1", "./modelfiles/")

Uso como CLI:
    python generate_modelfile.py \
        --model velvet-2b \
        --gguf-path ./models/velvet-2b-cpu-v1-Q4_K_M.gguf \
        --version v1 \
        --output-dir ./modelfiles/
"""

import argparse
import os
import sys
from pathlib import Path


# ---- Configuración por modelo ----
# Parámetros de inferencia optimizados para CPU según el tamaño del modelo.
# Velvet-2B usa contexto más reducido para menor consumo de RAM.
# Velvet-14B permite contexto más amplio dado su mayor capacidad.
CONFIGURACION_MODELOS = {
    "velvet-2b": {
        "num_ctx": 4096,       # Ventana de contexto (aumentado de 2048 — RAM suficiente)
        "num_predict": 512,    # Máximo de tokens a generar
        "temperature": 0.3,    # Temperatura baja para respuestas más deterministas
        "top_p": 0.9,          # Nucleus sampling
    },
    "velvet-14b": {
        "num_ctx": 4096,       # Ventana de contexto más amplia para el modelo grande
        "num_predict": 1024,   # Más tokens de generación para respuestas más completas
        "temperature": 0.3,
        "top_p": 0.9,
    },
    "gemma-4b": {
        "num_ctx": 4096,       # Contexto práctico para CPU con 20 GB RAM
        "num_predict": 512,    # Alineado con velvet-2b (modelo de tamaño similar ~4B)
        "temperature": 0.3,    # Consistente con modelos Velvet para evaluación
        "top_p": 0.9,          # Nucleus sampling estándar
    },
}

# Campos obligatorios que toda configuración de modelo debe incluir.
CAMPOS_OBLIGATORIOS = {"num_ctx", "num_predict", "temperature", "top_p"}

# Prompt de sistema en español, compartido por todos los modelos Velvet.
# Instruye al modelo a responder de forma profesional, concisa y estructurada.
PROMPT_SISTEMA = (
    "Eres un asistente profesional. Responde de forma concisa y estructurada "
    "en español. Usa viñetas para listas. Proporciona datos precisos y verificables."
)


def _extraer_componentes(model_name: str) -> tuple[str, str]:
    """
    Extrae la familia y el tamaño de un nombre de modelo.

    Separa el nombre usando el último guión como delimitador.

    Ejemplos:
        "velvet-2b"  → ("velvet", "2b")
        "gemma-4b"   → ("gemma", "4b")
        "llama-8b"   → ("llama", "8b")

    Parámetros:
        model_name: Nombre del modelo con formato "{familia}-{tamaño}"

    Retorna:
        Tupla (familia, tamaño)

    Lanza:
        ValueError: Si el nombre no contiene un guión
    """
    partes = model_name.rsplit("-", 1)
    if len(partes) != 2 or not partes[0] or not partes[1]:
        raise ValueError(
            f"Formato de nombre inválido: '{model_name}'. "
            f"Esperado: '{{familia}}-{{tamaño}}'"
        )
    return partes[0], partes[1]


def _validar_config(model_name: str, config: dict) -> None:
    """
    Valida que la configuración del modelo incluya todos los campos obligatorios.

    Parámetros:
        model_name: Nombre del modelo (para mensajes de error)
        config:     Diccionario de configuración a validar

    Lanza:
        ValueError: Si faltan campos obligatorios
    """
    faltantes = CAMPOS_OBLIGATORIOS - set(config.keys())
    if faltantes:
        raise ValueError(
            f"Configuración incompleta para '{model_name}': "
            f"faltan los campos {faltantes}"
        )


def generate_modelfile(
    model_name: str,
    gguf_path: str,
    version: str = "v1",
    output_dir: str = "./modelfiles/",
) -> str:
    """
    Genera un Modelfile para Ollama con la configuración específica del modelo.

    Parámetros:
        model_name: Nombre del modelo ("velvet-2b" o "velvet-14b")
        gguf_path:  Ruta al archivo GGUF del modelo
        version:    Versión del modelo (ej: "v1")
        output_dir: Directorio donde se guardará el Modelfile

    Retorna:
        Ruta absoluta al Modelfile generado

    Lanza:
        ValueError: Si el nombre del modelo no es reconocido
        FileNotFoundError: Si la ruta al GGUF no existe
    """
    # Normalizar el nombre del modelo a minúsculas
    model_name = model_name.lower().strip()

    # Validar que el modelo es reconocido
    if model_name not in CONFIGURACION_MODELOS:
        modelos_validos = ", ".join(CONFIGURACION_MODELOS.keys())
        raise ValueError(
            f"Modelo no reconocido: '{model_name}'. "
            f"Modelos válidos: {modelos_validos}"
        )

    # Validar que el archivo GGUF existe
    if not os.path.exists(gguf_path):
        raise FileNotFoundError(
            f"Archivo GGUF no encontrado: '{gguf_path}'"
        )

    # Obtener la configuración del modelo
    config = CONFIGURACION_MODELOS[model_name]

    # Validar que la configuración tiene todos los campos obligatorios
    _validar_config(model_name, config)

    # Extraer familia y tamaño del nombre del modelo
    familia, tamano = _extraer_componentes(model_name)

    # Construir el nombre del Modelfile según la convención:
    # {familia}-{tamaño}-cpu-{versión}.Modelfile
    nombre_modelfile = f"{familia}-{tamano}-cpu-{version}.Modelfile"

    # Crear el directorio de salida si no existe
    os.makedirs(output_dir, exist_ok=True)

    # Ruta completa del Modelfile
    ruta_modelfile = os.path.join(output_dir, nombre_modelfile)

    # Generar el contenido del Modelfile
    contenido = _generar_contenido_modelfile(gguf_path, config)

    # Escribir el Modelfile
    with open(ruta_modelfile, "w", encoding="utf-8") as f:
        f.write(contenido)

    ruta_absoluta = os.path.abspath(ruta_modelfile)
    print(f"[INFO] Modelfile generado: {ruta_absoluta}")
    return ruta_absoluta


def _generar_contenido_modelfile(gguf_path: str, config: dict) -> str:
    """
    Genera el contenido textual del Modelfile.

    El formato sigue la especificación de Ollama:
    - FROM: ruta al archivo GGUF base
    - PARAMETER: parámetros de inferencia
    - SYSTEM: prompt de sistema

    Parámetros:
        gguf_path: Ruta al archivo GGUF
        config:    Diccionario con los parámetros del modelo

    Retorna:
        Contenido del Modelfile como cadena de texto
    """
    lineas = []

    # Ruta al modelo base (archivo GGUF)
    lineas.append(f"FROM {gguf_path}")
    lineas.append("")

    # Parámetros de inferencia optimizados para CPU
    lineas.append(f"PARAMETER temperature {config['temperature']}")
    lineas.append(f"PARAMETER top_p {config['top_p']}")
    lineas.append(f"PARAMETER num_ctx {config['num_ctx']}")
    lineas.append(f"PARAMETER num_predict {config['num_predict']}")
    lineas.append("")

    # Prompt de sistema en español
    lineas.append('SYSTEM """')
    lineas.append(PROMPT_SISTEMA)
    lineas.append('"""')
    lineas.append("")

    return "\n".join(lineas)


def _parsear_argumentos() -> argparse.Namespace:
    """
    Parsea los argumentos de línea de comandos.

    Retorna:
        Namespace con los argumentos parseados
    """
    parser = argparse.ArgumentParser(
        description="Genera Modelfiles para Ollama optimizados para CPU.",
        epilog=(
            "Ejemplo:\n"
            "  python generate_modelfile.py \\\n"
            "    --model velvet-2b \\\n"
            "    --gguf-path ./models/velvet-2b-cpu-v1-Q4_K_M.gguf \\\n"
            "    --version v1 \\\n"
            "    --output-dir ./modelfiles/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--model",
        required=True,
        choices=list(CONFIGURACION_MODELOS.keys()),
        help="Nombre del modelo (opciones: velvet-2b, velvet-14b, gemma-4b)",
    )
    parser.add_argument(
        "--gguf-path",
        required=True,
        help="Ruta al archivo GGUF del modelo",
    )
    parser.add_argument(
        "--version",
        default="v1",
        help="Versión del modelo (default: v1)",
    )
    parser.add_argument(
        "--output-dir",
        default="./modelfiles/",
        help="Directorio de salida para el Modelfile (default: ./modelfiles/)",
    )

    return parser.parse_args()


def main() -> None:
    """
    Punto de entrada principal cuando se ejecuta como script CLI.
    Parsea argumentos, genera el Modelfile y muestra la ruta resultante.
    """
    args = _parsear_argumentos()

    try:
        ruta = generate_modelfile(
            model_name=args.model,
            gguf_path=args.gguf_path,
            version=args.version,
            output_dir=args.output_dir,
        )
        print(f"[OK] Modelfile generado exitosamente: {ruta}")
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
