# Web公開手順

## 方式A: Streamlit Community Cloud

1. このフォルダをGitHubの新規リポジトリにpushします。
2. Streamlit Community Cloudで「New app」を選びます。
3. Repository、branch、Main file path = `app.py` を指定します。
4. Advanced settings / Secrets に以下を設定します。

```toml
EDINET_API_KEY = "your_edinet_api_key"
SEC_USER_AGENT = "DisclosureMacroMVP your-email@example.com"
```

5. Deployします。

## 方式B: Render

1. GitHubにpushします。
2. RenderでNew Web Serviceを作成し、このリポジトリを接続します。
3. Build Command:

```bash
pip install -r requirements.txt
```

4. Start Command:

```bash
streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true
```

5. Environment Variablesに以下を設定します。

```bash
EDINET_API_KEY=your_edinet_api_key
SEC_USER_AGENT=DisclosureMacroMVP your-email@example.com
```

## 方式C: VPS / Docker

```bash
docker build -t disclosure-macro-mvp .
docker run -p 8501:8501 \
  -e EDINET_API_KEY="your_edinet_api_key" \
  -e SEC_USER_AGENT="DisclosureMacroMVP your-email@example.com" \
  disclosure-macro-mvp
```

公開URL例: `http://your-server-ip:8501`

本番ではNginx/CaddyでHTTPS化してください。
