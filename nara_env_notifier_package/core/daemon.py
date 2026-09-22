import time
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional

from .models import BidNotice
from .collector import NaraCollector
from .filter import EnvironmentalFilter
from .storage import NoticeStorage
from .notifier import MultiChannelNotifier

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    default_config = {
        "check_interval_seconds": 300, # 5분 주기
        "use_openapi": False,
        "data_go_kr_service_key": "",
        "use_ntfy": True,
        "ntfy_topic": "nara-env-alert-demo", # 기본 토픽 (사용자가 변경 가능)
        "use_telegram": False,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "use_discord": False,
        "discord_webhook_url": "",
        "custom_keywords": [],
        "custom_exclude_keywords": [],
        "simulation_mode": True # API 키 미등록 시 자동 시뮬레이션 모드 지원
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                default_config.update(user_cfg)
        except Exception as e:
            print(f"[Daemon] Error loading config: {e}")
    return default_config

def save_config(config_data: Dict[str, Any], config_path: str = DEFAULT_CONFIG_PATH):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)

class MonitoringDaemon:
    """나라장터 환경 공고 주기적 자동 감지 및 스마트폰 알림 데몬"""

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH, db_path: str = "nara_notices.db"):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.storage = NoticeStorage(db_path=db_path)
        self.notifier = MultiChannelNotifier(config=self.config)
        self.filter = EnvironmentalFilter(
            keywords=self.config.get("custom_keywords") if self.config.get("custom_keywords") else None,
            exclude_keywords=self.config.get("custom_exclude_keywords") if self.config.get("custom_exclude_keywords") else None
        )
        self.collector = NaraCollector(service_key=self.config.get("data_go_kr_service_key"))
        self.is_running = False

    def reload_config(self):
        """설정 리로드"""
        self.config = load_config(self.config_path)
        self.notifier = MultiChannelNotifier(config=self.config)
        self.filter = EnvironmentalFilter(
            keywords=self.config.get("custom_keywords") if self.config.get("custom_keywords") else None,
            exclude_keywords=self.config.get("custom_exclude_keywords") if self.config.get("custom_exclude_keywords") else None
        )
        self.collector = NaraCollector(service_key=self.config.get("data_go_kr_service_key"))

    def check_once(self) -> Dict[str, Any]:
        """
        1회 수집 및 신규 환경 공고 알람 전송 실행
        반환값: 실행 결과 통계 요약
        """
        self.reload_config()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 나라장터 공고 수집 시작...")

        raw_notices = []
        if self.config.get("use_openapi") and self.config.get("data_go_kr_service_key"):
            raw_notices = self.collector.fetch_all_openapi(num_of_rows=50, days_back=2)
        
        # OpenAPI 키가 없거나 수집 결과가 없으면 시뮬레이션/기본 모드 활용
        if not raw_notices and self.config.get("simulation_mode", True):
            raw_notices = self.collector.generate_mock_notices(count=10)

        total_collected = len(raw_notices)
        matched_env_count = 0
        new_alerts_sent = 0

        for notice in raw_notices:
            # 1. 환경 공고 여부 필터링
            is_env, matched_kws, cat_name = self.filter.evaluate(notice)
            if not is_env:
                continue

            matched_env_count += 1

            # 2. 이미 수집/알림한 공고인지 확인
            if self.storage.is_already_seen(notice.unique_id):
                continue

            # 3. 신규 공고 저장
            self.storage.save_notice(notice, notified=False)

            # 4. 스마트폰 푸시 알람 발송
            results = self.notifier.notify_all(notice)
            self.storage.mark_as_notified(notice.unique_id)
            new_alerts_sent += 1
            print(f"  -> [NEW ALERT] [{cat_name}] {notice.title} (Result: {results})")

        stats = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_collected": total_collected,
            "matched_env": matched_env_count,
            "new_alerts_sent": new_alerts_sent
        }
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 완료: 총 {total_collected}건 중 환경 공고 {matched_env_count}건 감지, 신규 {new_alerts_sent}건 알람 발송 완료.")
        return stats

    def start_loop(self):
        """백그라운드 무한 루프 모니터링 시작"""
        self.is_running = True
        interval = self.config.get("check_interval_seconds", 300)
        print(f"[*] 나라장터 환경 공고 모니터링 데몬 시작 (주기: {interval}초)")
        try:
            while self.is_running:
                self.check_once()
                interval = self.config.get("check_interval_seconds", 300)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n모니터링 데몬을 안전하게 종료합니다.")
            self.is_running = False

if __name__ == "__main__":
    daemon = MonitoringDaemon()
    daemon.start_loop()
