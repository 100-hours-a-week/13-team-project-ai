FROM vllm/vllm-openai:v0.7.2

WORKDIR /app

# netcat(nc) 설치
RUN apt-get update && apt-get install -y netcat

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/

EXPOSE 8000 8001

CMD ["sh", "-c", "python3 -m vllm.entrypoints.openai.api_server \
    --model /workspace/qwen3_final_full_model \
    --served-model-name qwen3-vl \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.8 \
    --enforce-eager \
    --port 8000 & \
    until nc -z localhost 8000; do echo 'Waiting for vLLM...'; sleep 5; done && \
    uvicorn app.main:app --host 0.0.0.0 --port 8001"]
