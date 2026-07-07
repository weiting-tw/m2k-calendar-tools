#!/usr/bin/env python3
"""
m2k MCP server — 讓 Claude 直接查你的 m2k 行事曆與建立會議（走 CalDAV）。

範圍：查詢自己的行事曆 + 建立會議（CalDAV，應用程式專用密碼）。
      「看他人行事曆」需 webmail 登入 session（SAML），MCP 拿不到 → 用使用者腳本。

安裝：
  pip install "mcp[cli]" caldav icalendar requests
  # 認證用環境變數（或同目錄 .env，m2kcal 會載入）：
  #   M2K_URL=https://mail.gss.com.tw/cgi-bin/cal/caldav/
  #   M2K_USER=you@example.com
  #   M2K_PASS=應用程式專用密碼

Claude Desktop 設定（claude_desktop_config.json）:
{
  "mcpServers": {
    "m2k-calendar": {
      "command": "python3",
      "args": ["/絕對路徑/m2k_mcp_server.py"],
      "env": {
        "M2K_USER": "you@example.com",
        "M2K_PASS": "應用程式專用密碼"
      }
    }
  }
}
執行測試：python3 m2k_mcp_server.py   （stdio 模式，等待 MCP 用戶端連入）
"""
import os
import sys
import datetime as dt
import uuid

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.exit('需要 mcp 套件：pip install "mcp[cli]"')

import requests
import m2kcal  # 重用既有 CalDAV / ICS 邏輯

m2kcal.load_dotenv()
mcp = FastMCP("m2k-calendar")


def _cal():
    p = m2kcal.connect()
    return m2kcal.pick_calendar(p)


@mcp.tool()
def list_calendars() -> str:
    """列出你在 CalDAV 可存取的行事曆名稱。"""
    p = m2kcal.connect()
    return "\n".join("- " + m2kcal.cal_name(c) for c in p.calendars()) or "（無）"


@mcp.tool()
def agenda(days: int = 7) -> str:
    """看未來 N 天的行程（依天分組）。days 預設 7。"""
    cal = _cal()
    start = dt.datetime.now()
    end = start + dt.timedelta(days=days)
    events = cal.search(start=start, end=end, event=True, expand=True)
    return f"未來 {days} 天，共 {len(events)} 筆:\n" + m2kcal.render_grouped(events)


@mcp.tool()
def list_events(start: str, end: str) -> str:
    """查指定期間的行程。start/end 格式 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'。"""
    cal = _cal()
    s = m2kcal.parse_when(start)
    e = m2kcal.parse_when(end)
    events = cal.search(start=s, end=e, event=True, expand=True)
    return f"{start} ~ {end}，共 {len(events)} 筆:\n" + m2kcal.render_grouped(events)


@mcp.tool()
def book(title: str, start: str, end: str = "", location: str = "",
         description: str = "", attendees: list[str] | None = None) -> str:
    """建立會議。
    title 標題；start/end 時間 'YYYY-MM-DD HH:MM'（end 省略則 +1 小時，台北時間）；
    location 地點；description 描述；attendees 與會者 email 清單。
    註：CalDAV 無排程，attendees 只記錄不自動寄邀請。
    """
    cal = _cal()
    url, user, pwd = m2kcal.creds()
    s = m2kcal.parse_when(start)
    e = m2kcal.parse_when(end) if end else s + dt.timedelta(hours=1)
    uid = str(uuid.uuid4())
    ics = m2kcal.build_ics(title, s, e, location, description,
                           attendees=attendees, organizer=user, uid=uid)
    coll = str(cal.url)
    if not coll.endswith("/"):
        coll += "/"
    put_url = coll + uid + ".ics"
    requests.put(put_url, data=ics.encode("utf-8"),
                 headers={"Content-Type": "text/calendar; charset=utf-8"},
                 auth=(user, pwd), timeout=30)
    # Mail2000 PUT 可能回 500 但已建立 → 用 GET 驗證
    g = requests.get(put_url, auth=(user, pwd), timeout=30)
    if not (g.status_code == 200 and uid in g.text):
        return f"建立失敗（GET 驗證 HTTP {g.status_code}）。"
    info = m2kcal.parse_ics(g.text)
    lines = ["已建立並驗證：",
             f"  標題: {info.get('SUMMARY', title)}",
             f"  時間: {info.get('start', '?')} → {info.get('end', '?')}"]
    if info.get("location"):
        lines.append(f"  地點: {info['location']}")
    if info.get("attendees"):
        lines.append("  與會者: " + ", ".join(info["attendees"]))
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
