from fastapi import FastAPI

app = FastAPI(
    title="Recommend Service",
    description="Group restaurant recommendation service",
    version="0.1.0",
)

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "recommend"
    }
