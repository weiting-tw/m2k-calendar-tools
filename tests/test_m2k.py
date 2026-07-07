#!/usr/bin/env python3
"""
離線功能測試 — 不需帳密、不連伺服器，用假資料驗證核心邏輯。
執行:  python3 test_m2k.py
涵蓋: ICS 產生、時間解析、通訊錄群組成員解析。
"""
import datetime as dt
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

# 3) parse_when: 多種格式
check("解析 日期時間", m2kcal.parse_when("2026-07-10 14:00") == dt.datetime(2026, 7, 10, 14, 0))
check("解析 純日期", m2kcal.parse_when("2026-07-10") == dt.datetime(2026, 7, 10, 0, 0))
check("解析 T 格式", m2kcal.parse_when("2026-07-10T09:30") == dt.datetime(2026, 7, 10, 9, 30))

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
