import json
c = json.load(open("/home/ec2-user/models/Velvet-2B/config.json"))
print(f"architectures: {c.get('architectures', [])}")
print(f"model_type: {c.get('model_type')}")
print(f"vocab_size: {c.get('vocab_size')}")
print(f"hidden_size: {c.get('hidden_size')}")
print(f"num_hidden_layers: {c.get('num_hidden_layers')}")
print(f"num_attention_heads: {c.get('num_attention_heads')}")
