from __future__ import annotations

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


def get_secret(path: str, default: str = "") -> str:
    """Read nested Streamlit secrets using dot notation, e.g. github.owner."""
    cur: Any = st.secrets
    try:
        for part in path.split("."):
            cur = cur[part]
        return str(cur).strip()
    except Exception:
        return default


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
    st.title("開示情報チェッカー")

    metadata = read_metadata()

    period = format_period(metadata)
    document_count = metadata.get("document_count")

    col1, col2 = st.columns(2)

    # st.metricだと対象期間が大きすぎるため、対象期間だけ小さめに表示します。
    col1.markdown("**対象期間**")
    col1.markdown(
        f"""
        <div style="font-size: 0.9rem; font-weight: 600; line-height: 1.3;">
            {period}
        </div>
        """,
        unsafe_allow_html=True,
    )

    col2.metric("対象文書数", format_count(document_count))

    st.divider()

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
            本データは、EDINET閲覧（提出）サイトで公開された開示情報をもとに、抽出および加工したものです。<br>
            本データは金融庁またはEDINETが作成および保証するものではありません。<br>
            正確な内容は必ずEDINET上の原文をご確認ください。<br>
            本データの二次配布は禁止いたします。著作権侵害に該当すると思われる方に対しては、法的措置を検討します。
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
