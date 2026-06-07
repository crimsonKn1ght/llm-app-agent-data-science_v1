from __future__ import annotations

import logging
from typing import Any, Dict

from app.orchestrator.events import emit_progress
from app.orchestrator.state import GraphState

logger = logging.getLogger(__name__)


async def router_node(state: GraphState) -> Dict[str, Any]:
    queue = state["stream_queue"]
    await emit_progress(queue, "Started pipeline")
    logger.info("Pipeline started | query=%r", state["user_query"][:80])
    return {"execution_path": ["router_node"]}
