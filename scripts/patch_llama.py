import re

filepath = "/home/ec2-user/llama.cpp/convert_hf_to_gguf.py"

with open(filepath, "r") as f:
    content = f.read()

# Reemplazar el raise NotImplementedError por res = "default"
old = 'raise NotImplementedError("BPE pre-tokenizer was not recognized - update get_vocab_base_pre()")'
new = 'res = "default"  # Patched: use default pre-tokenizer for unrecognized models'

if old in content:
    content = content.replace(old, new)
    with open(filepath, "w") as f:
        f.write(content)
    print("Patch aplicado exitosamente")
else:
    print("No se encontro el texto a parchear (puede que ya este parcheado)")
