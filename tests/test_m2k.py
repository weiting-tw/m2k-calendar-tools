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
import m2kgroup


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name


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
check("與會者1寫入", "ATTENDEE" in ics2 and "user_a@example.com" in ics2)
check("與會者2寫入", "user_b@example.com" in ics2)
check("ORGANIZER 寫入", "ORGANIZER:mailto:owner@example.com" in ics2)
check("與會者數=2", ics2.count("ATTENDEE") == 2)

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
u, p = m2kcal.parse_basic_auth("Basic " + base64.b64encode(b"a@gss.com.tw:s3cret").decode())
check("Basic 解析 user", u == "a@gss.com.tw")
check("Basic 解析 pwd", p == "s3cret")
u2, p2 = m2kcal.parse_basic_auth("basic " + base64.b64encode("a@gss.com.tw:p:w:d".encode()).decode())
check("小寫 basic 可解析、密碼含冒號只切第一個", u2 == "a@gss.com.tw" and p2 == "p:w:d")
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

# 4) MemberParser: 模擬 adb2 通訊錄列表 HTML（結構仿實際:每列 td 姓名 + td 信箱）
mock_html = """
<table>
<tr><td>類別</td><td>暱稱</td><td>信箱</td><td>電話</td></tr>
<tr><td><input type=checkbox></td><td>User A (測試甲)</td><td>user_a@example.com</td><td>10001</td></tr>
<tr><td><input type=checkbox></td><td>User B (測試乙)</td><td>user_b@example.com</td><td></td></tr>
<tr><td><input type=checkbox></td><td>User C (測試丙)</td><td>user_c@example.com</td><td>10003</td></tr>
</table>
"""
p = m2kgroup.MemberParser()
p.feed(mock_html)
emails = [e for _, e in p.rows]
names = [n for n, _ in p.rows]
check("解析出 3 位成員", len(p.rows) == 3)
check("email 正確", emails == ["user_a@example.com", "user_b@example.com", "user_c@example.com"])
check("姓名帶出", "User A (測試甲)" in names)
check("表頭列不被當成員", "信箱" not in "".join(emails))

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

print("\n全部通過 ✅")
