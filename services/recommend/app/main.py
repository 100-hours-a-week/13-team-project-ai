import uvicorn
from fastapi import FastAPI
from app.api.v1.recommend_router import router as recommendation_router

app = FastAPI(
    title="Recommend Service",
    description="Group restaurant recommendation service",
    version="0.1.0"
)

app.include_router(recommendation_router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "recommend"}

if __name__ == "__main__":
    # 실행 시에도 app 패키지 경로를 사용합니다.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)