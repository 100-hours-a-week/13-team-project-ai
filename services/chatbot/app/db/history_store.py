# app/db/history_store.py
import aiosqlite
from app.core.config import settings

async def init_db():
    """앱 시작 시 1회 호출 — SQLite 테이블 및 인덱스 생성"""
    async with aiosqlite.connect(settings.SQLITE_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT     NOT NULL,
                role       TEXT     NOT NULL,   -- "user" | "assistant"
                content    TEXT     NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id
            ON conversations(user_id, created_at)
        """)
        await db.commit()
    print("SQLite 채팅 히스토리 초기화 완료")


async def load_history(user_id: str) -> list[dict]:
    """user_id로 최근 대화 기록 로드 (멀티턴 용)"""
    async with aiosqlite.connect(settings.SQLITE_DB_PATH) as db:
        async with db.execute("""
            SELECT role, content
            FROM conversations
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, settings.HISTORY_LIMIT * 2)) as cursor:
            rows = await cursor.fetchall()

    # 최근 것부터 가져왔으므로 순서 뒤집기 (시간순)
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]


async def save_turn(user_id: str, user_message: str, assistant_answer: str):
    """1턴 (user + assistant) 저장"""
    async with aiosqlite.connect(settings.SQLITE_DB_PATH) as db:
        await db.executemany("""
            INSERT INTO conversations (user_id, role, content)
            VALUES (?, ?, ?)
        """, [
            (user_id, "user",      user_message),
            (user_id, "assistant", assistant_answer),
        ])
        await db.commit()


async def delete_history(user_id: str):
    """특정 유저의 대화 기록 전체 삭제"""
    async with aiosqlite.connect(settings.SQLITE_DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        await db.commit()


async def load_history_cursor(
    user_id: str,
    limit: int = 20,
    before_id: int | None = None,
) -> dict:
    """커서 기반 대화 기록 조회 (프론드용)"""
    async with aiosqlite.connect(settings.SQLITE_DB_PATH) as db:
        if before_id is not None:
            async with db.execute("""
                SELECT id, role, content, created_at
                FROM conversations
                WHERE user_id = ? AND id < ?
                ORDER BY id DESC
                LIMIT ?
            """, (user_id, before_id, limit + 1)) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute("""
                SELECT id, role, content, created_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (user_id, limit + 1)) as cursor:
                rows = await cursor.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # 오래된 순으로 정렬

    messages = [
        {"id": r[0], "role": r[1], "content": r[2], "created_at": r[3]}
        for r in rows
    ]
    next_cursor = messages[0]["id"] if has_more else None

    return {"messages": messages, "next_cursor": next_cursor}