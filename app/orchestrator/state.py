from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict


class DecomposedQuery(TypedDict):
    sub_query_id: int
    query: str
    intent: str
    scope: str
    scope_reasoning: str
    tool_hint: str


class AgentResult(TypedDict):
    sub_query_id: int
    query: str
    agent_type: str
    result: str
    status: str


class ErrorInfo(TypedDict, total=False):
    has_error: bool
    node: Optional[str]
    error_message: Optional[str]


class GraphState(TypedDict):
    user_query: str
    conversation_id: Optional[str]

    conversation_history: List[Dict[str, str]]
    conversation_summary: str

    query_type: str
    is_complex: bool
    sub_queries: List[DecomposedQuery]

    agent_results: Annotated[List[AgentResult], operator.add]

    final_response: str
    stream_queue: asyncio.Queue

    runtime: Any

    execution_path: Annotated[List[str], operator.add]
    expected_branches: List[str]
    completed_branches: Annotated[List[str], operator.add]

    error: ErrorInfo
    is_error_state: bool
