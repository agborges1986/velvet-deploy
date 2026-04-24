import os
os.environ["HF_TOKEN"] = os.environ.get("HF_TOKEN", "")
from huggingface_hub import HfApi
api = HfApi()
for model_id in ["Almawave/Velvet-2B", "Almawave/Velvet-14B"]:
    try:
        info = api.model_info(model_id)
        print(f"\nModel: {info.modelId}")
        print(f"Pipeline: {info.pipeline_tag}")
        print(f"Library: {info.library_name}")
        gguf_files = [s for s in info.siblings if s.rfilename.endswith('.gguf')]
        if gguf_files:
            print("GGUF files available:")
            for g in gguf_files:
                print(f"  {g.rfilename} ({g.size})")
        else:
            print("No GGUF files. Model files:")
            for s in info.siblings[:10]:
                print(f"  {s.rfilename}")
    except Exception as e:
        print(f"\nError for {model_id}: {e}")
