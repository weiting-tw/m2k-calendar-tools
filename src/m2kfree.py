#!/usr/bin/env python3
"""共同空檔（Common Free Slot）——一群人在工作時段內都沒有忙碌時段、
且長度足夠的區間。

這個 module 的承諾是：**指得出誰擋住**。所以它不接受「一堆合併好的忙碌區間」，
只接受「每個人各自的忙碌」，並且自己也是其中一個人（用 email 當 key，沒有特殊值）。
先前把每個人的忙碌累加進同一份清單、再拿那份清單當自己的忙碌，導致「缺 N 位」
一律把自己算進去——那種混淆在這裡的型別上不可能發生。

忙碌從哪來不是這個 module 的知識：呼叫端傳入一個 busy_source，它負責去問
（自己的行事曆走 CalDAV、他人走排程端點或已分享的行事曆），也負責處理那些
來源自己的毛病（session 過期要重換、一次能查幾個人）。

    busy_source(people, start, end) -> ({email: [(s, e)]}, {email: 原因})

回傳一律是完整結果，不截斷、不篩選：「只顯示 8 筆」「缺 2 人以內」是呈現決定，
留給呼叫端。
"""
import datetime as dt

# unavailable 的原因。暫時用字串列舉——之後排程端點改成可判別的錯誤型別時
# 會換掉這裡（見架構審查的「錯誤模式沒有型別」）。
NO_ACCOUNT = "查無帳號"
LOOKUP_FAILED = "查詢失敗"
NOT_SHARED = "未分享"
OVER_LIMIT = "超過人數上限"


def _work_windows(start, end, day_start, day_end, include_weekends):
    """把 [start, end) 切成每天的工作時段。沒有任何忙碌時的可用骨架。"""
    def hm(s):
        h, m = s.split(":")
        return int(h), int(m)
    sh, sm = hm(day_start)
    eh, em = hm(day_end)
    out = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        if include_weekends or day.weekday() < 5:
            ws = max(day.replace(hour=sh, minute=sm), start)
            we = min(day.replace(hour=eh, minute=em), end)
            if we > ws:
                out.append((ws, we))
        day += dt.timedelta(days=1)
    return out


def _slots_with_missing(busy_by_person, start, end, duration_min,
                        day_start, day_end, include_weekends):
    """回 [(slot_start, slot_end, missing)]，missing 是該時段忙的人（已排序）。
    依「缺的人數少 → 時間早」排序。"""
    people = list(busy_by_person or {})
    out = []
    for ws, we in _work_windows(start, end, day_start, day_end, include_weekends):
        # 用所有人的忙碌邊界把工作時段切成小段，每段的「誰忙」是固定的
        edges = {ws, we}
        for who in people:
            for b0, b1 in busy_by_person[who]:
                if b1 > ws and b0 < we:
                    edges.add(max(b0, ws))
                    edges.add(min(b1, we))
        marks = sorted(edges)
        segs = []
        for i in range(len(marks) - 1):
            a, b = marks[i], marks[i + 1]
            if b <= a:
                continue
            segs.append([a, b, sorted(
                who for who in people
                if any(b0 < b and b1 > a for b0, b1 in busy_by_person[who]))])
        # 相鄰且缺同一群人的段落接回去，否則會被切碎而湊不出時長
        merged = []
        for seg in segs:
            if merged and merged[-1][2] == seg[2] and merged[-1][1] == seg[0]:
                merged[-1][1] = seg[1]
            else:
                merged.append(seg)
        for a, b, missing in merged:
            if (b - a).total_seconds() >= duration_min * 60:
                out.append((a, b, missing))
    out.sort(key=lambda x: (len(x[2]), x[0]))
    return out


def common_free_slots(people, start, end, *, busy_source,
                      duration_min=60, day_start="09:00", day_end="18:00",
                      include_weekends=False):
    """求這群人的共同空檔。people 含使用者自己（用 email，不用特殊值）。

    busy_source 是必填的：不給預設值是刻意的——有預設就沒人會注意到這個 seam，
    久了又會變成測試去替換模組全域。

    回 {"slots": [(start, end, missing)], "unavailable": {email: 原因}}
      slots     ：missing 為空 = 全員都有空；依缺的人數少、時間早排序；不截斷
      unavailable：查不到的人與原因。查不到某人不算整個查詢失敗，結果照給
    """
    wanted, seen = [], set()
    for p in people or []:
        em = (p or "").strip().lower()
        if em and em not in seen:
            seen.add(em)
            wanted.append(em)
    if not wanted:
        return {"slots": [], "unavailable": {}}

    busy_by_person, unavailable = busy_source(wanted, start, end)
    busy_by_person = {k: list(v) for k, v in (busy_by_person or {}).items()}
    return {
        "slots": _slots_with_missing(busy_by_person, start, end, duration_min,
                                     day_start, day_end, include_weekends),
        "unavailable": dict(unavailable or {}),
    }


# ── 正式的 busy_source ───────────────────────────────────────────────
# 三種來源長得不一樣，但對 common_free_slots 來說都只是「某人的忙碌時段」：
#   自己   → CalDAV free-busy
#   他人   → webmail 排程端點（要 cookie；cookie 會過期，要重換重試）
#   沒 cookie 時的退路 → 對方已分享給你的行事曆
# 「cookie 會過期」是排程端點這個來源的毛病，不該讓 common_free_slots 知道，
# 所以吸收在這裡。去重與人數上限同理。


def live_busy_source(auth, *, cookie="", refresh_cookie=None, calendar=None):
    """組出正式的 busy_source。

    auth           ：CalDAV 憑證（自己的忙碌要用）
    cookie         ：webmail cookie；沒有就走「已分享的行事曆」那條退路
    refresh_cookie ：cookie 過期時用來重換的 callable，回新 cookie 字串
    calendar       ：指定要看自己的哪個日曆（預設主日曆）
    """
    import m2kcal

    def source(people, start, end):
        me = (auth[1] or "").strip().lower()
        busy_by, unavailable = {}, {}
        principal = m2kcal.connect(auth)

        # 自己：CalDAV free-busy
        if me in people:
            try:
                cal = m2kcal.pick_calendar(principal, calendar) if calendar \
                    else m2kcal.pick_calendar(principal)
                fb = cal.freebusy_request(start, end)
                busy_by[me] = m2kcal.parse_freebusy(
                    fb.data if isinstance(getattr(fb, "data", None), str) else str(fb.data))
            except Exception:
                unavailable[me] = LOOKUP_FAILED

        others = [p for p in people if p != me]
        if not others:
            return busy_by, unavailable

        if cookie:
            shown, dropped = others[:m2kcal.MAX_SCHED_PEOPLE], others[m2kcal.MAX_SCHED_PEOPLE:]
            for em in dropped:
                unavailable[em] = OVER_LIMIT
            cur, relogged = cookie, False
            for em in shown:
                try:
                    busy_by[em] = m2kcal.busy_periods(
                        m2kcal.fetch_schedule(cur, em, start, end))
                except m2kcal.M2KError as err:
                    # session 過期且拿得到新的 → 重換一次再試這人
                    if refresh_cookie and not relogged and "過期" in str(err):
                        relogged = True
                        fresh = (refresh_cookie() or "").strip()
                        if fresh and fresh != cur:
                            cur = fresh
                            try:
                                busy_by[em] = m2kcal.busy_periods(
                                    m2kcal.fetch_schedule(cur, em, start, end))
                                continue
                            except m2kcal.M2KError as err2:
                                err = err2
                    unavailable[em] = (NO_ACCOUNT if "查無此帳號" in str(err)
                                       else LOOKUP_FAILED)
                except Exception:
                    unavailable[em] = LOOKUP_FAILED
        else:
            shared, missing = m2kcal.busy_from_shared(principal, others, start, end)
            busy_by.update(shared)
            for em in missing:
                unavailable[em] = NOT_SHARED
        return busy_by, unavailable

    return source
