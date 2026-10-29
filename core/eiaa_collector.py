import os
import re
import ssl
import json
import sqlite3
import contextlib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from .filter import EnvironmentalFilter, DEFAULT_TARGET_CATEGORIES

class EiaaNoticeStorage:
    """(사)환경영향평가협회 전용 SQLite 저장소 (독립된 eiaa_notices.db 사용)"""

    def __init__(self, db_path: str = "eiaa_notices.db"):
        self.db_path = db_path
        self._init_db()

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
                    seq TEXT,
                    title TEXT NOT NULL,
                    agency TEXT,
                    post_date TEXT,
                    detail_url TEXT,
                    matched_keywords TEXT,
                    env_category TEXT,
                    stage TEXT DEFAULT '입찰공고',
                    notified INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_eiaa_seq ON notices(seq)")
            conn.commit()

    def is_already_seen(self, seq: str, title: str, post_date: str) -> bool:
        unique_id = f"EIAA-{seq}" if seq else f"EIAA-{title}-{post_date}"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM notices WHERE unique_id = ? LIMIT 1", (unique_id,))
            if cursor.fetchone():
                return True
            # 제목 + 일자 보조 중복 검사
            cursor.execute("SELECT 1 FROM notices WHERE title = ? AND post_date = ? LIMIT 1", (title, post_date))
            return cursor.fetchone() is not None

    def save_notice(
        self,
        seq: str,
        title: str,
        agency: str,
        post_date: str,
        detail_url: str,
        matched_keywords: List[str],
        env_category: str,
        notified: bool = True
    ) -> bool:
        unique_id = f"EIAA-{seq}" if seq else f"EIAA-{title}-{post_date}"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO notices (
                        unique_id, seq, title, agency,
                        post_date, detail_url,
                        matched_keywords, env_category, stage, notified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '입찰공고', ?)
                """, (
                    unique_id,
                    str(seq or ''),
                    title,
                    agency or '환경영향평가협회',
                    post_date,
                    detail_url,
                    json.dumps(matched_keywords, ensure_ascii=False),
                    env_category,
                    1 if notified else 0
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False


class EiaaCollector:
    """(사)환경영향평가협회 입찰정보 수집기"""

    BASE_URL = "https://eiaa.or.kr"
    LIST_URL = "https://eiaa.or.kr/page/s5/s11.php"
    LOGIN_URL = "https://eiaa.or.kr/app/rtmember/user/update.php"

    def __init__(self, user_id: str = "", user_pw: str = ""):
        self.user_id = user_id
        self.user_pw = user_pw
        self.cookies: Dict[str, str] = {}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://eiaa.or.kr/page/s5/s11.php"
        }
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def login(self) -> Tuple[bool, str]:
        """협회 세션 로그인 수행"""
        if not self.user_id or not self.user_pw:
            return False, "아이디 또는 비밀번호 미설정"

        data = urllib.parse.urlencode({
            "mode": "login",
            "pcd": "",
            "userid": self.user_id,
            "userpw": self.user_pw,
            "is_memory": "y"
        }).encode("utf-8")

        headers = dict(self.headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Referer"] = "https://eiaa.or.kr/page/mb/login.php?prepage=%2Fpage%2Fs5%2Fs11.php"

        req = urllib.request.Request(self.LOGIN_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                
                # 쿠키 수집
                cookie_headers = resp.headers.get_all("Set-Cookie") or []
                for ch in cookie_headers:
                    parts = ch.split(";")[0].split("=", 1)
                    if len(parts) == 2:
                        self.cookies[parts[0].strip()] = parts[1].strip()

                if "alert(" in body:
                    # 실패 알림 텍스트 파싱
                    m = re.search(r"alert\('([^']+)'\)", body)
                    err_msg = m.group(1) if m else "로그인 실패"
                    return False, err_msg

                return True, "로그인 성공"
        except Exception as e:
            return False, f"로그인 통신 오류: {e}"

    def _get_cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def fetch_board_items(self) -> List[Dict[str, Any]]:
        """입찰정보 게시판 목록 크롤링"""
        headers = dict(self.headers)
        if self.cookies:
            headers["Cookie"] = self._get_cookie_str()

        req = urllib.request.Request(self.LIST_URL, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=12) as resp:
                # 쿠키 업데이트
                for ch in (resp.headers.get_all("Set-Cookie") or []):
                    parts = ch.split(";")[0].split("=", 1)
                    if len(parts) == 2:
                        self.cookies[parts[0].strip()] = parts[1].strip()

                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[EiaaCollector] 페이지 요청 오류: {e}")
            return []

        # 로그인이 풀려 로그인 페이지로 리다이렉트 스크립트가 반환된 경우 자동 재로그인 시도
        if "login.php" in html and self.user_id and self.user_pw:
            ok, msg = self.login()
            if ok:
                headers["Cookie"] = self._get_cookie_str()
                req2 = urllib.request.Request(self.LIST_URL, headers=headers)
                try:
                    with urllib.request.urlopen(req2, context=self.ssl_ctx, timeout=12) as resp2:
                        html = resp2.read().decode("utf-8", errors="ignore")
                except Exception:
                    return []
            else:
                print(f"[EiaaCollector] 로그인 실패: {msg}")
                return []

        return self._parse_html(html)

    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        """HTML 게시판 행 데이터 파싱"""
        items: List[Dict[str, Any]] = []

        # 1. <table> 형태 행 파싱
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if not tds:
                continue

            # 링크 추출
            link_m = re.search(r'href=[\'"]([^\'"]+)[\'"]', row, re.IGNORECASE)
            if not link_m:
                continue
            href = link_m.group(1).replace("&amp;", "&")

            # 순수 텍스트 정제
            clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]

            # 제목 추출
            title = ""
            for t in clean_tds:
                if len(t) > 4 and not re.match(r'^\d{4}[-.]\d{2}[-.]\d{2}$', t) and not t.isdigit():
                    title = t
                    break

            if not title:
                continue

            # 날짜 추출 (YYYY-MM-DD 또는 YYYY.MM.DD)
            post_date = ""
            for t in clean_tds:
                m_date = re.search(r'(\d{4}[-.]\d{2}[-.]\d{2})', t)
                if m_date:
                    post_date = m_date.group(1)
                    break

            # 작성자 / 발주처 추출
            agency = "환경영향평가협회"
            for t in clean_tds:
                if t and t != title and t != post_date and not t.isdigit() and len(t) < 30:
                    agency = t
                    break

            # 고유 시퀀스 추출
            seq_m = re.search(r'[?&](?:seq|idx|no|bseq|wr_id)=(\d+)', href)
            seq = seq_m.group(1) if seq_m else ""
            if not seq:
                # 첫번째 td가 숫자인 경우 번호로 활용
                if clean_tds and clean_tds[0].isdigit():
                    seq = clean_tds[0]

            full_url = urllib.parse.urljoin(self.LIST_URL, href)
            items.append({
                "seq": seq,
                "title": title,
                "agency": agency,
                "post_date": post_date,
                "detail_url": full_url
            })

        return items

    def send_ntfy_alert(
        self,
        topic: str,
        title: str,
        agency: str,
        post_date: str,
        detail_url: str,
        matched_keywords: List[str],
        env_category: str
    ) -> bool:
        """ntfy 스마트폰 푸시 발송 ([평가협회] 구분 태그 탑재)"""
        if not topic:
            return False

        lines = [
            f"🏢 기관/출처: {agency or '환경영향평가협회'}",
            f"📅 공고일자: {post_date or '최신'}",
            f"🔍 매칭키워드: {', '.join(matched_keywords)}"
        ]

        actions = [
            {"action": "view", "label": "평가협회 바로가기", "url": detail_url}
        ]

        payload = {
            "topic": topic,
            "title": f"🏛️ [평가협회] [{env_category}] {title}",
            "message": "\n".join(lines),
            "priority": 4,
            "tags": ["classical_building", "scroll", "bell"],
            "click": detail_url,
            "actions": actions
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "EiaaNotifier/1.0"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[EiaaCollector] ntfy 발송 실패: {e}")
            return False


def run_eiaa_cloud_check(cfg: Optional[Dict[str, Any]] = None) -> int:
    """(사)환경영향평가협회 공고 클라우드/로컬 실행 검사"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if cfg is None:
        config_path = os.path.join(base_dir, "config.json")
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

    user_id = os.environ.get("EIAA_USER_ID") or cfg.get("eiaa_user_id", "")
    user_pw = os.environ.get("EIAA_USER_PW") or cfg.get("eiaa_user_pw", "")
    topic = os.environ.get("EIAA_NTFY_TOPIC") or cfg.get("eiaa_ntfy_topic") or os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic", "nara-env-alert-myphone")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str}] [(사)환경영향평가협회] 입찰정보 모니터링 검사...")

    if not user_id or not user_pw:
        print(" - [평가협회] 협회 계정(eiaa_user_id, eiaa_user_pw) 설정 대기 중 (설정 시 즉시 수집 활성화)")
        return 0

    db_path = os.path.join(base_dir, "eiaa_notices.db")
    storage = EiaaNoticeStorage(db_path=db_path)

    custom_kws = cfg.get("custom_keywords")
    custom_ex_kws = cfg.get("custom_exclude_keywords")
    env_filter = EnvironmentalFilter(
        keywords=custom_kws if custom_kws else None,
        exclude_keywords=custom_ex_kws if custom_ex_kws else None
    )

    collector = EiaaCollector(user_id=user_id, user_pw=user_pw)
    items = collector.fetch_board_items()
    print(f" - [평가협회] 수집된 공고: 총 {len(items)}건")

    new_alerts = 0
    for item in items:
        seq = item.get("seq", "")
        title = item.get("title", "")
        agency = item.get("agency", "")
        post_date = item.get("post_date", "")
        detail_url = item.get("detail_url", "")

        if not title:
            continue

        # 제외 키워드 검사 (해양환경 등)
        title_norm = re.sub(r'\s+', '', title).lower().replace('해양환경공단', '')
        if any(re.sub(r'\s+', '', ex).lower() in title_norm for ex in env_filter.exclude_keywords if ex):
            continue

        # 환경영향평가 전문 분류
        matched_kws = [k for k in env_filter.keywords if k in title or re.sub(r'\s+', '', k) in title_norm]
        
        # 협회 입찰정보는 기본적으로 환경영향평가 관련 게시판이므로 키워드가 없더라도 환경영향평가로 처리하거나 매칭 우선
        cat_name = "환경영향평가"
        if any(k in matched_kws for k in env_filter.category_map.get("전략환경영향평가", [])):
            cat_name = "전략환경영향평가"
        elif any(k in matched_kws for k in env_filter.category_map.get("소규모환경영향평가", [])):
            cat_name = "소규모환경영향평가"
        elif any(k in matched_kws for k in env_filter.category_map.get("사후환경영향조사", [])):
            cat_name = "사후환경영향조사"

        if not matched_kws:
            matched_kws = ["평가협회공고"]

        if storage.is_already_seen(seq, title, post_date):
            continue

        sent = collector.send_ntfy_alert(
            topic=topic,
            title=title,
            agency=agency,
            post_date=post_date,
            detail_url=detail_url,
            matched_keywords=matched_kws,
            env_category=cat_name
        )
        storage.save_notice(
            seq=seq,
            title=title,
            agency=agency,
            post_date=post_date,
            detail_url=detail_url,
            matched_keywords=matched_kws,
            env_category=cat_name,
            notified=sent
        )
        new_alerts += 1
        print(f"  [평가협회 새 알림 전송] [{cat_name}] {title} (ntfy: {sent})")

    print(f"[{now_str}] 평가협회 검사 완료: 신규 알림 {new_alerts}건 발송됨.")
    return new_alerts
