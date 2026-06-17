from __future__ import annotations

import streamlit as st

from news_ranking_page import render_news_ranking_page
from edinet_checker_page import render_edinet_checker_page


st.set_page_config(
    page_title="相場★大好きマン★アプリ",
    page_icon="📰",
    layout="wide",
)


def main() -> None:
    st.sidebar.title("相場★大好きマン★アプリ")
    page = st.sidebar.radio(
        "ページ選択",
        [
            "海外ニュースランキング",
            "法定開示情報チェッカー",
        ],
        index=0,
    )

    if page == "海外ニュースランキング":
        render_news_ranking_page()
    else:
        render_edinet_checker_page()


if __name__ == "__main__":
    main()
