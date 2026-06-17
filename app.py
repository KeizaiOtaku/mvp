from __future__ import annotations

import streamlit as st

from news_ranking_page import render_news_ranking_page
from edinet_checker_page import render_edinet_checker_page


st.set_page_config(
    page_title="相場★大好きマン★アプリ",
    page_icon="★",
    layout="wide",
)

APP_HOME = "home"
APP_NEWS = "news"
APP_EDINET = "edinet"


def go(page: str) -> None:
    st.session_state["active_app"] = page
    st.rerun()


def init_state() -> None:
    if "active_app" not in st.session_state:
        # 初回表示は共通トップページ「相場★大好きマン★アプリ」をデフォルトにする。
        st.session_state["active_app"] = APP_HOME


def render_global_sidebar() -> None:
    st.sidebar.title("相場★大好きマン★アプリ")
    st.sidebar.caption("共通ページから各アプリへ切り替えられます。")

    if st.sidebar.button("トップページ", use_container_width=True):
        go(APP_HOME)
    if st.sidebar.button("海外で話題の日本のニュース", use_container_width=True):
        go(APP_NEWS)
    if st.sidebar.button("法定開示情報チェッカー", use_container_width=True):
        go(APP_EDINET)


def render_top_nav(current_label: str | None = None) -> None:
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        if st.button("← 相場★大好きマン★アプリへ戻る", use_container_width=True):
            go(APP_HOME)
    with c2:
        if current_label != "海外で話題の日本のニュース":
            if st.button("海外で話題の日本のニュース", use_container_width=True):
                go(APP_NEWS)
    with c3:
        if current_label != "法定開示情報チェッカー":
            if st.button("法定開示情報チェッカー", use_container_width=True):
                go(APP_EDINET)
    st.markdown("---")


def render_home() -> None:
    st.markdown(
        """
        <style>
        .home-wrap {
            max-width: 1050px;
            margin: 0 auto;
            padding: 1.5rem 0 0.5rem 0;
        }
        .home-title {
            font-size: 3.0rem;
            font-weight: 900;
            color: #111827;
            line-height: 1.1;
            margin-bottom: 0.4rem;
        }
        .home-subtitle {
            font-size: 1.1rem;
            color: #111827;
            line-height: 1.7;
            margin-bottom: 1.8rem;
        }
        .app-card {
            border: 1px solid rgba(0,0,0,0.10);
            border-radius: 20px;
            padding: 1.25rem 1.25rem 1.05rem 1.25rem;
            background: #ffffff;
            box-shadow: 0 10px 30px rgba(0,0,0,0.08);
            min-height: 150px;
            margin-bottom: 0.8rem;
        }
        .app-card-title {
            font-size: 1.45rem;
            font-weight: 850;
            color: #111827;
            margin-bottom: 0.55rem;
        }
        .app-card-desc {
            font-size: 0.98rem;
            color: #111827;
            line-height: 1.65;
            margin-bottom: 0.8rem;
        }
        @media (max-width: 700px) {
            .home-title { font-size: 2.15rem; }
            .home-subtitle { font-size: 1.0rem; }
        }
        </style>
        <div class="home-wrap">
            <div class="home-title">相場★大好きマン★アプリ</div>
            <div class="home-subtitle">
                使いたいアプリを選んでください。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.markdown(
            """
            <div class="app-card">
                <div class="app-card-title">法定開示情報チェッカー</div>
                <div class="app-card-desc">
                    個別株投資の参考になる法定開示情報の要約レポートを読むことができます。
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("法定開示情報チェッカーを開く", type="primary", use_container_width=True):
            go(APP_EDINET)

    with col2:
        st.markdown(
            """
            <div class="app-card">
                <div class="app-card-title">海外で話題の日本のニュース</div>
                <div class="app-card-desc">
                    海外ニュースサイトを自動周回し、日本関係のニュースをランキング化します。
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("海外で話題の日本のニュースを開く", type="primary", use_container_width=True):
            go(APP_NEWS)


def main() -> None:
    init_state()
    render_global_sidebar()

    active_app = st.session_state.get("active_app", APP_HOME)

    if active_app == APP_NEWS:
        render_top_nav("海外で話題の日本のニュース")
        render_news_ranking_page()
    elif active_app == APP_EDINET:
        render_top_nav("法定開示情報チェッカー")
        render_edinet_checker_page()
    else:
        render_home()


if __name__ == "__main__":
    main()
