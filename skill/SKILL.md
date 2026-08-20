---
name: m2k-calendar
description: >
  查詢與操作公司 m2k（Openfind Mail2000）行事曆。當使用者要看自己的行程、
  未來幾天的會議、查某段期間的行程、建立/預約會議、產生行事曆看板、
  用通訊錄展開部門成員排會議、或想看他人/多人/公用行事曆時使用。
  關鍵字：m2k、Mail2000、行事曆、行程、會議、排程、預約、agenda、book、
  看板、CalDAV、部門、與會者、開會。
---

# m2k 行事曆 skill

GSS 的 m2k（Mail2000）行事曆工具組。**先依情境選工具**：

| 使用者想做 | 用什麼 |
|---|---|
| 查自己行程 / 建會議 / 改會議 / 刪會議（Claude 直接做） | MCP 工具：`agenda` / `list_events` / `book` / `update_event` / `delete_event` / `list_calendars` |
| 查同事分享給你的行事曆（「bear 這週有什麼會」） | `agenda` / `list_events` 帶 `person`（模糊名字或完整 email；對方需先在 webmail 分享，未分享會回提示） |
| 關鍵字搜會議（標題/地點/描述） | MCP 工具：`search_events` |
| 找自己的空檔（「明天哪裡有空 1 小時」） | MCP 工具：`find_free_slots` |
| 模糊人名查 email（「把 pekka 加進會議」） | MCP 工具：`find_person`（行事曆歷史＋信件往來；設 M2K_DIRECTORY_FILE 通訊錄匯出檔可涵蓋全公司） |
| 模糊群組名展開成員排會議（「約某某部門開會」） | MCP 工具：`find_group`（優先讀公司通訊錄的**正式部門群組**——CardDAV，用應用程式專用密碼即可；查無部門才退回你參與過的定期會議。多候選/名單要向使用者確認後再 book） |
| 建重複會議 / 加提醒 | `book` 的 `repeat`（daily/weekly/monthly）+ `repeat_until`、`reminder_minutes` |
| 互動行事曆畫面（週/月檢視、UI 上直接增改刪、拖曳改時間） | MCP 工具：`show_calendar`（支援 MCP Apps 的客戶端會渲染 UI；agenda/list_events/book/update_event/respond_event/delete_event 也會帶出同一個畫面） |
| 回覆會議邀請（接受/暫定/拒絕，只改自己日曆） | MCP 工具：`respond_event` |
| 查自己行程 / 建會議（終端機） | `python3 src/m2kcal.py ...` |
| 產生看板 HTML（每天一欄） | `python3 src/m2kcal.py board --days 7` |
| 展開部門成員、群組排會議＋寄通知信 | webmail 腳本 `userscripts/m2k-group-book.user.js` |
| 看他人 / 多人 / 公用行事曆合併看板 | webmail 腳本 `userscripts/m2k-multi-calendar-board.user.js` |

## 認證（CLI / MCP 共用）

- 走 CalDAV + **應用程式專用密碼**（公司是 SAML SSO，一般密碼不能用；到 webmail 設定產生）。
- 憑證放環境變數或專案根目錄 `.env`：`M2K_URL`（已內建預設）、`M2K_USER`、`M2K_PASS`。
- webmail 使用者腳本不需憑證，同源沿用瀏覽器登入。

## CLI 快速參考（src/m2kcal.py）

- 列日曆：`python3 src/m2kcal.py cals`
- 未來 N 天（依天分組）：`python3 src/m2kcal.py agenda --days 7`
- 指定期間：`python3 src/m2kcal.py list --start 2026-07-01 --end 2026-07-31`
- 建立會議：`python3 src/m2kcal.py book --title "週會" --start "2026-07-10 14:00" --end "2026-07-10 15:00" [--location ...] [--attendee a@gss.com.tw]`
- 看板 HTML：`python3 src/m2kcal.py board --days 7`
- 除錯：`raw`（印現有事件原始 ICS）、`diag`（權限/端點診斷）

## MCP server（src/m2k_mcp_server.py）

提供工具 `list_calendars` / `agenda` / `list_events` / `book` / `update_event`
（修改既有會議：標題/時間/地點/描述/增減與會者，uid 取自查詢輸出的 `id:` 欄位）；
設定範例見 README「四、MCP server」。
建立/修改/刪除只作用於自己的日曆；查詢除自己外，也能看同事「已分享給你」的
日曆（`agenda`/`list_events` 帶 `person`，走 CalDAV，未分享則讀不到）。
三種模式：stdio（本機、環境變數憑證，預設）、`--http`（公用部署，每請求帶
`Authorization: Basic`）、`--oauth`（claude.ai Connectors／手機 app，OAuth 2.1 +
無狀態加密 token）。後兩者伺服器都不保存帳密。

## 行為須知（避免誤判結果）

- book / update_event 時 Mail2000 的 PUT 常回 500 但**其實已寫入**；工具會自動 GET 驗證，以驗證結果為準。
- 時間輸入格式 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DD`（台北時間）；ICS 內部用 TZID=Asia/Taipei + VTIMEZONE。
- CalDAV 無排程：`--attendee` 只寫入事件、**不會自動寄邀請信**；要通知請走 group-book 腳本的原生流程。
- 可預期錯誤（帳密錯、時間格式錯、找不到日曆）會回「錯誤：...」訊息，MCP server 不會因此中斷。
- 重複會議（RRULE）：`update_event` / `delete_event` 會動到**整個系列**（無單次例外支援）。
- 使用者給模糊人名時，先用 `find_person` 查 email；**多個候選或查無時必須向使用者確認，
  絕不自行猜測 email**。資料源是行事曆歷史，沒開過會的人查不到（公司通訊錄需 webmail session）。
- book/update 改時間會附「⚠ 與現有行程重疊」警告；剛建立幾秒內的事件可能因伺服器索引延遲漏報。

## 已知限制

- `find_group` 優先用 CardDAV 讀公司通訊錄的正式部門群組（應用程式專用密碼即可，需部署設
  `M2K_ADBID`）；未設或查無部門時退回「你參與過的定期會議與會者」（名單可能略舊，book 前
  讓使用者確認）。
- 群組信箱（部門名小寫@網域）只是**一個收件位址、不會展開成員**；要每人收到邀請並能回覆出席，
  必須把個別成員放進 attendees。`find_group` 會一併列出群組信箱並標註是否確實存在，可
  「群組信箱＋個別成員」一起帶入。純郵件群組（distribution list）本身仍無法展開成員。
- 看他人行事曆需對方先在 webmail 把行事曆分享給你；分享後即可用 `person` 查完整行程，
  未分享則無法查（伺服器回 404）。沒有 API 能列出「誰分享給我」，需自己知道對方帳號。
- 使用者腳本必須裝在 webmail（Tampermonkey），且新版 Chrome 需開「允許使用者指令碼」。
