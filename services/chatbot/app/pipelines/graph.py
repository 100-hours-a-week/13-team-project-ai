# app/pipelines/graph.py
import operator
from typing import Annotated, List, TypedDict, Optional
from datetime import datetime

from langgraph.graph import StateGraph, END
from app.core.classifier import classify_query
from app.pipelines.multistep import multistep_pipeline
from app.pipelines.hyde import hyde_pipeline
from app.pipelines.multiturn import multiturn_pipeline
from app.core.config import settings

# ──────────────────────────────────────────
# 1. State 정의
# ──────────────────────────────────────────
class AgentState(TypedDict):
    """에이전트 워크플로우를 흐르는 상태 객체"""
    user_id: str
    message: str
    history: List[dict]
    current_time: str
    pipeline: str
    answer: Optional[str]

# ──────────────────────────────────────────
# 2. 노드(Nodes) 정의
# ──────────────────────────────────────────

async def classifier_node(state: AgentState):
    """질문의 의도를 분석하여 파이프라인을 결정"""
    pipeline = classify_query(state["message"], state["history"])
    return {"pipeline": pipeline}

async def multistep_node(state: AgentState):
    """Multistep 파이프라인 실행"""
    answer = await multistep_pipeline(state["message"], state["current_time"])
    return {"answer": answer}

async def hyde_node(state: AgentState):
    """HyDE 파이프라인 실행"""
    answer = await hyde_pipeline(state["message"], state["current_time"])
    return {"answer": answer}

async def multiturn_node(state: AgentState):
    """Multiturn 파이프라인 실행"""
    answer = await multiturn_pipeline(state["message"], state["history"], state["current_time"])
    return {"answer": answer}

# ──────────────────────────────────────────
# 3. 라우팅 로직 (Conditional Edge)
# ──────────────────────────────────────────

def route_by_pipeline(state: AgentState):
    """분류된 파이프라인에 따라 다음 노드 결정"""
    return state["pipeline"]

# ──────────────────────────────────────────
# 4. 그래프 구축
# ──────────────────────────────────────────

workflow = StateGraph(AgentState)

# 노드 추가
workflow.add_node("classifier", classifier_node)
workflow.add_node("multistep",  multistep_node)
workflow.add_node("hyde",       hyde_node)
workflow.add_node("multiturn",  multiturn_node)

# 엣지 연결
workflow.set_entry_point("classifier")

# 파이프라인 종류에 따른 조건부 분기
workflow.add_conditional_edges(
    "classifier",
    route_by_pipeline,
    {
        "multistep": "multistep",
        "hyde":      "hyde",
        "multiturn": "multiturn"
    }
)

# 모든 파이프라인 후 종료
workflow.add_edge("multistep", END)
workflow.add_edge("hyde",      END)
workflow.add_edge("multiturn", END)

# 앱 실행용 컴파일
app_graph = workflow.compile()
