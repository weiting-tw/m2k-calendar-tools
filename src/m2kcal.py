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
    export M2K_USER="you@example.com"
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
    user = os.environ.get("M2K_USER") or input("Mail2000 帳號 (e.g. name@example.com): ").strip()
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
    client = caldav.DAVClient(url=url, username=user, password=pwd)
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
        if x is None:
            return None
        if isinstance(x, dt.datetime):
            return x.astimezone(TW_TZ) if x.tzinfo else x
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
        })
    rows.sort(key=lambda r: r["start"])
    return rows


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
            extra.append(f"與會 {len(r['atts'])} 人")
        if extra:
            out.append("       " + " · ".join(extra))
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
              organizer="", uid=None, stamp=None):
    """組出 iCalendar 字串，格式對齊 Mail2000（帶 VTIMEZONE + TZID，
    Mail2000/SabreDAV 後端不吃純 UTC/浮動時間，會回 500）。純函式，方便測試。"""
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
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def put_and_verify(cal, ics, uid, auth=None):
    """PUT 事件到日曆 collection 並 GET 驗證，CLI 與 MCP 共用。

    直接 PUT（不走 caldav 套件的條件式 PUT / If-None-Match，那會讓部分
    Mail2000/SabreDAV 後端回 500）。Mail2000 的 PUT 也可能回 500（存檔後的
    通知步驟出錯）但事件其實已寫入，因此不看 PUT status，以 GET 驗證為準。
    回傳 (put_status, parse_ics 結果)；驗證失敗丟 M2KError。"""
    import requests
    url, user, pwd = auth or creds()
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
    return r.status_code, parse_ics(g.text)


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
