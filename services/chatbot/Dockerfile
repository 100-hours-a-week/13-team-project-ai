# 1. Base Image: 빌드 용량과 속도를 위한 slim 버전 사용
FROM python:3.11-slim

# 2. 작업 디렉토리 설정
WORKDIR /app

# 3. 환경 변수 설정
# Python이 pyc 파일을 생성하지 않게 하고, 출력을 버퍼링 없이 바로 표시하도록 함 
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 4. 필수 시스템 패키지 설치
# (선택) 빌드에 필요한 패키지나 DB 관련 의존성이 있다면 추가 (예: gcc, libpq-dev 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 5. 의존성 파일 복사 및 설치
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 6. 미리 임베딩 모델(sentence-transformers) 및 reranker 다운로드(선택 사항 - 콜드 스타트 시간 단축)
# 이 부분을 추가해두면 런팟 컨테이너가 처음 시작할 때 수 GB의 모델을 매번 다운로드하는 것을 방지할 수 있습니다.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('snunlp/KR-SBERT-V40K-klueNLI-augSTS')"
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"

# 7. 소스 코드 복사 (전체 복사)
COPY . /app/

# 8. 컨테이너가 사용할 포트 개방 (FastAPI 기본 8000번)
EXPOSE 8000

# 9. 실행 커맨드
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
