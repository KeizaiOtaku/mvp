from __future__ import annotations

import streamlit as st

from news_ranking_page import render_news_ranking_page
from edinet_checker_page import render_edinet_checker_page


st.set_page_config(
    page_title="相場大好きマンアプリ",
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
        # 初回表示は法定開示情報チェッカーをデフォルトにする。
        # 共通トップページへはサイドバー/上部ボタンから移動可能。
        st.session_state["active_app"] = APP_EDINET


def render_global_sidebar() -> None:
    st.sidebar.title("相場大好きマンアプリ")
    st.sidebar.caption("共通ページから各アプリへ切り替えられます。")

    if st.sidebar.button("トップページ", use_container_width=True):
        go(APP_HOME)
    if st.sidebar.button("海外ニュースランキング", use_container_width=True):
        go(APP_NEWS)
    if st.sidebar.button("法定開示情報チェッカー", use_container_width=True):
        go(APP_EDINET)


def render_top_nav(current_label: str | None = None) -> None:
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        if st.button("← 相場大好きマンアプリへ戻る", use_container_width=True):
            go(APP_HOME)
    with c2:
        if current_label != "海外ニュースランキング":
            if st.button("海外ニュースランキング", use_container_width=True):
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
            min-height: 210px;
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
        .app-card-meta {
            font-size: 0.84rem;
            color: #4b5563;
            line-height: 1.55;
        }
        @media (max-width: 700px) {
            .home-title { font-size: 2.15rem; }
            .home-subtitle { font-size: 1.0rem; }
        }
        </style>
        <div class="home-wrap">
            <div class="home-title">相場大好きマンアプリ</div>
            <div class="home-subtitle">
                使いたいアプリを選んでください。海外ニュースランキングと法定開示情報チェッカーを、同じページ内で切り替えて使えます。
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
                <div class="app-card-title">海外ニュースランキング</div>
                <div class="app-card-desc">
                    海外ニュースサイトを周回し、日本のニュースと思われる記事を独自アルゴリズムでランキング化します。
                </div>
                <div class="app-card-meta">
                    ・タイトル＋リンク付きランキング<br>
                    ・日本時間04:00の自動更新枠<br>
                    ・管理者のみスコア内訳を確認可能
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("海外ニュースランキングを開く", type="primary", use_container_width=True):
            go(APP_NEWS)

    with col2:
        st.markdown(
            """
            <div class="app-card">
                <div class="app-card-title">法定開示情報チェッカー</div>
                <div class="app-card-desc">
                    EDINET等の公開情報から抽出した重要箇所・要約レポート・CSVを確認できます。
                </div>
                <div class="app-card-meta">
                    ・要約PDFダウンロード<br>
                    ・全文CSV / 文書一覧CSV<br>
                    ・管理者のみGitHub Actions更新操作
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("法定開示情報チェッカーを開く", type="primary", use_container_width=True):
            go(APP_EDINET)


def main() -> None:
    init_state()
    render_global_sidebar()

    active_app = st.session_state.get("active_app", APP_HOME)

    if active_app == APP_NEWS:
        render_top_nav("海外ニュースランキング")
        render_news_ranking_page()
    elif active_app == APP_EDINET:
        render_top_nav("法定開示情報チェッカー")
        render_edinet_checker_page()
    else:
        render_home()


if __name__ == "__main__":
    main()
