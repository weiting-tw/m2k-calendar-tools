# m2k 行事曆工具組 — 進度與待辦

最後更新：2026-07-07（程式碼修正 + 目錄結構調整）

## 2026-07-07 修正紀錄
- `m2kcal.py` 可預期錯誤改丟 `M2KError`（原 `sys.exit` 會殺掉 MCP server）；CLI 於 `main()` 統一接住。
- 抽出 `put_and_verify()`，CLI 與 MCP 的 book 共用 PUT+GET 驗證邏輯；移除 `cmd_book` 重複輸出與死碼（`fmt_event`、未用 import）。
- `m2kgroup.py` 分頁參數修正為已實測的 `pageno`、端點改 `adb2main_mds`（原 `page` 參數會靜默截斷成 25 人）。
- `m2k-group-book.user.js` 補 HTML 跳脫（通訊錄姓名/部門塞 innerHTML 前 escape），v0.4.1。
- 測試補 `parse_ics`/`_linkify`/`_rrule_text`/`M2KError`，共 30 項。
- 目錄結構調整：`src/`、`tests/`、`userscripts/`、`docs/`；`README-使用說明.md` 改名 `README.md`；移除已完成任務的 `commit.sh`。

## 專案目標
把難用的公司 m2k 信箱行事曆（Openfind Mail2000）變好用：CLI 查詢/建立會議、
webmail 使用者腳本做群組排會議與多人看板。

## 技術發現摘要（都已實測）
- **m2k = Openfind Mail2000**，主機 `mail.gss.com.tw`,傳統 cgi-bin 架構。
- **登入是 SAML SSO**（`/cgi-bin/saml_login`）→ 獨立程式無法用帳密自動登入。
- **CalDAV（SabreDAV）**:`https://mail.gss.com.tw/cgi-bin/cal/caldav/`,HTTP Basic + **應用程式專用密碼**。
  - 讀（PROPFIND/REPORT）✓;寫（PUT）會回 500「system error」**但其實有建立成功**（存檔後的通知步驟報錯）→ CLI 已改為 PUT 後 GET 驗證。
  - ICS 需 **TZID=Asia/Taipei + VTIMEZONE + 本地時間**，純 UTC/浮動時間會被拒。
  - CalDAV 家目錄**只含自己的日曆**（他人日曆不在其中）。
- **通訊錄（adb2）**:同源、只靠 cookie。
  - 群組/部門展開：`/cgi-bin/adb2main_mds`(`command=list`,分頁參數 `pageno`)。
  - 部門樹：`/cgi-bin/adb2tree_mds`(`command=expand`,需帶 `workingabid`)，路徑如 `/ORG_DIR1/UNIT1`。
  - 人員搜尋：`/cgi-bin/adb2search_mds`(`command=mdssearch`)。
  - **純郵件群組（distribution list，如 group_list_a@）無法展開**——成員在郵件伺服器端，通訊錄查不到。
- **行事曆 feeds API（看多人/他人）**:`/cgi-bin/cal/calsrv/feeds/default/{default|subscribed/N|public/<id>}/events/instances/?starttime=&endtime=`(epoch 秒)。
  - 回傳 `{instances:[{summary,dtstart,dtend,organizer,attendee,...}]}`;同源、沿用登入。
  - 清單：`/cgi-bin/cal/calsrv/feeds/default/{type}/` → `{calendars:[{id,display_name,feeds,color,...}]}`。
- **分享是「擁有者授權制」**:你能看他人日曆，是對方/管理者發佈授權給你（自動進 subscribed）;**觀看端無法自行訂閱、也無法用 email 直接查他人完整內容**。唯一跨使用者可查的是 **free/busy 忙碌時段**。
- **加與會者、通知**:CalDAV 無排程（`schedule-outbox` 404）→ 與會者只記錄、不自動通知。要通知走 webmail 原生「會議排程」流程（有「寄送通知信」）。

## 交付物（檔案）
| 檔案 | 用途 | 狀態 |
|---|---|---|
| `src/m2kcal.py` | 行事曆 CLI（CalDAV）:cals/agenda/list/**board**/book/raw/diag | ✅ 真實環境驗證（讀+寫+看板） |
| `src/m2kgroup.py` | 通訊錄群組展開 CLI（需帶 session token） | ✅ 語法+離線測試（實務被腳本取代） |
| `userscripts/m2k-group-book.user.js` | webmail 使用者腳本：搜人(autocomplete)/搜部門展開/貼email → 填原生排程表單一鍵建立 | ✅ 對真實頁面實測 |
| `userscripts/m2k-multi-calendar-board.user.js` | webmail 使用者腳本：多人/他人/公用行事曆合併看板 | ✅ 資料流程+渲染實測 |
| `src/m2k_mcp_server.py` | MCP server：讓 Claude 直接查行程/建立會議（CalDAV） | ✅ 語法驗證（需 `pip install mcp[cli]`） |
| `skill/SKILL.md` | m2k-calendar skill（供 Settings ▸ Capabilities 匯入） | ✅ |
| `tests/test_m2k.py` | 離線單元測試 | ✅ 30 項全過 |
| `README.md` | 使用說明 | — |
| `docs/m2k-calendar-cli-feasibility.md` | 完整技術分析報告 | — |
| `.env.example` | 環境變數範本（複製成 `.env`） | — |

## 測試狀態
- 離線單元測試 `python3 tests/test_m2k.py` → 30 項全過（ICS 產生/解析/TZID/時間解析/linkify/rrule/通訊錄解析）。
- 真實環境：`cals`✓、`agenda` 讀取✓、`book` 建立（GET 驗證）✓、部門展開 UNIT1✓、autocomplete 資料✓、feeds 讀他人（colleague）事件✓、看板渲染✓。
- CLI `board` 用你真實資料開瀏覽器：功能已具備，建議再自行開一次確認畫面。

## 已知限制
1. **book 會回 500 但實際成功** → 已用 GET 驗證繞過（非 bug，是 Mail2000 後端通知步驟）。
2. **純郵件群組展不開**（group_list_a 這類）——資料源不開放，無解。
3. **看他人行事曆需對方先分享**;無法用 email 臨時查完整內容（僅 free/busy 可跨查）。
4. **多層部門**目前抓「該部門直接成員」;更深子部門需個別搜。
5. **GIL 警告**:Python 3.13t + lxml 的環境警告，加 `PYTHON_GIL=0` 可消，無害。

## 待辦（TODO）
- [ ] 決定並實作跨使用者查詢：**輸入 email 看 free/busy 忙碌時段**（唯一可行的臨時查）。
- [ ] 「一鍵發佈我的行事曆」給某人/群組（用 `pubCal*` 端點，分享方向）。
- [ ] 把兩支使用者腳本（群組排會議 + 多人看板）**合併成一支**、單一入口。
- [ ] 群組 book 通知：展開成員後**逐一寄 .ics 邀請信**（或走原生排程流程）。
- [ ] CLI 加 `delete` 指令清理測試/舊事件。
- [ ] 多層部門**遞迴展開**子部門成員（會多打幾支請求）。
- [x] 把 CalDAV 查詢/book 包成 **MCP**(`m2k_mcp_server.py`)。
- [x] 產出 **Skill**(`skill/SKILL.md`)。
- [ ] （選）腳本內靜音 GIL 警告，免每次加 `PYTHON_GIL=0`。
- [x] MCP 公用化：`--http` 啟動 streamable-http，認證採「每請求帶各自的應用程式專用密碼
      （Basic over HTTPS，pass-through 到 CalDAV）」，伺服器不保存憑證；stdio 模式並存。
      已實測：stdio 與 HTTP 兩模式工具呼叫、無/壞 Authorization 拒絕、假憑證轉拋 CalDAV 401。

## 打包方式
可以直接打包。deliverable 為 `src/`、`tests/`、`userscripts/`、`docs/`、`skill/`
與根目錄的 `README.md`、`PROGRESS.md`、`.env.example`。

**打包前請排除（機密/暫存）**：`.env`(密碼！)、`m2k-board.html`、`m2k-board-sample.html`、
`.env.test`、`__pycache__/`。（`.gitignore` 已涵蓋這些。）

建議用 `git archive` 打包（自動套用 .gitignore 範圍外的追蹤檔案）。
