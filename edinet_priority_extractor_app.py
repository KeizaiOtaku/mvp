"""
EDINET Priority Section Extractor - Streamlit app

目的:
- EDINET API v2 から直近7日など任意期間の提出書類一覧を取得
- 各書類の CSV ZIP(type=5) を優先して取得
- 有報・半期報告・臨時報告・大量保有・訂正報告などから、投資家向けの優先抽出箇所を抽出
- 企業×書類×抽出セクションの縦持ちCSVとしてダウンロード

必要:
- EDINET APIキー
- pip install -r requirements.txt

起動:
- streamlit run edinet_priority_extractor_app.py
"""

from __future__ import annotations

import hashlib
import html
import io
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup


EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2"
JST = timezone(timedelta(hours=9))


# -----------------------------------------------------------------------------
# 抽出対象セクション定義
# まずは広めに拾う。CSVの「要素ID」「項目名」「値」のいずれかにマッチさせる。
# -----------------------------------------------------------------------------
DEFAULT_PRIORITY_PATTERNS: Dict[str, List[str]] = {
    "事業等のリスク": [
        r"BusinessRisks",
        r"RiskFactors",
        r"事業等のリスク",
        r"リスク要因",
    ],
    "経営方針・経営環境・対処すべき課題": [
        r"BusinessPolicy.*BusinessEnvironment.*IssuesToAddress",
        r"BusinessPolicyBusinessEnvironmentIssuesToAddress",
        r"経営方針",
        r"経営環境",
        r"対処すべき課題",
    ],
    "経営成績等の状況・MD&A": [
        r"ManagementAnalysis",
        r"AnalysisOfFinancialPosition",
        r"経営者による財政状態",
        r"経営成績及びキャッシュ.フロー",
        r"経営成績等の状況",
        r"MD&A",
    ],
    "設備投資等の概要": [
        r"CapitalExpenditures",
        r"設備投資",
    ],
    "研究開発活動": [
        r"ResearchAndDevelopment",
        r"研究開発活動",
        r"研究開発",
    ],
    "重要な後発事象": [
        r"SubsequentEvents",
        r"後発事象",
    ],
    "継続企業の前提": [
        r"GoingConcern",
        r"継続企業",
        r"継続企業の前提",
    ],
    "大株主の状況": [
        r"MajorShareholders",
        r"PrincipalShareholders",
        r"大株主",
    ],
    "配当政策": [
        r"DividendPolicy",
        r"配当政策",
    ],
    "サステナビリティ・人的資本": [
        r"Sustainability",
        r"HumanCapital",
        r"サステナビリティ",
        r"人的資本",
        r"多様性",
    ],
    "大量保有：保有目的": [
        r"PurposeOfHolding",
        r"目的.*保有",
        r"保有目的",
    ],
    "大量保有：保有割合・増減": [
        r"ShareholdingRatio",
        r"HoldingRatio",
        r"保有割合",
        r"株券等保有割合",
        r"増加.*減少",
    ],
    "臨時報告書：提出事由・発生事実": [
        r"ReasonForFiling",
        r"ReasonForSubmission",
        r"提出事由",
        r"提出理由",
        r"発生事実",
        r"異動",
        r"主要株主",
        r"親会社",
        r"子会社",
        r"訴訟",
        r"合併",
        r"会社分割",
        r"株式交換",
        r"株式移転",
        r"公開買付",
        r"M&A",
    ],
    "訂正報告書：訂正理由・訂正箇所": [
        r"ReasonForCorrection",
        r"Correction",
        r"訂正理由",
        r"訂正箇所",
        r"訂正の理由",
    ],
}

DEFAULT_DOC_KEYWORDS = [
    "有価証券報告書",
    "半期報告書",
    "四半期報告書",
    "臨時報告書",
    "大量保有報告書",
    "変更報告書",
    "訂正報告書",
]


@dataclass
class AppConfig:
    api_key: str
    start_date: date
    end_date: date
    doc_keywords: List[str]
    max_docs: int
    max_chars: int
    min_text_chars: int
    sleep_sec: float
    use_type1_fallback: bool
    include_no_match_rows: bool


# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------
def today_jst() -> date:
    return datetime.now(JST).date()


def daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def first_present(d: Dict[str, Any], keys: List[str], default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip() != "":
            return str(v)
    return default


def normalize_space(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t \u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    s = str(value)
    if not s or s.lower() == "nan":
        return ""

    # EDINETのCSV値にはHTMLがエスケープされていることがある
    s = html.unescape(s)
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</\s*p\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</\s*div\s*>", "\n", s, flags=re.IGNORECASE)

    if re.search(r"<[^>]+>", s):
        soup = BeautifulSoup(s, "html.parser")
        s = soup.get_text("\n")

    return normalize_space(s)


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def decode_bytes(raw: bytes) -> str:
    for enc in ["utf-8-sig", "utf-16", "cp932", "shift_jis", "utf-8"]:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def find_col(columns: Iterable[str], candidates: List[str]) -> Optional[str]:
    cols = list(columns)
    lower_map = {str(c).lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    for c in cols:
        cl = str(c).lower()
        if any(cand.lower() in cl for cand in candidates):
            return c
    return None


def make_match_regex(patterns: List[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), flags=re.IGNORECASE)


COMPILED_PATTERNS = {
    section: make_match_regex(patterns) for section, patterns in DEFAULT_PRIORITY_PATTERNS.items()
}


# -----------------------------------------------------------------------------
# EDINET API
# -----------------------------------------------------------------------------
def resolve_api_key_from_secrets() -> str:
    # 1) 環境変数
    for env_name in ["EDINET_API_KEY", "SUBSCRIPTION_KEY"]:
        val = os.getenv(env_name, "").strip()
        if val:
            return val

    # 2) Streamlit secrets
    try:
        if "EDINET_API_KEY" in st.secrets:
            return str(st.secrets["EDINET_API_KEY"]).strip()
    except Exception:
        pass
    try:
        if "edinet" in st.secrets and "api_key" in st.secrets["edinet"]:
            return str(st.secrets["edinet"]["api_key"]).strip()
    except Exception:
        pass
    return ""


def edinet_get_json(endpoint: str, params: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    url = f"{EDINET_BASE}/{endpoint.lstrip('/')}"
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    try:
        return r.json()
    except Exception as exc:
        raise RuntimeError(f"JSONとして読めませんでした: {url} / {exc}") from exc


def edinet_get_binary(endpoint: str, params: Dict[str, Any], timeout: int = 120) -> bytes:
    url = f"{EDINET_BASE}/{endpoint.lstrip('/')}"
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    content_type = r.headers.get("Content-Type", "")
    if "application/json" in content_type.lower():
        try:
            j = r.json()
        except Exception:
            j = {"message": r.text[:500]}
        raise RuntimeError(f"EDINET API returned JSON instead of file: {j}")
    return r.content


@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_document_list_one_day(date_str: str, api_key: str) -> List[Dict[str, Any]]:
    data = edinet_get_json(
        "documents.json",
        {"date": date_str, "type": 2, "Subscription-Key": api_key},
    )
    meta = data.get("metadata", {}) or {}
    status = str(meta.get("status", "200"))
    if status != "200":
        raise RuntimeError(f"EDINET list API status={status}, message={meta.get('message')}")
    results = data.get("results") or []
    return results


def fetch_document_lists(config: AppConfig) -> pd.DataFrame:
    all_rows: List[Dict[str, Any]] = []
    for d in daterange(config.start_date, config.end_date):
        ds = d.isoformat()
        rows = fetch_document_list_one_day(ds, config.api_key)
        for row in rows:
            row2 = dict(row)
            row2["file_date"] = ds
            all_rows.append(row2)
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


def normalize_doc_row(doc: Dict[str, Any]) -> Dict[str, str]:
    return {
        "file_date": first_present(doc, ["file_date", "date"]),
        "submit_datetime": first_present(doc, ["submitDateTime", "submit_datetime"]),
        "doc_id": first_present(doc, ["docID", "docId", "doc_id"]),
        "edinet_code": first_present(doc, ["edinetCode", "edinet_code"]),
        "sec_code": first_present(doc, ["secCode", "sec_code"]),
        "jcn": first_present(doc, ["JCN", "jcn"]),
        "filer_name": first_present(doc, ["filerName", "filer_name"]),
        "fund_code": first_present(doc, ["fundCode", "fund_code"]),
        "ordinance_code": first_present(doc, ["ordinanceCode", "ordinance_code"]),
        "form_code": first_present(doc, ["formCode", "form_code"]),
        "doc_type_code": first_present(doc, ["docTypeCode", "doc_type_code"]),
        "doc_description": first_present(doc, ["docDescription", "doc_description"]),
        "period_start": first_present(doc, ["periodStart", "period_start"]),
        "period_end": first_present(doc, ["periodEnd", "period_end"]),
        "xbrl_flag": first_present(doc, ["xbrlFlag", "xbrl_flag"], "0"),
        "pdf_flag": first_present(doc, ["pdfFlag", "pdf_flag"], "0"),
        "csv_flag": first_present(doc, ["csvFlag", "csv_flag"], "0"),
        "legal_status": first_present(doc, ["legalStatus", "legal_status"]),
        "withdrawal_status": first_present(doc, ["withdrawalStatus", "withdrawal_status"]),
        "doc_info_edit_status": first_present(doc, ["docInfoEditStatus", "doc_info_edit_status"]),
        "disclosure_status": first_present(doc, ["disclosureStatus", "disclosure_status"]),
    }


def filter_documents(df: pd.DataFrame, keywords: List[str]) -> pd.DataFrame:
    if df.empty:
        return df
    if not keywords:
        return df
    desc = df.get("docDescription", pd.Series([""] * len(df))).fillna("").astype(str)
    pattern = "|".join(re.escape(k) for k in keywords if k.strip())
    if not pattern:
        return df
    return df[desc.str.contains(pattern, regex=True, na=False)].copy()


# -----------------------------------------------------------------------------
# CSV ZIP / TYPE=1 ZIP extraction
# -----------------------------------------------------------------------------
def read_csv_from_zip_member(zf: zipfile.ZipFile, name: str) -> Optional[pd.DataFrame]:
    raw = zf.read(name)
    for enc in ["utf-16", "utf-8-sig", "cp932", "shift_jis", "utf-8"]:
        try:
            return pd.read_csv(
                io.BytesIO(raw),
                sep="\t",
                encoding=enc,
                dtype=str,
                engine="python",
                on_bad_lines="skip",
            )
        except Exception:
            continue
    return None


def extract_priority_from_csv_zip(
    zip_bytes: bytes,
    doc_meta: Dict[str, str],
    max_chars: int,
    min_text_chars: int,
) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    seen = set()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        # XBRL_TO_CSVを優先。なければ全CSVを見る。
        preferred = [n for n in csv_names if "xbrl_to_csv" in n.lower()]
        if preferred:
            csv_names = preferred

        for name in csv_names:
            df = read_csv_from_zip_member(zf, name)
            if df is None or df.empty:
                continue

            element_col = find_col(df.columns, ["要素ID", "element id", "element_id", "elementid"])
            item_col = find_col(df.columns, ["項目名", "日本語ラベル", "label", "科目名", "name"])
            context_col = find_col(df.columns, ["コンテキストID", "context id", "context_id", "contextid"])
            value_col = find_col(df.columns, ["値", "value"])
            unit_col = find_col(df.columns, ["単位ID", "ユニットID", "unit"])

            if value_col is None:
                continue

            # テキストブロックや長めの値だけを対象にする
            for _, row in df.iterrows():
                raw_value = row.get(value_col, "")
                cleaned = clean_text(raw_value)
                if len(cleaned) < min_text_chars:
                    continue

                element_id = str(row.get(element_col, "") if element_col else "")
                item_name = str(row.get(item_col, "") if item_col else "")
                context_id = str(row.get(context_col, "") if context_col else "")
                unit_id = str(row.get(unit_col, "") if unit_col else "")

                haystack = " ".join([element_id, item_name, cleaned[:1500]])

                for section_name, regex in COMPILED_PATTERNS.items():
                    if regex.search(haystack):
                        key = (section_name, element_id, item_name, context_id, text_hash(cleaned))
                        if key in seen:
                            continue
                        seen.add(key)
                        extracted.append(
                            {
                                **doc_meta,
                                "priority_section": section_name,
                                "matched_element_id": element_id,
                                "matched_item_name": item_name,
                                "context_id": context_id,
                                "unit_id": unit_id,
                                "source_file": name,
                                "source_type": "csv_zip_type5",
                                "text_length": len(cleaned),
                                "extracted_text": truncate_text(cleaned, max_chars),
                                "error": "",
                            }
                        )
    return extracted


def extract_window_around_keyword(text: str, match_start: int, max_chars: int) -> str:
    if max_chars <= 0:
        # 0は全文扱い。ただし巨大ファイル事故防止で見出し周辺10万字に制限
        max_chars = 100_000
    pre = min(300, match_start)
    start = max(0, match_start - pre)
    end = min(len(text), match_start + max_chars)
    return text[start:end]


def extract_priority_from_type1_zip(
    zip_bytes: bytes,
    doc_meta: Dict[str, str],
    max_chars: int,
    min_text_chars: int,
) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    seen = set()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.lower().endswith((".htm", ".html", ".xhtml", ".xbrl", ".xml"))
            and not n.endswith("/")
        ]
        # 大きすぎる添付ファイルを避けるためPublicDocを優先
        preferred = [n for n in names if "publicdoc" in n.lower()]
        if preferred:
            names = preferred

        for name in names:
            try:
                raw = zf.read(name)
            except Exception:
                continue
            if len(raw) > 25_000_000:
                continue
            text = clean_text(decode_bytes(raw))
            if len(text) < min_text_chars:
                continue
            for section_name, regex in COMPILED_PATTERNS.items():
                m = regex.search(text)
                if not m:
                    continue
                snippet = extract_window_around_keyword(text, m.start(), max_chars)
                key = (section_name, name, text_hash(snippet))
                if key in seen:
                    continue
                seen.add(key)
                extracted.append(
                    {
                        **doc_meta,
                        "priority_section": section_name,
                        "matched_element_id": "",
                        "matched_item_name": "keyword_window",
                        "context_id": "",
                        "unit_id": "",
                        "source_file": name,
                        "source_type": "zip_type1_keyword_fallback",
                        "text_length": len(snippet),
                        "extracted_text": truncate_text(snippet, max_chars),
                        "error": "",
                    }
                )
    return extracted


def download_and_extract_one_doc(config: AppConfig, raw_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    doc_meta = normalize_doc_row(raw_doc)
    doc_id = doc_meta["doc_id"]
    if not doc_id:
        return [{**doc_meta, "priority_section": "", "extracted_text": "", "error": "docIDなし"}]

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    # 1) CSV ZIP type=5 を優先。XBRL_TO_CSVが取れる場合、テキストブロック抽出が比較的安定する。
    if doc_meta.get("csv_flag") == "1":
        try:
            bin_data = edinet_get_binary(
                f"documents/{doc_id}",
                {"type": 5, "Subscription-Key": config.api_key},
            )
            rows = extract_priority_from_csv_zip(
                bin_data,
                doc_meta,
                max_chars=config.max_chars,
                min_text_chars=config.min_text_chars,
            )
        except Exception as exc:
            errors.append(f"type=5 CSV取得/解析失敗: {exc}")

    # 2) 取れなかった場合だけ type=1 ZIP のHTML/XBRLからキーワード周辺を拾う
    if not rows and config.use_type1_fallback:
        try:
            bin_data = edinet_get_binary(
                f"documents/{doc_id}",
                {"type": 1, "Subscription-Key": config.api_key},
            )
            rows = extract_priority_from_type1_zip(
                bin_data,
                doc_meta,
                max_chars=config.max_chars,
                min_text_chars=config.min_text_chars,
            )
        except Exception as exc:
            errors.append(f"type=1 ZIP取得/解析失敗: {exc}")

    if not rows and (config.include_no_match_rows or errors):
        rows = [
            {
                **doc_meta,
                "priority_section": "",
                "matched_element_id": "",
                "matched_item_name": "",
                "context_id": "",
                "unit_id": "",
                "source_file": "",
                "source_type": "",
                "text_length": 0,
                "extracted_text": "",
                "error": " / ".join(errors) if errors else "優先抽出箇所に一致なし",
            }
        ]
    return rows


def run_extraction(config: AppConfig, docs_df: pd.DataFrame) -> pd.DataFrame:
    docs = docs_df.to_dict("records")
    if config.max_docs and config.max_docs > 0:
        docs = docs[: config.max_docs]

    out_rows: List[Dict[str, Any]] = []
    progress = st.progress(0)
    status = st.empty()

    for i, doc in enumerate(docs, start=1):
        meta = normalize_doc_row(doc)
        status.write(
            f"処理中 {i}/{len(docs)}: {meta.get('filer_name')} / {meta.get('doc_description')} / {meta.get('doc_id')}"
        )
        try:
            out_rows.extend(download_and_extract_one_doc(config, doc))
        except Exception as exc:
            out_rows.append({**meta, "priority_section": "", "extracted_text": "", "error": str(exc)})
        progress.progress(i / max(len(docs), 1))
        if config.sleep_sec > 0:
            time.sleep(config.sleep_sec)

    status.write("処理完了")
    return pd.DataFrame(out_rows)


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="EDINET 優先抽出CSV", layout="wide")
    st.title("EDINET 直近公開文書 優先抽出CSV 出力アプリ")
    st.caption("EDINET API v2から書類を取得し、投資家向けに重要な本文セクションをCSV化します。AI要約ではなく、まずは原文抽出に特化しています。")

    with st.sidebar:
        st.header("設定")
        default_key = resolve_api_key_from_secrets()
        api_key = st.text_input(
            "EDINET APIキー",
            value=default_key,
            type="password",
            help="Streamlit Cloudでは .streamlit/secrets.toml に [edinet] api_key='...' と置けます。",
        )

        tdy = today_jst()
        start_date = st.date_input("開始日", value=tdy - timedelta(days=6))
        end_date = st.date_input("終了日", value=tdy)

        st.subheader("対象書類")
        selected_keywords = st.multiselect(
            "docDescription に含めるキーワード",
            options=DEFAULT_DOC_KEYWORDS,
            default=DEFAULT_DOC_KEYWORDS,
        )
        custom_keywords = st.text_input(
            "追加キーワード（カンマ区切り）",
            value="",
            placeholder="例: 有価証券届出書, 自己株券買付状況報告書",
        )
        if custom_keywords.strip():
            selected_keywords = selected_keywords + [x.strip() for x in custom_keywords.split(",") if x.strip()]

        st.subheader("処理制限")
        max_docs = st.number_input(
            "最大処理件数（0で無制限）",
            min_value=0,
            max_value=10000,
            value=300,
            step=50,
            help="直近7日でも文書数が多い場合があります。まず300件程度でテスト推奨。",
        )
        max_chars = st.number_input(
            "抽出テキストの最大文字数（0で切らない）",
            min_value=0,
            max_value=200000,
            value=8000,
            step=1000,
        )
        min_text_chars = st.number_input(
            "抽出対象にする最小文字数",
            min_value=0,
            max_value=10000,
            value=80,
            step=20,
        )
        sleep_sec = st.number_input(
            "APIアクセス間隔（秒）",
            min_value=0.0,
            max_value=10.0,
            value=0.2,
            step=0.1,
        )
        use_type1_fallback = st.checkbox("CSVが取れない/該当なしのとき type=1 ZIP からキーワード周辺を抽出", value=True)
        include_no_match_rows = st.checkbox("一致なし・エラーの書類もCSVに出す", value=True)

    st.info(
        "使い方: 1) APIキーを入力 → 2) 文書一覧取得 → 3) 必要なら対象書類を確認 → 4) 優先箇所抽出 → 5) CSVダウンロード。"
    )

    if not api_key:
        st.warning("EDINET APIキーを入力してください。")
        st.stop()
    if start_date > end_date:
        st.error("開始日は終了日以前にしてください。")
        st.stop()

    config = AppConfig(
        api_key=api_key,
        start_date=start_date,
        end_date=end_date,
        doc_keywords=selected_keywords,
        max_docs=int(max_docs),
        max_chars=int(max_chars),
        min_text_chars=int(min_text_chars),
        sleep_sec=float(sleep_sec),
        use_type1_fallback=use_type1_fallback,
        include_no_match_rows=include_no_match_rows,
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        fetch_clicked = st.button("文書一覧を取得", type="primary")
    with col2:
        st.write(f"対象期間: **{start_date} 〜 {end_date}** / JST基準")

    if fetch_clicked:
        with st.spinner("EDINETから書類一覧を取得中..."):
            try:
                raw_df = fetch_document_lists(config)
            except Exception as exc:
                st.error(f"書類一覧の取得に失敗しました: {exc}")
                st.stop()
        st.session_state["raw_docs_df"] = raw_df

    raw_df = st.session_state.get("raw_docs_df")
    if raw_df is None:
        st.stop()

    st.subheader("取得した文書一覧")
    if raw_df.empty:
        st.warning("対象期間の文書が見つかりませんでした。")
        st.stop()

    filtered_df = filter_documents(raw_df, selected_keywords)
    st.write(f"全件: **{len(raw_df):,}** / フィルタ後: **{len(filtered_df):,}**")

    preview_cols = [
        c
        for c in [
            "file_date",
            "submitDateTime",
            "docID",
            "filerName",
            "secCode",
            "docDescription",
            "formCode",
            "docTypeCode",
            "xbrlFlag",
            "csvFlag",
            "pdfFlag",
        ]
        if c in filtered_df.columns
    ]
    st.dataframe(filtered_df[preview_cols].head(300), use_container_width=True, height=320)

    run_clicked = st.button("優先抽出箇所を抽出してCSVを作成", type="primary")
    if run_clicked:
        if filtered_df.empty:
            st.warning("抽出対象の文書がありません。")
            st.stop()
        with st.spinner("文書をダウンロードし、優先抽出箇所を抽出中..."):
            result_df = run_extraction(config, filtered_df)
        st.session_state["result_df"] = result_df

    result_df = st.session_state.get("result_df")
    if result_df is not None:
        st.subheader("抽出結果")
        st.write(f"出力行数: **{len(result_df):,}**")
        if not result_df.empty and "priority_section" in result_df.columns:
            sec_count = result_df["priority_section"].replace("", "一致なし/エラー").value_counts().reset_index()
            sec_count.columns = ["priority_section", "rows"]
            st.dataframe(sec_count, use_container_width=True)

        display_cols = [
            c
            for c in [
                "file_date",
                "submit_datetime",
                "filer_name",
                "sec_code",
                "doc_description",
                "doc_id",
                "priority_section",
                "matched_item_name",
                "text_length",
                "error",
                "extracted_text",
            ]
            if c in result_df.columns
        ]
        st.dataframe(result_df[display_cols].head(200), use_container_width=True, height=420)

        csv_bytes = result_df.to_csv(index=False).encode("utf-8-sig")
        filename = f"edinet_priority_sections_{start_date}_{end_date}.csv"
        st.download_button(
            "CSVをダウンロード",
            data=csv_bytes,
            file_name=filename,
            mime="text/csv",
        )

        st.caption(
            "注: このCSVはEDINET原文からの機械抽出です。投資判断や公開記事に使う場合は、元文書リンク・docIDを併記し、重要箇所は原文確認してください。"
        )


if __name__ == "__main__":
    main()
