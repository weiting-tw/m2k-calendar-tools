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
| 查自己行程 / 建會議（Claude 直接做） | MCP 工具：`agenda` / `list_events` / `book` / `list_calendars` |
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

提供工具 `list_calendars` / `agenda` / `list_events` / `book`；設定範例見 README「四、MCP server」。
只能查自己的日曆與建立會議（他人日曆需 webmail session，MCP 拿不到）。
三種模式：stdio（本機、環境變數憑證，預設）、`--http`（公用部署，每請求帶
`Authorization: Basic`）、`--oauth`（claude.ai Connectors／手機 app，OAuth 2.1 +
無狀態加密 token）。後兩者伺服器都不保存帳密。

## 行為須知（避免誤判結果）

- book 時 Mail2000 的 PUT 常回 500 但**其實已建立**；工具會自動 GET 驗證，以驗證結果為準。
- 時間輸入格式 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DD`（台北時間）；ICS 內部用 TZID=Asia/Taipei + VTIMEZONE。
- CalDAV 無排程：`--attendee` 只寫入事件、**不會自動寄邀請信**；要通知請走 group-book 腳本的原生流程。
- 可預期錯誤（帳密錯、時間格式錯、找不到日曆）會回「錯誤：...」訊息，MCP server 不會因此中斷。

## 已知限制

- 純郵件群組（distribution list，如 xxx@gss.com.tw）無法展開成員（資料源不開放）。
- 看他人行事曆需對方先分享/授權；跨使用者只能查 free/busy 忙碌時段。
- 使用者腳本必須裝在 webmail（Tampermonkey），且新版 Chrome 需開「允許使用者指令碼」。
