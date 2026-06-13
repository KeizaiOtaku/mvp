from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"

METADATA_PATH = DATA_DIR / "edinet_priority_sections_latest_metadata.json"
FULL_CSV_GZ_PATH = DATA_DIR / "edinet_priority_sections_latest_full.csv.gz"
DOC_LIST_CSV_PATH = DATA_DIR / "edinet_document_list_latest.csv"
SUMMARY_PDF_CANDIDATES = [
    DATA_DIR / "kaiji_summary.pdf",
    DATA_DIR / "summary.pdf",
    DATA_DIR / "edinet_summary.pdf",
]
BRAND_IMAGE_PATH = DATA_DIR / "brand_cat.png"


st.set_page_config(
    page_title="開示情報チェッカー",
    page_icon="📄",
    layout="centered",
)


# -----------------------------
# Helpers
# -----------------------------
def read_metadata() -> Dict[str, Any]:
    if not METADATA_PATH.exists():
        return {}
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def format_period(metadata: Dict[str, Any]) -> str:
    start_date = metadata.get("start_date") or metadata.get("from_date") or "不明"
    end_date = metadata.get("end_date") or metadata.get("to_date") or "不明"
    return f"{start_date} 〜 {end_date}"


def format_count(value: Any) -> str:
    if value is None or value == "":
        return "不明"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def file_size_label(path: Path) -> str:
    if not path.exists():
        return "ファイルなし"
    size = path.stat().st_size
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def find_summary_pdf() -> Optional[Path]:
    for path in SUMMARY_PDF_CANDIDATES:
        if path.exists():
            return path
    return None


def download_button_for_file(label: str, path: Path, mime: str) -> None:
    if not path.exists():
        st.button(label, disabled=True, help=f"{path.name} がまだ生成されていません。")
        st.caption(f"未生成: `{path}`")
        return

    st.download_button(
        label=label,
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        use_container_width=True,
    )
    st.caption(f"{path.name} / {file_size_label(path)}")


def image_to_data_uri(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None


def render_title_header() -> None:
    """Render the brand image near the main title instead of the far screen corner."""
    data_uri = image_to_data_uri(BRAND_IMAGE_PATH)
    image_html = ""
    if data_uri:
        image_html = (
            f'<img class="title-brand-image" src="{data_uri}" '
            f'alt="相場大好きマン アプリ画像">'
        )

    st.markdown(
        f"""
        <style>
        .title-header-wrap {{
            display: flex;
            align-items: center;
            gap: 1.05rem;
            margin: 0.4rem 0 1.35rem 0;
        }}
        .title-brand-image {{
            width: 96px;
            height: 96px;
            object-fit: cover;
            border-radius: 16px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.18);
            flex-shrink: 0;
        }}
        .title-text-block {{
            min-width: 0;
        }}
        .site-brand-text {{
            font-size: 1.25rem;
            font-weight: 700;
            color: #555;
            margin-bottom: 0.15rem;
        }}
        .site-main-title {{
            font-size: 2.45rem;
            font-weight: 800;
            color: #111827;
            line-height: 1.12;
            margin: 0;
        }}
        .site-subtitle {{
            font-size: 1.25rem;
            font-weight: 600;
            color: #444;
            line-height: 1.35;
            margin-top: 0.4rem;
        }}
        @media (max-width: 700px) {{
            .title-header-wrap {{
                gap: 0.75rem;
                align-items: flex-start;
            }}
            .title-brand-image {{
                width: 72px;
                height: 72px;
                border-radius: 12px;
            }}
            .site-brand-text {{
                font-size: 1.0rem;
            }}
            .site-main-title {{
                font-size: 1.9rem;
            }}
            .site-subtitle {{
                font-size: 1.0rem;
            }}
        }}
        </style>
        <div class="title-header-wrap">
            {image_html}
            <div class="title-text-block">
                <div class="site-brand-text">相場★大好きマン★アプリ</div>
                <div class="site-main-title">開示情報チェッカー</div>
                <div class="site-subtitle">数十万ページの日本企業の公開情報を毎週要約</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_secret(path: str, default: str = "") -> str:
    """Read nested Streamlit secrets using dot notation, e.g. github.owner."""
    cur: Any = st.secrets
    try:
        for part in path.split("."):
            cur = cur[part]
        return str(cur).strip()
    except Exception:
        return default


def render_right_links() -> None:
    """Render configurable social links in the right-side margin.

    Set URLs in Streamlit Secrets:

    [links]
    note = "https://note.com/..."
    x = "https://x.com/..."
    blogger = "https://...blogspot.com/"
    """
    links = [
        ("note", "note", get_secret("links.note")),
        ("X", "X", get_secret("links.x")),
        ("Blogger", "Blogger", get_secret("links.blogger")),
    ]
    active_links = [(label, css_class, url) for label, css_class, url in links if url]
    if not active_links:
        return

    items_html = ""
    for label, css_class, url in active_links:
        safe_label = html.escape(label)
        safe_url = html.escape(url, quote=True)
        safe_class = html.escape(css_class.lower())
        items_html += (
            f'<a class="right-social-link right-social-link-{safe_class}" '
            f'href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_label}</a>'
        )

    st.markdown(
        f"""
        <style>
        .right-social-box {{
            position: fixed;
            top: 7.5rem;
            right: 1.0rem;
            width: 136px;
            padding: 0.75rem 0.7rem;
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.92);
            border: 1px solid rgba(0, 0, 0, 0.08);
            box-shadow: 0 8px 24px rgba(0,0,0,0.12);
            z-index: 9998;
        }}
        .right-social-title {{
            font-size: 0.78rem;
            font-weight: 700;
            color: #555;
            margin-bottom: 0.5rem;
            text-align: center;
        }}
        .right-social-link {{
            display: block;
            text-align: center;
            text-decoration: none !important;
            font-weight: 700;
            font-size: 0.92rem;
            color: #333 !important;
            padding: 0.46rem 0.5rem;
            margin: 0.36rem 0;
            border-radius: 999px;
            background: #f4f4f5;
            border: 1px solid rgba(0,0,0,0.07);
        }}
        .right-social-link:hover {{
            background: #e9ecef;
            transform: translateY(-1px);
        }}
        @media (max-width: 1100px) {{
            .right-social-box {{
                position: static;
                width: auto;
                margin: 0.5rem 0 1.2rem 0;
                box-shadow: none;
            }}
            .right-social-title {{
                text-align: left;
            }}
            .right-social-link {{
                display: inline-block;
                min-width: 88px;
                margin-right: 0.35rem;
            }}
        }}
        </style>
        <div class="right-social-box">
            <div class="right-social-title">リンク</div>
            {items_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def dispatch_github_workflow(
    *,
    owner: str,
    repo: str,
    branch: str,
    workflow_file: str,
    token: str,
    days: int,
    end_offset_days: int,
    max_docs: int,
    max_chars: int,
    sleep_sec: str,
) -> Optional[str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": branch,
        "inputs": {
            "days": str(days),
            "end_offset_days": str(end_offset_days),
            "max_docs": str(max_docs),
            "max_chars": str(max_chars),
            "sleep_sec": str(sleep_sec),
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code == 204:
        return None

    try:
        detail = response.json()
    except Exception:
        detail = response.text
    return f"status={response.status_code}, detail={detail}"


# -----------------------------
# Admin UI
# -----------------------------
def render_admin_panel() -> None:
    st.sidebar.divider()
    st.sidebar.subheader("管理者用")

    admin_password = get_secret("admin.password")
    if not admin_password:
        st.sidebar.warning("admin.password がStreamlit Secretsに設定されていません。")
        return

    input_password = st.sidebar.text_input("管理者パスワード", type="password")
    if input_password != admin_password:
        if input_password:
            st.sidebar.error("パスワードが違います。")
        return

    st.sidebar.success("管理者モード")

    owner = get_secret("github.owner")
    repo = get_secret("github.repo")
    branch = get_secret("github.branch", "main")
    workflow_file = get_secret("github.workflow_file", "weekly_edinet_extract.yml")
    token = get_secret("github.token")

    missing = []
    for name, value in {
        "github.owner": owner,
        "github.repo": repo,
        "github.branch": branch,
        "github.workflow_file": workflow_file,
        "github.token": token,
    }.items():
        if not value:
            missing.append(name)

    if missing:
        st.sidebar.error("GitHub連携設定が不足しています: " + ", ".join(missing))
        return

    with st.sidebar.expander("更新条件", expanded=True):
        days = st.number_input("対象日数", min_value=1, max_value=31, value=7, step=1)
        end_offset_days = st.number_input(
            "終了日オフセット",
            min_value=0,
            max_value=7,
            value=1,
            step=1,
            help="1なら昨日まで、0なら今日まで。",
        )
        max_docs = st.number_input(
            "最大処理文書数",
            min_value=0,
            max_value=10000,
            value=20,
            step=10,
            help="0で全件。テスト時は20〜100推奨。",
        )
        max_chars = st.number_input(
            "抽出テキスト最大文字数",
            min_value=100,
            max_value=50000,
            value=2000,
            step=500,
        )
        sleep_sec = st.text_input("APIアクセス間隔 秒", value="0.2")

    if st.sidebar.button("今すぐ更新を開始", type="primary", use_container_width=True):
        error = dispatch_github_workflow(
            owner=owner,
            repo=repo,
            branch=branch,
            workflow_file=workflow_file,
            token=token,
            days=int(days),
            end_offset_days=int(end_offset_days),
            max_docs=int(max_docs),
            max_chars=int(max_chars),
            sleep_sec=sleep_sec,
        )
        if error is None:
            st.sidebar.success("GitHub Actionsを起動しました。数分後にGitHubのActions画面で結果を確認してください。")
        else:
            st.sidebar.error(f"GitHub Actionsの起動に失敗しました。{error}")

    if st.sidebar.button("表示を再読み込み", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# -----------------------------
# Public UI
# -----------------------------
def render_public_page() -> None:
    render_right_links()
    render_title_header()

    metadata = read_metadata()

    period = format_period(metadata)
    document_count = metadata.get("document_count")

    col1, col2 = st.columns(2)

    # st.metricだと対象期間が大きすぎるため、対象期間だけ小さめに表示します。
    col1.markdown("**対象期間**")
    col1.markdown(
        f"""
        <div style="font-size: 1.5rem; font-weight: 600; line-height: 1.3;">
            {period}
        </div>
        """,
        unsafe_allow_html=True,
    )

    col2.metric("対象文書数", format_count(document_count))

    st.divider()

    summary_pdf_path = find_summary_pdf()
    if summary_pdf_path is None:
        st.button(
            "要約レポートPDFのダウンロード(注意：手動アップロードのため、csvと日付が違う可能性あり)",
            disabled=True,
            help="管理者がPDFを data/kaiji_summary.pdf としてアップロードすると有効になります。",
            use_container_width=True,
        )
        st.caption("要約PDFはまだアップロードされていません。管理者は `data/kaiji_summary.pdf` としてPDFを配置してください。")
    else:
        download_button_for_file(
            "要約レポートPDFのダウンロード(注意：手動アップロードのため、csvと日付が違う可能性あり)",
            summary_pdf_path,
            "application/pdf",
        )

    download_button_for_file(
        "全文版CSVをダウンロード",
        FULL_CSV_GZ_PATH,
        "application/gzip",
    )
    download_button_for_file(
        "文書一覧CSVをダウンロード",
        DOC_LIST_CSV_PATH,
        "text/csv",
    )

    st.markdown(
        """
        <div style="font-size: 0.85rem; line-height: 1.7; color: #555; margin-top: 1.0rem;">
            CSVデータは、EDINET閲覧（提出）サイトで公開された開示情報をもとに、抽出および加工したものです。<br>
            CSVデータは金融庁またはEDINETが作成および保証するものではありません。<br>
            正確な内容は必ずEDINET上の原文をご確認ください。また、データの二次配布は禁止いたします。著作権侵害に該当すると思われる方に対しては、法的措置を検討します。<br>
            本サイトおよび本サイトでダウンロードできるデータ(CSVとPDFの全て)に、投資助言は一切含まれておりません。
        </div>
        """,
        unsafe_allow_html=True,
    )

    if metadata:
        generated = (
            metadata.get("generated_at_jst")
            or metadata.get("created_at_jst")
            or metadata.get("generated_at_utc")
            or metadata.get("created_at_utc")
            or "不明"
        )
        st.caption(f"最終更新: {generated}")
    else:
        st.caption("メタデータがまだ生成されていません。")


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    render_admin_panel()
    render_public_page()


if __name__ == "__main__":
    main()
