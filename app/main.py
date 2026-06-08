from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from app.logging_config import setup_logging

setup_logging()

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.routes import router
from runtime.runtime_config import init_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_runtime()
    yield


app = FastAPI(title="RAG Chatbot", lifespan=lifespan)
app.include_router(health_router)
app.include_router(health_router, prefix="/api")
app.include_router(router, prefix="/api")
