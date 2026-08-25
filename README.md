# m2k 行事曆工具組 — 使用說明

[![docker](https://img.shields.io/docker/v/a26007565/m2k-calendar?sort=semver&label=docker%20hub)](https://hub.docker.com/r/a26007565/m2k-calendar)

一組交付物，共用同一套已驗證的 m2k（Mail2000）行為分析。挑符合情境的用。
版本以 `src/_version.py` 為單一來源（image tag、登入頁 footer 都引用它）。
發佈全自動：bump `_version.py` push 到 main → GitHub Actions 自動 build 多平台
image 推上 Docker Hub（`:版號` + `:latest`）→ 部署端 Watchtower 自動拉新版。

## 專案結構

```
src/          Python 原始碼（CLI 與 MCP server）
tests/        離線單元測試（免帳密、免連線）
userscripts/  webmail 使用者腳本（Tampermonkey）
docs/         技術分析報告
skill/        m2k-calendar skill（供 Claude 匯入）
```

## 檔案一覽

| 檔案 | 用途 | 認證 | 狀態 |
|---|---|---|---|
| `userscripts/m2k-group-book.user.js` | **webmail 使用者腳本**：部門遞迴展開 + 批次加入與會者 | 沿用瀏覽器登入（同源、免 CORS、免 token） | ✅ 離線 26 項 + 實機 10 項斷言 |
| `userscripts/m2k-multi-calendar-board.user.js` | webmail 使用者腳本：多人/他人行事曆合併看板 | 同上 | ✅ 資料流程+渲染實測 |
| `src/m2kcal.py` | 行事曆 CLI：查詢 / book / 看板 / 加與會者 | CalDAV Basic（需應用程式密碼） | ✅ 真實環境驗證（讀+寫+看板） |
| `src/m2kgroup.py` | 群組展開 CLI | 需帶瀏覽器 session token（SAML 限制） | ✅ 語法 + 離線測試通過 |
| `src/m2k_mcp_server.py` | MCP server：讓 Claude 查行程/建會議 | 同 m2kcal（環境變數/.env） | ✅ 語法驗證 |
| `skill/SKILL.md` | m2k-calendar skill | — | ✅ |
| `tests/group-book.test.mjs` | 使用者腳本離線測試（jsdom + 假伺服器） | — | ✅ 26 項全過（`npm test`） |
| `tests/live_adb2_probe.js` | 通訊錄實機迴歸測試（瀏覽器 console 貼上） | 沿用瀏覽器登入 | ✅ 10 項斷言 |
| `tests/test_m2k.py` | 離線單元測試（假資料、免帳密） | — | ⚠ 需 `pip install caldav icalendar` 才跑得起來 |
| `docs/m2k-calendar-cli-feasibility.md` | 完整技術分析報告 | — | — |

---

## 一、使用者腳本（建議先用這個，大家都能用）

**解決什麼**：book 群組/組織信箱時，底下成員收不到。此腳本把群組**當下成員展開，批次灌進原生「會議排程」的與會者欄**，用 Mail2000 原生流程送出（含「寄送通知信」），成員才會真的收到。

**安裝**
1. Chrome 安裝 Tampermonkey 擴充。
2. Tampermonkey → 新增腳本 → 貼上 `userscripts/m2k-group-book.user.js` 內容 → 存檔。

> 新版 Chrome 需額外授權：`chrome://extensions` → Tampermonkey → 詳細資料 → 開「**允許使用者指令碼**」，再重整頁面（這是腳本沒出現最常見的原因）。

**使用（v0.4：面板一站式，全部在排程頁完成）**
1. 進 Mail2000 →「會議排程」頁，右下「👥 群組排會議」開面板。
2. **A. 填會議資訊**：標題、日期、開始/結束時間、地點。
3. **B. 加入與會者**（下拉切換）：
   - **搜姓名（autocomplete）**：邊打邊跳建議（中文 1 字、英文 2 字即觸發），點一下該人就加入，右側顯示「＋加入 / ✓已加」。
   - **搜部門**：打部門代碼 → 按「展開全部並加入」→ **遞迴抓齊該部門與所有子部門的成員**逐一加入（跨部門自動去重）。只要本層的話按旁邊的「僅本層」。
   - 或展開「貼上 email」批次加入。
   - 面板即時顯示「目前與會者：N 人」。
4. **C. 按「✅ 建立會議」**：自動填入原生表單、勾「寄送通知信」、跳出確認後儲存。

**重要：群組信箱 vs 展開**
- 直接打**群組信箱**只是「一個收件者」,**不會展開**、底下成員收不到 —— 這是原本的問題。
- 要展開成員，請用 **B → 搜部門**，它會列出該部門並把成員一個個加入，大家才收得到。

**原理（皆已實測）**
- 搜人：`/cgi-bin/adb2search_mds`(`command=mdssearch`)，需帶通訊錄範圍（自動取得）。
- 搜部門：`/cgi-bin/adb2main_mds`(`command=list`,`pageno` 分頁) 讀 `input[name=Entries]`；`adbetype="D"` 是子部門（`value` 為完整路徑）、`adbetype="C"` 是人（`email` 屬性）。一支請求同時給「這層的人」與「這層的子部門」,所以遞迴邊走邊收即可。
- 註：部門**不在** `/cgi-bin/adb2tree_mds` 的樹裡——那支不論參數只回幾個頂層目錄（已實測 10 種參數組合）;程式改為動態取那些目錄當遞迴起點。
- 填表：標題/地點=文字欄；日期=jQuery datepicker `setDate`；時間=`HH:MM` 下拉；儲存鈕 `publishSettingOK`（實測填入正常）。
- 全程同源、只靠瀏覽器登入 cookie、免 token、免 CORS。
- 每頁固定 25 筆,未滿即最後一頁,所以請求數約等於部門數。實機驗證見 `tests/live_adb2_probe.js`（數字會隨組織調整而變,不寫死在文件裡）。

**限制：純郵件群組（distribution list）無法展開**
只有「一個群組信箱」的郵件群組（distribution list），成員名單存在郵件伺服器端，通訊錄與任何前端 API 都讀不到 —— 因此**無法自動拆成個別成員**（連原生 webmail 也看不到其成員）。能展開的只有「通訊錄裡看得到成員」的部門與個人群組。若要邀請郵件群組的個別成員，需先向群組管理者/IT 取得名單，再用「貼上 email」批次加入。

**已驗證**：對真實排程表單實測——批次加入成員成功、忙碌狀態正常帶出、可正確移除；腳本不會自動存檔，一切以你按「儲存」為準。

---

## 二、行事曆 CLI（m2kcal.py）

```bash
pip install caldav icalendar
export M2K_USER="you@example.com"
export M2K_PASS="應用程式專用密碼"     # 因 SAML，需在信箱設定產生 App 密碼
python3 src/m2kcal.py agenda --days 7
python3 src/m2kcal.py book --title "週會" --start "2026-07-10 14:00" --end "2026-07-10 15:00" \
    --attendee user_a@example.com --attendee user_b@example.com
```

> 注意：CalDAV 無排程功能，`--attendee` 只寫入事件、不會自動寄邀請。要通知請用使用者腳本的原生流程。

## 三、群組展開 CLI（m2kgroup.py）

因 SAML SSO，獨立程式無法自行登入，需從瀏覽器帶入 session：

```bash
pip install requests
export M2K_M="<通訊錄網址的 m 參數>"; export M2K_SSNID="<ssnid>"; export M2K_COOKIE="<Cookie>"
python3 src/m2kgroup.py expand --abid <ABID> --dirid <DIRID> --as-attendees
```

---

## 四、MCP server（讓 Claude 直接查行程 / 建會議）

`src/m2k_mcp_server.py` 提供工具：
- 查詢：`agenda`、`list_events`、`search_events`（關鍵字搜標題/地點/描述）、
  `find_free_slots`（free-busy 找自己的空檔）、`find_person`（模糊人名查 email）、
  `list_calendars`

**find_person 的資料來源**（自動合併；前兩項零設定、跟著各使用者自己的憑證走，
共用部署天生每人隔離）：
1. 行事曆近一年的與會者/召集人。
2. **信箱最近往來**（IMAP 抓 INBOX＋寄件匣最近各 1500 封的 From/To/Cc 建池，
   依使用者快取 15 分鐘；Mail2000 IMAP SEARCH 無索引，故不做即時搜尋）。
3. `M2K_DIRECTORY_FILE`（選配）＝webmail 匯出的全公司通訊錄（CSV/vCard），
   涵蓋沒往來過的人；依 mtime 自動重載。
- 異動：`book`（支援 repeat 重複會議與 reminder_minutes 提醒；時段重疊會附警告）、
  `update_event`（改標題/時間/地點/描述/與會者，uid 取自查詢輸出的 `id:` 欄位；重複會議改整串）、
  `respond_event`（回覆出席狀態 accept/tentative/decline，只更新自己日曆、不通知召集人）、
  `delete_event`（刪除，重複會議刪整串）
- UI：`show_calendar`（互動行事曆，見下）

**MCP App 行事曆 UI**：支援 [MCP Apps](https://github.com/modelcontextprotocol/ext-apps) 的客戶端
（如 Claude）呼叫 `show_calendar` 時會直接在對話中渲染互動行事曆——週/月檢視、點事件看
與會者細節、UI 上直接新增（走 `book`）與編輯會議（走 `update_event`）。不支援 UI 的客戶端
拿到同樣資料的 JSON 文字。UI 原始碼在 `apps/calendar/`，建置產物 `apps/calendar/dist/calendar.html`
已進版控（server 直接讀取，執行期不需要 node）；改 UI 後重建：
`cd apps/calendar && npm install && npm run build`。

```bash
pip install "mcp[cli]" caldav icalendar requests
```

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "m2k-calendar": {
      "command": "python3",
      "args": ["/絕對路徑/m2k-calendar-tools/src/m2k_mcp_server.py"],
      "env": {
        "M2K_USER": "you@example.com",
        "M2K_PASS": "應用程式專用密碼"
      }
    }
  }
}
```

**Claude Code**：

```bash
claude mcp add m2k-calendar \
  -e M2K_USER=you@example.com -e M2K_PASS=應用程式專用密碼 \
  -- python3 /絕對路徑/m2k-calendar-tools/src/m2k_mcp_server.py
```

注意事項：
- 憑證也可放專案根目錄 `.env`（server 啟動時自動載入），就不必寫進設定檔。
- 可預期錯誤（帳密錯、時間格式錯）會以「錯誤：...」回覆 Claude，server 不會中斷。
- 範圍：只能查**自己的**日曆與建立會議；看他人行事曆請用使用者腳本（SAML session 限制）。

### 公用部署（HTTP 模式，多人共用）

在內網主機啟動（**伺服器不保存任何帳密**）：

```bash
python3 src/m2k_mcp_server.py --http --host 0.0.0.0 --port 8763
```

每位使用者帶**自己的**應用程式專用密碼連入；伺服器逐請求把憑證
pass-through 給 CalDAV：

```bash
claude mcp add --transport http m2k-calendar https://主機:8763/mcp \
  --header "Authorization: Basic $(printf '%s' '帳號@公司網域:應用程式專用密碼' | base64)"
```

安全須知：
- **必須放在 HTTPS 反向代理後**（nginx / caddy）——Basic 標頭在純 HTTP 下等同明文傳帳密。
- HTTP 模式**絕不回退**到環境變數憑證；沒帶或帶錯 `Authorization` 一律回錯誤，不會冒用部署者身分。
- 撤銷存取＝使用者自己到 webmail 撤銷該應用程式專用密碼，伺服器端無需任何操作。
- stdio 模式（上方本機設定）行為不變，各模式可並存部署。

### claude.ai Connectors（手機 app / 網頁版）：OAuth bridge

claude.ai 的 Connectors 不支援自訂 header，需走標準 OAuth 2.1（動態註冊 + PKCE）：

```bash
pip install cryptography   # 額外相依
python3 src/m2k_mcp_server.py --oauth --issuer https://對外網址 --host 0.0.0.0 --port 8763
```

- 使用者初次連接會被導到 `/login` 輸入 m2k 帳號＋應用程式專用密碼；bridge 先打一次
  CalDAV 驗證，通過後把憑證用伺服器金鑰 AES-GCM 加密封進 token——**無狀態設計，
  伺服器不存任何憑證**。access token 1 小時、refresh token 30 天自動輪替。
- 金鑰來源：環境變數 `M2K_BRIDGE_KEY`（urlsafe base64 的 32 bytes），或首次啟動自動
  產生專案根目錄 `.bridge-key`（chmod 600）。**換金鑰＝所有已發 token 立即失效**。
- claude.ai 端設定：Settings ▸ Connectors ▸ Add custom connector → 填 `https://對外網址/mcp`
  → 依畫面完成授權登入。
- 前置需求：**公網可達的 HTTPS 網址**（claude.ai 的連線來自 Anthropic 雲端，不是你的裝置）；
  行程內容會經過 Anthropic 伺服器，部署前請先確認公司資料政策。
- 防護：`/login` 同一授權交易密碼連錯 5 次即作廢；登入頁帶 `X-Frame-Options: DENY`、
  CSP、`Cache-Control: no-store`。redirect_uri 由 mcp SDK 對註冊清單完全比對。

### Docker 部署（HTTP / OAuth 模式）

```bash
docker build -t m2k-calendar .
# HTTP 模式（Basic pass-through）
docker run -d -p 8763:8763 m2k-calendar
# OAuth 模式（掛 volume 保留金鑰與 client 註冊；換金鑰＝全部 token 失效）
docker run -d -p 8763:8763 -v m2k-data:/data m2k-calendar \
  --oauth --issuer https://對外網址 --host 0.0.0.0 --port 8763
```

容器以非 root 執行；stdio 本機模式不需要 Docker。HTTPS 一樣由前面的反向代理處理。
**NAS（Synology）完整架設步驟**（compose、反向代理、憑證、維運）見
[docs/deploy-nas.md](docs/deploy-nas.md)。注意：OAuth 授權狀態在記憶體，**只能單行程**，
不可開多 replica / 負載平衡。

## 五、Skill（skill/SKILL.md）

`skill/SKILL.md` 描述了「什麼情境用哪個工具」與行為須知（PUT 500、時區、不寄邀請等），
讓 Claude 自動判斷。匯入方式擇一：
- claude.ai / Claude Desktop：Settings ▸ Capabilities 匯入。
- Claude Code：複製到專案 `.claude/skills/m2k-calendar/SKILL.md`。

---

## 功能測試怎麼做

1. **使用者腳本離線測試**（最快、免帳密、免登入）：`npm install && npm test`
   → 26 項，涵蓋通訊錄 `Entries` 解析、分頁截斷、部門遞迴（含環狀路徑與節點上限）、
   加入與會者（去重／連續失敗中止／欄位隱藏）、HTML 跳脫、警告傳遞。
   用 jsdom + 假伺服器，假伺服器的行為（每頁 25 筆、目錄排在成員前、超界頁回捲）
   都取自實機驗證過的事實，不是憑空假設。
2. **通訊錄實機迴歸測試**（需已登入的瀏覽器）：在 webmail 開 DevTools Console，
   貼上 `tests/live_adb2_probe.js`，跑 `await adb2probe("<部門代碼>")`
   → 10 項斷言，驗「伺服器實際怎麼回應」：端點行為、`adbetype` 值域、分頁邊界、
   遞迴規模、chip 的 `data-id` 格式。可重複跑；輸出含真實通訊錄內容，外流前請斟酌。
3. **行事曆離線單元測試**：`python3 tests/test_m2k.py`（需先 `pip install caldav icalendar`）
   → 驗證 ICS 產生/解析、時間解析、linkify 跳脫、Basic 認證解析。
4. **MCP 煙霧測試**（免真帳密，需 `pip install "mcp[cli]" caldav icalendar requests`）：
   `python3 tests/smoke_mcp.py` → 自動啟動 server，驗證 stdio 與 HTTP 兩種模式的
   工具呼叫、無/壞 Authorization 拒絕、假憑證 pass-through、錯誤後 server 存活。
5. **使用者腳本即時測試**：在「會議排程」頁開啟事件編輯、切到「與會者」頁籤，
   展開一個小部門，看與會者是否正確加入（不要按儲存即無副作用）。
6. **真實整合測試**：用測試行事曆 book 一筆給自己，確認收得到；再小範圍找一位同事驗證群組通知。

> 測試分工是刻意的：離線測「拿到回應後我方邏輯怎麼處理」（尤其實機難觸發的錯誤分支），
> 實機測「伺服器到底怎麼回應」。用猜的 mock 去測後者，只會測到自己的假設。

## 關鍵限制（務必知道）
- **登入是 SAML SSO** → 獨立 CLI 無法自動登入通訊錄；群組功能最穩的是使用者腳本（沿用瀏覽器登入）。
- **CalDAV 無排程** → 與會者不會由 CalDAV 自動通知；通知走原生流程（使用者腳本）或 .ics 邀請信。
- **「按接受」無法省略** → 那是行事曆邀請的正常確認；腳本的價值在確保每個人都「收到」。
- **CORS** → 純網頁跨站打 mail.gss.com.tw 會被擋；使用者腳本同源運作故無此問題。
