import requests
import urllib.parse
from typing import Optional, Dict, Any
from .models import BidNotice

class MultiChannelNotifier:
    """스마트폰 푸시(ntfy.sh), 텔레그램 봇, 디스코드 등 멀티채널 알림 발송기"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def format_message(self, notice: BidNotice) -> str:
        """알림용 텍스트 메시지 생성"""
        keywords_str = ", ".join(notice.matched_keywords) if notice.matched_keywords else "환경"
        msg = (
            f"🌿 [나라장터 환경 공고 알림]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 공고명: {notice.title}\n"
            f"🏢 발주기관: {notice.order_agency or notice.announce_agency}\n"
            f"🏷 분류: [{notice.env_category}] ({notice.category})\n"
            f"💰 배정예산: {notice.formatted_budget}\n"
            f"⏰ 마감일시: {notice.close_date or '마감일 미정'}\n"
            f"🔍 매칭키워드: {keywords_str}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔗 공고 상세확인:\n{notice.detail_url or 'https://www.g2b.go.kr'}"
        )
        return msg

    def send_ntfy(self, notice: BidNotice, topic: Optional[str] = None) -> bool:
        """
        ntfy.sh 스마트폰 푸시 알림 발송
        스마트폰에서 'ntfy' 앱을 설치하고 해당 topic을 구독하면 즉시 알람이 울립니다.
        """
        target_topic = topic or self.config.get("ntfy_topic")
        if not target_topic:
            return False

        url = "https://ntfy.sh"
        title = f"🌿 [환경공고] {notice.env_category}: {notice.title}"
        body = (
            f"🏛️ 발주: {notice.order_agency or notice.announce_agency}\n"
            f"💰 예산: {notice.formatted_budget} | ⏰ 마감: {notice.close_date}\n"
            f"🔍 매칭: {', '.join(notice.matched_keywords)}"
        )

        payload = {
            "topic": target_topic,
            "title": title,
            "message": body,
            "priority": 4,
            "tags": ["earth_africa", "leaves", "bell"]
        }

        if notice.detail_url:
            payload["click"] = notice.detail_url
            payload["actions"] = [
                {
                    "action": "view",
                    "label": "공고 바로가기",
                    "url": notice.detail_url
                }
            ]

        try:
            resp = requests.post(url, json=payload, timeout=8)
            return resp.status_code == 200
        except Exception as e:
            print(f"[Notifier] ntfy push failed: {e}")
            return False

    def send_telegram(self, notice: BidNotice, bot_token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
        """텔레그램 봇 스마트폰 알림 발송"""
        token = bot_token or self.config.get("telegram_bot_token")
        cid = chat_id or self.config.get("telegram_chat_id")
        if not token or not cid:
            return False

        text = self.format_message(notice)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text,
            "disable_web_page_preview": False
        }

        # 인라인 버튼 추가 (공고 바로가기)
        if notice.detail_url:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": "🌐 나라장터 공고 바로가기", "url": notice.detail_url}]
                ]
            }

        try:
            resp = requests.post(url, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(f"[Notifier] Telegram send failed: {e}")
            return False

    def send_discord(self, notice: BidNotice, webhook_url: Optional[str] = None) -> bool:
        """디스코드 웹훅 알림 발송"""
        wh_url = webhook_url or self.config.get("discord_webhook_url")
        if not wh_url:
            return False

        embed = {
            "title": f"🌿 [환경공고] {notice.title}",
            "url": notice.detail_url or "https://www.g2b.go.kr",
            "color": 3066993, # 녹색 계열
            "fields": [
                {"name": "분류 / 카테고리", "value": f"{notice.env_category} ({notice.category})", "inline": True},
                {"name": "발주 / 수요기관", "value": notice.order_agency or notice.announce_agency or "-", "inline": True},
                {"name": "배정 예산", "value": notice.formatted_budget, "inline": True},
                {"name": "입찰 마감일시", "value": notice.close_date or "-", "inline": True},
                {"name": "매칭 키워드", "value": ", ".join(notice.matched_keywords) or "-", "inline": False},
            ],
            "footer": {"text": f"공고번호: {notice.unique_id}"}
        }

        try:
            resp = requests.post(wh_url, json={"embeds": [embed]}, timeout=5)
            return resp.status_code in [200, 204]
        except Exception as e:
            print(f"[Notifier] Discord send failed: {e}")
            return False

    def notify_all(self, notice: BidNotice) -> Dict[str, bool]:
        """설정된 모든 유효 채널로 알림 발송"""
        results = {}
        if self.config.get("use_ntfy", True) and self.config.get("ntfy_topic"):
            results["ntfy"] = self.send_ntfy(notice)
        if self.config.get("use_telegram") and self.config.get("telegram_bot_token"):
            results["telegram"] = self.send_telegram(notice)
        if self.config.get("use_discord") and self.config.get("discord_webhook_url"):
            results["discord"] = self.send_discord(notice)
        return results

    def send_test_message(self, channel: str) -> Tuple_Result if False else bool:
        """테스트 메시지 발송"""
        test_notice = BidNotice(
            bid_no="20260900001",
            bid_seq="00",
            title="[테스트] 2026 스마트 생태하천 복원 및 수질오염 방지시설 구축공사",
            category="공사",
            order_agency="환경부 원주지방환경청",
            announce_agency="조달청",
            budget=1520000000,
            contract_method="일반경쟁",
            post_date="2026-09-22 14:00",
            close_date="2026-09-30 18:00",
            detail_url="https://www.g2b.go.kr",
            matched_keywords=["수질", "생태복원", "오염"],
            env_category="수질/상하수도"
        )
        if channel == "ntfy":
            return self.send_ntfy(test_notice)
        elif channel == "telegram":
            return self.send_telegram(test_notice)
        elif channel == "discord":
            return self.send_discord(test_notice)
        return False
