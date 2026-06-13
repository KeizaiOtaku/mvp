"""
Weekly EDINET priority CSV collector.

GitHub Actions などの定期実行でこのファイルを実行すると、直近7日分の
EDINET公開文書から優先抽出箇所を抽出し、data/ にCSVを出力します。
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List

import pandas as pd

from edinet_core import (
    DEFAULT_DOC_KEYWORDS,
    ExtractConfig,
    filter_documents,
    fetch_document_lists,
    now_jst_iso,
    resolve_api_key_from_env,
    run_extraction,
    today_jst,
)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_keywords(value: str) -> List[str]:
    if not value.strip():
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect EDINET priority sections for the latest N days.")
    parser.add_argument("--days", type=int, default=7, help="取得対象日数。既定は7日。")
    parser.add_argument("--end-date", type=str, default="", help="終了日 YYYY-MM-DD。未指定ならJST今日からoffsetを引く。")
    parser.add_argument("--end-offset-days", type=int, default=1, help="未指定時の終了日オフセット。1ならJST昨日まで。")
    parser.add_argument("--max-docs", type=int, default=0, help="最大処理件数。0で無制限。")
    parser.add_argument("--max-chars", type=int, default=8000, help="抽出テキスト最大文字数。0で無制限。")
    parser.add_argument("--min-text-chars", type=int, default=80, help="抽出対象にする最小文字数。")
    parser.add_argument("--sleep-sec", type=float, default=0.2, help="EDINET APIアクセス間隔。")
    parser.add_argument("--keywords", type=str, default=",".join(DEFAULT_DOC_KEYWORDS), help="docDescriptionフィルタ。カンマ区切り。")
    parser.add_argument("--no-type1-fallback", action="store_true", help="type=1 ZIPフォールバックを使わない。")
    parser.add_argument("--no-no-match-rows", action="store_true", help="一致なし行を出力しない。")
    parser.add_argument("--data-dir", type=str, default="data", help="CSV出力ディレクトリ。")
    args = parser.parse_args()

    api_key = resolve_api_key_from_env()
    if not api_key:
        raise SystemExit("EDINET_API_KEY が環境変数 / GitHub Actions Secret にありません。")

    if args.end_date:
        end_date = parse_date(args.end_date)
    else:
        end_date = today_jst() - timedelta(days=args.end_offset_days)
    start_date = end_date - timedelta(days=args.days - 1)

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    config = ExtractConfig(
        api_key=api_key,
        start_date=start_date,
        end_date=end_date,
        doc_keywords=parse_keywords(args.keywords),
        max_docs=args.max_docs,
        max_chars=args.max_chars,
        min_text_chars=args.min_text_chars,
        sleep_sec=args.sleep_sec,
        use_type1_fallback=not args.no_type1_fallback,
        include_no_match_rows=not args.no_no_match_rows,
    )

    print(f"EDINET weekly extraction: {start_date} to {end_date}", flush=True)
    print(f"Keywords: {config.doc_keywords}", flush=True)

    raw_df = fetch_document_lists(config)
    raw_latest = data_dir / "edinet_document_list_latest.csv"
    raw_df.to_csv(raw_latest, index=False, encoding="utf-8-sig")
    print(f"Fetched document list rows: {len(raw_df):,}", flush=True)

    filtered_df = filter_documents(raw_df, config.doc_keywords)
    print(f"Filtered target documents: {len(filtered_df):,}", flush=True)

    result_df = run_extraction(config, filtered_df)
    if result_df.empty:
        # 空でも列が分かるように最低限の列を作る
        result_df = pd.DataFrame(
            columns=[
                "file_date", "submit_datetime", "doc_id", "edinet_code", "sec_code", "jcn", "filer_name",
                "doc_description", "period_start", "period_end", "priority_section", "matched_element_id",
                "matched_item_name", "context_id", "unit_id", "source_file", "source_type", "text_length",
                "extracted_text", "error",
            ]
        )

    stamp = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    dated_csv = data_dir / f"edinet_priority_sections_{stamp}.csv"
    latest_csv = data_dir / "edinet_priority_sections_latest.csv"
    result_df.to_csv(dated_csv, index=False, encoding="utf-8-sig")
    shutil.copyfile(dated_csv, latest_csv)

    section_counts = {}
    if "priority_section" in result_df.columns:
        section_counts = result_df["priority_section"].fillna("").replace("", "一致なし/エラー").value_counts().to_dict()

    metadata = {
        "generated_at_jst": now_jst_iso(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": args.days,
        "raw_document_count": int(len(raw_df)),
        "target_document_count": int(len(filtered_df)),
        "output_row_count": int(len(result_df)),
        "max_docs": args.max_docs,
        "max_chars": args.max_chars,
        "keywords": config.doc_keywords,
        "latest_csv": str(latest_csv),
        "dated_csv": str(dated_csv),
        "section_counts": section_counts,
    }
    meta_path = data_dir / "edinet_priority_sections_latest_metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
