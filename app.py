from pathlib import Path
import json

import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"

METADATA_FILE = DATA_DIR / "edinet_priority_sections_latest_metadata.json"
FULL_CSV_GZ_FILE = DATA_DIR / "edinet_priority_sections_latest_full.csv.gz"
DOC_LIST_CSV_FILE = DATA_DIR / "edinet_document_list_latest.csv"


st.set_page_config(
    page_title="開示情報チェッカー",
    page_icon="📄",
    layout="centered",
)


CUSTOM_CSS = """
<style>
.block-container {
    max-width: 820px;
    padding-top: 3rem;
    padding-bottom: 3rem;
}
.main-title {
    font-size: 2.4rem;
    font-weight: 800;
    letter-spacing: 0.02em;
    margin-bottom: 0.2rem;
}
.subtitle {
    color: #666;
    margin-bottom: 2rem;
}
.info-card {
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    padding: 1.2rem 1.4rem;
    background: #ffffff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    margin-bottom: 1rem;
}
.small-note {
    color: #777;
    font-size: 0.9rem;
}
</style>
"""


def load_metadata() -> dict:
    if not METADATA_FILE.exists():
        return {}
    try:
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def file_size_label(path: Path) -> str:
    if not path.exists():
        return "ファイルなし"
    size = path.stat().st_size
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def read_doc_count_from_csv() -> int | None:
    if not DOC_LIST_CSV_FILE.exists():
        return None
    try:
        # 文書一覧CSVは抽出本文CSVより小さい想定。
        df = pd.read_csv(DOC_LIST_CSV_FILE, dtype=str)
        if "doc_id" in df.columns:
            non_empty = df["doc_id"].dropna().astype(str).str.strip()
            return int((non_empty != "").sum())
        return int(len(df))
    except Exception:
        return None


def download_button_for_file(label: str, path: Path, mime: str) -> None:
    if not path.exists():
        st.warning(f"{label} はまだ作成されていません。")
        return
    st.download_button(
        label=f"{label}（{file_size_label(path)}）",
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        use_container_width=True,
    )


def main() -> None:
    metadata = load_metadata()

    start_date = metadata.get("start_date", "不明")
    end_date = metadata.get("end_date", "不明")
    generated_at = (
        metadata.get("generated_at_jst")
        or metadata.get("created_at_jst")
        or metadata.get("generated_at_utc")
        or metadata.get("created_at_utc")
        or "不明"
    )

    document_count = metadata.get("document_count")
    if document_count is None:
        document_count = read_doc_count_from_csv()
    if document_count is None:
        document_count = "不明"

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    st.markdown('<div class="main-title">開示情報チェッカー</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">EDINET公開文書の優先抽出CSVをダウンロードできます。</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="info-card">', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    col1.metric("対象期間", f"{start_date} 〜 {end_date}")
    col2.metric("対象文書数", document_count)
    st.markdown(f'<div class="small-note">最終作成日時: {generated_at}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.subheader("CSVダウンロード")
    download_button_for_file(
        "全文版CSVをダウンロード",
        FULL_CSV_GZ_FILE,
        "application/gzip",
    )
    download_button_for_file(
        "文書一覧CSVをダウンロード",
        DOC_LIST_CSV_FILE,
        "text/csv",
    )

    if not metadata:
        st.info("メタデータがまだありません。GitHub ActionsでCSV生成を実行してください。")


if __name__ == "__main__":
    main()
