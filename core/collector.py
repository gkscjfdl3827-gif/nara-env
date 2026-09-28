import requests
import json
import html
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from .models import BidNotice

class NaraCollector:
    """
    나라장터 전주기 공공조달 모니터링 수집기
    1. 사전규격 (HrcspSsstndrd)
    2. 발주계획 (OrderPlan)
    3. 입찰공고 (BidPbac - 조달청 자체공고 및 LH, 국방부, 한전 등 외부연계기관 공고 전수)
    """

    OPENAPI_BASE = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"
    G2B_BASE = "https://www.g2b.go.kr"

    def __init__(self, service_key: Optional[str] = None):
        self.service_key = service_key

    @staticmethod
    def _clean_title(val: Any) -> str:
        """HTML 태그 및 특수문자 엔티티를 완벽하게 제거하여 깨끗한 텍스트로 정제"""
        if not val:
            return ""
        text = html.unescape(str(val))
        text = re.sub(r'<[^>]+>', '', text)
        text = html.unescape(text)
        return text.strip()

    @staticmethod
    def _format_date(val: Any) -> str:
        """YYYYMMDD 또는 YYYYMMDDHHMMSS 문자열을 읽기 쉬운 포맷으로 변환"""
        if not val or val == "0":
            return ""
        s = str(val).strip()
        if len(s) == 14 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
        elif len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return s

    @staticmethod
    def _parse_int(val: Any) -> int:
        """금액 문자열을 안전하게 정수로 변환"""
        if not val or val == "0":
            return 0
        try:
            return int(float(str(val).replace(",", "").strip()))
        except (ValueError, TypeError):
            return 0

    def fetch_from_unty_search(self, days_back: int = 3) -> List[BidNotice]:
        """
        나라장터 차세대 통합검색(UntySrch)을 통해
        1. 사전규격(BD), 2. 발주계획(DD), 3. 입찰공고(BK, BUK) 3대 전주기를
        LH, 국방부, 한전, 수자원, 지자체 등 모든 기관을 아울러 실시간 전수 수집합니다.
        """
        url = f'{self.G2B_BASE}/fi/fiu/fiua/UntySrch/srchUntyTotal.do'
        menu_info = json.dumps({"menuNo":"13677","menuCangVal":"FIUA009_01","bsneClsfCd":"업130020","scrnNo":"05665"}, ensure_ascii=False)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Content-Type': 'application/json;charset=UTF-8',
            'Menu-Info': urllib.parse.quote(menu_info),
            'Referer': f'{self.G2B_BASE}/'
        }

        today = datetime.now()
        from_dt = (today - timedelta(days=days_back)).strftime('%Y%m%d')
        to_dt = today.strftime('%Y%m%d')

        keywords = ['환경영향', '전략환경', '소규모환경', '사후환경']
        stage_targets = [
            ('BD', '사전규격'),
            ('DD', '발주계획'),
            ('BK', '입찰공고'),
            ('BUK', '입찰공고')
        ]

        notices: List[BidNotice] = []
        seen_uids = set()

        for stg_cd, stage_name in stage_targets:
            for kw in keywords:
                payload = {
                    'dlSrchParamM': {
                        'currentPage': 1,
                        'recordCountPerPage': '100',
                        'bizNm': kw,
                        'untySrchSeCd': stg_cd,
                        'startBizYmd': from_dt,
                        'endBizYmd': to_dt
                    }
                }
                try:
                    resp = requests.post(url, json=payload, headers=headers, timeout=12)
                    if resp.status_code != 200:
                        continue

                    items = resp.json().get('dlTotalSrchL', [])
                    for it in items:
                        biz_no = str(it.get('bizNo') or '').strip()
                        biz_ord = str(it.get('bizOrd') or '00').strip()
                        if not biz_no:
                            continue

                        uid = f"{stage_name}-{biz_no}-{biz_ord}"
                        if uid in seen_uids:
                            continue
                        seen_uids.add(uid)

                        # 제목 정제 (HTML 태그 제거 및 unescape)
                        title = self._clean_title(it.get('bizNm', ''))

                        # 날짜 파싱
                        post_date = self._format_date(it.get('pbancPstgDt') or it.get('inptDt') or it.get('bizYmd'))
                        
                        # 마감일시 / 발주예정시기 파싱
                        close_date = ""
                        if stg_cd == 'DD' and it.get('oderSrchItm01'):
                            ym = str(it.get('oderSrchItm01')).strip()
                            if len(ym) >= 6:
                                close_date = f"발주예정: {ym[:4]}년 {ym[4:6]}월"
                            else:
                                close_date = f"발주예정: {ym}"
                        elif it.get('pbancSrchItm02'):
                            close_date = self._format_date(it.get('pbancSrchItm02'))
                        elif stg_cd == 'BD':
                            close_date = "의견수렴 기간 (통상 5일간)"

                        # 예산 파싱
                        budget = self._parse_int(it.get('pbancSrchItm03')) or self._parse_int(it.get('ctrtAmt'))

                        # 기관명
                        order_inst = it.get('dmstUntyGrpNm') or it.get('pbancInstUntyGrpNm') or ''
                        announce_inst = it.get('pbancInstUntyGrpNm') or it.get('dmstUntyGrpNm') or ''

                        # 상세 URL 구성
                        ext_url = it.get('bizDtlItm03', '').strip()
                        if ext_url and ext_url.startswith('http'):
                            detail_url = ext_url
                        elif stg_cd == 'BK':
                            detail_url = f"https://www.g2b.go.kr/pn/pnp/pnpe/BidPbac/selectBidPbacDtl.do?bidPbacNo={biz_no}&bidPbacOrd={biz_ord}"
                        elif stg_cd == 'BD':
                            detail_url = f"https://www.g2b.go.kr/pn/pnp/pnpd/HrcspSsstndrd/selectHrcspSsstndrdDtl.do?bfSpecRgstNo={biz_no}"
                        elif stg_cd == 'DD':
                            detail_url = f"https://www.g2b.go.kr/pn/pnp/pnpd/OrderPlan/selectOrderPlanDtl.do?orderPlanNo={biz_no}"
                        else:
                            detail_url = "https://www.g2b.go.kr"

                        notice = BidNotice(
                            bid_no=biz_no,
                            bid_seq=biz_ord,
                            title=title,
                            category=it.get('prcmBsneAreaNm') or '기술용역',
                            order_agency=order_inst,
                            announce_agency=announce_inst,
                            budget=budget,
                            contract_method=it.get('bidCtrtMthdNm', ''),
                            post_date=post_date,
                            close_date=close_date,
                            detail_url=detail_url,
                            stage=stage_name,
                            raw_data=it
                        )
                        notices.append(notice)
                except Exception as e:
                    print(f"[NaraCollector] unty_search 실패 (단계: {stage_name}, 키워드: {kw}): {e}")

        return notices

    def fetch_from_g2b_unified(self, days_back: int = 3) -> List[BidNotice]:
        """
        나라장터 입찰공고 스크롤 엔드포인트를 통해
        LH, 국방부 등 연계 입찰공고를 보조 수집합니다.
        """
        s = requests.Session()
        s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Referer': f'{self.G2B_BASE}/',
        })

        try:
            # 1. 메인 세션 쿠키 획득
            s.get(f'{self.G2B_BASE}/', timeout=10)

            # 2. WebSquare 세션 핸드셰이크
            h_sess = {
                'Content-Type': 'application/json;charset=UTF-8',
                'Menu-Info': '{"menuNo":"02488","menuCangVal":"FUUA001_01","bsneClsfCd":"%EC%97%85130024","scrnNo":"00406"}',
                'Target-Id': 'genMenu1_1_genMenu2_0_genMenu3_0_btnMenu3',
                'submissionid': 'mf_sbmGetSession',
                'Usr-Id': 'null'
            }
            s.post(f'{self.G2B_BASE}/co/coz/coza/util/getSession.do', headers=h_sess, timeout=10)

            # 3. 입찰공고 검색 엔드포인트 호출
            url = f'{self.G2B_BASE}/pn/pnp/pnpe/BidPbac/selectBidPbacScrollTypeList.do'
            post_headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json;charset=UTF-8',
                'Menu-Info': '{"menuNo":"01175","menuCangVal":"PNPE001_01","bsneClsfCd":"%EC%97%85130026","scrnNo":"00941"}',
                'submissionid': 'mf_wfm_container_tacBidPbancLst_contents_tab2_body_sbmPbancBidPbancLst',
                'Usr-Id': 'null'
            }

            today = datetime.now()
            from_dt = (today - timedelta(days=days_back)).strftime('%Y%m%d')
            to_dt = today.strftime('%Y%m%d')

            keywords = ['환경영향평가', '전략환경', '소규모환경', '사후환경']
            notices: List[BidNotice] = []
            seen_uids = set()

            for kw in keywords:
                payload = {
                    'dlBidPbancLstM': {
                        'untyBidPbancNo': '',
                        'bidPbancNo': '',
                        'bidPbancOrd': '',
                        'prcmBsneUntyNoOrd': '',
                        'prcmBsneSeCd': '0000 조070001 조070002 조070003 조070004 조070005 민079999',
                        'bidPbancNm': kw,
                        'pbancPstgDt': '',
                        'ldocNoVal': '',
                        'bidPrspPrce': '',
                        'ctrtDmndRcptNo': '',
                        'dmstcOvrsSeCd': '',
                        'pbancKndCd': '공440002',
                        'ctrtTyCd': '',
                        'bidCtrtMthdCd': '',
                        'scsbdMthdCd': '',
                        'fromBidDt': from_dt,
                        'toBidDt': to_dt,
                        'minBidPrspPrce': '',
                        'maxBidPrspPrce': '',
                        'bsneAllYn': 'Y', 'frcpYn': 'Y', 'rsrvYn': 'Y', 'laseYn': 'Y',
                        'untyGrpGb': '', 'dmstNm': '', 'pbancPicNm': '',
                        'odnLmtLgdngCd': '', 'odnLmtLgdngNm': '',
                        'intpCd': '', 'intpNm': '', 'dtlsPrnmNo': '', 'dtlsPrnmNm': '',
                        'slprRcptDdlnYn': '', 'lcrtTyCd': '', 'isMas': '', 'isElpdt': '',
                        'oderInstUntyGrpNo': '', 'instSearchRangeYn': '', 'esdacYn': '',
                        'infoSysCd': '정010029', 'contxtSeCd': '콘010006', 'bidDateType': 'R',
                        'brcoOrgnCd': '', 'deptOrgnCd': '', 'isShop': '',
                        'srchTy': '0', 'cangParmVal': '', 'currentPage': '',
                        'recordCountPerPage': '50', 'startIndex': 1, 'endIndex': 50
                    }
                }
                resp = s.post(url, headers=post_headers, json=payload, timeout=12)
                if resp.status_code != 200:
                    continue

                items = resp.json().get('result', [])
                for it in items:
                    bid_no = str(it.get('bidPbancUntyNo') or it.get('bidPbancNo') or '').strip()
                    bid_seq = str(it.get('bidPbancUntyOrd') or it.get('bidPbancOrd') or '00').strip()
                    if not bid_no:
                        continue

                    uid = f"입찰공고-{bid_no}-{bid_seq}"
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)

                    title = self._clean_title(it.get('bidPbancNm', ''))

                    date_raw = html.unescape(it.get('pbancPstgDt', ''))
                    post_date = ''
                    close_date = ''
                    if '<br/>' in date_raw:
                        parts = date_raw.split('<br/>')
                        post_date = parts[0].strip()
                        close_date = parts[1].replace('(', '').replace(')', '').strip()
                    else:
                        post_date = date_raw.strip()

                    budget = 0
                    if it.get('alotBgtAmt'):
                        try:
                            budget = int(float(it.get('alotBgtAmt', 0)))
                        except (ValueError, TypeError):
                            budget = 0
                    elif it.get('prspPrce'):
                        try:
                            budget = int(float(it.get('prspPrce', 0)))
                        except (ValueError, TypeError):
                            budget = 0

                    link = it.get('linkInstPbancLnkUrl') or f"https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={bid_no}&bidPbancOrd={bid_seq}"

                    notice = BidNotice(
                        bid_no=bid_no,
                        bid_seq=bid_seq,
                        title=title,
                        category=it.get('prcmBsneSeCdNm') or '기술용역',
                        order_agency=it.get('dmstNm') or it.get('oderInstUntyGrpNm') or '',
                        announce_agency=it.get('oderInstUntyGrpNm') or it.get('dmstNm') or '',
                        budget=budget,
                        contract_method=it.get('scsbdMthdNm', ''),
                        post_date=post_date,
                        close_date=close_date,
                        detail_url=link,
                        stage="입찰공고",
                        raw_data=it
                    )
                    notices.append(notice)
            return notices
        except Exception as e:
            print(f"[NaraCollector] g2b 입찰공고 스크롤 수집 오류: {e}")
            return []

    def fetch_from_openapi(
        self, 
        category: str = "servc", 
        num_of_rows: int = 100,
        hours_back: int = 12
    ) -> List[BidNotice]:
        """한국 시간(KST) 기준 조달청 공공데이터 OpenAPI 수집 (보조)"""
        if not self.service_key:
            return []

        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)
        start_dt = (now - timedelta(hours=hours_back)).strftime("%Y%m%d%H%M")
        end_dt = now.strftime("%Y%m%d%H%M")

        endpoint_map = {
            "servc": f"{self.OPENAPI_BASE}/getBidPblancListInfoServc",
            "cnstwk": f"{self.OPENAPI_BASE}/getBidPblancListInfoCnstwk",
            "thng": f"{self.OPENAPI_BASE}/getBidPblancListInfoThng"
        }
        url = endpoint_map.get(category, endpoint_map["servc"])

        params = {
            "serviceKey": self.service_key,
            "pageNo": 1,
            "numOfRows": num_of_rows,
            "inqryDiv": "1",
            "inqryBgnDt": start_dt,
            "inqryEndDt": end_dt,
            "type": "json"
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
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
                    except (ValueError, TypeError):
                        budget = 0

                cat_name_map = {"servc": "기술용역", "cnstwk": "공사", "thng": "물품"}
                bid_no = str(item.get("bidNtceNo", ""))
                bid_seq = str(item.get("bidNtceOrd", "00"))
                notice = BidNotice(
                    bid_no=bid_no,
                    bid_seq=bid_seq,
                    title=item.get("bidNtceNm", "").strip(),
                    category=cat_name_map.get(category, "용역"),
                    order_agency=item.get("dminsttNm", "").strip(),
                    announce_agency=item.get("ntceInsttNm", "").strip(),
                    budget=budget,
                    contract_method=item.get("cntrctCnclsMthdNm", ""),
                    post_date=item.get("bidNtceDt", ""),
                    close_date=item.get("bidClseDt", ""),
                    detail_url=item.get("bidNtceDtlUrl", "") or item.get("bidNtceUrl", ""),
                    stage="입찰공고",
                    raw_data=item
                )
                notices.append(notice)
            return notices
        except Exception:
            return []

    def fetch_all(self, days_back: int = 3) -> List[BidNotice]:
        """
        3대 전주기 통합 수집:
        1. 사전규격, 2. 발주계획, 3. 입찰공고 (LH, 국방부, 한전 등 외부연계기관 전수 포함)
        OpenAPI 보조 수집 결합 및 단계별 고유 ID 기반 중복 제거
        """
        all_notices = []
        seen = set()

        # 1. 나라장터 차세대 통합검색 (사전규격 + 발주계획 + 입찰공고 전수)
        unty_notices = self.fetch_from_unty_search(days_back=days_back)
        for n in unty_notices:
            if n.unique_id not in seen:
                seen.add(n.unique_id)
                all_notices.append(n)

        # 2. 입찰공고 스크롤 엔드포인트 보강 수집
        g2b_notices = self.fetch_from_g2b_unified(days_back=days_back)
        for n in g2b_notices:
            if n.unique_id not in seen:
                seen.add(n.unique_id)
                all_notices.append(n)

        # 3. 공공데이터포털 OpenAPI 보조 수집
        if self.service_key:
            for cat in ["servc", "cnstwk"]:
                api_notices = self.fetch_from_openapi(category=cat, num_of_rows=100, hours_back=days_back * 24)
                for n in api_notices:
                    if n.unique_id not in seen:
                        seen.add(n.unique_id)
                        all_notices.append(n)

        return all_notices

    def fetch_all_openapi(self, num_of_rows: int = 100, days_back: int = 1) -> List[BidNotice]:
        return self.fetch_all(days_back=days_back)

    def generate_mock_notices(self, count: int = 5) -> List[BidNotice]:
        samples = [
            BidNotice(
                bid_no="202609280001",
                bid_seq="00",
                title="○○지구 도시개발사업 환경영향평가 용역",
                category="기술용역",
                order_agency="한국토지주택공사",
                announce_agency="한국토지주택공사",
                budget=500000000,
                contract_method="일반경쟁",
                post_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
                close_date=(datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d %H:%M"),
                detail_url="https://www.g2b.go.kr"
            ),
            BidNotice(
                bid_no="202609280002",
                bid_seq="00",
                title="○○하천 정비사업 전략환경영향평가",
                category="기술용역",
                order_agency="환경부",
                announce_agency="조달청",
                budget=300000000,
                contract_method="적격심사",
                post_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
                close_date=(datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d %H:%M"),
                detail_url="https://www.g2b.go.kr"
            )
        ]
        return samples[:count]
