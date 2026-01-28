
### 라이브러리 설치

pip install -r requirements.txt

### .env 추가
PG_HOST=10.10.0.1
PG_PORT=5432
PG_USER=appuser
PG_PASSWORD=your_password
PG_DB=matchimban

### 로컬 테스트 서버 실행
# services/recommend 폴더로 이동
uvicorn app.main:app --reload