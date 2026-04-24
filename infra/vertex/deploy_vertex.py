#!/usr/bin/env python3
# =============================================================================
# deploy_vertex.py — Script de despliegue de modelos Velvet en Vertex AI
# =============================================================================
# Despliega modelos Velvet directamente desde Hugging Face como endpoints
# gestionados en Google Cloud Vertex AI, usando la integración nativa de HF
# con Hugging Face Deep Learning Containers.
#
# Uso:
#   python deploy_vertex.py \
#     --model-id "Almawave/Velvet-2B" \
#     --project "mi-proyecto-gcp" \
#     --region "us-central1" \
#     --machine-type "g2-standard-12" \
#     --min-replicas 0 \
#     --max-replicas 1 \
#     --revision "main"
#
# Requisitos:
#   - google-cloud-aiplatform SDK instalado (pip install google-cloud-aiplatform)
#   - Autenticación GCP configurada (gcloud auth application-default login)
#   - Permisos: aiplatform.endpoints.create, aiplatform.endpoints.deploy,
#     aiplatform.models.upload, aiplatform.endpoints.predict
# =============================================================================

import argparse
import sys
import time
import logging

# ─────────────────────────────────────────────────────────────────────────────
# Configuración de logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parsear_argumentos() -> argparse.Namespace:
    """Parsea los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Despliega modelos Velvet en Vertex AI desde Hugging Face.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Desplegar Velvet-2B con configuración por defecto
  python deploy_vertex.py \\
    --model-id "Almawave/Velvet-2B" \\
    --project "mi-proyecto-gcp" \\
    --region "us-central1"

  # Desplegar Velvet-14B con máquina personalizada y réplicas
  python deploy_vertex.py \\
    --model-id "Almawave/Velvet-14B" \\
    --project "mi-proyecto-gcp" \\
    --region "us-central1" \\
    --machine-type "g2-standard-24" \\
    --max-replicas 2 \\
    --revision "v1.0"
        """,
    )

    parser.add_argument(
        "--model-id",
        required=True,
        help='ID del modelo en Hugging Face (ej: "Almawave/Velvet-2B")',
    )
    parser.add_argument(
        "--project",
        required=True,
        help="ID del proyecto de Google Cloud",
    )
    parser.add_argument(
        "--region",
        default="us-central1",
        help="Región de Google Cloud (default: us-central1)",
    )
    parser.add_argument(
        "--machine-type",
        default="g2-standard-12",
        help="Tipo de máquina para el endpoint (default: g2-standard-12)",
    )
    parser.add_argument(
        "--min-replicas",
        type=int,
        default=0,
        help="Número mínimo de réplicas — 0 habilita scale-to-zero (default: 0)",
    )
    parser.add_argument(
        "--max-replicas",
        type=int,
        default=1,
        help="Número máximo de réplicas (default: 1)",
    )
    parser.add_argument(
        "--revision",
        default="main",
        help='Revisión o branch del repositorio HF (default: "main")',
    )

    return parser.parse_args()


def validar_argumentos(args: argparse.Namespace) -> None:
    """Valida que los argumentos proporcionados sean coherentes."""
    # Verificar formato del model-id (debe contener org/modelo)
    if "/" not in args.model_id:
        logger.error(
            "El --model-id debe tener el formato 'organización/modelo' "
            "(ej: 'Almawave/Velvet-2B'). Recibido: %s",
            args.model_id,
        )
        sys.exit(1)

    # Verificar que min_replicas <= max_replicas
    if args.min_replicas > args.max_replicas:
        logger.error(
            "--min-replicas (%d) no puede ser mayor que --max-replicas (%d).",
            args.min_replicas,
            args.max_replicas,
        )
        sys.exit(1)

    # Verificar que min_replicas y max_replicas son no negativos
    if args.min_replicas < 0 or args.max_replicas < 0:
        logger.error(
            "Los valores de réplicas deben ser >= 0. "
            "Recibido: min=%d, max=%d",
            args.min_replicas,
            args.max_replicas,
        )
        sys.exit(1)


def construir_nombre_modelo(model_id: str) -> str:
    """
    Extrae un nombre corto del model_id para usar en etiquetas.

    Ejemplo: "Almawave/Velvet-2B" → "velvet-2b"
    """
    # Tomar la parte después de la barra y convertir a minúsculas
    nombre = model_id.split("/")[-1].lower()
    # Reemplazar caracteres no válidos para etiquetas GCP (solo alfanuméricos y guiones)
    nombre_limpio = ""
    for c in nombre:
        if c.isalnum() or c == "-":
            nombre_limpio += c
        elif c in ("_", "."):
            nombre_limpio += "-"
    return nombre_limpio


def construir_publisher_model_name(model_id: str) -> str:
    """
    Construye el nombre del modelo en formato Vertex AI Publisher Model.

    Formato: publishers/hf-{org}/models/{modelo}@001

    Ejemplo:
      "Almawave/Velvet-2B" → "publishers/hf-Almawave/models/Velvet-2B@001"
    """
    partes = model_id.split("/")
    if len(partes) != 2:
        logger.error(
            "Formato de model-id inválido: '%s'. Se esperaba 'org/modelo'.",
            model_id,
        )
        sys.exit(1)

    org, modelo = partes
    return f"publishers/hf-{org}/models/{modelo}@001"


def desplegar_modelo(args: argparse.Namespace) -> None:
    """
    Ejecuta el despliegue del modelo en Vertex AI usando la integración
    nativa de Hugging Face (deployPublisherModel API).
    """
    # Importar el SDK aquí para dar un mensaje claro si no está instalado
    try:
        from google.cloud import aiplatform
        from google.api_core import exceptions as google_exceptions
    except ImportError:
        logger.error(
            "El SDK google-cloud-aiplatform no está instalado. "
            "Instálalo con: pip install google-cloud-aiplatform"
        )
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────
    # Inicializar el SDK de Vertex AI
    # ─────────────────────────────────────────────────────────────────────
    logger.info("Inicializando Vertex AI SDK...")
    logger.info("  Proyecto: %s", args.project)
    logger.info("  Región:   %s", args.region)

    try:
        aiplatform.init(project=args.project, location=args.region)
    except Exception as e:
        logger.error(
            "Error al inicializar Vertex AI SDK. Verifica que el proyecto "
            "y la región sean correctos, y que la autenticación esté configurada.\n"
            "  Proyecto: %s\n"
            "  Región:   %s\n"
            "  Detalle:  %s",
            args.project,
            args.region,
            e,
        )
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────
    # Construir el nombre del Publisher Model
    # ─────────────────────────────────────────────────────────────────────
    publisher_model_name = construir_publisher_model_name(args.model_id)
    nombre_modelo = construir_nombre_modelo(args.model_id)

    logger.info("Modelo HF:          %s", args.model_id)
    logger.info("Publisher Model:    %s", publisher_model_name)
    logger.info("Revisión HF:        %s", args.revision)
    logger.info("Tipo de máquina:    %s", args.machine_type)
    logger.info("Réplicas:           min=%d, max=%d", args.min_replicas, args.max_replicas)

    # ─────────────────────────────────────────────────────────────────────
    # Definir etiquetas para los recursos
    # ─────────────────────────────────────────────────────────────────────
    etiquetas = {
        "project": "velvet",
        "model": nombre_modelo,
        "source": "huggingface",
        "environment": "stress-test",
    }
    logger.info("Etiquetas:          %s", etiquetas)

    # ─────────────────────────────────────────────────────────────────────
    # Desplegar usando la API de Publisher Model (integración nativa HF)
    # ─────────────────────────────────────────────────────────────────────
    logger.info("Iniciando despliegue del modelo en Vertex AI...")
    logger.info("Esto puede tardar varios minutos mientras se descarga el modelo "
                "y se configura el endpoint...")

    tiempo_inicio = time.time()

    try:
        # Usar la API deployPublisherModel que integra directamente con HF
        # El SDK de Vertex AI maneja la creación del endpoint, la carga del
        # modelo desde HF y el despliegue usando HF Deep Learning Containers
        endpoint = aiplatform.Model.upload_and_deploy(
            model_id=publisher_model_name,
            display_name=f"velvet-{nombre_modelo}-endpoint",
            machine_type=args.machine_type,
            min_replica_count=args.min_replicas,
            max_replica_count=args.max_replicas,
            labels=etiquetas,
            deploy_request_timeout=1800,  # 30 minutos de timeout para el despliegue
        )

    except google_exceptions.ResourceExhausted as e:
        # Cuota insuficiente en GCP
        tiempo_transcurrido = time.time() - tiempo_inicio
        logger.error(
            "Cuota insuficiente en GCP para desplegar el modelo.\n"
            "  Modelo:    %s\n"
            "  Máquina:   %s\n"
            "  Región:    %s\n"
            "  Tiempo:    %.1f segundos\n"
            "  Detalle:   %s\n\n"
            "Sugerencia: Solicita un aumento de cuota en la consola de GCP:\n"
            "  https://console.cloud.google.com/iam-admin/quotas",
            args.model_id,
            args.machine_type,
            args.region,
            tiempo_transcurrido,
            e,
        )
        sys.exit(1)

    except google_exceptions.NotFound as e:
        # Modelo no accesible en Hugging Face o recurso no encontrado
        tiempo_transcurrido = time.time() - tiempo_inicio
        logger.error(
            "Modelo no encontrado o no accesible.\n"
            "  Modelo HF:        %s\n"
            "  Publisher Model:   %s\n"
            "  Revisión:          %s\n"
            "  Tiempo:            %.1f segundos\n"
            "  Detalle:           %s\n\n"
            "Sugerencias:\n"
            "  - Verifica que el model-id sea correcto en Hugging Face\n"
            "  - Si el modelo es gated, asegúrate de tener acceso aprobado\n"
            "  - Verifica que la revisión '%s' exista en el repositorio",
            args.model_id,
            publisher_model_name,
            args.revision,
            tiempo_transcurrido,
            e,
            args.revision,
        )
        sys.exit(1)

    except google_exceptions.InvalidArgument as e:
        # Error de configuración (región, proyecto, tipo de máquina inválido)
        tiempo_transcurrido = time.time() - tiempo_inicio
        logger.error(
            "Error de configuración en los parámetros de despliegue.\n"
            "  Proyecto:  %s\n"
            "  Región:    %s\n"
            "  Máquina:   %s\n"
            "  Tiempo:    %.1f segundos\n"
            "  Detalle:   %s\n\n"
            "Sugerencias:\n"
            "  - Verifica que el tipo de máquina '%s' esté disponible en la región '%s'\n"
            "  - Verifica que el proyecto '%s' exista y tenga Vertex AI habilitado\n"
            "  - Ejecuta: gcloud services enable aiplatform.googleapis.com",
            args.project,
            args.region,
            args.machine_type,
            tiempo_transcurrido,
            e,
            args.machine_type,
            args.region,
            args.project,
        )
        sys.exit(1)

    except google_exceptions.DeadlineExceeded as e:
        # Timeout en el despliegue
        tiempo_transcurrido = time.time() - tiempo_inicio
        logger.error(
            "Timeout durante el despliegue del modelo.\n"
            "  Modelo:    %s\n"
            "  Tiempo:    %.1f segundos\n"
            "  Detalle:   %s\n\n"
            "Sugerencias:\n"
            "  - El despliegue puede tardar hasta 30 minutos para modelos grandes\n"
            "  - Verifica el estado del endpoint en la consola de GCP:\n"
            "    https://console.cloud.google.com/vertex-ai/endpoints",
            args.model_id,
            tiempo_transcurrido,
            e,
        )
        sys.exit(1)

    except google_exceptions.PermissionDenied as e:
        # Permisos insuficientes
        logger.error(
            "Permisos insuficientes para desplegar en Vertex AI.\n"
            "  Proyecto:  %s\n"
            "  Detalle:   %s\n\n"
            "Sugerencias:\n"
            "  - Verifica que tu cuenta tenga el rol 'Vertex AI User' (roles/aiplatform.user)\n"
            "  - Ejecuta: gcloud projects add-iam-policy-binding %s \\\n"
            "      --member='user:<tu-email>' --role='roles/aiplatform.user'\n"
            "  - Ejecuta infra/vertex/auth_setup.sh para configurar la autenticación",
            args.project,
            e,
            args.project,
        )
        sys.exit(1)

    except Exception as e:
        # Error genérico no previsto
        tiempo_transcurrido = time.time() - tiempo_inicio
        logger.error(
            "Error inesperado durante el despliegue.\n"
            "  Modelo:    %s\n"
            "  Proyecto:  %s\n"
            "  Región:    %s\n"
            "  Tiempo:    %.1f segundos\n"
            "  Tipo:      %s\n"
            "  Detalle:   %s",
            args.model_id,
            args.project,
            args.region,
            tiempo_transcurrido,
            type(e).__name__,
            e,
        )
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────
    # Despliegue exitoso — imprimir resumen
    # ─────────────────────────────────────────────────────────────────────
    tiempo_total = time.time() - tiempo_inicio

    print()
    print("=============================================================================")
    print(" DESPLIEGUE COMPLETADO")
    print("=============================================================================")
    print(f" Modelo HF:         {args.model_id}")
    print(f" Revisión:          {args.revision}")
    print(f" Proyecto GCP:      {args.project}")
    print(f" Región:            {args.region}")
    print(f" Tipo de máquina:   {args.machine_type}")
    print(f" Réplicas:          min={args.min_replicas}, max={args.max_replicas}")
    print(f" Endpoint ID:       {endpoint.name}")
    print(f" Endpoint URL:      {endpoint.resource_name}")
    print(f" Etiquetas:         {etiquetas}")
    print(f" Tiempo total:      {tiempo_total:.1f} segundos")
    print("=============================================================================")
    print()
    print(" Para enviar predicciones:")
    print(f"   from google.cloud import aiplatform")
    print(f"   endpoint = aiplatform.Endpoint('{endpoint.resource_name}')")
    print(f"   response = endpoint.predict(instances=[{{'prompt': 'Hola'}}])")
    print()
    print(" Para eliminar el endpoint:")
    print(f"   python infra/vertex/undeploy_vertex.py \\")
    print(f"     --endpoint-id {endpoint.name} \\")
    print(f"     --project {args.project} \\")
    print(f"     --region {args.region} \\")
    print(f"     --confirm")
    print("=============================================================================")


def main() -> None:
    """Punto de entrada principal del script."""
    args = parsear_argumentos()
    validar_argumentos(args)
    desplegar_modelo(args)


if __name__ == "__main__":
    main()
