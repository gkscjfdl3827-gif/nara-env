import re
from typing import List, Dict, Tuple, Optional
from .models import BidNotice

DEFAULT_TARGET_CATEGORIES = {
    "전략환경영향평가": ["전략환경영향평가", "전략환경", "전략평가"],
    "소규모환경영향평가": ["소규모환경영향평가", "소규모환경", "소규모평가"],
    "사후환경영향조사": ["사후환경영향조사", "사후환경영향평가", "사후환경조사", "사후환경"],
    "환경영향평가": ["환경영향평가", "환경영향조사", "환경영향검토"]
}

DEFAULT_TARGET_KEYWORDS = [
    "전략환경영향평가",
    "소규모환경영향평가",
    "사후환경영향조사",
    "환경영향평가"
]

DEFAULT_ENV_CATEGORIES = DEFAULT_TARGET_CATEGORIES
DEFAULT_ENV_KEYWORDS = DEFAULT_TARGET_KEYWORDS

DEFAULT_EXCLUDE_KEYWORDS = [
    "종량제봉투", "쓰레기봉투", "화장지", "청소용품", "사무용품"
]

class EnvironmentalFilter:
    def __init__(self, keywords=None, exclude_keywords=None, category_map=None):
        self.keywords = keywords if keywords else DEFAULT_TARGET_KEYWORDS
        self.exclude_keywords = exclude_keywords if exclude_keywords is not None else DEFAULT_EXCLUDE_KEYWORDS
        self.category_map = category_map if category_map is not None else DEFAULT_TARGET_CATEGORIES

    def evaluate(self, notice: BidNotice) -> Tuple[bool, List[str], str]:
        text_to_check = f"{notice.title} {notice.order_agency} {notice.announce_agency} {notice.category}"

        has_core_keyword = any(k in notice.title for k in ["환경영향평가", "환경영향조사", "전략환경", "소규모환경", "사후환경"])
        if not has_core_keyword:
            for ex_kw in self.exclude_keywords:
                if ex_kw.lower() in text_to_check.lower():
                    return False, [], ""

        matched = []
        for kw in sorted(self.keywords, key=lambda x: len(x), reverse=True):
            pattern = re.escape(kw)
            if re.search(pattern, text_to_check, re.IGNORECASE):
                matched.append(kw)

        if not matched:
            return False, [], ""

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
