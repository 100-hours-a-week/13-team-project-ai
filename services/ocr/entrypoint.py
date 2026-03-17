import subprocess
import time
import sys
import httpx  

def run_vllm():
    return subprocess.Popen([
        "python3", "-m", "vllm.entrypoints.openai.api_server",
        "--model", "/workspace/qwen3_final_full_model",
        "--served-model-name", "/workspace/qwen3_final_full_model",
        "--max-model-len", "8192",
        "--gpu-memory-utilization", "0.9",
        "--enable-prefix-caching",
        "--port", "8000"
    ])

def run_fastapi():
    return subprocess.Popen([
        "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"
    ])

print("Starting vLLM engine...")
vllm_proc = run_vllm()

print(" Waiting for vLLM to be ready (this may take a few minutes)...")
while True:
    try:
        with httpx.Client() as client:
            response = client.get("http://localhost:8000/health")
            if response.status_code == 200:
                print("vLLM is up and running!")
                break
    except Exception:
        pass
    time.sleep(5)

print("Starting FastAPI server...")
api_proc = run_fastapi()

try:
    while True:
        if vllm_proc.poll() is not None:
            print("vLLM process terminated unexpectedly.")
            break
        if api_proc.poll() is not None:
            print("FastAPI process terminated unexpectedly.")
            break
        time.sleep(5)
finally:
    vllm_proc.terminate()
    api_proc.terminate()
    sys.exit(1)