from openai import AsyncOpenAI
from app.core.config import settings
import httpx
import os
from langsmith import traceable

# LangSmith 환경변수 설정 (OS 레벨에서 인식 필요)
os.environ["LANGCHAIN_TRACING_V2"] = settings.LANGCHAIN_TRACING_V2
os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGCHAIN_ENDPOINT

# client를 전역으로 초기화 (세션 재사용)
# vLLM과 Ollama(v1) 모두 OpenAI 방식의 API를 지원합니다.
client = AsyncOpenAI(
    base_url=settings.VLLM_BASE_URL,
    api_key="ollama",
    timeout=float(settings.LLM_TIMEOUT_SEC)
)

@traceable(run_type="llm", name="vLLM Chat Completion")
async def chat(messages: list[dict], max_tokens: int = 600, temperature: float = 0.1) -> str:
    """vLLM/Ollama OpenAI API compatible client using openai-python"""
    try:
        response = await client.chat.completions.create(
            model=settings.VLLM_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=["<|im_start|>", "<|im_end|>", "<|endoftext|>"]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[LLM Client] Error with {len(messages)} messages: {e}")
        return "LLM 연결 중 오류가 발생했습니다."
