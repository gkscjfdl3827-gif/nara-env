import os
import sys
import json
import sqlite3
from datetime import datetime

from core.models import BidNotice
from core.filter import EnvironmentalFilter
from core.storage import NoticeStorage
from core.notifier import MultiChannelNotifier
from core.collector import NaraCollector

def run_cloud_check():
    """
    클라우드(GitHub Actions / 무료 서버)에서 컴퓨터 없이 24시간 실행되는 수집기
    환경 변수(Secrets) 또는 config.json을 자동으로 감지합니다.
    """
    # 1. 설정 로드 (GitHub Secrets 환경변수 우선, 없으면 config.json)
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", cfg.get("data_go_kr_service_key", ""))
    ntfy_topic = os.environ.get("NTFY_TOPIC", cfg.get("ntfy_topic", "nara-env-alert-myphone"))
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", cfg.get("telegram_bot_token", ""))
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", cfg.get("telegram_chat_id", ""))

    notifier_cfg = {
        "use_ntfy": bool(ntfy_topic),
        "ntfy_topic": ntfy_topic,
        "use_telegram": bool(tg_token and tg_chat_id),
        "telegram_bot_token": tg_token,
        "telegram_chat_id": tg_chat_id
    }

    db_path = os.path.join(os.path.dirname(__file__), "nara_notices.db")
    storage = NoticeStorage(db_path=db_path)
    notifier = MultiChannelNotifier(config=notifier_cfg)
    env_filter = EnvironmentalFilter()
    collector = NaraCollector(service_key=service_key)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 클라우드 나라장터 환경영향평가 모니터링 시작...")
    print(f" - 알림 채널(ntfy 토픽): {ntfy_topic}")

    raw_notices = []
    # 2. 공공데이터 OpenAPI 연동 수집 (우선순위 1)
    if service_key:
        print(" - 공공데이터 OpenAPI 연동 수집 진행...")
        raw_notices = collector.fetch_all_openapi(num_of_rows=50, days_back=2)

    # OpenAPI 키가 없거나 수집 건수가 없을 경우 시뮬레이터 활용
    if not raw_notices and cfg.get("simulation_mode", True):
        print(" - 시뮬레이션/샘플 데이터셋 점검...")
        raw_notices = collector.generate_mock_notices(count=10)

    print(f" - 수집된 원천 공고: 총 {len(raw_notices)}건")

    new_alerts = 0
    for notice in raw_notices:
        is_env, matched_kws, cat_name = env_filter.evaluate(notice)
        if not is_env:
            continue

        # 중복 발송 방지
        if storage.is_already_seen(notice.unique_id):
            continue

        # 저장 및 푸시 발송
        storage.save_notice(notice, notified=False)
        results = notifier.notify_all(notice)
        storage.mark_as_notified(notice.unique_id)
        new_alerts += 1
        print(f"  [새 공고 알림 전송] [{cat_name}] {notice.title} (결과: {results})")

    print(f"[{now_str}] 클라우드 검사 완료: 신규 알림 {new_alerts}건 발송됨.")
    return new_alerts

if __name__ == "__main__":
    run_cloud_check()
