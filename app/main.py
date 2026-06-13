from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from app.logging_config import setup_logging

setup_logging()

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.routes import router
from runtime.runtime_config import close_runtime, init_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_runtime()
    try:
        yield
    finally:
        await close_runtime()


app = FastAPI(title="LLM Chat Agent", lifespan=lifespan)
app.include_router(health_router)
app.include_router(health_router, prefix="/api")
app.include_router(router, prefix="/api")


def _allowed_origins() -> list[str]:
    raw_origins = os.getenv(
        "URL_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
