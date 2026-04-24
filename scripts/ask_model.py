import requests, json, sys, time

url = "http://localhost:8080/api/generate"
auth = ("admin", "VelvetSecure2025!")
model = sys.argv[1] if len(sys.argv) > 1 else "velvet-14b-cpu-v1"
prompt = sys.argv[2] if len(sys.argv) > 2 else "Ciao"

payload = {
    "model": model,
    "prompt": prompt,
    "system": "Eres un asistente profesional. Responde de forma concisa y estructurada.",
    "stream": False,
    "options": {"num_ctx": 4096, "temperature": 0.3}
}

start = time.time()
r = requests.post(url, json=payload, auth=auth, timeout=300)
elapsed = time.time() - start

data = r.json()
print(data.get("response", ""))
print(f"\n--- {elapsed:.1f}s | {data.get('eval_count', 0)} tokens ---")
