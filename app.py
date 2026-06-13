"""
EDINET weekly priority section viewer with admin-triggered manual refresh.

通常のサイト訪問者には、GitHub Actionsが作成済みのCSVだけを表示します。
管理者はパスワード認証後、Streamlit画面からGitHub Actionsのworkflow_dispatchを呼び出して、
任意のタイミングでCSV更新ジョブを開始できます。

必要なStreamlit Secrets:

[admin]
password = "管理者パスワード"

[github]
owner = "GitHubユーザー名またはOrganization名"
repo = "リポジトリ名"
branch = "main"
workflow_file = "weekly_edinet_extract.yml"
token = "GitHub fine-grained PAT。Actions: Read and write 権限を付与"
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

DATA_DIR = Path("data")
LATEST_CSV = DATA_DIR / "edinet_priority_sections_latest.csv"
LATEST_META = DATA_DIR / "edinet_priority_sections_latest_metadata.json"


# -----------------------------
# Data loading
# -----------------------------

def load_metadata() -> dict:
    if not LATEST_META.exists():
        return {}
    try:
        return json.loads(LATEST_META.read_text(encoding="utf-8"))
    except Exception:
        return {}

@st.cache_data(ttl=60, show_spinner=False)
def load_latest_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def load_csv(path: str):
    return pd.read_csv(path)


# -----------------------------
# Secrets helpers
# -----------------------------

def get_secret(section: str, key: str, default: str = "") -> str:
    try:
        if section in st.secrets and key in st.secrets[section]:
            return str(st.secrets[section][key]).strip()
    except Exception:
        pass
    return default


def admin_password_configured() -> bool:
    return bool(get_secret("admin", "password"))


def github_config() -> Dict[str, str]:
    return {
        "owner": get_secret("github", "owner"),
        "repo": get_secret("github", "repo"),
        "branch": get_secret("github", "branch", "main") or "main",
        "workflow_file": get_secret("github", "workflow_file", "weekly_edinet_extract.yml") or "weekly_edinet_extract.yml",
        "token": get_secret("github", "token"),
    }


def github_config_missing(config: Dict[str, str]) -> list[str]:
    required = ["owner", "repo", "branch", "workflow_file", "token"]
    return [k for k in required if not config.get(k)]


# -----------------------------
# GitHub Actions API
# -----------------------------

def dispatch_workflow(
    *,
    owner: str,
    repo: str,
    workflow_file: str,
    token: str,
    branch: str,
    inputs: Dict[str, str],
) -> Tuple[bool, str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"ref": branch, "inputs": inputs}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        return False, f"GitHub APIへの接続に失敗しました: {exc}"

    if response.status_code == 204:
        return True, "更新ジョブを開始しました。数分後にCSVがGitHubへコミットされ、Streamlitに反映されます。"

    try:
        detail: Any = response.json()
    except Exception:
        detail = response.text
    return False, f"GitHub Actionsの起動に失敗しました。status={response.status_code}, detail={detail}"


def fetch_latest_workflow_runs(
    *,
    owner: str,
    repo: str,
    workflow_file: str,
    token: str,
    limit: int = 5,
) -> Tuple[bool, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"per_page": limit}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        return False, f"GitHub APIへの接続に失敗しました: {exc}"

    if response.ok:
        return True, response.json().get("workflow_runs", [])

    try:
        detail: Any = response.json()
    except Exception:
        detail = response.text
    return False, f"workflow runの取得に失敗しました。status={response.status_code}, detail={detail}"


# -----------------------------
# UI
# -----------------------------

def render_admin_panel() -> None:
    st.sidebar.divider()
    with st.sidebar.expander("管理者メニュー", expanded=False):
        if not admin_password_configured():
            st.warning("Streamlit Secretsに [admin] password が設定されていません。")
            st.code('[admin]\npassword = "管理者パスワード"', language="toml")
            return

        password = st.text_input("管理者パスワード", type="password")
        if password != get_secret("admin", "password"):
            if password:
                st.error("パスワードが違います。")
            return

        st.success("管理者として認証済み")

        config = github_config()
        missing = github_config_missing(config)
        if missing:
            st.error("GitHub連携用Secretsが不足しています: " + ", ".join(missing))
            st.code(
                '[github]\n'
                'owner = "GitHubユーザー名またはOrganization名"\n'
                'repo = "リポジトリ名"\n'
                'branch = "main"\n'
                'workflow_file = "weekly_edinet_extract.yml"\n'
                'token = "GitHub fine-grained PAT"',
                language="toml",
            )
            return

        st.caption("このボタンはEDINET取得をStreamlit上で実行せず、GitHub Actionsの更新ジョブだけを開始します。")

        days = st.number_input("取得対象日数", min_value=1, max_value=31, value=7, step=1)
        end_offset_days = st.number_input("終了日オフセット", min_value=0, max_value=14, value=1, step=1, help="1ならJST昨日まで。0ならJST今日まで。")
        max_docs = st.number_input("最大処理件数", min_value=0, max_value=100000, value=0, step=100, help="0で無制限。テスト時は100などを推奨。")
        max_chars = st.number_input("抽出テキスト最大文字数", min_value=0, max_value=100000, value=8000, step=1000, help="0で無制限。")
        sleep_sec = st.number_input("APIアクセス間隔 秒", min_value=0.0, max_value=5.0, value=0.2, step=0.1)

        if st.button("今すぐCSV更新ジョブを開始", type="primary"):
            inputs = {
                "days": str(int(days)),
                "end_offset_days": str(int(end_offset_days)),
                "max_docs": str(int(max_docs)),
                "max_chars": str(int(max_chars)),
                "sleep_sec": str(float(sleep_sec)),
            }
            ok, message = dispatch_workflow(
                owner=config["owner"],
                repo=config["repo"],
                workflow_file=config["workflow_file"],
                token=config["token"],
                branch=config["branch"],
                inputs=inputs,
            )
            if ok:
                st.success(message)
                st.info("反映後に画面右上のRerun、またはブラウザ更新をしてください。")
            else:
                st.error(message)

        if st.button("最近の実行状況を確認"):
            ok, runs = fetch_latest_workflow_runs(
                owner=config["owner"],
                repo=config["repo"],
                workflow_file=config["workflow_file"],
                token=config["token"],
                limit=5,
            )
            if not ok:
                st.error(str(runs))
            else:
                if not runs:
                    st.info("実行履歴が見つかりません。")
                for run in runs:
                    title = f"{run.get('event', '')} / {run.get('status', '')} / {run.get('conclusion', '')}"
                    created_at = run.get("created_at", "")
                    html_url = run.get("html_url", "")
                    st.markdown(f"- [{title}]({html_url})  ")
                    st.caption(f"created_at: {created_at}")


def render_data_view() -> None:
    if not LATEST_CSV.exists():
        st.error("まだCSVが作成されていません。管理者メニューまたはGitHub Actionsから更新ジョブを実行してください。")
        st.code("Actions → Weekly EDINET priority CSV → Run workflow", language="text")
        st.stop()

    meta = load_metadata()
    df = load_latest_csv(str(LATEST_CSV))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("対象期間", f"{meta.get('start_date', '?')}〜{meta.get('end_date', '?')}")
    c2.metric("対象文書数", f"{int(meta.get('target_document_count', len(df))):,}" if str(meta.get('target_document_count', '')).isdigit() else f"{meta.get('target_document_count', len(df))}")
    c3.metric("出力行数", f"{len(df):,}")
    c4.metric("作成日時", meta.get("generated_at_jst", "不明"))

    with st.expander("抽出対象キーワード・実行条件", expanded=False):
        st.json({
            "keywords": meta.get("keywords", []),
            "max_docs": meta.get("max_docs"),
            "max_chars": meta.get("max_chars"),
            "raw_document_count": meta.get("raw_document_count"),
            "days": meta.get("days"),
        })

    st.subheader("セクション別件数")
    if "priority_section" in df.columns:
        section_count = df["priority_section"].replace("", "一致なし/エラー").value_counts().reset_index()
        section_count.columns = ["priority_section", "rows"]
        st.dataframe(section_count, use_container_width=True, height=260)

    st.subheader("抽出結果")
    filtered = df.copy()

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if "priority_section" in filtered.columns:
            sections = [x for x in sorted(filtered["priority_section"].replace("", "一致なし/エラー").unique())]
            selected_sections = st.multiselect("セクション", options=sections, default=sections)
            normalized_section = filtered["priority_section"].replace("", "一致なし/エラー")
            filtered = filtered[normalized_section.isin(selected_sections)]
    with col2:
        if "doc_description" in filtered.columns:
            doc_types = [x for x in sorted(filtered["doc_description"].unique()) if x]
            selected_doc_types = st.multiselect("書類種別", options=doc_types, default=[])
            if selected_doc_types:
                filtered = filtered[filtered["doc_description"].isin(selected_doc_types)]
    with col3:
        q = st.text_input("企業名・証券コード・本文検索", value="")
        if q.strip():
            hay_cols = [c for c in ["filer_name", "sec_code", "doc_description", "priority_section", "extracted_text"] if c in filtered.columns]
            hay = filtered[hay_cols].astype(str).agg(" ".join, axis=1)
            filtered = filtered[hay.str.contains(q.strip(), case=False, regex=False, na=False)]

    st.write(f"表示行数: **{len(filtered):,}** / 全体: **{len(df):,}**")
    display_cols = [
        c
        for c in [
            "file_date", "submit_datetime", "filer_name", "sec_code", "doc_description", "doc_id",
            "priority_section", "matched_item_name", "text_length", "error", "extracted_text",
        ]
        if c in filtered.columns
    ]
    st.dataframe(filtered[display_cols], use_container_width=True, height=560)

    st.download_button(
        "最新CSVをダウンロード",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="edinet_priority_sections_latest.csv",
        mime="text/csv",
    )


def main() -> None:
    st.set_page_config(page_title="EDINET週次 優先抽出CSV", layout="wide")
    st.title("EDINET 週次公開文書 優先抽出CSV")
    st.caption("週1回の自動更新に加えて、管理者だけが任意のタイミングで更新ジョブを開始できます。")

    render_admin_panel()
    render_data_view()

    st.caption("注: このCSVはEDINET原文からの機械抽出です。投資判断や公開記事に使う場合は、元文書のdocIDを併記し、重要箇所は原文確認してください。")


if __name__ == "__main__":
    main()
