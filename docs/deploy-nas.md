# NAS 部署指南（Synology / 一般 Docker 主機）

把 m2k-calendar MCP server 以 **OAuth 模式**架在 NAS 上，讓同事用
claude.ai Connectors／Claude Desktop 連上，各自用自己的應用程式專用密碼授權。

```
claude.ai / Claude Desktop
        │  HTTPS (443)
        ▼
NAS 反向代理（Let's Encrypt 憑證）
        │  http://localhost:8763
        ▼
m2k-calendar 容器（--oauth）──► mail.gss.com.tw（CalDAV / IMAP）
```

## 前置條件

- NAS 有公網可達的網域（例如 Synology DDNS：`xxx.synology.me`），443 已開通
- Synology **Container Manager**（或任一 Docker 主機）
- NAS 走內網或 VPN 連得到 `mail.gss.com.tw`

## 一、部署容器

**不需要 clone**：Docker Hub 上有現成的多平台 image
（`a26007565/m2k-calendar`，支援 amd64 / arm64）。
在 NAS 上建一個資料夾放 `docker-compose.yml`：

```yaml
# docker-compose.yml
services:
  m2k-calendar:
    image: a26007565/m2k-calendar:latest
    container_name: m2k-calendar
    # issuer 必須是使用者瀏覽器與 claude.ai 都連得到的對外 HTTPS 網址
    command: ["--oauth", "--issuer", "https://m2kcal.xxx.synology.me",
              "--host", "0.0.0.0", "--port", "8763"]
    ports:
      - "8763:8763"
    volumes:
      - m2k-data:/data          # 金鑰與 client 註冊；沒掛 volume，重佈署＝全員重新授權
    restart: unless-stopped
volumes:
  m2k-data:
```

```bash
docker compose up -d
docker compose logs -f   # 應看到 Uvicorn running on http://0.0.0.0:8763
```

（Synology 也可以在 Container Manager 的「專案」直接貼上這份 compose。
想自己建 image 的話：clone 本 repo 後把 `image:` 換成 `build: .`。）

**自動拉最新版**：在同一份 compose 加一個 Watchtower 服務，每小時檢查
Docker Hub、有新版就自動拉取並重建容器（資料在 volume，不受影響）：

```yaml
  watchtower:
    image: containrrr/watchtower
    container_name: watchtower
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --interval 3600 --cleanup m2k-calendar   # 只盯 m2k-calendar，順手清舊 image
    restart: unless-stopped
```

**重要：只能單一容器/單行程。** OAuth 的進行中授權交易存在記憶體，
開多個 replica 或負載平衡會讓授權流程隨機失敗。

## 二、反向代理（Synology）

控制台 → 登入入口 → 進階 → **反向代理伺服器** → 新增：

| 欄位 | 值 |
|---|---|
| 來源 | HTTPS、`m2kcal.xxx.synology.me`、443 |
| 目的地 | HTTP、`localhost`、8763 |

再到「自訂標頭」點 **新增 → WebSocket**（自動加 Upgrade/Connection 標頭），
「進階設定」把 **Proxy 逾時調大（如 3600 秒）**——MCP 用長連線（SSE），
預設 60 秒逾時會造成用一用斷線。

憑證：控制台 → 安全性 → 憑證 → 為該網域申請 **Let's Encrypt**，
並在「設定」把該反向代理綁定此憑證。

防火牆只需對外開 443；8763 不要直接對公網。

## 三、使用者連接

- **claude.ai（網頁/手機）**：設定 → Connectors → 新增自訂連接器 →
  URL 填 `https://m2kcal.xxx.synology.me/mcp` → 依畫面完成授權
  （輸入 m2k 帳號＋**應用程式專用密碼**，於 webmail 設定產生）。
- **Claude Desktop**（`claude_desktop_config.json`）：
  ```json
  "gss-calendar": {
    "command": "npx",
    "args": ["-y", "mcp-remote@0.1.37", "https://m2kcal.xxx.synology.me/mcp"]
  }
  ```
  注意 Desktop 有 60 秒初始化逾時：首次授權建議先在終端機跑一次
  `npx -y mcp-remote@0.1.37 https://.../mcp` 完成登入（token 會快取），
  再重啟 Desktop。

## 四、維運

| 事項 | 做法 |
|---|---|
| 更新版本（手動） | `docker compose pull && docker compose up -d`；或 Container Manager → 映像檔 → 更新後，到專案「建置」重建 |
| 更新版本（自動） | compose 加 Watchtower（見下），或 DSM 任務排程表定時跑上面那行 |
| 備份 | 備 `m2k-data` volume（含 `bridge-key`：遺失＝全員 token 失效、需重新授權） |
| 全面撤銷 | 刪掉 volume 裡的 `bridge-key` 後重啟（換金鑰＝所有已發 token 立即失效） |
| 個人撤銷 | 使用者到 webmail 撤銷該應用程式專用密碼即可 |
| 看記錄 | `docker compose logs -f`（uvicorn access log） |
| （選配）全公司人名搜尋 | webmail 匯出公司通訊錄 CSV/vCard 放進容器可讀路徑，環境變數 `M2K_DIRECTORY_FILE` 指向它 |

## 五、安全注意事項

- 伺服器**不儲存任何帳密**：憑證加密封在 token 內、每請求解密 pass-through。
- 內建防護：`/login` 同一授權交易錯 5 次作廢、動態註冊 client 有上限（超過淘汰最舊）、
  聯絡人快取有 TTL 與數量上限。
- **建議**在反向代理層加 per-IP rate limit（Synology 內建反向代理沒有此功能；
  在意的話可在容器前再放一個 nginx，`limit_req` 幾行即可），
  搭配 Mail2000 本身的登入失敗鎖定作為第二道防線。
