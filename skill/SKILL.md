---
name: m2k-calendar
description: >
  查詢與操作公司 m2k（Openfind Mail2000）行事曆。當使用者要看自己的行程、
  未來幾天的會議、建立/預約會議、或產生行事曆看板時使用。也涵蓋用通訊錄群組
  展開成員排會議、以及多人/他人行事曆檢視的說明。關鍵字：m2k、Mail2000、行事曆、
  會議、排程、agenda、book、看板、CalDAV。
---

# m2k 行事曆 skill

協助操作 GSS 的 m2k（Mail2000）行事曆。工具分兩類：**CLI（走 CalDAV）** 與
**webmail 使用者腳本（走登入 session）**。

## 認證
- CLI/MCP 走 CalDAV，用 **應用程式專用密碼**（因公司是 SAML SSO，一般密碼不能用）。
- 憑證放環境變數或 `.env`：`M2K_URL`、`M2K_USER`、`M2K_PASS`。

## 查詢 / 建立會議（CLI：m2kcal.py）
- 列日曆：`python3 m2kcal.py cals`
- 未來 N 天（依天分組）：`python3 m2kcal.py agenda --days 7`
- 指定期間：`python3 m2kcal.py list --start 2026-07-01 --end 2026-07-31`
- 建立會議：`python3 m2kcal.py book --title "週會" --start "2026-07-10 14:00" --end "2026-07-10 15:00" [--location ...] [--attendee a@example.com]`
- 看板 HTML：`python3 m2kcal.py board --days 7`
- 重點：ICS 需 TZID=Asia/Taipei + VTIMEZONE；book 後會自動 GET 驗證（Mail2000 PUT 常回 500 但其實已建立）。

## 讓 Claude 直接操作（MCP：m2k_mcp_server.py）
提供工具 `list_calendars` / `agenda` / `list_events` / `book`。設定見該檔開頭。
MCP 只能查自己的日曆與建立會議（他人日曆需 webmail session）。

## 群組排會議 / 多人看板（webmail 使用者腳本）
- `m2k-group-book.user.js`：在「會議排程」頁搜人(autocomplete)/展開部門/貼 email → 填原生表單一鍵建立。
- `m2k-multi-calendar-board.user.js`：合併檢視自己＋他人＋公用行事曆成看板。
- 這些必須裝在 webmail（Tampermonkey），同源沿用登入。

## 已知限制
- 純郵件群組（distribution list）無法展開成員（資料源不開放）。
- 看他人行事曆需對方先分享/授權；無法用 email 臨時查完整內容（僅 free/busy）。
