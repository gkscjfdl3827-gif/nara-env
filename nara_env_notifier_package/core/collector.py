import requests
import json
import random
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from .models import BidNotice

class NaraCollector:
    """나라장터 입찰공고 수집기 (공공데이터 OpenAPI + 시뮬레이터)"""

    # 올바른 조달청 입찰공고 API 주소 (/ad/ 경로 필수)
    OPENAPI_BASE = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key

    def fetch_from_openapi(
        self, 
        category: str = "servc", 
        num_of_rows: int = 999,
        hours_back: int = 12
    ) -> List[BidNotice]:
        """
        공공데이터포털 조달청 나라장터 OpenAPI로 최근 공고를 수집합니다.
        - hours_back: 최근 몇 시간 전부터 조회할지 (기본 12시간 - 한국 시간 기준 누락 방지)
        - category: 'servc'(용역), 'cnstwk'(공사), 'thng'(물품)
        """
        if not self.service_key:
            return []

        # GitHub 가상머신(UTC)에서도 한국 시간(KST)으로 정확히 계산
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

        # 최대 3페이지(최대 약 3,000건)까지 순회하며 누락 없이 수집
        while page_no <= 3:
            params = {
                "serviceKey": self.service_key,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "inqryDiv": "1",  # 1
