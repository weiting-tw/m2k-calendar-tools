#!/usr/bin/env python3
"""
m2k OAuth bridge — 讓 claude.ai Connectors（手機/網頁版）能連 m2k MCP server。

設計：無狀態加密 token（伺服器不保存任何憑證）
  1. 用戶端走標準 OAuth 2.1（動態註冊 + PKCE，由 mcp SDK 處理端點）。
  2. /authorize 會把使用者導到本模組的 /login 頁，輸入 m2k 帳號＋
     應用程式專用密碼；bridge 先打一次 CalDAV 驗證。
  3. 驗證通過後，把憑證用伺服器金鑰 AES-GCM 加密封進 access/refresh
     token。之後每個 MCP 請求由 token 解密取回憑證，pass-through 給
     CalDAV。伺服器端沒有憑證資料庫；撤銷＝使用者撤掉應用程式專用密碼。

金鑰：環境變數 M2K_BRIDGE_KEY（urlsafe base64 的 32 bytes）優先；
     否則用專案根目錄 .bridge-key（首次啟動自動產生，chmod 600）。
     換金鑰＝所有已發 token 立即失效。

需要：pip install cryptography（其餘同 MCP server）。
"""
import json
import os
import secrets
import time
from urllib.parse import urlencode, urlparse, urlunparse

import anyio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

import m2kcal

SCOPE = "m2k"
ACCESS_TTL = 3600            # access token 1 小時
REFRESH_TTL = 30 * 24 * 3600  # refresh token 30 天
TXN_TTL = 600                # /authorize → /login 完成的時限
CODE_TTL = 300               # 授權碼時限
MAX_LOGIN_TRIES = 5          # 同一授權交易的密碼錯誤上限（防透過 /login 暴力猜測）
DEFAULT_DOMAIN = os.environ.get("M2K_DOMAIN", "gss.com.tw")  # 帳號沒打 @ 時自動補

_SEC_HEADERS = {             # /login 頁安全標頭：防點擊劫持、不快取憑證頁
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                               "form-action 'self'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KEY_PATH = os.environ.get("M2K_BRIDGE_KEY_FILE") or os.path.join(ROOT, ".bridge-key")
DEFAULT_CLIENTS_PATH = os.environ.get("M2K_OAUTH_CLIENTS") or os.path.join(ROOT, ".oauth-clients.json")


# ---------- token 加解密（憑證只存在於 token 密文內） ----------
class TokenCrypto:
    _AAD = b"m2k-bridge-v1"

    def __init__(self, key: bytes):
        self._aead = AESGCM(key)

    @staticmethod
    def load_key(path=DEFAULT_KEY_PATH) -> bytes:
        import base64
        env = os.environ.get("M2K_BRIDGE_KEY")
        if env:
            return base64.urlsafe_b64decode(env)
        if os.path.isfile(path):
            with open(path) as f:
                return base64.urlsafe_b64decode(f.read().strip())
        key = AESGCM.generate_key(bit_length=256)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.urlsafe_b64encode(key).decode())
        return key

    def seal(self, payload: dict) -> str:
        import base64
        nonce = secrets.token_bytes(12)
        ct = self._aead.encrypt(nonce, json.dumps(payload).encode("utf-8"), self._AAD)
        return "m2k1." + base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")

    def open(self, token: str) -> dict | None:
        """解不開/被竄改/格式錯 → None（不丟例外，交由呼叫端視為無效 token）。"""
        import base64
        if not isinstance(token, str) or not token.startswith("m2k1."):
            return None
        body = token[5:]
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            data = self._aead.decrypt(raw[:12], raw[12:], self._AAD)
            return json.loads(data)
        except Exception:
            return None


# ---------- 帶憑證的 token 模型（只在伺服器行程內，不外露） ----------
class CredAuthorizationCode(AuthorizationCode):
    m2k_user: str = ""
    m2k_pass: str = ""


class CredRefreshToken(RefreshToken):
    m2k_user: str = ""
    m2k_pass: str = ""


class CredAccessToken(AccessToken):
    m2k_user: str = ""
    m2k_pass: str = ""


def _merge_query(url: str, extra: dict) -> str:
    parts = urlparse(url)
    q = parts.query + ("&" if parts.query else "") + urlencode(extra)
    return urlunparse(parts._replace(query=q))


class M2KOAuthProvider:
    """OAuthAuthorizationServerProvider 實作。
    狀態僅有：已註冊 client（存 JSON 檔，重啟保留）、進行中的授權交易
    與授權碼（記憶體、短效）。token 本身無狀態。"""

    def __init__(self, crypto: TokenCrypto, clients_path=DEFAULT_CLIENTS_PATH):
        self.crypto = crypto
        self.clients_path = clients_path
        self._clients: dict[str, dict] = {}
        if os.path.isfile(clients_path):
            try:
                with open(clients_path) as f:
                    self._clients = json.load(f)
            except Exception:
                self._clients = {}
        # txn → {cid, params, exp, tries}；tries 達上限即作廢該授權交易
        self._pending: dict[str, dict] = {}
        self._codes: dict[str, CredAuthorizationCode] = {}

    # --- client 註冊（DCR） ---
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._clients.get(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info.model_dump(mode="json")
        tmp = self.clients_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._clients, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.clients_path)

    # --- 授權流程 ---
    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        self._prune()
        txn = secrets.token_urlsafe(24)
        self._pending[txn] = {"cid": client.client_id, "params": params,
                              "exp": time.time() + TXN_TTL, "tries": 0}
        return "/login?" + urlencode({"txn": txn})

    def _prune(self):
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v["exp"] > now}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at > now}

    def _login_html(self, txn: str, error: str = "") -> str:
        err = f'<p class="err">{error}</p>' if error else ""
        return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>m2k 行事曆授權</title>
<style>body{{font-family:-apple-system,"PingFang TC",sans-serif;background:#f1f5f9;display:flex;
justify-content:center;padding-top:8vh;margin:0}}form{{background:#fff;padding:28px;border-radius:12px;
box-shadow:0 4px 16px rgba(0,0,0,.08);width:320px}}h1{{font-size:17px;margin:0 0 6px}}
p{{font-size:13px;color:#475569;margin:6px 0}}input{{width:100%;box-sizing:border-box;padding:8px;
margin:6px 0;border:1px solid #cbd5e1;border-radius:6px;font-size:14px}}
button{{width:100%;padding:10px;margin-top:10px;background:#2563eb;color:#fff;border:none;
border-radius:8px;font-size:14px;cursor:pointer}}.err{{color:#dc2626}}
.note{{font-size:11px;color:#94a3b8}}</style></head><body>
<form method="post" action="/login">
<h1>連接 m2k 行事曆</h1>
<p>請輸入 Mail2000 帳號與<b>應用程式專用密碼</b>（非登入密碼，於 webmail 設定產生）。</p>
{err}
<input type="hidden" name="txn" value="{txn}">
<input name="user" placeholder="帳號（可省略 @{DEFAULT_DOMAIN}）" autocomplete="username" required>
<input name="password" type="password" placeholder="應用程式專用密碼" autocomplete="current-password" required>
<button type="submit">驗證並授權</button>
<p class="note">憑證只用來即時驗證並加密封入你的存取權杖，伺服器不儲存。
撤銷方式：到 webmail 撤銷該應用程式專用密碼。</p>
</form></body></html>"""

    def _page(self, html: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(html, status_code=status, headers=_SEC_HEADERS)

    async def login_page(self, request: Request) -> Response:
        txn = request.query_params.get("txn", "")
        if txn not in self._pending:
            return self._page(self._login_html("", "此授權連結無效或已過期，請回到用戶端重新連接。"), 400)
        return self._page(self._login_html(txn))

    async def login_submit(self, request: Request) -> Response:
        form = await request.form()
        txn = str(form.get("txn", ""))
        user = str(form.get("user", "")).strip()
        # 應用程式專用密碼不含前後空白；strip 掉複製貼上常見的殘留空白/換行
        pwd = str(form.get("password", "")).strip()
        if "@" not in user:
            user += "@" + DEFAULT_DOMAIN
        entry = self._pending.get(txn)
        if not entry or entry["exp"] < time.time():
            return self._page(self._login_html(
                "", "此授權階段已失效——可能已完成授權（請回應用程式確認），"
                    "或已過期／舊分頁重送。若尚未連上，請回到用戶端重新連接一次。"), 400)
        client_id, params = entry["cid"], entry["params"]

        # 打一次 CalDAV 驗證憑證（blocking → 丟 worker thread）
        auth = (os.environ.get("M2K_URL", m2kcal.DEFAULT_URL), user, pwd)
        try:
            await anyio.to_thread.run_sync(lambda: m2kcal.connect(auth))
        except m2kcal.M2KError:
            entry["tries"] += 1
            if entry["tries"] >= MAX_LOGIN_TRIES:
                del self._pending[txn]
                return self._page(self._login_html(
                    "", "嘗試次數過多，此授權交易已作廢，請回到用戶端重新連接。"), 429)
            return self._page(self._login_html(txn, "驗證失敗：帳號或應用程式專用密碼不正確。"), 401)

        del self._pending[txn]
        code = secrets.token_urlsafe(32)
        self._codes[code] = CredAuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=user,
            m2k_user=user,
            m2k_pass=pwd,
        )
        extra = {"code": code}
        if params.state:
            extra["state"] = params.state
        return RedirectResponse(_merge_query(str(params.redirect_uri), extra), status_code=302)

    async def load_authorization_code(self, client, authorization_code: str):
        code = self._codes.get(authorization_code)
        if not code or code.client_id != client.client_id or code.expires_at < time.time():
            return None
        return code

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)  # 一次性
        return self._mint(client.client_id, authorization_code.scopes,
                          authorization_code.m2k_user, authorization_code.m2k_pass)

    # --- token 發行 / 驗證（無狀態） ---
    def _mint(self, client_id: str, scopes: list[str], user: str, pwd: str) -> OAuthToken:
        now = int(time.time())
        base = {"c": client_id, "s": scopes, "u": user, "p": pwd}
        access = self.crypto.seal({**base, "t": "a", "e": now + ACCESS_TTL})
        refresh = self.crypto.seal({**base, "t": "r", "e": now + REFRESH_TTL})
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          scope=" ".join(scopes), refresh_token=refresh)

    def _open_typed(self, token: str, typ: str) -> dict | None:
        d = self.crypto.open(token)
        if not d or d.get("t") != typ or d.get("e", 0) < time.time():
            return None
        return d

    async def load_refresh_token(self, client, refresh_token: str):
        d = self._open_typed(refresh_token, "r")
        if not d or d["c"] != client.client_id:
            return None
        return CredRefreshToken(token=refresh_token, client_id=d["c"], scopes=d["s"],
                                expires_at=d["e"], subject=d["u"],
                                m2k_user=d["u"], m2k_pass=d["p"])

    async def exchange_refresh_token(self, client, refresh_token, scopes: list[str]) -> OAuthToken:
        if scopes and not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "要求的 scope 超出原授權範圍")
        return self._mint(client.client_id, scopes or refresh_token.scopes,
                          refresh_token.m2k_user, refresh_token.m2k_pass)

    async def load_access_token(self, token: str):
        d = self._open_typed(token, "a")
        if not d:
            return None
        return CredAccessToken(token=token, client_id=d["c"], scopes=d["s"],
                               expires_at=d["e"], subject=d["u"],
                               m2k_user=d["u"], m2k_pass=d["p"])


def create(issuer: str, key_path=DEFAULT_KEY_PATH,
           clients_path=DEFAULT_CLIENTS_PATH) -> tuple[M2KOAuthProvider, AuthSettings]:
    """建立 provider 與 AuthSettings。issuer 需為用戶端可達的對外網址。"""
    provider = M2KOAuthProvider(TokenCrypto(TokenCrypto.load_key(key_path)), clients_path)
    issuer = issuer.rstrip("/")
    settings = AuthSettings(
        issuer_url=AnyHttpUrl(issuer),
        resource_server_url=AnyHttpUrl(issuer + "/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        required_scopes=[SCOPE],
    )
    return provider, settings


def add_login_routes(server, provider: M2KOAuthProvider) -> None:
    server.custom_route("/login", methods=["GET"])(provider.login_page)
    server.custom_route("/login", methods=["POST"])(provider.login_submit)
