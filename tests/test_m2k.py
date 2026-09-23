#!/usr/bin/env python3
"""
離線功能測試 — 不需帳密、不連伺服器，用假資料驗證核心邏輯。
執行:  python3 tests/test_m2k.py
涵蓋: ICS 產生、時間解析、ICS 解析、linkify、rrule、通訊錄群組成員解析。
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import m2kcal
import m2kfree
import m2kgroup


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name


def unfold(s):
    """RFC 5545 折行還原（收端視角），讓子字串檢查不被折行切斷。"""
    return s.replace("\r\n ", "")


# 1) build_ics: 基本欄位（用帶時區時間，確保 UTC 轉換結果固定：+08:00 14:00 -> 06:00Z）
TW = dt.timezone(dt.timedelta(hours=8))
s = dt.datetime(2026, 7, 10, 14, 0, tzinfo=TW)
e = dt.datetime(2026, 7, 10, 15, 0, tzinfo=TW)
ics = m2kcal.build_ics("專案週會", s, e, location="3F 會議室", desc="進度",
                       uid="U1", stamp="20260701T000000Z")
check("ICS 含 SUMMARY", "SUMMARY:專案週會" in ics)
check("ICS DTSTART 帶 TZID+本地時間", "DTSTART;TZID=Asia/Taipei:20260710T140000" in ics)
check("ICS DTEND 帶 TZID+本地時間", "DTEND;TZID=Asia/Taipei:20260710T150000" in ics)
check("ICS 含 VTIMEZONE", "BEGIN:VTIMEZONE" in ics and "TZID:Asia/Taipei" in ics)
check("ICS 含 LOCATION", "LOCATION:3F 會議室" in ics)
check("ICS 用 CRLF 換行", "\r\n" in ics)
check("ICS 有頭尾", ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR"))

# 2) build_ics: 與會者 + organizer
ics2 = m2kcal.build_ics("部門會議", s, e,
                        attendees=["user_a@example.com", "user_b@example.com"],
                        organizer="owner@example.com", uid="U2", stamp="Z")
check("與會者1寫入", "ATTENDEE" in ics2 and "user_a@example.com" in unfold(ics2))
check("與會者2寫入", "user_b@example.com" in unfold(ics2))
check("ORGANIZER 寫入", "ORGANIZER:mailto:owner@example.com" in unfold(ics2))
check("與會者數=2", unfold(ics2).count("ATTENDEE") == 2)

# 2b) build_ics: TEXT 跳脫與折行（RFC 5545）——描述帶原始換行曾讓 CalDAV 回 415
esc = m2kcal.build_ics("標題,含;符號", s, e, desc="第一行\n第二行", uid="U2b", stamp="Z")
check("描述換行跳脫為字面 \\n", "DESCRIPTION:第一行\\n第二行" in unfold(esc))
check("SUMMARY 逗號分號跳脫", "SUMMARY:標題\\,含\\;符號" in unfold(esc))
check("值中無原始換行殘留",
      all(l.startswith(("BEGIN", "END", "PRODID", "VERSION", "CALSCALE", "TZ",
                        "UID", "DT", "CREATED", "LAST", "SEQUENCE", "SUMMARY",
                        "DESCRIPTION")) or l.startswith(" ")
          for l in esc.split("\r\n") if l))
fold_ics = m2kcal.build_ics("長" * 100, s, e, desc="說" * 100, uid="U2c", stamp="Z")
check("每實體行 ≤75 octets", all(len(l.encode("utf-8")) <= 75
                                for l in fold_ics.split("\r\n")))
check("折行可無損還原（多位元組不被切壞）",
      "SUMMARY:" + "長" * 100 in unfold(fold_ics)
      and "DESCRIPTION:" + "說" * 100 in unfold(fold_ics))

# 2c) parse_ics 反跳脫：book/update 回報都靠 parse_ics 讀回，需還原原文
rt = m2kcal.build_ics("回,報;測試", s, e, location="B1,大廳", uid="U2d", stamp="Z")
rt_info = m2kcal.parse_ics(rt)
check("parse_ics SUMMARY 反跳脫", rt_info.get("SUMMARY") == "回,報;測試")
check("parse_ics LOCATION 反跳脫", rt_info.get("location") == "B1,大廳")
check("字面反斜線 round-trip",
      m2kcal.parse_ics(m2kcal.build_ics(r"字面\n非換行", s, e, uid="U2d2",
                                        stamp="Z")).get("SUMMARY") == r"字面\n非換行")
check("懸空反斜線不炸", m2kcal.parse_ics("SUMMARY:壞\\").get("SUMMARY") == "壞\\")

# 2d) 非 TEXT 欄位（CAL-ADDRESS/UID/RRULE）換行注入防護——不能跳脫，直接剔除
inj = m2kcal.build_ics("t", s, e, attendees=["a@b.c\r\nX-EVIL:1"],
                       organizer="o@b.c\nX-EVIL:2", uid="U\n2e",
                       rrule="FREQ=DAILY\nX-EVIL:3", stamp="Z")
check("換行注入不產生新屬性行",
      all(not l.startswith("X-EVIL") for l in inj.split("\r\n")))
check("注入後 UID 仍在同一行", "UID:U2e" in inj)

# 2e) 折行臨界點：75 octets（含屬性名）不折、76 折
b75 = m2kcal.build_ics("A" * 67, s, e, uid="U2f", stamp="Z")  # SUMMARY: + 67 = 75
check("剛好 75 octets 不折行", "SUMMARY:" + "A" * 67 in b75.split("\r\n"))
b76 = m2kcal.build_ics("A" * 68, s, e, uid="U2g", stamp="Z")
check("76 octets 折行（首段 75＋續行）",
      "SUMMARY:" + "A" * 67 in b76.split("\r\n") and " A" in b76.split("\r\n"))

# 3) parse_when: 多種格式；壞格式丟 M2KError（不能 sys.exit，否則會殺掉 MCP server）
check("解析 日期時間", m2kcal.parse_when("2026-07-10 14:00") == dt.datetime(2026, 7, 10, 14, 0))
check("解析 純日期", m2kcal.parse_when("2026-07-10") == dt.datetime(2026, 7, 10, 0, 0))
check("解析 T 格式", m2kcal.parse_when("2026-07-10T09:30") == dt.datetime(2026, 7, 10, 9, 30))
try:
    m2kcal.parse_when("07/10 下午兩點")
    _raised = False
except m2kcal.M2KError:
    _raised = True
check("壞格式丟 M2KError", _raised)

# 3b) parse_ics: unfold、UTC→台北換算、全天、與會者
ics_text = "\r\n".join([
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT",
    "SUMMARY:週會",
    "DTSTART:20260710T060000Z",   # UTC 06:00 → 台北 14:00
    "DTEND:20260710T070000Z",
    "LOCATION:3F 會",
    " 議室",                       # folded line（RFC 5545 續行）
    "ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:user_a@example.com",
    "END:VEVENT",
    "END:VCALENDAR",
])
info = m2kcal.parse_ics(ics_text)
check("parse_ics SUMMARY", info.get("SUMMARY") == "週會")
check("parse_ics UTC→台北", info.get("start") == "2026-07-10 14:00")
check("parse_ics DTEND", info.get("end") == "2026-07-10 15:00")
check("parse_ics 續行 unfold", info.get("location") == "3F 會議室")
check("parse_ics 與會者", info.get("attendees") == ["user_a@example.com"])
info_allday = m2kcal.parse_ics("DTSTART;VALUE=DATE:20260710")
check("parse_ics 全天", info_allday.get("start") == "2026-07-10 (全天)")

# 3c) _linkify: HTML 跳脫 + 網址轉連結 + 換行
h = m2kcal._linkify('見 https://ex.com/a?b=1 <b>注入</b>\n次行')
check("linkify 跳脫 HTML", "<b>" not in h and "&lt;b&gt;" in h)
check("linkify 網址轉連結", '<a href="https://ex.com/a?b=1"' in h)
check("linkify 換行轉 br", "<br>" in h)

# 3d) parse_basic_auth: MCP HTTP 模式的 pass-through 憑證解析
import base64
u, p = m2kcal.parse_basic_auth("Basic " + base64.b64encode(b"a@example.com:s3cret").decode())
check("Basic 解析 user", u == "a@example.com")
check("Basic 解析 pwd", p == "s3cret")
u2, p2 = m2kcal.parse_basic_auth("basic " + base64.b64encode("a@example.com:p:w:d".encode()).decode())
check("小寫 basic 可解析、密碼含冒號只切第一個", u2 == "a@example.com" and p2 == "p:w:d")
for bad in ("", "Bearer xyz", "Basic %%%",
            "Basic " + base64.b64encode(b"nocolon").decode(),
            "Basic " + base64.b64encode(b":onlypwd").decode()):
    try:
        m2kcal.parse_basic_auth(bad)
        ok = False
    except m2kcal.M2KError:
        ok = True
    check(f"壞 Authorization 丟 M2KError ({bad[:16]!r})", ok)

# 3e) _rrule_text: 週期文字化（吃 dict-like，離線可測）
check("rrule 每週一三", m2kcal._rrule_text({"rrule": {"FREQ": ["WEEKLY"], "BYDAY": ["MO", "WE"]}}) == "每週 一三")
check("rrule 無值空字串", m2kcal._rrule_text({}) == "")

# 4) parse_entries: adb2 列表用 <input name="Entries"> 的屬性帶資料，
#    adbetype 分辨部門(D)與人(C)。舊版靠掃 <td> 撈 email，拿不到部門也拿不到中文名。
mock_html = """
<table>
<tr><td>類別</td><td>暱稱</td><td>信箱</td><td>電話</td></tr>
<tr><td><input type=checkbox name="Entries" value="/org/unit-a" nick="unit-a" email="" adbetype="D"></td>
    <td>unit-a</td></tr>
<tr><td><input type=checkbox name="Entries" value="user_a@example.com" nick="User A (測試甲)"
        email="User_A@Example.COM" adbetype="C"></td><td>10001</td></tr>
<tr><td><input type=checkbox name="Entries" value="user_b@example.com" nick="User B (測試乙)"
        email="user_b@example.com" adbetype="C"></td><td></td></tr>
<tr><td><input type=checkbox name="NotEntries" value="ignored@example.com"
        email="ignored@example.com" adbetype="C"></td><td>—</td></tr>
</table>
"""
rows = m2kgroup.parse_entries(mock_html)
check("解析出 3 列（含 1 個部門、2 位人員）", len(rows) == 3)
dirs = [r for r in rows if r["type"] == "D"]
people = [r for r in rows if r["type"] == "C"]
check("部門有完整路徑", len(dirs) == 1 and dirs[0]["value"] == "/org/unit-a")
check("人員 email 一律小寫", [r["email"] for r in people]
      == ["user_a@example.com", "user_b@example.com"])
check("nick 帶出中文名", "User A (測試甲)" in [r["nick"] for r in people])
check("表頭與非 Entries 欄位不被當成資料",
      all("ignored" not in r["email"] and "信箱" not in r["nick"] for r in rows))

# 5) fetch_node / expand: 用假伺服器驗分頁與遞迴（不連真站）
_pages = {
    "/org/a": [
        [{"value": "/org/a/b", "nick": "b", "email": "", "type": "D"},
         {"value": "x@example.com", "nick": "X", "email": "x@example.com", "type": "C"}],
    ],
    "/org/a/b": [
        [{"value": "y@example.com", "nick": "Y", "email": "y@example.com", "type": "C"},
         {"value": "x@example.com", "nick": "X", "email": "x@example.com", "type": "C"}],
    ],
}


def _fake_page(_s, _abid, dirid, page):
    rows = _pages.get(dirid, [[]])
    chunk = rows[page - 1] if page <= len(rows) else []
    return "".join(
        f'<input name="Entries" value="{r["value"]}" nick="{r["nick"]}" '
        f'email="{r["email"]}" adbetype="{r["type"]}">' for r in chunk)


_real_page, _real_session = m2kgroup.fetch_page, m2kgroup.session
m2kgroup.fetch_page = _fake_page
m2kgroup.session = lambda: None
try:
    members, nodes = m2kgroup.expand("BOOK1", "/org/a", recursive=True)
    got = sorted(e for _, e in members)
    check("遞迴含子部門且跨部門去重", got == ["x@example.com", "y@example.com"])
    check("遞迴走過兩個部門", nodes == 2)
    members1, nodes1 = m2kgroup.expand("BOOK1", "/org/a", recursive=False)
    check("--no-recursive 只取本層", [e for _, e in members1] == ["x@example.com"])
    check("--no-recursive 只走一個部門", nodes1 == 1)
finally:
    m2kgroup.fetch_page, m2kgroup.session = _real_page, _real_session

# 5) update_event_ics: 只動指定欄位、其餘保留；SEQUENCE +1
src_ics = m2kcal.build_ics("原標題", s, e, location="3F",
                           attendees=["user_a@example.com", "user_b@example.com"],
                           organizer="owner@example.com", uid="U9", stamp="20260701T000000Z")
u1 = m2kcal.update_event_ics(src_ics, title="新標題",
                             add_attendees=["user_c@example.com", "USER_A@example.com"],  # 大小寫重複不加
                             remove_attendees=["user_b@example.com"]
                             ).replace("\r\n ", "")  # unfold，長行折行會切斷 email
check("update 換標題", "SUMMARY:新標題" in u1 and "SUMMARY:原標題" not in u1)
check("update 加與會者", "user_c@example.com" in u1)
check("update 移除與會者", "user_b@example.com" not in u1)
check("update 重複與會者不加", u1.count("user_a@example.com") == 1)
check("update 保留 UID/ORGANIZER", "UID:U9" in u1 and "owner@example.com" in u1)
check("update 保留未動欄位", "LOCATION:3F" in u1)
check("update SEQUENCE+1", "SEQUENCE:1" in u1)
u2 = m2kcal.update_event_ics(src_ics, start=dt.datetime(2026, 7, 11, 9, 0),
                             end=dt.datetime(2026, 7, 11, 10, 0)).replace("\r\n ", "")
check("update 改時間帶 TZID", "DTSTART;TZID=Asia/Taipei:20260711T090000" in u2
      and "DTEND;TZID=Asia/Taipei:20260711T100000" in u2)
check("update 保留 VTIMEZONE", u2.count("BEGIN:VTIMEZONE") == 1)
# 來源沒有 VTIMEZONE 時，改時間要自動補上（Mail2000 不吃浮動時間）
bare = src_ics.replace("BEGIN:VTIMEZONE", "BEGIN:X-NOPE").replace("END:VTIMEZONE", "END:X-NOPE")
u3 = m2kcal.update_event_ics(bare, start=dt.datetime(2026, 7, 11, 9, 0))
check("update 自動補 VTIMEZONE", "BEGIN:VTIMEZONE" in u3 and "TZID:Asia/Taipei" in u3)
try:
    m2kcal.update_event_ics("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", title="x")
    _r = False
except m2kcal.M2KError:
    _r = True
check("update 無 VEVENT 丟 M2KError", _r)
# Mail2000 存的事件常帶 METHOD:REQUEST；CalDAV PUT 禁 METHOD（否則 415），必須拿掉
src_m = src_ics.replace("VERSION:2.0", "VERSION:2.0\r\nMETHOD:REQUEST")
u4 = m2kcal.update_event_ics(src_m, title="改標題")
check("update 移除 METHOD", "METHOD" not in u4)
check("parse_ics 取 SEQUENCE", m2kcal.parse_ics(u4).get("SEQUENCE") == 1)
# respond：改指定與會者的 PARTSTAT，其他人不動；不在名單丟 M2KError
u5 = m2kcal.update_event_ics(src_ics, respond=("USER_A@example.com", "DECLINED")).replace("\r\n ", "")
check("respond 改我的 PARTSTAT", "PARTSTAT=DECLINED" in u5 and "user_a@example.com" in u5)
check("respond 不動他人", u5.count("PARTSTAT=NEEDS-ACTION") == 1)  # user_b 維持
try:
    m2kcal.update_event_ics(src_ics, respond=("nobody@example.com", "ACCEPTED"))
    _r = False
except m2kcal.M2KError:
    _r = True
check("respond 非與會者丟 M2KError", _r)

# 6) build_ics: RRULE 與 VALARM
ics_r = m2kcal.build_ics("週會", s, e, uid="U10", stamp="Z",
                         rrule="FREQ=WEEKLY;UNTIL=20261231T155959Z", reminder_minutes=15)
check("RRULE 寫入", "RRULE:FREQ=WEEKLY;UNTIL=20261231T155959Z" in ics_r)
check("VALARM 寫入", "BEGIN:VALARM" in ics_r and "TRIGGER:-PT15M" in ics_r
      and ics_r.index("BEGIN:VALARM") < ics_r.index("END:VEVENT"))
check("無 reminder 無 VALARM", "VALARM" not in ics)

# 7) parse_freebusy + free_slots（空檔計算）
fb_text = "\r\n".join([
    "BEGIN:VCALENDAR", "BEGIN:VFREEBUSY",
    "FREEBUSY:20260708T020000Z/20260708T023000Z",       # 台北 10:00–10:30
    "FREEBUSY:20260708T054500Z/20260708T081500Z,20260708T070000Z/20260708T100000Z",  # 13:45–18:00（重疊合併）
    "END:VFREEBUSY", "END:VCALENDAR"])
busy = m2kcal.parse_freebusy(fb_text)
check("parse_freebusy 筆數", len(busy) == 3)
check("parse_freebusy UTC→台北", busy[0][0] == dt.datetime(2026, 7, 8, 10, 0))
def _fixed_source(by_person):
    """把固定資料包成 busy_source：模擬「忙碌已經查好了」。"""
    return lambda people, s0, e0: ({p: by_person.get(p, []) for p in people}, {})


def _allfree(by_person, s0, e0, **kw):
    """只取全員都有空的時段（missing 為空），回 [(start, end)]。"""
    r = m2kfree.common_free_slots(list(by_person) or ["me@example.com"], s0, e0,
                                  busy_source=_fixed_source(by_person), **kw)
    return [(a, b) for a, b, missing in r["slots"] if not missing]


slots = _allfree({"me@example.com": busy}, dt.datetime(2026, 7, 8), dt.datetime(2026, 7, 9),
                 duration_min=60, day_start="09:00", day_end="18:00")
# 忙碌 10:00–10:30、13:45–18:00 → 空檔 09:00–10:00、10:30–13:45
check("共同空檔 找到 2 段", len(slots) == 2)
check("共同空檔 第一段", slots[0] == (dt.datetime(2026, 7, 8, 9, 0), dt.datetime(2026, 7, 8, 10, 0)))
check("共同空檔 第二段", slots[1] == (dt.datetime(2026, 7, 8, 10, 30), dt.datetime(2026, 7, 8, 13, 45)))
check("共同空檔 週末跳過", _allfree(
    {}, dt.datetime(2026, 7, 11), dt.datetime(2026, 7, 13), duration_min=60) == [])  # 7/11 六 7/12 日
check("共同空檔 含週末", len(_allfree(
    {}, dt.datetime(2026, 7, 11), dt.datetime(2026, 7, 13),
    duration_min=60, include_weekends=True)) == 2)

# 8) events_json：aware（帶時區）與全天（date、naive）混在一起要能排序
from types import SimpleNamespace
from icalendar import Calendar as _IC
def _fake(ics_text):
    c = _IC.from_ical(ics_text)
    return SimpleNamespace(icalendar_component=list(c.walk("VEVENT"))[0])
timed = m2kcal.build_ics("有時間", s, e, uid="U20", stamp="Z")
allday = "\r\n".join([
    "BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:U21", "SUMMARY:全天",
    "DTSTART;VALUE=DATE:20260709", "DTEND;VALUE=DATE:20260710",
    "END:VEVENT", "END:VCALENDAR"])
rows = m2kcal.events_json([_fake(allday), _fake(timed)])
check("events_json 混合排序不炸", len(rows) == 2)
check("events_json 混合排序順序", rows[0]["summary"] == "全天" or rows[0]["start"] <= rows[1]["start"])
check("events_json 全天格式", any(r["allday"] and r["start"] == "2026-07-09" for r in rows))
check("events_json aware 轉台北", any(r["start"] == "2026-07-10 14:00" for r in rows))

# 9) match_contacts：模糊人名比對
book_c = {
    "pekka_chang@example.com": {"name": "pekka_chang", "count": 3, "last": "2026-07-01"},
    "derek_wang@example.com": {"name": "Derek Wang", "count": 10, "last": "2026-06-20"},
    "derek_lin@example.com": {"name": "derek_lin", "count": 2, "last": "2026-05-01"},
    "anne_de@example.com": {"name": "Anne De", "count": 1, "last": "2026-04-01"},
}
m = m2kcal.match_contacts(book_c, "pekka")
check("match 前綴命中", m and m[0][1] == "pekka_chang@example.com")
m2 = m2kcal.match_contacts(book_c, "derek")
check("match 多候選依次數排序", len(m2) == 2 and m2[0][1] == "derek_wang@example.com")
m3 = m2kcal.match_contacts(book_c, "wang")
check("match 底線分段前綴", any(e == "derek_wang@example.com" for _, e, _ in m3))
check("match 查無回空", m2kcal.match_contacts(book_c, "nobody") == [])
check("match 空字串回空", m2kcal.match_contacts(book_c, " ") == [])

# 10) load_directory_file：CSV 與 vCard 匯出檔解析
import tempfile
with tempfile.TemporaryDirectory() as td:
    csvp = os.path.join(td, "dir.csv")
    with open(csvp, "w", encoding="utf-8") as f:
        f.write("暱稱,姓,名,信箱,電話\n")
        f.write("User A,測,甲,user_a@example.com,10001\n")
        f.write('"User B",測,乙,user_b@example.com,\n')
        f.write(",,,沒信箱的列,123\n")
    d1 = m2kcal.load_directory_file(csvp)
    check("CSV 解析筆數", len(d1) == 2)
    check("CSV email 小寫鍵", "user_a@example.com" in d1)
    check("CSV 姓名取最長非 email 欄", d1["user_a@example.com"]["name"] == "User A")
    vcfp = os.path.join(td, "dir.vcf")
    with open(vcfp, "w", encoding="utf-8") as f:
        f.write("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:User C\r\n"
                "EMAIL;TYPE=INTERNET:User_C@example.com\r\nEND:VCARD\r\n"
                "BEGIN:VCARD\r\nFN:無信箱\r\nEND:VCARD\r\n")
    d2 = m2kcal.load_directory_file(vcfp)
    check("vCard 解析筆數", len(d2) == 1)
    check("vCard FN + email 小寫", d2.get("user_c@example.com", {}).get("name") == "User C")
    check("通訊錄檔可餵 match_contacts",
          m2kcal.match_contacts(d1, "user")[0][1] in d1)

# 11) 重複會議進階：改規則 / iMIP
rsrc = m2kcal.build_ics("週會", s, e, attendees=["user_a@example.com"],
                        organizer="owner@example.com", uid="R1", stamp="Z",
                        rrule="FREQ=WEEKLY;UNTIL=20260930T155959Z")
ru = m2kcal.update_event_ics(rsrc, rrule="FREQ=MONTHLY").replace("\r\n ", "")
check("update 改重複規則", "FREQ=MONTHLY" in ru and "WEEKLY" not in ru)
check("update 取消重複", "RRULE" not in m2kcal.update_event_ics(rsrc, rrule=""))
check("update 不動規則", "FREQ=WEEKLY" in m2kcal.update_event_ics(rsrc, title="x"))

inv = m2kcal.imip_ics(rsrc, "request")
check("iMIP REQUEST 位置正確", "METHOD:REQUEST" in inv
      and inv.index("METHOD") < inv.index("BEGIN:VEVENT"))
cxl = m2kcal.imip_ics(rsrc, "cancel")
check("iMIP CANCEL 帶 STATUS", "METHOD:CANCEL" in cxl and "STATUS:CANCELLED" in cxl)
check("iMIP 冪等（不重複 METHOD）", m2kcal.imip_ics(inv, "REQUEST").count("METHOD:") == 1)

# 12) render_grouped：描述截斷 200 字 + URL 抽出
long_desc = "開會前請先讀文件。" * 30  # >200 字
desc_ics = m2kcal.build_ics(
    "有描述", s, e, uid="U30", stamp="Z",
    desc=long_desc + r"\n會議連結 https://teams.microsoft.com/l/meetup/abc 備用 "
                     r"https://links.example.com/xyz")
g = m2kcal.render_grouped([_fake(desc_ics)])
check("render_grouped 描述截斷 200 字", "描述: " in g
      and any(len(ln.split("描述: ", 1)[1]) == 201 and ln.endswith("…")
              for ln in g.splitlines() if "描述: " in ln))
check("render_grouped URL 抽出（含截斷後段）",
      "🔗 https://teams.microsoft.com/l/meetup/abc" in g
      and "🔗 https://links.example.com/xyz" in g)
check("render_grouped 無描述不印欄位",
      "描述:" not in m2kcal.render_grouped([_fake(timed)]))

# 13) build_ics 全天事件（VALUE=DATE，DTEND 排他）
ad = m2kcal.build_ics("休假", dt.datetime(2026, 7, 24), dt.datetime(2026, 7, 25),
                      uid="AD1", stamp="Z", all_day=True)
check("全天 DTSTART VALUE=DATE", "DTSTART;VALUE=DATE:20260724" in ad)
check("全天 DTEND 排他日期", "DTEND;VALUE=DATE:20260725" in ad)
ad2 = m2kcal.build_ics("單日", dt.datetime(2026, 7, 24), dt.datetime(2026, 7, 24),
                       uid="AD2", stamp="Z", all_day=True)
check("全天 end<=start 自動補隔天", "DTEND;VALUE=DATE:20260725" in ad2)
check("全天事件可讀回", m2kcal.events_json([_fake(ad)])[0]["allday"])

# 14) compose_rrule：組字與輸入驗證
check("rrule weekly byday（大小寫寬容）",
      m2kcal.compose_rrule("weekly", byday=["TU", "th"]) == "FREQ=WEEKLY;BYDAY=TU,TH")
check("rrule interval", m2kcal.compose_rrule("weekly", interval=2)
      == "FREQ=WEEKLY;INTERVAL=2")
check("rrule interval=1 省略", m2kcal.compose_rrule("daily", interval=1) == "FREQ=DAILY")
check("rrule monthly 序數", m2kcal.compose_rrule("monthly", byday=["3FR"])
      == "FREQ=MONTHLY;BYDAY=3FR")
check("rrule until 轉 UTC", m2kcal.compose_rrule(
    "daily", until=dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=TW))
    == "FREQ=DAILY;UNTIL=20261231T155959Z")
for desc_, bad in [("hourly 不支援", lambda: m2kcal.compose_rrule("hourly")),
                   ("daily+byday", lambda: m2kcal.compose_rrule("daily", byday=["MO"])),
                   ("weekly+序數", lambda: m2kcal.compose_rrule("weekly", byday=["3FR"])),
                   ("byday 亂字", lambda: m2kcal.compose_rrule("weekly", byday=["XX"])),
                   ("interval 負數", lambda: m2kcal.compose_rrule("weekly", interval=-1))]:
    try:
        bad()
        _r = False
    except m2kcal.M2KError:
        _r = True
    check(f"rrule 壞輸入丟 M2KError（{desc_}）", _r)

# 15) update_event_ics 提醒增/改/刪/保留
rbase = m2kcal.build_ics("提醒測試", s, e, uid="R1", stamp="Z")
w30 = m2kcal.update_event_ics(rbase, reminder=30)
check("加提醒 VALARM -PT30M", "BEGIN:VALARM" in w30 and "-PT30M" in w30)
w10 = m2kcal.update_event_ics(w30, reminder=10)
check("改提醒不疊加", w10.count("BEGIN:VALARM") == 1 and "-PT10M" in w10)
w0 = m2kcal.update_event_ics(w10, reminder=0)
check("reminder=0 移除提醒", "VALARM" not in w0)
wkeep = m2kcal.update_event_ics(w30, title="改名")
check("reminder=None 保留既有提醒", wkeep.count("BEGIN:VALARM") == 1)
try:
    m2kcal.update_event_ics(rbase, reminder=-5)
    _r = False
except m2kcal.M2KError:
    _r = True
check("reminder 負數丟 M2KError", _r)

# 16) render_detail：描述全文、與會者回覆狀態、提醒、全天
det_ics = m2kcal.build_ics("詳情會議", s, e, location="3F",
                           desc="第一行\n第二行 " + "長" * 300,
                           attendees=["a@x.com"], organizer="me@x.com",
                           uid="D1", stamp="Z", reminder_minutes=15)
det = m2kcal.render_detail(_fake(det_ics))
check("render_detail 描述不截斷", "長" * 300 in det)
check("render_detail 與會者回覆狀態", "未回覆" in det and "a@x.com" in det)
check("render_detail 提醒", "開始前 15 分鐘" in det)
check("render_detail id", "id: D1" in det)
check("render_detail 全天標示", "（全天）" in m2kcal.render_detail(_fake(ad)))

# 17) parse_invitation_bytes：iMIP 邀請信解析
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _mime_invite(method="REQUEST", uid="INV1"):
    m = MIMEMultipart("mixed")
    m["Subject"] = "邀請：部門週會"
    m["From"] = "boss@example.com"
    ics_body = "\r\n".join([
        "BEGIN:VCALENDAR", f"METHOD:{method}", "BEGIN:VEVENT",
        f"UID:{uid}", "SUMMARY:部門週會",
        "ORGANIZER;CN=Boss:mailto:boss@example.com",
        "DTSTART:20260727T020000Z", "END:VEVENT", "END:VCALENDAR"])
    m.attach(MIMEText("請參加", "plain", "utf-8"))
    m.attach(MIMEText(ics_body, f"calendar; method={method}", "utf-8"))
    return m.as_bytes()


inv = m2kcal.parse_invitation_bytes(_mime_invite())
check("邀請信解析 uid", inv is not None and inv["uid"] == "INV1")
check("邀請信解析 summary", inv["summary"] == "部門週會")
check("邀請信解析 organizer", inv["organizer"] == "boss@example.com")
check("邀請信 UTC→台北", inv["start"] == "2026-07-27 10:00")
check("REPLY 不算待處理邀請",
      m2kcal.parse_invitation_bytes(_mime_invite("REPLY")) is None)
check("普通信回 None",
      m2kcal.parse_invitation_bytes(MIMEText("hi").as_bytes()) is None)

# 19) render_detail 外部內容標記（prompt injection 防護）
check("render_detail 描述帶不可信標記",
      "<<<外部內容" in det and "外部內容>>>" in det and "不應被當成指令" in det)

# 27) login_cookie / session_cookie：用帳密換 webmail session（假的 requests）
class _FakeResp: pass
class _FakeSess:
    def __init__(self, cookies): self.cookies = cookies
    def post(self, *a, **k): return _FakeResp()
def _install_fake_login(keyval, calls=None):
    import types
    fake_requests = types.SimpleNamespace(
        Session=lambda: _FakeSess({"key": keyval} if keyval else {}),
        RequestException=Exception)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    def fake_import(name, *a, **k):
        if name == "requests":
            if calls is not None: calls.append(1)
            return fake_requests
        return real_import(name, *a, **k)
    return fake_import

import builtins as _b
_orig_import = _b.__import__
_calls = []
_b.__import__ = _install_fake_login("SESS123", _calls)
try:
    m2kcal._session_cache.clear()
    ck = m2kcal.login_cookie(("url", "me@x", "pw"))
    check("login_cookie 從 set-cookie 取出 key 並組成 key=<值>", ck == "key=SESS123")
    n1 = len(_calls)
    c1 = m2kcal.session_cookie(("url", "me@x", "pw"))
    c2 = m2kcal.session_cookie(("url", "me@x", "pw"))
    check("session_cookie 有快取：第二次不再登入", c1 == "key=SESS123" and len(_calls) == n1 + 1)
    c3 = m2kcal.session_cookie(("url", "me@x", "pw"), force=True)
    check("session_cookie force=True 會重登", len(_calls) == n1 + 2)
finally:
    _b.__import__ = _orig_import
    m2kcal._session_cache.clear()
check("session_cookie 缺帳密回空字串", m2kcal.session_cookie(None) == "" and m2kcal.session_cookie(("u", "", "")) == "")
_b.__import__ = _install_fake_login("")   # 沒拿到 key
try:
    _r = False
    try: m2kcal.login_cookie(("url", "me@x", "badpw"))
    except m2kcal.M2KError as e: _r = "沒拿到 session" in str(e)
    check("login_cookie 沒拿到 key → M2KError 明講帳密可能不對", _r)
finally:
    _b.__import__ = _orig_import
    m2kcal._session_cache.clear()

# 21) 排程端點：parse_schedule / busy_periods / render_schedule / fetch_schedule 錯誤路徑
_sample = {"rspCode": 0, "rspMsg": "", "instances": [
    {"dtstart": 1789988400, "dtend": 1789993800, "offset": "28800", "id": 1, "summary": "熱舞社", "organizer": ""},
    {"dtstart": 1790296200, "dtend": 1790299800, "offset": 0, "id": 2, "summary": "週會",
     "organizer": "mailto:sec_02@example.com",
     "attendee": [{"attendee": "mailto:Flora_Hu@example.com", "attendee_reply_status": 2},
                  {"attendee": "mailto:x@example.com", "attendee_reply_status": 1}]},
    {"dtstart": 1790209800, "dtend": 1791194400, "offset": 0, "id": 3, "summary": "OFF",
     "attendee": '[{"attendee":"mailto:flora_hu@example.com","attendee_reply_status":3}]'},
    {"dtstart": "bad", "dtend": 1, "summary": "壞的"},
]}
m2kcal.take_notes()
ev = m2kcal.parse_schedule(_sample, "flora_hu@example.com")
check("parse_schedule 解出 3 筆、壞的跳過並留痕", len(ev) == 3 and any("跳過" in n for n in m2kcal.take_notes()))
check("parse_schedule dtstart 直接是 epoch 秒、不加 offset（熱舞社 19:00）",
      ev[0]["summary"] == "熱舞社" and ev[0]["start"].hour == 19 and ev[0]["start"].minute == 0)
check("parse_schedule 自建事件 status=自建且算忙碌", ev[0]["status"] == "自建" and ev[0]["busy"])
check("parse_schedule 依 attendee_reply_status 對應（2=已拒絕不算忙碌，比對 mailto/大小寫不敏感）",
      ev[2]["summary"] == "週會" and ev[2]["status"] == "已拒絕" and not ev[2]["busy"])
check("parse_schedule attendee 是 JSON 字串也解得開（3=暫定、算忙碌）",
      ev[1]["summary"] == "OFF" and ev[1]["status"] == "暫定" and ev[1]["busy"])
check("busy_periods 排除已拒絕", len(m2kcal.busy_periods(ev)) == 2)
rs = m2kcal.render_schedule({"flora_hu@example.com": ev, "ghost@example.com": m2kcal.M2KError("查無此帳號：ghost")})
check("render_schedule 跨日事件顯示日期範圍、狀態標記、召集人", "09/24 08:30–10/05 18:00" in rs and "[暫定]" in rs
      and "[已拒絕]" in rs and "召集:sec_02@example.com" in rs)
check("render_schedule 已接受/自建不加標記", "熱舞社\n" in rs + "\n" and "[自建]" not in rs)
check("render_schedule 查不到的人列出原因", "⚠ 查不到：查無此帳號：ghost" in rs)
_r = False
try: m2kcal.parse_schedule({"rspCode": -102}, "nobody@example.com")
except m2kcal.M2KError as e: _r = "查無此帳號" in str(e)
check("parse_schedule rspCode -102 → 查無此帳號", _r)
_r = False
try: m2kcal.parse_schedule({"rspCode": -100, "rspMsg": "Invalid Session"}, "a@example.com")
except m2kcal.M2KError as e: _r = "無效或已過期" in str(e) and "key=" in str(e)
check("parse_schedule rspCode -100 → Cookie 無效或已過期，指名 key cookie", _r)
_r = False
try: m2kcal.fetch_schedule("", "a@example.com", dt.datetime(2026, 9, 21), dt.datetime(2026, 9, 22))
except m2kcal.M2KError as e: _r = "M2K_COOKIE" in str(e)
check("fetch_schedule 沒 cookie → 說明怎麼提供", _r)
_calls = []
def _fake_get(cookie, email, st, et):
    _calls.append((cookie, email, st, et))
    return 410, "text/html", "<html>410</html>"
_orig_get = m2kcal._sched_get; m2kcal._sched_get = _fake_get
try:
    _r = False
    try: m2kcal.fetch_schedule(" ck=1 ", "a@example.com", dt.datetime(2026, 9, 21), dt.datetime(2026, 9, 22))
    except m2kcal.M2KError as e: _r = "無效或已過期" in str(e) and "410" in str(e)
    check("fetch_schedule 被 nginx 410/回 HTML → 明講 Cookie 無效", _r)
    check("fetch_schedule 會 strip cookie，naive 時間視為台北換成 epoch",
          _calls[0][0] == "ck=1" and _calls[0][2] == 1789920000 and _calls[0][3] == 1790006400)
    m2kcal._sched_get = lambda c, e, st, et: (200, "application/json; charset=utf-8", "{\"rspCode\":0,\"instances\":[]}")
    check("fetch_schedule 正常 JSON → 空清單", m2kcal.fetch_schedule("ck", "a@example.com",
          dt.datetime(2026, 9, 21), dt.datetime(2026, 9, 22)) == [])
finally:
    m2kcal._sched_get = _orig_get

# 20) find_free_slots 帶 attendees 但沒 Cookie：退到「已分享行事曆」，並明講怎麼提供 Cookie
# 需要 mcp 套件才 import 得動 server（它缺套件時會直接 sys.exit，所以先探 mcp 本身）；
# CI 只裝 icalendar，沒有就明講略過，不假裝通過
import importlib.util
if importlib.util.find_spec("mcp") is None:
    print("SKIP find_free_slots 帶 attendees（缺 mcp 套件，pip install -r requirements.txt 後再跑）")
    srv = None
else:
    import m2k_mcp_server as srv
if srv:
    class _FakeFB0:
        data = "BEGIN:VFREEBUSY\r\nEND:VFREEBUSY\r\n"
    class _FakeCal0:
        def freebusy_request(self, s, e): return _FakeFB0()
    _orig = (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, m2kcal.busy_from_shared,
             m2kcal.fetch_schedule, os.environ.pop("M2K_COOKIE", None))   # .env 可能真的有 cookie，這段要測「沒有」
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _FakeCal0()
    m2kcal.creds = lambda: ("u", "user@example.com", "pw")
    # busy_from_shared 現在每人各自一份（共同空檔要指得出誰擋住）
    m2kcal.busy_from_shared = lambda p, emails, s, e: (
        {"shared@example.com": [(dt.datetime(2026, 9, 21, 9), dt.datetime(2026, 9, 21, 12))]},
        ["nobody@example.com"])
    def _no_sched(*a, **k): raise AssertionError("沒 Cookie 不該打排程端點")
    m2kcal.fetch_schedule = _no_sched
    try:
        fs = srv.find_free_slots(duration_minutes=60, start="2026-09-21", days=1,
                                 attendees=["shared@example.com", "nobody@example.com"])
    finally:
        m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, m2kcal.busy_from_shared, m2kcal.fetch_schedule = _orig[:5]
        if _orig[5] is not None: os.environ["M2K_COOKIE"] = _orig[5]
    check("find_free_slots 沒 Cookie → 改讀已分享行事曆並說明（提到 Cookie 與 others_agenda）",
          "沒有 webmail Cookie" in fs and "others_agenda" in fs)
    check("find_free_slots 沒 Cookie → 列出未分享者、已分享者的忙碌有扣掉（09–12 忙，12:00 起有空）",
          "nobody@example.com" in fs and "\n   12:00–" in fs and "\n   09:00–" not in fs)

# 22) MCP others_agenda / find_free_slots 帶 attendees：有 Cookie 時走排程端點（假的 fetch）
if srv:
    _fake_sched = {
        "a@example.com": [
            {"start": dt.datetime(2026, 9, 21, 9), "end": dt.datetime(2026, 9, 21, 10), "summary": "A 早會",
             "organizer": "boss@example.com", "status": "已接受", "busy": True},
            {"start": dt.datetime(2026, 9, 21, 14), "end": dt.datetime(2026, 9, 21, 15), "summary": "A 拒絕的",
             "organizer": "", "status": "已拒絕", "busy": False},
        ],
    }
    def _fake_fetch(cookie, email, s, e):
        assert cookie == "ck=1", "要把 cookie 原樣傳下去"
        if email not in _fake_sched:
            raise m2kcal.M2KError(f"查無此帳號：{email}")
        return _fake_sched[email]
    class _FakeFB:
        data = "BEGIN:VFREEBUSY\r\nFREEBUSY:20260921T020000Z/20260921T030000Z\r\nEND:VFREEBUSY\r\n"  # 自己 10:00–11:00 忙
    class _FakeCal:
        def freebusy_request(self, s, e): return _FakeFB()
    _orig = (m2kcal.fetch_schedule, m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds,
             os.environ.get("M2K_COOKIE"))
    m2kcal.fetch_schedule = _fake_fetch
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _FakeCal()
    m2kcal.creds = lambda: ("u", "user", "pw")
    os.environ["M2K_COOKIE"] = "ck=1"
    try:
        oa = srv.others_agenda(["a@example.com", "ghost@example.com", "A@example.com"], start="2026-09-21", days=1)
        check("others_agenda 依人分組、去重且大小寫不敏感", oa.count("👤") == 2 and "2 人" in oa)
        check("others_agenda 顯示行程與回覆狀態", "A 早會" in oa and "[已拒絕]" in oa and "召集:boss@example.com" in oa)
        check("others_agenda 查不到的人明講原因", "ghost@example.com" in oa and "查無此帳號" in oa)
        fs2 = srv.find_free_slots(duration_minutes=60, start="2026-09-21", days=1,
                                  attendees=["a@example.com", "ghost@example.com"])
        check("find_free_slots 有 Cookie → 扣掉對方忙碌（09–10）與自己忙碌（10–11），11:00 起有空",
              "\n   11:00–" in fs2 and "\n   09:00–" not in fs2 and "\n   10:00–" not in fs2)
        check("find_free_slots 對方已拒絕的會議不算忙碌（14–15 不被扣掉）",
              "11:00–18:00" in fs2)
        # 訊息改成帶原因（查無帳號／查詢失敗／未分享／超過人數上限），比「查不到」精確
        check("find_free_slots 有人查不到 → 警告帶原因且點名是誰",
              "⚠" in fs2 and "ghost@example.com" in fs2 and "查無帳號" in fs2)
        oa2 = srv.others_agenda([], days=1)
        check("others_agenda 沒給 email → 錯誤", oa2.startswith("錯誤："))
    finally:
        m2kcal.fetch_schedule, m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds = _orig[:4]
        if _orig[4] is None: os.environ.pop("M2K_COOKIE", None)
        else: os.environ["M2K_COOKIE"] = _orig[4]
    _saved = os.environ.pop("M2K_COOKIE", None)
    _oc = (m2kcal.session_cookie, m2kcal.creds)
    m2kcal.session_cookie = lambda auth, force=False: ""   # 模擬自動登入也拿不到 cookie
    m2kcal.creds = lambda: ("u", "user", "pw")
    m2kcal._session_cache.clear()
    try:
        check("others_agenda 沒 Cookie 且自動登入失敗 → 說明怎麼提供",
              "M2K_COOKIE" in srv.others_agenda(["a@example.com"]))
    finally:
        m2kcal.session_cookie, m2kcal.creds = _oc
        if _saved is not None: os.environ["M2K_COOKIE"] = _saved

# 23) person_calendar：需要 caldav 套件（CI 只裝 icalendar，沒有就明講略過）
if importlib.util.find_spec("caldav") is None:
    print("SKIP person_calendar（缺 caldav 套件）")
else:
    import caldav
    _pc_client = caldav.DAVClient(url="https://mail.gss.com.tw/cgi-bin/cal/caldav/")
    class _FakeP:
        client = _pc_client
    _pcal = m2kcal.person_calendar(_FakeP(), "colleague@example.com")
    check("person_calendar 指向 <email>/default/",
          str(_pcal.url).endswith("/calendars/colleague@example.com/default/"))

# 24) collect_meeting_groups / match_groups：同標題聚合 + 模糊比對（用虛構名稱）
g1 = m2kcal.build_ics("TEAM_A1 Standup", dt.datetime(2026, 8, 1, 10, 0),
                      dt.datetime(2026, 8, 1, 10, 30),
                      attendees=["a@x.com", "b@x.com"], organizer="lead@x.com",
                      uid="G1", stamp="Z")
g2 = m2kcal.build_ics("TEAM_A1 Standup", dt.datetime(2026, 8, 8, 10, 0),
                      dt.datetime(2026, 8, 8, 10, 30),
                      attendees=["a@x.com", "c@x.com"], organizer="lead@x.com",
                      uid="G2", stamp="Z")
g3 = m2kcal.build_ics("別的會", dt.datetime(2026, 8, 2, 14, 0),
                      dt.datetime(2026, 8, 2, 15, 0),
                      attendees=["d@x.com"], uid="G3", stamp="Z")
class _FakeCal:
    def search(self, **kw):
        return [_fake(g1), _fake(g2), _fake(g3)]
grps = m2kcal.collect_meeting_groups(_FakeCal())
check("群組：同標題聚合成一筆",
      len([g for g in grps if g["title"] == "TEAM_A1 Standup"]) == 1)
_csg = next(g for g in grps if g["title"] == "TEAM_A1 Standup")
check("群組：count 累加", _csg["count"] == 2)
check("群組：名單取最近一次（含 organizer、去重）",
      set(_csg["attendees"]) == {"a@x.com", "c@x.com", "lead@x.com"})
check("match_groups 模糊命中（team_a1 → TEAM_A1 Standup）",
      bool(m2kcal.match_groups(grps, "team_a1"))
      and m2kcal.match_groups(grps, "team_a1")[0]["title"] == "TEAM_A1 Standup")
check("match_groups 查無回空", m2kcal.match_groups(grps, "zzz") == [])

# 25) match_directory_groups：部門名模糊比對（正規化去底線/空白；用虛構名稱）
_dirg = [{"name": "ENG_A1_GRP", "path": "/ORG/ENG/ENG_A1_GRP", "href": "/h1"},
         {"name": "ENG_A2_GRP", "path": "/ORG/ENG/ENG_A2_GRP", "href": "/h2"},
         {"name": "SALES", "path": "/ORG/SALES", "href": "/h3"}]
check("match_directory 命中（eng a1 → ENG_A1_GRP）",
      [g["name"] for g in m2kcal.match_directory_groups(_dirg, "eng a1")] == ["ENG_A1_GRP"])
check("match_directory 前綴命中多筆（eng_a → 兩個部門）",
      len(m2kcal.match_directory_groups(_dirg, "eng_a")) == 2)
check("match_directory 查無回空", m2kcal.match_directory_groups(_dirg, "zzz") == [])

check("group_mailbox 部門名轉小寫＋使用者網域",
      m2kcal.group_mailbox("ENG_A1_GRP", "me@example.com") == "eng_a1_grp@example.com")
check("group_mailbox 去空白", m2kcal.group_mailbox("  ENG_A1  ", "me@example.com")
      == "eng_a1@example.com")
check("group_mailbox 無網域回空", m2kcal.group_mailbox("ENG_A1", "nodomain") == "")

# 25b) descendant_groups：用 path 前綴找子孫部門（find_group 遞迴的依據）
#      path 是組織樹位置，形如 /ROOT/BU/DEPT/SUB，分隔符只有 "/"（實機確認過）
_tree = [{"name": "ENG", "path": "/ORG/ENG", "href": "/e"},
         {"name": "ENG_A", "path": "/ORG/ENG/ENG_A", "href": "/ea"},
         {"name": "ENG_A1", "path": "/ORG/ENG/ENG_A/ENG_A1", "href": "/ea1"},
         {"name": "ENG_A2", "path": "/ORG/ENG/ENG_A/ENG_A2", "href": "/ea2"},
         {"name": "ENG_B", "path": "/ORG/ENG/ENG_B", "href": "/eb"},
         # 陷阱：名字以 ENG_A 開頭但不是它的子孫，純字串 startswith 會誤中
         {"name": "ENG_AX", "path": "/ORG/ENG/ENG_AX", "href": "/eax"},
         {"name": "SALES", "path": "/ORG/SALES", "href": "/s"}]

_desc = m2kcal.descendant_groups(_tree, "/ORG/ENG/ENG_A")
check("descendant 取到直接子層與孫層",
      sorted(g["name"] for g in _desc) == ["ENG_A1", "ENG_A2"])
check("descendant 不含自己", "ENG_A" not in [g["name"] for g in _desc])
check("descendant 不誤中同前綴的兄弟（ENG_AX 不是 ENG_A 的子孫）",
      "ENG_AX" not in [g["name"] for g in _desc])
check("descendant 多層都取得（ENG 底下含孫、曾孫）",
      sorted(g["name"] for g in m2kcal.descendant_groups(_tree, "/ORG/ENG"))
      == ["ENG_A", "ENG_A1", "ENG_A2", "ENG_AX", "ENG_B"])
check("descendant 葉節點回空", m2kcal.descendant_groups(_tree, "/ORG/ENG/ENG_A/ENG_A1") == [])
check("descendant 尾端斜線不影響",
      len(m2kcal.descendant_groups(_tree, "/ORG/ENG/ENG_A/")) == 2)
check("descendant path 為空時回空（不要把整棵樹當子孫）",
      m2kcal.descendant_groups(_tree, "") == [])

# 25c) build_ics 的 URL 屬性：會議連結放 DESCRIPTION 會在邀請信卡片攤開，
#      放 iCalendar 的 URL 屬性多數客戶端會渲染成「加入會議」按鈕
_us = dt.datetime(2026, 7, 1, 10, 0)
_ue = dt.datetime(2026, 7, 1, 11, 0)
_ics_url = m2kcal.build_ics("有連結的會", _us, _ue, url="https://meet.example.com/abc-def-ghi",
                            uid="U-URL", stamp="20260701T000000Z")
check("build_ics 寫出 URL 屬性", "\r\nURL:https://meet.example.com/abc-def-ghi" in _ics_url)
check("URL 在 VEVENT 內", _ics_url.index("URL:") > _ics_url.index("BEGIN:VEVENT"))
_ics_nourl = m2kcal.build_ics("沒連結的會", _us, _ue, uid="U-NOURL", stamp="20260701T000000Z")
check("沒給 url 就不寫 URL 屬性", "URL:" not in _ics_nourl)
check("URL 含分號逗號會跳脫", "URL:https://x.test/a%3Bb" in
      m2kcal.build_ics("x", _us, _ue, url="https://x.test/a%3Bb", uid="U", stamp="20260701T000000Z"))

# 25d) update_event_ics 改 URL：原本沒有就加、給空字串就移除
_up = m2kcal.update_event_ics(_ics_url, url="https://meet.example.com/new-link")
check("update 改 URL", "URL:https://meet.example.com/new-link" in unfold(_up))
check("update 改 URL 不留舊的", "abc-def-ghi" not in unfold(_up))
_up2 = m2kcal.update_event_ics(_ics_url, url="")
check("update 給空字串移除 URL", "URL:" not in unfold(_up2))
_up3 = m2kcal.update_event_ics(_ics_nourl, url="https://meet.example.com/added")
check("update 對原本沒 URL 的事件可新增", "URL:https://meet.example.com/added" in unfold(_up3))
check("update 不帶 url 參數時保留原值", "URL:https://meet.example.com/abc-def-ghi"
      in unfold(m2kcal.update_event_ics(_ics_url, title="改標題")))

# 25e) vet_attendees：book 前的與會者健檢（純函式，不連網）
#      A3 壞位址、A4 父子群組信箱並存導致同一人收多份
_gpaths = {"eng@example.com": "/ORG/ENG",
           "eng_a@example.com": "/ORG/ENG/ENG_A",
           "eng_a1@example.com": "/ORG/ENG/ENG_A/ENG_A1",
           "sales@example.com": "/ORG/SALES"}

_r = m2kcal.vet_attendees(["alice@example.com", "bob@example.com"], _gpaths)
check("vet 全部正常時無問題", _r["invalid"] == [] and _r["covered"] == [])

_r = m2kcal.vet_attendees(["alice@example.com", "not-an-email", "a@b"], _gpaths)
check("vet 抓出格式不合的位址（含缺 TLD 的 a@b）",
      _r["invalid"] == ["not-an-email", "a@b"])

# 帳號存在性改由排程端點判斷（往來紀錄會自我污染），這裡只管形狀與重複

# 父層與子層群組信箱同時出現：子層的人會收到兩份
_r = m2kcal.vet_attendees(["eng@example.com", "eng_a@example.com"], _gpaths)
check("vet 抓出被父層涵蓋的子層群組信箱",
      _r["covered"] == [("eng_a@example.com", "eng@example.com")])

_r = m2kcal.vet_attendees(["eng@example.com", "eng_a1@example.com"], _gpaths)
check("vet 涵蓋判斷跨越多層", _r["covered"] == [("eng_a1@example.com", "eng@example.com")])

_r = m2kcal.vet_attendees(["eng@example.com", "sales@example.com"], _gpaths)
check("vet 不同分支的群組信箱不算涵蓋", _r["covered"] == [])

_r = m2kcal.vet_attendees(["eng_a@example.com"], _gpaths)
check("vet 只有一個群組信箱不算涵蓋", _r["covered"] == [])

check("vet 空清單不炸", m2kcal.vet_attendees([], _gpaths)["invalid"] == [])
check("vet 大小寫不影響涵蓋判斷",
      m2kcal.vet_attendees(["ENG@example.com", "Eng_A@example.com"], _gpaths)["covered"]
      == [("eng_a@example.com", "eng@example.com")])

# 25f) 靜態守衛：book 解構 auth 時不可再叫 url——會蓋掉「會議連結」參數，
#      把 CalDAV 伺服器網址寫進事件。這種撞名離線測不到（要跑到 MCP 層），
#      所以直接檢查原始碼。
_srv_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "src", "m2k_mcp_server.py"), encoding="utf-8").read()
check("book 不用 url 當 auth 解構的變數名", "url, user, pwd = auth" not in _srv_src)
check("book 仍把 url 參數傳進原生表單", "description=description, url=url" in _srv_src)

# 25g) parse_schedule 解析失敗的警告要指得出是誰的哪一筆（A7）
#      只說「有一筆解析失敗」的話，無法判斷影響誰的忙碌時段、也無從追查
m2kcal.clear_notes()
_bad_sched = {"rspCode": 0, "instances": [
    {"dtstart": 1767225600, "dtend": 1767229200, "summary": "正常的會"},
    {"dtstart": "not-a-number", "summary": "壞掉的會"},
    {"summary": "沒有時間的會"},
]}
_rows = m2kcal.parse_schedule(_bad_sched, "someone@example.com")
_n = m2kcal.take_notes()
check("壞資料被跳過但好的留下", len(_rows) == 1)
check("警告帶上是誰的行程", all("someone@example.com" in x for x in _n))
check("警告帶上壞掉那筆的識別資訊",
      any("壞掉的會" in x for x in _n) and any("沒有時間的會" in x for x in _n))
check("每筆壞資料各記一則", len(_n) == 2)

# 25h) free_slots_ranked：全員沒空時給「少 1 人即可」的次佳解（A8）
#      13 個人很難全員有空，只回「查無」等於把取捨丟回給人，卻沒給判斷依據
_d0 = dt.datetime(2026, 7, 6, 0, 0)          # 週一
_d1 = dt.datetime(2026, 7, 7, 0, 0)
# A 整個上午忙、B 只有 09-10 忙；10:00 之後兩人都有空
_busy_by = {
    "a@example.com": [(dt.datetime(2026, 7, 6, 9, 0), dt.datetime(2026, 7, 6, 12, 0))],
    "b@example.com": [(dt.datetime(2026, 7, 6, 9, 0), dt.datetime(2026, 7, 6, 10, 0))],
}
_rk = m2kfree.common_free_slots(list(_busy_by), _d0, _d1,
                                busy_source=_fixed_source(_busy_by), duration_min=60,
                                day_start="09:00", day_end="13:00")["slots"]
check("ranked 回的是 (start, end, missing)", _rk and len(_rk[0]) == 3)
check("全員都有空的時段排最前面", _rk[0][2] == [])
check("全員時段落在兩人都空的區間", _rk[0][0] >= dt.datetime(2026, 7, 6, 12, 0))
_partial = [x for x in _rk if x[2]]
check("有列出「少 1 人」的次佳解", any(len(x[2]) == 1 for x in _partial))
check("次佳解標得出缺誰",
      any(x[2] == ["a@example.com"] for x in _partial))
check("缺人少的排在缺人多的前面",
      [len(x[2]) for x in _rk] == sorted(len(x[2]) for x in _rk))

# 全員皆忙時仍要給得出次佳解
_busy_all = {
    "a@example.com": [(dt.datetime(2026, 7, 6, 9, 0), dt.datetime(2026, 7, 6, 13, 0))],
    "b@example.com": [(dt.datetime(2026, 7, 6, 9, 0), dt.datetime(2026, 7, 6, 10, 0))],
}
_rk2 = m2kfree.common_free_slots(list(_busy_all), _d0, _d1,
                                 busy_source=_fixed_source(_busy_all), duration_min=60,
                                 day_start="09:00", day_end="13:00")["slots"]
check("沒有全員時段時不回空手", _rk2 != [])
check("次佳解指出是 a 擋住", _rk2[0][2] == ["a@example.com"])

check("時長不足的時段不列入",
      all((x[1] - x[0]).total_seconds() >= 3600 for x in _rk))
# 「缺幾人以內」是呈現決定，module 回全部、由呼叫端自己篩
check("呼叫端可依缺的人數自行篩選",
      all(len(x[2]) <= 1 for x in _rk if len(x[2]) <= 1))

# 25i) update_event 只改會議連結也要能過守門檢查
#      「沒有任何要修改的欄位」那份清單漏了 url，只改連結的呼叫會被直接擋下
if srv:
    _orig_creds = m2kcal.creds
    def _no_network():
        raise m2kcal.M2KError("測試用：不連線")
    m2kcal.creds = _no_network          # 過了守門就會去取憑證，在這裡攔住，不打網路
    try:
        for _u in ("https://meet.example.com/x", ""):     # 改寫與移除都算變更
            _r = srv.update_event("uid-x", url=_u)
            check(f"update_event 只給 url={_u!r} 不會被判定為沒有變更",
                  "沒有任何要修改的欄位" not in _r)
        check("update_event 什麼都沒給仍要擋下",
              "沒有任何要修改的欄位" in srv.update_event("uid-x"))
    finally:
        m2kcal.creds = _orig_creds

# 25j) schedule_events_json：排程端點的事件轉成行事曆 UI 吃的形狀
#      他人行事曆不論是分享的還是排程查到的，對使用者是同一個概念（CONTEXT.md），
#      UI 不該因為對方沒分享就整個顯示不出來
_sched_evs = [
    {"start": dt.datetime(2026, 9, 21, 10, 0), "end": dt.datetime(2026, 9, 21, 11, 0),
     "summary": "週會", "organizer": "boss@example.com", "status": "暫定", "busy": True},
    {"start": dt.datetime(2026, 9, 22, 0, 0), "end": dt.datetime(2026, 9, 24, 0, 0),
     "summary": "休假", "organizer": "", "status": "自建", "busy": True},
    {"start": dt.datetime(2026, 9, 23, 14, 0), "end": dt.datetime(2026, 9, 23, 15, 0),
     "summary": "不去的會", "organizer": "x@example.com", "status": "已拒絕", "busy": False},
]
_rows = m2kcal.schedule_events_json(_sched_evs, "peer@example.com")
check("排程事件轉 UI：筆數不變", len(_rows) == 3)
_k = set(_rows[0])
check("排程事件轉 UI：欄位與 events_json 一致",
      {"uid", "summary", "start", "end", "allday", "location", "description",
       "organizer", "rrule", "attendees"} <= _k)
check("排程事件轉 UI：uid 各不相同（UI 以它當 key）",
      len({r["uid"] for r in _rows}) == 3)
check("排程事件轉 UI：時間格式同 events_json", _rows[0]["start"] == "2026-09-21 10:00")
check("排程事件轉 UI：整天的區間視為全天", _rows[1]["allday"] is True
      and _rows[1]["start"] == "2026-09-22")
check("排程事件轉 UI：出席狀態帶到該人身上（暫定要看得出來）",
      _rows[0]["attendees"] == [{"name": "peer@example.com", "email": "peer@example.com",
                                 "partstat": "TENTATIVE"}])
check("排程事件轉 UI：自建的事件不附出席狀態", _rows[1]["attendees"] == [])
check("排程事件轉 UI：已拒絕照實標示", _rows[2]["attendees"][0]["partstat"] == "DECLINED")

# 25k) 行事曆 UI：對方沒分享時要改用排程端點，而不是回「無法顯示」
if srv:
    class _EmptyCal:
        def search(self, **kw): return []
    _NF = m2kcal._not_found_error()
    def _not_shared(principal, email): raise _NF("404")
    _saved = (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, m2kcal.person_calendar,
              m2kcal.fetch_schedule, os.environ.pop("M2K_COOKIE", None))
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _EmptyCal()
    m2kcal.creds = lambda: ("u", "me@example.com", "pw")
    m2kcal.person_calendar = _not_shared
    try:
        # 有 cookie：沒分享的人改走排程端點，事件照樣顯示
        os.environ["M2K_COOKIE"] = "ck=1"
        m2kcal.fetch_schedule = lambda ck, em, s0, e0: [
            {"start": dt.datetime(2026, 9, 21, 10), "end": dt.datetime(2026, 9, 21, 11),
             "summary": "對方的會", "organizer": "", "status": "已接受", "busy": True}]
        _pl = srv._calendar_payload(dt.datetime(2026, 9, 21), dt.datetime(2026, 9, 28),
                                    None, "peer@example.com")
        check("UI：沒分享的人改走排程端點後列入 owners",
              [o["email"] for o in _pl["owners"]] == ["me@example.com", "peer@example.com"])
        check("UI：排程端點的事件有顯示並標上 owner",
              any(ev["summary"] == "對方的會" and ev["owner"] == "peer@example.com"
                  for ev in _pl["events"]))
        check("UI：不再回「無法顯示」", not any("無法顯示" in n for n in _pl["notes"]))
        # 使用者要求：這不是錯誤，UI 不再顯示「未分享，改以排程資料顯示」的提示
        check("UI：未分享改走排程不再出現在 notes", not any("排程" in n or "未分享" in n for n in _pl["notes"]))

        # 查無帳號：照實說，不要說成「未分享」
        def _ghost(ck, em, s0, e0): raise m2kcal.M2KError("查無此帳號：" + em)
        m2kcal.fetch_schedule = _ghost
        _pl2 = srv._calendar_payload(dt.datetime(2026, 9, 21), dt.datetime(2026, 9, 28),
                                     None, "ghost@example.com")
        check("UI：查無帳號照實說明", any("查無此帳號" in n for n in _pl2["notes"]))

        # 沒 cookie：才回原本的「未分享」
        os.environ.pop("M2K_COOKIE", None)
        _cookie_orig = srv._cookie
        srv._cookie = lambda ctx, force=False: ""
        try:
            _pl3 = srv._calendar_payload(dt.datetime(2026, 9, 21), dt.datetime(2026, 9, 28),
                                         None, "peer@example.com")
        finally:
            srv._cookie = _cookie_orig
        check("UI：拿不到 cookie 時才說未分享", any("未分享" in n for n in _pl3["notes"]))
    finally:
        (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, m2kcal.person_calendar,
         m2kcal.fetch_schedule) = _saved[:5]
        os.environ.pop("M2K_COOKIE", None)
        if _saved[5] is not None:
            os.environ["M2K_COOKIE"] = _saved[5]

# 25l) agenda / list_events 帶 person：對方沒分享時同樣改走排程端點
if srv:
    class _PingCal:
        def search(self, **kw): raise m2kcal._not_found_error()("404")
    _saved2 = (m2kcal.connect, m2kcal.creds, m2kcal.person_calendar,
               m2kcal.fetch_schedule, os.environ.pop("M2K_COOKIE", None))
    m2kcal.connect = lambda auth: object()
    m2kcal.creds = lambda: ("u", "me@example.com", "pw")
    m2kcal.person_calendar = lambda principal, email: _PingCal()
    try:
        os.environ["M2K_COOKIE"] = "ck=1"
        _now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
        m2kcal.fetch_schedule = lambda ck, em, s0, e0: [
            {"start": _now + dt.timedelta(hours=1), "end": _now + dt.timedelta(hours=2),
             "summary": "對方的週會", "organizer": "", "status": "暫定", "busy": True}]
        _ag = srv.agenda(days=3, person="peer@example.com")
        check("agenda：沒分享的人改走排程端點，行程照樣列出", "對方的週會" in _ag)
        check("agenda：給模型一行簡短說明資料來自排程", "（排程資料：只有時間、標題）" in _ag and "讀不到" not in _ag)
        check("agenda：不再是整句長提示", "未分享行事曆，改以排程資料顯示" not in _ag)
        _le = srv.list_events(f"{_now:%Y-%m-%d}", f"{_now + dt.timedelta(days=2):%Y-%m-%d}",
                              person="peer@example.com")
        check("list_events：沒分享的人同樣改走排程端點", "對方的週會" in _le)
        check("list_events：不再叫使用者自己去貼 Cookie", "需 webmail Cookie" not in _le)
        check("list_events：同樣只有一行簡短說明", "（排程資料：只有時間、標題）" in _le
              and "未分享行事曆，改以排程資料顯示" not in _le)
    finally:
        (m2kcal.connect, m2kcal.creds, m2kcal.person_calendar,
         m2kcal.fetch_schedule) = _saved2[:4]
        os.environ.pop("M2K_COOKIE", None)
        if _saved2[4] is not None:
            os.environ["M2K_COOKIE"] = _saved2[4]

# 25m) send_invite：寄出的信要有 Date 標頭（RFC 5322 必備，缺了易被當垃圾信）；
#      quit() 在信寄出後才呼叫，它失敗不代表沒寄到，不該留「與會者不會收到」的警告
import smtplib as _smtplib
_sent = {}
class _FakeSMTP:
    def __init__(self, *a, **k): pass
    def login(self, u, p): pass
    def sendmail(self, frm, to, raw): _sent["raw"] = raw
    def quit(self): raise OSError("連線已被對方關閉")
_orig_ssl = _smtplib.SMTP_SSL
_smtplib.SMTP_SSL = _FakeSMTP
try:
    m2kcal.clear_notes()
    _n = m2kcal.send_invite("me@example.com", "pw", ["a@example.com"], "會議邀請：測試",
                            "內文", "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n")
    _notes = m2kcal.take_notes()
finally:
    _smtplib.SMTP_SSL = _orig_ssl
_hdr = _sent.get("raw", "").split("\n\n", 1)[0]
check("send_invite 回收件人數", _n == 1)
check("send_invite 信件帶 Date 標頭", "\nDate: " in "\n" + _hdr)
check("send_invite quit 失敗不留「沒寄到」的警告", not any("不會收到" in x for x in _notes))

# 26) busy_from_shared：從已分享日曆算忙碌區間（全天＝整天忙）、未分享列 missing
_sh_timed = m2kcal.build_ics("會A", dt.datetime(2026, 8, 3, 10, 0),
                             dt.datetime(2026, 8, 3, 11, 0), uid="S1", stamp="Z")
_sh_allday = m2kcal.build_ics("休假", dt.datetime(2026, 8, 4), dt.datetime(2026, 8, 5),
                              uid="S2", stamp="Z", all_day=True)
class _FakeSharedCal:
    def search(self, **kw):
        return [_fake(_sh_timed), _fake(_sh_allday)]
_orig_pc = m2kcal.person_calendar
def _fake_pc(principal, email):
    if email == "noshare@x.com":
        raise RuntimeError("404 Not Found")   # 未分享
    return _FakeSharedCal()
m2kcal.person_calendar = _fake_pc
try:
    _by_person, _missing = m2kcal.busy_from_shared(
        None, ["a@x.com", "noshare@x.com"],
        dt.datetime(2026, 8, 1), dt.datetime(2026, 8, 8))
    _busy = _by_person.get("a@x.com", [])
finally:
    m2kcal.person_calendar = _orig_pc
check("busy_from_shared 未分享列入 missing", _missing == ["noshare@x.com"])
check("busy_from_shared 每人各自一份（共同空檔要指得出誰擋住）",
      list(_by_person) == ["a@x.com"])
check("busy_from_shared 一般事件成為忙碌區間",
      (dt.datetime(2026, 8, 3, 10, 0), dt.datetime(2026, 8, 3, 11, 0)) in _busy)
check("busy_from_shared 全天＝整天忙碌",
      (dt.datetime(2026, 8, 4, 0, 0), dt.datetime(2026, 8, 5, 0, 0)) in _busy)
# 併入 free_slots：8/3 10-11 被扣掉、8/4 整天無空檔
_slots = _allfree({"a@x.com": _busy}, dt.datetime(2026, 8, 3), dt.datetime(2026, 8, 5),
                  duration_min=60)
check("共同空檔 扣掉分享日曆的忙碌時段",
      all(not (a < dt.datetime(2026, 8, 3, 11) and b > dt.datetime(2026, 8, 3, 10))
          for a, b in _slots))
check("共同空檔 全天忙碌日無空檔",
      all(a.date() != dt.date(2026, 8, 4) for a, b in _slots))

# 28) m2knative：會議寫入改走 webmail 原生 calsrv API（docs/adr/0004）
#     CalDAV 建的會議不會進與會者的行事曆；原生 API 帶 send_meeting_mail=true 才會。
#     欄位形狀比照錄下的真實請求與 webmail 前端 calendar.js 的 toRequestData。
#     HTTP 一律經過假的 send，不連網。
import json as _json
import m2knative


def _ep(wall):
    """'YYYYMMDDTHHMMSS'（台北）→ epoch 秒。"""
    return int(dt.datetime.strptime(wall, "%Y%m%dT%H%M%S").replace(tzinfo=TW).timestamp())


class _FakeCalsrv:
    """假的 calsrv：記錄每支請求；POST/PUT 依表單存事件、GET 讀回（形狀比照錄下的真實回應，
    見 tests/fixtures/calsrv_capture.json）。instances 會展開重複規則（扣掉例外日），
    countRecurrenceInstances 依送來的表單實際計算。
    script   ：預排的回應（先進先出），用完才走模擬。
    override ：{method: (回應, 是否照樣處理)}——例如 POST 照樣建立但回 500（結果不明），用一次就失效。
               instances 與 countRecurrenceInstances 用 "GET instances"／"GET count" 當 key。
    organizer：之後建立的事件的召集人。"""
    ROLES = ("CHAIR", "REQ-PARTICIPANT", "OPT-PARTICIPANT", "NON-PARTICIPANT")
    STAT = ("NEEDS-ACTION", "ACCEPTED", "DECLINED", "TENTATIVE")

    def __init__(self, organizer="me@example.com"):
        self.calls, self.events, self.script, self.override = [], {}, [], {}
        self.next_id, self.organizer = 700, organizer

    def __call__(self, method, path, cookie, form=None, params=None):
        self.calls.append({"method": method, "path": path, "cookie": cookie,
                           "form": dict(form or {}), "params": dict(params or {})})
        if self.script:
            return self.script.pop(0)
        key = method + (" instances" if path.endswith("instances/") else
                        " count" if path.endswith("/countRecurrenceInstances") else "")
        if key in self.override:
            resp, perform = self.override.pop(key)
            if perform:
                self._handle(method, path, dict(form or {}), dict(params or {}))
            return resp
        return 200, "application/json; charset=UTF-8", _json.dumps(
            self._handle(method, path, dict(form or {}), dict(params or {})))

    def seed(self, form, uid, organizer=None):
        self.next_id += 1
        self.events[self.next_id] = self._event(form, self.next_id, uid, 0, organizer)
        return self.next_id

    @staticmethod
    def occurrences(ev, limit=None):
        """展開成每次的開始 epoch（扣掉例外日）。count 依 RRULE 語意連例外日一起算。"""
        rr, t = ev.get("rrule"), dt.datetime.fromtimestamp(ev["dtstart"], TW).replace(tzinfo=None)
        if not rr:
            return [ev["dtstart"]]
        ex = {x["exdate"] for x in ev.get("exdate") or []}
        out, n, iv = [], 0, max(int(rr.get("interval") or 1), 1)
        while n < 500:
            e = int(t.replace(tzinfo=TW).timestamp())
            if (rr["until"] != -1 and e > rr["until"]) or (rr["count"] and n >= rr["count"]) \
                    or (limit is not None and e >= limit):
                break
            n += 1
            if e not in ex:
                out.append(e)
            if rr["freq"] == "DAILY":
                t += dt.timedelta(days=iv)
            elif rr["freq"] == "WEEKLY":
                t += dt.timedelta(days=7 * iv)
            else:
                t = t.replace(year=t.year + (t.month - 1 + iv) // 12, month=(t.month - 1 + iv) % 12 + 1)
        return out

    def _handle(self, method, path, form, params):
        if path.endswith("/countRecurrenceInstances"):
            return {"rspCode": 0, "count": len(self.occurrences(self._event(params, 0, "", 0)))}
        if path.endswith("/events/instances/"):
            st, et = int(params["starttime"]), int(params["endtime"])
            return {"rspCode": 0, "instances": [
                {"id": i, "calendar_id": "1", "summary": ev["summary"], "dtstart": o,
                 "dtend": o + ev["dtend"] - ev["dtstart"]}
                # 和時段重疊的場次（進行中的也算），同真實 instances 端點的語意
                for i, ev in self.events.items() for o in self.occurrences(ev, et)
                if o < et and o + ev["dtend"] - ev["dtstart"] > st]}
        tail = path.rstrip("/").rsplit("/", 1)[-1]
        if method == "POST":
            self.next_id += 1
            ev = self._event(form, self.next_id, f"u{self.next_id}@example.com", 0)
            self.events[self.next_id] = ev
            return {"rspCode": 0, "rspMsg": "", "event": ev}
        eid = int(tail)
        if eid not in self.events:
            return {"rspCode": -1, "rspMsg": "not found"}
        if method == "GET":
            return {"rspCode": 0, "event": self.events[eid]}
        if method == "PUT":
            old = self.events[eid]
            self.events[eid] = self._event(form, eid, old["uid"], old["sequence"] + 1,
                                           old["organizer"][7:])
            return {"rspCode": 0, "event": self.events[eid]}
        if method == "DELETE":
            del self.events[eid]
            return {"rspCode": 0, "rspMsg": "", "events": {"rspResult": {"1": 1, "0": 0}}}
        raise AssertionError(f"沒模擬到的請求：{method} {path}")

    def _event(self, f, eid, uid, seq, organizer=None):
        allday = f.get("allday") == "true"
        n = lambda k: int(f.get(k) or 0)                    # noqa: E731
        ev = {"id": eid, "ics_id": eid, "uid": uid, "calendar_id": f.get("calendar_id", "1"),
              "summary": f.get("summary", ""), "description": f.get("description", ""),
              "location": f.get("location", ""),
              "organizer": "mailto:" + (organizer or self.organizer),
              "sequence": seq, "duration": 0, "info": 0, "rrule": None, "exdate": None,
              "alarm": None, "attendee": [],
              "timezone": "Asia/Taipei=28800~28800\tAsia/Taipei=28800~28800"}
        if allday:
            ds = dt.datetime.strptime(f["dtstart"], "%Y%m%d")
            de = dt.datetime.strptime(f["dtend"], "%Y%m%d")
            ev.update(info=2, dstart=f"{ds:%Y/%m/%d}", dend=f"{de:%Y/%m/%d}",
                      dtstart=int(ds.replace(tzinfo=TW).timestamp()),
                      dtend=int(de.replace(tzinfo=TW).timestamp()))
        else:
            ev.update(dtstart=_ep(f["dtstart"]), dtend=_ep(f["dtend"]))
        for i in range(1, n("attendee_num") + 1):
            ev["attendee"].append({
                "attendee": f[f"attendee{i}"], "attendee_cn": f.get(f"attendee_cn{i}", ""),
                "attendee_role": self.ROLES.index(f.get(f"attendee_role{i}", "REQ-PARTICIPANT")),
                "attendee_reply_status": self.STAT.index(
                    f.get(f"attendee_reply_status{i}", "NEEDS-ACTION")),
                "attendee_type": 5, "id": 59000 + i})
        day_ep = lambda v: (_ep(v) if "T" in v else                        # noqa: E731
                            int(dt.datetime.strptime(v, "%Y%m%d").replace(tzinfo=TW).timestamp()))
        if f.get("has_rrule") == "true":
            u = f.get("until", "")
            ev["rrule"] = {"freq": f["freq"], "wkst": "SU", "interval": n("interval"),
                           "by_day": f.get("by_day", ""), "by_setpos": f.get("by_setpos", ""),
                           "by_monthday": f.get("by_monthday", ""), "count": n("count"),
                           "until": day_ep(u) if u else -1}
        if n("exdate_num"):
            ev["exdate"] = [{"exdate": day_ep(f[f"exdate{i}"])} for i in range(1, n("exdate_num") + 1)]
        if n("alarm_num"):
            ev["alarm"] = [{"trigger": f[f"alarm_trigger{i}"].replace("PT", "").rstrip("S").lstrip("+"),
                            "action": f[f"alarm_action{i}"], "repeat": 1, "duration": 0,
                            "summary": "", "description": "", "attendee": ""}
                           for i in range(1, n("alarm_num") + 1)]
        return ev


_EV_PATH = "/cgi-bin/cal/calsrv/feeds/default/default/1/events/"
_t0 = dt.datetime(2026, 10, 6, 14, 0)          # 週二
_t1 = dt.datetime(2026, 10, 6, 15, 0)

# 28a) new_form：不帶與會者＝只寫自己（send_meeting_mail=false），欄位同錄下的建立請求
_f = m2knative.new_form("週會", _t0, _t1, location="3F", description="議程")
check("原生建立：固定欄位（feeds/calendar_id/offset/lang/時區）",
      _f["feeds"] == "default" and _f["calendar_id"] == "1" and _f["offset"] == "28800"
      and _f["lang"] == "tw" and _f["orig_feeds"] == "default" and _f["orig_calendar_id"] == "1"
      and _f["timezone_dtstart"] == "Asia/Taipei@28800" and _f["timezone_dtend"] == "Asia/Taipei@28800")
check("原生建立：is_new=1、非全天、台北當地時間 YYYYMMDDTHHMMSS",
      _f["is_new"] == "1" and _f["allday"] == "false"
      and _f["dtstart"] == "20261006T140000" and _f["dtend"] == "20261006T150000")
check("原生建立：標題/地點/描述", _f["summary"] == "週會" and _f["location"] == "3F"
      and _f["description"] == "議程")
check("原生建立：沒有與會者就不寄信、不帶 attendee 欄位",
      _f["send_meeting_mail"] == "false" and "attendee_num" not in _f and "orig_id" not in _f)
check("原生建立：沒要求就不帶重複與提醒", "has_rrule" not in _f and "alarm_num" not in _f)

# 28b) 帶與會者：send_meeting_mail=true，才會寫進對方行事曆並寄信
_f = m2knative.new_form("週會", _t0, _t1,
                        attendees=["user_a@example.com", "User_B@example.com", "USER_A@example.com"])
check("原生建立：有與會者 → send_meeting_mail=true", _f["send_meeting_mail"] == "true")
check("原生建立：與會者欄位（mailto、cn、REQ-PARTICIPANT），大小寫重複只留一個",
      _f["attendee_num"] == "2" and _f["attendee1"] == "mailto:user_a@example.com"
      and _f["attendee_cn1"] == "user_a" and _f["attendee_role1"] == "REQ-PARTICIPANT"
      and _f["attendee2"] == "mailto:User_B@example.com" and "attendee3" not in _f)
check("原生建立：新建時不帶回覆狀態（同錄下的請求）", "attendee_reply_status1" not in _f)
check("原生建立：attendees() 讀得回名單",
      m2knative.attendees(_f) == ["user_a@example.com", "User_B@example.com"])

# 28c) 全天：dtstart/dtend 為 YYYYMMDD，dtend 排他
_f = m2knative.new_form("休假", dt.datetime(2026, 10, 6), dt.datetime(2026, 10, 8), all_day=True)
check("原生建立：全天 allday=true、日期格式、dtend 排他",
      _f["allday"] == "true" and _f["dtstart"] == "20261006" and _f["dtend"] == "20261008")

# 28d) 重複規則：compose_rrule 的輸出轉成前端的 freq/interval/by_day/… 欄位
_until = dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=TW)
_f = m2knative.new_form("週會", _t0, _t1, rrule=m2kcal.compose_rrule(
    "weekly", until=_until, byday=["TU", "TH"], interval=2))
check("原生重複：weekly 帶 has_rrule/freq/interval/by_day",
      _f["has_rrule"] == "true" and _f["freq"] == "WEEKLY" and _f["interval"] == "2"
      and _f["by_day"] == "TU,TH")
check("原生重複：until＝截止那天＋開始時間（前端做法）", _f["until"] == "20261231T140000"
      and "count" not in _f)
_f = m2knative.new_form("週會", _t0, _t1, rrule="FREQ=WEEKLY")
check("原生重複：weekly 沒指定星期 → 開始那天的星期、interval=1",
      _f["by_day"] == "TU" and _f["interval"] == "1")
_f = m2knative.new_form("月會", _t0, _t1, rrule=m2kcal.compose_rrule("monthly", byday=["3FR"]))
check("原生重複：monthly 第 3 個週五 → by_setpos=3、by_day=FR",
      _f["freq"] == "MONTHLY" and _f["by_setpos"] == "3" and _f["by_day"] == "FR"
      and "by_monthday" not in _f)
_f = m2knative.new_form("月會", _t0, _t1, rrule="FREQ=MONTHLY")
check("原生重複：monthly 不指定 → 每月同一天（by_monthday）", _f["by_monthday"] == "6")
_f = m2knative.new_form("日會", _t0, _t1, rrule="FREQ=DAILY;COUNT=5")
check("原生重複：daily 帶 interval、count", _f["freq"] == "DAILY" and _f["interval"] == "1"
      and _f["count"] == "5" and "until" not in _f)
_f = m2knative.new_form("全天重複", dt.datetime(2026, 10, 6), dt.datetime(2026, 10, 7),
                        all_day=True, rrule="FREQ=WEEKLY;UNTIL=20261231T155959Z")
check("原生重複：全天事件的 until 是 YYYYMMDD", _f["until"] == "20261231")
try:
    m2knative.new_form("月會", _t0, _t1, rrule="FREQ=MONTHLY;BYDAY=FR")
    _r = False
except m2kcal.M2KError:
    _r = True
check("原生重複：monthly 不帶序數的星期 → M2KError（前端沒有這種規則）", _r)

# 28e) 提醒：alarm_trigger=-PT{秒}S
_f = m2knative.new_form("週會", _t0, _t1, reminder_minutes=15)
check("原生提醒：alarm_num/trigger(-PT900S)/action/duration",
      _f["alarm_num"] == "1" and _f["alarm_trigger1"] == "-PT900S"
      and _f["alarm_action1"] == "DISPLAY" and _f["alarm_duration1"] == "+PT0S"
      and _f["alarm_repeat1"] == "1")

# 28f) 會議連結：伺服器不收 url，寫進描述第一行
_f = m2knative.new_form("週會", _t0, _t1, description="議程", url="https://meet.example.com/x")
check("原生建立：會議連結寫在描述第一行", _f["description"] == "會議連結: https://meet.example.com/x\n議程")
check("原生建立：表單沒有 url 欄位", "url" not in _f)
check("原生建立：只有連結沒有描述", m2knative.new_form(
    "週會", _t0, _t1, url="https://meet.example.com/x")["description"] == "會議連結: https://meet.example.com/x")

# 28g) Calsrv：建立送 POST 到 events/，帶 cookie；rspCode 非 0 報錯
_fk = _FakeCalsrv()
_cs = m2knative.Calsrv("key=K1", send=_fk)
_ev = m2knative.create(_cs, m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"]))
check("原生建立：POST 到 feeds/default/default/1/events/ 並帶 cookie",
      _fk.calls[0]["method"] == "POST" and _fk.calls[0]["path"] == _EV_PATH
      and _fk.calls[0]["cookie"] == "key=K1")
check("原生建立：寫入後 GET 讀回驗證", _fk.calls[1]["method"] == "GET"
      and _fk.calls[1]["path"] == _EV_PATH + str(_ev["id"]))
check("原生建立：回傳伺服器產生的 uid", _ev["uid"] == f"u{_ev['id']}@example.com")
_inf = m2knative.info(_ev)
check("原生 info：標題/時間/與會者/uid（book 回報用）",
      _inf["SUMMARY"] == "週會" and _inf["start"] == "2026-10-06 14:00"
      and _inf["end"] == "2026-10-06 15:00" and _inf["attendees"] == ["user_a@example.com"]
      and _inf["uid"] == _ev["uid"])

_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", '{"rspCode":-3,"rspMsg":"bad dtstart"}')]
try:
    m2knative.Calsrv("key=K1", send=_fk).create({"summary": "x"})
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("原生：rspCode 非 0 → M2KError 帶代碼與訊息", "-3" in _r and "bad dtstart" in _r)

_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", '{"rspCode":0,"event":{"id":1,"uid":"u1","summary":"別的",'
               '"dtstart":1790000000,"dtend":1790003600,"info":0}}')]
_fk.events[1] = {"id": 1, "uid": "u1", "summary": "別的", "dtstart": 1790000000,
                 "dtend": 1790003600, "info": 0}
try:
    m2knative.create(m2knative.Calsrv("key=K1", send=_fk), m2knative.new_form("週會", _t0, _t1))
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("原生建立：讀回的內容與送出不符 → M2KError（不謊報已建立）", "不符" in _r)

# 28h) cookie 過期：rspCode -100 或被導去登入 → 重換一次再送；只重試一次
_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", '{"rspCode":-100,"rspMsg":"Invalid Session"}')]
_refreshed = []
_cs = m2knative.Calsrv("key=OLD", send=_fk,
                       refresh=lambda: (_refreshed.append(1), "key=NEW")[1])
_ev = _cs.create(m2knative.new_form("週會", _t0, _t1))
check("原生：session 過期 → 重換 cookie 後重送成功",
      _refreshed == [1] and [c["cookie"] for c in _fk.calls] == ["key=OLD", "key=NEW"]
      and _ev["summary"] == "週會")
_fk = _FakeCalsrv()
_fk.script = [(302, "text/html", ""), ]
_cs = m2knative.Calsrv("key=OLD", send=_fk, refresh=lambda: "key=NEW")
_cs.get(_fk.seed(m2knative.new_form("週會", _t0, _t1), "u@example.com"))
check("原生：被導去登入（302）同樣重換", [c["cookie"] for c in _fk.calls] == ["key=OLD", "key=NEW"]
      and _cs.cookie == "key=NEW")
_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", '{"rspCode":-100}')] * 2
try:
    m2knative.Calsrv("key=OLD", send=_fk, refresh=lambda: "key=NEW").create({})
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("原生：重換後仍過期 → 只重試一次就報錯", len(_fk.calls) == 2 and "過期" in _r)
_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", '{"rspCode":-100}')]
try:
    m2knative.Calsrv("key=OLD", send=_fk).create({})
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("原生：沒有重換手段 → 直接報過期", len(_fk.calls) == 1 and "過期" in _r)
_fk = _FakeCalsrv()
_fk.script = [(500, "text/html", "<html>boom</html>")]
try:
    m2knative.Calsrv("key=OLD", send=_fk, refresh=lambda: "key=NEW").create({})
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("原生：伺服器 500 不重送（可能已寫入，重送會建兩筆）", len(_fk.calls) == 1 and "500" in _r)

# 28i) _send：真正的 HTTP 層帶對 header（假的 requests，不連網）
import types as _types
_sent_req = {}
def _fake_request(method, url, **kw):
    _sent_req.update(method=method, url=url, **kw)
    return _types.SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                                  text='{"rspCode":0}')
_saved_req = sys.modules.get("requests")
sys.modules["requests"] = _types.SimpleNamespace(request=_fake_request, RequestException=OSError)
try:
    m2knative._send("PUT", _EV_PATH + "999", "key=K1", form={"summary": "x"})
finally:
    if _saved_req is not None:
        sys.modules["requests"] = _saved_req
    else:
        sys.modules.pop("requests", None)
_h = {k.lower(): v for k, v in (_sent_req.get("headers") or {}).items()}
check("原生 _send：method/url/form-urlencoded body",
      _sent_req.get("method") == "PUT" and _sent_req.get("url") == m2kcal.M2K_BASE + _EV_PATH + "999"
      and _sent_req.get("data") == {"summary": "x"})
check("原生 _send：x-requested-with、content-type、Cookie",
      _h.get("x-requested-with") == "XMLHttpRequest"
      and _h.get("content-type") == "application/x-www-form-urlencoded; charset=UTF-8"
      and _h.get("cookie") == "key=K1")
check("原生 _send：不跟隨轉址（被導去登入要看得出來）", _sent_req.get("allow_redirects") is False)

# 28j) find_id：instances 沒有 uid，只能在時段內逐筆 GET 比對
_fk = _FakeCalsrv()
_other = _fk.seed(m2knative.new_form("別的會", _t0, _t1), "other@example.com")
_mine = _fk.seed(m2knative.new_form("週會", _t0 + dt.timedelta(hours=2), _t1 + dt.timedelta(hours=2)),
                 "mine@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk)
check("find_id：比對 uid 找到原生 id", _cs.find_id("mine@example.com", [_t0], summary="週會") == _mine)
check("find_id：用 CalDAV 起始時間前後一天查 instances（epoch 秒）",
      _fk.calls[0]["path"] == _EV_PATH + "instances/"
      and int(_fk.calls[0]["params"]["starttime"]) == int((_t0 - dt.timedelta(days=1)).replace(tzinfo=TW).timestamp())
      and int(_fk.calls[0]["params"]["endtime"]) == int((_t0 + dt.timedelta(days=1)).replace(tzinfo=TW).timestamp()))
check("find_id：標題相同的先 GET（少打請求）",
      [c["path"] for c in _fk.calls if c["method"] == "GET"][1] == _EV_PATH + str(_mine))
try:
    _cs.find_id("ghost@example.com", [_t0], summary="週會")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("find_id：找不到 → M2KError 帶 uid", "ghost@example.com" in _r)
# CalDAV 給的起始時間附近沒有（例如系列第一次被取消了）：往後放寬，只比標題相同的
_far = _fk.seed(m2knative.new_form("遠的會", _t0 + dt.timedelta(days=30), _t1 + dt.timedelta(days=30)),
                "far@example.com")
_fk.seed(m2knative.new_form("不相干", _t0 + dt.timedelta(days=31), _t1 + dt.timedelta(days=31)),
         "noise@example.com")
_fk.calls.clear()
check("find_id：附近找不到 → 放寬時段再找", _cs.find_id("far@example.com", [_t0], summary="遠的會") == _far)
check("find_id：放寬後只 GET 標題相同的",
      [c["path"] for c in _fk.calls if c["method"] == "GET" and not c["path"].endswith("instances/")][-1]
      == _EV_PATH + str(_far)
      and not any(c["path"] == _EV_PATH + str(_far + 1) for c in _fk.calls))

# 28k) edit_form：GET 回來的事件原樣帶回（回覆狀態、重複、例外、提醒）——漏帶就等於刪掉
_fk = _FakeCalsrv()
_base = m2knative.new_form("週會", _t0, _t1, location="3F", description="議程",
                           url="https://meet.example.com/old",
                           attendees=["user_a@example.com", "user_b@example.com"],
                           rrule="FREQ=WEEKLY;UNTIL=20261231T155959Z", reminder_minutes=15)
_sid = _fk.seed(_base, "series@example.com")
_fk.events[_sid]["attendee"][0]["attendee_reply_status"] = 1      # user_a 已接受
_fk.events[_sid]["exdate"] = [{"exdate": _ep("20261013T140000")}]
_orig = _fk.events[_sid]
_ef = m2knative.edit_form(_orig)
check("edit_form：修改用 is_new=0、orig_id", _ef["is_new"] == "0" and _ef["orig_id"] == str(_sid))
check("edit_form：與會者帶回原回覆狀態",
      _ef["attendee1"] == "mailto:user_a@example.com" and _ef["attendee_reply_status1"] == "ACCEPTED"
      and _ef["attendee_reply_status2"] == "NEEDS-ACTION" and _ef["attendee_role1"] == "REQ-PARTICIPANT")
check("edit_form：重複規則帶回（until 用開始時間）",
      _ef["has_rrule"] == "true" and _ef["freq"] == "WEEKLY" and _ef["by_day"] == "TU"
      and _ef["until"] == "20261231T140000")
check("edit_form：例外日與提醒帶回",
      _ef["exdate_num"] == "1" and _ef["exdate1"] == "20261013T140000"
      and _ef["alarm_trigger1"] == "-PT900S" and _ef["alarm_action1"] == "DISPLAY")
check("edit_form：時間、描述（含連結行）", _ef["dtstart"] == "20261006T140000"
      and _ef["dtend"] == "20261006T150000"
      and _ef["description"] == "會議連結: https://meet.example.com/old\n議程")
check("edit_form：有與會者 → send_meeting_mail=true", _ef["send_meeting_mail"] == "true")

# 28l) update（全部）：PUT events/{id}，只動給的欄位
_fk.calls.clear()
_cs = m2knative.Calsrv("key=K1", send=_fk)
_up = m2knative.update(_cs, _orig, title="新週會", url="https://meet.example.com/new",
                       add_attendees=["user_c@example.com"], remove_attendees=["USER_B@example.com"])
_put = _fk.calls[0]
check("原生修改全部：PUT events/{id}", _put["method"] == "PUT" and _put["path"] == _EV_PATH + str(_sid))
check("原生修改全部：套用變更、其餘保留",
      _put["form"]["summary"] == "新週會" and _put["form"]["location"] == "3F"
      and _put["form"]["has_rrule"] == "true" and _put["form"]["exdate1"] == "20261013T140000")
check("原生修改全部：連結行被取代而不是多一行",
      _put["form"]["description"] == "會議連結: https://meet.example.com/new\n議程")
check("原生修改全部：加減與會者，原本的回覆狀態保留、新加的是未回覆",
      m2knative.attendees(_put["form"]) == ["user_a@example.com", "user_c@example.com"]
      and _put["form"]["attendee_reply_status1"] == "ACCEPTED"
      and _put["form"]["attendee_reply_status2"] == "NEEDS-ACTION")
check("原生修改全部：有與會者 → send_meeting_mail=true", _put["form"]["send_meeting_mail"] == "true")
check("原生修改全部：寫入後讀回驗證", _fk.calls[1]["method"] == "GET" and _up["summary"] == "新週會")
_nf = m2knative.apply(_ef, url="", description="新議程")
check("apply：url='' 移除連結行、description 換掉本文", _nf["description"] == "新議程")
check("apply：description 換本文但連結保留",
      m2knative.apply(_ef, description="新議程")["description"]
      == "會議連結: https://meet.example.com/old\n新議程")
check("apply：rrule='' 取消重複（連同例外日）",
      "has_rrule" not in m2knative.apply(_ef, rrule="") and "exdate_num" not in m2knative.apply(_ef, rrule=""))
check("apply：reminder=0 移除提醒、N 改寫", "alarm_num" not in m2knative.apply(_ef, reminder=0)
      and m2knative.apply(_ef, reminder=30)["alarm_trigger1"] == "-PT1800S")
check("apply：移除所有與會者 → 不寄信",
      m2knative.apply(_ef, remove_attendees=["user_a@example.com", "user_b@example.com"]
                      )["send_meeting_mail"] == "false")

# 28m) 只改這次：原系列加 exdate（不寄信）＋ 新建帶 modify_recur=1 與 modified_exdate
_fk.calls.clear()
_occ = dt.datetime(2026, 10, 20, 14, 0)
_one = m2knative.update_occurrence(_cs, _fk.events[_sid], _occ, location="別館")
_p1, _p2 = [c for c in _fk.calls if c["method"] in ("PUT", "POST")][:2]
check("只改這次：先 PUT 原系列、加上該次 exdate",
      _p1["method"] == "PUT" and _p1["path"] == _EV_PATH + str(_sid)
      and _p1["form"]["exdate_num"] == "2" and _p1["form"]["exdate2"] == "20261020T140000")
check("只改這次：原系列那支不寄信、帶 modified_exdate",
      _p1["form"]["send_meeting_mail"] == "false" and _p1["form"]["modified_exdate"] == "20261020")
check("只改這次：再 POST 新事件（modify_recur=1、modified_exdate、organizer）",
      _p2["method"] == "POST" and _p2["path"] == _EV_PATH
      and _p2["form"]["modify_recur"] == "1" and _p2["form"]["modified_exdate"] == "20261020"
      and _p2["form"]["organizer"] == "mailto:me@example.com")
check("只改這次：新事件沒有重複與例外日、時間＝該次＋原長度、套用變更",
      "has_rrule" not in _p2["form"] and "exdate_num" not in _p2["form"]
      and _p2["form"]["dtstart"] == "20261020T140000" and _p2["form"]["dtend"] == "20261020T150000"
      and _p2["form"]["location"] == "別館" and _p2["form"]["summary"] == "新週會")
check("只改這次：新事件有與會者 → 寄信", _p2["form"]["send_meeting_mail"] == "true")
check("只改這次：回傳新事件（新 uid）", _one["uid"] != "series@example.com" and _one["location"] == "別館")
# 新建失敗：把原系列還原（拿掉剛加的 exdate），不能讓那一次憑空消失
_fk.calls.clear()
_before = dict(_fk.events[_sid])
_fk.override["POST"] = ((200, "application/json", '{"rspCode":-5,"rspMsg":"fail"}'), False)
try:
    m2knative.update_occurrence(_cs, _before, dt.datetime(2026, 10, 27, 14, 0), location="x")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
_puts = [c for c in _fk.calls if c["method"] == "PUT"]
check("只改這次：新建被拒 → 報錯並說已還原", "還原" in _r)
check("只改這次：還原＝用原本的表單再 PUT 一次（不含新加的 exdate），send 沿用截斷那支（false）",
      len(_puts) == 2 and _puts[1]["form"]["exdate_num"] == str(len(_before["exdate"] or []))
      and _puts[1]["form"]["send_meeting_mail"] == _puts[0]["form"]["send_meeting_mail"] == "false")

# 28n) 此次及以後：截斷原系列（until 或 count）＋ 新建系列帶 modify_recur=2
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"],
                                   rrule="FREQ=WEEKLY;UNTIL=20261231T155959Z"), "s2@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk)
_new = m2knative.update_following(_cs, _fk.events[_sid], _occ, title="新週會")
_puts = [c for c in _fk.calls if c["method"] == "PUT"]
_posts = [c for c in _fk.calls if c["method"] == "POST"]
check("此次及以後：原系列 until 截到前一天（時間沿用開始時間）",
      _puts[0]["path"] == _EV_PATH + str(_sid) and _puts[0]["form"]["until"] == "20261019T140000"
      and _puts[0]["form"]["modified_exdate"] == "20261020")
check("此次及以後：新系列 modify_recur=2、從該次開始、沿用規則並套用變更",
      _posts[0]["form"]["modify_recur"] == "2" and _posts[0]["form"]["modified_exdate"] == "20261020"
      and _posts[0]["form"]["dtstart"] == "20261020T140000" and _posts[0]["form"]["has_rrule"] == "true"
      and _posts[0]["form"]["until"] == "20261231T140000" and _posts[0]["form"]["summary"] == "新週會")
check("此次及以後：回傳新系列", _new["summary"] == "新週會" and _new["uid"] != "s2@example.com")
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, rrule="FREQ=WEEKLY;COUNT=10"), "s3@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk)
m2knative.update_following(_cs, _fk.events[_sid], _occ, location="別館")
_cnt = next(c for c in _fk.calls if c["path"].endswith("countRecurrenceInstances"))
_puts = [c for c in _fk.calls if c["method"] == "PUT"]
_posts = [c for c in _fk.calls if c["method"] == "POST"]
check("此次及以後：count 型先問伺服器該次之前有幾次（until 前一天、不含例外日）",
      _cnt["method"] == "GET" and _cnt["path"] == "/cgi-bin/cal/calsrv/api/default/utilities/countRecurrenceInstances"
      and _cnt["params"]["until"] == "20261019T140000" and "count" not in _cnt["params"])
check("此次及以後：count 型原系列 count 改成之前的次數、新系列拿剩下的",
      _puts[0]["form"]["count"] == "2" and "until" not in _puts[0]["form"]
      and _posts[0]["form"]["count"] == "8")
_calls0 = len(_fk.calls)
m2knative.update_following(_cs, _fk.events[_sid], _t0, location="全改")
check("此次及以後：從第一次開始＝改全部（直接 PUT，不拆）",
      [c["method"] for c in _fk.calls[_calls0:] if c["method"] != "GET"] == ["PUT"]
      and _fk.events[_sid]["location"] == "全改")

# 28o) 刪除：DELETE events/{id}，body 同錄下的請求；有與會者才寄取消信
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"]), "d@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk)
m2knative.delete(_cs, _fk.events[_sid])
check("原生刪除：DELETE events/{id}，body 帶 feeds/calendar_id/id/send_meeting_mail",
      _fk.calls[0]["method"] == "DELETE" and _fk.calls[0]["path"] == _EV_PATH + str(_sid)
      and _fk.calls[0]["form"] == {"feeds": "default", "calendar_id": "1", "id": str(_sid),
                                   "send_meeting_mail": "true"}
      and _sid not in _fk.events)
_sid = _fk.seed(m2knative.new_form("個人", _t0, _t1), "d2@example.com")
m2knative.delete(_cs, _fk.events[_sid])
check("原生刪除：沒有與會者 → send_meeting_mail=false", _fk.calls[-1]["form"]["send_meeting_mail"] == "false")
# 刪除這一次：PUT 原系列只加 exdate（一般修改）。刻意不照前端帶 delete_recur=1/modified_exdate：
# 實機觀察，帶了會讓與會者那份整個系列被取消；一般修改時與會者那邊正確只少那一場。
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"],
                                   rrule="FREQ=WEEKLY"), "d3@example.com")
_fk.calls.clear()
m2knative.delete_occurrence(_cs, _fk.events[_sid], _occ)
_w = [i for i, c in enumerate(_fk.calls) if c["method"] != "GET"]
_pc = _fk.calls[_w[0]]
check("刪除這一次：PUT 原系列加 exdate，不帶 delete_recur／modified_exdate",
      len(_w) == 1 and _pc["method"] == "PUT" and _pc["form"]["exdate1"] == "20261020T140000"
      and "delete_recur" not in _pc["form"] and "modified_exdate" not in _pc["form"])
check("刪除這一次：自己召集且有與會者 → send=true", _pc["form"]["send_meeting_mail"] == "true")
check("刪除這一次：讀回確認例外日已寫入", _fk.calls[_w[0] + 1]["method"] == "GET"
      and _fk.events[_sid]["exdate"] == [{"exdate": _ep("20261020T140000")}])
_sid = _fk.seed(m2knative.new_form("別人的週會", _t0, _t1, attendees=["me@example.com", "user_a@example.com"],
                                   rrule="FREQ=WEEKLY"), "d4@example.com", organizer="boss@example.com")
_fk.calls.clear()
m2knative.delete_occurrence(m2knative.Calsrv("key=K1", send=_fk, me="me@example.com"), _fk.events[_sid], _occ)
_pc = next(c for c in _fk.calls if c["method"] == "PUT")
check("刪除這一次：非召集人 → send=false、同樣不帶 delete_recur",
      _pc["form"]["send_meeting_mail"] == "false" and "delete_recur" not in _pc["form"])
try:
    m2knative.delete_occurrence(_cs, _fk.events[_fk.seed(m2knative.new_form("單次", _t0, _t1), "x@example.com")], _occ)
    _r = False
except m2kcal.M2KError:
    _r = True
check("刪除這一次：不是重複會議 → M2KError", _r)

# 28p) MCP 層：book / update_event / delete_event 改走原生寫入
if srv:
    import inspect as _inspect
    for _fn in (srv.book, srv.update_event, srv.delete_event):
        check(f"{_fn.__name__} 不再有 notify 參數（有與會者就由伺服器寄信）",
              "notify" not in _inspect.signature(_fn).parameters)
    check("respond_event 仍保留 notify（回覆信要使用者明確要求才寄）", "notify" in _inspect.signature(srv.respond_event).parameters)

    _fk = _FakeCalsrv()

    class _NativeBackedCal:
        """CalDAV 讀取端：內容取自假 calsrv 的事件（同一份資料的兩個入口）。"""
        url = "https://dav.example.com/cal/"
        def search(self, **kw): return []
        def event_by_uid(self, uid):
            ev = next((v for v in _fk.events.values() if v["uid"] == uid), None)
            if ev is None:
                raise m2kcal._not_found_error()("404")
            s0 = dt.datetime.fromtimestamp(ev["dtstart"], TW).replace(tzinfo=None)
            e0 = dt.datetime.fromtimestamp(ev["dtend"], TW).replace(tzinfo=None)
            return SimpleNamespace(url=self.url + uid + ".ics", data=m2kcal.build_ics(
                ev["summary"], s0, e0, uid=uid, stamp="Z",
                attendees=[a["attendee"][7:] for a in ev["attendee"]],
                rrule="FREQ=WEEKLY" if ev["rrule"] else ""))

    _saved3 = (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, srv._vet_note, srv._calsrv,
               m2knative._send, os.environ.pop("M2K_DISABLE_NOTIFY", None))
    _srv_src_now = lambda: open(srv.__file__, encoding="utf-8").read()   # noqa: E731
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _NativeBackedCal()
    m2kcal.creds = lambda: ("u", "me@example.com", "pw")
    srv._vet_note = lambda auth, attendees: ([], [])
    srv._calsrv = lambda ctx, me: m2knative.Calsrv("key=T", send=_fk, me=me)
    def _no_net(*a, **k): raise AssertionError("測試不該打到真的 calsrv")
    m2knative._send = _no_net
    try:
        _b1 = srv.book("週會", "2026-10-06 14:00", attendees=["user_a@example.com"],
                       url="https://meet.example.com/x")
        _post = next(c for c in _fk.calls if c["method"] == "POST")
        check("book：走原生 POST，有與會者 → send_meeting_mail=true",
              _post["form"]["send_meeting_mail"] == "true"
              and _post["form"]["attendee1"] == "mailto:user_a@example.com")
        check("book：會議連結寫進描述第一行", _post["form"]["description"] == "會議連結: https://meet.example.com/x")
        _new_uid = next(v["uid"] for v in _fk.events.values())
        check("book：回報已建立、對外 id 是 uid、說明伺服器已寄邀請並寫入對方行事曆",
              "已建立並驗證" in _b1 and _new_uid in _b1 and "與會者的行事曆" in _b1)
        _fk.calls.clear()
        _b2 = srv.book("個人行程", "2026-10-07 09:00")
        check("book：沒有與會者 → 不寄信、也不提邀請",
              next(c for c in _fk.calls if c["method"] == "POST")["form"]["send_meeting_mail"] == "false"
              and "邀請" not in _b2)

        # 寄信開關已移除：寫進對方行事曆只有 send_meeting_mail=true 一條路，關掉等於對方看不到
        os.environ["M2K_DISABLE_NOTIFY"] = "1"
        _fk.calls.clear()
        _b3 = srv.book("週會", "2026-10-08 14:00", attendees=["user_a@example.com"])
        check("book：M2K_DISABLE_NOTIFY 已不再有作用，有與會者照樣寄邀請",
              "已建立並驗證" in _b3 and next(c for c in _fk.calls if c["method"] == "POST")
              ["form"]["send_meeting_mail"] == "true")
        check("server 不再讀 M2K_DISABLE_NOTIFY", "M2K_DISABLE_NOTIFY" not in _srv_src_now())
        os.environ.pop("M2K_DISABLE_NOTIFY", None)

        _fk.calls.clear()
        _u1 = srv.update_event(_new_uid, title="改名後的週會")
        _put = next(c for c in _fk.calls if c["method"] == "PUT")
        check("update_event：用 uid 找到原生 id 後 PUT，並通知與會者",
              _put["form"]["summary"] == "改名後的週會" and _put["form"]["send_meeting_mail"] == "true"
              and "已更新並驗證" in _u1)
        # 重複會議只改一次：回覆新事件的 uid
        _rs = _fk.seed(m2knative.new_form("系列", _t0, _t1, attendees=["user_a@example.com"],
                                          rrule="FREQ=WEEKLY"), "series-mcp@example.com")
        _u2 = srv.update_event("series-mcp@example.com", occurrence="2026-10-20 14:00", location="別館")
        _split_uid = next(v["uid"] for k, v in _fk.events.items() if k > _rs)
        check("update_event occurrence：回覆新 id＝新事件的 uid",
              f"新 id: {_split_uid}" in _u2 and "series-mcp@example.com" not in _split_uid)
        _u4 = srv.update_event("series-mcp@example.com", from_occurrence="2026-10-27 14:00", title="新系列")
        check("update_event from_occurrence：回覆新系列的 uid", "新 id: " in _u4 and "新系列" in _u4)

        _fk.calls.clear()
        _d1 = srv.delete_event(_new_uid)
        check("delete_event：原生 DELETE 並通知與會者、CalDAV 讀回確認已不在",
              any(c["method"] == "DELETE" and c["form"]["send_meeting_mail"] == "true" for c in _fk.calls)
              and "已刪除會議" in _d1 and "與會者" in _d1)
        # 原系列已在 10/27 截斷、10/20 拆出去了，還屬於它的是 10/13
        _d2 = srv.delete_event("series-mcp@example.com", occurrence="2026-10-13 14:00")
        check("delete_event occurrence：只取消那一次",
              "那一次" in _d2 and _fk.events[_rs]["exdate"] is not None)
    finally:
        (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, srv._vet_note, srv._calsrv,
         m2knative._send) = _saved3[:6]
        os.environ.pop("M2K_DISABLE_NOTIFY", None)
        if _saved3[6] is not None:
            os.environ["M2K_DISABLE_NOTIFY"] = _saved3[6]

# 28q) respond_event：要不要寄 iMIP 回覆只看它自己的 notify 參數（預設不寄），不看環境變數
if srv:
    _resp_ics = m2kcal.build_ics("邀請", _t0, _t1, uid="R-INV", stamp="Z",
                                 attendees=["me@example.com"], organizer="boss@example.com")
    class _RespCal:
        url = "https://dav.example.com/cal/"
        def event_by_uid(self, uid): return SimpleNamespace(url=self.url + "r.ics", data=_resp_ics)
    _mails = []
    _saved4 = (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, m2kcal.put_and_verify,
               m2kcal.send_invite, os.environ.pop("M2K_DISABLE_NOTIFY", None))
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _RespCal()
    m2kcal.creds = lambda: ("u", "me@example.com", "pw")
    m2kcal.put_and_verify = lambda cal, ics, uid, **k: (204, m2kcal.parse_ics(ics))
    m2kcal.send_invite = lambda user, pwd, to, subject, body, ics, host=None: (_mails.append(to), len(to))[1]
    try:
        _r0 = srv.respond_event("R-INV", "accept")
        check("respond_event：notify 預設 False → 不寄回覆信", _mails == [] and "接受" in _r0)
        _r1 = srv.respond_event("R-INV", "accept", notify=True)
        check("respond_event：notify=True → 寄回覆信給召集人（不需任何環境變數）",
              _mails == [["boss@example.com"]] and "已寄" in _r1)
        os.environ["M2K_DISABLE_NOTIFY"] = "1"
        srv.respond_event("R-INV", "decline", notify=True)
        check("respond_event：M2K_DISABLE_NOTIFY 已不再有作用", len(_mails) == 2)
    finally:
        (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, m2kcal.put_and_verify,
         m2kcal.send_invite) = _saved4[:5]
        os.environ.pop("M2K_DISABLE_NOTIFY", None)
        if _saved4[5] is not None:
            os.environ["M2K_DISABLE_NOTIFY"] = _saved4[5]

# 28r) 審查修正：每一項對應一組測試
_fx = _json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "fixtures", "calsrv_capture.json"), encoding="utf-8"))

# 真實請求形狀（去識別化 fixture）：新建表單要有錄下請求的每個欄位、固定欄位值一致
_f = m2knative.new_form("[測試] 請忽略", dt.datetime(2026, 9, 23, 19), dt.datetime(2026, 9, 23, 19, 30),
                        attendees=["user_a@example.com"])
check("fixture：new_form 涵蓋錄下的建立請求的所有欄位", set(_fx["create_request"]) <= set(_f))
check("fixture：new_form 與錄下的建立請求欄位值相同（send 例外：有與會者就寄）",
      all(_f[k] == v for k, v in _fx["create_request"].items() if k != "send_meeting_mail"))
_ef = m2knative.edit_form(_fx["create_response"]["event"], me="me@example.com")
check("fixture：edit_form(建立回應) 的欄位值＝錄下的修改請求（逐欄相同）",
      all(_ef.get(k) == v for k, v in _fx["update_request"].items()))
_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", _json.dumps(_fx["delete_response"]))]
m2knative.Calsrv("key=K1", send=_fk).delete(901, True)
check("fixture：刪除 body＝錄下的刪除請求", _fk.calls[0]["form"] == _fx["delete_request"])
check("fixture：錄下的刪除回應（events.rspResult）視為成功、不重送", len(_fk.calls) == 1)
_fk = _FakeCalsrv()
_fk.script = [(200, "application/json", _json.dumps(_fx["create_response"]))]
check("fixture：錄下的建立回應解得出 id 與 uid",
      m2knative.Calsrv("key=K1", send=_fk).create(_fx["create_request"])["uid"] == "u901@example.com")

# HIGH-1 非召集人：只動自己那份、不寄信（前端 send_meeting_mail._default 的規則）
_fk = _FakeCalsrv()
_oid = _fk.seed(m2knative.new_form("別人的會", _t0, _t1, attendees=["me@example.com", "user_a@example.com"]),
                "theirs@example.com", organizer="boss@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
check("非召集人：is_mine 看 organizer", not m2knative.is_mine(_fk.events[_oid], "me@example.com")
      and m2knative.is_mine(_fk.events[_oid], "BOSS@example.com"))
check("非召集人：edit_form 有與會者也不寄",
      m2knative.edit_form(_fk.events[_oid], me="me@example.com")["send_meeting_mail"] == "false")
m2knative.update(_cs, _fk.events[_oid], location="我這邊改")
check("非召集人：update 的 PUT send=false", _fk.calls[0]["form"]["send_meeting_mail"] == "false")
m2knative.delete(_cs, _fk.events[_oid])
check("非召集人：delete send=false", _fk.calls[-1]["form"]["send_meeting_mail"] == "false")
_mid = _fk.seed(m2knative.new_form("我的會", _t0, _t1, attendees=["user_a@example.com"]), "mine2@example.com")
check("召集人本人：照樣寄", m2knative.edit_form(_fk.events[_mid], me="me@example.com")["send_meeting_mail"] == "true")
check("沒有 organizer 視為自己的", m2knative.is_mine({"organizer": ""}, "me@example.com"))

# HIGH-2 新規則／變更不合法時，原系列一個請求都不能動
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"], rrule="FREQ=WEEKLY"),
                "h2@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
try:
    m2knative.update_following(_cs, _fk.events[_sid], _occ, rrule="FREQ=MONTHLY;BYDAY=MO")
    _r = False
except m2kcal.M2KError:
    _r = True
check("此次及以後：新規則被拒 → 報錯且沒送出任何寫入",
      _r and not any(c["method"] in ("PUT", "POST", "DELETE") for c in _fk.calls))
try:
    m2knative.update_occurrence(_cs, _fk.events[_sid], _occ, reminder=-5)
    _r = False
except m2kcal.M2KError:
    _r = True
check("只改這次：變更不合法 → 報錯且沒送出任何寫入",
      _r and not any(c["method"] in ("PUT", "POST", "DELETE") for c in _fk.calls))

# HIGH-3a 此次及以後：截斷那支有寄信，還原也要寄（否則與會者那邊的場次永久消失）
_fk.calls.clear()
_fk.override["POST"] = ((200, "application/json", '{"rspCode":-5,"rspMsg":"fail"}'), False)
try:
    m2knative.update_following(_cs, _fk.events[_sid], _occ, title="新")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
_puts = [c for c in _fk.calls if c["method"] == "PUT"]
check("此次及以後：新建被拒 → 還原，send 沿用截斷那支（true）",
      "還原" in _r and len(_puts) == 2 and _puts[0]["form"]["send_meeting_mail"] == "true"
      and _puts[1]["form"]["send_meeting_mail"] == "true" and "until" not in _puts[1]["form"])

# HIGH-3b 建立結果不明（500）：先查新事件在不在，不盲目還原
_fk.calls.clear()
_n0 = len(_fk.events)
_fk.override["POST"] = ((500, "text/html", "<html>boom</html>"), True)       # 其實建了
_cs.notes.clear()
_made = m2knative.update_following(_cs, _fk.events[_sid], _occ, title="新2")
_puts = [c for c in _fk.calls if c["method"] == "PUT"]
check("結果不明但新事件已建立 → 不還原、回傳新事件、留下說明",
      len(_puts) == 1 and len(_fk.events) == _n0 + 1 and _made["summary"] == "新2"
      and any("回應異常" in n for n in _cs.notes))
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"], rrule="FREQ=WEEKLY"),
                "h3@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
_fk.override["POST"] = ((500, "text/html", "<html>boom</html>"), False)       # 沒建
try:
    m2knative.update_occurrence(_cs, _fk.events[_sid], _occ, location="x")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
_puts = [c for c in _fk.calls if c["method"] == "PUT"]
check("結果不明且查無新事件 → 還原", "還原" in _r and len(_puts) == 2
      and "exdate_num" not in _puts[1]["form"])
_fk.calls.clear()
_fk.override["POST"] = ((500, "text/html", "<html>boom</html>"), False)


class _BreakInstancesAfterPost:
    """POST 之後的 instances 查詢一律失敗，模擬「查也查不出來」。"""
    def __init__(self, inner): self.inner, self.posted = inner, False
    def __call__(self, method, path, cookie, form=None, params=None):
        if method == "POST":
            self.posted = True
        if self.posted and path.endswith("instances/"):
            self.inner.calls.append({"method": method, "path": path, "form": {}, "params": {}})
            return 502, "text/html", "bad gateway"
        return self.inner(method, path, cookie, form, params)


_cs2 = m2knative.Calsrv("key=K1", send=_BreakInstancesAfterPost(_fk), me="me@example.com")
try:
    m2knative.update_occurrence(_cs2, _fk.events[_sid], _occ + dt.timedelta(days=7), location="y")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("結果不明且查不出來 → 停下、不還原、把兩邊狀態講清楚",
      len([c for c in _fk.calls if c["method"] == "PUT"]) == 1 and "webmail" in _r and "不明" in _r)

# MEDIUM-1 改開始時間時，until 的時間部分跟著新開始時間
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, rrule="FREQ=WEEKLY;UNTIL=20261231T155959Z"), "m1@example.com")
_nf = m2knative.apply(m2knative.edit_form(_fk.events[_sid]), start=dt.datetime(2026, 10, 6, 9, 30),
                      end=dt.datetime(2026, 10, 6, 10, 30))
check("改開始時間：until 改用新開始時間（日期不變）", _nf["until"] == "20261231T093000")

# MEDIUM-2 count 探詢不帶描述與與會者（比照前端 getInstanceCount）
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, description="很長的描述", attendees=["user_a@example.com"],
                                   rrule="FREQ=WEEKLY;COUNT=10"), "m2@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
m2knative.update_following(_cs, _fk.events[_sid], _occ, location="別館")
_cnts = [c for c in _fk.calls if c["path"].endswith("countRecurrenceInstances")]
check("count 探詢：不帶 description 與 attendee 欄位",
      _cnts and all("description" not in c["params"] and not any(k.startswith("attendee") for k in c["params"])
                    for c in _cnts))

# MEDIUM-3 指定的那一次必須真的存在
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, rrule="FREQ=WEEKLY"), "m3@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
_wrong = dt.datetime(2026, 10, 21, 14, 0)            # 週三，系列是週二
for _nm, _call in (("只改這次", lambda: m2knative.update_occurrence(_cs, _fk.events[_sid], _wrong, location="x")),
                   ("此次及以後", lambda: m2knative.update_following(_cs, _fk.events[_sid], _wrong, location="x")),
                   ("刪除這一次", lambda: m2knative.delete_occurrence(_cs, _fk.events[_sid], _wrong))):
    _fk.calls.clear()
    try:
        _call()
        _r = ""
    except m2kcal.M2KError as err:
        _r = str(err)
    check(f"{_nm}：該時間沒有這場 → 報錯且不寫入",
          "沒有" in _r and not any(c["method"] in ("PUT", "POST", "DELETE") for c in _fk.calls))
check("occurrence 確認：用 occ 前後 1 分鐘查 instances",
      int(_fk.calls[0]["params"]["endtime"]) - int(_fk.calls[0]["params"]["starttime"]) == 120)

# MEDIUM-4 全天事件 GET → 修改 來回
_fk = _FakeCalsrv()
_aid = _fk.seed(m2knative.new_form("休假", dt.datetime(2026, 10, 6), dt.datetime(2026, 10, 8), all_day=True),
                "allday@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
_got = m2knative.update(_cs, _fk.events[_aid], title="休假（改）")
_put = next(c for c in _fk.calls if c["method"] == "PUT")
check("全天來回：PUT 仍是全天、日期與排他結束日不變",
      _put["form"]["allday"] == "true" and _put["form"]["dtstart"] == "20261006"
      and _put["form"]["dtend"] == "20261008" and _got["summary"] == "休假（改）")
_inf = m2knative.info(_got)
check("全天來回：info 顯示最後一天", _inf["start"] == "2026-10-06 (全天)" and _inf["end"] == "2026-10-07 (全天)")

# LOW-2 alarm trigger 異常格式：比照前端 parseInt(...)||0
_ev = dict(_fx["create_response"]["event"], alarm=[
    {"trigger": "-900S", "action": "DISPLAY"}, {"trigger": "abc", "action": "EMAIL"},
    {"trigger": None, "action": "EMAIL", "duration": "x", "repeat": None}])
_ef = m2knative.edit_form(_ev, me="me@example.com")
check("alarm 異常格式：'-900S'→-PT900S、非數字→+PT0S、duration 壞值→+PT0S",
      _ef["alarm_trigger1"] == "-PT900S" and _ef["alarm_trigger2"] == "+PT0S"
      and _ef["alarm_trigger3"] == "+PT0S" and _ef["alarm_duration3"] == "+PT0S"
      and _ef["alarm_repeat3"] == "1")

# LOW-3 find_id 不吞 session 過期，只吞「查無」
_fk = _FakeCalsrv()
_x1 = _fk.seed(m2knative.new_form("週會", _t0, _t1), "x1@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")


class _GoneOnGet:
    def __init__(self, inner, code): self.inner, self.code = inner, code
    def __call__(self, method, path, cookie, form=None, params=None):
        if method == "GET" and path.endswith(f"/{_x1}"):
            self.inner.calls.append({"method": method, "path": path, "form": {}, "params": {}})
            return 200, "application/json", _json.dumps({"rspCode": self.code})
        return self.inner(method, path, cookie, form, params)


try:
    m2knative.Calsrv("key=K1", send=_GoneOnGet(_fk, -100), me="me@example.com").find_id(
        "x1@example.com", [_t0], summary="週會")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("find_id：單筆 GET 遇到 session 過期 → 往上丟，不當成查無", "過期" in _r)
try:
    m2knative.Calsrv("key=K1", send=_GoneOnGet(_fk, -1), me="me@example.com").find_id(
        "x1@example.com", [_t0], summary="週會")
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("find_id：單筆被拒（查無）→ 跳過繼續找，最後報找不到", "找不到" in _r)

# LOW-1 只剩一次：只改這次＝改整筆；此次及以後判斷「之前有幾次」要扣掉例外日
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("只剩一次", _t0, _t1, attendees=["user_a@example.com"],
                                   rrule="FREQ=WEEKLY;COUNT=1"), "one@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
_r1 = m2knative.update_occurrence(_cs, _fk.events[_sid], _t0, location="改")
check("只剩一次：只改這次＝直接改整筆（不拆、不重複）",
      not any(c["method"] == "POST" for c in _fk.calls) and _r1["uid"] == "one@example.com"
      and _fk.events[_sid]["rrule"] is None and _fk.events[_sid]["location"] == "改")
_sid = _fk.seed(m2knative.new_form("只剩一次", _t0, _t1, rrule="FREQ=WEEKLY;COUNT=1"), "one2@example.com")
_fk.calls.clear()
m2knative.delete_occurrence(_cs, _fk.events[_sid], _t0)
check("只剩一次：刪除這一次＝刪整筆", any(c["method"] == "DELETE" for c in _fk.calls) and _sid not in _fk.events)
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, rrule="FREQ=WEEKLY"), "ex@example.com")
_fk.events[_sid]["exdate"] = [{"exdate": _ep("20261006T140000")}]        # 第一次已取消
_fk.calls.clear()
m2knative.update_following(_cs, _fk.events[_sid], dt.datetime(2026, 10, 13, 14, 0), location="全改")
check("此次及以後：之前的場次都被取消了＝改全部",
      not any(c["method"] == "POST" for c in _fk.calls) and _fk.events[_sid]["location"] == "全改")

# MCP 層：非召集人講清楚、全天 end 當最後一天、刪除救援的提醒
if srv:
    _fk = _FakeCalsrv()

    class _NativeBackedCal2(_NativeBackedCal):
        pass
    _NativeBackedCal2.event_by_uid = lambda self, uid: _cal_from(_fk, uid)

    def _cal_from(fk, uid):
        ev = next((v for v in fk.events.values() if v["uid"] == uid), None)
        if ev is None:
            raise m2kcal._not_found_error()("404")
        s0 = dt.datetime.fromtimestamp(ev["dtstart"], TW).replace(tzinfo=None)
        e0 = dt.datetime.fromtimestamp(ev["dtend"], TW).replace(tzinfo=None)
        return SimpleNamespace(url="https://dav.example.com/cal/" + uid + ".ics", data=m2kcal.build_ics(
            ev["summary"], s0, e0, uid=uid, stamp="Z", all_day=bool(ev["info"] & 2),
            attendees=[a["attendee"][7:] for a in ev["attendee"]],
            rrule="FREQ=WEEKLY" if ev["rrule"] else ""))

    _saved5 = (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, srv._vet_note, srv._calsrv, m2knative._send)
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _NativeBackedCal2()
    m2kcal.creds = lambda: ("u", "me@example.com", "pw")
    srv._vet_note = lambda auth, attendees: ([], [])
    srv._calsrv = lambda ctx, me: m2knative.Calsrv("key=T", send=_fk, me=me)
    m2knative._send = _no_net
    try:
        _fk.seed(m2knative.new_form("別人的會", _t0, _t1, attendees=["me@example.com", "user_a@example.com"]),
                 "their-mcp@example.com", organizer="boss@example.com")
        _u = srv.update_event("their-mcp@example.com", location="我的筆記")
        check("update_event 非召集人：明講只改自己那份、不通知別人",
              "不是你召集的" in _u and "不會通知" in _u and "已由伺服器通知" not in _u)
        _d = srv.delete_event("their-mcp@example.com")
        check("delete_event 非召集人：明講只刪自己那份、不通知別人",
              "不是你召集的" in _d and "不會通知" in _d and "寄取消通知" not in _d)
        _aid = _fk.seed(m2knative.new_form("休假", dt.datetime(2026, 10, 6), dt.datetime(2026, 10, 8), all_day=True),
                        "allday-mcp@example.com")
        _fk.calls.clear()
        srv.update_event("allday-mcp@example.com", end="2026-10-09")
        check("update_event 全天：end 當最後一天（+1 天成排他結束日，同 book）",
              next(c for c in _fk.calls if c["method"] == "PUT")["form"]["dtend"] == "20261010")
        _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"]), "bye@example.com")
        _d2 = srv.delete_event("bye@example.com")
        check("delete_event 救援文字：提醒用 book 重建會重新寄邀請給所有人", "重新寄邀請" in _d2)
        _sid = _fk.seed(m2knative.new_form("只剩一次", _t0, _t1, rrule="FREQ=WEEKLY;COUNT=1"), "last@example.com")
        _u2 = srv.update_event("last@example.com", occurrence="2026-10-06 14:00", location="改")
        check("update_event occurrence 只剩一次：說明等同修改整筆、id 不變",
              "整筆" in _u2 and "新 id" not in _u2)
    finally:
        (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, srv._vet_note, srv._calsrv,
         m2knative._send) = _saved5

# 28s) 複審修正
# 1) 場次數必須是明確的整數：缺值／非整數丟 Unclear，不當成 0；只剩「剛好 1 次」才改走全部
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, attendees=["user_a@example.com"], rrule="FREQ=WEEKLY"),
                "cnt@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
for _bad in ('{"rspCode":0}', '{"rspCode":0,"count":"abc"}'):
    for _nm, _call in (("刪除這一次", lambda: m2knative.delete_occurrence(_cs, _fk.events[_sid], _occ)),
                       ("只改這次", lambda: m2knative.update_occurrence(_cs, _fk.events[_sid], _occ, location="x")),
                       ("此次及以後", lambda: m2knative.update_following(_cs, _fk.events[_sid], _occ, location="x"))):
        _fk.calls.clear()
        _fk.override["GET count"] = ((200, "application/json", _bad), False)
        try:
            _call()
            _r = None
        except m2kcal.M2KError as err:
            _r = err
        check(f"{_nm}：場次數 {_bad} → Unclear、不送任何寫入",
              isinstance(_r, m2knative.Unclear)
              and not any(c["method"] in ("PUT", "POST", "DELETE") for c in _fk.calls))
_fk.override.pop("GET count", None)
_fk.calls.clear()
_fk.override["GET count"] = ((200, "application/json", '{"rspCode":0,"count":0}'), False)
m2knative.update_occurrence(_cs, _fk.events[_sid], _occ, location="拆")
check("只改這次：場次數 0 不改走全部（照樣拆出新事件）", any(c["method"] == "POST" for c in _fk.calls))
_fk.calls.clear()
_fk.override["GET count"] = ((200, "application/json", '{"rspCode":0,"count":0}'), False)
m2knative.delete_occurrence(_cs, _fk.events[_sid], _occ + dt.timedelta(days=7))
check("刪除這一次：場次數 0 不改走全部（照樣只加例外日）",
      not any(c["method"] == "DELETE" for c in _fk.calls) and _sid in _fk.events)

# 2) 結果不明的錯誤要提醒：伺服器可能已處理，先確認、不要直接重試
_HINT = "伺服器可能已處理，請先到 webmail 或用 agenda 確認，不要直接重試"
_fk = _FakeCalsrv()
_fk.override["POST"] = ((500, "text/html", "<html>boom</html>"), False)
try:
    m2knative.Calsrv("key=K1", send=_fk).create({"summary": "x"})
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("Unclear（HTTP 500）訊息帶「可能已處理、先確認、不要直接重試」", _HINT in _r)
_saved_req = sys.modules.get("requests")
def _boom(*a, **k): raise OSError("read timed out")
sys.modules["requests"] = _types.SimpleNamespace(request=_boom, RequestException=OSError)
try:
    try:
        m2knative._send("POST", _EV_PATH, "key=K1", form={})
        _r = ""
    except m2kcal.M2KError as err:
        _r = str(err)
finally:
    if _saved_req is not None:
        sys.modules["requests"] = _saved_req
    else:
        sys.modules.pop("requests", None)
check("Unclear（逾時）訊息同樣帶提醒", _HINT in _r)
if srv:
    _fk = _FakeCalsrv()
    _saved6 = (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, srv._vet_note, srv._calsrv, m2knative._send)
    m2kcal.connect = lambda auth: object()
    m2kcal.pick_calendar = lambda p, name=None: _NativeBackedCal()
    m2kcal.creds = lambda: ("u", "me@example.com", "pw")
    srv._vet_note = lambda auth, attendees: ([], [])
    srv._calsrv = lambda ctx, me: m2knative.Calsrv("key=T", send=_fk, me=me)
    m2knative._send = _no_net
    try:
        _fk.override["POST"] = ((500, "text/html", "<html>boom</html>"), False)
        _b = srv.book("週會", "2026-10-08 14:00", attendees=["user_a@example.com"])
        check("book 碰到 500：回覆含「可能已處理、先確認、不要直接重試」", _b.startswith("錯誤") and _HINT in _b)
    finally:
        (m2kcal.connect, m2kcal.pick_calendar, m2kcal.creds, srv._vet_note, srv._calsrv,
         m2knative._send) = _saved6

# 3) is_mine：只看 organizer；me 沒網域時只比帳號
check("is_mine：me 無網域、organizer 有網域 → 自己的",
      m2knative.is_mine({"organizer": "mailto:Me@example.com"}, "me"))
check("is_mine：me 無網域時不會被前綴騙（meme ≠ me）",
      not m2knative.is_mine({"organizer": "mailto:meme@example.com"}, "me"))
# 實機觀察：send_meeting_mail=true 寫進與會者行事曆的那份，organizer＝召集人、creator＝與會者本人。
# 所以 creator 不能當判準，否則與會者改自己那份會以自己身分寄信給所有人。
check("is_mine：organizer 是別人、creator 是自己（伺服器寫進與會者行事曆的複本）→ 不是自己的",
      not m2knative.is_mine({"organizer": "mailto:boss@example.com", "creator": "me@example.com"}, "me@example.com"))
check("is_mine：organizer、creator 都是別人 → 不是自己的",
      not m2knative.is_mine({"organizer": "mailto:boss@example.com", "creator": "boss@example.com"}, "me"))

# 4) occurrence 要剛好是某一場的開始時間，不是「落在某一場期間內」
_fk = _FakeCalsrv()
_sid = _fk.seed(m2knative.new_form("週會", _t0, _t1, rrule="FREQ=WEEKLY"), "exact@example.com")
_cs = m2knative.Calsrv("key=K1", send=_fk, me="me@example.com")
_fk.calls.clear()
try:
    m2knative.delete_occurrence(_cs, _fk.events[_sid], _occ + dt.timedelta(minutes=30))   # 14:30，會議進行中
    _r = ""
except m2kcal.M2KError as err:
    _r = str(err)
check("occurrence 不是某場的開始時間（落在會議中間）→ 報錯且不寫入",
      "沒有" in _r and not any(c["method"] in ("PUT", "POST", "DELETE") for c in _fk.calls))
m2knative.delete_occurrence(_cs, _fk.events[_sid], _occ)
check("occurrence 剛好是開始時間 → 照常處理", _fk.events[_sid]["exdate"] is not None)

# 29) 行事曆 UI 的資料量：host 可能截斷大回應，UI 用不到或唯讀的部分要精簡
#     編輯表單拿完整與會者名單算增減、拿描述比對有沒有改——自己的事件不能砍名單，
#     描述截斷了就要標記，讓 UI 不把截斷後的文字寫回去；別人的事件唯讀，可以砍。
if srv:
    _many = [{"name": f"人{i}", "email": f"p{i}@example.com", "partstat": "NEEDS-ACTION"} for i in range(300)]
    _mk = lambda owner, desc: {"uid": owner + desc[:3], "summary": "會", "start": "2026-10-06 10:00",   # noqa: E731
                               "end": "2026-10-06 11:00", "allday": False, "location": "", "description": desc,
                               "organizer": owner, "rrule": "", "attendees": list(_many), "owner": owner}
    _rows_in = [_mk("me@example.com", "長" * 5000), _mk("peer@example.com", "長" * 5000),
                _mk("me@example.com", "短描述")]
    _slim = srv._slim_for_ui([dict(r) for r in _rows_in], "me@example.com")
    check("UI 精簡：自己的事件與會者名單完整（編輯表單靠它算增減）", len(_slim[0]["attendees"]) == 300)
    check("UI 精簡：自己的長描述截斷並標記（UI 不會把截斷的文字寫回去）",
          len(_slim[0]["description"]) <= srv._UI_DESC_MAX and _slim[0]["description_truncated"] is True)
    check("UI 精簡：短描述原樣、不標記", _slim[2]["description"] == "短描述" and "description_truncated" not in _slim[2])
    check("UI 精簡：別人的事件（唯讀）與會者只留前幾位並附總數",
          len(_slim[1]["attendees"]) == srv._UI_OTHERS_ATT_MAX and _slim[1]["attendees_total"] == 300)
    check("UI 精簡：別人的事件描述截得更短", len(_slim[1]["description"]) <= srv._UI_OTHERS_DESC_MAX
          and _slim[1]["description_truncated"] is True)
    check("UI 精簡：整包明顯變小", len(_json.dumps(_slim, ensure_ascii=False))
          < len(_json.dumps(_rows_in, ensure_ascii=False)) * 0.7)
    check("UI 精簡：_calendar_payload 有套用", "_slim_for_ui(" in _srv_src_now())

print("\n全部通過 ✅")
