# 統合版アプリ

## 内容

1つのStreamlitアプリで以下を切り替え表示します。

- 海外で注目されている日本のニュースランキング
- 法定開示情報チェッカー

サイドバーの「ページ選択」で切り替えます。

## 海外ニュースランキング

- RSSソースは現状維持
- 対象期間: 168時間
- RSSごとの最大取得件数: 100件
- RSSアクセス間隔: 0.2秒
- ランキング上限: 1000件
- 日本時間04:00を更新枠としてキャッシュ更新

GitHub Actionsで午前4時JSTにウォームアップしたい場合は、`.github/workflows/daily_4am_jst_warmup.yml` に配置し、GitHub Secretsに `STREAMLIT_APP_URL` を設定してください。

## 法定開示情報チェッカー

アップロードされた `app (2).py` の構成を統合しています。`data/` 配下に以下のファイルがあるとダウンロード等が有効になります。

- `data/edinet_priority_sections_latest_metadata.json`
- `data/edinet_priority_sections_latest_full.csv.gz`
- `data/edinet_document_list_latest.csv`
- `data/kaiji_summary.pdf` など
- `data/brand_cat.png`

## Secrets例

```toml
[admin]
password = "your-password"

[github]
owner = "your-github-owner"
repo = "your-repo"
branch = "main"
workflow_file = "weekly_edinet_extract.yml"
token = "ghp_xxx"

[analytics]
google_measurement_id = "G-XXXXXXXXXX"

[links]
note = "https://note.com/..."
x = "https://x.com/..."
blogger = "https://..."
PDF = "https://..."
```
