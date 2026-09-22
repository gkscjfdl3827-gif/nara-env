import requests
import json
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET
from .models import BidNotice

class NaraCollector:
    """나라장터 입찰공고 수집기 (공공데이터 OpenAPI + 웹/피드 + 시뮬레이터)"""

    # 공공데이터포털 조달청 입찰공고정보 OpenAPI 엔드포인트
    OPENAPI_BASE = "http://apis.data.go.kr/1230000/BidPublicInfoService05"

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key

    def fetch_from_openapi(
        self, 
        category: str = "servc", 
        page_no: int = 1, 
        num_of_rows: int = 50,
        days_back: int = 3
    ) -> List[BidNotice]:
        """
        공공데이터포털 조달청 나라장터 OpenAPI로 최근 공고를 수집합니다.
        category: 'servc'(용역), 'cnstwk'(공사), 'thng'(물품)
        """
        if not self.service_key:
            return []

        now = datetime.now()
        start_dt = (now - timedelta(days=days_back)).strftime("%Y%m%d0000")
        end_dt = now.strftime("%Y%m%d2359")

        endpoint_map = {
            "servc": f"{self.OPENAPI_BASE}/getBidPblancListInfoServcPPSSrch",
            "cnstwk": f"{self.OPENAPI_BASE}/getBidPblancListInfoCnstwkPPSSrch",
            "thng": f"{self.OPENAPI_BASE}/getBidPblancListInfoThngPPSSrch"
        }
        url = endpoint_map.get(category, endpoint_map["servc"])

        params = {
            "serviceKey": self.service_key,
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "inqryDiv": "1", # 1: 공고게시일시
            "inqryBgnDt": start_dt,
            "inqryEndDt": end_dt,
            "type": "json"
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                print(f"[Collector] OpenAPI error HTTP {resp.status_code}")
                return []

            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", [])
            if isinstance(items, dict):
                items = [items]
            
            notices: List[BidNotice] = []
            for item in items:
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
                    detail_url=item.get("bidNtceDtlUrl", ""),
                    raw_data=item
                )
                notices.append(notice)
            return notices
        except Exception as e:
            print(f"[Collector] OpenAPI fetch exception: {e}")
            return []

    def fetch_all_openapi(self, num_of_rows: int = 50, days_back: int = 3) -> List[BidNotice]:
        """용역, 공사, 물품 전 분야 OpenAPI 통합 수집"""
        all_notices = []
        for cat in ["servc", "cnstwk", "thng"]:
            res = self.fetch_from_openapi(category=cat, num_of_rows=num_of_rows, days_back=days_back)
            all_notices.extend(res)
        return all_notices

    def generate_mock_notices(self, count: int = 15) -> List[BidNotice]:
        """
        시스템 검증 및 시연을 위한 실물 수준의 나라장터 환경 공고 샘플 생성기
        """
        now = datetime.now()
        sample_templates = [
            ("OO도시기본계획(재수립) 전략환경영향평가 용역", "용역", "충청남도 천안시", 380000000, "일반경쟁", "전략환경영향평가"),
            ("국도OO호선 확포장공사 환경영향평가(사후환경영향조사 포함) 용역", "용역", "국토교통부 대전지방국토관리청", 720000000, "일반경쟁", "환경영향평가"),
            ("스마트 관광휴양단지 조성사업 소규모환경영향평가 용역", "용역", "강원특별자치도 삼척시", 165000000, "제한경쟁", "소규모환경영향평가"),
            ("OO산업단지 폐수종말처리시설 설치사업 사후환경영향조사 용역", "용역", "한국환경공단", 290000000, "일반경쟁", "사후환경영향조사"),
            ("OO항만 배후단지 개발계획 전략환경영향평가 용역", "용역", "해양수산부 부산항만공사", 410000000, "일반경쟁", "전략환경영향평가"),
            ("신규 공공지원민간임대주택 공급촉진지구 소규모환경영향평가", "용역", "한국토지주택공사(LH)", 195000000, "일반경쟁", "소규모환경영향평가"),
            ("OO태양광 및 풍력발전단지 구축사업 환경영향평가 용역", "용역", "한국중부발전", 530000000, "일반경쟁", "환경영향평가"),
            ("하수관로정비 BTL사업 3차년도 사후환경영향조사", "용역", "경기도 용인시", 140000000, "수의계약", "사후환경영향조사"),
            # 비대상 공고 (필터링 테스트용)
            ("2026년 청사 전산서버 유지보수 사업", "용역", "행정안전부 국가정보자원관리원", 450000000, "일반경쟁", "비환경"),
            ("구내식당 식자재 육류 납품 계약", "물품", "경찰청", 120000000, "일반경쟁", "비환경"),
            ("청사용 종량제 쓰레기봉투 구매", "물품", "강남구청", 15000000, "수의계약", "비환경"),
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
