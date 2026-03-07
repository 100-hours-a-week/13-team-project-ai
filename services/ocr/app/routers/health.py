from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/health")
def health(request: Request):
    llm = request.app.state.llm
    return {
        "ok": True, 
        "model": llm.model
    }