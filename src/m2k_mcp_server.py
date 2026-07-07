#!/usr/bin/env python3
"""
m2k MCP server — 讓 Claude 直接查你的 m2k 行事曆與建立會議（走 CalDAV）。

範圍：查詢自己的行事曆 + 建立會議（CalDAV，應用程式專用密碼）。
      「看他人行事曆」需 webmail 登入 session（SAML），MCP 拿不到 → 用使用者腳本。

安裝：
  pip install "mcp[cli]" caldav icalendar requests

== 模式一：stdio（本機、單人，預設）==
憑證用環境變數（或專案根目錄 .env，會自動載入）：
  M2K_URL=https://mail.gss.com.tw/cgi-bin/cal/caldav/   # 可省略，內建預設
  M2K_USER=wilber_chen@gss.com.tw
  M2K_PASS=應用程式專用密碼

Claude Desktop 設定（claude_desktop_config.json）:
{
  "mcpServers": {
    "m2k-calendar": {
      "command": "python3",
      "args": ["/絕對路徑/src/m2k_mcp_server.py"],
      "env": {
        "M2K_USER": "wilber_chen@gss.com.tw",
        "M2K_PASS": "應用程式專用密碼"
      }
    }
  }
}
執行測試：python3 src/m2k_mcp_server.py   （stdio 模式，等待 MCP 用戶端連入）

== 模式二：streamable-http（公用部署、多人）==
  python3 src/m2k_mcp_server.py --http --host 0.0.0.0 --port 8763
憑證採 pass-through：伺服器**不保存任何帳密**，每個請求必須自帶
  Authorization: Basic <base64(帳號:應用程式專用密碼)>
標頭，伺服器原樣轉給 CalDAV。HTTP 模式絕不回退到環境變數憑證。
注意：Basic 是明文等級，正式部署必須放在 HTTPS 反向代理後面。

Claude Code 用戶端設定：
  claude mcp add --transport http m2k-calendar https://主機:8763/mcp \\
    --header "Authorization: Basic $(printf '%s' '帳號:應用程式專用密碼' | base64)"
"""
import argparse
import os
import sys
import datetime as dt
import uuid

try:
    from mcp.server.fastmcp import FastMCP, Context
except ImportError:
    sys.exit('需要 mcp 套件：pip install "mcp[cli]"')

import m2kcal  # 重用既有 CalDAV / ICS 邏輯

m2kcal.load_dotenv()
mcp = FastMCP("m2k-calendar")


# 注意：工具內一律 catch M2KError 回傳錯誤字串。
# m2kcal 遇可預期錯誤丟 M2KError（不再 sys.exit），MCP server 才不會整個被殺掉。


def _auth(ctx):
    """取本次呼叫的 CalDAV 憑證來源。
    stdio 模式（無 HTTP request）→ 回 None，走環境變數/.env（creds()）。
    HTTP 模式 → 強制從該請求的 Authorization: Basic 取，絕不回退到
    環境變數，避免多人共用時冒用部署者身分。"""
    req = getattr(ctx.request_context, "request", None) if ctx else None
    if req is None:
        return None
    user, pwd = m2kcal.parse_basic_auth(req.headers.get("authorization", ""))
    return (os.environ.get("M2K_URL", m2kcal.DEFAULT_URL), user, pwd)


def _cal(auth):
    p = m2kcal.connect(auth)
    return m2kcal.pick_calendar(p)


@mcp.tool()
def list_calendars(ctx: Context = None) -> str:
    """列出你在 CalDAV 可存取的行事曆名稱。"""
    try:
        p = m2kcal.connect(_auth(ctx))
        return "\n".join("- " + m2kcal.cal_name(c) for c in p.calendars()) or "（無）"
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


@mcp.tool()
def agenda(days: int = 7, ctx: Context = None) -> str:
    """看未來 N 天的行程（依天分組）。days 預設 7。"""
    try:
        cal = _cal(_auth(ctx))
        start = dt.datetime.now()
        end = start + dt.timedelta(days=days)
        events = cal.search(start=start, end=end, event=True, expand=True)
        return f"未來 {days} 天，共 {len(events)} 筆:\n" + m2kcal.render_grouped(events)
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


@mcp.tool()
def list_events(start: str, end: str, ctx: Context = None) -> str:
    """查指定期間的行程。start/end 格式 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'。"""
    try:
        s = m2kcal.parse_when(start)
        e = m2kcal.parse_when(end)
        cal = _cal(_auth(ctx))
        events = cal.search(start=s, end=e, event=True, expand=True)
        return f"{start} ~ {end}，共 {len(events)} 筆:\n" + m2kcal.render_grouped(events)
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


@mcp.tool()
def book(title: str, start: str, end: str = "", location: str = "",
         description: str = "", attendees: list[str] | None = None,
         ctx: Context = None) -> str:
    """建立會議。
    title 標題；start/end 時間 'YYYY-MM-DD HH:MM'（end 省略則 +1 小時，台北時間）；
    location 地點；description 描述；attendees 與會者 email 清單。
    註：CalDAV 無排程，attendees 只記錄不自動寄邀請。
    """
    try:
        s = m2kcal.parse_when(start)
        e = m2kcal.parse_when(end) if end else s + dt.timedelta(hours=1)
        auth = _auth(ctx) or m2kcal.creds()
        url, user, pwd = auth
        cal = _cal(auth)
        uid = str(uuid.uuid4())
        ics = m2kcal.build_ics(title, s, e, location, description,
                               attendees=attendees, organizer=user, uid=uid)
        put_status, info = m2kcal.put_and_verify(cal, ics, uid, auth=auth)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    lines = ["已建立並驗證：",
             f"  標題: {info.get('SUMMARY', title)}",
             f"  時間: {info.get('start', '?')} → {info.get('end', '?')}"]
    if put_status not in (200, 201, 204):
        lines.append(f"  （伺服器 PUT 回 {put_status}，但已驗證事件確實建立）")
    if info.get("location"):
        lines.append(f"  地點: {info['location']}")
    if info.get("attendees"):
        lines.append("  與會者: " + ", ".join(info["attendees"]))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="m2k 行事曆 MCP server")
    ap.add_argument("--http", action="store_true",
                    help="以 streamable-http 模式啟動（公用部署，憑證走每請求 Basic 標頭）")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP 模式綁定位址（預設 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8763, help="HTTP 模式埠號（預設 8763）")
    args = ap.parse_args()
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
