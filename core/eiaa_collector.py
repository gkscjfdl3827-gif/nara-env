import os
import re
import ssl
import json
import sqlite3
import contextlib
import urllib.parse
import requests
import urllib3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
            cursor.execute("SELECT 1 FROM notices WHERE unique_id = ? AND notified = 1 LIMIT 1", (unique_id,))
            if cursor.fetchone():
                return True
            cursor.execute("SELECT 1 FROM notices WHERE title = ? AND post_date = ? AND notified = 1 LIMIT 1", (title, post_date))
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
                    ON CONFLICT(unique_id) DO UPDATE SET notified = MAX(notices.notified, excluded.notified)
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
    """(사)환경영향평가협회 입찰정보 수집기 (requests 기반 고신뢰성 엔진)"""

    BASE_URL = "https://eiaa.or.kr"
    LIST_URL = "https://eiaa.or.kr/page/s5/s11.php"
    LOGIN_URL = "https://eiaa.or.kr/app/rtmember/user/update.php"

    def __init__(self, user_id: str = "", user_pw: str = ""):
        self.user_id = user_id
        self.user_pw = user_pw
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://eiaa.or.kr/page/s5/s11.php"
        })

    LOGIN_PAGE_URL = BASE_URL + "/page/mb/login.php?prepage=%2Fpage%2Fs5%2Fs11.php"

    def _request(self, stage, method, url, **kwargs):
        # Never log bodies, URLs with queries, cookie values, or exception text.
        try:
            response = self.session.request(method, url, timeout=20, verify=False, **kwargs)
        except requests.RequestException:
            raise RuntimeError(f"EIAA NETWORK_ERROR stage={stage}") from None
        response.encoding = response.apparent_encoding
        print(f"[EIAA] stage={stage} status={response.status_code} "
              f"session_cookie={bool(self.session.cookies)}")
        if response.status_code in (401, 403, 429):
            raise RuntimeError(f"EIAA ACCESS_DENIED stage={stage} HTTP={response.status_code}; "
                               "인증 실패 확정 불가: IP/봇 차단 또는 접근 정책 확인 필요")
        if response.status_code >= 400:
            raise RuntimeError(f"EIAA HTTP_ERROR stage={stage} HTTP={response.status_code}")
        return response

    @staticmethod
    def _login_required(response):
        return ("/mb/login.php" in response.url or
                bool(re.search(r'<form[^>]+(?:name|id)=[\"\']login_form', response.text, re.I)) or
                bool(re.search(r'(?:location|location.href)\s*=\s*[\"\'][^\"\']*login.php', response.text)))

    def login(self) -> Tuple[bool, str]:
        if not self.user_id or not self.user_pw:
            return False, "EIAA CREDENTIALS_MISSING"
        # Anonymous PHPSESSID is not proof of authentication. Bootstrap and POST
        # through the very same Session before checking the protected board.
        self._request("login_page", "GET", self.LOGIN_PAGE_URL)
        response = self._request("login_post", "POST", self.LOGIN_URL, data={
            "mode": "login", "pcd": "", "userid": self.user_id,
            "userpw": self.user_pw, "is_memory": "y"
        }, headers={"Origin": self.BASE_URL, "Referer": self.LOGIN_PAGE_URL})
        if re.search(r'alert\s*\(', response.text):
            return False, "EIAA AUTH_REJECTED: 로그인 응답이 거절을 표시함 (계정/회원 권한 확인)"
        if self._login_required(response):
            return False, "EIAA AUTH_UNCONFIRMED: 로그인 화면으로 복귀"
        return True, "로그인 요청 완료; 보호된 목록에서 인증 확인 필요"

    def fetch_board_items(self) -> List[Dict[str, Any]]:
        ok, message = self.login()
        if not ok:
            raise RuntimeError(message)
        response = self._request("authenticated_list", "GET", self.LIST_URL)
        if self._login_required(response):
            raise RuntimeError("EIAA AUTH_REQUIRED: 로그인 후에도 로그인 화면 반환")
        html = response.text
        # A navigation login link or shared alert() script is not an auth failure.
        if not re.search(r'logout', html, re.I):
            raise RuntimeError("EIAA AUTH_UNCONFIRMED: 로그아웃 표시 없음; 인증 성공 판정 보류")
        items = self._parse_html(html)
        if not items and not re.search(r'등록된\s*(?:게시물|자료|글|공고).*?없', html, re.S):
            raise RuntimeError("EIAA BOARD_UNRECOGNIZED: 공고 파싱 실패를 0건 성공으로 처리하지 않음")
        print(f"[EIAA] authentication=confirmed items={len(items)}")
        return items

    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        """HTML 게시판 목록 파싱 (반응형 div 구조 및 table 구조 모두 지원)"""
        items: List[Dict[str, Any]] = []

        # 1. 반응형 DIV 구조 (rt-rwd-list-con)
        pattern = re.compile(
            r'<div class="rt-rwd-list-con rt-rwd-list-(?:norm|sign)-con[^"]*">.*?'
            r'<div class="rt-rwd-list-num"><p>(.*?)</p></div>.*?'
            r'<div class="rt-rwd-list-subject">.*?<a\s+href="([^"]+)">(.*?)</a>.*?'
            r'등록일\s*:\s*</span>\s*([0-9.]+)',
            re.DOTALL | re.IGNORECASE
        )
        for num, href, raw_title, date_str in pattern.findall(html):
            clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
            if not clean_title:
                continue
            agency = "(사)환경영향평가협회"
            m_agency = re.match(r'^\s*\[(.*?)\]', clean_title)
            if m_agency:
                agency = m_agency.group(1).strip()
            seq_m = re.search(r'[?&](?:seq|idx|no|bseq|wr_id)=(\d+)', href)
            seq = seq_m.group(1) if seq_m else num.strip()
            full_url = urllib.parse.urljoin(self.LIST_URL, href.strip().replace("&amp;", "&"))
            post_date = date_str.strip().replace('.', '-')
            items.append({
                "seq": seq,
                "title": clean_title,
                "agency": agency,
                "post_date": post_date,
                "detail_url": full_url
            })

        # 2. 전통적인 <table> 형태 fallback
        if not items:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
            for row in rows:
                tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                if not tds:
                    continue
                link_m = re.search(r'href=[\'"]([^\'"]+)[\'"]', row, re.IGNORECASE)
                if not link_m:
                    continue
                href = link_m.group(1).replace("&amp;", "&").strip()
                clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
                title = ""
                for t in clean_tds:
                    if len(t) > 4 and not re.match(r'^\d{4}[-.]\d{2}[-.]\d{2}$', t) and not t.isdigit():
                        title = t
                        break
                if not title:
                    continue
                post_date = ""
                for t in clean_tds:
                    m_date = re.search(r'(\d{4}[-.]\d{2}[-.]\d{2})', t)
                    if m_date:
                        post_date = m_date.group(1).replace('.', '-')
                        break
                agency = "(사)환경영향평가협회"
                m_agency = re.match(r'^\s*\[(.*?)\]', title)
                if m_agency:
                    agency = m_agency.group(1).strip()
                seq_m = re.search(r'[?&](?:seq|idx|no|bseq|wr_id)=(\d+)', href)
                seq = seq_m.group(1) if seq_m else (clean_tds[0] if clean_tds and clean_tds[0].isdigit() else "")
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

        try:
            resp = requests.post(
                "https://ntfy.sh",
                json=payload,
                headers={"User-Agent": "EiaaNotifier/1.0"},
                timeout=10
            )
            return resp.status_code == 200
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

    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    user_id = os.environ.get("EIAA_USER_ID") or ("" if in_actions else cfg.get("eiaa_user_id", ""))
    user_pw = os.environ.get("EIAA_USER_PW") or ("" if in_actions else cfg.get("eiaa_user_pw", ""))
    print(f"[EIAA] credentials_source={'github_secrets' if in_actions else 'environment_or_local_config'} configured={bool(user_id and user_pw)}")
    topic = os.environ.get("EIAA_NTFY_TOPIC") or cfg.get("eiaa_ntfy_topic") or os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic", "nara-env-alert-myphone")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str}] [(사)환경영향평가협회] 입찰정보 모니터링 검사...")

    if not user_id or not user_pw:
        raise RuntimeError("EIAA CREDENTIALS_MISSING: EIAA_USER_ID / EIAA_USER_PW 설정 필요")

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
    delivery_failed = False
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
        cat_name = "환경영향평가"
        if any(k in matched_kws for k in env_filter.category_map.get("전략환경영향평가", [])):
            cat_name = "전략환경영향평가"
        elif any(k in matched_kws for k in env_filter.category_map.get("소규모환경영향평가", [])):
            cat_name = "소규모환경영향평가"
        elif any(k in matched_kws for k in env_filter.category_map.get("사후환경영향조사", [])):
            cat_name = "사후환경영향조사"

        if not matched_kws:
            matched_kws = ["평가협회공고"]

        # 중복 발송 방지 1: SQLite DB 검사
        if storage.is_already_seen(seq, title, post_date):
            continue

        # 중복 발송 방지 2: ntfy 실시간 사전 조회 (동시 러너 병렬 실행 시 이중 발송 방어)
        if topic:
            try:
                poll_chk_url = f"https://ntfy.sh/{topic}/json?poll=1&since=45m"
                resp_chk = requests.get(poll_chk_url, headers={"User-Agent": "EiaaNotifier/1.0"}, timeout=3)
                if resp_chk.status_code == 200:
                    eiaa_recent_sig = set()
                    for l in resp_chk.text.splitlines():
                        try:
                            d = json.loads(l)
                            if d.get("event") == "message":
                                eiaa_recent_sig.add(d.get("title", ""))
                                if d.get("click"):
                                    eiaa_recent_sig.add(d.get("click"))
                        except Exception:
                            pass
                    if detail_url and detail_url in eiaa_recent_sig:
                        print(f"  [평가협회 중복 억제] 러너 동시 발송 감지 (seq={seq}): {title}")
                        storage.save_notice(seq=seq, title=title, agency=agency, post_date=post_date, detail_url=detail_url, matched_keywords=matched_kws, env_category=cat_name, notified=True)
                        continue
            except Exception:
                pass

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
        if sent:
            new_alerts += 1
        else:
            delivery_failed = True
        print(f"  [평가협회 새 알림 전송] [{cat_name}] {title} (ntfy: {sent})")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 평가협회 검사 완료: 신규 알림 {new_alerts}건 발송됨.")
    if delivery_failed:
        raise RuntimeError("일부 ntfy 발송 실패: 미발송 건은 다음 실행에서 재시도")
    return new_alerts
