# ============================================
# Stage 1: Build stage (의존성 컴파일)
# ============================================
FROM python:3.11.9-slim AS builder

WORKDIR /build

# 빌드 도구 설치 (psycopg2 컴파일에 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 가상환경 생성 (의존성 격리)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ============================================
# Stage 2: Runtime stage (실행 환경)
# ============================================
FROM python:3.11.9-slim AS runtime

WORKDIR /app

# 런타임 의존성만 설치 (gcc 제외)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 빌드 스테이지에서 가상환경 복사
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 비root 유저 생성 및 전환
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

# 애플리케이션 코드 복사
COPY --chown=appuser:appgroup . .

# 유저 전환
USER appuser

# 포트 노출
EXPOSE 8000

# 헬스체크 (30초마다 /health 엔드포인트 확인)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 실행 (프로덕션 설정)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]