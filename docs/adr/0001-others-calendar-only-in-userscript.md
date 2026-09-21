---
status: superseded by ADR-0002
---

# 他人行事曆只在 webmail 使用者腳本實作，CLI/MCP 明講不支援

Mail2000 唯一能不經對方分享就查到任意同事完整行程的介面，是排程頁用的
`/cgi-bin/cal/calsrv/schedule/{email}/instances`。它只吃 webmail 登入 cookie
（CalDAV 的 Basic 認證直打會被 nginx 回 410），而站台登入是 SAML SSO，獨立程式
無法用帳密換到 cookie；CalDAV 端的 RFC 6638 free-busy（schedule-outbox）在此
站台也是 404。因此決定：他人行事曆與共同空檔只在瀏覽器內的使用者腳本實作，
CLI 與 MCP 的 `find_free_slots` 帶 attendees 時直接回「此站台不支援，請用
webmail 面板」，不再嘗試 schedule-outbox。

## Considered Options

- 讓 MCP 收使用者貼上的 webmail cookie：session 短效、每個模組 token 不同，
  公用 HTTP 模式下等於把 webmail 全權限交給伺服器，風險遠高於應用程式密碼。
  若日後重評，前提是 cookie 只在 client 端持有、伺服器不儲存。
- 要求對方先分享行事曆：需對方或管理者操作，無法臨時查，不符合排會議情境。
