# 開示×マクロAIサーチ MVP

EDINET・SEC EDGAR・BLS・World Bank・U.S. Treasury Fiscal Data を使った投資データサイトのローカルMVPです。

## 機能

### 1. EDINET 有報AIサーチ

- 指定日のEDINET提出書類一覧を取得
- 会社名、証券コード、EDINETコードで絞り込み
- 文書ZIPを取得して本文抽出
- AI、半導体、防衛、データセンター等のテーマ言及数を集計
- 「事業等のリスク」らしきセクションを抽出
- 前年と今年のリスク文言を比較

### 2. SEC 開示チェッカー

- ティッカーからCIKを取得
- Recent filingsを表示
- companyfactsから主要XBRLファクトを表示
- 最新10-K本文のテーマ言及を集計
- 最新2年の10-K Risk Factorsを比較
- Form 4を一覧表示

### 3. マクロ投資ダッシュボード

- BLSのCPI、失業率、雇用者数、平均時給などを取得
- World BankのGDP、人口、インフレ率などを取得
- U.S. Treasury Fiscal Dataの公的債務残高を取得
- CPI前年比と失業率上昇幅による簡易シグナルを表示

## セットアップ

```bash
cd disclosure_macro_mvp
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## 必要なもの

- EDINET APIキー
- SEC用のUser-Agent（例: `YourSiteName your-email@example.com`）

## 注意

このMVPは実験用です。商用公開する場合は以下を必ず実装してください。

- 出典表示
- 加工表示
- 免責表示
- レート制限
- キャッシュ
- ログ監視
- データ品質チェック
- XBRLタグ辞書の整備
- APIキーの安全な管理

## 推奨する次の開発

1. PostgreSQLに会社・提出書類・抽出テキスト・ファクトを保存
2. 毎日夜間バッチでEDINET/SECを差分取得
3. Next.jsでSEOページを生成
4. OpenAI API等で差分要約を生成
5. 有料アラート機能を追加
