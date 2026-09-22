import sqlite3
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from .models import BidNotice

class NoticeStorage:
    """SQLite 기반 나라장터 공고 저장소 및 중복 방지 매니저"""

    def __init__(self, db_path: str = "nara_notices.db"):
        self.db_path = db_path
        self._init_db()

    import contextlib

    @contextlib.contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notices (
                    unique_id TEXT PRIMARY KEY,
                    bid_no TEXT NOT NULL,
                    bid_seq TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT,
                    order_agency TEXT,
                    announce_agency TEXT,
                    budget INTEGER,
                    contract_method TEXT,
                    post_date TEXT,
                    close_date TEXT,
                    detail_url TEXT,
                    matched_keywords TEXT,
                    env_category TEXT,
                    notified INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_post_date ON notices (post_date DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_notified ON notices (notified)")
            conn.commit()

    def is_already_seen(self, unique_id: str) -> bool:
        """이미 수집/처리된 공고인지 확인"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM notices WHERE unique_id = ?", (unique_id,))
            return cursor.fetchone() is not None

    def save_notice(self, notice: BidNotice, notified: bool = False) -> bool:
        """새 공고 저장 (신규 저장 시 True, 기존 중복이면 False)"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO notices (
                        unique_id, bid_no, bid_seq, title, category,
                        order_agency, announce_agency, budget, contract_method,
                        post_date, close_date, detail_url, matched_keywords,
                        env_category, notified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    notice.unique_id,
                    notice.bid_no,
                    notice.bid_seq,
                    notice.title,
                    notice.category,
                    notice.order_agency,
                    notice.announce_agency,
                    notice.budget or 0,
                    notice.contract_method,
                    notice.post_date,
                    notice.close_date,
                    notice.detail_url,
                    json.dumps(notice.matched_keywords, ensure_ascii=False),
                    notice.env_category,
                    1 if notified else 0
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_as_notified(self, unique_id: str):
        """알림 발송 완료 처리"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE notices SET notified = 1 WHERE unique_id = ?", (unique_id,))
            conn.commit()

    def get_recent_notices(self, limit: int = 50, env_category: Optional[str] = None) -> List[Dict[str, Any]]:
        """최근 공고 목록 조회"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if env_category and env_category != "전체":
                cursor.execute("""
                    SELECT * FROM notices 
                    WHERE env_category = ? 
                    ORDER BY post_date DESC, created_at DESC 
                    LIMIT ?
                """, (env_category, limit))
            else:
                cursor.execute("""
                    SELECT * FROM notices 
                    ORDER BY post_date DESC, created_at DESC 
                    LIMIT ?
                """, (limit,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("matched_keywords"):
                    try:
                        item["matched_keywords"] = json.loads(item["matched_keywords"])
                    except Exception:
                        item["matched_keywords"] = []
                results.append(item)
            return results

    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM notices")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM notices WHERE notified = 1")
            notified = cursor.fetchone()[0]

            cursor.execute("""
                SELECT env_category, COUNT(*) as cnt 
                FROM notices 
                GROUP BY env_category 
                ORDER BY cnt DESC
            """)
            categories = dict(cursor.fetchall())

            return {
                "total_notices": total,
                "notified_notices": notified,
                "categories": categories
            }
