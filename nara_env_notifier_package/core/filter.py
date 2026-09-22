import re
from typing import List, Dict, Tuple, Optional
from .models import BidNotice

# 사용자가 지정한 4대 핵심 환경영향평가 카테고리 및 세부 매칭 키워드
DEFAULT_TARGET_CATEGORIES = {
    "전략환경영향평가": [
        "전략환경영향평가", "전략환경", "전략평가"
    ],
    "소규모환경영향평가": [
        "소규모환경영향평가", "소규모환경", "소규모평가"
    ],
    "사후환경영향조사": [
        "사후환경영향조사", "사후환경영향평가", "사후환경조사", "사후환경"
    ],
    "환경영향평가": [
        "환경영향평가", "환경영향조사", "환경영향검토"
    ]
}

# 기본 전체 타겟 키워드 (길이가 긴 구체적 키워드 우선)
DEFAULT_TARGET_KEYWORDS = [
    "전략환경영향평가",
    "소규모환경영향평가",
    "사후환경영향조사",
    "환경영향평가"
]

# 하위 호환성 별칭
DEFAULT_ENV_CATEGORIES = DEFAULT_TARGET_CATEGORIES
DEFAULT_ENV_KEYWORDS = DEFAULT_TARGET_KEYWORDS

# 제외 키워드 (단순 소모품 또는 타 분야 오탐 방지)
DEFAULT_EXCLUDE_KEYWORDS = [
    "종량제봉투", "쓰레기봉투", "화장지", "청소용품", "사무용품"
]

class EnvironmentalFilter:
    """
    환경영향평가 4대 분야 전문 필터링 엔진
    - 전략환경영향평가
    - 소규모환경영향평가
    - 사후환경영향조사
    - 환경영향평가
    """

    def __init__(
        self, 
        keywords: Optional[List[str]] = None, 
        exclude_keywords: Optional[List[str]] = None,
        category_map: Optional[Dict[str, List[str]]] = None
    ):
        # 지정된 키워드가 없으면 사용자의 4대 키워드를 기본 사용
        self.keywords = keywords if keywords else DEFAULT_TARGET_KEYWORDS
        self.exclude_keywords = exclude_keywords if exclude_keywords is not None else DEFAULT_EXCLUDE_KEYWORDS
        self.category_map = category_map if category_map is not None else DEFAULT_TARGET_CATEGORIES

    def evaluate(self, notice: BidNotice) -> Tuple[bool, List[str], str]:
        """
        공고명(title), 발주처 등을 검토하여 사용자가 원하는 4대 환경영향평가 공고인지 정밀 판별합니다.
        """
        text_to_check = f"{notice.title} {notice.order_agency} {notice.announce_agency} {notice.category}"

        # 1. 제외 키워드 검사 (단, 사용자의 핵심 공고명이 직접 들어있으면 제외하지 않음)
        has_core_keyword = any(core_kw in notice.title for core_kw in ["환경영향평가", "환경영향조사", "전략환경", "소규모환경", "사후환경"])
        if not has_core_keyword:
            for ex_kw in self.exclude_keywords:
                if ex_kw.lower() in text_to_check.lower():
                    return False, [], ""

        # 2. 키워드 매칭 (길이가 긴 단어 우선 매칭)
        matched: List[str] = []
        for kw in sorted(self.keywords, key=lambda x: len(x), reverse=True):
            pattern = re.escape(kw)
            if re.search(pattern, text_to_check, re.IGNORECASE):
                matched.append(kw)

        if not matched:
            return False, [], ""

        # 3. 4대 카테고리 정밀 분류 (특화 키워드 우선)
        best_category = "환경영향평가"
        if any(k in matched for k in self.category_map["전략환경영향평가"]):
            best_category = "전략환경영향평가"
        elif any(k in matched for k in self.category_map["소규모환경영향평가"]):
            best_category = "소규모환경영향평가"
        elif any(k in matched for k in self.category_map["사후환경영향조사"]):
            best_category = "사후환경영향조사"
        elif any(k in matched for k in self.category_map["환경영향평가"]):
            best_category = "환경영향평가"

        notice.matched_keywords = matched
        notice.env_category = best_category
        return True, matched, best_category
