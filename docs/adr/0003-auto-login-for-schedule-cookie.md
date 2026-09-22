---
status: accepted
---

# MCP 用 CalDAV 帳密自動換 webmail session，取代手動貼 Cookie

ADR-0002 讓查同事行程改用 webmail Cookie 透傳，但要使用者手動從瀏覽器複製、且短效常過期。
實測發現：CalDAV 用的那組帳號＋應用程式專用密碼，純 POST 到 `/cgi-bin/login`
（不需 challenge-response）即可換到 webmail session（`key` cookie），該 cookie 打排程端點正常。
因此改為：查同事行程時，若使用者沒有明確提供 Cookie，伺服器就用該請求的 CalDAV 帳密
（stdio 的 M2K_USER/M2K_PASS、HTTP 的 Basic、OAuth token 內的憑證）自動換一個，
記憶體快取約 10 分鐘、過期自動重換，不落地。手動 Cookie（M2K_COOKIE / X-M2K-Cookie /
登入頁欄位）保留為覆蓋用。

安全影響：不擴大受攻擊面。用的仍是原本就交給伺服器的那組單一用途、可撤銷的應用程式專用密碼，
不是網域主密碼；换到的 session 與該密碼同壽命，撤銷應用程式專用密碼即全部失效。

## Considered Options

- 維持手動貼 Cookie（ADR-0002）：可行但每隔數小時要重貼，體驗差。
- 用網域主密碼走 SAML／一般登入自動換：需要主密碼進伺服器、且 IdP（Microsoft Entra）
  有 MFA，等於繞過帳號安全機制，受攻擊面遠大於應用程式專用密碼，不採用。
