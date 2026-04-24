#!/usr/bin/env python3
# =============================================================================
# undeploy_vertex.py — Script de limpieza de recursos Vertex AI
# =============================================================================
# Elimina los recursos de Vertex AI creados por deploy_vertex.py:
#   - Desdesplega el modelo del endpoint
#   - Elimina el endpoint
#   - Elimina el modelo del registro de Vertex AI
#
# REQUIERE el flag --confirm para ejecutar la eliminación. Sin este flag,
# el script solo muestra un resumen de lo que se eliminaría (dry-run).
#
# Uso:
#   python undeploy_vertex.py \
#     --endpoint-id <ENDPOINT_ID> \
#     --project <PROJECT_ID> \
#     --region <REGION> \
#     --confirm
#
# Requisitos:
#   - google-cloud-aiplatform SDK instalado (pip install google-cloud-aiplatform)
#   - Autenticación GCP configurada
#   - Permisos: aiplatform.endpoints.delete, aiplatform.endpoints.undeploy,
#     aiplatform.models.delete
# =============================================================================

import argparse
import sys
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
        description="Elimina recursos de Vertex AI (endpoint, modelo desplegado, modelo registrado).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Dry-run: ver qué se eliminaría sin ejecutar
  python undeploy_vertex.py \\
    --endpoint-id 1234567890 \\
    --project mi-proyecto-gcp \\
    --region us-central1

  # Ejecutar la eliminación
  python undeploy_vertex.py \\
    --endpoint-id 1234567890 \\
    --project mi-proyecto-gcp \\
    --region us-central1 \\
    --confirm
        """,
    )

    parser.add_argument(
        "--endpoint-id",
        required=True,
        help="ID del endpoint de Vertex AI a eliminar",
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
        "--confirm",
        action="store_true",
        help="Confirmar la eliminación de recursos (sin este flag = dry-run)",
    )

    return parser.parse_args()


def obtener_info_endpoint(endpoint):
    """
    Obtiene información detallada del endpoint para mostrar en el resumen.

    Retorna un diccionario con la información del endpoint y sus modelos
    desplegados, o None si no se pudo obtener.
    """
    info = {
        "display_name": getattr(endpoint, "display_name", "desconocido"),
        "resource_name": getattr(endpoint, "resource_name", "desconocido"),
        "labels": getattr(endpoint, "labels", {}),
        "modelos_desplegados": [],
    }

    # Obtener los modelos desplegados en el endpoint
    modelos_desplegados = getattr(endpoint, "gca_resource", None)
    if modelos_desplegados and hasattr(modelos_desplegados, "deployed_models"):
        for modelo in modelos_desplegados.deployed_models:
            info["modelos_desplegados"].append({
                "deployed_model_id": getattr(modelo, "id", "desconocido"),
                "model": getattr(modelo, "model", "desconocido"),
                "display_name": getattr(modelo, "display_name", "desconocido"),
            })

    return info


def eliminar_recursos(args: argparse.Namespace) -> None:
    """
    Ejecuta la eliminación de recursos de Vertex AI:
    1. Desdesplegar modelos del endpoint
    2. Eliminar el endpoint
    3. Eliminar modelos del registro de Vertex AI
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
    try:
        aiplatform.init(project=args.project, location=args.region)
    except Exception as e:
        logger.error(
            "Error al inicializar Vertex AI SDK.\n"
            "  Proyecto: %s\n"
            "  Región:   %s\n"
            "  Detalle:  %s",
            args.project,
            args.region,
            e,
        )
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────
    # Obtener el endpoint
    # ─────────────────────────────────────────────────────────────────────
    logger.info("Buscando endpoint: %s...", args.endpoint_id)

    try:
        endpoint = aiplatform.Endpoint(args.endpoint_id)
        # Forzar la carga de datos del endpoint
        _ = endpoint.display_name
    except google_exceptions.NotFound:
        logger.error(
            "Endpoint no encontrado: %s\n"
            "  Proyecto: %s\n"
            "  Región:   %s\n\n"
            "Verifica que el endpoint ID sea correcto y que exista en el "
            "proyecto y región especificados.",
            args.endpoint_id,
            args.project,
            args.region,
        )
        sys.exit(1)
    except google_exceptions.PermissionDenied as e:
        logger.error(
            "Permisos insuficientes para acceder al endpoint.\n"
            "  Endpoint: %s\n"
            "  Detalle:  %s",
            args.endpoint_id,
            e,
        )
        sys.exit(1)
    except Exception as e:
        logger.error(
            "Error al obtener el endpoint: %s\n"
            "  Tipo:    %s\n"
            "  Detalle: %s",
            args.endpoint_id,
            type(e).__name__,
            e,
        )
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────
    # Obtener información del endpoint y modelos desplegados
    # ─────────────────────────────────────────────────────────────────────
    info = obtener_info_endpoint(endpoint)

    # ─────────────────────────────────────────────────────────────────────
    # Mostrar resumen de recursos a eliminar
    # ─────────────────────────────────────────────────────────────────────
    print()
    print("=============================================================================")
    print(" RECURSOS A ELIMINAR")
    print("=============================================================================")
    print(f" Proyecto:          {args.project}")
    print(f" Región:            {args.region}")
    print(f" Endpoint ID:       {args.endpoint_id}")
    print(f" Endpoint nombre:   {info['display_name']}")
    print(f" Recurso:           {info['resource_name']}")
    print(f" Etiquetas:         {info['labels']}")
    print()

    if info["modelos_desplegados"]:
        print(" Modelos desplegados:")
        for modelo in info["modelos_desplegados"]:
            print(f"   - ID despliegue:  {modelo['deployed_model_id']}")
            print(f"     Modelo:         {modelo['model']}")
            print(f"     Nombre:         {modelo['display_name']}")
            print()
    else:
        print(" Modelos desplegados: ninguno")
        print()

    print("=============================================================================")
    print()

    # ─────────────────────────────────────────────────────────────────────
    # Verificar flag --confirm
    # ─────────────────────────────────────────────────────────────────────
    if not args.confirm:
        print("⚠️  MODO DRY-RUN: No se eliminará ningún recurso.")
        print("   Para ejecutar la eliminación, agrega el flag --confirm")
        print()
        print("   Ejemplo:")
        print(f"   python infra/vertex/undeploy_vertex.py \\")
        print(f"     --endpoint-id {args.endpoint_id} \\")
        print(f"     --project {args.project} \\")
        print(f"     --region {args.region} \\")
        print(f"     --confirm")
        sys.exit(0)

    # ─────────────────────────────────────────────────────────────────────
    # Contadores para el resumen final
    # ─────────────────────────────────────────────────────────────────────
    recursos_eliminados = 0
    recursos_fallidos = 0
    resumen = []

    # Recopilar IDs de modelos para eliminar del registro después
    model_resource_names = []

    # ─────────────────────────────────────────────────────────────────────
    # Paso 1: Desdesplegar modelos del endpoint
    # ─────────────────────────────────────────────────────────────────────
    if info["modelos_desplegados"]:
        for modelo in info["modelos_desplegados"]:
            deployed_model_id = modelo["deployed_model_id"]
            model_resource = modelo["model"]
            logger.info(
                "Desdespliegando modelo '%s' (ID: %s) del endpoint...",
                modelo["display_name"],
                deployed_model_id,
            )

            # Guardar el resource name del modelo para eliminarlo después
            if model_resource and model_resource != "desconocido":
                model_resource_names.append(model_resource)

            try:
                endpoint.undeploy(deployed_model_id=deployed_model_id)
                logger.info("Modelo '%s' desdesplegado exitosamente.", deployed_model_id)
                resumen.append(f"  ✓ Modelo {deployed_model_id} — desdesplegado del endpoint")
                recursos_eliminados += 1
            except Exception as e:
                logger.warning(
                    "No se pudo desdesplegar el modelo '%s': %s",
                    deployed_model_id,
                    e,
                )
                resumen.append(f"  ✗ Modelo {deployed_model_id} — error al desdesplegar: {e}")
                recursos_fallidos += 1
    else:
        logger.info("No hay modelos desplegados en el endpoint.")

    # ─────────────────────────────────────────────────────────────────────
    # Paso 2: Eliminar el endpoint
    # ─────────────────────────────────────────────────────────────────────
    logger.info("Eliminando endpoint: %s...", args.endpoint_id)

    try:
        endpoint.delete(force=True)
        logger.info("Endpoint %s eliminado exitosamente.", args.endpoint_id)
        resumen.append(f"  ✓ Endpoint {args.endpoint_id} — eliminado")
        recursos_eliminados += 1
    except Exception as e:
        logger.warning(
            "No se pudo eliminar el endpoint %s: %s",
            args.endpoint_id,
            e,
        )
        resumen.append(f"  ✗ Endpoint {args.endpoint_id} — error al eliminar: {e}")
        recursos_fallidos += 1

    # ─────────────────────────────────────────────────────────────────────
    # Paso 3: Eliminar modelos del registro de Vertex AI
    # ─────────────────────────────────────────────────────────────────────
    for model_resource_name in model_resource_names:
        logger.info("Eliminando modelo del registro: %s...", model_resource_name)

        try:
            modelo_registro = aiplatform.Model(model_resource_name)
            modelo_registro.delete()
            logger.info("Modelo %s eliminado del registro.", model_resource_name)
            resumen.append(f"  ✓ Modelo {model_resource_name} — eliminado del registro")
            recursos_eliminados += 1
        except google_exceptions.NotFound:
            logger.info(
                "Modelo %s no encontrado en el registro (puede haber sido eliminado automáticamente).",
                model_resource_name,
            )
            resumen.append(f"  ○ Modelo {model_resource_name} — no encontrado (ya eliminado)")
        except Exception as e:
            logger.warning(
                "No se pudo eliminar el modelo %s del registro: %s",
                model_resource_name,
                e,
            )
            resumen.append(f"  ✗ Modelo {model_resource_name} — error al eliminar: {e}")
            recursos_fallidos += 1

    # ─────────────────────────────────────────────────────────────────────
    # Resumen final
    # ─────────────────────────────────────────────────────────────────────
    print()
    print("=============================================================================")
    print(" RESUMEN DE LIMPIEZA")
    print("=============================================================================")
    print(f" Proyecto: {args.project}")
    print(f" Región:   {args.region}")
    print()
    print(" Recursos procesados:")
    for linea in resumen:
        print(linea)
    print()
    print(f" Total eliminados:  {recursos_eliminados}")
    print(f" Total con errores: {recursos_fallidos}")
    print("=============================================================================")

    # Código de salida: 0 si todo fue exitoso, 1 si hubo errores
    if recursos_fallidos > 0:
        sys.exit(1)


def main() -> None:
    """Punto de entrada principal del script."""
    args = parsear_argumentos()
    eliminar_recursos(args)


if __name__ == "__main__":
    main()
