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
import time
import datetime as dt
import uuid
from typing import Any

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


def _overlap_note(cal, s, e, exclude_uid=None) -> str:
    """檢查 [s, e) 是否與現有行程重疊，有則回傳警告文字（查詢失敗回空字串）。"""
    try:
        rows = m2kcal.events_json(cal.search(start=s, end=e, event=True, expand=True))
    except Exception:
        return ""
    clash = [r for r in rows if r["uid"] != exclude_uid and not r["allday"]]
    if not clash:
        return ""
    lines = ["  ⚠ 與現有行程重疊："]
    for r in clash[:5]:
        lines.append(f"    - {r['start']}–{r['end'][-5:] if r['end'] else '?'} {r['summary']}")
    return "\n".join(lines)


def _notify_note(auth, ics: str, method: str, subject: str, body: str,
                 attendees: list[str]) -> str:
    """寄 iMIP 通知信（用使用者自己的 SMTP 身分），回報告文字。失敗不拋錯。"""
    to = [a for a in (attendees or []) if a.strip()
          and a.strip().lower() != auth[1].lower()]  # 不用通知自己
    if not to:
        return "  （沒有其他與會者可通知）"
    try:
        n = m2kcal.send_invite(auth[1], auth[2], to, subject, body,
                               m2kcal.imip_ics(ics, method))
        return f"  已寄{('取消' if method.upper() == 'CANCEL' else '')}通知信給 {n} 位與會者。"
    except Exception as e:
        return f"  ⚠ 通知信寄送失敗：{e}"


def book(title: str, start: str, end: str = "", location: str = "",
         description: str = "", attendees: list[str] | None = None,
         repeat: str = "", repeat_until: str = "", reminder_minutes: int = 0,
         notify: bool = False, ctx: Context = None) -> str:
    """建立會議。
    title 標題；start/end 時間 'YYYY-MM-DD HH:MM'（end 省略則 +1 小時，台北時間）；
    location 地點；description 描述；attendees 與會者 email 清單；
    repeat 重複頻率 daily/weekly/monthly（省略＝不重複）；repeat_until 重複截止 'YYYY-MM-DD'；
    reminder_minutes 開始前 N 分鐘提醒（0＝不提醒）；
    notify=true 時以你的名義寄標準會議邀請信（iMIP）給與會者——寄信是對外動作，
    使用者明確要求通知才帶 true。若時段與現有行程重疊會附警告。
    """
    freq = {"daily": "DAILY", "weekly": "WEEKLY",
            "monthly": "MONTHLY"}.get(repeat.strip().lower()) if repeat else None
    if repeat and not freq:
        return "錯誤：repeat 需為 daily / weekly / monthly。"
    try:
        s = m2kcal.parse_when(start)
        e = m2kcal.parse_when(end) if end else s + dt.timedelta(hours=1)
        rrule = ""
        if freq:
            rrule = f"FREQ={freq}"
            if repeat_until:
                u = m2kcal.parse_when(repeat_until).replace(
                    hour=23, minute=59, second=59, tzinfo=m2kcal.TW_TZ)
                rrule += ";UNTIL=" + m2kcal._zulu(u)
        auth = _auth(ctx) or m2kcal.creds()
        url, user, pwd = auth
        cal = _cal(auth)
        note = _overlap_note(cal, s, e)
        uid = str(uuid.uuid4())
        ics = m2kcal.build_ics(title, s, e, location, description,
                               attendees=attendees, organizer=user, uid=uid,
                               rrule=rrule, reminder_minutes=reminder_minutes)
        put_status, info = m2kcal.put_and_verify(cal, ics, uid, auth=auth)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    lines = ["已建立並驗證：",
             f"  標題: {info.get('SUMMARY', title)}",
             f"  時間: {info.get('start', '?')} → {info.get('end', '?')}"]
    if rrule:
        lines.append(f"  重複: {repeat}" + (f"（至 {repeat_until}）" if repeat_until else ""))
    if reminder_minutes:
        lines.append(f"  提醒: 開始前 {reminder_minutes} 分鐘")
    if put_status not in (200, 201, 204):
        lines.append(f"  （伺服器 PUT 回 {put_status}，但已驗證事件確實建立）")
    if info.get("location"):
        lines.append(f"  地點: {info['location']}")
    if info.get("attendees"):
        lines.append("  與會者: " + ", ".join(info["attendees"]))
    if notify:
        lines.append(_notify_note(auth, ics, "REQUEST",
                                  f"會議邀請：{title}",
                                  f"{auth[1]} 邀請你參加「{title}」（{start}）。",
                                  info.get("attendees") or attendees or []))
    if note:
        lines.append(note)
    return "\n".join(lines)


def update_event(uid: str, title: str = "", start: str = "", end: str = "",
                 location: str = "", description: str = "",
                 add_attendees: list[str] | None = None,
                 remove_attendees: list[str] | None = None,
                 occurrence: str = "", repeat: str = "", repeat_until: str = "",
                 notify: bool = False, ctx: Context = None) -> str:
    """修改既有會議。uid 取自 agenda / list_events 輸出的「id:」欄位。
    只更新有給的欄位：title 標題；start/end 時間 'YYYY-MM-DD HH:MM'；
    location 地點；description 描述；add_attendees / remove_attendees
    增減與會者 email 清單（其餘與會者保留）。
    重複會議：預設改整個系列；occurrence='該次原開始時間' 時只改那一次
    （實作＝該次從系列剔除並另建獨立會議，Mail2000 不支援原生單次例外）；
    repeat 改重複規則（none=取消重複/daily/weekly/monthly，搭配 repeat_until）。
    notify=true 以你的名義寄更新通知信（iMIP）給與會者——使用者明確要求才帶。
    改時間時若與現有行程重疊會附警告。
    """
    if not any([title, start, end, location, description,
                add_attendees, remove_attendees, repeat]):
        return "錯誤：沒有任何要修改的欄位。"
    if occurrence and repeat:
        return "錯誤：occurrence（只改某一次）不能與 repeat（改整串規則）同時使用。"
    rrule = None
    if repeat:
        if repeat.strip().lower() == "none":
            rrule = ""
        else:
            freq = {"daily": "DAILY", "weekly": "WEEKLY",
                    "monthly": "MONTHLY"}.get(repeat.strip().lower())
            if not freq:
                return "錯誤：repeat 需為 none / daily / weekly / monthly。"
            rrule = f"FREQ={freq}"
            if repeat_until:
                u = m2kcal.parse_when(repeat_until).replace(
                    hour=23, minute=59, second=59, tzinfo=m2kcal.TW_TZ)
                rrule += ";UNTIL=" + m2kcal._zulu(u)
    try:
        auth = _auth(ctx) or m2kcal.creds()
        cal = _cal(auth)
        ev = m2kcal.find_event_by_uid(cal, uid)
        note = ""
        if start or end:
            try:
                olds = m2kcal.parse_ics(ev.data)
                ns = (m2kcal.parse_when(start) if start
                      else m2kcal.parse_when(olds.get("start", "")))
                ne = (m2kcal.parse_when(end) if end
                      else m2kcal.parse_when(olds.get("end", "")))
                note = _overlap_note(cal, ns, ne, exclude_uid=uid)
            except m2kcal.M2KError:
                note = ""  # 舊值解析不了（如全天事件）就略過重疊檢查

        if occurrence:
            # 只改某一次：先建帶變更的獨立事件，成功後再從系列剔除該次
            occ = m2kcal.parse_when(occurrence)
            new_uid = str(uuid.uuid4())
            ics = m2kcal.detach_occurrence_ics(
                ev.data, occ, new_uid, title=title or None,
                start=m2kcal.parse_when(start) if start else None,
                end=m2kcal.parse_when(end) if end else None,
                location=location or None, desc=description or None,
                add_attendees=add_attendees, remove_attendees=remove_attendees)
            put_status, info = m2kcal.put_and_verify(cal, ics, new_uid, auth=auth)
            try:
                ex = m2kcal.add_exdate_ics(ev.data, occ)
                m2kcal.put_and_verify(cal, ex, uid, auth=auth, put_url=str(ev.url),
                                      expect_seq=m2kcal.parse_ics(ex).get("SEQUENCE"))
            except m2kcal.M2KError as err:
                try:
                    m2kcal.find_event_by_uid(cal, new_uid).delete()
                except Exception:
                    pass
                return f"錯誤：從系列剔除該次失敗（已還原）：{err}"
            head = (f"已把 {occurrence} 那一次從系列拆出為獨立會議並套用變更"
                    f"（新 id: {new_uid}）：")
        else:
            ics = m2kcal.update_event_ics(
                ev.data, title=title or None,
                start=m2kcal.parse_when(start) if start else None,
                end=m2kcal.parse_when(end) if end else None,
                location=location or None, desc=description or None,
                add_attendees=add_attendees, remove_attendees=remove_attendees,
                rrule=rrule)
            new_seq = m2kcal.parse_ics(ics).get("SEQUENCE")
            put_status, info = m2kcal.put_and_verify(cal, ics, uid, auth=auth,
                                                     put_url=str(ev.url),
                                                     expect_seq=new_seq)
            head = "已更新並驗證："
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    lines = [head,
             f"  標題: {info.get('SUMMARY', '?')}",
             f"  時間: {info.get('start', '?')} → {info.get('end', '?')}"]
    if repeat:
        lines.append("  重複: " + ("已取消" if rrule == "" else repeat
                                   + (f"（至 {repeat_until}）" if repeat_until else "")))
    if put_status not in (200, 201, 204):
        lines.append(f"  （伺服器 PUT 回 {put_status}，但已驗證異動確實寫入）")
    if info.get("location"):
        lines.append(f"  地點: {info['location']}")
    if info.get("attendees"):
        lines.append("  與會者: " + ", ".join(info["attendees"]))
    if notify:
        lines.append(_notify_note(
            auth, ics, "REQUEST",
            f"會議更新：{info.get('SUMMARY', '?')}",
            f"{auth[1]} 更新了會議「{info.get('SUMMARY', '?')}」"
            f"（{info.get('start', '?')}）。",
            info.get("attendees") or []))
    if note:
        lines.append(note)
    return "\n".join(lines)


# ---------- MCP App：互動行事曆 UI ----------
_CAL_UI_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "apps", "calendar", "dist", "calendar.html")


def _cal_ui_uri() -> str:
    """resource URI 帶內容 hash：部分 host 會以 URI 為 key 快取 UI，
    不帶版本的話改版後使用者仍拿到舊 UI。"""
    try:
        import hashlib
        with open(_CAL_UI_HTML, "rb") as f:
            h = hashlib.sha1(f.read()).hexdigest()[:8]
        return f"ui://m2k-calendar/calendar-{h}.html"
    except OSError:
        return "ui://m2k-calendar/calendar.html"


CAL_UI_URI = _cal_ui_uri()


def _calendar_payload(s: "dt.datetime", e: "dt.datetime", ctx) -> dict[str, Any]:
    auth = _auth(ctx) or m2kcal.creds()
    cal = _cal(auth)
    events = cal.search(start=s, end=e, event=True, expand=True)
    return {
        "range": {"start": s.strftime("%Y-%m-%d"), "end": e.strftime("%Y-%m-%d")},
        "today": dt.date.today().isoformat(),
        "me": auth[1],  # UI 據此顯示「我的出席狀態」快速回覆按鈕
        "events": m2kcal.events_json(events),
    }


def show_calendar(start: str = "", days: int = 7, ctx: Context = None) -> dict[str, Any]:
    """以互動行事曆 UI 顯示行程（週/月檢視，可直接在 UI 建立與修改會議）。
    start 'YYYY-MM-DD'（預設今天）起 days 天。
    使用者要「看行事曆／排程總覽」時優先用這個；純文字摘要用 agenda / list_events。"""
    try:
        s = (m2kcal.parse_when(start) if start
             else dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
        return _calendar_payload(s, s + dt.timedelta(days=days), ctx)
    except m2kcal.M2KError as e:
        return {"error": str(e), "events": []}
    except Exception as e:  # UI 端要能顯示錯誤，不能讓 tool call 直接炸掉
        return {"error": f"讀取行程失敗：{e}", "events": []}


def calendar_data(start: str, end: str, ctx: Context = None) -> dict[str, Any]:
    """（行事曆 UI 專用）回指定期間的結構化行程資料。start/end 'YYYY-MM-DD'。"""
    try:
        return _calendar_payload(m2kcal.parse_when(start), m2kcal.parse_when(end), ctx)
    except m2kcal.M2KError as e:
        return {"error": str(e), "events": []}
    except Exception as e:
        return {"error": f"讀取行程失敗：{e}", "events": []}


def respond_event(uid: str, response: str, ctx: Context = None) -> str:
    """回覆會議邀請：把你在該會議的出席狀態改為 accept（接受）/
    tentative（暫定）/ decline（拒絕）。uid 取自查詢輸出的 id: 欄位。
    註：CalDAV 無排程，只更新你日曆上的狀態，不會寄回覆信給召集人。"""
    status = {"accept": "ACCEPTED", "tentative": "TENTATIVE",
              "decline": "DECLINED"}.get(response.strip().lower())
    if not status:
        return "錯誤：response 需為 accept / tentative / decline。"
    try:
        auth = _auth(ctx) or m2kcal.creds()
        cal = _cal(auth)
        ev = m2kcal.find_event_by_uid(cal, uid)
        ics = m2kcal.update_event_ics(ev.data, respond=(auth[1], status))
        new_seq = m2kcal.parse_ics(ics).get("SEQUENCE")
        put_status, info = m2kcal.put_and_verify(cal, ics, uid, auth=auth,
                                                 put_url=str(ev.url),
                                                 expect_seq=new_seq)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    zh = {"ACCEPTED": "接受", "TENTATIVE": "暫定", "DECLINED": "拒絕"}[status]
    return (f"已將你對「{info.get('SUMMARY', '?')}」的出席狀態改為：{zh}。\n"
            "（僅更新你的日曆，不會自動通知召集人）")


# 聯絡人快取：掃一年行事曆要幾秒，依使用者 email 快取 15 分鐘
_CONTACTS_CACHE: dict[str, tuple[float, dict]] = {}
_CONTACTS_TTL = 900
_CACHE_MAX_USERS = 300       # 快取淘汰：先清過期，仍超過就淘汰最舊（防長期記憶體累積）


def _cache_put(cache: dict, key, value) -> None:
    now = time.time()
    for k in [k for k, (ts, _) in cache.items() if now - ts >= _CONTACTS_TTL]:
        del cache[k]
    if len(cache) >= _CACHE_MAX_USERS:
        for k in sorted(cache, key=lambda k: cache[k][0])[:len(cache) - _CACHE_MAX_USERS + 1]:
            del cache[k]
    cache[key] = (now, value)

# 信件往來聯絡人池：一次抓最近信件的 header 建池、依使用者快取
#（Mail2000 IMAP SEARCH 無索引，不能按查詢即時搜，見 imap_recent_contacts）
_MAIL_CACHE: dict[str, tuple[float, dict]] = {}


def _mail_contacts(auth) -> dict:
    """用使用者自己的憑證抓自己信箱的最近聯絡人池。
    共用部署天生每人隔離、零設定；失敗（IMAP 關閉等）回空 dict。"""
    hit = _MAIL_CACHE.get(auth[1])
    if hit and time.time() - hit[0] < _CONTACTS_TTL:
        return hit[1]
    try:
        found = m2kcal.imap_recent_contacts(auth[1], auth[2])
    except Exception:
        found = {}
    _cache_put(_MAIL_CACHE, auth[1], found)
    return found

# （選配）靜態公司通訊錄檔：M2K_DIRECTORY_FILE 指向 webmail 匯出的
# CSV/vCard。共用部署免 cookie 即可全公司搜尋；人員異動時重新匯出覆蓋即可。
_DIRFILE_CACHE: tuple[str, float, dict] | None = None


def _directory_contacts() -> dict:
    global _DIRFILE_CACHE
    path = os.environ.get("M2K_DIRECTORY_FILE", "")
    if not path or not os.path.isfile(path):
        return {}
    mtime = os.path.getmtime(path)
    if _DIRFILE_CACHE and _DIRFILE_CACHE[0] == path and _DIRFILE_CACHE[1] == mtime:
        return _DIRFILE_CACHE[2]
    try:
        contacts = m2kcal.load_directory_file(path)
    except OSError:
        return {}
    _DIRFILE_CACHE = (path, mtime, contacts)
    return contacts


def find_person(names: list[str], ctx: Context = None) -> str:
    """依（模糊的）名字或暱稱查同事的 email。資料來源（自動合併，
    前兩項用使用者自己的憑證、每人隔離）：
    1) 行事曆近一年的與會者/召集人 2) 你信箱的信件往來（IMAP）
    3) 公司通訊錄匯出檔（M2K_DIRECTORY_FILE，選配）。
    使用者提到模糊人名（如「把 pekka 加進會議」）時先用這個查；
    若有多個候選或查無此人，把結果列給使用者確認，**絕不自行猜測 email**。
    names 可一次查多個名字。"""
    try:
        auth = _auth(ctx) or m2kcal.creds()
        key = auth[1]
        hit = _CONTACTS_CACHE.get(key)
        if hit and time.time() - hit[0] < _CONTACTS_TTL:
            contacts = hit[1]
        else:
            contacts = m2kcal.collect_contacts(_cal(auth))
            _cache_put(_CONTACTS_CACHE, key, contacts)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    dircon = _directory_contacts()
    lines = []
    src = [f"行事曆歷史 {len(contacts)} 位", "信件往來"]
    if dircon:
        src.append(f"公司通訊錄檔 {len(dircon)} 位")
    lines.append("（資料來源：" + " + ".join(src) + "）")
    mailcon = _mail_contacts(auth)
    for n in names:
        seen = set()
        rows = []
        for _s, email, rec in m2kcal.match_contacts(dircon, n)[:8]:
            seen.add(email)
            extra = ""
            crec = contacts.get(email)
            if crec:
                extra = f"，行事曆出現 {crec['count']} 次"
            rows.append(f"  - {rec['name'] or email.split('@')[0]} <{email}>（公司通訊錄檔{extra}）")
        for _s, email, rec in m2kcal.match_contacts(contacts, n)[:5]:
            if email in seen:
                continue
            seen.add(email)
            nm = rec["name"] or email.split("@")[0]
            mails = mailcon.get(email, {}).get("count")
            extra = f"，信件 {mails} 封" if mails else ""
            rows.append(f"  - {nm} <{email}>"
                        f"（行事曆出現 {rec['count']} 次，最近 {rec['last'] or '?'}{extra}）")
        for _s, email, rec in m2kcal.match_contacts(mailcon, n)[:5]:
            if email in seen:
                continue
            seen.add(email)
            nm = rec["name"] or email.split("@")[0]
            rows.append(f"  - {nm} <{email}>"
                        f"（信件往來 {rec['count']} 封，最近 {rec['last'] or '?'}）")
        if not rows:
            lines.append(f"「{n}」：找不到，請向使用者確認 email。")
            continue
        lines.append(f"「{n}」：" + ("" if len(rows) == 1 else f"（{len(rows)} 個候選，請確認）"))
        lines += rows
    return "\n".join(lines)


def delete_event(uid: str, occurrence: str = "", notify: bool = False,
                 ctx: Context = None) -> str:
    """刪除會議（依 uid，取自查詢輸出的 id: 欄位）。無法復原。
    重複會議：預設刪整個系列；occurrence='該次原開始時間' 時只取消那一次。
    notify=true 以你的名義寄取消通知信（iMIP CANCEL）給與會者——
    使用者明確要求才帶。"""
    try:
        auth = _auth(ctx) or m2kcal.creds()
        cal = _cal(auth)
        ev = m2kcal.find_event_by_uid(cal, uid)
        info = m2kcal.parse_ics(ev.data)
        title = info.get("SUMMARY", uid)
        if occurrence:
            occ = m2kcal.parse_when(occurrence)
            # 取消通知的內容：該次的獨立表示（同 UID＋RECURRENCE-ID），僅供寄信
            cancel_src = m2kcal.detach_occurrence_ics(ev.data, occ, uid)
            cancel_src = cancel_src.replace(
                f"UID:{uid}",
                f"UID:{uid}\r\nRECURRENCE-ID;TZID={m2kcal.TZID}:"
                + m2kcal._local_wall(occ), 1)
            ex = m2kcal.add_exdate_ics(ev.data, occ)
            m2kcal.put_and_verify(cal, ex, uid, auth=auth, put_url=str(ev.url),
                                  expect_seq=m2kcal.parse_ics(ex).get("SEQUENCE"))
            result = f"已取消「{title}」{occurrence} 那一次（系列其他場次不受影響）。"
        else:
            cancel_src = ev.data
            ev.delete()
            try:
                m2kcal.find_event_by_uid(cal, uid)
                return f"錯誤：刪除「{title}」後事件仍存在，請稍後重試或到 webmail 確認。"
            except m2kcal.M2KError:
                result = f"已刪除會議：「{title}」。"
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    if notify:
        result += "\n" + _notify_note(
            auth, cancel_src, "CANCEL",
            f"會議取消：{title}",
            f"{auth[1]} 取消了會議「{title}」"
            + (f"（{occurrence} 那一次）" if occurrence else "") + "。",
            info.get("attendees") or [])
    return result


def search_events(keyword: str, start: str = "", end: str = "",
                  ctx: Context = None) -> str:
    """依關鍵字搜尋會議（比對標題/地點/描述，伺服器端過濾）。
    start/end 'YYYY-MM-DD' 可省略，預設搜過去 90 天到未來 180 天。"""
    try:
        s = (m2kcal.parse_when(start) if start
             else dt.datetime.now() - dt.timedelta(days=90))
        e = (m2kcal.parse_when(end) if end
             else dt.datetime.now() + dt.timedelta(days=180))
        cal = _cal(_auth(ctx))
        seen, hits = set(), []
        for field in ("summary", "location", "description"):
            try:
                found = cal.search(start=s, end=e, event=True, expand=True,
                                   **{field: keyword})
            except Exception:
                continue
            for ev in found:
                c = ev.icalendar_component
                key = (str(c.get("uid", "")), str(c.get("dtstart", "")))
                if key not in seen:
                    seen.add(key)
                    hits.append(ev)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    if not hits:
        return f"「{keyword}」在 {s:%Y-%m-%d} ~ {e:%Y-%m-%d} 沒有符合的會議。"
    return (f"「{keyword}」{s:%Y-%m-%d} ~ {e:%Y-%m-%d} 共 {len(hits)} 筆:\n"
            + m2kcal.render_grouped(hits))


def find_free_slots(duration_minutes: int = 60, start: str = "", days: int = 7,
                    day_start: str = "09:00", day_end: str = "18:00",
                    include_weekends: bool = False, ctx: Context = None) -> str:
    """找自己行事曆的空檔（free-busy）。回傳工作時段內長度足夠的可預約時間。
    duration_minutes 需要的長度；start 'YYYY-MM-DD'（預設今天）起 days 天；
    day_start/day_end 每天的可排時段；include_weekends 是否含週末。"""
    try:
        s = m2kcal.parse_when(start) if start else dt.datetime.now()
        e = (s.replace(hour=0, minute=0, second=0, microsecond=0)
             + dt.timedelta(days=days))
        cal = _cal(_auth(ctx))
        fb = cal.freebusy_request(s, e)
        busy = m2kcal.parse_freebusy(
            fb.data if isinstance(getattr(fb, "data", None), str) else str(fb.data))
        slots = m2kcal.free_slots(busy, s, e, duration_minutes,
                                  day_start, day_end, include_weekends)
    except m2kcal.M2KError as err:
        return f"錯誤：{err}"
    except Exception as err:
        return f"錯誤：free-busy 查詢失敗：{err}"
    if not slots:
        return (f"{s:%Y-%m-%d} 起 {days} 天內（{day_start}–{day_end}）"
                f"找不到 ≥ {duration_minutes} 分鐘的空檔。")
    wk = "一二三四五六日"
    out = [f"{s:%Y-%m-%d} 起 {days} 天，工作時段 {day_start}–{day_end}，"
           f"≥ {duration_minutes} 分鐘的空檔："]
    cur = None
    for a, b in slots:
        if a.date() != cur:
            cur = a.date()
            out.append(f"\n📅 {cur.isoformat()} (週{wk[cur.weekday()]})")
        mins = int((b - a).total_seconds() // 60)
        out.append(f"   {a:%H:%M}–{b:%H:%M}（{mins} 分鐘）")
    return "\n".join(out)


def _register_calendar_app(server: "FastMCP") -> None:
    server.tool(meta={"ui": {"resourceUri": CAL_UI_URI}},
                structured_output=True)(show_calendar)
    # calendar_data 不設 visibility:["app"]：部分 host（如 Claude Desktop）
    # 會擋 App 對「模型不可見」工具的呼叫，導致 UI 翻頁拿不到資料
    server.tool(meta={"ui": {"resourceUri": CAL_UI_URI}},
                structured_output=True)(calendar_data)

    @server.resource(CAL_UI_URI, name="m2k 行事曆 UI",
                     mime_type="text/html;profile=mcp-app")
    def calendar_ui() -> str:
        with open(_CAL_UI_HTML, encoding="utf-8") as f:
            return f.read()


TOOLS = (list_calendars, search_events, find_free_slots, find_person)
# 行事曆相關工具都掛 UI meta：支援 MCP Apps 的客戶端呼叫時一律渲染行事曆畫面
# （文字輸出照舊給模型；UI 端自行透過 calendar_data 取結構化資料）
APP_TOOLS = (agenda, list_events, book, update_event, respond_event, delete_event)


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
    for f in APP_TOOLS:
        server.tool(meta={"ui": {"resourceUri": CAL_UI_URI}})(f)
    _register_calendar_app(server)
    if host:
        server.settings.host = host
    if port:
        server.settings.port = port
    return server


class _NoBufferMiddleware:
    """回應加 X-Accel-Buffering: no——nginx 系反向代理（Synology、NPM…）
    看到會對該回應停用緩衝，SSE 事件才會即時送達；代理端不用改設定。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send2(msg):
            if msg["type"] == "http.response.start":
                msg.setdefault("headers", []).append((b"x-accel-buffering", b"no"))
            await send(msg)

        return await self.app(scope, receive, send2)


def _run_http(server: "FastMCP") -> None:
    import uvicorn
    # timeout_graceful_shutdown：SSE 長連線不會自己斷，收到 SIGTERM 後
    # 最多等 3 秒就強制關閉，容器 stop/重啟才不會卡住
    uvicorn.run(_NoBufferMiddleware(server.streamable_http_app()),
                host=server.settings.host, port=server.settings.port,
                timeout_graceful_shutdown=3)


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
        _run_http(build_server(args.host, args.port, oauth=True, issuer=args.issuer))
    elif args.http:
        _run_http(build_server(args.host, args.port))
    else:
        build_server().run()
