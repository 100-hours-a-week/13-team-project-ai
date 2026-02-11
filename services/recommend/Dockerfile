# ============================================
# Stage 1: Builder — 의존성 빌드 전용
# ============================================
FROM python:3.11-slim AS builder

WORKDIR /build

# 빌드 도구 설치 (psycopg2 네이티브 컴파일용)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# 의존성 파일만 먼저 복사 → 캐시 최적화
COPY requirements.txt .

# wheel로 빌드하여 /build/wheels에 저장
RUN pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# ============================================
# Stage 2: Runtime — 최소 실행 환경
# ============================================
FROM python:3.11-slim

WORKDIR /app

# 런타임에 필요한 라이브러리만 설치 (gcc 제외)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# 비루트 사용자 생성
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --no-create-home appuser

# builder 스테이지에서 빌드된 wheel만 복사하여 설치
COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/* && \
    rm -rf /tmp/wheels

# 소스 코드 복사 (가장 자주 변경 → 최하단)
COPY . .

# 소유권 변경 및 사용자 전환
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# HEALTHCHECK — 컨테이너 헬스 자동 감지
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ENTRYPOINT = 고정 실행 파일, CMD = 기본 인자 (오버라이드 가능)
ENTRYPOINT ["uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
