from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any

@dataclass
class BidNotice:
    """나라장터 입찰공고 데이터 모델"""
    bid_no: str               # 공고번호 (예: 20260912345)
    bid_seq: str              # 공고차수 (예: 00)
    title: str                # 공고명
    category: str             # 공고구분 (용역, 공사, 물품 등)
    order_agency: str         # 발주기관/수요기관
    announce_agency: str      # 공고기관
    budget: Optional[int] = 0 # 추정가격 / 배정예산 (원)
    contract_method: str = "" # 계약방법 (일반경쟁, 수의계약 등)
    post_date: str = ""       # 공고게시일시 (YYYY-MM-DD HH:MM)
    close_date: str = ""      # 입찰마감일시 (YYYY-MM-DD HH:MM)
    detail_url: str = ""      # 나라장터 공고 상세 링크
    matched_keywords: List[str] = field(default_factory=list) # 매칭된 환경 키워드
    env_category: str = ""    # 환경 분류 (수질, 대기, 폐기물 등)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        """중복 방지를 위한 고유 키"""
        return f"{self.bid_no}-{self.bid_seq}"

    @property
    def formatted_budget(self) -> str:
        """금액 포맷팅"""
        if not self.budget:
            return "금액 정보 없음"
        if self.budget >= 100_000_000:
            eok = self.budget / 100_000_000
            return f"{eok:,.1f}억원 ({self.budget:,.0f}원)"
        return f"{self.budget:,.0f}원"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["unique_id"] = self.unique_id
        d["formatted_budget"] = self.formatted_budget
        return d
