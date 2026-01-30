from __future__ import annotations

import os
import uvicorn
from fastapi import FastAPI

from app.api.v1.recommend_router import router as recommendation_router

# v2 router (존재하면 include)
try:
    from app.api.v2.recommend import router as v2_router
except Exception:
    v2_router = None

app = FastAPI(
    title="Recommend Service",
    description="Group restaurant recommendation service",
    version="0.1.0",
)

# V1
app.include_router(recommendation_router, prefix="/api/v1")

# Health
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "recommend"}

# V2 (feature flag)
ENABLE_V2 = os.getenv("ENABLE_V2", "0") == "1"
if ENABLE_V2 and v2_router is not None:
    app.include_router(v2_router, prefix="/api/v2")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
