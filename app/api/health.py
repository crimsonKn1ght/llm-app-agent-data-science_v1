from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from runtime.runtime_config import runtime_status

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check():
    current_status = runtime_status()
    if current_status["ready"]:
        return {"status": "ready"}

    missing = ", ".join(current_status["missing"]) or "unknown"
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "not_ready",
            "reason": f"Runtime is not initialized: missing {missing}",
        },
    )
