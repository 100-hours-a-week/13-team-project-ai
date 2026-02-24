from __future__ import annotations

import os
import uvicorn

from fastapi import FastAPI
#<<<<<<< v2-recommendation-api

#from app.api.v1.recommend_router import router as recommendation_router
from app.api.v2.recommend import router as recommend_v2_router
=======
#from prometheus_fastapi_instrumentator import Instrumentator
#from app.api.v1.recommend_router import router as recommendation_router
#>>>>>>> main

app = FastAPI(
    title="Recommend Service",
    description="Group restaurant recommendation service",
    version="0.1.0",
)

#<<<<<<< v2-recommendation-api
# -------------------------
# V1
# -------------------------
#app.include_router(recommendation_router, prefix="/api/v1")


# -------------------------
# V2 (Permanent)
# -------------------------
app.include_router(recommend_v2_router, prefix="/api/v2")

=======
#Instrumentator().instrument(app).expose(app)

#app.include_router(recommendation_router, prefix="/api/v1")
#>>>>>>> main

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "recommend", "version": "v2"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)


