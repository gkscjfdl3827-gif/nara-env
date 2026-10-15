import os
import sys
import json
import shutil
import sqlite3
from datetime import datetime

# Windows 콘솔 인코딩 안전 처리
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
    # 실수로 바깥 폴더째 업로드된 하위 폴더가 있으면 자동 정리
    base_dir = os.path.dirname(os.path.abspath(__file__))
    mistaken_dir = os.path.join(base_dir, "나라장터_알리오_통합_깃허브업로드본")
    if os.path.isdir(mistaken_dir):
        shutil.rmtree(mistaken_dir, ignore_errors=True)
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
    custom_kws = cfg.get("custom_keywords")
    custom_ex_kws = cfg.get("custom_exclude_keywords")
    env_filter = EnvironmentalFilter(
        keywords=custom_kws if custom_kws else None,
        exclude_keywords=custom_ex_kws if custom_ex_kws else None
    )
    collector = NaraCollector(service_key=service_key)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 클라우드 나라장터 환경영향평가 모니터링 시작...")
    print(f" - 알림 채널(ntfy 토픽): {ntfy_topic}")

    raw_notices = []
    # 2. 나라장터 차세대 통합 수집 (LH, 국방부, 한전 등 외부 연계공고 전수 포함)
    print(" - 나라장터 차세대 통합 공고 수집 진행 (LH, 국방부 연계 포함)...")
    raw_notices = collector.fetch_all(days_back=2)

    # 수집 건수가 없을 경우 시뮬레이터 활용 (개발 모드)
    if not raw_notices and cfg.get("simulation_mode", False):
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
        print(f"  [새 알림 전송] [{notice.stage}] [{cat_name}] {notice.title} (결과: {results})")

    print(f"[{now_str}] 클라우드 나라장터 검사 완료: 신규 알림 {new_alerts}건 발송됨.")

    # 3. 알리오(ALIO) 공공기관 입찰공고 독립 검사 (기존 나라장터와 DB 및 ntfy 토픽 완전 분리)
    try:
        from core.alio_collector import run_alio_cloud_check
        run_alio_cloud_check(cfg)
    except Exception as e:
        print(f"[ALIO] 알리오 검사 중 예외 발생 (나라장터 정상 작동 유지): {e}")

    return new_alerts

if __name__ == "__main__":
    run_cloud_check()
