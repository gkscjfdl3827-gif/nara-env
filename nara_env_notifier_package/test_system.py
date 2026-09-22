import unittest
import os
import json
import tempfile
import shutil
from datetime import datetime

from core.models import BidNotice
from core.filter import EnvironmentalFilter, DEFAULT_ENV_CATEGORIES
from core.storage import NoticeStorage
from core.notifier import MultiChannelNotifier
from core.collector import NaraCollector
from core.daemon import MonitoringDaemon

class TestNaraEnvNotifier(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_notices.db")
        self.config_path = os.path.join(self.temp_dir, "test_config.json")
        self.config = {
            "check_interval_seconds": 60,
            "use_ntfy": True,
            "ntfy_topic": "test-env-topic-unit-test",
            "simulation_mode": True,
            "custom_keywords": [],
            "custom_exclude_keywords": []
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_bid_notice_model(self):
        """1. 입찰 공고 모델 및 금액 포맷 검증"""
        notice = BidNotice(
            bid_no="20260901001",
            bid_seq="00",
            title="하수처리시설 악취저감 설비공사",
            category="공사",
            order_agency="한국환경공단",
            announce_agency="조달청",
            budget=2500000000,
            post_date="2026-09-22 10:00",
            close_date="2026-09-30 18:00",
            detail_url="https://www.g2b.go.kr/test"
        )
        self.assertEqual(notice.unique_id, "20260901001-00")
        self.assertIn("25.0억원", notice.formatted_budget)
        d = notice.to_dict()
        self.assertEqual(d["unique_id"], "20260901001-00")

    def test_environmental_filter_four_categories(self):
        """2. 사용자가 요청한 4대 환경영향평가 키워드 정밀 필터링 검증"""
        env_filter = EnvironmentalFilter()

        # 케이스 1: 전략환경영향평가
        n1 = BidNotice(
            bid_no="1", bid_seq="0",
            title="2026년 OO도시기본계획 전략환경영향평가 용역",
            category="용역", order_agency="천안시", announce_agency="조달청"
        )
        is_env1, matched1, cat1 = env_filter.evaluate(n1)
        self.assertTrue(is_env1)
        self.assertIn("전략환경영향평가", matched1)
        self.assertEqual(cat1, "전략환경영향평가")

        # 케이스 2: 소규모환경영향평가
        n2 = BidNotice(
            bid_no="2", bid_seq="0",
            title="스마트 관광휴양단지 조성사업 소규모환경영향평가 용역",
            category="용역", order_agency="삼척시", announce_agency="조달청"
        )
        is_env2, matched2, cat2 = env_filter.evaluate(n2)
        self.assertTrue(is_env2)
        self.assertIn("소규모환경영향평가", matched2)
        self.assertEqual(cat2, "소규모환경영향평가")

        # 케이스 3: 사후환경영향조사
        n3 = BidNotice(
            bid_no="3", bid_seq="0",
            title="하수관로정비 BTL사업 3차년도 사후환경영향조사",
            category="용역", order_agency="용인시", announce_agency="조달청"
        )
        is_env3, matched3, cat3 = env_filter.evaluate(n3)
        self.assertTrue(is_env3)
        self.assertIn("사후환경영향조사", matched3)
        self.assertEqual(cat3, "사후환경영향조사")

        # 케이스 4: 환경영향평가
        n4 = BidNotice(
            bid_no="4", bid_seq="0",
            title="OO태양광 및 풍력발전단지 구축사업 환경영향평가 용역",
            category="용역", order_agency="한국중부발전", announce_agency="조달청"
        )
        is_env4, matched4, cat4 = env_filter.evaluate(n4)
        self.assertTrue(is_env4)
        self.assertIn("환경영향평가", matched4)
        self.assertEqual(cat4, "환경영향평가")

        # 케이스 5: 일반 비환경 공고 (탈락 확인)
        n5 = BidNotice(
            bid_no="5", bid_seq="0",
            title="초등학교 학생 급식용 돈육 구매",
            category="물품", order_agency="교육청", announce_agency="조달청"
        )
        is_env5, matched5, cat5 = env_filter.evaluate(n5)
        self.assertFalse(is_env5)

        # 케이스 6: 환경 관련이지만 4대 평가가 아닌 경우 (예: 단순 하수관로 공사 -> 사용자의 4대 키워드 외이므로 필터링)
        n6 = BidNotice(
            bid_no="6", bid_seq="0",
            title="2026년 노후 하수관로 준설 및 정밀조사",
            category="공사", order_agency="강남구청", announce_agency="조달청"
        )
        is_env6, _, _ = env_filter.evaluate(n6)
        self.assertFalse(is_env6, "4대 환경영향평가가 아닌 일반 공사는 알림에서 제외되어야 함")

    def test_sqlite_storage(self):
        """3. SQLite 저장소 중복 저장 방지 및 조회 검증"""
        storage = NoticeStorage(db_path=self.db_path)
        notice = BidNotice(
            bid_no="2026999999", bid_seq="01",
            title="태양광 발전설비 설치공사",
            category="공사", order_agency="한전", announce_agency="조달청",
            budget=500000000, post_date="2026-09-22 12:00",
            env_category="에너지/친환경/ESG", matched_keywords=["태양광"]
        )

        # 첫 번째 저장: 성공
        res1 = storage.save_notice(notice)
        self.assertTrue(res1)
        self.assertTrue(storage.is_already_seen(notice.unique_id))

        # 두 번째 동일 공고 저장 시도: 중복 방지 (False 반환)
        res2 = storage.save_notice(notice)
        self.assertFalse(res2)

        # 조회 확인
        recent = storage.get_recent_notices(limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["title"], "태양광 발전설비 설치공사")
        self.assertEqual(recent[0]["matched_keywords"], ["태양광"])

    def test_multi_channel_notifier_formatting(self):
        """4. 알림 메시지 포맷팅 및 ntfy 발송 구조 검증"""
        notifier = MultiChannelNotifier(config=self.config)
        notice = BidNotice(
            bid_no="2026090555", bid_seq="00",
            title="하천 생태복원 사업 기본계획 수립",
            category="용역", order_agency="금강유역환경청", announce_agency="조달청",
            budget=450000000, post_date="2026-09-22 11:00", close_date="2026-09-29 18:00",
            detail_url="https://www.g2b.go.kr", env_category="토양/생태/영향평가",
            matched_keywords=["생태복원", "하천"]
        )
        msg = notifier.format_message(notice)
        self.assertIn("하천 생태복원 사업 기본계획 수립", msg)
        self.assertIn("금강유역환경청", msg)
        self.assertIn("4.5억원", msg)

        # 실제 ntfy.sh 발송 통신 테스트 (HTTP 200 반환 여부 실증)
        sent = notifier.send_ntfy(notice, topic=f"unit-test-{int(datetime.now().timestamp())}")
        self.assertTrue(sent, "ntfy.sh 서버와 실시간 통신 및 푸시 발송 성공 확인")

    def test_daemon_full_cycle(self):
        """5. 모니터링 데몬 1회 순환 수집 및 중복 필터링 엔드투엔드 검증"""
        from unittest.mock import patch
        with patch.object(MultiChannelNotifier, "send_ntfy", return_value=True):
            daemon = MonitoringDaemon(config_path=self.config_path, db_path=self.db_path)
            stats1 = daemon.check_once()
            self.assertGreater(stats1["total_collected"], 0)
            self.assertGreater(stats1["matched_env"], 0)
            self.assertGreater(stats1["new_alerts_sent"], 0)

            # 바로 2회차 실행 시 -> 이미 수집된 공고이므로 신규 알람 전송 0건이어야 함 (중복 방지 실증)
            stats2 = daemon.check_once()
            self.assertEqual(stats2["new_alerts_sent"], 0)

if __name__ == "__main__":
    unittest.main()
