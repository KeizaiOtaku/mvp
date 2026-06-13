import argparse
import csv
import io
import json
import os
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests


EDINET_API_BASE = "https://disclosure.edinet-fsa.go.jp/api/v2"


PRIORITY_PATTERNS: Dict[str, List[str]] = {
    "事業等のリスク": [
        "事業等のリスク",
        "BusinessRisks",
        "RiskFactors",
        "risks",
    ],
    "経営方針・経営環境・対処すべき課題": [
        "経営方針",
        "経営環境",
        "対処すべき課題",
        "BusinessPolicy",
        "ManagementPolicy",
        "IssuesToAddress",
    ],
    "経営成績等の状況・MD&A": [
        "経営成績等の状況",
        "財政状態",
        "経営成績",
        "キャッシュ・フロー",
        "MD&A",
        "ManagementAnalysis",
        "AnalysisOfFinancialPosition",
        "OperatingResults",
    ],
    "設備投資等の概要": [
        "設備投資",
        "CapitalExpenditures",
        "OverviewOfCapitalExpenditures",
    ],
    "研究開発活動": [
        "研究開発活動",
        "ResearchAndDevelopment",
        "R&D",
    ],
    "重要な後発事象": [
        "重要な後発事象",
        "SubsequentEvents",
        "SignificantSubsequentEvents",
    ],
    "継続企業の前提": [
        "継続企業",
        "GoingConcern",
        "GoingConcernAssumption",
    ],
    "大株主の状況": [
        "大株主の状況",
        "MajorShareholders",
        "PrincipalShareholders",
    ],
    "配当政策": [
        "配当政策",
        "DividendPolicy",
    ],
    "サステナビリティ・人的資本": [
        "サステナビリティ",
        "人的資本",
        "人材",
        "多様性",
        "Sustainability",
        "HumanCapital",
        "Diversity",
    ],
    "大量保有：保有目的": [
        "保有目的",
        "PurposeOfHolding",
    ],
    "大量保有：保有割合・増減": [
        "保有割合",
        "株券等保有割合",
        "増加",
        "減少",
        "HoldingRatio",
        "ShareholdingRatio",
    ],
    "臨時報告書：提出事由・発生事実": [
        "提出事由",
        "発生事実",
        "異動",
        "決定",
        "ReasonForFiling",
        "Event",
        "ExtraordinaryReport",
    ],
    "訂正報告書：訂正理由・訂正箇所": [
        "訂正理由",
        "訂正箇所",
        "訂正前",
        "訂正後",
        "ReasonForCorrection",
        "Correction",
    ],
}


DOC_LIST_COLUMNS = [
    "generated_at_utc",
    "file_date",
    "seq_number",
    "doc_id",
    "edinet_code",
    "sec_code",
    "jcn",
    "filer_name",
    "fund_code",
    "ordinance_code",
    "form_code",
    "doc_type_code",
    "period_start",
    "period_end",
    "submit_datetime",
    "doc_description",
    "issuer_edinet_code",
    "subject_edinet_code",
    "subsidiary_edinet_code",
    "current_report_reason",
    "parent_doc_id",
    "ope_date_time",
    "withdrawal_status",
    "doc_info_edit_status",
    "disclosure_status",
    "xbrl_flag",
    "pdf_flag",
    "attach_doc_flag",
    "english_doc_flag",
    "csv_flag",
]


EXTRACT_COLUMNS = [
    "generated_at_utc",
    "file_date",
    "submit_datetime",
    "filer_name",
    "edinet_code",
    "sec_code",
    "doc_description",
    "doc_type_code",
    "form_code",
    "doc_id",
    "priority_section",
    "matched_keyword",
    "matched_file",
    "matched_row_index",
    "matched_columns",
    "source_type",
    "text_length",
    "extracted_text",
    "error",
]


def get_api_key() -> str:
    api_key = os.environ.get("EDINET_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("EDINET_API_KEY is not set.")
    return api_key


def build_headers(api_key: str) -> Dict[str, str]:
    return {
        "User-Agent": "edinet-weekly-priority-extractor/1.0",
        "Accept": "application/json, application/zip, */*",
        "Ocp-Apim-Subscription-Key": api_key,
    }


def request_get(
    url: str,
    api_key: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> requests.Response:
    params = dict(params or {})
    headers = build_headers(api_key)

    # EDINET API v2 environments may accept the subscription key through a header.
    # Some setups/documentation examples also use a query parameter. Supplying both
    # makes the script more tolerant without exposing the key in logs.
    params.setdefault("Subscription-Key", api_key)

    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    if response.status_code >= 400:
        detail = response.text[:1000]
        raise RuntimeError(
            f"HTTP {response.status_code} for {url}. detail={detail}"
        )
    return response


def daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def normalize_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).replace("\u3000", " ").strip()


def truncate_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", normalize_str(text))
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def get_doc_id(item: Dict[str, Any]) -> str:
    return normalize_str(item.get("docID") or item.get("docId") or item.get("doc_id"))


def doc_item_to_row(item: Dict[str, Any], file_date: str, generated_at_utc: str) -> Dict[str, Any]:
    return {
        "generated_at_utc": generated_at_utc,
        "file_date": file_date,
        "seq_number": item.get("seqNumber", ""),
        "doc_id": get_doc_id(item),
        "edinet_code": item.get("edinetCode", ""),
        "sec_code": item.get("secCode", ""),
        "jcn": item.get("JCN", ""),
        "filer_name": item.get("filerName", ""),
        "fund_code": item.get("fundCode", ""),
        "ordinance_code": item.get("ordinanceCode", ""),
        "form_code": item.get("formCode", ""),
        "doc_type_code": item.get("docTypeCode", ""),
        "period_start": item.get("periodStart", ""),
        "period_end": item.get("periodEnd", ""),
        "submit_datetime": item.get("submitDateTime", ""),
        "doc_description": item.get("docDescription", ""),
        "issuer_edinet_code": item.get("issuerEdinetCode", ""),
        "subject_edinet_code": item.get("subjectEdinetCode", ""),
        "subsidiary_edinet_code": item.get("subsidiaryEdinetCode", ""),
        "current_report_reason": item.get("currentReportReason", ""),
        "parent_doc_id": item.get("parentDocID", ""),
        "ope_date_time": item.get("opeDateTime", ""),
        "withdrawal_status": item.get("withdrawalStatus", ""),
        "doc_info_edit_status": item.get("docInfoEditStatus", ""),
        "disclosure_status": item.get("disclosureStatus", ""),
        "xbrl_flag": item.get("xbrlFlag", ""),
        "pdf_flag": item.get("pdfFlag", ""),
        "attach_doc_flag": item.get("attachDocFlag", ""),
        "english_doc_flag": item.get("englishDocFlag", ""),
        "csv_flag": item.get("csvFlag", ""),
    }


def fetch_document_list_for_date(
    target_date: date,
    api_key: str,
    sleep_sec: float = 0.2,
) -> List[Dict[str, Any]]:
    url = f"{EDINET_API_BASE}/documents.json"
    params = {
        "date": target_date.isoformat(),
        "type": 2,
    }

    response = request_get(url, api_key=api_key, params=params, timeout=60)
    data = response.json()
    results = data.get("results", [])

    if sleep_sec > 0:
        time.sleep(sleep_sec)

    if not isinstance(results, list):
        return []
    return results


def fetch_document_csv_zip(
    doc_id: str,
    api_key: str,
    sleep_sec: float = 0.2,
) -> bytes:
    url = f"{EDINET_API_BASE}/documents/{doc_id}"
    params = {
        "type": 5,
    }

    response = request_get(url, api_key=api_key, params=params, timeout=120)
    content = response.content

    if sleep_sec > 0:
        time.sleep(sleep_sec)

    if not content:
        raise RuntimeError("Empty response body.")
    return content


def detect_encoding(raw: bytes) -> List[str]:
    candidates = []

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        candidates.append("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")

    candidates.extend(
        [
            "utf-8-sig",
            "utf-8",
            "cp932",
            "shift_jis",
            "utf-16",
        ]
    )

    seen = set()
    unique = []
    for enc in candidates:
        if enc not in seen:
            unique.append(enc)
            seen.add(enc)
    return unique


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    last_error = None

    for enc in detect_encoding(raw):
        for sep in [None, ",", "\t"]:
            try:
                if sep is None:
                    return pd.read_csv(
                        io.BytesIO(raw),
                        encoding=enc,
                        sep=None,
                        engine="python",
                        dtype=str,
                        on_bad_lines="skip",
                    ).fillna("")
                return pd.read_csv(
                    io.BytesIO(raw),
                    encoding=enc,
                    sep=sep,
                    dtype=str,
                    on_bad_lines="skip",
                ).fillna("")
            except Exception as e:
                last_error = e

    raise RuntimeError(f"Could not read CSV bytes. last_error={last_error}")


def iter_csv_files_from_zip(zip_bytes: bytes) -> Iterable[Tuple[str, pd.DataFrame]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if not lower.endswith(".csv"):
                continue

            try:
                raw = zf.read(name)
                df = read_csv_bytes(raw)
                yield name, df
            except Exception:
                continue


def row_to_combined_text(row: pd.Series) -> Tuple[str, List[str]]:
    parts = []
    cols = []

    for col, val in row.items():
        s = normalize_str(val)
        if not s:
            continue
        parts.append(f"{col}: {s}")
        cols.append(str(col))

    return " | ".join(parts), cols


def find_priority_matches_in_df(
    df: pd.DataFrame,
    file_name: str,
    max_chars: int,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []

    if df.empty:
        return matches

    # Normalize column names once.
    df = df.copy()
    df.columns = [normalize_str(c) for c in df.columns]

    for row_index, row in df.iterrows():
        combined, used_cols = row_to_combined_text(row)
        if not combined:
            continue

        combined_lower = combined.lower()

        for section, keywords in PRIORITY_PATTERNS.items():
            for kw in keywords:
                kw_norm = normalize_str(kw)
                if not kw_norm:
                    continue

                if kw_norm.lower() in combined_lower:
                    matches.append(
                        {
                            "priority_section": section,
                            "matched_keyword": kw_norm,
                            "matched_file": file_name,
                            "matched_row_index": int(row_index),
                            "matched_columns": ",".join(used_cols[:30]),
                            "source_type": "edinet_csv_zip",
                            "text_length": len(combined),
                            "extracted_text": truncate_text(combined, max_chars),
                            "error": "",
                        }
                    )
                    break

    return matches


def make_error_extract_row(
    doc_row: Dict[str, Any],
    generated_at_utc: str,
    error: str,
) -> Dict[str, Any]:
    return {
        "generated_at_utc": generated_at_utc,
        "file_date": doc_row.get("file_date", ""),
        "submit_datetime": doc_row.get("submit_datetime", ""),
        "filer_name": doc_row.get("filer_name", ""),
        "edinet_code": doc_row.get("edinet_code", ""),
        "sec_code": doc_row.get("sec_code", ""),
        "doc_description": doc_row.get("doc_description", ""),
        "doc_type_code": doc_row.get("doc_type_code", ""),
        "form_code": doc_row.get("form_code", ""),
        "doc_id": doc_row.get("doc_id", ""),
        "priority_section": "",
        "matched_keyword": "",
        "matched_file": "",
        "matched_row_index": "",
        "matched_columns": "",
        "source_type": "error",
        "text_length": 0,
        "extracted_text": "",
        "error": truncate_text(error, 2000),
    }


def attach_doc_metadata(
    match: Dict[str, Any],
    doc_row: Dict[str, Any],
    generated_at_utc: str,
) -> Dict[str, Any]:
    return {
        "generated_at_utc": generated_at_utc,
        "file_date": doc_row.get("file_date", ""),
        "submit_datetime": doc_row.get("submit_datetime", ""),
        "filer_name": doc_row.get("filer_name", ""),
        "edinet_code": doc_row.get("edinet_code", ""),
        "sec_code": doc_row.get("sec_code", ""),
        "doc_description": doc_row.get("doc_description", ""),
        "doc_type_code": doc_row.get("doc_type_code", ""),
        "form_code": doc_row.get("form_code", ""),
        "doc_id": doc_row.get("doc_id", ""),
        "priority_section": match.get("priority_section", ""),
        "matched_keyword": match.get("matched_keyword", ""),
        "matched_file": match.get("matched_file", ""),
        "matched_row_index": match.get("matched_row_index", ""),
        "matched_columns": match.get("matched_columns", ""),
        "source_type": match.get("source_type", ""),
        "text_length": match.get("text_length", 0),
        "extracted_text": match.get("extracted_text", ""),
        "error": match.get("error", ""),
    }


def should_try_csv(doc_row: Dict[str, Any]) -> bool:
    csv_flag = normalize_str(doc_row.get("csv_flag"))
    doc_id = normalize_str(doc_row.get("doc_id"))

    if not doc_id:
        return False

    # If csv_flag exists and is explicitly 0, skip.
    if csv_flag == "0":
        return False

    return True


def collect_weekly(
    days: int,
    end_offset_days: int,
    max_docs: int,
    max_chars: int,
    sleep_sec: float,
    data_dir: Path,
) -> Dict[str, Any]:
    api_key = get_api_key()

    generated_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    today_utc = datetime.now(timezone.utc).date()
    end_date = today_utc - timedelta(days=end_offset_days)
    start_date = end_date - timedelta(days=days - 1)

    data_dir.mkdir(parents=True, exist_ok=True)

    doc_rows: List[Dict[str, Any]] = []

    for d in daterange(start_date, end_date):
        items = fetch_document_list_for_date(d, api_key=api_key, sleep_sec=sleep_sec)
        for item in items:
            row = doc_item_to_row(
                item=item,
                file_date=d.isoformat(),
                generated_at_utc=generated_at_utc,
            )
            doc_rows.append(row)

    docs_df = pd.DataFrame(doc_rows, columns=DOC_LIST_COLUMNS)
    if docs_df.empty:
        docs_df = pd.DataFrame(columns=DOC_LIST_COLUMNS)
        docs_df.loc[0, "generated_at_utc"] = generated_at_utc
        docs_df.loc[0, "file_date"] = ""
        docs_df.loc[0, "doc_id"] = ""

    # Sort for deterministic output.
    if not docs_df.empty and "submit_datetime" in docs_df.columns:
        docs_df = docs_df.sort_values(
            by=["submit_datetime", "doc_id"],
            ascending=[False, True],
            na_position="last",
        ).reset_index(drop=True)

    process_rows = docs_df.copy()
    if max_docs and max_docs > 0:
        process_rows = process_rows.head(max_docs)

    extract_rows: List[Dict[str, Any]] = []

    for _, doc_row_series in process_rows.iterrows():
        doc_row = doc_row_series.to_dict()

        if not should_try_csv(doc_row):
            continue

        doc_id = normalize_str(doc_row.get("doc_id"))
        if not doc_id:
            continue

        try:
            zip_bytes = fetch_document_csv_zip(
                doc_id=doc_id,
                api_key=api_key,
                sleep_sec=sleep_sec,
            )

            any_match = False

            for file_name, df in iter_csv_files_from_zip(zip_bytes):
                matches = find_priority_matches_in_df(
                    df=df,
                    file_name=file_name,
                    max_chars=max_chars,
                )

                for match in matches:
                    any_match = True
                    extract_rows.append(
                        attach_doc_metadata(
                            match=match,
                            doc_row=doc_row,
                            generated_at_utc=generated_at_utc,
                        )
                    )

            # Keep a visible trace even if no priority section was found.
            if not any_match:
                extract_rows.append(
                    {
                        "generated_at_utc": generated_at_utc,
                        "file_date": doc_row.get("file_date", ""),
                        "submit_datetime": doc_row.get("submit_datetime", ""),
                        "filer_name": doc_row.get("filer_name", ""),
                        "edinet_code": doc_row.get("edinet_code", ""),
                        "sec_code": doc_row.get("sec_code", ""),
                        "doc_description": doc_row.get("doc_description", ""),
                        "doc_type_code": doc_row.get("doc_type_code", ""),
                        "form_code": doc_row.get("form_code", ""),
                        "doc_id": doc_row.get("doc_id", ""),
                        "priority_section": "",
                        "matched_keyword": "",
                        "matched_file": "",
                        "matched_row_index": "",
                        "matched_columns": "",
                        "source_type": "no_priority_match",
                        "text_length": 0,
                        "extracted_text": "",
                        "error": "",
                    }
                )

        except Exception as e:
            extract_rows.append(
                make_error_extract_row(
                    doc_row=doc_row,
                    generated_at_utc=generated_at_utc,
                    error=str(e),
                )
            )

    result_df = pd.DataFrame(extract_rows, columns=EXTRACT_COLUMNS)

    if result_df.empty:
        result_df = pd.DataFrame(columns=EXTRACT_COLUMNS)
        result_df.loc[0, "generated_at_utc"] = generated_at_utc
        result_df.loc[0, "source_type"] = "empty"
        result_df.loc[0, "error"] = "No rows generated."

    # Force generated_at_utc into both CSVs so GitHub sees a real diff on every run.
    docs_df["generated_at_utc"] = generated_at_utc
    result_df["generated_at_utc"] = generated_at_utc

    stamp = f"{start_date.isoformat()}_to_{end_date.isoformat()}"

    dated_csv = data_dir / f"edinet_priority_sections_{stamp}.csv"
    latest_csv = data_dir / "edinet_priority_sections_latest.csv"
    latest_doc_list_csv = data_dir / "edinet_document_list_latest.csv"
    metadata_json = data_dir / "edinet_priority_sections_latest_metadata.json"

    result_df.to_csv(dated_csv, index=False, encoding="utf-8-sig")
    result_df.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    docs_df.to_csv(latest_doc_list_csv, index=False, encoding="utf-8-sig")

    metadata = {
        "generated_at_utc": generated_at_utc,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": days,
        "end_offset_days": end_offset_days,
        "max_docs": max_docs,
        "max_chars": max_chars,
        "sleep_sec": sleep_sec,
        "document_count": int(len(doc_rows)),
        "processed_document_count": int(len(process_rows)),
        "extracted_row_count": int(len(result_df)),
        "latest_csv": str(latest_csv),
        "dated_csv": str(dated_csv),
        "latest_doc_list_csv": str(latest_doc_list_csv),
    }

    metadata_json.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect EDINET documents and extract priority sections to CSV."
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to collect.",
    )
    parser.add_argument(
        "--end-offset-days",
        type=int,
        default=1,
        help="0 means today UTC, 1 means yesterday UTC.",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=20,
        help="Maximum documents to process. 0 means unlimited.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=8000,
        help="Maximum extracted text length per matched row.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=float,
        default=0.2,
        help="Sleep seconds between EDINET API requests.",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Output data directory.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    metadata = collect_weekly(
        days=max(1, args.days),
        end_offset_days=max(0, args.end_offset_days),
        max_docs=max(0, args.max_docs),
        max_chars=max(0, args.max_chars),
        sleep_sec=max(0.0, args.sleep_sec),
        data_dir=Path(args.data_dir),
    )

    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
