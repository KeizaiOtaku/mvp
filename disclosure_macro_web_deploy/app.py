"""
Disclosure x Macro MVP
======================
A Streamlit prototype for:
1) Japan EDINET filing search / risk-change / theme mentions
2) US SEC EDGAR filing checker / risk-change / insider filings / XBRL facts
3) Macro dashboard using public APIs such as BLS, World Bank, and U.S. Treasury Fiscal Data

Run:
    pip install -r requirements.txt
    streamlit run app.py

Notes:
- EDINET API v2 requires an API key. Configure it in Streamlit Secrets or environment variables.
- SEC requires a descriptive User-Agent with contact information.
- This is an MVP: extraction uses heuristics and should be validated before commercial use.
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_NAME = "開示×マクロAIサーチ MVP"
APP_VERSION = "0.1.0"

EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2"
SEC_DATA_BASE = "https://data.sec.gov"
SEC_WWW_BASE = "https://www.sec.gov"
BLS_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"
TREASURY_FISCAL_BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

DEFAULT_THEMES: Dict[str, List[str]] = {
    "AI": ["AI", "人工知能", "生成AI", "機械学習", "machine learning", "artificial intelligence", "generative AI"],
    "半導体": ["半導体", "semiconductor", "chip", "GPU", "foundry", "wafer"],
    "防衛": ["防衛", "防衛省", "安全保障", "defense", "aerospace", "missile", "military"],
    "データセンター": ["データセンター", "data center", "datacenter", "cloud infrastructure", "GPU cluster"],
    "量子": ["量子", "quantum", "quantum computing"],
}

BLS_SERIES = {
    "米国CPI All Urban Consumers - CUUR0000SA0": "CUUR0000SA0",
    "米国失業率 - LNS14000000": "LNS14000000",
    "米国非農業部門雇用者数 - CES0000000001": "CES0000000001",
    "米国平均時給 - CES0500000003": "CES0500000003",
    "米国PPI Final Demand - WPUFD4": "WPUFD4",
}

WORLD_BANK_INDICATORS = {
    "GDP current US$ - NY.GDP.MKTP.CD": "NY.GDP.MKTP.CD",
    "人口 - SP.POP.TOTL": "SP.POP.TOTL",
    "インフレ率 CPI - FP.CPI.TOTL.ZG": "FP.CPI.TOTL.ZG",
    "GDP成長率 - NY.GDP.MKTP.KD.ZG": "NY.GDP.MKTP.KD.ZG",
}

USER_AGENT_DEFAULT = "DisclosureMacroMVP/0.1 contact@example.com"


def get_deploy_secret(name: str, default: str = "") -> str:
    """Read a value from Streamlit secrets first, then environment variables.

    This lets you deploy the app without exposing API keys to visitors.
    On Streamlit Community Cloud, set secrets in the app settings.
    On Render/Railway/Fly/VPS, set environment variables.
    """
    try:
        value = st.secrets.get(name)  # type: ignore[attr-defined]
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, default)


EDINET_API_KEY_DEFAULT = get_deploy_secret("EDINET_API_KEY", "")
SEC_USER_AGENT_DEFAULT = get_deploy_secret("SEC_USER_AGENT", USER_AGENT_DEFAULT)


# -----------------------------
# Generic helpers
# -----------------------------

class ApiError(RuntimeError):
    pass


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        preview = resp.text[:300] if resp.text else ""
        raise ApiError(f"JSONの解析に失敗しました。status={resp.status_code}, body={preview}") from exc


def request_json(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None,
                 method: str = "GET", json_body: Optional[dict] = None, timeout: int = 30,
                 sec_rate_limit: bool = False) -> Any:
    if sec_rate_limit:
        time.sleep(0.12)  # keep below roughly 10 req/sec
    try:
        if method.upper() == "POST":
            resp = requests.post(url, params=params, headers=headers, json=json_body, timeout=timeout)
        else:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ApiError(f"APIリクエストに失敗しました: {exc}") from exc
    if not resp.ok:
        preview = resp.text[:500] if resp.text else ""
        raise ApiError(f"APIエラー: status={resp.status_code}, url={resp.url}, body={preview}")
    return _safe_json(resp)


def request_bytes(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None,
                  timeout: int = 60, sec_rate_limit: bool = False) -> bytes:
    if sec_rate_limit:
        time.sleep(0.12)
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ApiError(f"ダウンロードに失敗しました: {exc}") from exc
    if not resp.ok:
        preview = resp.text[:500] if resp.text else ""
        raise ApiError(f"ダウンロードエラー: status={resp.status_code}, url={resp.url}, body={preview}")
    return resp.content


def normalize_text(text: str) -> str:
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        for enc in ("utf-8", "cp932", "shift_jis", "euc_jp", "latin-1"):
            try:
                raw = raw.decode(enc)
                break
            except Exception:  # noqa: BLE001
                continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_text(soup.get_text("\n"))


def split_sentences(text: str) -> List[str]:
    text = normalize_text(text)
    # Japanese and English sentence-ish splitter.
    parts = re.split(r"(?<=[。．.!?！？])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 10]


def sentence_mentions(text: str, keywords: List[str], limit: int = 30) -> pd.DataFrame:
    rows = []
    sentences = split_sentences(text)
    lower_keywords = [(kw, kw.lower()) for kw in keywords]
    for sent in sentences:
        lower = sent.lower()
        hits = [kw for kw, kw_l in lower_keywords if kw_l in lower]
        if hits:
            rows.append({"keyword": ", ".join(sorted(set(hits))), "sentence": sent[:600]})
        if len(rows) >= limit:
            break
    return pd.DataFrame(rows)


def count_theme_mentions(text: str, themes: Dict[str, List[str]] = DEFAULT_THEMES) -> pd.DataFrame:
    rows = []
    lower = text.lower()
    for theme, keywords in themes.items():
        counts = {}
        total = 0
        for kw in keywords:
            c = lower.count(kw.lower())
            if c:
                counts[kw] = c
                total += c
        rows.append({"theme": theme, "total_mentions": total, "keyword_counts": json.dumps(counts, ensure_ascii=False)})
    return pd.DataFrame(rows).sort_values("total_mentions", ascending=False)


def extract_section_by_markers(text: str, start_markers: Iterable[str], end_markers: Iterable[str]) -> str:
    """Best-effort section extraction for Japanese/English filings."""
    norm = normalize_text(text)
    lower = norm.lower()

    starts = []
    for marker in start_markers:
        idx = lower.find(marker.lower())
        if idx >= 0:
            starts.append(idx)
    if not starts:
        return ""
    start = min(starts)

    ends = []
    search_area = lower[start + 20:]
    for marker in end_markers:
        idx = search_area.find(marker.lower())
        if idx >= 0:
            ends.append(start + 20 + idx)
    end = min(ends) if ends else min(len(norm), start + 120_000)
    if end <= start:
        end = min(len(norm), start + 120_000)
    return norm[start:end].strip()


def compare_texts(old_text: str, new_text: str, max_lines: int = 250) -> Tuple[pd.DataFrame, str]:
    old_sents = split_sentences(old_text)
    new_sents = split_sentences(new_text)
    diff = list(difflib.ndiff(old_sents, new_sents))
    rows = []
    added = []
    removed = []
    for line in diff:
        if line.startswith("+ "):
            value = line[2:].strip()
            rows.append({"change": "追加", "text": value[:800]})
            added.append(value)
        elif line.startswith("- "):
            value = line[2:].strip()
            rows.append({"change": "削除", "text": value[:800]})
            removed.append(value)
        if len(rows) >= max_lines:
            break
    summary = local_diff_summary(added, removed)
    return pd.DataFrame(rows), summary


def local_diff_summary(added: List[str], removed: List[str]) -> str:
    """Cheap extractive summary without calling an LLM."""
    risk_terms = [
        "リスク", "訴訟", "規制", "為替", "金利", "サプライチェーン", "半導体", "AI", "情報セキュリティ",
        "cyber", "litigation", "regulation", "supply", "inflation", "interest", "geopolitical", "AI",
    ]
    def score(s: str) -> int:
        lower = s.lower()
        return sum(1 for t in risk_terms if t.lower() in lower) + min(len(s) // 160, 3)

    top_added = sorted(added, key=score, reverse=True)[:5]
    top_removed = sorted(removed, key=score, reverse=True)[:3]
    lines = []
    if top_added:
        lines.append("主な追加・強調箇所:")
        for s in top_added:
            lines.append(f"- {s[:240]}")
    if top_removed:
        lines.append("主な削除・弱められた箇所:")
        for s in top_removed:
            lines.append(f"- {s[:240]}")
    if not lines:
        return "大きな文章差分は検出されませんでした。抽出範囲や文書形式を確認してください。"
    return "\n".join(lines)


def dataframe_download_button(df: pd.DataFrame, filename: str, label: str = "CSVをダウンロード") -> None:
    st.download_button(
        label=label,
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=filename,
        mime="text/csv",
    )


# -----------------------------
# EDINET helpers
# -----------------------------

@st.cache_data(show_spinner=False, ttl=3600)
def edinet_documents(date_str: str, api_key: str) -> pd.DataFrame:
    params = {"date": date_str, "type": 2, "Subscription-Key": api_key}
    data = request_json(f"{EDINET_BASE}/documents.json", params=params)
    rows = data.get("results", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    keep = [
        "seqNumber", "docID", "edinetCode", "secCode", "JCN", "filerName", "fundCode", "ordinanceCode",
        "formCode", "docTypeCode", "periodStart", "periodEnd", "submitDateTime", "docDescription",
        "issuerEdinetCode", "subjectEdinetCode", "subsidiaryEdinetCode", "currentReportReason",
    ]
    cols = [c for c in keep if c in df.columns]
    return df[cols].copy() if cols else df


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def edinet_download_zip(doc_id: str, api_key: str, doc_type: int = 1) -> bytes:
    params = {"type": doc_type, "Subscription-Key": api_key}
    return request_bytes(f"{EDINET_BASE}/documents/{doc_id}", params=params, timeout=120)


def extract_edinet_zip_text(zip_bytes: bytes, max_files: int = 80) -> Tuple[str, List[str]]:
    texts = []
    names = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        candidates = []
        for name in zf.namelist():
            lower = name.lower()
            if lower.endswith((".xbrl", ".xml", ".html", ".htm", ".txt")):
                candidates.append(name)
        # Prefer Japanese report files and XBRL main content first.
        candidates.sort(key=lambda n: ("audit" in n.lower(), "summary" in n.lower(), len(n)))
        for name in candidates[:max_files]:
            try:
                raw = zf.read(name)
                if len(raw) > 8_000_000:
                    raw = raw[:8_000_000]
                text = html_to_text(raw)
                if len(text) > 100:
                    texts.append(text)
                    names.append(name)
            except Exception:  # noqa: BLE001
                continue
    return normalize_text("\n\n".join(texts)), names


def filter_edinet_docs(df: pd.DataFrame, keyword: str, doc_type_codes: List[str]) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if keyword:
        keyword_l = keyword.lower()
        mask = pd.Series(False, index=out.index)
        for col in ["filerName", "docDescription", "secCode", "edinetCode"]:
            if col in out.columns:
                mask |= out[col].fillna("").astype(str).str.lower().str.contains(keyword_l, regex=False)
        out = out[mask]
    if doc_type_codes and "docTypeCode" in out.columns:
        out = out[out["docTypeCode"].astype(str).isin(doc_type_codes)]
    return out


# -----------------------------
# SEC helpers
# -----------------------------


def sec_headers(user_agent: str) -> Dict[str, str]:
    ua = user_agent.strip() or USER_AGENT_DEFAULT
    return {
        "User-Agent": ua,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    }


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def sec_company_tickers(user_agent: str) -> pd.DataFrame:
    data = request_json(f"{SEC_WWW_BASE}/files/company_tickers.json", headers=sec_headers(user_agent), sec_rate_limit=True)
    rows = list(data.values())
    df = pd.DataFrame(rows)
    df["ticker"] = df["ticker"].astype(str).str.upper()
    df["cik_str"] = df["cik_str"].astype(int)
    df["cik_padded"] = df["cik_str"].apply(lambda x: f"{x:010d}")
    return df


def cik_for_ticker(ticker: str, user_agent: str) -> Tuple[str, str]:
    df = sec_company_tickers(user_agent)
    hit = df[df["ticker"] == ticker.upper()]
    if hit.empty:
        raise ApiError(f"SEC ticker mapで {ticker} が見つかりませんでした。")
    row = hit.iloc[0]
    return str(row["cik_padded"]), str(row.get("title", ticker.upper()))


@st.cache_data(show_spinner=False, ttl=3600)
def sec_submissions(cik_padded: str, user_agent: str) -> Dict[str, Any]:
    return request_json(
        f"{SEC_DATA_BASE}/submissions/CIK{cik_padded}.json",
        headers=sec_headers(user_agent),
        sec_rate_limit=True,
    )


def sec_recent_filings_df(submissions: Dict[str, Any]) -> pd.DataFrame:
    recent = submissions.get("filings", {}).get("recent", {})
    if not recent:
        return pd.DataFrame()
    df = pd.DataFrame(recent)
    keep = ["form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "primaryDocDescription", "items"]
    cols = [c for c in keep if c in df.columns]
    return df[cols].copy() if cols else df


@st.cache_data(show_spinner=False, ttl=3600)
def sec_companyfacts(cik_padded: str, user_agent: str) -> Dict[str, Any]:
    return request_json(
        f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json",
        headers=sec_headers(user_agent),
        sec_rate_limit=True,
    )


def _fact_rows(companyfacts: Dict[str, Any], concept: str) -> pd.DataFrame:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    node = facts.get(concept)
    if not node:
        return pd.DataFrame()
    rows = []
    for unit, vals in node.get("units", {}).items():
        for v in vals:
            row = dict(v)
            row["unit"] = unit
            row["concept"] = concept
            row["label"] = node.get("label", concept)
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "end" in df.columns:
        df = df.sort_values("end")
    return df


def sec_key_facts_table(companyfacts: Dict[str, Any]) -> pd.DataFrame:
    concepts = {
        "売上高/Revenues": ["Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "純利益/Net income": ["NetIncomeLoss"],
        "総資産/Assets": ["Assets"],
        "自己資本/Equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "研究開発費/R&D": ["ResearchAndDevelopmentExpense"],
        "営業CF/Operating CF": ["NetCashProvidedByUsedInOperatingActivities"],
        "設備投資/Capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    }
    rows = []
    for label, concept_candidates in concepts.items():
        best = pd.DataFrame()
        used = ""
        for concept in concept_candidates:
            df = _fact_rows(companyfacts, concept)
            if not df.empty:
                best = df
                used = concept
                break
        if best.empty:
            rows.append({"metric": label, "concept": "", "period_end": "", "form": "", "value": None, "unit": ""})
            continue
        # Prefer annual 10-K facts with fiscal year duration.
        annual = best[best.get("form", pd.Series(dtype=str)).astype(str).str.contains("10-K", na=False)] if "form" in best.columns else best
        if annual.empty:
            annual = best
        row = annual.sort_values([c for c in ["end", "filed"] if c in annual.columns]).iloc[-1]
        rows.append({
            "metric": label,
            "concept": used,
            "period_end": row.get("end", ""),
            "form": row.get("form", ""),
            "value": row.get("val", None),
            "unit": row.get("unit", ""),
        })
    return pd.DataFrame(rows)


def sec_filing_url(cik_padded: str, accession: str, primary_doc: str) -> str:
    cik_int = int(cik_padded)
    acc_no_dash = accession.replace("-", "")
    return f"{SEC_WWW_BASE}/Archives/edgar/data/{cik_int}/{acc_no_dash}/{primary_doc}"


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def sec_download_filing_text(cik_padded: str, accession: str, primary_doc: str, user_agent: str) -> str:
    url = sec_filing_url(cik_padded, accession, primary_doc)
    raw = request_bytes(url, headers=sec_headers(user_agent), sec_rate_limit=True, timeout=120)
    return html_to_text(raw)


def sec_extract_risk_factors(text: str) -> str:
    # Best-effort Item 1A extraction. It works reasonably for many 10-K HTML filings,
    # but should be validated for production.
    patterns = [
        r"item\s+1a\.?\s+risk\s+factors",
        r"item\s+1a\s*[—\-:]\s*risk\s+factors",
    ]
    lower = text.lower()
    starts = []
    for pat in patterns:
        m = re.search(pat, lower, flags=re.IGNORECASE)
        if m:
            starts.append(m.start())
    if not starts:
        return ""
    start = min(starts)
    end_candidates = []
    for pat in [r"item\s+1b\.?", r"item\s+2\.?\s+properties", r"item\s+7\.?\s+management"]:
        m = re.search(pat, lower[start + 1000:], flags=re.IGNORECASE)
        if m:
            end_candidates.append(start + 1000 + m.start())
    end = min(end_candidates) if end_candidates else min(len(text), start + 180_000)
    return text[start:end]


# -----------------------------
# Macro helpers
# -----------------------------

@st.cache_data(show_spinner=False, ttl=6 * 3600)
def bls_series(series_id: str, start_year: int, end_year: int) -> pd.DataFrame:
    payload = {"seriesid": [series_id], "startyear": str(start_year), "endyear": str(end_year)}
    data = request_json(BLS_BASE, method="POST", json_body=payload, headers={"Content-Type": "application/json"})
    if data.get("status") != "REQUEST_SUCCEEDED":
        messages = data.get("message", [])
        raise ApiError(f"BLS APIエラー: {messages}")
    series = data.get("Results", {}).get("series", [])
    if not series:
        return pd.DataFrame()
    rows = []
    for obs in series[0].get("data", []):
        period = obs.get("period", "")
        if not period.startswith("M") or period == "M13":
            continue
        year = int(obs["year"])
        month = int(period[1:])
        rows.append({
            "date": pd.Timestamp(year=year, month=month, day=1),
            "value": pd.to_numeric(obs.get("value"), errors="coerce"),
            "series_id": series_id,
        })
    df = pd.DataFrame(rows).dropna(subset=["value"])
    if not df.empty:
        df = df.sort_values("date")
    return df


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def world_bank_indicator(country: str, indicator: str) -> pd.DataFrame:
    url = f"{WORLD_BANK_BASE}/country/{country}/indicator/{indicator}"
    params = {"format": "json", "per_page": 20000}
    data = request_json(url, params=params)
    if not isinstance(data, list) or len(data) < 2:
        return pd.DataFrame()
    rows = []
    for obs in data[1]:
        if obs.get("value") is None:
            continue
        rows.append({
            "date": pd.Timestamp(year=int(obs["date"]), month=1, day=1),
            "year": int(obs["date"]),
            "value": pd.to_numeric(obs.get("value"), errors="coerce"),
            "country": obs.get("country", {}).get("value", country),
            "indicator": obs.get("indicator", {}).get("value", indicator),
        })
    df = pd.DataFrame(rows).dropna(subset=["value"])
    if not df.empty:
        df = df.sort_values("date")
    return df


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def treasury_debt_to_penny(limit: int = 1000) -> pd.DataFrame:
    url = f"{TREASURY_FISCAL_BASE}/v2/accounting/od/debt_to_penny"
    params = {
        "sort": "-record_date",
        "page[size]": limit,
        "fields": "record_date,tot_pub_debt_out_amt",
        "format": "json",
    }
    data = request_json(url, params=params)
    rows = []
    for obs in data.get("data", []):
        rows.append({
            "date": pd.to_datetime(obs.get("record_date"), errors="coerce"),
            "value": pd.to_numeric(obs.get("tot_pub_debt_out_amt"), errors="coerce"),
        })
    df = pd.DataFrame(rows).dropna(subset=["date", "value"])
    if not df.empty:
        df = df.sort_values("date")
    return df


def macro_signal_from_bls(cpi: Optional[pd.DataFrame], unemp: Optional[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    if cpi is not None and not cpi.empty and len(cpi) >= 13:
        latest = cpi.iloc[-1]
        prev12 = cpi.iloc[-13]
        yoy = latest["value"] / prev12["value"] - 1
        rows.append({
            "signal": "CPI前年比",
            "latest_date": latest["date"].date(),
            "value": f"{yoy * 100:.2f}%",
            "interpretation": "3%超ならインフレ再燃に注意" if yoy > 0.03 else "インフレ圧力は相対的に落ち着き気味",
        })
    if unemp is not None and not unemp.empty and len(unemp) >= 12:
        latest = unemp.iloc[-1]
        min12 = unemp.tail(12)["value"].min()
        rise = latest["value"] - min12
        rows.append({
            "signal": "失業率の12か月内ボトムからの上昇幅",
            "latest_date": latest["date"].date(),
            "value": f"{rise:.2f}pt",
            "interpretation": "景気後退警戒度が上昇" if rise >= 0.5 else "雇用悪化シグナルは限定的",
        })
    return pd.DataFrame(rows)


# -----------------------------
# UI
# -----------------------------

st.set_page_config(page_title=APP_NAME, page_icon="📈", layout="wide")

st.title("📈 開示×マクロAIサーチ MVP")
st.caption(f"Version {APP_VERSION} / EDINET・SEC・BLS・World Bank・U.S. Treasury Fiscal Data の公開APIを使う試作品")

# Public UI users should not see API keys or operator settings.
# Configure these values in Streamlit Cloud Secrets, .streamlit/secrets.toml,
# or environment variables on the server.
edinet_key = EDINET_API_KEY_DEFAULT
sec_ua = SEC_USER_AGENT_DEFAULT

jp_tab, us_tab, macro_tab, about_tab = st.tabs(["🇯🇵 EDINET 有報AIサーチ", "🇺🇸 SEC 開示チェッカー", "🌐 マクロ投資ダッシュボード", "README"])

with jp_tab:
    st.subheader("🇯🇵 EDINET 有報AIサーチ")
    st.write("提出書類一覧を取得し、文書ZIPから本文を抽出して、テーマ言及・事業等のリスク差分を試せます。")

    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        ed_date = st.date_input("提出日", value=date.today())
    with col_b:
        keyword = st.text_input("会社名/証券コード/EDINETコード", value="")
    with col_c:
        doc_type_text = st.text_input("docTypeCodeフィルタ 任意・カンマ区切り", value="120,130,140,150", help="例: 120=有価証券報告書系として使われることが多いです。必要に応じて空欄にしてください。")

    doc_type_codes = [x.strip() for x in doc_type_text.split(",") if x.strip()]

    if st.button("EDINET提出書類を取得", type="primary", disabled=not bool(edinet_key)):
        if not edinet_key:
            st.error("EDINET APIキーを入力してください。")
        else:
            try:
                docs = edinet_documents(ed_date.strftime("%Y-%m-%d"), edinet_key)
                docs_f = filter_edinet_docs(docs, keyword, doc_type_codes)
                st.session_state["edinet_docs"] = docs_f
                st.success(f"{len(docs_f):,}件を取得しました。")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    docs = st.session_state.get("edinet_docs", pd.DataFrame())
    if not docs.empty:
        st.dataframe(docs, use_container_width=True, height=300)
        dataframe_download_button(docs, f"edinet_documents_{ed_date}.csv")

        st.markdown("### 文書本文抽出・テーマ言及")
        doc_ids = docs["docID"].dropna().astype(str).unique().tolist() if "docID" in docs.columns else []
        selected_doc = st.selectbox("解析するdocID", options=doc_ids)
        if st.button("選択文書をダウンロードして解析"):
            try:
                zip_bytes = edinet_download_zip(selected_doc, edinet_key, doc_type=1)
                text, file_names = extract_edinet_zip_text(zip_bytes)
                st.session_state["edinet_text_current"] = text
                st.session_state["edinet_text_current_doc"] = selected_doc
                st.success(f"本文抽出: {len(text):,}文字 / ファイル {len(file_names)}件")
                with st.expander("抽出ファイル"):
                    st.write(file_names[:100])
            except zipfile.BadZipFile:
                st.error("ZIPとして解析できませんでした。EDINET document typeを確認してください。")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    ed_text = st.session_state.get("edinet_text_current", "")
    if ed_text:
        st.markdown("#### テーマ言及カウント")
        theme_df = count_theme_mentions(ed_text)
        st.dataframe(theme_df, use_container_width=True)
        fig = px.bar(theme_df, x="theme", y="total_mentions", title="テーマ言及数")
        st.plotly_chart(fig, use_container_width=True)

        theme = st.selectbox("根拠文を見るテーマ", list(DEFAULT_THEMES.keys()))
        mentions_df = sentence_mentions(ed_text, DEFAULT_THEMES[theme], limit=50)
        st.dataframe(mentions_df, use_container_width=True, height=260)

        st.markdown("#### 事業等のリスク抽出")
        risk_text = extract_section_by_markers(
            ed_text,
            start_markers=["事業等のリスク", "リスク情報", "Risk Factors"],
            end_markers=["経営者による財政状態", "経営成績等の状況", "重要な契約", "研究開発活動", "Item 1B"],
        )
        if risk_text:
            st.text_area("抽出されたリスク文言", risk_text[:20_000], height=300)
            st.download_button("リスク文言TXTをダウンロード", risk_text.encode("utf-8"), f"risk_{st.session_state.get('edinet_text_current_doc','doc')}.txt", "text/plain")
        else:
            st.info("見出しベースではリスクセクションを抽出できませんでした。全文検索・XBRLタグ抽出の改善が必要です。")

        st.markdown("#### 2文書のリスク差分")
        st.write("前年文書と今年文書のリスク文言を貼り付けるか、上の抽出TXTを使って比較できます。")
        old_risk = st.text_area("前年のリスク文言", height=180)
        new_risk = st.text_area("今年のリスク文言", value=risk_text[:20_000] if risk_text else "", height=180)
        if st.button("差分を計算", key="edinet_diff_btn"):
            if old_risk and new_risk:
                diff_df, summary = compare_texts(old_risk, new_risk)
                st.markdown("##### 自動要約")
                st.markdown(summary)
                st.dataframe(diff_df, use_container_width=True, height=360)
            else:
                st.error("前年・今年の両方を入力してください。")
    elif not edinet_key:
        st.info("EDINET機能は現在、管理者側のAPI設定が未完了のため利用できません。")

with us_tab:
    st.subheader("🇺🇸 SEC 開示チェッカー")
    st.write("SEC EDGARのcompany submissions、companyfacts、10-K本文、Form 4を確認します。")
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        ticker = st.text_input("米国ティッカー", value="NVDA").upper().strip()
    with col2:
        form_filter = st.multiselect("表示フォーム", ["10-K", "10-Q", "8-K", "4", "N-PORT", "NPORT-P"], default=["10-K", "10-Q", "8-K", "4"])
    with col3:
        st.write(" ")
        run_sec = st.button("SECデータ取得", type="primary")

    if run_sec and ticker:
        try:
            cik, company_name = cik_for_ticker(ticker, sec_ua)
            subs = sec_submissions(cik, sec_ua)
            facts = sec_companyfacts(cik, sec_ua)
            st.session_state["sec_cik"] = cik
            st.session_state["sec_company_name"] = company_name
            st.session_state["sec_subs"] = subs
            st.session_state["sec_facts"] = facts
            st.success(f"{ticker}: {company_name} / CIK {cik}")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    subs = st.session_state.get("sec_subs")
    cik = st.session_state.get("sec_cik")
    facts = st.session_state.get("sec_facts")
    if subs and cik:
        filings = sec_recent_filings_df(subs)
        if form_filter and not filings.empty and "form" in filings.columns:
            filings_view = filings[filings["form"].isin(form_filter)].copy()
        else:
            filings_view = filings
        st.markdown("### Recent filings")
        st.dataframe(filings_view.head(80), use_container_width=True, height=300)
        dataframe_download_button(filings_view, f"sec_filings_{ticker}.csv")

        st.markdown("### XBRL主要ファクト")
        if facts:
            key_df = sec_key_facts_table(facts)
            st.dataframe(key_df, use_container_width=True)
            dataframe_download_button(key_df, f"sec_key_facts_{ticker}.csv")

        st.markdown("### テーマ言及・10-Kリスク前年差分")
        tenk = filings[filings["form"].eq("10-K")].copy() if not filings.empty and "form" in filings.columns else pd.DataFrame()
        if len(tenk) >= 1:
            latest = tenk.iloc[0]
            if st.button("最新10-K本文を取得してテーマ分析"):
                try:
                    text = sec_download_filing_text(cik, latest["accessionNumber"], latest["primaryDocument"], sec_ua)
                    st.session_state["sec_latest_10k_text"] = text
                    st.success(f"10-K本文抽出: {len(text):,}文字")
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

            sec_text = st.session_state.get("sec_latest_10k_text", "")
            if sec_text:
                theme_df = count_theme_mentions(sec_text)
                st.dataframe(theme_df, use_container_width=True)
                fig = px.bar(theme_df, x="theme", y="total_mentions", title=f"{ticker} 10-K テーマ言及数")
                st.plotly_chart(fig, use_container_width=True)
                theme = st.selectbox("SEC根拠文を見るテーマ", list(DEFAULT_THEMES.keys()), key="sec_theme")
                st.dataframe(sentence_mentions(sec_text, DEFAULT_THEMES[theme], limit=50), use_container_width=True, height=260)

            if len(tenk) >= 2 and st.button("最新2年の10-K Risk Factorsを比較"):
                try:
                    current = tenk.iloc[0]
                    previous = tenk.iloc[1]
                    cur_text = sec_download_filing_text(cik, current["accessionNumber"], current["primaryDocument"], sec_ua)
                    pre_text = sec_download_filing_text(cik, previous["accessionNumber"], previous["primaryDocument"], sec_ua)
                    cur_risk = sec_extract_risk_factors(cur_text)
                    pre_risk = sec_extract_risk_factors(pre_text)
                    if not cur_risk or not pre_risk:
                        st.warning("Risk Factors抽出が不完全です。HTML構造に応じて抽出ロジックを調整してください。")
                    diff_df, summary = compare_texts(pre_risk or pre_text[:80_000], cur_risk or cur_text[:80_000])
                    st.markdown("##### 自動要約")
                    st.markdown(summary)
                    st.dataframe(diff_df, use_container_width=True, height=360)
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
        else:
            st.info("Recent filingsに10-Kが見つかりませんでした。")

        st.markdown("### Form 4 インサイダー売買ウォッチ")
        form4 = filings[filings["form"].eq("4")].copy() if not filings.empty and "form" in filings.columns else pd.DataFrame()
        if not form4.empty:
            st.dataframe(form4.head(30), use_container_width=True, height=260)
        else:
            st.info("Recent filings内にForm 4がありません。")

with macro_tab:
    st.subheader("🌐 マクロ投資ダッシュボード")
    st.write("BLS、World Bank、U.S. Treasury Fiscal Dataから主要データを取得します。")

    st.markdown("### BLS 時系列")
    col1, col2, col3 = st.columns(3)
    with col1:
        bls_name = st.selectbox("BLS指標", list(BLS_SERIES.keys()))
    with col2:
        start_year = st.number_input("開始年", min_value=1940, max_value=datetime.now().year, value=max(datetime.now().year - 10, 1940), step=1)
    with col3:
        end_year = st.number_input("終了年", min_value=1940, max_value=datetime.now().year, value=datetime.now().year, step=1)
    if st.button("BLSデータ取得", type="primary"):
        try:
            df = bls_series(BLS_SERIES[bls_name], int(start_year), int(end_year))
            st.session_state["bls_df"] = df
            st.session_state["bls_name"] = bls_name
            st.success(f"{len(df):,}件取得")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    bls_df = st.session_state.get("bls_df", pd.DataFrame())
    if not bls_df.empty:
        st.plotly_chart(px.line(bls_df, x="date", y="value", title=st.session_state.get("bls_name", "BLS series")), use_container_width=True)
        st.dataframe(bls_df.tail(24), use_container_width=True)

    st.markdown("### 景気後退シグナル簡易版")
    if st.button("CPIと失業率から簡易シグナルを計算"):
        try:
            current_year = datetime.now().year
            cpi = bls_series("CUUR0000SA0", current_year - 5, current_year)
            unemp = bls_series("LNS14000000", current_year - 5, current_year)
            signal_df = macro_signal_from_bls(cpi, unemp)
            st.session_state["macro_signal"] = signal_df
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    signal_df = st.session_state.get("macro_signal", pd.DataFrame())
    if not signal_df.empty:
        st.dataframe(signal_df, use_container_width=True)

    st.markdown("### World Bank 指標")
    col1, col2 = st.columns(2)
    with col1:
        country = st.text_input("国コード", value="USA", help="例: USA, JPN, CHE, WLD")
    with col2:
        wb_name = st.selectbox("World Bank指標", list(WORLD_BANK_INDICATORS.keys()))
    if st.button("World Bankデータ取得"):
        try:
            wb_df = world_bank_indicator(country.upper(), WORLD_BANK_INDICATORS[wb_name])
            st.session_state["wb_df"] = wb_df
            st.session_state["wb_name"] = wb_name
            st.success(f"{len(wb_df):,}件取得")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    wb_df = st.session_state.get("wb_df", pd.DataFrame())
    if not wb_df.empty:
        st.plotly_chart(px.line(wb_df, x="date", y="value", title=st.session_state.get("wb_name", "World Bank series")), use_container_width=True)
        st.dataframe(wb_df.tail(20), use_container_width=True)

    st.markdown("### 米国公的債務: Debt to the Penny")
    if st.button("U.S. Treasury Fiscal Dataから債務残高を取得"):
        try:
            debt_df = treasury_debt_to_penny(1000)
            st.session_state["debt_df"] = debt_df
            st.success(f"{len(debt_df):,}件取得")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    debt_df = st.session_state.get("debt_df", pd.DataFrame())
    if not debt_df.empty:
        st.plotly_chart(px.line(debt_df, x="date", y="value", title="U.S. Total Public Debt Outstanding"), use_container_width=True)
        st.dataframe(debt_df.tail(20), use_container_width=True)

with about_tab:
    st.subheader("README / 使い方")
    st.markdown(
        """
### このMVPでできること

- **EDINET**: 指定日の提出書類一覧を取得、文書ZIPから本文抽出、テーマ言及、リスク文言抽出、前年差分比較
- **SEC**: ティッカーからCIK取得、Recent filings、companyfactsの主要XBRLファクト、10-Kテーマ言及、Risk Factors差分、Form 4一覧
- **マクロ**: BLS、World Bank、U.S. Treasury Fiscal Dataの時系列取得とグラフ化

### ローカル起動

```bash
pip install -r requirements.txt
streamlit run app.py
```

### 本番化する時の優先改良

1. EDINETのXBRLタグ辞書を整備し、平均年収・従業員数・研究開発費をタグベースで抽出
2. 会社マスターをDB化し、毎晩バッチで提出書類を差分更新
3. リスク文言・大株主・テーマ言及をPostgreSQL + pgvectorに保存
4. 重要な差分だけLLMで要約
5. SEO用にNext.jsで静的/ISRページを生成

### 注意

このアプリは投資助言ではありません。データ・抽出・要約は自動処理であり、正確性を保証しません。
商用公開時は、各データソースの利用規約、出典表示、加工表示、レート制限、免責表示を必ず実装してください。
        """
    )
