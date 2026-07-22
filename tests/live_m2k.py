#!/usr/bin/env python3
"""實機整合測試 — 用真實憑證對 Mail2000 CalDAV 驗證新功能。

需要環境變數（或專案根目錄 .env）：M2K_USER、M2K_PASS（應用程式專用密碼）。
執行:  python3 tests/live_m2k.py
會在你的主行事曆建立帶「[live-test]」前綴的測試事件，結束時全部清除；
free-busy 探測與 IMAP 掃描為唯讀。任何一步失敗不會中斷後續項目。
"""
import datetime as dt
import os
import re
import sys
import traceback
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import m2kcal
import m2k_mcp_server as srv

m2kcal.load_dotenv()
if not (os.environ.get("M2K_USER") and os.environ.get("M2K_PASS")):
    sys.exit("缺憑證：請先設定 M2K_USER / M2K_PASS（或放到專案根目錄 .env）。")

PFX = "[live-test]"
_created: list[str] = []   # 建立過的 uid，最後清理
_results: list[tuple[str, bool, str]] = []


def step(name):
    def deco(fn):
        def run():
            try:
                msg = fn() or ""
                _results.append((name, True, msg))
                print(f"PASS {name}" + (f" — {msg}" if msg else ""))
            except Exception as e:
                _results.append((name, False, str(e)))
                print(f"FAIL {name} — {e}")
                traceback.print_exc(limit=2)
        return run
    return deco


def _tomorrow(h=0, m=0):
    d = dt.date.today() + dt.timedelta(days=1)
    return f"{d:%Y-%m-%d}" + (f" {h:02d}:{m:02d}" if h or m else "")


def _book_uid(out):
    """book() 回覆文字沒有直接列 uid，改由伺服器搜標題拿。"""
    assert out.startswith("已建立並驗證"), out
    return out


@step("多行 description（415 迴歸驗證）")
def t_multiline_desc():
    out = srv.book(f"{PFX} 多行描述", _tomorrow(20, 0), _tomorrow(20, 30),
                   description="第一行\n第二行\n含逗號,與分號;")
    _book_uid(out)
    ev = _find(f"{PFX} 多行描述")
    _created.append(_uid(ev))
    det = m2kcal.render_detail(ev)
    assert "第一行" in det and "第二行" in det, det
    return "描述換行/跳脫在伺服器端往返無損"


@step("全天事件（all_day）")
def t_all_day():
    out = srv.book(f"{PFX} 全天", _tomorrow(), all_day=True)
    _book_uid(out)
    ev = _find(f"{PFX} 全天")
    _created.append(_uid(ev))
    assert "VALUE=DATE" in ev.data.replace("\r\n ", ""), ev.data[:400]
    return "伺服器接受 VALUE=DATE 並存為全天"


@step("重複 BYDAY+INTERVAL+UNTIL")
def t_rrule():
    until = (dt.date.today() + dt.timedelta(days=60)).strftime("%Y-%m-%d")
    out = srv.book(f"{PFX} 隔週二四", _tomorrow(21, 0), _tomorrow(21, 30),
                   repeat="weekly", repeat_byday=["TU", "TH"],
                   repeat_interval=2, repeat_until=until)
    _book_uid(out)
    ev = _find(f"{PFX} 隔週二四")
    _created.append(_uid(ev))
    ics = ev.data.replace("\r\n ", "")
    assert "FREQ=WEEKLY" in ics and "BYDAY=TU,TH" in ics and "INTERVAL=2" in ics, ics[:600]
    return "伺服器保留完整 RRULE"


@step("update_event 提醒 加→改→刪")
def t_reminder():
    out = srv.book(f"{PFX} 提醒", _tomorrow(22, 0), _tomorrow(22, 30))
    _book_uid(out)
    ev = _find(f"{PFX} 提醒")
    uid = _uid(ev)
    _created.append(uid)
    assert "提醒: 開始前 30 分鐘" in srv.update_event(uid, reminder_minutes=30)
    assert "-PT30M" in _find(f"{PFX} 提醒").data.replace("\r\n ", "")
    assert "開始前 10 分鐘" in srv.update_event(uid, reminder_minutes=10)
    assert "已移除" in srv.update_event(uid, reminder_minutes=0)
    assert "VALARM" not in _find(f"{PFX} 提醒").data
    return "VALARM 增改刪都已在伺服器端生效"


@step("get_event 完整詳情")
def t_get_event():
    ev = _find(f"{PFX} 多行描述")
    out = srv.get_event(_uid(ev))
    assert "描述（外部輸入內容" in out and "id:" in out, out
    return "回傳全文詳情"


@step("指定行事曆（calendar 參數）")
def t_calendar_param():
    p = m2kcal.connect(m2kcal.creds())
    names = [str(m2kcal.cal_name(c)) for c in p.calendars()]
    assert names, "沒有任何行事曆"
    out = srv.agenda(days=1, calendar=names[0])
    assert not out.startswith("錯誤"), out
    bad = srv.agenda(days=1, calendar="不存在的行事曆名稱")
    assert bad.startswith("錯誤：找不到名為"), bad
    return f"可用行事曆: {', '.join(names)}；名稱錯誤時明確報錯"


@step("多人 free-busy（RFC 6638 outbox 探測）")
def t_freebusy_others():
    me = m2kcal.creds()[1]
    out = srv.find_free_slots(duration_minutes=30, days=3, attendees=[me])
    assert not out.startswith("錯誤：free-busy 查詢失敗"), out
    if out.startswith("錯誤"):
        return f"伺服器不支援（如預期優雅回報）: {out.splitlines()[0]}"
    return "伺服器支援排程 free-busy！共同空檔查詢可用"


@step("拆分系列 from_occurrence（改此次及以後）")
def t_split_series():
    out = srv.book(f"{PFX} 週會拆分", _tomorrow(19, 0), _tomorrow(19, 30),
                   repeat="weekly")
    _book_uid(out)
    ev = _find(f"{PFX} 週會拆分")
    uid = _uid(ev)
    _created.append(uid)
    nxt = (dt.date.today() + dt.timedelta(days=8)).strftime("%Y-%m-%d") + " 19:00"
    out2 = srv.update_event(uid, title=f"{PFX} 週會拆分-新", from_occurrence=nxt)
    assert "新 id:" in out2, out2
    new_uid = re.search(r"新 id: ([0-9a-f-]{36})", out2).group(1)
    _created.append(new_uid)
    old_ics = _find(f"{PFX} 週會拆分").data.replace("\r\n ", "")
    assert "UNTIL=" in old_ics, old_ics[:400]
    new_ics = _find(f"{PFX} 週會拆分-新").data.replace("\r\n ", "")
    assert "FREQ=WEEKLY" in new_ics and "UNTIL" not in new_ics, new_ics[:400]
    return "原系列已截斷、新系列沿用規則且套用變更"


@step("IMAP 掃描會議邀請")
def t_invitations():
    out = srv.list_invitations(days=14)
    assert not out.startswith("錯誤：IMAP"), out
    return out.splitlines()[0]


def _find(title):
    """依標題找事件。重要：Mail2000 對不帶 expand 的 summary 過濾會整個忽略、
    回傳區間內所有事件（已實測），所以務必在客戶端做精確比對——
    直接信 hits[0] 曾誤抓（並誤刪）使用者的真實事件。"""
    cal = m2kcal.pick_calendar(m2kcal.connect(m2kcal.creds()))
    hits = cal.search(event=True,
                      start=dt.datetime.now() - dt.timedelta(days=1),
                      end=dt.datetime.now() + dt.timedelta(days=90))
    match = [h for h in hits
             if str(h.icalendar_component.get("summary", "")) == title]
    assert match, f"伺服器上找不到 {title}"
    return match[0]


def _uid(ev):
    return str(ev.icalendar_component.get("uid"))


def _cleanup():
    cal = m2kcal.pick_calendar(m2kcal.connect(m2kcal.creds()))
    for uid in dict.fromkeys(_created):
        try:
            ev = m2kcal.find_event_by_uid(cal, uid)
            summ = str(ev.icalendar_component.get("summary", ""))
            if not summ.startswith(PFX):  # 雙重防護：只刪測試事件
                print(f"清理 {uid}: 跳過（標題「{summ}」沒有 {PFX} 前綴，拒刪）")
                continue
            ev.delete()
            print(f"清理 {uid}: OK")
        except Exception as e:
            print(f"清理 {uid}: 失敗（請手動刪除）: {e}")
    # 依標題前綴掃蕩：Mail2000 的 REPORT 結果可能延遲/不完整（已實測——
    # 曾有事件在建立後幾分鐘內掃不到、隔天才浮現），連續兩輪乾淨才收工。
    now = dt.datetime.now()
    clean_rounds = 0
    for _ in range(4):
        hits = [ev for ev in cal.search(start=now - dt.timedelta(days=400),
                                        end=now + dt.timedelta(days=400),
                                        event=True)
                if str(ev.icalendar_component.get("summary", "")).startswith(PFX)]
        if not hits:
            clean_rounds += 1
            if clean_rounds >= 2:
                break
            continue
        clean_rounds = 0
        for ev in hits:
            summ = str(ev.icalendar_component.get("summary", ""))
            ev.delete()
            print(f"掃蕩刪除: {summ}")


if __name__ == "__main__":
    steps = [t_multiline_desc, t_all_day, t_rrule, t_reminder,
             t_get_event, t_calendar_param, t_freebusy_others,
             t_split_series, t_invitations]
    try:
        for fn in steps:
            fn()
    finally:
        _cleanup()
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} 通過"
          + (f"；失敗: {', '.join(failed)}" if failed else " ✅"))
    sys.exit(1 if failed else 0)
