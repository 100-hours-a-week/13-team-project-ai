from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title="Chatbot Service",
    description="AI Chatbot service",
    version="0.1.0",
)

Instrumentator().instrument(app).expose(app)

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "recommend"
    }
