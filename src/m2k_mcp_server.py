#!/usr/bin/env python3
"""
m2k MCP server — 讓 Claude 直接查你的 m2k 行事曆與建立會議（走 CalDAV）。

範圍：查詢自己的行事曆 + 建立會議（CalDAV，應用程式專用密碼）。
      「看他人行事曆」需 webmail 登入 session（SAML），MCP 拿不到 → 用使用者腳本。

安裝：
  pip install "mcp[cli]" caldav icalendar requests
  # OAuth 模式另需： pip install cryptography

== 模式一：stdio（本機、單人，預設）==
憑證用環境變數（或專案根目錄 .env，會自動載入）：
  M2K_URL=https://mail.gss.com.tw/cgi-bin/cal/caldav/   # 可省略，內建預設
  M2K_USER=you@example.com
  M2K_PASS=應用程式專用密碼

Claude Desktop 設定（claude_desktop_config.json）:
{
  "mcpServers": {
    "m2k-calendar": {
      "command": "python3",
      "args": ["/絕對路徑/src/m2k_mcp_server.py"],
      "env": {
        "M2K_USER": "you@example.com",
        "M2K_PASS": "應用程式專用密碼"
      }
    }
  }
}
執行測試：python3 src/m2k_mcp_server.py   （stdio 模式，等待 MCP 用戶端連入）

== 模式二：streamable-http（Claude Code/Desktop 多人共用）==
  python3 src/m2k_mcp_server.py --http --host 0.0.0.0 --port 8763
憑證採 pass-through：伺服器**不保存任何帳密**，每個請求必須自帶
  Authorization: Basic <base64(帳號:應用程式專用密碼)>
標頭，伺服器原樣轉給 CalDAV。HTTP 模式絕不回退到環境變數憑證。
注意：Basic 是明文等級，正式部署必須放在 HTTPS 反向代理後面。

Claude Code 用戶端設定：
  claude mcp add --transport http m2k-calendar https://主機:8763/mcp \\
    --header "Authorization: Basic $(printf '%s' '帳號:應用程式專用密碼' | base64)"

== 模式三：OAuth bridge（claude.ai Connectors：手機 app / 網頁版）==
  python3 src/m2k_mcp_server.py --oauth --issuer https://對外網址 \\
      --host 0.0.0.0 --port 8763
標準 OAuth 2.1（動態註冊 + PKCE）。使用者第一次連接時會被導到 /login
輸入 m2k 帳號＋應用程式專用密碼，驗證後憑證以伺服器金鑰加密封進
token（無狀態，伺服器不存憑證）。細節見 m2k_oauth.py。
issuer 必須是用戶端可達的 HTTPS 網址（claude.ai 的連線來自 Anthropic
雲端，需公網可達）。
"""
import argparse
import os
import sys
import datetime as dt
import uuid

try:
    from mcp.server.fastmcp import FastMCP, Context
    from mcp.server.auth.middleware.auth_context import get_access_token
except ImportError:
    sys.exit('需要 mcp 套件：pip install "mcp[cli]"')

import m2kcal  # 重用既有 CalDAV / ICS 邏輯

m2kcal.load_dotenv()


# 注意：工具內一律 catch M2KError 回傳錯誤字串。
# m2kcal 遇可預期錯誤丟 M2KError（不再 sys.exit），MCP server 才不會整個被殺掉。


def _auth(ctx):
    """取本次呼叫的 CalDAV 憑證來源，依模式：
    OAuth 模式 → 從 Bearer token 解密出的使用者憑證（get_access_token）。
    HTTP 模式  → 該請求的 Authorization: Basic，絕不回退到環境變數，
                 避免多人共用時冒用部署者身分。
    stdio 模式 → 回 None，走環境變數/.env（creds()）。"""
    tok = get_access_token()
    if tok is not None and getattr(tok, "m2k_user", ""):
        return (os.environ.get("M2K_URL", m2kcal.DEFAULT_URL), tok.m2k_user, tok.m2k_pass)
    req = getattr(ctx.request_context, "request", None) if ctx else None
    if req is None:
        return None
    user, pwd = m2kcal.parse_basic_auth(req.headers.get("authorization", ""))
    return (os.environ.get("M2K_URL", m2kcal.DEFAULT_URL), user, pwd)


def _cal(auth):
    p = m2kcal.connect(auth)
    return m2kcal.pick_calendar(p)


def list_calendars(ctx: Context = None) -> str:
    """列出你在 CalDAV 可存取的行事曆名稱。"""
    try:
        p = m2kcal.connect(_auth(ctx))
        return "\n".join("- " + m2kcal.cal_name(c) for c in p.calendars()) or "（無）"
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


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


def update_event(uid: str, title: str = "", start: str = "", end: str = "",
                 location: str = "", description: str = "",
                 add_attendees: list[str] | None = None,
                 remove_attendees: list[str] | None = None,
                 ctx: Context = None) -> str:
    """修改既有會議。uid 取自 agenda / list_events 輸出的「id:」欄位。
    只更新有給的欄位：title 標題；start/end 時間 'YYYY-MM-DD HH:MM'；
    location 地點；description 描述；add_attendees / remove_attendees
    增減與會者 email 清單（其餘與會者保留）。
    註：CalDAV 無排程，異動不會自動寄通知信給與會者。
    """
    if not any([title, start, end, location, description,
                add_attendees, remove_attendees]):
        return "錯誤：沒有任何要修改的欄位。"
    try:
        auth = _auth(ctx) or m2kcal.creds()
        cal = _cal(auth)
        ev = m2kcal.find_event_by_uid(cal, uid)
        ics = m2kcal.update_event_ics(
            ev.data, title=title or None,
            start=m2kcal.parse_when(start) if start else None,
            end=m2kcal.parse_when(end) if end else None,
            location=location or None, desc=description or None,
            add_attendees=add_attendees, remove_attendees=remove_attendees)
        new_seq = m2kcal.parse_ics(ics).get("SEQUENCE")
        put_status, info = m2kcal.put_and_verify(cal, ics, uid, auth=auth,
                                                 put_url=str(ev.url),
                                                 expect_seq=new_seq)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    lines = ["已更新並驗證：",
             f"  標題: {info.get('SUMMARY', '?')}",
             f"  時間: {info.get('start', '?')} → {info.get('end', '?')}"]
    if put_status not in (200, 201, 204):
        lines.append(f"  （伺服器 PUT 回 {put_status}，但已驗證異動確實寫入）")
    if info.get("location"):
        lines.append(f"  地點: {info['location']}")
    if info.get("attendees"):
        lines.append("  與會者: " + ", ".join(info["attendees"]))
    return "\n".join(lines)


TOOLS = (list_calendars, agenda, list_events, book, update_event)


def build_server(host=None, port=None, oauth=False, issuer=None) -> FastMCP:
    if oauth:
        import m2k_oauth
        provider, auth_settings = m2k_oauth.create(issuer)
        server = FastMCP("m2k-calendar", auth_server_provider=provider, auth=auth_settings)
        m2k_oauth.add_login_routes(server, provider)
    else:
        server = FastMCP("m2k-calendar")
    for f in TOOLS:
        server.tool()(f)
    if host:
        server.settings.host = host
    if port:
        server.settings.port = port
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="m2k 行事曆 MCP server")
    ap.add_argument("--http", action="store_true",
                    help="streamable-http 模式（憑證走每請求 Basic 標頭）")
    ap.add_argument("--oauth", action="store_true",
                    help="streamable-http + OAuth bridge 模式（claude.ai Connectors 用）")
    ap.add_argument("--issuer",
                    help="OAuth 模式必填：用戶端可達的對外網址（如 https://host:8763）")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP/OAuth 模式綁定位址（預設 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8763, help="HTTP/OAuth 模式埠號（預設 8763）")
    args = ap.parse_args()
    if args.oauth:
        if not args.issuer:
            ap.error("--oauth 需要 --issuer（用戶端可達的對外網址）")
        build_server(args.host, args.port, oauth=True,
                     issuer=args.issuer).run(transport="streamable-http")
    elif args.http:
        build_server(args.host, args.port).run(transport="streamable-http")
    else:
        build_server().run()
