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
      "args": ["/絕對路徑/src/m2k_mcp_server.py"],
      "env": {
        "M2K_USER": "you@example.com",
        "M2K_PASS": "應用程式專用密碼"
      }
    }
  }
}
執行測試：python3 src/m2k_mcp_server.py   （stdio 模式，等待 MCP 用戶端連入）
"""
import os
import sys
import datetime as dt
import uuid

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.exit('需要 mcp 套件：pip install "mcp[cli]"')

import m2kcal  # 重用既有 CalDAV / ICS 邏輯

m2kcal.load_dotenv()
mcp = FastMCP("m2k-calendar")


def _cal():
    p = m2kcal.connect()
    return m2kcal.pick_calendar(p)


# 注意：工具內一律 catch M2KError 回傳錯誤字串。
# m2kcal 遇可預期錯誤丟 M2KError（不再 sys.exit），MCP server 才不會整個被殺掉。


@mcp.tool()
def list_calendars() -> str:
    """列出你在 CalDAV 可存取的行事曆名稱。"""
    try:
        p = m2kcal.connect()
        return "\n".join("- " + m2kcal.cal_name(c) for c in p.calendars()) or "（無）"
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


@mcp.tool()
def agenda(days: int = 7) -> str:
    """看未來 N 天的行程（依天分組）。days 預設 7。"""
    try:
        cal = _cal()
        start = dt.datetime.now()
        end = start + dt.timedelta(days=days)
        events = cal.search(start=start, end=end, event=True, expand=True)
        return f"未來 {days} 天，共 {len(events)} 筆:\n" + m2kcal.render_grouped(events)
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


@mcp.tool()
def list_events(start: str, end: str) -> str:
    """查指定期間的行程。start/end 格式 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'。"""
    try:
        cal = _cal()
        s = m2kcal.parse_when(start)
        e = m2kcal.parse_when(end)
        events = cal.search(start=s, end=e, event=True, expand=True)
        return f"{start} ~ {end}，共 {len(events)} 筆:\n" + m2kcal.render_grouped(events)
    except m2kcal.M2KError as e:
        return f"錯誤：{e}"


@mcp.tool()
def book(title: str, start: str, end: str = "", location: str = "",
         description: str = "", attendees: list[str] | None = None) -> str:
    """建立會議。
    title 標題；start/end 時間 'YYYY-MM-DD HH:MM'（end 省略則 +1 小時，台北時間）；
    location 地點；description 描述；attendees 與會者 email 清單。
    註：CalDAV 無排程，attendees 只記錄不自動寄邀請。
    """
    try:
        cal = _cal()
        url, user, pwd = m2kcal.creds()
        s = m2kcal.parse_when(start)
        e = m2kcal.parse_when(end) if end else s + dt.timedelta(hours=1)
        uid = str(uuid.uuid4())
        ics = m2kcal.build_ics(title, s, e, location, description,
                               attendees=attendees, organizer=user, uid=uid)
        put_status, info = m2kcal.put_and_verify(cal, ics, uid)
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
    mcp.run()
