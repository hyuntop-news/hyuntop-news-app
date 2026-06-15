import html
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import streamlit as st
from morning_news_mailer import run_mailer


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
NEWS_SCRIPT = BASE_DIR / "Send-MorningNews.ps1"
SCHEDULE_SCRIPT = BASE_DIR / "schedule_daily.ps1"
LOG_PATH = BASE_DIR / "logs" / "morning-news.log"

DEFAULT_SETTINGS = {
    "news_query": "과학",
    "news_limit": 5,
    "recipient_email": "",
    "notification_channel": "email",
    "schedule_time": "07:00",
    "blog_enabled": True,
    "blog_pick_index": 1,
    "blog_draft_dir": "blog_drafts",
    "retry_count": 3,
    "retry_delay_seconds": 3,
    "request_timeout_seconds": 10,
    "error_email_enabled": True,
}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()

    with SETTINGS_PATH.open("r", encoding="utf-8") as file:
        loaded = json.load(file)

    settings = DEFAULT_SETTINGS.copy()
    settings.update(loaded)
    return settings


def save_settings(settings: dict) -> None:
    with SETTINGS_PATH.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


def run_powershell(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        timeout=120,
    )


def read_recent_logs(limit: int = 8) -> list[str]:
    if not LOG_PATH.exists():
        return ["아직 실행 기록이 없습니다."]

    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:] or ["아직 실행 기록이 없습니다."]


def get_recent_draft(settings: dict) -> str:
    draft_dir = BASE_DIR / str(settings.get("blog_draft_dir") or "blog_drafts")
    if not draft_dir.exists():
        return "초안 없음"

    drafts = sorted(draft_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not drafts:
        return "초안 없음"

    return drafts[0].name


def show_result(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode == 0:
        st.success("완료되었습니다.")
        if result.stdout.strip():
            st.code(result.stdout.strip())
    else:
        st.error("실행 중 오류가 발생했습니다.")
        message = result.stderr.strip() or result.stdout.strip()
        if message:
            st.code(message)


def is_cloud() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE"))


def run_news_now(settings: dict) -> None:
    try:
        result = run_mailer(settings=settings)
        if result.get("sent", 0):
            st.success(f"{result['recipient']} 주소로 뉴스 {result['sent']}개를 보냈습니다.")
            blog_draft = result.get("blog_draft")
            if blog_draft:
                st.success("블로그 초안도 만들었습니다. 이메일에서 확인하세요.")
        else:
            st.warning(result.get("message", "보낼 뉴스가 없습니다."))
    except Exception as exc:
        st.error(f"실행 중 오류가 발생했습니다: {exc}")


def card(title: str, value: str, caption: str = "", accent: str = "cyan") -> None:
    accent_colors = {
        "cyan": "#22d3ee",
        "violet": "#8b5cf6",
        "green": "#22c55e",
        "amber": "#f59e0b",
        "rose": "#fb7185",
    }
    color = accent_colors.get(accent, accent_colors["cyan"])
    st.markdown(
        f"""
        <div class="metric-card" style="border-color:{color}55;">
            <div class="metric-label">{html.escape(title)}</div>
            <div class="metric-value">{html.escape(value)}</div>
            <div class="metric-caption">{html.escape(caption)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="현탑부동산 뉴스앱", page_icon="HN", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --bg: #070b16;
        --panel: #111827;
        --panel-2: #172033;
        --line: #263248;
        --text: #f8fafc;
        --muted: #8ea0bd;
        --cyan: #22d3ee;
        --violet: #8b5cf6;
        --green: #22c55e;
        --amber: #f59e0b;
    }

    .stApp {
        background: var(--bg);
        color: var(--text);
    }

    header[data-testid="stHeader"] {
        display: none;
    }

    div[data-testid="stToolbar"] {
        display: none;
    }

    div[data-testid="stDecoration"] {
        display: none;
    }

    section[data-testid="stSidebar"] {
        background: #0d1322;
        border-right: 1px solid var(--line);
    }

    section[data-testid="stSidebar"] * {
        color: var(--text);
    }

    div[data-testid="stSidebarNav"] {
        display: none;
    }

    .block-container {
        padding-top: 1.6rem;
        padding-bottom: 2rem;
        max-width: 1380px;
    }

    .topbar {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        border-bottom: 1px solid var(--line);
        padding: 8px 0 18px;
        margin-bottom: 24px;
        min-height: 88px;
    }

    .brand {
        font-weight: 900;
        letter-spacing: .08em;
        color: white;
        font-size: 20px;
    }

    .status-row {
        display: flex;
        gap: 10px;
        align-items: center;
        color: var(--muted);
        font-size: 13px;
    }

    .status-pill {
        border: 1px solid #14532d;
        background: #052e1a;
        color: #4ade80;
        border-radius: 999px;
        padding: 4px 10px;
        font-weight: 700;
    }

    .page-title {
        font-size: 28px;
        line-height: 1.25;
        font-weight: 900;
        margin: 0 0 6px 0;
        color: white;
        word-break: keep-all;
    }

    .page-subtitle {
        color: var(--muted);
        margin-top: 6px;
        margin-bottom: 20px;
        font-size: 14px;
    }

    .section-title {
        color: #c4b5fd;
        font-size: 14px;
        font-weight: 800;
        letter-spacing: .06em;
        margin: 18px 0 10px;
        text-transform: uppercase;
    }

    .metric-card {
        min-height: 150px;
        background: linear-gradient(180deg, #182235 0%, #111827 100%);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 18px;
        box-shadow: 0 14px 40px rgba(0, 0, 0, .25);
    }

    .metric-label {
        color: #dbeafe;
        font-size: 15px;
        font-weight: 800;
        margin-bottom: 10px;
    }

    .metric-value {
        color: white;
        font-size: 30px;
        line-height: 1.05;
        font-weight: 900;
    }

    .metric-caption {
        color: var(--muted);
        font-size: 13px;
        margin-top: 12px;
        word-break: break-word;
    }

    .log-box {
        background: #0b1020;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
        color: #b7c5dc;
        font-family: Consolas, monospace;
        font-size: 12px;
        white-space: pre-wrap;
        min-height: 260px;
    }

    .side-note {
        background: #111827;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
        margin-top: 12px;
    }

    .side-note strong {
        display: block;
        color: white;
        margin-bottom: 6px;
    }

    .stButton > button {
        border-radius: 8px;
        border: 1px solid #3b82f6;
        background: #172554;
        color: white;
        font-weight: 800;
        min-height: 42px;
    }

    .stButton > button:hover {
        border-color: var(--cyan);
        color: white;
        background: #1e3a8a;
    }

    div[data-testid="stForm"] {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 20px;
        background: #0d1322;
    }

    input, textarea, select {
        border-radius: 8px !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }

    .stTabs [data-baseweb="tab"] {
        background: #111827;
        border: 1px solid var(--line);
        border-radius: 8px;
        color: var(--text);
        padding: 10px 16px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

settings = load_settings()
now_text = datetime.now().strftime("%Y. %m. %d. %H:%M")

with st.sidebar:
    st.markdown('<div class="brand">HYUNTOP NEWS</div>', unsafe_allow_html=True)
    st.caption("자동화 컨트롤")
    menu = st.radio("메뉴", ["대시보드", "설정", "실행 기록"], label_visibility="collapsed")
    st.markdown(
        f"""
        <div class="side-note">
            <strong>시스템 상태</strong>
            데이터 수집: ONLINE<br>
            메일 발송: ONLINE<br>
            블로그 초안: {'ON' if settings['blog_enabled'] else 'OFF'}
        </div>
        <div class="side-note">
            <strong>오늘 설정</strong>
            키워드: {html.escape(str(settings['news_query'] or '전체'))}<br>
            시간: {html.escape(str(settings['schedule_time']))}<br>
            뉴스: {int(settings['news_limit'])}개
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    f"""
    <div class="topbar">
        <div>
            <div class="page-title">현탑부동산 뉴스 자동화 대시보드..</div>
            <div class="page-subtitle">{now_text} KST · 메일 발송, 블로그 초안, 스케줄러 관리</div>
        </div>
        <div class="status-row">
            <span class="status-pill">LIVE</span>
            <span>settings.json 저장</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if menu == "대시보드":
    st.markdown('<div class="section-title">Automation Overview</div>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        card("뉴스 키워드", str(settings["news_query"] or "전체"), "Google 뉴스 RSS 기준", "cyan")
    with col2:
        card("발송 시간", str(settings["schedule_time"]), "작업 스케줄러 등록 시간", "violet")
    with col3:
        recipient = str(settings["recipient_email"] or "기본 Gmail")
        card("수신 채널", recipient, "이메일 발송", "green")
    with col4:
        blog_status = "ON" if settings["blog_enabled"] else "OFF"
        card("블로그 초안", blog_status, f"{settings['blog_pick_index']}번째 뉴스 선택", "amber")

    st.markdown('<div class="section-title">Quick Actions</div>', unsafe_allow_html=True)
    action1, action2, action3 = st.columns([1, 1, 2])
    with action1:
        if st.button("지금 테스트 실행", use_container_width=True):
            run_news_now(settings)
    with action2:
        if st.button("스케줄러 등록/갱신", use_container_width=True, disabled=is_cloud()):
            show_result(run_powershell(["-File", str(SCHEDULE_SCRIPT), "-Time", settings["schedule_time"]]))
    with action3:
        if is_cloud():
            st.info("클라우드 예약 발송은 GitHub Actions가 담당합니다.")
        else:
            st.info("테스트 실행은 실제 메일을 발송합니다.")

    st.markdown('<div class="section-title">Recent Activity</div>', unsafe_allow_html=True)
    left, right = st.columns([1.4, 1])
    with left:
        st.markdown(
            f'<div class="log-box">{html.escape(chr(10).join(read_recent_logs()))}</div>',
            unsafe_allow_html=True,
        )
    with right:
        card("최근 블로그 초안", get_recent_draft(settings), "blog_drafts 폴더 기준", "rose")
        card("안정장치", f"재시도 {settings['retry_count']}회", f"시간초과 {settings['request_timeout_seconds']}초", "cyan")

elif menu == "설정":
    st.markdown('<div class="section-title">Configuration</div>', unsafe_allow_html=True)
    with st.form("settings_form"):
        tab_news, tab_blog, tab_guard = st.tabs(["뉴스/수신", "블로그", "안정장치"])

        with tab_news:
            news_query = st.text_input("키워드", value=settings["news_query"], placeholder="예: 과학, 경제, AI")
            news_limit = st.number_input("뉴스 개수", min_value=1, max_value=20, value=int(settings["news_limit"]))
            recipient_email = st.text_input("받는 이메일", value=settings["recipient_email"])
            notification_channel = st.selectbox(
                "수신 채널",
                options=["email", "none"],
                index=0 if settings["notification_channel"] == "email" else 1,
                format_func=lambda value: "이메일" if value == "email" else "알림 끄기",
            )
            schedule_time = st.text_input("매일 실행 시간", value=settings["schedule_time"], help="24시간 형식으로 입력하세요. 예: 07:00")

        with tab_blog:
            blog_enabled = st.toggle("블로그 초안 생성", value=bool(settings["blog_enabled"]))
            blog_pick_index = st.number_input(
                "고를 뉴스 번호",
                min_value=1,
                max_value=20,
                value=int(settings["blog_pick_index"]),
                disabled=not blog_enabled,
            )
            blog_draft_dir = st.text_input("초안 저장 폴더", value=settings["blog_draft_dir"], disabled=not blog_enabled)

        with tab_guard:
            retry_count = st.number_input("실패 시 재시도 횟수", min_value=0, max_value=10, value=int(settings["retry_count"]))
            retry_delay_seconds = st.number_input("재시도 간격(초)", min_value=1, max_value=60, value=int(settings["retry_delay_seconds"]))
            request_timeout_seconds = st.number_input("요청 시간 초과(초)", min_value=3, max_value=120, value=int(settings["request_timeout_seconds"]))
            error_email_enabled = st.toggle("오류 발생 시 이메일 알림", value=bool(settings["error_email_enabled"]))

        saved = st.form_submit_button("설정 저장", use_container_width=True)

    if saved:
        new_settings = {
            "news_query": news_query.strip(),
            "news_limit": int(news_limit),
            "recipient_email": recipient_email.strip(),
            "notification_channel": notification_channel,
            "schedule_time": schedule_time.strip(),
            "blog_enabled": bool(blog_enabled),
            "blog_pick_index": int(blog_pick_index),
            "blog_draft_dir": blog_draft_dir.strip() or "blog_drafts",
            "retry_count": int(retry_count),
            "retry_delay_seconds": int(retry_delay_seconds),
            "request_timeout_seconds": int(request_timeout_seconds),
            "error_email_enabled": bool(error_email_enabled),
        }
        save_settings(new_settings)
        st.success("설정을 저장했습니다.")
        settings = new_settings

elif menu == "실행 기록":
    st.markdown('<div class="section-title">Execution Log</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="log-box">{html.escape(chr(10).join(read_recent_logs(30)))}</div>',
        unsafe_allow_html=True,
    )

    with st.expander("현재 JSON 설정 보기"):
        st.json(settings)
