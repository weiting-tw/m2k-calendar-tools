---
status: superseded by ADR-0003
---

# MCP 查他人行事曆採 webmail Cookie 透傳，伺服器不持有

ADR-0001 把他人行事曆限制在使用者腳本，理由是排程端點只吃 webmail cookie 而
MCP 拿不到。實際使用時 MCP 端仍不斷被問「某同事下週有什麼會」，所以改為：
使用者自行從已登入的瀏覽器複製 Cookie，交給 client 每次請求帶上
（stdio 用環境變數 `M2K_COOKIE`、HTTP 用 `X-M2K-Cookie` 標頭、OAuth 在登入頁貼上並
以伺服器金鑰加密封進 token）。伺服器沿用既有的無狀態設計：用完即丟，不落地、不快取。
接受的代價：Cookie 短效、過期要重貼；webmail cookie 權限大於應用程式專用密碼，
在公用 HTTP/OAuth 部署下每個請求都經過伺服器，使用者必須信任該部署。

## Considered Options

- 伺服器用帳密自動登入換 cookie：登入是 SAML SSO，做不到。
- 伺服器保存 cookie 供多次使用：違反「伺服器不存憑證」的既有原則，也讓外洩影響擴大。
