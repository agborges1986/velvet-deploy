"""
Tests basados en propiedades para el generador de Modelfiles.

Utiliza Hypothesis para verificar propiedades universales de correctitud
del módulo generate_modelfile.py con inputs generados aleatoriamente.
"""

import os
import re
import tempfile

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from infra.conversion.generate_modelfile import (
    generate_modelfile,
    _generar_contenido_modelfile,
    _extraer_componentes,
    _validar_config,
    CONFIGURACION_MODELOS,
    CAMPOS_OBLIGATORIOS,
)


def parsear_modelfile(contenido: str) -> dict:
    """
    Parsea el contenido de un Modelfile y extrae los valores de FROM, PARAMETER y SYSTEM.

    Parámetros:
        contenido: Contenido textual de un Modelfile

    Retorna:
        Diccionario con las claves:
            - "from": ruta del modelo (str)
            - "parameters": dict con los parámetros de inferencia
            - "system": texto del prompt de sistema (str)
    """
    resultado = {
        "from": "",
        "parameters": {},
        "system": "",
    }

    # Extraer FROM
    from_match = re.search(r"^FROM\s+(.+)$", contenido, re.MULTILINE)
    if from_match:
        resultado["from"] = from_match.group(1).strip()

    # Extraer PARAMETERs
    for param_match in re.finditer(
        r"^PARAMETER\s+(\w+)\s+(.+)$", contenido, re.MULTILINE
    ):
        nombre = param_match.group(1)
        valor_str = param_match.group(2).strip()
        # Intentar convertir a int o float
        try:
            valor = int(valor_str)
        except ValueError:
            try:
                valor = float(valor_str)
            except ValueError:
                valor = valor_str
        resultado["parameters"][nombre] = valor

    # Extraer SYSTEM (contenido entre triple comillas)
    system_match = re.search(
        r'^SYSTEM\s+"""\s*\n(.*?)\n"""', contenido, re.MULTILINE | re.DOTALL
    )
    if system_match:
        resultado["system"] = system_match.group(1).strip()

    return resultado


# =============================================================================
# Propiedad 3: Generación de Modelfile válido para cualquier modelo configurado
# Feature: gema4-aws-deployment, Property 3
# Valida: Requisitos 2.1, 2.5
# =============================================================================


@given(model_name=st.sampled_from(list(CONFIGURACION_MODELOS.keys())))
@settings(max_examples=100)
def test_modelfile_valido_para_cualquier_modelo_configurado(model_name: str):
    """Para cada modelo en CONFIGURACION_MODELOS, generar un Modelfile con una
    ruta GGUF temporal y verificar que contiene: directiva FROM con la ruta,
    directivas PARAMETER para los 4 campos obligatorios con los valores
    correctos, y directiva SYSTEM con texto no vacío."""
    config = CONFIGURACION_MODELOS[model_name]

    with tempfile.TemporaryDirectory() as tmpdir:
        # Crear un archivo GGUF dummy para que pase la validación de existencia
        gguf_path = os.path.join(tmpdir, f"{model_name}-test.gguf")
        with open(gguf_path, "w") as f:
            f.write("dummy")

        output_dir = os.path.join(tmpdir, "modelfiles")
        ruta = generate_modelfile(
            model_name=model_name,
            gguf_path=gguf_path,
            version="v1",
            output_dir=output_dir,
        )

        # Leer y parsear el Modelfile generado
        with open(ruta, "r", encoding="utf-8") as f:
            contenido = f.read()

        parsed = parsear_modelfile(contenido)

        # Verificar directiva FROM
        assert parsed["from"] == gguf_path, (
            f"FROM esperado: {gguf_path}, obtenido: {parsed['from']}"
        )

        # Verificar que los 4 parámetros obligatorios están presentes
        for campo in CAMPOS_OBLIGATORIOS:
            assert campo in parsed["parameters"], (
                f"Parámetro '{campo}' no encontrado en el Modelfile"
            )
            # Verificar que el valor coincide con la configuración
            esperado = config[campo]
            obtenido = parsed["parameters"][campo]
            if isinstance(esperado, float):
                assert abs(obtenido - esperado) < 1e-6, (
                    f"Parámetro '{campo}': esperado {esperado}, obtenido {obtenido}"
                )
            else:
                assert obtenido == esperado, (
                    f"Parámetro '{campo}': esperado {esperado}, obtenido {obtenido}"
                )

        # Verificar directiva SYSTEM no vacía
        assert parsed["system"], "El prompt de sistema SYSTEM está vacío"


# =============================================================================
# Propiedad 4: Convención de nombres de archivo Modelfile
# Feature: gema4-aws-deployment, Property 4
# Valida: Requisitos 2.4
# =============================================================================


@given(
    familia=st.from_regex(r"[a-z]{2,10}", fullmatch=True),
    tamano=st.from_regex(r"[0-9]{1,3}b", fullmatch=True),
    version=st.from_regex(r"v[0-9]{1,2}", fullmatch=True),
)
@settings(max_examples=100)
def test_convencion_nombres_modelfile(familia: str, tamano: str, version: str):
    """Para nombres de modelo con formato {familia}-{tamaño} y versiones
    aleatorias, verificar que el archivo generado se llama
    {familia}-{tamaño}-cpu-{versión}.Modelfile."""
    model_name = f"{familia}-{tamano}"

    # Registrar temporalmente el modelo en CONFIGURACION_MODELOS
    config_temp = {
        "num_ctx": 4096,
        "num_predict": 512,
        "temperature": 0.3,
        "top_p": 0.9,
    }
    original_config = CONFIGURACION_MODELOS.copy()
    CONFIGURACION_MODELOS[model_name] = config_temp

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            gguf_path = os.path.join(tmpdir, f"{model_name}-test.gguf")
            with open(gguf_path, "w") as f:
                f.write("dummy")

            output_dir = os.path.join(tmpdir, "modelfiles")
            ruta = generate_modelfile(
                model_name=model_name,
                gguf_path=gguf_path,
                version=version,
                output_dir=output_dir,
            )

            nombre_esperado = f"{familia}-{tamano}-cpu-{version}.Modelfile"
            nombre_obtenido = os.path.basename(ruta)
            assert nombre_obtenido == nombre_esperado, (
                f"Nombre esperado: {nombre_esperado}, obtenido: {nombre_obtenido}"
            )
    finally:
        # Restaurar configuración original
        CONFIGURACION_MODELOS.clear()
        CONFIGURACION_MODELOS.update(original_config)


# =============================================================================
# Propiedad 5: Modelos no reconocidos producen error descriptivo
# Feature: gema4-aws-deployment, Property 5
# Valida: Requisitos 2.6
# =============================================================================


@given(nombre=st.text(min_size=1, max_size=50))
@settings(max_examples=100)
def test_modelo_no_reconocido_produce_error_descriptivo(nombre: str):
    """Para cualquier string que no sea una clave de CONFIGURACION_MODELOS,
    verificar que generate_modelfile() lanza ValueError con un mensaje que
    contenga todos los nombres de modelos válidos incluyendo gemma-4b."""
    # Filtrar nombres que coincidan con modelos válidos (normalizado a minúsculas)
    assume(nombre.lower().strip() not in CONFIGURACION_MODELOS)

    with pytest.raises(ValueError) as exc_info:
        generate_modelfile(
            model_name=nombre,
            gguf_path="/dummy/path.gguf",
        )

    mensaje = str(exc_info.value)
    # Verificar que el mensaje contiene todos los modelos válidos
    for modelo_valido in CONFIGURACION_MODELOS:
        assert modelo_valido in mensaje, (
            f"Modelo válido '{modelo_valido}' no encontrado en el mensaje de error: {mensaje}"
        )


# =============================================================================
# Propiedad 6: Validación de campos obligatorios en configuración
# Feature: gema4-aws-deployment, Property 6
# Valida: Requisitos 6.3
# =============================================================================


@given(campos_presentes=st.sets(st.sampled_from(sorted(CAMPOS_OBLIGATORIOS))))
@settings(max_examples=100)
def test_validacion_campos_obligatorios(campos_presentes: set):
    """Generar diccionarios con subconjuntos aleatorios de los campos
    obligatorios y verificar que _validar_config() rechaza los incompletos
    y acepta los completos."""
    # Construir un diccionario con solo los campos seleccionados
    valores_default = {
        "num_ctx": 4096,
        "num_predict": 512,
        "temperature": 0.3,
        "top_p": 0.9,
    }
    config = {k: v for k, v in valores_default.items() if k in campos_presentes}

    if campos_presentes == CAMPOS_OBLIGATORIOS:
        # Todos los campos presentes → debe aceptar sin error
        _validar_config("test-model", config)
    else:
        # Faltan campos → debe rechazar con ValueError
        with pytest.raises(ValueError) as exc_info:
            _validar_config("test-model", config)
        mensaje = str(exc_info.value)
        # Verificar que menciona los campos faltantes
        faltantes = CAMPOS_OBLIGATORIOS - campos_presentes
        for campo in faltantes:
            assert campo in mensaje, (
                f"Campo faltante '{campo}' no mencionado en el error: {mensaje}"
            )


# =============================================================================
# Propiedad 7: Round-trip de generación y parseo de Modelfile
# Feature: gema4-aws-deployment, Property 7
# Valida: Requisitos 6.4
# =============================================================================


@given(
    num_ctx=st.integers(min_value=2048, max_value=8192),
    num_predict=st.integers(min_value=256, max_value=1024),
    temperature=st.floats(min_value=0.1, max_value=0.7, allow_nan=False, allow_infinity=False),
    top_p=st.floats(min_value=0.8, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_roundtrip_generacion_parseo_modelfile(
    num_ctx: int, num_predict: int, temperature: float, top_p: float
):
    """Generar configuraciones aleatorias con valores en rangos válidos,
    generar un Modelfile con _generar_contenido_modelfile(), parsear el
    contenido resultante y verificar que los valores coinciden con la
    configuración original."""
    config = {
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "temperature": temperature,
        "top_p": top_p,
    }

    gguf_path = "/models/test-model.gguf"
    contenido = _generar_contenido_modelfile(gguf_path, config)
    parsed = parsear_modelfile(contenido)

    # Verificar FROM
    assert parsed["from"] == gguf_path

    # Verificar parámetros enteros
    assert parsed["parameters"]["num_ctx"] == num_ctx
    assert parsed["parameters"]["num_predict"] == num_predict

    # Verificar parámetros float (con tolerancia por representación textual)
    assert abs(parsed["parameters"]["temperature"] - temperature) < 1e-6, (
        f"temperature: esperado {temperature}, obtenido {parsed['parameters']['temperature']}"
    )
    assert abs(parsed["parameters"]["top_p"] - top_p) < 1e-6, (
        f"top_p: esperado {top_p}, obtenido {parsed['parameters']['top_p']}"
    )

    # Verificar SYSTEM no vacío
    assert parsed["system"], "El prompt de sistema SYSTEM está vacío"
