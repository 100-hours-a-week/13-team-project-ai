import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services.llm import LLMService
from app.routers.health import router as health_router
from app.routers.gen import router as gen_router

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = "moyeobab-ocr"

app = FastAPI(title="Qwen API (minimal)")

@app.get("/")
async def root():
    return {"message": "OCR Server is running", "status": "online"}


@app.on_event("startup")
async def startup():

    app.state.llm = LLMService()     
    app.state.ocr_semaphore = asyncio.Semaphore(5)

@app.on_event("shutdown")
async def shutdown():
    await app.state.llm.close()
    print("OCR Server is shutting down, client closed.")

app.include_router(health_router)
app.include_router(gen_router)