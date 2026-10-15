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

from .models import BidNotice
from .filter import EnvironmentalFilter, DEFAULT_TARGET_CATEGORIES

class AlioNoticeStorage:
    """알리오(ALIO) 전용 SQLite 저장소 (기존 nara_notices.db와 완전히 분리된 alio_notices.db 사용)"""

    def __init__(self, db_path: str = "alio_notices.db"):
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
                    seq TEXT NOT NULL,
                    title TEXT NOT NULL,
                    agency TEXT,
                    budget INTEGER DEFAULT 0,
                    post_date TEXT,
                    close_date TEXT,
                    detail_url TEXT,
                    source_url TEXT,
                    files TEXT,
                    matched_keywords TEXT,
                    env_category TEXT,
                    stage TEXT DEFAULT '입찰공고',
                    notified INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alio_post_date ON notices (post_date DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alio_dedup ON notices (agency, title, post_date, close_date)")
            conn.commit()

    def is_already_seen(self, seq: str, agency: str, title: str, post_date: str, close_date: str) -> bool:
        unique_id = f"ALIO-{seq}"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM notices 
                WHERE unique_id = ? 
                   OR (agency = ? AND title = ? AND post_date = ? AND close_date = ?)
                """,
                (unique_id, agency, title, post_date, close_date)
            )
            return cursor.fetchone() is not None

    def save_notice(
        self,
        seq: str,
        title: str,
        agency: str,
        budget: int,
        post_date: str,
        close_date: str,
        detail_url: str,
        source_url: str,
        files: List[Dict[str, str]],
        matched_keywords: List[str],
        env_category: str,
        notified: bool = True
    ) -> bool:
        unique_id = f"ALIO-{seq}"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO notices (
                        unique_id, seq, title, agency, budget,
                        post_date, close_date, detail_url, source_url,
                        files, matched_keywords, env_category, stage, notified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '입찰공고', ?)
                """, (
                    unique_id,
                    str(seq),
                    title,
                    agency,
                    budget or 0,
                    post_date,
                    close_date,
                    detail_url,
                    source_url,
                    json.dumps(files, ensure_ascii=False),
                    json.dumps(matched_keywords, ensure_ascii=False),
                    env_category,
                    1 if notified else 0
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False


class AlioCollector:
    """알리오(ALIO) 공공기관 입찰공고 수집 및 ntfy 발송 엔진"""

    BASE_LIST_URL = "https://www.alio.go.kr/occasional/findBidList.json"
    BASE_DTL_URL = "https://www.alio.go.kr/occasional/findBidDtl.json"
    WEB_DTL_URL = "https://www.alio.go.kr/occasional/bidDtl.do"

    SEARCH_SEEDS = [
        "",
        "환경영향",
        "사후환경",
        "전략환경",
        "소규모환경",
        "소규모 환경"
    ]

    def __init__(self, max_pages: int = 3):
        self.max_pages = max_pages
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.alio.go.kr/occasional/bidList.do",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _get_json(self, url: str, timeout: int = 12) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            print(f"[AlioCollector] 요청 오류 ({url}): {e}")
        return None

    def fetch_page(self, page_no: int = 1, word: str = "") -> List[Dict[str, Any]]:
        params = {"pageNo": str(page_no), "type": "title", "word": word, "area": ""}
        url = f"{self.BASE_LIST_URL}?{urllib.parse.urlencode(params)}"
        res_json = self._get_json(url)
        if not res_json or res_json.get("status") != "success":
            return []
        data = res_json.get("data") or {}
        return data.get("result") or []

    def fetch_all_items(self, days_back: int = 14) -> List[Dict[str, Any]]:
        collected: Dict[str, Dict[str, Any]] = {}
        cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y.%m.%d")

        for page in range(1, self.max_pages + 1):
            items = self.fetch_page(page_no=page, word="")
            if not items:
                break
            for item in items:
                seq = str(item.get("seq") or "").strip()
                if seq and seq not in collected:
                    collected[seq] = item

        for seed in self.SEARCH_SEEDS:
            if not seed:
                continue
            items = self.fetch_page(page_no=1, word=seed)
            for item in items:
                seq = str(item.get("seq") or "").strip()
                bdate = str(item.get("bdate") or "").strip()
                if seq and (not bdate or bdate >= cutoff_date):
                    if seq not in collected:
                        collected[seq] = item

        return sorted(
            collected.values(),
            key=lambda x: int(x.get("seq")) if str(x.get("seq", "")).isdigit() else 0
        )

    def fetch_detail(self, seq: str) -> Tuple[str, int, List[Dict[str, str]]]:
        url = f"{self.BASE_DTL_URL}?seq={urllib.parse.quote(str(seq))}"
        res_json = self._get_json(url)
        if not res_json or res_json.get("status") != "success":
            return "", 0, []

        data = res_json.get("data") or {}
        dtl = data.get("bidDtl") or {}
        file_list = data.get("fileList") or []

        refr_url = str(dtl.get("refrUrl") or "").strip()
        if refr_url and not refr_url.startswith("http"):
            refr_url = "https://" + refr_url.lstrip("/")

        tot_amt = dtl.get("totContAmt")
        budget = int(tot_amt) if (tot_amt and isinstance(tot_amt, (int, float)) and tot_amt > 0) else 0

        files = []
        for f in file_list:
            f_name = str(f.get("fileNm") or "").strip()
            f_url = str(f.get("fileNo") or "").strip()
            if f_url.startswith("http://www.g2b.go.kr"):
                f_url = f_url.replace("http://www.g2b.go.kr", "https://www.g2b.go.kr")
            if f_name:
                files.append({"name": f_name, "url": f_url})

        return refr_url, budget, files

    def send_ntfy_alert(
        self,
        topic: str,
        title: str,
        agency: str,
        post_date: str,
        close_date: str,
        detail_url: str,
        source_url: str,
        files: List[Dict[str, str]],
        matched_keywords: List[str],
        env_category: str
    ) -> bool:
        if not topic:
            return False

        lines = [
            f"🏢 기관: {agency}",
            f"📅 공고: {post_date} | ⏰ 마감: {close_date or '미정'}",
            f"🔍 키워드: {', '.join(matched_keywords)}"
        ]
        if files:
            first_file = files[0].get("name", "")
            extra = f" 외 {len(files)-1}건" if len(files) > 1 else ""
            lines.append(f"📎 첨부: {first_file}{extra}")

        actions = [{"action": "view", "label": "알리오 상세보기", "url": detail_url}]
        if source_url:
            actions.append({"action": "view", "label": "원문공고 바로가기", "url": source_url})

        payload = {
            "topic": topic,
            "title": f"🏛️ [알리오] {env_category}: {title}",
            "message": "\n".join(lines),
            "priority": 4,
            "tags": ["classical_building", "leaves", "bell"],
            "click": detail_url,
            "actions": actions
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "AlioEnvMonitor/1.0"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[AlioCollector] ntfy 발송 실패: {e}")
            return False


def evaluate_alio_title(title: str, agency: str, env_filter: EnvironmentalFilter) -> Tuple[bool, List[str], str]:
    """공고명 기준 환경영향평가 필터링 (띄어쓰기 무시 매칭 + 해양환경/소모품 제외)"""
    title_norm = re.sub(r"\s+", "", title).lower()
    title_for_ex = title_norm.replace("해양환경공단", "")
    for ex_kw in env_filter.exclude_keywords:
        ex_clean = re.sub(r"\s+", "", ex_kw).lower()
        if ex_clean and ex_clean in title_for_ex:
            return False, [], ""

    matched: List[str] = []
    for kw in sorted(env_filter.keywords, key=lambda x: len(x), reverse=True):
        kw_norm = re.sub(r"\s+", "", kw)
        if re.search(re.escape(kw), title, re.IGNORECASE) or re.search(re.escape(kw_norm), title_norm, re.IGNORECASE):
            matched.append(kw)

    if not matched:
        return False, [], ""

    best_cat = "환경영향평가"
    cat_map = env_filter.category_map
    if any(k in matched or re.sub(r"\s+", "", k) in title_norm for k in cat_map.get("전략환경영향평가", [])):
        best_cat = "전략환경영향평가"
    elif any(k in matched or re.sub(r"\s+", "", k) in title_norm for k in cat_map.get("소규모환경영향평가", [])):
        best_cat = "소규모환경영향평가"
    elif any(k in matched or re.sub(r"\s+", "", k) in title_norm for k in cat_map.get("사후환경영향조사", [])):
        best_cat = "사후환경영향조사"
    elif any(k in matched or re.sub(r"\s+", "", k) in title_norm for k in cat_map.get("환경영향평가", [])):
        best_cat = "환경영향평가"

    return True, matched, best_cat


def run_alio_cloud_check(cfg: Optional[Dict[str, Any]] = None) -> int:
    """알리오(ALIO) 공공기관 입찰공고 독립 검사 및 ntfy 발송"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if cfg is None:
        config_path = os.path.join(base_dir, "config.json")
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

    alio_topic = os.environ.get("ALIO_NTFY_TOPIC") or cfg.get("alio_ntfy_topic", "alio-env-alert-myphone")
    db_path = os.path.join(base_dir, "alio_notices.db")
    storage = AlioNoticeStorage(db_path=db_path)

    custom_kws = cfg.get("custom_keywords")
    custom_ex_kws = cfg.get("custom_exclude_keywords")
    env_filter = EnvironmentalFilter(
        keywords=custom_kws if custom_kws else None,
        exclude_keywords=custom_ex_kws if custom_ex_kws else None
    )

    collector = AlioCollector(max_pages=3)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str}] [알리오(ALIO)] 공공기관 환경영향평가 모니터링 시작...")
    print(f" - 알리오 알림 채널(ntfy 토픽): {alio_topic}")

    items = collector.fetch_all_items(days_back=14)
    print(f" - 알리오 수집된 원천 공고: 총 {len(items)}건")

    new_alerts = 0
    for item in items:
        seq = str(item.get("seq") or "").strip()
        title = str(item.get("rtitle") or "").strip()
        agency = str(item.get("pname") or "").strip()
        post_date = str(item.get("bdate") or "").strip()
        close_date = str(item.get("bidInfoEndDt") or "").strip()

        if not seq or not title:
            continue

        is_env, matched_kws, cat_name = evaluate_alio_title(title, agency, env_filter)
        if not is_env:
            continue

        if storage.is_already_seen(seq, agency, title, post_date, close_date):
            continue

        detail_url = f"{collector.WEB_DTL_URL}?seq={seq}"
        source_url, budget, files = collector.fetch_detail(seq)

        sent = collector.send_ntfy_alert(
            topic=alio_topic,
            title=title,
            agency=agency,
            post_date=post_date,
            close_date=close_date,
            detail_url=detail_url,
            source_url=source_url,
            files=files,
            matched_keywords=matched_kws,
            env_category=cat_name
        )
        storage.save_notice(
            seq=seq,
            title=title,
            agency=agency,
            budget=budget,
            post_date=post_date,
            close_date=close_date,
            detail_url=detail_url,
            source_url=source_url,
            files=files,
            matched_keywords=matched_kws,
            env_category=cat_name,
            notified=sent
        )
        new_alerts += 1
        print(f"  [알리오 새 알림 전송] [{cat_name}] [{agency}] {title} (ntfy: {sent})")

    print(f"[{now_str}] 알리오 검사 완료: 신규 알림 {new_alerts}건 발송됨.")
    return new_alerts
