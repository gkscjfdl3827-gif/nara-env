import streamlit as st
import json
import os
import pandas as pd
from datetime import datetime

from core.models import BidNotice
from core.storage import NoticeStorage
from core.notifier import MultiChannelNotifier
from core.daemon import MonitoringDaemon, load_config, save_config
from core.filter import DEFAULT_TARGET_CATEGORIES, DEFAULT_TARGET_KEYWORDS, DEFAULT_EXCLUDE_KEYWORDS

st.set_page_config(
    page_title="나라장터 환경영향평가 알리미",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 커스텀 스타일 (모바일 친화적 카드 디자인)
st.markdown("""
<style>
    .reportview-container {
        margin-top: -2em;
    }
    .badge {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: bold;
        margin-right: 4px;
        margin-bottom: 4px;
    }
    .badge-category { background-color: #E8F5E9; color: #1B5E20; border: 1px solid #81C784; }
    .badge-keyword { background-color: #E3F2FD; color: #0D47A1; border: 1px solid #90CAF9; }
    .notice-card {
        background: #ffffff;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 16px;
        border: 1px solid #E0E0E0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    .notice-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: #1A237E;
        margin-bottom: 8px;
    }
    .notice-meta {
        font-size: 0.9rem;
        color: #555555;
        line-height: 1.6;
    }
</style>
""", unsafe_allow_html=True)

# 초기화
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "nara_notices.db")

storage = NoticeStorage(db_path=DB_PATH)
config = load_config(CONFIG_PATH)
notifier = MultiChannelNotifier(config=config)
daemon = MonitoringDaemon(config_path=CONFIG_PATH, db_path=DB_PATH)

# 사이드바: 빠른 작업 & 시스템 요약
with st.sidebar:
    st.title("🌿 환경영향평가 알리미")
    st.caption("나라장터 실시간 4대 환경영향평가 스마트폰 알림")
    st.divider()

    st.subheader("⚡ 원클릭 즉시 작업")
    if st.button("🚀 지금 즉시 나라장터 수집 & 알림", use_container_width=True, type="primary"):
        with st.spinner("나라장터 최신 공고를 수집하고 환경영향평가 공고를 판별 중..."):
            stats = daemon.check_once()
            st.success(f"수집 완료!\n총 {stats['total_collected']}건 중 환경 공고 {stats['matched_env']}건 발견 (신규 {stats['new_alerts_sent']}건 알람 전송)")
            st.rerun()

    st.divider()
    st.subheader("📱 스마트폰 알람 테스트")
    test_channel = st.selectbox("테스트할 알림 채널", ["ntfy (초간편 푸시)", "telegram (텔레그램)", "discord (디스코드)"])
    if st.button("📲 핸드폰으로 테스트 알람 쏘기", use_container_width=True):
        channel_key = "ntfy" if "ntfy" in test_channel else ("telegram" if "telegram" in test_channel else "discord")
        with st.spinner("알림 발송 중..."):
            success = notifier.send_test_message(channel_key)
            if success:
                st.success("✅ 스마트폰으로 알림이 전송되었습니다! 화면을 확인해 보세요.")
            else:
                st.error("❌ 알림 전송 실패. '알림 설정' 탭에서 토픽명 또는 토큰을 확인해 주세요.")

    st.divider()
    # 통계 정보
    stats_data = storage.get_stats()
    st.metric("누적 감지된 공고", f"{stats_data['total_notices']}건")
    st.metric("발송된 모바일 알림", f"{stats_data['notified_notices']}건")

# 메인 헤더
st.title("🌿 나라장터 환경영향평가 실시간 스마트폰 알리미")
st.markdown("🎯 **전략환경영향평가 | 소규모환경영향평가 | 환경영향평가 | 사후환경영향조사** 신규 공고 발생 시 **핸드폰으로 즉시 소리/진동 알람**을 발송합니다.")

# 탭 메뉴 구성
tab1, tab2, tab3 = st.tabs(["📋 감지된 공고 피드", "📱 스마트폰 알림 연동 설정", "⚙️ 타겟 키워드 및 API 설정"])

with tab1:
    col_filter1, col_filter2 = st.columns([2, 1])
    with col_filter1:
        cat_options = ["전체"] + list(DEFAULT_TARGET_CATEGORIES.keys())
        selected_cat = st.selectbox("카테고리 필터", cat_options)
    with col_filter2:
        search_query = st.text_input("공고명 / 기관명 검색", placeholder="예: 천안시, 사후환경")

    notices = storage.get_recent_notices(limit=100, env_category=selected_cat)

    if search_query:
        notices = [n for n in notices if search_query.lower() in n['title'].lower() or search_query.lower() in (n.get('order_agency') or '').lower()]

    st.markdown(f"**총 {len(notices)}건의 환경 공고가 등록되어 있습니다.**")

    if not notices:
        st.info("💡 아직 수집된 공고가 없습니다. 왼쪽 사이드바의 **[지금 즉시 나라장터 수집 & 알림]** 버튼을 눌러보세요!")
    else:
        for item in notices:
            with st.container():
                st.markdown(f"""
                <div class="notice-card">
                    <div class="notice-title">{item['title']}</div>
                    <div style="margin-bottom: 8px;">
                        <span class="badge badge-category">📁 {item.get('env_category', '환경')}</span>
                        <span class="badge badge-category">📋 {item.get('category', '공고')}</span>
                        <span class="badge badge-keyword">🔍 {' / '.join(item.get('matched_keywords', []))}</span>
                    </div>
                    <div class="notice-meta">
                        🏛️ <b>발주기관:</b> {item.get('order_agency') or item.get('announce_agency') or '-'}<br/>
                        💰 <b>배정예산:</b> {f"{item.get('budget'):,}원" if item.get('budget') else '금액 정보 없음'} | 🤝 <b>계약방법:</b> {item.get('contract_method') or '-'}<br/>
                        📅 <b>공고일시:</b> {item.get('post_date') or '-'} | ⏰ <b>마감일시:</b> {item.get('close_date') or '-'}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                url = item.get("detail_url") or "https://www.g2b.go.kr"
                st.link_button("🌐 나라장터 상세공고 원문 바로가기", url)
                st.write("")

with tab2:
    st.subheader("📱 스마트폰 알람 연동 설정 (3분 완성)")
    st.markdown("""
    회원가입 없이 10초 만에 스마트폰으로 알림을 받을 수 있는 **ntfy 푸시 알림** 또는 **텔레그램 메신저**를 지원합니다.
    """)

    # 1. ntfy 설정
    with st.expander("📲 1. ntfy.sh 초간편 스마트폰 푸시 알림 (강력 추천 / 10초 세팅)", expanded=True):
        st.markdown("""
        **[설정 방법]**
        1. 핸드폰(안드로이드 Play 스토어 또는 아이폰 App Store)에서 **`ntfy`** 앱을 무료 다운로드합니다.
        2. 앱 실행 후 우측 상단 `+` (구독 추가) 버튼을 누르고, 아래 입력한 **'나만의 알림 채널명(토픽)'**을 그대로 입력합니다.
        3. 설정 완료! 나라장터에 환경 공고가 뜨면 스마트폰 화면에 즉시 소리/진동과 함께 팝업 알람이 울립니다.
        """)

        use_ntfy = st.checkbox("ntfy 스마트폰 푸시 사용", value=config.get("use_ntfy", True))
        ntfy_topic = st.text_input("나만의 알림 채널명 (영어/숫자)", value=config.get("ntfy_topic", "nara-env-alert-myphone"), help="남들과 겹치지 않는 고유한 이름을 권장합니다 (예: my-env-notice-2026)")
        
        web_link = f"https://ntfy.sh/{ntfy_topic}"
        st.caption(f"💡 PC 브라우저에서도 수신 가능: [{web_link}]({web_link})")

    # 2. 텔레그램 설정
    with st.expander("💬 2. 텔레그램(Telegram) 봇 연동 (선택 사항)"):
        st.markdown("""
        **[설정 방법]**
        1. 텔레그램에서 `@BotFather`를 검색하고 `/newbot` 명령어로 봇을 만든 후 발급된 **API Token**을 입력합니다.
        2. 내 봇과 대화를 시작하고, `@userinfobot` 등을 통해 확인한 **Chat ID**를 입력합니다.
        """)
        use_telegram = st.checkbox("텔레그램 알림 사용", value=config.get("use_telegram", False))
        tg_token = st.text_input("Telegram Bot Token", value=config.get("telegram_bot_token", ""), type="password")
        tg_chat_id = st.text_input("Telegram Chat ID", value=config.get("telegram_chat_id", ""))

    # 3. 디스코드 설정
    with st.expander("🔔 3. 디스코드(Discord) 웹훅 연동 (선택 사항)"):
        use_discord = st.checkbox("디스코드 알림 사용", value=config.get("use_discord", False))
        discord_url = st.text_input("Discord Webhook URL", value=config.get("discord_webhook_url", ""))

    if st.button("💾 알림 설정 저장하기", type="primary"):
        config["use_ntfy"] = use_ntfy
        config["ntfy_topic"] = ntfy_topic.strip()
        config["use_telegram"] = use_telegram
        config["telegram_bot_token"] = tg_token.strip()
        config["telegram_chat_id"] = tg_chat_id.strip()
        config["use_discord"] = use_discord
        config["discord_webhook_url"] = discord_url.strip()
        save_config(config, CONFIG_PATH)
        st.success("✅ 알림 설정이 성공적으로 저장되었습니다!")

with tab3:
    st.subheader("⚙️ 타겟 평가 키워드 및 나라장터 OpenAPI 설정")

    with st.expander("🔑 공공데이터포털(data.go.kr) OpenAPI 키 설정"):
        st.markdown("""
        공공데이터포털에서 **'조달청_나라장터 공고정보서비스'** 무료 활용신청 후 발급받은 일반 인증키를 입력하시면 정부 공식 실시간 API로 수집됩니다.
        *(키가 없어도 시스템 기본 시뮬레이션 및 수집 모드로 즉시 테스트 가능합니다)*
        """)
        use_openapi = st.checkbox("공공데이터포털 OpenAPI 사용", value=config.get("use_openapi", False))
        service_key = st.text_input("공공데이터포털 일반 인증키 (ServiceKey)", value=config.get("data_go_kr_service_key", ""), type="password")
        simulation_mode = st.checkbox("OpenAPI 키 미입력 시 테스트/데모 공고 자동 생성", value=config.get("simulation_mode", True))

    with st.expander("🔍 4대 환경영향평가 감지 키워드", expanded=True):
        st.markdown("현재 **전략환경영향평가, 소규모환경영향평가, 환경영향평가, 사후환경영향조사** 4대 핵심 분야가 활성화되어 있습니다.")
        default_kws_str = ", ".join(config.get("custom_keywords") if config.get("custom_keywords") else DEFAULT_TARGET_KEYWORDS)
        new_kws = st.text_area("감지 대상 키워드 (쉼표로 구분)", value=default_kws_str, height=100)

    with st.expander("🚫 제외 키워드 (오탐지 방지 필터)"):
        st.markdown("단순 환경미화 청소용품이나 사무용품 등 입찰과 무관한 공고를 자동으로 걸러냅니다.")
        default_ex_str = ", ".join(config.get("custom_exclude_keywords") if config.get("custom_exclude_keywords") else DEFAULT_EXCLUDE_KEYWORDS)
        new_ex_kws = st.text_area("제외할 키워드 (쉼표로 구분)", value=default_ex_str, height=80)

    interval = st.number_input("자동 수집 주기 (초)", min_value=60, max_value=3600, value=int(config.get("check_interval_seconds", 300)), step=60)

    if st.button("💾 키워드 및 API 설정 저장"):
        config["use_openapi"] = use_openapi
        config["data_go_kr_service_key"] = service_key.strip()
        config["simulation_mode"] = simulation_mode
        config["check_interval_seconds"] = interval
        config["custom_keywords"] = [k.strip() for k in new_kws.split(",") if k.strip()]
        config["custom_exclude_keywords"] = [k.strip() for k in new_ex_kws.split(",") if k.strip()]
        save_config(config, CONFIG_PATH)
        st.success("✅ 키워드 및 수집 설정이 저장되었습니다!")
