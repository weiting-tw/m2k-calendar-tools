#!/usr/bin/env python3
"""MCP 煙霧測試 — 實際啟動 server，驗證 stdio 與 streamable-http 兩種模式。

不需要真實帳密：驗證重點是傳輸能通、每請求認證流程正確、
可預期錯誤以「錯誤：...」回覆且 server 不會死。
（上游一律導到本機不存在的埠，假憑證案例連不到上游，會被包成「錯誤：登入失敗」：
  斷言只看這個結果，不對真實 Mail2000 送錯誤密碼。）
走 MCP 連線的案例都用兩種協議時代各跑一次：legacy（initialize 握手，claude.ai
connector、Claude Code 這類 2025 年代的 client）與 2026-07-28（server/discover 協商）。

需求: pip install -r requirements.txt（mcp 2.x；測試 client 用 SDK 附帶的 httpx2）
執行: python3 tests/smoke_mcp.py
"""
import asyncio
import base64
import contextlib
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import time

import httpx2
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
SRV = os.path.join(ROOT, "src", "m2k_mcp_server.py")
PORT = 8971        # 煙霧測試專用埠，避免撞正式部署的 8763
PORT_OAUTH = 8972
PORT_HOST = 8973
ERAS = ("legacy", "2026-07-28")


@contextlib.asynccontextmanager
async def connect(transport, era):
    """依協議時代連上 server，並確認 serverInfo 是本服務的名稱與版號。"""
    from _version import __version__
    if era == "legacy":
        async with transport as (r, w):
            async with ClientSession(r, w) as s:
                info = (await s.initialize()).server_info
                assert (info.name, info.version) == ("m2k-calendar", __version__), info
                yield s
    else:
        # cache=None：每次呼叫都真的打到 server，「錯誤後仍存活」才有意義
        async with Client(transport, mode="auto", cache=None) as c:
            assert c.protocol_version == era, c.protocol_version
            info = c.server_info
            assert info and (info.name, info.version) == ("m2k-calendar", __version__), info
            yield c


@contextlib.asynccontextmanager
async def connect_http(url, era, headers=None):
    # 2.x 的 streamable_http_client 不收 headers，要自帶 httpx2 client（逾時同 SDK 預設）
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=300)) as hc:
        async with connect(streamable_http_client(url, http_client=hc), era) as s:
            yield s


async def test_stdio(era):
    params = StdioServerParameters(
        command=sys.executable, args=[SRV],
        env={**os.environ, "M2K_USER": "fake@example.com", "M2K_PASS": "x"})
    async with connect(stdio_client(params), era) as s:
        print(f"--- stdio [{era}]")
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        assert tools == ["agenda", "book", "calendar_data", "delete_event",
                         "find_free_slots", "find_group", "find_person", "get_event",
                         "list_calendars", "list_events", "list_invitations",
                         "others_agenda", "respond_event", "search_events",
                         "show_calendar", "update_event"], tools
        prompts = sorted(p.name for p in (await s.list_prompts()).prompts)
        assert prompts == ["morning-brief", "reschedule",
                           "schedule-meeting", "weekly-review"], prompts
        pr = await s.get_prompt("schedule-meeting",
                                {"attendees": "a@x.com", "topic": "週會"})
        ptxt = pr.messages[0].content.text
        assert "find_person" in ptxt and "find_free_slots" in ptxt, ptxt[:80]
        print("prompts:", prompts)
        resources = {str(r.uri) for r in (await s.list_resources()).resources}
        assert "m2k://whoami" in resources, resources
        who = await s.read_resource("m2k://whoami")
        assert "fake@example.com" in who.contents[0].text  # stdio 模式回環境變數身分
        print("whoami resource: OK")
        ui_tools = {t.name: (t.meta or {}).get("ui", {})
                    for t in (await s.list_tools()).tools if t.meta}
        uri = ui_tools["show_calendar"]["resourceUri"]
        assert uri.startswith("ui://m2k-calendar/calendar"), uri  # URI 帶內容 hash 版本
        assert ui_tools["calendar_data"]["resourceUri"] == uri
        assert "visibility" not in ui_tools["calendar_data"]  # 不能是 app-only，見 server 註解
        # 只有這兩個掛 UI：host 每呼叫一次掛 UI 的工具就多開一個 app
        assert set(ui_tools) == {"show_calendar", "calendar_data"}, sorted(ui_tools)
        rr = await s.read_resource(uri)
        assert rr.contents[0].mime_type == "text/html;profile=mcp-app"
        print("app ui resource + tool meta: OK")
        print("stdio tools:", tools)
        res = await s.call_tool("list_events", {"start": "亂格式", "end": "2026-07-31"})
        txt = res.content[0].text
        print("stdio bad-date ->", txt[:60])
        assert txt.startswith("錯誤：時間格式看不懂"), txt
        again = await s.list_tools()  # 錯誤後 server 仍活著
        assert len(again.tools) == len(tools)
        print("stdio alive after error: True")


async def test_http(url, era):
    print(f"--- http [{era}]")
    # 1) 無 Authorization → 拒絕（不得回退到環境變數憑證）
    async with connect_http(url, era) as s:
        txt = (await s.call_tool("agenda", {"days": 1})).content[0].text
        print("http no-auth ->", txt[:60])
        assert txt.startswith("錯誤：需要 Authorization"), txt

    # 2) 壞標頭格式 → 拒絕
    async with connect_http(url, era, {"Authorization": "Bearer xyz"}) as s:
        txt = (await s.call_tool("list_calendars", {})).content[0].text
        print("http bad-header ->", txt[:60])
        assert txt.startswith("錯誤：需要 Authorization"), txt

    # 3) 假 Basic 憑證 → pass-through 到 CalDAV 後被拒（或連線失敗），同包成登入失敗
    hdr = {"Authorization": "Basic " + base64.b64encode(b"fake@example.com:wrongpass").decode()}
    async with connect_http(url, era, hdr) as s:
        txt = (await s.call_tool("agenda", {"days": 1})).content[0].text
        print("http fake-auth ->", txt[:80])
        assert txt.startswith("錯誤：登入失敗"), txt


async def test_oauth(base, key_b64):
    redirect = "http://127.0.0.1:19999/callback"
    async with httpx2.AsyncClient() as c:
        # 1) metadata → 動態註冊 → /authorize 轉導到 /login
        meta = (await c.get(f"{base}/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"].rstrip("/") == base, meta
        reg = (await c.post(meta["registration_endpoint"], json={
            "redirect_uris": [redirect], "client_name": "smoke",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
        })).json()
        assert "client_id" in reg, reg
        print("oauth register client_id:", reg["client_id"][:12], "…")

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        r = await c.get(meta["authorization_endpoint"], params={
            "client_id": reg["client_id"], "response_type": "code",
            "redirect_uri": redirect, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "st1", "scope": "m2k",
        }, follow_redirects=False)
        assert r.status_code in (302, 307), (r.status_code, r.text[:200])
        loc = r.headers["location"]
        assert "/login?txn=" in loc, loc
        print("oauth authorize -> redirect", loc[:30], "…")

        # 2) 登入頁可開（含安全標頭）；假憑證被 CalDAV 驗證擋下；連錯 5 次作廢交易
        login = await c.get(base + loc if loc.startswith("/") else loc)
        assert login.status_code == 200 and "應用程式專用密碼" in login.text
        assert login.headers.get("x-frame-options") == "DENY", login.headers
        assert "驗證中，請稍候" in login.text, "登入頁應含 loading 腳本"
        txn = loc.split("txn=")[1]
        for i in range(5):
            # user 不帶 @ → 伺服器自動補預設網域後才驗證
            bad = await c.post(f"{base}/login",
                               data={"txn": txn, "user": "fakeuser", "password": "wrong"})
            expect = 401 if i < 4 else 429
            assert bad.status_code == expect, (i, bad.status_code)
        assert "嘗試次數過多" in bad.text
        gone = await c.get(f"{base}/login?txn={txn}")
        assert gone.status_code == 400, gone.status_code
        print("oauth login fake-cred -> 401×4、第5次 429 作廢交易")

        # 3) 無 token 打 MCP endpoint → HTTP 401（OAuth 模式由 middleware 擋）
        r2 = await c.post(f"{base}/mcp", json={})
        assert r2.status_code == 401, r2.status_code
        print("oauth mcp no-token -> 401")

    # 4) 用同一把金鑰直接鑄 token（模擬完成授權）→ Bearer 呼叫工具
    #    假憑證會 pass-through 到 CalDAV 被拒 → 證明 token→憑證→CalDAV 全鏈路
    os.environ["M2K_BRIDGE_KEY"] = key_b64
    import m2k_oauth
    provider = m2k_oauth.M2KOAuthProvider(
        m2k_oauth.TokenCrypto(base64.urlsafe_b64decode(key_b64)),
        clients_path="/nonexistent/no.json")
    tok = provider._mint("smoke", ["m2k"], "fake@example.com", "wrongpass")
    hdr = {"Authorization": f"Bearer {tok.access_token}"}
    for era in ERAS:
        async with connect_http(f"{base}/mcp", era, hdr) as s:
            txt = (await s.call_tool("agenda", {"days": 1})).content[0].text
            print(f"oauth bearer fake-cred [{era}] ->", txt[:60])
            assert txt.startswith("錯誤：登入失敗"), txt


async def probe_hosts(port, allowed, cases):
    """Host 白名單（DNS-rebinding 防護）：(Host 標頭, 是否該被擋成 421)。"""
    async with httpx2.AsyncClient(timeout=10) as c:
        for host, blocked in cases:
            r = await c.post(f"http://127.0.0.1:{port}/mcp", json={}, headers={"Host": host})
            print(f"host [allowed={allowed or '-'}] {host} -> {r.status_code}")
            assert (r.status_code == 421) == blocked, (allowed, host, r.status_code)


def wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"HTTP server 未在 {timeout}s 內開始監聽 :{port}")


def main():
    # 每跑一次有好幾個假憑證登入，打到真實 Mail2000 可能觸發來源 IP 封鎖。
    # 子行程（stdio／HTTP／OAuth server）都繼承 os.environ；.env 載入不覆蓋既有值。
    os.environ["M2K_URL"] = "http://127.0.0.1:9/cgi-bin/cal/caldav/"
    os.environ["M2K_BASE"] = "http://127.0.0.1:9"
    for era in ERAS:
        asyncio.run(asyncio.wait_for(test_stdio(era), 60))

    proc = subprocess.Popen(
        [sys.executable, SRV, "--http", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(PORT)
        for era in ERAS:
            asyncio.run(asyncio.wait_for(test_http(f"http://127.0.0.1:{PORT}/mcp", era), 90))
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # Host 白名單：綁 0.0.0.0（同 Dockerfile 預設），才測得到「把 host 傳給
    # streamable_http_app 會關掉防護」這類回歸。沒設白名單只放行本機 Host；
    # 設了 M2K_ALLOWED_HOSTS 後放行該網域、其他照擋。
    for allowed, cases in (
            ("", [("example.test", True), (f"127.0.0.1:{PORT_HOST}", False)]),
            ("example.test", [("example.test", False), ("evil.test", True)])):
        proc = subprocess.Popen(
            [sys.executable, SRV, "--http", "--host", "0.0.0.0", "--port", str(PORT_HOST)],
            env={**os.environ, "M2K_ALLOWED_HOSTS": allowed},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_port(PORT_HOST)
            asyncio.run(asyncio.wait_for(probe_hosts(PORT_HOST, allowed, cases), 30))
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    # OAuth 模式：臨時金鑰＋臨時 clients 檔，不落任何檔案到專案目錄
    key_b64 = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    base = f"http://127.0.0.1:{PORT_OAUTH}"
    proc = subprocess.Popen(
        [sys.executable, SRV, "--oauth", "--issuer", base, "--port", str(PORT_OAUTH)],
        env={**os.environ, "M2K_BRIDGE_KEY": key_b64,
             "M2K_OAUTH_CLIENTS": f"/tmp/m2k-smoke-clients-{os.getpid()}.json"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(PORT_OAUTH)
        asyncio.run(asyncio.wait_for(test_oauth(base, key_b64), 120))
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print("\nSMOKE-OK ✅")


if __name__ == "__main__":
    main()
