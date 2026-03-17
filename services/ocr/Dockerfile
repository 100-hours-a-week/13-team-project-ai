FROM vllm/vllm-openai:latest

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/
COPY entrypoint.py /app/

EXPOSE 8000 8001

ENTRYPOINT ["python3", "entrypoint.py"]