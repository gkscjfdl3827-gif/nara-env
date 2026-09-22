import requests
import json
import random
from datetime import datetime, timedelta
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
        num_of_rows: int = 500,
        hours_back: int = 4
    ) -> List[BidNotice]:
        """
        공공데이터포털 조달청 나라장터 OpenAPI로 최근 공고를 수집합니다.
        - hours_back: 최근 몇 시간 전부터 조회할지 (기본 4시간 - 최신순 누락 방지)
        - category: 'servc'(용역), 'cnstwk'(공사), 'thng'(물품)
        """
        if not self.service_key:
            return []

        now = datetime.now()
        # 최근 4시간 전부터 현재까지로 설정하여 최신 공고가 뒤로 밀리지 않고 1~2페이지 내에 즉시 잡히도록 함
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

        # 최대 3페이지(최대 1,500건)까지 순회하며 누락 없이 수집
        while page_no <= 3:
            params = {
                "serviceKey": self.service_key,
                "pageNo": page_no,
                "numOfRows": num_of_rows,
                "inqryDiv": "1",  # 1: 공고게시일시
                "inqryBgnDt": start_dt,
                "inqryEndDt": end_dt,
                "type": "json"
            }

            try:
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code != 200:
                    print(f"[Collector] OpenAPI error HTTP {resp.status_code}")
                    break

                data = resp.json()
                body = data.get("response", {}).get("body", {})
                total_count = body.get("totalCount", 0)
                items = body.get("items", [])
                
                if isinstance(items, dict):
                    if "item" in items:
                        items = items["item"]
                    else:
                        items = [items]
                elif isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and "item" in items[0]:
                    items = items[0]["item"]

                if not items:
                    break

                all_items.extend(items)

                # 전체 개수를 다 가져왔으면 루프 종료
                if len(all_items) >= total_count or len(items) < num_of_rows:
                    break

                page_no += 1
            except Exception as e:
                print(f"[Collector] OpenAPI fetch exception: {e}")
                break

        notices: List[BidNotice] = []
        for item in all_items:
            # 예산 필드 오타 수정 (asgnBdgtAmt -> asignBdgtAmt)
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

    def fetch_all_openapi(self, num_of_rows: int = 500, days_back: int = 1) -> List[BidNotice]:
        """용역, 공사, 물품 전 분야 OpenAPI 통합 수집 (최근 4시간 집중 수집)"""
        all_notices = []
        for cat in ["servc", "cnstwk", "thng"]:
            res = self.fetch_from_openapi(category=cat, num_of_rows=num_of_rows, hours_back=4)
            all_notices.extend(res)
        return all_notices

    def generate_mock_notices(self, count: int = 15) -> List[BidNotice]:
        """테스트용 시뮬레이션 데이터 생성기"""
        now = datetime.now()
        sample_templates = [
            ("OO도시기본계획(재수립) 전략환경영향평가 용역", "용역", "충청남도 천안시", 380000000, "일반경쟁", "전략환경영향평가"),
            ("국도OO호선 확포장공사 환경영향평가(사후환경영향조사 포함) 용역", "용역", "국토교통부 대전지방국토관리청", 720000000, "일반경쟁", "환경영향평가"),
            ("스마트 관광휴양단지 조성사업 소규모환경영향평가 용역", "용역", "강원특별자치도 삼척시", 165000000, "제한경쟁", "소규모환경영향평가"),
            ("OO사업단지 폐수종말처리시설 설치사업 사후환경영향조사 용역", "용역", "한국환경공단", 290000000, "일반경쟁", "사후환경영향조사"),
        ]

        notices = []
        for i, item in enumerate(sample_templates[:count]):
            title, cat, agency, budget, method, _ = item
            post_time = now - timedelta(hours=i * 2, minutes=15)
            close_time = post_time + timedelta(days=7, hours=4)
            bid_num = f"20260922{1000 + i:04d}"
            
            notice = BidNotice(
                bid_no=bid_num,
                bid_seq="00",
                title=title,
                category=cat,
                order_agency=agency,
                announce_agency="조달청",
                budget=budget,
                contract_method=method,
                post_date=post_time.strftime("%Y-%m-%d %H:%M"),
                close_date=close_time.strftime("%Y-%m-%d %H:%M"),
                detail_url=f"https://www.g2b.go.kr/pt/menu/selectSubFrame.do?bidNo={bid_num}",
                raw_data={}
            )
            notices.append(notice)
        return notices
