#!/usr/bin/env python3
"""
m2kgroup — Mail2000 (m2k) 通訊錄部門展開工具
把公司通訊錄的部門展開成成員 email，供 book 時逐一帶入與會者。

== 授權：只需要 Cookie ==
GSS Mail2000 登入走 SAML SSO（/cgi-bin/saml_login），沒有帳密表單，
所以獨立程式「無法自己登入」——必須從已登入的瀏覽器複製 Cookie：

    export M2K_COOKIE="<整串 Cookie>"      # DevTools → Network → 任一請求 → Cookie
    python3 src/m2kgroup.py expand --dirid <部門路徑>

實測（瀏覽器）：adb2main_mds 只靠 Cookie 就會回資料，**不需要** m / ssnid。
舊版說明要求那兩個短效 token，是誤解；仍保留為選填，環境有需要時才帶。

== 已驗證的 adb2 規格 ==
  端點   : GET /cgi-bin/adb2main_mds
  參數   : command=list, workingabid=<通訊錄id>, workingdirid=<部門路徑>, pageno=<1起>
  分頁   : 每頁固定 25 筆；未滿 25 即最後一頁
  資料   : 列表裡的 <input name="Entries">，靠 adbetype 分辨
             adbetype="D" → 子部門（value 是完整路徑、nick 是名稱）
             adbetype="C" → 人（email 屬性是信箱、nick 是「英文名 (中文名)」）
           所以一支請求同時拿到「這層的人」與「這層的子部門」，遞迴不必另外建樹。
  注意   : 部門**不在** adb2tree_mds 的樹節點裡（那支只回幾個頂層目錄），
           所以 --dirid 要給部門路徑；不給 --abid 時會自動從 adb2tree 取第一本公司通訊錄。

用法:
  python3 src/m2kgroup.py expand --dirid <部門路徑>                 # 含所有子部門（預設）
  python3 src/m2kgroup.py expand --dirid <部門路徑> --no-recursive  # 只要本層
  python3 src/m2kgroup.py expand --dirid <部門路徑> --as-attendees
  # 直接串進 book:
  python3 src/m2kcal.py book --title "部門會議" --start "2026-07-10 10:00" \\
      $(python3 src/m2kgroup.py expand --dirid <部門路徑> --as-attendees)
"""
import os
import sys
import re
import argparse
from html.parser import HTMLParser

def _requests():
    """延遲載入 requests。HTML 解析（MemberParser）是純函式，離線測試用不到它 ——
    頂層 import 會讓測試在沒裝 requests 的環境整批跑不起來。"""
    try:
        import requests
    except ImportError:
        sys.exit("需要 requests 套件，請先執行:  pip install requests")
    return requests

BASE = os.environ.get("M2K_BASE", "https://mail.gss.com.tw")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def session():
    s = _requests().Session()
    cookie = os.environ.get("M2K_COOKIE")
    if cookie:
        s.headers["Cookie"] = cookie
    return s


PAGE_SIZE = 25      # adb2main_mds 每頁固定筆數，未滿即最後一頁
MAX_PAGES = 40      # 單一部門的分頁上限
MAX_NODES = 300     # 遞迴節點上限


def fetch_page(s, abid, dirid, page):
    """抓某一頁的列表 HTML。page 從 1 起算。"""
    params = {
        "command": "list",
        "workingabid": abid,
        "workingdirid": dirid,
        "tofield": "widget",
        "pageno": page,
    }
    # 選填：某些環境若真的需要短效 token，帶了也無害
    for k, env in (("m", "M2K_M"), ("ssnid", "M2K_SSNID")):
        v = os.environ.get(env)
        if v:
            params[k] = v
    r = s.get(f"{BASE}/cgi-bin/adb2main_mds", params=params, timeout=20)
    r.raise_for_status()
    return r.text


class EntryParser(HTMLParser):
    """抓列表裡的 <input name="Entries">，靠屬性取值。

    比「掃 <td> 文字撈 email」可靠得多：拿得到 adbetype（分辨部門與人）、
    完整部門路徑、以及含中文名的 nick，也不會誤撈頁面裝飾裡的 email。
    """

    def __init__(self):
        super().__init__()
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        a = dict(attrs)
        if a.get("name") != "Entries":
            return
        self.rows.append({
            "value": (a.get("value") or "").strip(),
            "nick": (a.get("nick") or "").strip(),
            "email": (a.get("email") or "").strip().lower(),
            "type": (a.get("adbetype") or "").strip(),
        })


def parse_entries(html):
    """回 [{value, nick, email, type}]。"""
    p = EntryParser()
    p.feed(html)
    return p.rows


def default_abid(s):
    """沒給 --abid 時，從 adb2tree 取第一本「非空」的通訊錄（空的那本是個人通訊錄）。"""
    r = s.get(f"{BASE}/cgi-bin/adb2tree", params={"tofield": "widget"}, timeout=20)
    r.raise_for_status()
    m = re.search(r"do_switchto\(\s*['\"]([^'\"]+)['\"]", r.text)
    if not m:
        raise SystemExit("取不到通訊錄 id（Cookie 可能過期），請重新複製 M2K_COOKIE。")
    return m.group(1)


def fetch_node(s, abid, dirid, on_page=None):
    """一個部門的全部內容。回 (members: {email: nick}, subs: {path: nick})。"""
    members, subs = {}, {}
    bottomed = False
    for page in range(1, MAX_PAGES + 1):
        rows = parse_entries(fetch_page(s, abid, dirid, page))
        for r in rows:
            if r["type"] == "D":
                if r["value"]:
                    subs.setdefault(r["value"], r["nick"] or r["value"])
            elif r["email"]:
                members.setdefault(r["email"], r["nick"])
        if on_page:
            on_page(dirid, page, len(members))
        if len(rows) < PAGE_SIZE:
            bottomed = True
            break
    if not bottomed:
        sys.stderr.write(f"警告：{dirid} 讀滿 {MAX_PAGES} 頁仍未見底，成員可能不只 {len(members)} 位。\n")
    return members, subs


def expand(abid, dirid, recursive=True, on_page=None):
    """展開部門。recursive=True 時含所有子孫部門，跨部門去重。
    回 [(nick, email)]。"""
    s = session()
    if not abid:
        abid = default_abid(s)
    members, visited, queue = {}, set(), [dirid]
    while queue and len(visited) < MAX_NODES:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        ms, subs = fetch_node(s, abid, cur, on_page)
        for em, nick in ms.items():
            members.setdefault(em, nick)
        if recursive:
            queue.extend(d for d in subs if d not in visited)
    if queue:
        sys.stderr.write(f"警告：已達節點上限 {MAX_NODES}，還有 {len(queue)} 個子部門沒展開。\n")
    return [(nick, em) for em, nick in members.items()], len(visited)


def main():
    ap = argparse.ArgumentParser(description="Mail2000 通訊錄群組展開")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("expand", help="展開部門成員（預設含子部門）")
    pe.add_argument("--abid", default="", help="workingabid（通訊錄 id）；不給則自動取公司通訊錄")
    pe.add_argument("--dirid", required=True, help="workingdirid（部門路徑）")
    pe.add_argument("--no-recursive", action="store_true", help="只取本層，不展開子部門")
    pe.add_argument("--as-attendees", action="store_true",
                    help="輸出成 --attendee a@x --attendee b@x，可直接串進 m2kcal book")
    args = ap.parse_args()

    if not os.environ.get("M2K_COOKIE"):
        sys.stderr.write(
            "警告：未設定 M2K_COOKIE。因 SAML SSO 無法自行登入，\n"
            "請從已登入的瀏覽器複製整串 Cookie 到 M2K_COOKIE。\n")

    members, nodes = expand(args.abid, args.dirid, recursive=not args.no_recursive)
    if args.as_attendees:
        out = []
        for _, email in members:
            out.append("--attendee")
            out.append(email)
        print(" ".join(out))
    else:
        for name, email in members:
            print(f"{email}\t{name}")
        sys.stderr.write(f"\n共 {len(members)} 位成員（{nodes} 個部門）\n")


if __name__ == "__main__":
    main()
