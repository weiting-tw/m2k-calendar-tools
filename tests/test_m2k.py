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
                        organizer="you@example.com", uid="U2", stamp="Z")
check("與會者1寫入", "ATTENDEE" in ics2 and "user_a@example.com" in ics2)
check("與會者2寫入", "user_b@example.com" in ics2)
check("ORGANIZER 寫入", "ORGANIZER:mailto:you@example.com" in ics2)
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

# 3d) _rrule_text: 週期文字化（吃 dict-like，離線可測）
check("rrule 每週一三", m2kcal._rrule_text({"rrule": {"FREQ": ["WEEKLY"], "BYDAY": ["MO", "WE"]}}) == "每週 一三")
check("rrule 無值空字串", m2kcal._rrule_text({}) == "")

# 4) MemberParser: 模擬 adb2 通訊錄列表 HTML（結構仿實際:每列 td 姓名 + td 信箱）
mock_html = """
<table>
<tr><td>類別</td><td>暱稱</td><td>信箱</td><td>電話</td></tr>
<tr><td><input type=checkbox></td><td>User A (測試甲)</td><td>user_a@example.com</td><td>10379</td></tr>
<tr><td><input type=checkbox></td><td>User B (測試乙)</td><td>user_b@example.com</td><td></td></tr>
<tr><td><input type=checkbox></td><td>User C (測試丙)</td><td>user_c@example.com</td><td>10124</td></tr>
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

print("\n全部通過 ✅")
