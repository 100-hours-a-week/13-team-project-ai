FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

WORKDIR /app

# netcat(nc) 설치
RUN apt-get update && apt-get install -y netcat

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ /app/app/

EXPOSE 8000 8001

CMD ["sh", "-c", "python3 -m vllm.entrypoints.openai.api_server \
    --model /workspace/models/qwen3-vl \
    --served-model-name /workspace/models/qwen3-vl \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.8 \
    --enforce-eager \
    --port 8000 & \
    until nc -z localhost 8000; do echo 'Waiting for vLLM...'; sleep 5; done && \
    uvicorn app.main:app --host 0.0.0.0 --port 8001"]

