#!/usr/bin/env python3
"""會議寫入走 webmail 原生 calsrv API（見 docs/adr/0004）。

CalDAV 建的會議只存在召集人自己的行事曆：站台沒有排程，ATTENDEE 只是資料。
webmail 前端自己用的 calsrv API 帶 send_meeting_mail=true 時，伺服器會把會議寫進
每位與會者的行事曆並寄通知信——所以寫入一律走這裡，CalDAV 只留讀取。

欄位怎麼序列化以 webmail 前端 calendar.js 的 eventEditObject.toRequestData 為準；
重複會議的三種範圍照它的 eventRecurrenceModify（只改這次／此次及以後／全部）。

表單（form）是送給伺服器的 dict，也是這裡唯一的事件表示：新建用 new_form 組、
既有事件用 edit_form 從 GET 回來的 JSON 還原、變更用 apply 套上去。值一律是字串。
HTTP 只經過 Calsrv 的 send（預設 _send），測試換掉它就不必連網。
"""
import datetime as dt
import json
import re

import m2kcal
from m2kcal import M2KError, TW_TZ

CALSRV = "/cgi-bin/cal/calsrv"
TZ = "Asia/Taipei"
OFFSET = 28800
LINK_PREFIX = "會議連結: "      # 伺服器不收 url（實測會忽略），連結寫在描述第一行

# 前端用陣列索引對應 GET 回來的數字（calendar.js 的 eventEditObject 初始化）
_ROLES = ("CHAIR", "REQ-PARTICIPANT", "OPT-PARTICIPANT", "NON-PARTICIPANT")
_STATUS = ("NEEDS-ACTION", "ACCEPTED", "DECLINED", "TENTATIVE", "DELEGATED",
           "COMPLETED", "IN-PROCESS")
_WEEKDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

# 編號欄位（attendee1、attendee_cn1…）以 *_num 為筆數
_ATT = ("attendee", "attendee_cn", "attendee_role", "attendee_reply_status")
_ALARM = ("alarm_trigger", "alarm_action", "alarm_repeat", "alarm_duration",
          "alarm_summary", "alarm_description", "alarm_attendee")
_EXDATE = ("exdate",)
_RRULE = ("has_rrule", "freq", "interval", "by_day", "by_setpos", "by_monthday",
          "by_month", "count", "until")

# session 失效時伺服器的樣子：rspCode -100（前端 tool.ajax 也這樣判斷），
# 或 nginx 直接把人導去登入／回 410。這些情況請求沒被處理，重換 cookie 重送是安全的；
# 其餘（例如 500）可能已經寫入，重送會建出兩筆，所以不重試。
_SESSION_GONE_HTTP = (301, 302, 303, 307, 401, 403, 410)


class Rejected(M2KError):
    """伺服器明確拒絕（rspCode 非 0）：這次請求沒有生效。"""


class Unclear(M2KError):
    """結果不明（HTTP 500、回應不是 JSON、連線中斷或逾時）：伺服器可能已經處理了。"""


_UNCLEAR_HINT = "伺服器可能已處理，請先到 webmail 或用 agenda 確認，不要直接重試"


# ---------- 時間 ----------
def _wall(t):
    return m2kcal._local_wall(t)


def _day(d):
    return f"{d:%Y%m%d}"


def _from_epoch(sec):
    return dt.datetime.fromtimestamp(int(sec), TW_TZ).replace(tzinfo=None)


def _parse_wall(v):
    return dt.datetime.strptime(v, "%Y%m%dT%H%M%S" if "T" in v else "%Y%m%d")


def _date_field(v):
    """全天事件的 dstart/dend。實機格式沒錄到，epoch 與常見日期字串都接。"""
    s = str(v).strip()
    if re.fullmatch(r"-?\d{9,}", s):
        return _from_epoch(s).date()
    m = re.match(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})", s)
    if not m:
        raise M2KError(f"看不懂伺服器回的全天日期：{v!r}")
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _times(start, end, all_day):
    if all_day:
        sd = start.date() if isinstance(start, dt.datetime) else start
        ed = end.date() if isinstance(end, dt.datetime) else end
        if ed <= sd:
            ed = sd + dt.timedelta(days=1)
        return {"dtstart": _day(sd), "dtend": _day(ed)}
    return {"dtstart": _wall(start), "dtend": _wall(end)}


def _until(day, start, all_day):
    """前端的做法：全天給日期；否則是截止那天、時間沿用開始時間。"""
    return _day(day) if all_day else _wall(dt.datetime.combine(day, start.time()))


def _dur(sec):
    """前端的 h()：秒數 → ±PT{秒}S。"""
    return ("-" if sec < 0 else "+") + f"PT{abs(int(sec))}S"


def _int(v, default=0):
    """比照前端 parseInt(v, 10) || default：取開頭的整數，讀不到就用預設值。"""
    m = re.match(r"\s*([+-]?\d+)", "" if v is None else str(v))
    return int(m.group(1)) if m else default


# ---------- 編號欄位 ----------
def _rows(form, num, keys):
    return [{k: form[f"{k}{i}"] for k in keys if f"{k}{i}" in form}
            for i in range(1, int(form.get(num) or 0) + 1)]


def _put_rows(form, num, keys, rows):
    for i in range(1, int(form.get(num) or 0) + 1):
        for k in keys:
            form.pop(f"{k}{i}", None)
    form.pop(num, None)
    if rows:
        form[num] = str(len(rows))
        for i, row in enumerate(rows, 1):
            for k in keys:
                if k in row:
                    form[f"{k}{i}"] = row[k]


def _addr(a):
    return re.sub(r"^mailto:", "", str(a or ""), flags=re.I).strip()


def attendees(form):
    """表單裡的與會者 email（照順序）。"""
    return [_addr(r.get("attendee")) for r in _rows(form, "attendee_num", _ATT)]


def _new_attendee(email, status=None):
    row = {"attendee": "mailto:" + email, "attendee_cn": email.split("@")[0],
           "attendee_role": "REQ-PARTICIPANT"}
    if status:
        row["attendee_reply_status"] = status
    return row


def is_mine(ev, me):
    """這場是不是 me 召集的：只看 organizer（沒有 organizer、或不知道 me 是誰，都當成
    自己的）。me 沒帶網域時只比 @ 前面的帳號。
    不看 creator：實機上伺服器寫進與會者行事曆的那份，creator 是與會者本人。
    前端的規則：organizer 不是自己時 send_meeting_mail 預設 false——與會者改的只是自己那份。"""
    org = _addr((ev or {}).get("organizer")).lower()
    me = (me or "").strip().lower()
    if not org or not me:
        return True
    return org == me if "@" in me else org.split("@")[0] == me


def _sync_send(form, mine=True):
    # 帶了與會者就是要邀請他們；沒有與會者時寄信沒有對象；別人召集的只改自己那份、不寄
    form["send_meeting_mail"] = "true" if mine and attendees(form) else "false"


def _display_alarm(minutes):
    return {"alarm_trigger": _dur(-int(minutes) * 60), "alarm_action": "DISPLAY",
            "alarm_repeat": "1", "alarm_duration": _dur(0), "alarm_summary": "",
            "alarm_description": "", "alarm_attendee": ""}


# ---------- 描述裡的會議連結 ----------
def _split_link(desc):
    first, _, rest = (desc or "").partition("\n")
    if first.startswith(LINK_PREFIX):
        return first[len(LINK_PREFIX):].strip(), rest
    return "", desc or ""


def _with_link(desc, url):
    return (LINK_PREFIX + url.strip() + ("\n" + desc if desc else "")) if url else (desc or "")


# ---------- 重複規則 ----------
def _parse_until(v):
    v = v.strip()
    if v.endswith("Z"):
        return dt.datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(
            tzinfo=dt.timezone.utc).astimezone(TW_TZ).replace(tzinfo=None)
    return _parse_wall(v)


def _rrule_fields(rrule, start, all_day):
    """RRULE 字串（compose_rrule 的輸出）→ 前端的重複欄位。"""
    parts = {}
    for p in rrule.split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            parts[k.strip().upper()] = v.strip()
    freq = parts.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        raise M2KError(f"原生行事曆不支援這個重複頻率：{rrule}")
    f = {"has_rrule": "true", "freq": freq, "interval": parts.get("INTERVAL") or "1"}
    days = [d for d in parts.get("BYDAY", "").upper().split(",") if d]
    if freq == "WEEKLY":
        f["by_day"] = ",".join(days) or _WEEKDAY[start.weekday()]
    elif freq == "MONTHLY":
        m = re.fullmatch(r"(-?[1-4])(MO|TU|WE|TH|FR|SA|SU)", days[0]) if len(days) == 1 else None
        if days and not m:
            raise M2KError("原生行事曆的每月重複只支援一個「第 N 個星期幾」（如 3FR、-1MO）。")
        if m:
            f["by_setpos"], f["by_day"] = m.group(1), m.group(2)
        else:
            f["by_monthday"] = str(start.day)
    if parts.get("COUNT"):
        f["count"] = parts["COUNT"]
    elif parts.get("UNTIL"):
        f["until"] = _until(_parse_until(parts["UNTIL"]).date(), start, all_day)
    return f


def _native_rrule_fields(rr, start, all_day):
    """GET 回來的 rrule 物件 → 前端的重複欄位（照 eventEditObject 讀入再 toRequestData）。"""
    freq = str(rr.get("freq") or "").upper()
    f = {"has_rrule": "true", "freq": freq}
    iv = str(int(rr.get("interval") or 0) or 1)
    by_day = str(rr.get("by_day") or "")
    setpos = str(rr.get("by_setpos") or "")
    m = re.match(r"^(-?\d+)(.*)$", by_day)
    if m:
        setpos, by_day = m.group(1), m.group(2)
    if freq == "DAILY":
        if by_day:
            f["by_day"] = by_day
        else:
            f["interval"] = iv
    elif freq == "WEEKLY":
        f["interval"] = iv
        f["by_day"] = by_day or _WEEKDAY[start.weekday()]
    elif freq in ("MONTHLY", "YEARLY"):
        if freq == "MONTHLY":
            f["interval"] = iv
        else:
            f["by_month"] = str(start.month)
        if setpos and by_day:
            f["by_setpos"], f["by_day"] = setpos, by_day
        else:
            f["by_monthday"] = str(start.day)
    count = int(rr.get("count") or 0)
    until = rr.get("until")
    if count:
        f["count"] = str(count)
    elif until not in (None, "", -1, "-1"):
        f["until"] = _until(_from_epoch(until).date(), start, all_day)
    return f


# ---------- 表單 ----------
def new_form(title, start, end, *, location="", description="", url="", attendees=(),
             rrule="", reminder_minutes=0, all_day=False, calendar_id="1"):
    """組「新建事件」的表單。end 是排他的（全天事件＝最後一天的隔天）。"""
    cid = str(calendar_id)
    form = {"feeds": "default", "calendar_id": cid, "allday": "true" if all_day else "false",
            **_times(start, end, all_day),
            "description": _with_link(description, url), "summary": title,
            "location": location or "", "offset": str(OFFSET), "send_meeting_mail": "false",
            "lang": "tw", "orig_feeds": "default", "orig_calendar_id": cid, "is_new": "1"}
    rows, seen = [], set()
    for a in attendees or []:
        em = (a or "").strip()
        if em and em.lower() not in seen:
            seen.add(em.lower())
            rows.append(_new_attendee(em))
    _put_rows(form, "attendee_num", _ATT, rows)
    if rrule:
        form.update(_rrule_fields(rrule, start, all_day))
    form["timezone_dtstart"] = form["timezone_dtend"] = f"{TZ}@{OFFSET}"
    if reminder_minutes:
        _put_rows(form, "alarm_num", _ALARM, [_display_alarm(reminder_minutes)])
    _sync_send(form)
    return form


def _pick(names, idx, default):
    try:
        return names[int(idx)]
    except (TypeError, ValueError, IndexError):
        return default


def _span(ev):
    """事件的 (開始, 排他結束)，台北 naive；全天事件是當天 00:00。"""
    if int(ev.get("info") or 0) & 2:            # EVENT_INFO.FMT_ALLDAY
        s = _date_field(ev.get("dstart") or ev["dtstart"])
        e = _date_field(ev["dend"]) if ev.get("dend") else s + dt.timedelta(days=1)
        return dt.datetime.combine(s, dt.time()), dt.datetime.combine(e, dt.time())
    s = _from_epoch(ev["dtstart"])
    dtend = int(ev.get("dtend") if ev.get("dtend") is not None else -1)
    e = (s + dt.timedelta(seconds=int(ev.get("duration") or 0))) if dtend < 0 else _from_epoch(dtend)
    return s, e


def edit_form(ev, *, me=None):
    """GET 回來的事件 JSON → 修改用的表單。所有欄位原樣帶回——與會者的回覆狀態、
    重複規則、例外日、提醒漏帶哪一項，伺服器就會當成刪掉。
    me 是登入帳號：這場不是 me 召集的就不寄信（見 is_mine）。"""
    cid = str(ev.get("calendar_id") or "1")
    allday = bool(int(ev.get("info") or 0) & 2)
    start, end = _span(ev)
    form = {"feeds": "default", "calendar_id": cid, "allday": "true" if allday else "false",
            **_times(start, end, allday),
            "description": ev.get("description") or "", "summary": ev.get("summary") or "",
            "location": ev.get("location") or "", "offset": str(OFFSET),
            "send_meeting_mail": "false", "lang": "tw", "orig_feeds": "default",
            "orig_calendar_id": cid, "orig_id": str(ev["id"]), "is_new": "0"}
    _put_rows(form, "attendee_num", _ATT, [
        {"attendee": a.get("attendee") or "", "attendee_cn": a.get("attendee_cn") or "",
         "attendee_role": _pick(_ROLES, a.get("attendee_role"), "REQ-PARTICIPANT"),
         "attendee_reply_status": _pick(_STATUS, a.get("attendee_reply_status"), "NEEDS-ACTION")}
        for a in ev.get("attendee") or []])
    if isinstance(ev.get("rrule"), dict):
        form.update(_native_rrule_fields(ev["rrule"], start, allday))
    form["timezone_dtstart"] = form["timezone_dtend"] = f"{TZ}@{OFFSET}"
    alarms = ev.get("alarm") or []
    if isinstance(alarms, dict):
        alarms = list(alarms.values())
    rows = []
    for al in alarms:
        trig = "" if al.get("trigger") is None else str(al.get("trigger"))
        rows.append({
            # 前端：數字開頭的是絕對時間（epoch），其餘是相對秒數（如 -900）
            "alarm_trigger": _wall(_from_epoch(_int(trig))) if re.match(r"^\d", trig) and _int(trig)
                             else _dur(_int(trig)),
            "alarm_action": al.get("action") or "EMAIL",
            "alarm_repeat": str(_int(al.get("repeat"), 1)),
            "alarm_duration": _dur(_int(al.get("duration"))),
            "alarm_summary": al.get("summary") or "",
            "alarm_description": al.get("description") or "",
            "alarm_attendee": al.get("attendee") or ""})
    _put_rows(form, "alarm_num", _ALARM, rows)
    _put_rows(form, "exdate_num", _EXDATE, [
        {"exdate": _day(_date_field(x.get("exdate"))) if allday else _wall(_from_epoch(x.get("exdate")))}
        for x in ev.get("exdate") or []])
    _sync_send(form, is_mine(ev, me))
    return form


def apply(form, *, mine=True, title=None, start=None, end=None, location=None, description=None,
          url=None, add_attendees=None, remove_attendees=None, rrule=None, reminder=None):
    """把變更套到表單上，回新表單（None＝不變）。
    url：""＝移除描述裡的連結行；description 只換本文，連結行保留。
    rrule：""＝取消重複（連同例外日）；'FREQ=…'＝改寫。reminder：0＝移除提醒。
    mine：這場是不是自己召集的（別人召集的不寄信）。"""
    f = dict(form)
    allday = f.get("allday") == "true"
    if title:
        f["summary"] = title
    if location:
        f["location"] = location
    if description is not None or url is not None:
        link, body = _split_link(f.get("description", ""))
        f["description"] = _with_link(body if description is None else description,
                                      link if url is None else url)
    if start:
        f["dtstart"] = _day(start) if allday else _wall(start)
    if end:
        f["dtend"] = _day(end) if allday else _wall(end)
    if add_attendees or remove_attendees:
        rm = {e.strip().lower() for e in remove_attendees or []}
        rows = [r for r in _rows(f, "attendee_num", _ATT)
                if _addr(r.get("attendee")).lower() not in rm]
        have = {_addr(r.get("attendee")).lower() for r in rows}
        for e in add_attendees or []:
            e = e.strip()
            if e and e.lower() not in have:
                have.add(e.lower())
                rows.append(_new_attendee(e, "NEEDS-ACTION" if f.get("is_new") == "0" else None))
        _put_rows(f, "attendee_num", _ATT, rows)
    if rrule is not None:
        for k in _RRULE:
            f.pop(k, None)
        if rrule:
            f.update(_rrule_fields(rrule, _parse_wall(f["dtstart"]), allday))
        else:
            _put_rows(f, "exdate_num", _EXDATE, [])
    elif start and "T" in f.get("until", ""):
        # 前端序列化時用 dtstart 的時分組 until；開始時間改了，until 的時間部分要跟著換
        f["until"] = _until(_parse_wall(f["until"]).date(), start, allday)
    if reminder is not None:
        mins = int(reminder)
        if mins < 0:
            raise M2KError("reminder_minutes 不可為負數（0＝移除提醒）。")
        _put_rows(f, "alarm_num", _ALARM, [_display_alarm(mins)] if mins else [])
    _sync_send(f, mine)
    return f


def info(ev):
    """回報用的摘要（形狀同 m2kcal.parse_ics）：SUMMARY/start/end/location/attendees，外加 uid。"""
    f = edit_form(ev)
    s, e = _span(ev)
    if f["allday"] == "true":
        start = f"{s:%Y-%m-%d} (全天)"
        end = f"{e - dt.timedelta(days=1):%Y-%m-%d} (全天)"
    else:
        start, end = f"{s:%Y-%m-%d %H:%M}", f"{e:%Y-%m-%d %H:%M}"
    return {"uid": ev.get("uid") or "", "SUMMARY": ev.get("summary") or "",
            "start": start, "end": end, "location": ev.get("location") or "",
            "attendees": attendees(f)}


# ---------- HTTP ----------
def _send(method, path, cookie, form=None, params=None):
    """實際打 calsrv。回 (status_code, content_type, text)。"""
    import requests
    headers = {"Cookie": cookie, "X-Requested-With": "XMLHttpRequest",
               "Referer": m2kcal.M2K_BASE + "/", "User-Agent": "Mozilla/5.0 (m2k-calendar)"}
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    try:
        r = requests.request(method, m2kcal.M2K_BASE + path, data=form, params=params,
                             headers=headers, timeout=30, allow_redirects=False)
    except requests.RequestException as err:
        raise Unclear(f"連線 webmail 行事曆失敗或逾時（{err}）：{_UNCLEAR_HINT}。")
    return r.status_code, r.headers.get("content-type", ""), r.text


class Calsrv:
    """一個日曆的原生 API。cookie 是 webmail session（只需 key）；refresh 是 session
    過期時重換 cookie 的 callable（回新 cookie 字串），沒給就不重換。
    me 是登入帳號，用來判斷會議是不是自己召集的。notes 收集寫入過程中值得轉告使用者的事。"""

    def __init__(self, cookie, *, refresh=None, send=None, calendar_id="1", me=""):
        self.cookie = (cookie or "").strip()
        self.calendar_id = str(calendar_id)
        self.me = (me or "").strip()
        self.notes = []
        self._refresh = refresh
        self._send = send

    def _events(self):
        return f"{CALSRV}/feeds/default/default/{self.calendar_id}/events/"

    def _call(self, method, path, form=None, params=None):
        send = self._send or _send          # 執行時才取，測試換掉 _send 也擋得住
        for attempt in (0, 1):
            code, ctype, text = send(method, path, self.cookie, form, params)
            data = None
            if code == 200 and "json" in (ctype or "").lower():
                try:
                    data = json.loads(text)
                except ValueError:
                    data = None
            gone = (code in _SESSION_GONE_HTTP
                    or (isinstance(data, dict) and str(data.get("rspCode")) == "-100"))
            if gone and attempt == 0 and self._refresh:
                fresh = (self._refresh() or "").strip()
                if fresh and fresh != self.cookie:
                    self.cookie = fresh
                    continue
            if gone:
                raise M2KError("webmail session 已過期（重新登入後仍失敗），這次沒有寫入行事曆。")
            if not isinstance(data, dict):
                raise Unclear(f"webmail 行事曆回應異常（HTTP {code}）：{_UNCLEAR_HINT}。")
            rc = str(data.get("rspCode") if data.get("rspCode") is not None else 0)
            if rc != "0":
                msg = str(data.get("rspMsg") or "").strip()
                raise Rejected(f"行事曆伺服器拒絕請求（rspCode {rc}{'：' + msg if msg else ''}）")
            return data

    def use_calendar(self, name):
        """改用名為 name 的日曆（名稱比對原生的 display_name）。"""
        data = self._call("GET", f"{CALSRV}/feeds/default/default/")
        for c in data.get("calendars") or []:
            if (c.get("display_name") or "").strip() == name.strip():
                self.calendar_id = str(c["id"])
                return
        raise M2KError(f"webmail 行事曆找不到名為「{name}」的日曆（名稱見 list_calendars）。")

    def instances(self, start, end):
        data = self._call("GET", self._events() + "instances/",
                          params={"starttime": m2kcal._epoch(start), "endtime": m2kcal._epoch(end)})
        return data.get("instances") or []

    def get(self, eid):
        ev = self._call("GET", self._events() + str(eid)).get("event")
        if not isinstance(ev, dict):
            raise M2KError(f"伺服器沒有回傳事件 {eid} 的內容。")
        return ev

    def create(self, form):
        return self._call("POST", self._events(), form=form).get("event") or {}

    def modify(self, eid, form):
        return self._call("PUT", self._events() + str(eid), form=form).get("event") or {}

    def delete(self, eid, send_mail):
        self._call("DELETE", self._events() + str(eid), form={
            "feeds": "default", "calendar_id": self.calendar_id, "id": str(eid),
            "send_meeting_mail": "true" if send_mail else "false"})

    def count_instances(self, form):
        data = self._call("GET", f"{CALSRV}/api/default/utilities/countRecurrenceInstances",
                          params=form)
        n = data.get("count")
        if isinstance(n, bool) or not re.fullmatch(r"\d+", str(n if n is not None else "")):
            # 回 0 會被當成「沒有場次」而改走全部；讀不懂就停下，不猜
            raise Unclear(f"伺服器回的場次數看不懂（count={n!r}），這次沒有寫入。")
        return int(n)

    def find_id(self, uid, near, summary=""):
        """uid → 原生數字 id。instances 列表沒有 uid，只能在時段內逐筆 GET 比對。
        near 是 CalDAV 讀到的起始時間（可多個）：先在前後一天找（標題相同的先比），
        找不到再往後放寬一年、只比標題相同的。"""
        uid = (uid or "").strip()
        near = [t for t in near if t]
        if not near:
            raise M2KError(f"沒有起始時間可以縮小範圍，找不到 id 為 {uid} 的事件。")
        tried = set()

        def scan(s, e, same_title_only):
            rows = sorted(self.instances(s, e), key=lambda r: r.get("summary") != summary)
            for r in rows:
                eid = r.get("id")
                if eid in tried or (same_title_only and summary and r.get("summary") != summary):
                    continue
                tried.add(eid)
                try:
                    ev = self.get(eid)
                except Rejected:
                    continue            # 單筆被拒（權限、已刪）不影響找其他筆；session 過期照樣往上丟
                if str(ev.get("uid") or "").strip() == uid:
                    return eid
            return None

        day = dt.timedelta(days=1)
        for t in near:
            hit = scan(t - day, t + day, False)
            if hit is not None:
                return hit
        t0 = min(near)
        hit = scan(t0 - day, t0 + dt.timedelta(days=366), True)
        if hit is not None:
            return hit
        raise M2KError(f"webmail 行事曆裡找不到 id 為 {uid} 的事件"
                       f"（查過 {t0:%Y-%m-%d %H:%M} 附近）。請先用 agenda/list_events 確認。")


# ---------- 寫入（含驗證） ----------
def _verified(cs, eid, form):
    """寫入後讀回比對：rspCode 0 不代表寫進去的就是我們送的內容。"""
    if eid in (None, ""):
        raise M2KError("伺服器回應沒有事件 id，無法確認是否寫入，請到 webmail 確認。")
    got = cs.get(eid)
    back = edit_form(got)
    for k in ("summary", "dtstart", "dtend"):
        if (back.get(k) or "").strip() != (form.get(k) or "").strip():
            raise M2KError(f"寫入後讀回不符（{k}：送出 {form.get(k)!r}、讀回 {back.get(k)!r}），"
                           "請到 webmail 確認。")
    return got


def create(cs, form):
    """建立並驗證，回伺服器上的事件（含 id、uid）。"""
    return _verified(cs, cs.create(form).get("id"), form)


def update(cs, orig, **changes):
    """改整個事件（重複會議＝整個系列）。orig 是 cs.get 回來的事件。"""
    form = apply(edit_form(orig, me=cs.me), mine=is_mine(orig, cs.me), **changes)
    cs.modify(orig["id"], form)
    return _verified(cs, orig["id"], form)


def _series(cs, orig, occ):
    """重複會議的表單；順便確認 occ 那一次真的存在（打錯時間就不能動原系列）。"""
    base = edit_form(orig, me=cs.me)
    if base.get("has_rrule") != "true":
        raise M2KError("這不是重複會議，直接修改／刪除整筆即可（不用指定哪一次）。")
    minute = dt.timedelta(minutes=1)
    at = m2kcal._epoch(occ)
    # instances 回的是和時段重疊的場次；要剛好是某一場的開始時間才算
    if not any(str(r.get("id")) == str(orig["id"]) and _int(r.get("dtstart"), -1) == at
               for r in cs.instances(occ - minute, occ + minute)):
        raise M2KError(f"「{orig.get('summary') or ''}」在 {occ:%Y-%m-%d %H:%M} 沒有這一場"
                       "（請用該次原本的開始時間，見 agenda/list_events）。")
    return base


def _probe(base, until=None, keep_exdates=True):
    """給 countRecurrenceInstances 的表單：比照前端 getInstanceCount，拿掉描述與與會者；
    until 給了就把系列截在那天（並拿掉 count）。"""
    p = {k: v for k, v in base.items() if k != "description"}
    _put_rows(p, "attendee_num", _ATT, [])
    if until:
        p.pop("count", None)
        p["until"] = until
    if not keep_exdates:
        _put_rows(p, "exdate_num", _EXDATE, [])
    return p


def _exdate(form, occ):
    f = dict(form)
    rows = _rows(f, "exdate_num", _EXDATE)
    rows.append({"exdate": _day(occ) if f.get("allday") == "true" else _wall(occ)})
    _put_rows(f, "exdate_num", _EXDATE, rows)
    return f


def _around(cs, form):
    """新事件開始前後一天的 instances id（建立前拍一張，結果不明時比對用）。"""
    t = _parse_wall(form["dtstart"])
    day = dt.timedelta(days=1)
    return t - day, t + day, {str(r.get("id")) for r in cs.instances(t - day, t + day)}


def _create_or_restore(cs, orig, base, cut, new, before):
    """原系列已經先改了（cut），接著建新事件：
    - 伺服器明確拒絕 → 把原系列改回 base（send 沿用 cut 那支，與會者那邊才會一起還原）
    - 結果不明 → 先查新事件在不在：在就不還原；不在才還原；查不出來就停下、把狀態講清楚"""
    def restore(err):
        try:
            cs.modify(orig["id"], dict(base, send_meeting_mail=cut["send_meeting_mail"]))
        except M2KError as err2:
            raise M2KError(f"建立新事件失敗：{err}；原系列還原也失敗：{err2}，請到 webmail 檢查。")
        raise M2KError(f"建立新事件失敗（原系列已還原）：{err}")

    s, e, seen = before
    try:
        made = cs.create(new)
    except Unclear as err:
        try:
            rows = cs.instances(s, e)
        except M2KError as err2:
            raise M2KError(f"原系列已經改好，但建立新事件時伺服器回應異常（{err}），之後也查不到結果"
                           f"（{err2}）：新事件是否建立不明，原系列沒有還原。請到 webmail 確認，"
                           "不要直接重試以免建出兩筆。")
        hit = next((r.get("id") for r in rows
                    if str(r.get("id")) not in seen and str(r.get("id")) != str(orig["id"])
                    and r.get("summary") == new.get("summary")), None)
        if hit is None:
            restore(err)
        cs.notes.append(f"建立新事件時伺服器回應異常（{err}），但查到新事件已建立，所以沒有還原原系列。")
        return _verified(cs, hit, new)
    except M2KError as err:
        restore(err)
    return _verified(cs, made.get("id"), new)


def update_occurrence(cs, orig, occ, **changes):
    """只改這一次（occ＝該次原開始時間）：原系列加 exdate（這支不寄信，同前端），
    再新建一筆獨立事件（modify_recur=1）套用變更。回新事件。
    系列只剩這一次時同前端改走「全部」：直接把整筆改成單次。"""
    base = _series(cs, orig, occ)
    mine = is_mine(orig, cs.me)
    total = cs.count_instances(_probe(base))
    s0, e0 = _parse_wall(base["dtstart"]), _parse_wall(base["dtend"])
    one = {k: v for k, v in base.items() if k not in _RRULE}
    _put_rows(one, "exdate_num", _EXDATE, [])
    one.update(_times(occ, occ + (e0 - s0), base["allday"] == "true"))
    one = apply(one, mine=mine, **changes)          # 先組好：變更不合法就一個請求都不送
    if total == 1:            # 同前端 1===r
        cs.modify(orig["id"], one)
        return _verified(cs, orig["id"], one)
    key = f"{occ:%Y%m%d}"
    one.update(modify_recur="1", modified_exdate=key, organizer=orig.get("organizer") or "")
    cut = _exdate(base, occ)
    cut["send_meeting_mail"] = "false"
    cut["modified_exdate"] = key
    before = _around(cs, one)
    cs.modify(orig["id"], cut)
    return _create_or_restore(cs, orig, base, cut, one, before)


def update_following(cs, orig, occ, **changes):
    """改此次及以後：原系列截在前一天（until；原本是 count 就改成之前的次數），
    再從該次起新建系列（modify_recur=2）套用變更。回新系列。
    該次之前沒有任何一場（含被取消的都不算）時同前端改走「全部」。"""
    base = _series(cs, orig, occ)
    mine = is_mine(orig, cs.me)
    allday = base["allday"] == "true"
    s0, e0 = _parse_wall(base["dtstart"]), _parse_wall(base["dtend"])
    prev = _until((occ - dt.timedelta(days=1)).date(), s0, allday)
    # 前端 getInstanceCount(false, false)：之前還有幾場（扣掉已取消的）
    held = cs.count_instances(_probe(base, until=prev))
    cut, new = dict(base), dict(base)
    if base.get("count"):
        # 前端 getInstanceCount(false, true)：count 連已取消的一起算
        before = cs.count_instances(_probe(base, until=prev, keep_exdates=False))
        cut["count"] = str(before)
        new["count"] = str(max(int(base["count"]) - before, 1))
    else:
        cut["until"] = prev
    new.update(_times(occ, occ + (e0 - s0), allday))
    new = apply(new, mine=mine, **changes)          # 先組好：新規則不合法就一個請求都不送
    if held == 0:
        cs.modify(orig["id"], new)
        return _verified(cs, orig["id"], new)
    key = f"{occ:%Y%m%d}"
    new.update(modify_recur="2", modified_exdate=key, organizer=orig.get("organizer") or "")
    cut["modified_exdate"] = key
    before = _around(cs, new)
    cs.modify(orig["id"], cut)
    return _create_or_restore(cs, orig, base, cut, new, before)


def delete(cs, orig):
    """刪整個事件。自己召集且有與會者才寄取消信（伺服器也會從他們的行事曆移除）；
    別人召集的只刪自己那份。"""
    cs.delete(orig["id"], edit_form(orig, me=cs.me)["send_meeting_mail"] == "true")


def delete_occurrence(cs, orig, occ):
    """只取消這一次：原系列用一般修改加上 exdate（send 照 _sync_send：自己召集且有與會者
    才寄），讀回確認。系列只剩這一次時同前端改走「全部」：刪整筆。
    刻意不照前端：前端會另帶 delete_recur=1 與 modified_exdate，但那會讓與會者那份整個
    系列被取消（實機觀察）；不帶時與會者那邊正確只少那一場。"""
    base = _series(cs, orig, occ)
    if cs.count_instances(_probe(base)) == 1:     # 同前端 1===r
        return delete(cs, orig)
    cut = _exdate(base, occ)
    cs.modify(orig["id"], cut)
    want = cut[f"exdate{cut['exdate_num']}"]
    if want not in [r.get("exdate") for r in _rows(edit_form(cs.get(orig["id"])), "exdate_num", _EXDATE)]:
        raise M2KError("取消那一次後讀回，例外日沒有寫進去，請到 webmail 確認。")
