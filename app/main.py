from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as chat_router
from app.logging_config import setup_logging
from app.orchestrator.orchestrator_workflow import build_orchestrator_graph
from runtime.runtime_config import build_runtime, set_runtime

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = build_runtime()
    runtime.compiled_graph = build_orchestrator_graph()
    set_runtime(runtime)
    logging.getLogger(__name__).info(
        "Runtime initialized | model=%s | prompts=%s",
        runtime.llm_client.model_name,
        runtime.prompt_loader.list_prompts(),
    )
    yield


app = FastAPI(
    title="RAG Chatbot",
    description="Claude-powered chatbot with LangGraph orchestration",
    lifespan=lifespan,
)

app.include_router(chat_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    return {"status": "ready"}


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
