#!/bin/bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/ec2-user/.local/bin
export HF_TOKEN="${HF_TOKEN:?Error: HF_TOKEN no configurado. Exporta la variable antes de ejecutar.}"

echo "=== PASO 1: Instalar dependencias de compilacion ==="
sudo dnf install -y gcc gcc-c++ cmake make python3-devel 2>&1 | tail -3

echo "=== PASO 2: Clonar llama.cpp ==="
cd /home/ec2-user
if [ ! -d "llama.cpp" ]; then
    git clone https://github.com/ggerganov/llama.cpp.git
fi
cd llama.cpp

echo "=== PASO 3: Compilar llama.cpp (CPU only) ==="
cmake -B build -DGGML_CUDA=OFF -DGGML_VULKAN=OFF 2>&1 | tail -5
cmake --build build --config Release -j$(nproc) 2>&1 | tail -5

echo "=== PASO 4: Instalar dependencias Python para conversion ==="
pip3 install --user torch numpy sentencepiece transformers protobuf 2>&1 | tail -3

echo "=== PASO 5: Descargar Velvet-2B desde Hugging Face ==="
mkdir -p /home/ec2-user/models
huggingface-cli download Almawave/Velvet-2B --local-dir /home/ec2-user/models/Velvet-2B 2>&1 | tail -5

echo "=== PASO 6: Convertir Velvet-2B a GGUF (F16) ==="
python3 /home/ec2-user/llama.cpp/convert_hf_to_gguf.py \
    /home/ec2-user/models/Velvet-2B \
    --outfile /home/ec2-user/models/velvet-2b-cpu-v1-F16.gguf \
    --outtype f16 2>&1 | tail -10

echo "=== PASO 7: Cuantizar Velvet-2B a Q4_K_M ==="
/home/ec2-user/llama.cpp/build/bin/llama-quantize \
    /home/ec2-user/models/velvet-2b-cpu-v1-F16.gguf \
    /home/ec2-user/models/velvet-2b-cpu-v1-Q4_K_M.gguf \
    Q4_K_M 2>&1 | tail -10

echo "=== PASO 8: Limpiar archivo F16 intermedio ==="
rm -f /home/ec2-user/models/velvet-2b-cpu-v1-F16.gguf

echo "=== PASO 9: Crear Modelfile para Velvet-2B ==="
mkdir -p /home/ec2-user/velvet-project/infra/modelfiles
cat > /home/ec2-user/velvet-project/infra/modelfiles/velvet-2b-cpu-v1.Modelfile << 'MODELEOF'
FROM /home/ec2-user/models/velvet-2b-cpu-v1-Q4_K_M.gguf

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 2048
PARAMETER num_predict 512

SYSTEM """
Eres un asistente profesional. Responde de forma concisa y estructurada en español.
Usa viñetas para listas. Proporciona datos precisos y verificables.
"""
MODELEOF

echo "=== PASO 10: Cargar modelo en Ollama ==="
docker exec velvet-ollama ollama create velvet-2b-cpu-v1 -f - < /home/ec2-user/velvet-project/infra/modelfiles/velvet-2b-cpu-v1.Modelfile 2>&1 | tail -5

echo "=== PASO 11: Verificar modelo cargado ==="
docker exec velvet-ollama ollama list

echo "=== CONVERSION VELVET-2B COMPLETADA ==="
ls -lh /home/ec2-user/models/velvet-2b-cpu-v1-Q4_K_M.gguf
