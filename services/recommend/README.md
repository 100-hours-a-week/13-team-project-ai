
### 라이브러리 설치

pip install -r requirements.txt

### .env 추가
PG_HOST=
PG_PORT=
PG_USER=
PG_PASSWORD=
PG_DB=

### 로컬 테스트 서버 실행
# services/recommend 폴더로 이동
uvicorn app.main:app --reload