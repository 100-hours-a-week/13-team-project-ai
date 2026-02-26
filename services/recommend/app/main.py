from __future__ import annotations

import uvicorn
from typing import Optional
from fastapi import FastAPI, APIRouter

# v2 router (존재하면 include)
v2_router: Optional[APIRouter]
try:
    from app.api.v2.recommend import router as v2_router
except ImportError:
    v2_router = None

app = FastAPI(
    title="Recommend Service",
    description="Group restaurant recommendation service",
    version="0.1.0",
)


#Instrumentator().instrument(app).expose(app)

# # V1
# app.include_router(recommendation_router, prefix="/api/v1")


# -------------------------
# V2 (Permanent)
# -------------------------
if v2_router:
    app.include_router(v2_router, prefix="/api/v2")


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "recommend", "version": "v2"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)


