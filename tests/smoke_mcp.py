#!/usr/bin/env python3
"""MCP 煙霧測試 — 實際啟動 server，驗證 stdio 與 streamable-http 兩種模式。

不需要真實帳密：驗證重點是傳輸能通、每請求認證流程正確、
可預期錯誤以「錯誤：...」回覆且 server 不會死。
（假憑證案例若連得到 mail.gss.com.tw 會驗證 pass-through 到 CalDAV 401；
  連不到也會被包成同樣的「錯誤：登入失敗」訊息，斷言不受影響。）

需求: pip install "mcp[cli]" caldav icalendar requests
執行: python3 tests/smoke_mcp.py
"""
import asyncio
import base64
import os
import socket
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.path.join(ROOT, "src", "m2k_mcp_server.py")
PORT = 8971  # 煙霧測試專用埠，避免撞正式部署的 8763


async def test_stdio():
    params = StdioServerParameters(
        command=sys.executable, args=[SRV],
        env={**os.environ, "M2K_USER": "fake@example.com", "M2K_PASS": "x"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            assert tools == ["agenda", "book", "list_calendars", "list_events"], tools
            print("stdio tools:", tools)
            res = await s.call_tool("list_events", {"start": "亂格式", "end": "2026-07-31"})
            txt = res.content[0].text
            print("stdio bad-date ->", txt[:60])
            assert txt.startswith("錯誤：時間格式看不懂"), txt
            again = await s.list_tools()  # 錯誤後 server 仍活著
            assert len(again.tools) == len(tools)
            print("stdio alive after error: True")


async def test_http(url):
    # 1) 無 Authorization → 拒絕（不得回退到環境變數憑證）
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            txt = (await s.call_tool("agenda", {"days": 1})).content[0].text
            print("http no-auth ->", txt[:60])
            assert txt.startswith("錯誤：需要 Authorization"), txt

    # 2) 壞標頭格式 → 拒絕
    async with streamablehttp_client(url, headers={"Authorization": "Bearer xyz"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            txt = (await s.call_tool("list_calendars", {})).content[0].text
            print("http bad-header ->", txt[:60])
            assert txt.startswith("錯誤：需要 Authorization"), txt

    # 3) 假 Basic 憑證 → pass-through 到 CalDAV 後被拒（或連線失敗），同包成登入失敗
    hdr = {"Authorization": "Basic " + base64.b64encode(b"fake@example.com:wrongpass").decode()}
    async with streamablehttp_client(url, headers=hdr) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            txt = (await s.call_tool("agenda", {"days": 1})).content[0].text
            print("http fake-auth ->", txt[:80])
            assert txt.startswith("錯誤：登入失敗"), txt


def wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"HTTP server 未在 {timeout}s 內開始監聽 :{port}")


def main():
    asyncio.run(asyncio.wait_for(test_stdio(), 60))
    proc = subprocess.Popen(
        [sys.executable, SRV, "--http", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(PORT)
        asyncio.run(asyncio.wait_for(test_http(f"http://127.0.0.1:{PORT}/mcp"), 90))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print("\nSMOKE-OK ✅")


if __name__ == "__main__":
    main()
