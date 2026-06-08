from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict


class DecomposedQuery(TypedDict):
    sub_query_id: int
    query: str
    intent: str
    scope: str
    scope_reasoning: str
    tool_hint: str


class Citation(TypedDict, total=False):
    title: str
    url: str
    snippet: str
    rank: int
    provider: str


class AgentResultRequired(TypedDict):
    sub_query_id: int
    query: str
    agent_type: str
    result: str
    status: Literal["success", "error", "out_of_scope"]


class AgentResult(AgentResultRequired, total=False):
    error_message: str
    latency_ms: int
    output_chars: int
    source: str
    data_sources: List[str]
    citations: List[Citation]
    confidence: Literal["high", "medium", "low", "unknown"]
    tool_metadata: Dict[str, Any]


class ErrorInfo(TypedDict, total=False):
    has_error: bool
    node: Optional[str]
    error_message: Optional[str]


class GraphState(TypedDict):
    user_query: str
    conversation_id: str
    conversation_history: List[Dict[str, str]]
    conversation_summary: str
    query_type: str
    is_complex: bool
    sub_queries: List[DecomposedQuery]
    agent_results: Annotated[List[AgentResult], operator.add]
    source_contents: Annotated[Dict[str, str], lambda a, b: {**a, **b}]
    final_response: str
    compiler_metadata: Dict[str, Any]
    stream_queue: asyncio.Queue
    runtime: Any
    execution_path: Annotated[List[str], operator.add]
    expected_branches: List[str]
    completed_branches: Annotated[List[str], operator.add]
    error: ErrorInfo
    is_error_state: bool
