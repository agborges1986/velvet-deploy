#!/bin/bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/ec2-user/.local/bin

echo "=== Creando Modelfile para Velvet-2B (importación directa safetensors) ==="
mkdir -p /home/ec2-user/velvet-project/infra/modelfiles

cat > /tmp/velvet-2b-import.Modelfile << 'MODELEOF'
FROM /models/Velvet-2B

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 2048
PARAMETER num_predict 512

SYSTEM """
Eres un asistente profesional. Responde de forma concisa y estructurada en español.
Usa viñetas para listas. Proporciona datos precisos y verificables.
"""
MODELEOF

echo "=== Importando Velvet-2B en Ollama (esto puede tardar varios minutos) ==="
docker exec -i velvet-ollama ollama create velvet-2b-cpu-v1 -f - < /tmp/velvet-2b-import.Modelfile

echo "=== Verificando modelo cargado ==="
docker exec velvet-ollama ollama list

echo "=== Probando inferencia rapida ==="
docker exec velvet-ollama ollama run velvet-2b-cpu-v1 "Hola, responde con una sola palabra: funciona?" --nowordwrap

echo "=== VELVET-2B IMPORTADO EXITOSAMENTE ==="
