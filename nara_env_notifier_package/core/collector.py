import requests
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from .models import BidNotice

class NaraCollector:
    """나라장터 입찰공고 수집기 (공공데이터 OpenAPI + 시뮬레이터)"""

    OPENAPI_BASE = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key

    def fetch_from_openapi(
        self, 
        category: str = "servc", 
        num_of_rows: int = 999,
        hours_back: int = 12
    ) -> List[BidNotice]:
        """한국 시간(KST) 기준 최근 12시간 공고 수집"""
        if not self.service_key:
            return []

        # GitHub 가상머신(UTC)에서도 한국 시간(KST)으로 정확하게 계산
        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        start_dt = (now - timedelta(hours=hours_back)).strftime("%Y%m%d%H%M")
        end_dt = now.strftime("%Y%m%d%H%M")

        endpoint_map = {
            "servc": f"{self.OPENAPI_BASE}/getBidPblancListInfoServcPPSSrch",
            "cnstwk": f"{self.OPENAPI_BASE}/getBidPblancListInfoCnstwkPPSSrch",
            "thng": f"{self.OPENAPI_BASE}/getBidPblancListInfoThngPPSSrch"
        }
        url = endpoint_map.get(category, endpoint_map["servc"])

        all_items = []
        page_no = 1

        while page_no <= 3:
            params = {
                "serviceKey": self.service_key,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "inqryDiv": "1",
                "inqryBgnDt": start_dt,
                "inqryEndDt": end_dt,
                "type": "json"
            }

            try:
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code != 200:
                    break

                data = resp.json()
                body = data.get("response", {}).get("body", {})
                total_count = body.get("totalCount", 0)
                items = body.get("items", [])
                
                if isinstance(items, dict):
                    items = items.get("item", [items])
                elif isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and "item" in items[0]:
                    items = items[0]["item"]

                if not items:
                    break

                all_items.extend(items)

                if len(all_items) >= total_count or len(items) < num_of_rows:
                    break

                page_no += 1
            except Exception as e:
                print(f"[Collector] Exception: {e}")
                break

        notices: List[BidNotice] = []
        for item in all_items:
            budget = 0
            if item.get("asignBdgtAmt"):
                try:
                    budget = int(float(item.get("asignBdgtAmt", 0)))
                except ValueError:
                    budget = 0

            cat_name_map = {"servc": "용역", "cnstwk": "공사", "thng": "물품"}
            notice = BidNotice(
                bid_no=str(item.get("bidNtceNo", "")),
                bid_seq=str(item.get("bidNtceOrd", "00")),
                title=item.get("bidNtceNm", "").strip(),
                category=cat_name_map.get(category, "기타"),
                order_agency=item.get("dminsttNm", "").strip(),
                announce_agency=item.get("ntceInsttNm", "").strip(),
                budget=budget,
                contract_method=item.get("cntrctCnclsMthdNm", ""),
                post_date=item.get("bidNtceDt", ""),
                close_date=item.get("bidClseDt", ""),
                detail_url=item.get("bidNtceDtlUrl", "") or item.get("bidNtceUrl", ""),
                raw_data=item
            )
            notices.append(notice)
            
        return notices

    def fetch_all_openapi(self, num_of_rows: int = 999, days_back: int = 1) -> List[BidNotice]:
        """용역, 공사, 물품 전 분야 OpenAPI 수집 (한국 시간 기준 최근 12시간)"""
        all_notices = []
        for cat in ["servc", "cnstwk", "thng"]:
            res = self.fetch_from_openapi(category=cat, num_of_rows=num_of_rows, hours_back=12)
            all_notices.extend(res)
        return all_notices

    def generate_mock_notices(self, count: int = 5) -> List[BidNotice]:
        """테스트 시뮬레이션용"""
        return []
