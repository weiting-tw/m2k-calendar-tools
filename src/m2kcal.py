#!/usr/bin/env python3
"""
m2kcal — Mail2000 (m2k) 行事曆 CLI
透過標準 CalDAV 協定查詢與建立會議，不需爬網頁。

伺服器分析結果:
  m2k = Openfind Mail2000, 後端為 SabreDAV (標準 CalDAV / RFC 4791)
  CalDAV base URL: https://mail.gss.com.tw/cgi-bin/cal/caldav/
  認證方式       : HTTP Basic (Mail2000 帳號 + 密碼)

安裝相依套件:
  pip install caldav icalendar

認證設定 (擇一，切勿把密碼寫進程式碼):
  匯出環境變數:
    export M2K_URL="https://mail.gss.com.tw/cgi-bin/cal/caldav/"
    export M2K_USER="wilber_chen@gss.com.tw"
    export M2K_PASS="你的密碼或應用程式專用密碼"
  或不設 M2K_PASS，執行時會安全地互動輸入 (getpass)。

使用範例:
  python3 src/m2kcal.py cals                       # 列出你有哪些日曆
  python3 src/m2kcal.py agenda --days 7            # 未來 7 天的會議
  python3 src/m2kcal.py list --start 2026-07-01 --end 2026-07-31
  python3 src/m2kcal.py book --title "專案週會" \
      --start "2026-07-08 14:00" --end "2026-07-08 15:00" \
      --location "3F 會議室" --desc "討論進度"
"""
import os
import re
import sys
import argparse
import getpass
import datetime as dt
import uuid
from html import escape as _esc

TW_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Taipei

try:
    import caldav
except ImportError:
    sys.exit("需要 caldav 套件，請先執行:  pip install caldav icalendar")

DEFAULT_URL = "https://mail.gss.com.tw/cgi-bin/cal/caldav/"


class M2KError(Exception):
    """可預期的操作錯誤（認證/參數/找不到資源）。
    CLI 端 catch 後印出離開；MCP 端 catch 後回傳錯誤字串，避免整個 server 被殺掉。"""


# ---------- .env 載入（不需額外套件）----------
def load_dotenv(path=None):
    """讀取 .env（KEY=VALUE，每行一組），寫入 os.environ（不覆蓋既有的）。
    找尋順序：指定路徑 → 目前工作目錄 → 本程式所在目錄 → 專案根目錄（src 的上層）。"""
    candidates = []
    if path:
        candidates.append(path)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(os.path.join(here, ".env"))
    candidates.append(os.path.join(os.path.dirname(here), ".env"))
    for p in candidates:
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)  # 既有環境變數優先
        return p
    return None


# ---------- 認證 ----------
_CREDS = None


def creds():
    """回傳 (url, user, pwd)，快取；缺就互動輸入。"""
    global _CREDS
    if _CREDS:
        return _CREDS
    url = os.environ.get("M2K_URL", DEFAULT_URL)
    user = os.environ.get("M2K_USER") or input("Mail2000 帳號 (e.g. name@gss.com.tw): ").strip()
    pwd = os.environ.get("M2K_PASS") or getpass.getpass("應用程式專用密碼 (輸入不會顯示): ")
    _CREDS = (url, user, pwd)
    return _CREDS


def parse_basic_auth(header):
    """解析 'Basic <base64(user:pwd)>' 標頭，回 (user, pwd)。
    供 MCP HTTP 模式 pass-through 用；格式不對丟 M2KError。"""
    import base64
    if not header or not header.lower().startswith("basic "):
        raise M2KError("需要 Authorization: Basic <base64(帳號:應用程式專用密碼)> 標頭。")
    try:
        user, sep, pwd = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
    except Exception:
        raise M2KError("Authorization 標頭不是合法的 Base64。")
    if not sep or not user or not pwd:
        raise M2KError("Authorization 標頭內容需為 帳號:應用程式專用密碼。")
    return user, pwd


# ---------- 連線 ----------
def connect(auth=None):
    """auth=(url, user, pwd) 可覆寫憑證（MCP HTTP 模式每請求不同人）；
    未給則走 creds()（環境變數 / .env / 互動輸入）。"""
    url, user, pwd = auth or creds()
    # timeout 必設：MCP OAuth 登入頁會等這個驗證，無 timeout 時瀏覽器端會無限轉圈
    client = caldav.DAVClient(url=url, username=user, password=pwd, timeout=30)
    try:
        principal = client.principal()
    except Exception as e:
        raise M2KError(f"登入失敗，請確認帳號密碼與 CalDAV 是否啟用: {e}")
    return principal


def cal_name(c):
    """相容不同 caldav 版本取日曆名稱（新版用 get_display_name）。"""
    try:
        return c.get_display_name()
    except Exception:
        try:
            return c.name
        except Exception:
            return str(c)


def pick_calendar(principal, name=None):
    cals = principal.calendars()
    if not cals:
        raise M2KError("找不到任何日曆。")
    if name:
        for c in cals:
            if (cal_name(c) or "").strip() == name:
                return c
        raise M2KError(f"找不到名為 '{name}' 的日曆。可用: " +
                       ", ".join(str(cal_name(c)) for c in cals))
    return cals[0]  # 預設第一本 (通常是主日曆)


# ---------- 時間解析 ----------
def parse_when(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise M2KError(f"時間格式看不懂: {s} (請用 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD')")


def _fmt_dt_line(line):
    """把 DTSTART/DTEND 那行轉成易讀時間；UTC(Z) 會換算台北。"""
    val = line.split(":", 1)[1].strip()
    z = val.endswith("Z")
    v = val[:-1] if z else val
    try:
        d = dt.datetime.strptime(v, "%Y%m%dT%H%M%S")
    except ValueError:
        try:
            return dt.datetime.strptime(v, "%Y%m%d").strftime("%Y-%m-%d (全天)")
        except ValueError:
            return val
    if z:
        d = d.replace(tzinfo=dt.timezone.utc).astimezone(TW_TZ)
    return d.strftime("%Y-%m-%d %H:%M")


def parse_ics(text):
    """從 ICS 抽出 SUMMARY / 時間 / 地點 / 與會者（驗證用）。"""
    text = text.replace("\r\n ", "").replace("\n ", "")  # unfold
    out = {"attendees": []}
    for line in text.splitlines():
        if line.startswith("SUMMARY:"):
            out["SUMMARY"] = line[8:].strip()
        elif line.startswith("LOCATION:"):
            out["location"] = line[9:].strip()
        elif line.startswith("DTSTART"):
            out["start"] = _fmt_dt_line(line)
        elif line.startswith("DTEND"):
            out["end"] = _fmt_dt_line(line)
        elif line.startswith("ATTENDEE"):
            m = re.search(r"mailto:([^\s;>]+)", line)
            if m:
                out["attendees"].append(m.group(1))
        elif line.startswith("SEQUENCE:"):
            try:
                out["SEQUENCE"] = int(line[9:].strip())
            except ValueError:
                pass
    return out


_WK = "一二三四五六日"


_BYDAY = {"MO": "一", "TU": "二", "WE": "三", "TH": "四", "FR": "五", "SA": "六", "SU": "日"}
_FREQ = {"DAILY": "每天", "WEEKLY": "每週", "MONTHLY": "每月", "YEARLY": "每年"}


def _rrule_text(c):
    r = c.get("rrule")
    if not r:
        return ""
    def _first(v):
        if isinstance(v, (list, tuple)):
            return v
        return [v] if v is not None else []
    try:
        fq = _first(r.get("FREQ"))
        freq = str(fq[0]) if fq else ""
        txt = _FREQ.get(freq, freq or "重複")
        byday = _first(r.get("BYDAY"))
        if byday:
            txt += " " + "".join(_BYDAY.get(str(d), str(d)) for d in byday)
        return txt
    except Exception:
        return "重複"


def _addr(v):
    """從 mailto 位址物件取 (顯示名, email)。"""
    email = str(v).split(":")[-1]
    cn = None
    try:
        cn = v.params.get("CN")
    except Exception:
        pass
    return (cn or email, email)


def _event_rows(events):
    """整理成 dict 清單，含所有可顯示欄位，依開始時間排序（時間轉台北）。"""
    def norm(x):
        """一律轉成台北牆鐘的 naive datetime——全天事件（date）天生 naive，
        aware 不去掉 tzinfo 的話混在一起 sort 會 TypeError。"""
        if x is None:
            return None
        if isinstance(x, dt.datetime):
            return x.astimezone(TW_TZ).replace(tzinfo=None) if x.tzinfo else x
        return dt.datetime(x.year, x.month, x.day)

    rows = []
    for ev in events:
        c = ev.icalendar_component
        s = c.get("dtstart").dt
        e = c.get("dtend").dt if c.get("dtend") else None
        atts = []
        a = c.get("attendee")
        if a:
            for it in (a if isinstance(a, list) else [a]):
                name, email = _addr(it)
                ps = ""
                try:
                    ps = it.params.get("PARTSTAT") or ""
                except Exception:
                    pass
                atts.append((name, email, ps))
        org = c.get("organizer")
        rows.append({
            "start": norm(s), "end": norm(e),
            "allday": not isinstance(s, dt.datetime),
            "summary": str(c.get("summary", "(無標題)")),
            "loc": str(c.get("location")) if c.get("location") else "",
            "desc": str(c.get("description")) if c.get("description") else "",
            "atts": atts,
            "organizer": _addr(org)[0] if org else "",
            "rrule": _rrule_text(c),
            "url": str(c.get("url")) if c.get("url") else "",
            "status": str(c.get("status")) if c.get("status") else "",
            "uid": str(c.get("uid")) if c.get("uid") else "",
        })
    rows.sort(key=lambda r: r["start"])
    return rows


def events_json(events):
    """整理成 JSON 可序列化的清單（MCP App UI 用）。時間為台北牆鐘字串。"""
    out = []
    for r in _event_rows(events):
        fmt = "%Y-%m-%d" if r["allday"] else "%Y-%m-%d %H:%M"
        out.append({
            "uid": r["uid"], "summary": r["summary"],
            "start": r["start"].strftime(fmt),
            "end": r["end"].strftime(fmt) if r["end"] else "",
            "allday": r["allday"], "location": r["loc"], "description": r["desc"],
            "organizer": r["organizer"], "rrule": r["rrule"],
            "attendees": [{"name": n, "email": em, "partstat": ps}
                          for n, em, ps in r["atts"]],
        })
    return out


_URL_RE = re.compile(r'(https?://[^\s<>"\')\]]+)')


def _linkify(text):
    """HTML-escape 文字，並把網址轉成可點連結，換行轉 <br>。"""
    parts = _URL_RE.split(text or "")
    out = []
    for i, p in enumerate(parts):
        if i % 2 == 1:  # 網址
            u = _esc(p)
            out.append(f'<a href="{u}" target="_blank" rel="noopener">{u}</a>')
        else:
            out.append(_esc(p).replace("\n", "<br>"))
    return "".join(out)


def render_grouped(events):
    """把事件按『天』分組、每天列出時段，較易讀。"""
    rows = _event_rows(events)
    out, cur = [], None
    for r in rows:
        s, e = r["start"], r["end"]
        if s.date() != cur:
            cur = s.date()
            out.append(f"\n📅 {cur.isoformat()} (週{_WK[cur.weekday()]})")
        tstr = "全天" if r["allday"] else f"{s.strftime('%H:%M')}–{e.strftime('%H:%M') if e else '?'}"
        line = f"   {tstr:<11} {r['summary']}"
        if r["rrule"]:
            line += f"  ⟳{r['rrule']}"
        if r["loc"]:
            line += f"  @ {r['loc']}"
        out.append(line)
        extra = []
        if r["organizer"]:
            extra.append("召集:" + r["organizer"])
        if r["atts"]:
            ppl = ", ".join(f"{nm} <{em}>" if nm and nm != em else em
                            for nm, em, _ in r["atts"])
            extra.append(f"與會 {len(r['atts'])} 人: {ppl}")
        if extra:
            out.append("       " + " · ".join(extra))
        if r["uid"]:
            out.append(f"       id: {r['uid']}")
    return "\n".join(out) if out else "（無事件）"


def render_board_html(events, title):
    """產生看板樣式 HTML：每天一欄、事件為卡片。"""
    from collections import OrderedDict
    rows = _event_rows(events)
    days = OrderedDict()
    for r in rows:
        days.setdefault(r["start"].date(), []).append(r)

    ps_sym = {"ACCEPTED": "✓", "DECLINED": "✗", "TENTATIVE": "?", "NEEDS-ACTION": "·"}
    palette = ["#2563eb", "#16a34a", "#7c3aed", "#db2777", "#ea580c",
               "#0891b2", "#ca8a04"]
    cols = []
    for i, (d, items) in enumerate(days.items()):
        accent = palette[i % len(palette)]
        cards = []
        for r in items:
            s, e = r["start"], r["end"]
            t = "全天" if r["allday"] else (f"{s:%H:%M}–{e:%H:%M}" if e else f"{s:%H:%M}")
            meta = ""
            if r["rrule"]:
                meta += f'<div class="meta">⟳ {_esc(r["rrule"])}</div>'
            if r["loc"]:
                meta += f'<div class="loc">📍 {_linkify(r["loc"])}</div>'
            if r["organizer"]:
                meta += f'<div class="meta">👤 召集人：{_esc(r["organizer"])}</div>'
            if r["url"]:
                meta += f'<div class="meta">🔗 {_linkify(r["url"])}</div>'
            atth = ""
            if r["atts"]:
                lis = "".join(
                    f'<div class="att">{ps_sym.get((ps or "").upper(), "·")} {_esc(nm)}'
                    f'<span class="attmail">&lt;{_esc(em)}&gt;</span></div>'
                    for nm, em, ps in r["atts"])
                atth = (f'<details class="att-d"><summary>與會者 {len(r["atts"])}</summary>'
                        f'<div class="attbody">{lis}</div></details>')
            desch = ""
            if r["desc"] and r["desc"].strip():
                desch = (f'<details class="desc"><summary></summary>'
                         f'<div class="descbody">{_linkify(r["desc"])}</div></details>')
            cards.append(
                f'<div class="card" style="border-left-color:{accent}">'
                f'<div class="time">{t}</div>'
                f'<div class="title">{_linkify(r["summary"])}</div>'
                f'{meta}{atth}{desch}</div>')
        body = "".join(cards) or '<div class="empty">—</div>'
        is_today = (d == dt.date.today())
        cols.append(
            f'<div class="col{" today" if is_today else ""}">'
            f'<div class="colhdr" style="background:{accent}">'
            f'{d.strftime("%m/%d")} <span>週{_WK[d.weekday()]}</span>'
            f'{" · 今天" if is_today else ""}</div>'
            f'<div class="cards">{body}</div></div>')
    board = "".join(cols) or '<p style="color:#64748b">這段期間沒有事件。</p>'
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;padding:20px;background:#f1f5f9;font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;color:#0f172a}}
  h1{{font-size:18px;margin:0 0 14px}}
  .board{{display:flex;gap:12px;overflow-x:auto;padding-bottom:12px;align-items:flex-start}}
  .col{{flex:0 0 220px;background:#e2e8f0;border-radius:12px;overflow:hidden}}
  .col.today{{outline:2px solid #2563eb}}
  .colhdr{{color:#fff;font-weight:600;padding:8px 12px;font-size:14px}}
  .colhdr span{{opacity:.85;font-weight:400;margin-left:4px}}
  .cards{{padding:8px;display:flex;flex-direction:column;gap:8px;min-height:40px}}
  .card{{background:#fff;border-radius:8px;border-left:4px solid #2563eb;padding:8px 10px;box-shadow:0 1px 2px rgba(0,0,0,.08)}}
  .card .time{{font-size:12px;color:#475569;font-variant-numeric:tabular-nums}}
  .card .title{{font-size:13px;font-weight:600;margin-top:2px;line-height:1.35}}
  .card .loc{{font-size:11px;color:#64748b;margin-top:4px}}
  .card .meta{{font-size:11px;color:#64748b;margin-top:4px}}
  .card a{{color:#2563eb;word-break:break-all}}
  .att-d{{margin-top:6px}}
  .att-d>summary{{font-size:11px;color:#0f766e;cursor:pointer;list-style:none;user-select:none}}
  .att-d>summary::-webkit-details-marker{{display:none}}
  .att-d>summary::before{{content:"▸ "}}
  .att-d[open]>summary::before{{content:"▾ "}}
  .attbody{{margin-top:6px;border-top:1px dashed #e2e8f0;padding-top:6px;max-height:200px;overflow:auto}}
  .att{{font-size:11px;color:#334155;padding:1px 0}}
  .attmail{{color:#94a3b8;margin-left:4px}}
  .desc{{margin-top:6px}}
  .desc>summary{{font-size:11px;color:#2563eb;cursor:pointer;list-style:none;user-select:none}}
  .desc>summary::-webkit-details-marker{{display:none}}
  .desc>summary::before{{content:"▸ 描述";}}
  .desc[open]>summary::before{{content:"▾ 描述";}}
  .descbody{{font-size:11px;color:#334155;margin-top:6px;max-height:240px;overflow:auto;line-height:1.55;border-top:1px dashed #e2e8f0;padding-top:6px}}
  .empty{{color:#94a3b8;text-align:center;padding:12px 0}}
</style></head>
<body><h1>{_esc(title)}</h1><div class="board">{board}</div></body></html>"""


# ---------- 指令 ----------
def cmd_cals(args):
    p = connect()
    print("你的日曆:")
    for c in p.calendars():
        print(f"  - {cal_name(c)}")


def cmd_board(args):
    import webbrowser
    p = connect()
    cal = pick_calendar(p, args.calendar)
    start = dt.datetime.now()
    end = start + dt.timedelta(days=args.days)
    events = cal.search(start=start, end=end, event=True, expand=True)
    title = f"{cal_name(cal)}｜{start:%m/%d} 起 {args.days} 天（{len(events)} 筆）"
    doc = render_board_html(events, title)
    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("看板已產生：", out)
    if not args.no_open:
        try:
            webbrowser.open("file://" + out)
        except Exception:
            pass


def cmd_list(args):
    p = connect()
    cal = pick_calendar(p, args.calendar)
    start = parse_when(args.start)
    end = parse_when(args.end)
    events = cal.search(start=start, end=end, event=True, expand=True)
    print(f"[{cal_name(cal)}] {args.start} ~ {args.end}，共 {len(events)} 筆:")
    print(render_grouped(events))


def cmd_agenda(args):
    p = connect()
    cal = pick_calendar(p, args.calendar)
    start = dt.datetime.now()
    end = start + dt.timedelta(days=args.days)
    events = cal.search(start=start, end=end, event=True, expand=True)
    print(f"[{cal_name(cal)}] 未來 {args.days} 天，共 {len(events)} 筆:")
    print(render_grouped(events))


TZID = "Asia/Taipei"  # GSS 在台灣


def _zulu(t):
    """UTC 時間戳 (YYYYMMDDTHHMMSSZ)，給 DTSTAMP/CREATED 等用。"""
    if t.tzinfo is None:
        t = t.astimezone()
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _local_wall(t):
    """台北本地牆鐘時間 (YYYYMMDDTHHMMSS)，配合 TZID 使用。"""
    tw = dt.timezone(dt.timedelta(hours=8))
    if t.tzinfo is not None:
        t = t.astimezone(tw)
    return t.strftime("%Y%m%dT%H%M%S")


def build_ics(title, start, end, location="", desc="", attendees=None,
              organizer="", uid=None, stamp=None, rrule="", reminder_minutes=0):
    """組出 iCalendar 字串，格式對齊 Mail2000（帶 VTIMEZONE + TZID，
    Mail2000/SabreDAV 後端不吃純 UTC/浮動時間，會回 500）。純函式，方便測試。
    rrule：RRULE 內容（如 'FREQ=WEEKLY;UNTIL=...'）；reminder_minutes：開始前 N 分鐘 VALARM。"""
    uid = uid or str(uuid.uuid4())
    stamp = stamp or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//m2kcal//CalDAV CLI//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "BEGIN:VTIMEZONE",
        f"TZID:{TZID}",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"CREATED:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        "SEQUENCE:0",
        f"DTSTART;TZID={TZID}:{_local_wall(start)}",
        f"DTEND;TZID={TZID}:{_local_wall(end)}",
        f"SUMMARY:{title}",
    ]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if organizer:
        lines.append(f"ORGANIZER:mailto:{organizer}")
    if location:
        lines.append(f"LOCATION:{location}")
    if desc:
        lines.append(f"DESCRIPTION:{desc}")
    # 與會者：此站台 CalDAV 無排程 (schedule-outbox 404)，ATTENDEE 只記錄、不會自動寄邀請。
    if attendees:
        for a in attendees:
            email = a.strip()
            lines.append(
                f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{email}"
            )
    if reminder_minutes:
        lines += ["BEGIN:VALARM", f"TRIGGER:-PT{int(reminder_minutes)}M",
                  "ACTION:DISPLAY", f"DESCRIPTION:{title}", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


# ---------- 聯絡人（從行事曆歷史萃取，供模糊人名查 email） ----------
def collect_contacts(cal, start=None, end=None):
    """掃行事曆事件的與會者/召集人，回 {email: {"name","count","last"}}。
    公司通訊錄需 webmail session 拿不到；跟你開過會的人都在這裡。"""
    start = start or dt.datetime.now() - dt.timedelta(days=365)
    end = end or dt.datetime.now() + dt.timedelta(days=180)
    out = {}
    for ev in cal.search(start=start, end=end, event=True):
        c = ev.icalendar_component
        when = ""
        try:
            when = c.get("dtstart").dt.strftime("%Y-%m-%d")
        except Exception:
            pass
        people = []
        a = c.get("attendee")
        if a:
            people += [_addr(x) for x in (a if isinstance(a, list) else [a])]
        if c.get("organizer"):
            people.append(_addr(c.get("organizer")))
        for name, email in people:
            email = email.strip().lower()
            if "@" not in email:
                continue
            rec = out.setdefault(email, {"name": "", "count": 0, "last": ""})
            rec["count"] += 1
            if name and name != email and not rec["name"]:
                rec["name"] = name
            if when > rec["last"]:
                rec["last"] = when
    return out


def match_contacts(contacts, query):
    """模糊比對名字：完整 local-part > 前綴（含底線分段）> 包含 > 顯示名包含。
    回傳 [(score, email, rec)]，分數與出現次數高者在前。"""
    q = query.strip().lower()
    if not q:
        return []
    res = []
    for email, rec in contacts.items():
        local = email.split("@")[0]
        name = (rec.get("name") or "").lower()
        if local == q:
            score = 100
        elif local.startswith(q) or any(p.startswith(q) for p in local.split("_")):
            score = 80
        elif q in local:
            score = 60
        elif q in name:
            score = 50
        else:
            continue
        res.append((score, email, rec))
    res.sort(key=lambda t: (-t[0], -t[2]["count"]))
    return res


# ---------- free-busy / 空檔計算 ----------
def parse_freebusy(text):
    """解析 VFREEBUSY 回應中的 FREEBUSY 行（UTC period），
    回傳台北時間 naive (start, end) 清單（依開始時間排序）。"""
    text = text.replace("\r\n ", "").replace("\n ", "")
    out = []
    for line in text.splitlines():
        if not line.startswith("FREEBUSY"):
            continue
        for period in line.split(":", 1)[1].split(","):
            try:
                a, b = period.strip().split("/")
                pair = []
                for v in (a, b):
                    d = dt.datetime.strptime(v, "%Y%m%dT%H%M%SZ")
                    pair.append(d.replace(tzinfo=dt.timezone.utc)
                                 .astimezone(TW_TZ).replace(tzinfo=None))
                out.append((pair[0], pair[1]))
            except ValueError:
                continue  # start/duration 形式或壞行，略過
    return sorted(out)


def free_slots(busy, start, end, duration_min=60,
               day_start="09:00", day_end="18:00", include_weekends=False):
    """純函式：在 [start, end) 每天的工作時段扣除 busy 區間，
    回傳長度 >= duration_min 的空檔 (start, end) 清單。"""
    def hm(s):
        h, m = s.split(":")
        return int(h), int(m)
    sh, sm = hm(day_start)
    eh, em = hm(day_end)
    merged = []
    for s0, e0 in sorted(busy):
        if merged and s0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e0))
        else:
            merged.append((s0, e0))
    out = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        if include_weekends or day.weekday() < 5:
            ws = day.replace(hour=sh, minute=sm)
            we = day.replace(hour=eh, minute=em)
            cur = max(ws, start)
            for s0, e0 in merged:
                if e0 <= cur or s0 >= we:
                    continue
                if s0 > cur:
                    out.append((cur, min(s0, we)))
                cur = max(cur, e0)
                if cur >= we:
                    break
            if cur < min(we, end):
                out.append((cur, min(we, end)))
        day += dt.timedelta(days=1)
    return [(s0, e0) for s0, e0 in out
            if (e0 - s0).total_seconds() >= duration_min * 60]


def _mk_attendee(email):
    from icalendar.prop import vCalAddress, vText
    a = vCalAddress("mailto:" + email)
    a.params["ROLE"] = vText("REQ-PARTICIPANT")
    a.params["PARTSTAT"] = vText("NEEDS-ACTION")
    a.params["RSVP"] = vText("TRUE")
    return a


def update_event_ics(ics_text, title=None, start=None, end=None, location=None,
                     desc=None, add_attendees=None, remove_attendees=None,
                     respond=None):
    """純函式：讀入既有事件的 ICS，套用指定變更後回傳新 ICS 文字（None＝不變）。
    respond=(email, PARTSTAT)：把該與會者的出席狀態改為 ACCEPTED/DECLINED/TENTATIVE。
    只動有給的欄位，其餘屬性（RRULE、VALARM、X-…）原樣保留；
    SEQUENCE +1、更新 DTSTAMP/LAST-MODIFIED。"""
    from icalendar import Calendar as _ICal, Timezone, TimezoneStandard
    from icalendar.prop import vDatetime, vText
    ical = _ICal.from_ical(ics_text)
    # Mail2000 存的事件常帶 METHOD:REQUEST，但 CalDAV PUT 禁止 METHOD
    # （RFC 4791 §4.1），帶著 PUT 回去 SabreDAV 會回 415，必須拿掉。
    ical.pop("METHOD", None)
    evs = [c for c in ical.walk("VEVENT")]
    if not evs:
        raise M2KError("這筆事件資料裡沒有 VEVENT，無法修改。")
    ev = evs[0]

    if title:
        ev.pop("SUMMARY", None)
        ev.add("SUMMARY", title)
    if location:
        ev.pop("LOCATION", None)
        ev.add("LOCATION", location)
    if desc:
        ev.pop("DESCRIPTION", None)
        ev.add("DESCRIPTION", desc)

    def _wall(key, t):
        # 台北牆鐘時間 + TZID 參數（Mail2000 不吃純 UTC/浮動時間，見 build_ics）
        p = vDatetime(dt.datetime.strptime(_local_wall(t), "%Y%m%dT%H%M%S"))
        p.params["TZID"] = vText(TZID)
        ev.pop(key, None)
        ev[key] = p
    if start:
        _wall("DTSTART", start)
    if end:
        _wall("DTEND", end)
    if (start or end) and not list(ical.walk("VTIMEZONE")):
        tz = Timezone()
        tz.add("TZID", TZID)
        std = TimezoneStandard()
        std.add("DTSTART", dt.datetime(1970, 1, 1))
        std.add("TZOFFSETFROM", dt.timedelta(hours=8))
        std.add("TZOFFSETTO", dt.timedelta(hours=8))
        std.add("TZNAME", "CST")
        tz.add_component(std)
        ical.add_component(tz)

    if respond:
        email, status = respond
        cur = ev.get("ATTENDEE")
        cur = list(cur) if isinstance(cur, list) else ([cur] if cur else [])
        hit = False
        for a in cur:
            if str(a).split(":")[-1].lower() == email.strip().lower():
                a.params["PARTSTAT"] = vText(status)
                hit = True
        if not hit:
            raise M2KError(f"{email} 不在此會議的與會者名單中，無法回覆出席狀態。")

    if add_attendees or remove_attendees:
        cur = ev.get("ATTENDEE")
        cur = list(cur) if isinstance(cur, list) else ([cur] if cur else [])
        rm = {e.strip().lower() for e in (remove_attendees or [])}
        keep = [a for a in cur if str(a).split(":")[-1].lower() not in rm]
        have = {str(a).split(":")[-1].lower() for a in keep}
        for e in (add_attendees or []):
            e = e.strip()
            if e and e.lower() not in have:
                keep.append(_mk_attendee(e))
                have.add(e.lower())
        ev.pop("ATTENDEE", None)
        for a in keep:
            ev.add("ATTENDEE", a)

    try:
        seq = int(ev.get("SEQUENCE", 0))
    except Exception:
        seq = 0
    ev.pop("SEQUENCE", None)
    ev.add("SEQUENCE", seq + 1)
    now = dt.datetime.now(dt.timezone.utc)
    for k in ("DTSTAMP", "LAST-MODIFIED"):
        ev.pop(k, None)
        ev.add(k, now)
    return ical.to_ical().decode("utf-8")


def find_event_by_uid(cal, uid):
    """依 UID 取回既有事件（uid 見 agenda/list 輸出的 id: 欄位）。"""
    try:
        return cal.event_by_uid(uid)
    except Exception:
        raise M2KError(f"找不到 id 為 {uid} 的事件。請先用 agenda/list 查出正確的 id。")


def put_and_verify(cal, ics, uid, auth=None, put_url=None, expect_seq=None):
    """PUT 事件到日曆 collection 並 GET 驗證，CLI 與 MCP 共用。
    put_url 未給時為新建（collection + uid.ics）；更新既有事件時
    傳入該事件自己的 URL（server 端 href 不一定等於 uid.ics），並帶
    expect_seq＝新 ICS 的 SEQUENCE——僅驗 uid 會被舊版本騙過（更新被
    拒時 GET 回的舊資料一樣含 uid），必須比對 SEQUENCE 才知道有生效。

    直接 PUT（不走 caldav 套件的條件式 PUT / If-None-Match，那會讓部分
    Mail2000/SabreDAV 後端回 500）。Mail2000 的 PUT 也可能回 500（存檔後的
    通知步驟出錯）但事件其實已寫入，因此不看 PUT status，以 GET 驗證為準。
    回傳 (put_status, parse_ics 結果)；驗證失敗丟 M2KError。"""
    import requests
    url, user, pwd = auth or creds()
    if not put_url:
        coll = str(cal.url)
        if not coll.endswith("/"):
            coll += "/"
        put_url = coll + uid + ".ics"
    r = requests.put(
        put_url, data=ics.encode("utf-8"),
        headers={"Content-Type": "text/calendar; charset=utf-8"},
        auth=(user, pwd), timeout=30,
    )
    g = requests.get(put_url, auth=(user, pwd), timeout=30)
    if not (g.status_code == 200 and uid in g.text):
        msg = f"建立失敗：PUT HTTP {r.status_code}、驗證 GET HTTP {g.status_code}"
        if r.text:
            msg += "\n伺服器回應：" + r.text[:500]
        raise M2KError(msg)
    info = parse_ics(g.text)
    if expect_seq is not None and info.get("SEQUENCE") != expect_seq:
        msg = (f"更新未生效：伺服器拒絕了這次 PUT（HTTP {r.status_code}），"
               f"行事曆上仍是舊版本（SEQUENCE {info.get('SEQUENCE')}，預期 {expect_seq}）。")
        if r.text:
            msg += "\n伺服器回應：" + r.text[:500]
        raise M2KError(msg)
    return r.status_code, info


def cmd_diag(args):
    """診斷：印出日曆 collection 網址、你的權限、支援的元件、PUT 測試。"""
    import requests
    p = connect()
    cal = pick_calendar(p, args.calendar)
    url, user, pwd = creds()
    coll = str(cal.url)
    print("日曆 collection:", coll)
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
            '<d:current-user-privilege-set/><c:supported-calendar-component-set/>'
            '<d:resourcetype/><d:owner/></d:prop></d:propfind>')
    r = requests.request("PROPFIND", coll, data=body,
                         headers={"Depth": "0", "Content-Type": "application/xml"},
                         auth=(user, pwd), timeout=30)
    print("PROPFIND", r.status_code)
    print(r.text[:2000])
    # OPTIONS：看這個 collection 允許哪些方法
    ro = requests.options(coll, auth=(user, pwd), timeout=30)
    print("\nOPTIONS", ro.status_code, "Allow:", ro.headers.get("Allow"))
    print("DAV:", ro.headers.get("DAV"))


def cmd_raw(args):
    """印出一筆現有事件的原始 ICS（用來對照 Mail2000 接受的格式）。"""
    p = connect()
    cal = pick_calendar(p, args.calendar)
    start = dt.datetime.now()
    end = start + dt.timedelta(days=args.days)
    events = cal.search(start=start, end=end, event=True)
    if not events:
        print("這段期間找不到事件，試試加大 --days。")
        return
    print("=== 第一筆事件原始 ICS ===")
    print(events[0].data)


def cmd_book(args):
    p = connect()
    cal = pick_calendar(p, args.calendar)
    start = parse_when(args.start)
    end = parse_when(args.end) if args.end else start + dt.timedelta(hours=1)
    url, user, pwd = creds()
    uid = str(uuid.uuid4())
    ics = build_ics(args.title, start, end, args.location, args.desc,
                    attendees=args.attendee, organizer=user, uid=uid)
    try:
        put_status, info = put_and_verify(cal, ics, uid)
    except M2KError as e:
        print(e)
        print("\n--- 送出的 ICS ---\n" + ics)
        sys.exit(1)

    if put_status not in (200, 201, 204):
        print(f"（伺服器 PUT 回 {put_status}，但已驗證事件確實建立）")
    print("已建立並驗證：")
    print(f"  標題: {info.get('SUMMARY', args.title)}")
    print(f"  時間: {info.get('start','?')}  →  {info.get('end','?')}")
    if info.get("location"):
        print(f"  地點: {info['location']}")
    if info.get("attendees"):
        print("  與會者: " + ", ".join(info["attendees"]))
        print("  (提醒：此站台無 CalDAV 排程，系統不會自動寄邀請信；"
              "與會者只記錄在事件中)")


def main():
    ap = argparse.ArgumentParser(description="Mail2000 (m2k) 行事曆 CLI — 走 CalDAV")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("cals", help="列出日曆").set_defaults(func=cmd_cals)

    pa = sub.add_parser("agenda", help="未來 N 天的會議")
    pa.add_argument("--days", type=int, default=7)
    pa.add_argument("--calendar")
    pa.set_defaults(func=cmd_agenda)

    pbd = sub.add_parser("board", help="產生看板樣式 HTML（每天一欄）並開啟")
    pbd.add_argument("--days", type=int, default=7)
    pbd.add_argument("--out", default="m2k-board.html", help="輸出 HTML 檔名")
    pbd.add_argument("--no-open", action="store_true", help="只產生檔案、不自動開瀏覽器")
    pbd.add_argument("--calendar")
    pbd.set_defaults(func=cmd_board)

    pd = sub.add_parser("diag", help="診斷寫入權限與 collection（除錯用）")
    pd.add_argument("--calendar")
    pd.set_defaults(func=cmd_diag)

    pr = sub.add_parser("raw", help="印出一筆現有事件的原始 ICS（除錯用）")
    pr.add_argument("--days", type=int, default=30)
    pr.add_argument("--calendar")
    pr.set_defaults(func=cmd_raw)

    pl = sub.add_parser("list", help="指定期間的會議")
    pl.add_argument("--start", required=True)
    pl.add_argument("--end", required=True)
    pl.add_argument("--calendar")
    pl.set_defaults(func=cmd_list)

    pb = sub.add_parser("book", help="建立 / 預約會議")
    pb.add_argument("--title", required=True)
    pb.add_argument("--start", required=True)
    pb.add_argument("--end")
    pb.add_argument("--location")
    pb.add_argument("--desc")
    pb.add_argument("--attendee", action="append",
                    help="與會者 email，可重複使用加多人 (e.g. --attendee a@x --attendee b@x)")
    pb.add_argument("--calendar")
    pb.set_defaults(func=cmd_book)

    args = ap.parse_args()
    load_dotenv()  # 若同目錄有 .env 會自動載入
    try:
        args.func(args)
    except M2KError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
