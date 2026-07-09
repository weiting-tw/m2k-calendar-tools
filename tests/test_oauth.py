#!/usr/bin/env python3
"""OAuth bridge 離線單元測試 — token 加解密與無狀態驗證邏輯。
需要 mcp + cryptography（用專案 venv 跑）:  .venv/bin/python tests/test_oauth.py
不連網、不需帳密。"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import AnyUrl

from mcp.shared.auth import OAuthClientInformationFull

import m2k_oauth as mo


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name


run = asyncio.run

# 1) TokenCrypto: 回還、壞金鑰、竄改
key = AESGCM.generate_key(bit_length=256)
crypto = mo.TokenCrypto(key)
sealed = crypto.seal({"a": 1, "u": "x@example.com"})
check("seal/open 回還", crypto.open(sealed) == {"a": 1, "u": "x@example.com"})
check("token 有版本前綴", sealed.startswith("m2k1."))
other = mo.TokenCrypto(AESGCM.generate_key(bit_length=256))
check("換金鑰解不開", other.open(sealed) is None)
check("竄改解不開", crypto.open(sealed[:-4] + "AAAA") is None)
check("垃圾輸入回 None", crypto.open("m2k1.zzz") is None and crypto.open("") is None
      and crypto.open("Bearer x") is None)

# 2) provider 無狀態 token：型別、過期、client 綁定
provider = mo.M2KOAuthProvider(crypto, clients_path="/nonexistent/no.json")
tok = provider._mint("client-1", ["m2k"], "u@example.com", "pw")
at = run(provider.load_access_token(tok.access_token))
check("access token 解出憑證", at is not None and at.m2k_user == "u@example.com" and at.m2k_pass == "pw")
check("access token scope/client", at.scopes == ["m2k"] and at.client_id == "client-1")
check("refresh 不能當 access 用", run(provider.load_access_token(tok.refresh_token)) is None)

client1 = OAuthClientInformationFull(client_id="client-1",
                                     redirect_uris=[AnyUrl("http://127.0.0.1/cb")])
client2 = OAuthClientInformationFull(client_id="client-2",
                                     redirect_uris=[AnyUrl("http://127.0.0.1/cb")])
rt = run(provider.load_refresh_token(client1, tok.refresh_token))
check("refresh token 正常載入", rt is not None and rt.m2k_user == "u@example.com")
check("refresh token 綁定 client", run(provider.load_refresh_token(client2, tok.refresh_token)) is None)
check("access 不能當 refresh 用", run(provider.load_refresh_token(client1, tok.access_token)) is None)

new = run(provider.exchange_refresh_token(client1, rt, []))
check("refresh 換發新 access", run(provider.load_access_token(new.access_token)).m2k_user == "u@example.com")

expired = crypto.seal({"t": "a", "c": "client-1", "s": ["m2k"],
                       "u": "u@example.com", "p": "pw", "e": int(time.time()) - 10})
check("過期 access 拒絕", run(provider.load_access_token(expired)) is None)

# 3) 授權碼一次性與過期
params = dict(scopes=["m2k"], client_id="client-1", code_challenge="c",
              redirect_uri=AnyUrl("http://127.0.0.1/cb"),
              redirect_uri_provided_explicitly=True,
              m2k_user="u@example.com", m2k_pass="pw")
code = mo.CredAuthorizationCode(code="abc", expires_at=time.time() + 60, **params)
provider._codes["abc"] = code
check("授權碼載入", run(provider.load_authorization_code(client1, "abc")) is not None)
check("授權碼綁定 client", run(provider.load_authorization_code(client2, "abc")) is None)
tok2 = run(provider.exchange_authorization_code(client1, code))
check("授權碼換 token", run(provider.load_access_token(tok2.access_token)).m2k_pass == "pw")
check("授權碼一次性", run(provider.load_authorization_code(client1, "abc")) is None)
stale = mo.CredAuthorizationCode(code="old", expires_at=time.time() - 1, **params)
provider._codes["old"] = stale
check("過期授權碼拒絕", run(provider.load_authorization_code(client1, "old")) is None)

# 4) 已完成的 txn：舊分頁 GET /login 應顯示正向完成頁，而非「失效」
from starlette.requests import Request


def mk_get(query):
    return Request({"type": "http", "method": "GET",
                    "query_string": query.encode(), "headers": []})


provider._done["donetxn"] = time.time() + 60
done_resp = run(provider.login_page(mk_get("txn=donetxn")))
body = bytes(done_resp.body).decode()
check("已完成 txn → 200 完成頁", done_resp.status_code == 200 and "授權完成" in body)
miss_resp = run(provider.login_page(mk_get("txn=nope")))
check("未知 txn → 400 失效頁", miss_resp.status_code == 400 and "重新連接" in bytes(miss_resp.body).decode())
check("完成頁不含輸入框", "password" not in body)

# 5) DCR 上限：超過 MAX_CLIENTS 淘汰最舊註冊的 client
import tempfile

with tempfile.TemporaryDirectory() as td:
    p2 = mo.M2KOAuthProvider(crypto, clients_path=os.path.join(td, "clients.json"))
    orig_max = mo.MAX_CLIENTS
    mo.MAX_CLIENTS = 5
    try:
        for i in range(8):
            run(p2.register_client(OAuthClientInformationFull(
                client_id=f"c{i}", client_id_issued_at=1000 + i,
                redirect_uris=[AnyUrl("http://127.0.0.1/cb")])))
        check("DCR 上限生效", len(p2._clients) == 5)
        check("DCR 淘汰最舊", "c0" not in p2._clients and "c2" not in p2._clients
              and "c3" in p2._clients and "c7" in p2._clients)
    finally:
        mo.MAX_CLIENTS = orig_max

# 6) 聯絡人快取淘汰：先清過期、超上限淘汰最舊
import m2k_mcp_server as srv

orig_cap = srv._CACHE_MAX_USERS
srv._CACHE_MAX_USERS = 3
try:
    c = {}
    srv._cache_put(c, "old", {})
    c["old"] = (time.time() - srv._CONTACTS_TTL - 1, {})  # 弄成過期
    srv._cache_put(c, "a", {})
    check("快取清過期", "old" not in c)
    srv._cache_put(c, "b", {})
    srv._cache_put(c, "c", {})
    srv._cache_put(c, "d", {})  # 超過上限 3 → 淘汰最舊的 a
    check("快取上限淘汰最舊", len(c) == 3 and "a" not in c and "d" in c)
finally:
    srv._CACHE_MAX_USERS = orig_cap

# 7) 登入失敗節流：達上限回 429、不再打上游；失敗寫入 fail2ban 日誌
from urllib.parse import urlencode

from mcp.server.auth.provider import AuthorizationParams

calls = {"n": 0}


def _boom(auth):
    calls["n"] += 1
    raise mo.m2kcal.M2KError("bad password")


def mk_post(txn, ip="9.9.9.9"):
    body = urlencode({"txn": txn, "user": "x", "password": "y"}).encode()
    scope = {"type": "http", "method": "POST",
             "headers": [(b"content-type", b"application/x-www-form-urlencoded"),
                         (b"content-length", str(len(body)).encode())],
             "query_string": b"", "client": (ip, 1)}
    sent = {"v": False}

    async def receive():
        if sent["v"]:
            return {"type": "http.disconnect"}
        sent["v"] = True
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


client_ok = OAuthClientInformationFull(client_id="client-1",
                                       redirect_uris=[AnyUrl("http://127.0.0.1/cb")])


async def new_txn(p):
    params_obj = AuthorizationParams(
        state=None, scopes=["m2k"], code_challenge="c",
        redirect_uri=AnyUrl("http://127.0.0.1/cb"),
        redirect_uri_provided_explicitly=True, resource=None)
    loc = await p.authorize(client_ok, params_obj)
    return loc.split("txn=")[1]


orig_connect = mo.m2kcal.connect
mo.m2kcal.connect = _boom
with tempfile.TemporaryDirectory() as td:
    os.environ["M2K_AUTH_LOG"] = os.path.join(td, "auth.log")
    try:
        p3 = mo.M2KOAuthProvider(crypto, clients_path=os.path.join(td, "c.json"))
        statuses = []
        for _ in range(mo.FAIL_LIMIT_IP + 2):
            resp = run(p3.login_submit(mk_post(run(new_txn(p3)))))
            statuses.append(resp.status_code)
        check("節流前失敗回 401", statuses[0] == 401)
        check("達上限後回 429", statuses[-1] == 429 and statuses[-2] == 429)
        check("429 後不再打上游", calls["n"] == mo.FAIL_LIMIT_IP)
        with open(os.environ["M2K_AUTH_LOG"]) as f:
            log = f.read()
        check("失敗寫入 fail2ban 日誌", log.count("m2k-login-fail ip=9.9.9.9") == mo.FAIL_LIMIT_IP)
        # 不同 IP 不受同一 IP 的節流影響（X-Forwarded-For 取第一個）
        resp2 = run(p3.login_submit(mk_post(run(new_txn(p3)), ip="8.8.8.8")))
        check("其他 IP 不被連坐", resp2.status_code == 401)
        # 日誌自我輪替：超過門檻換檔到 .1，不會無限成長
        logp = os.environ["M2K_AUTH_LOG"]
        orig_max = mo.AUTH_LOG_MAX
        mo.AUTH_LOG_MAX = 10  # 壓低門檻觸發輪替
        try:
            p3._record_fail("7.7.7.7")
            check("日誌輪替產生 .1", os.path.isfile(logp + ".1"))
            with open(logp) as f:
                check("輪替後新檔只剩最新一筆", f.read().count("m2k-login-fail") == 1)
        finally:
            mo.AUTH_LOG_MAX = orig_max
    finally:
        mo.m2kcal.connect = orig_connect
        del os.environ["M2K_AUTH_LOG"]

print("\n全部通過 ✅")
