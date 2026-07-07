#!/usr/bin/env python3
"""
m2kgroup — Mail2000 (m2k) 通訊錄群組展開工具
把公司通訊錄的「群組 / 部門」展開成成員 email，供 book 時逐一帶入與會者。

== 為什麼需要帶 session token（重要）==
GSS Mail2000 登入走 SAML SSO（/cgi-bin/saml_login），沒有帳密表單，
所以獨立程式「無法自己登入」。通訊錄模組(adb2)靠登入後的 session token 授權。
因此本工具有兩種用法：

  【A. 瀏覽器驅動（建議、最穩）】
     直接在已登入的瀏覽器上跑（例如透過 Claude in Chrome 自動化 / MCP），
     沿用現有 SAML session，不必處理 token。本檔的 parse_members() 可重用。

  【B. 終端機 CLI + 手動帶 token】
     從已登入的瀏覽器複製 session 參數，用環境變數餵給本工具：
       export M2K_BASE="https://mail.gss.com.tw"
       export M2K_M="<網址上的 m 參數>"          # 通訊錄分頁網址裡的 m=...
       export M2K_SSNID="<ssnid 參數>"
       export M2K_COOKIE="<整串 Cookie>"          # DevTools 複製
     注意：m/ssnid 為短效且各模組不同，過期就要重拿；這是 SAML 架構的先天限制。

== 已驗證的 adb2 規格 ==
  端點   : GET /cgi-bin/adb2main
  參數   : command=list, workingabid=<通訊錄id>, workingdirid=<部門/群組id>,
           tofield=widget, m=<token>, ssnid=<token>
  分頁   : 每頁 25 筆
  通訊錄 : GSS / ORG_ALL (公司)、ORG_DIR1 / ORG_DIR2 (部門樹)、個人通訊錄
  欄位   : 暱稱、姓氏、名字、信箱、電話；email 格式 英文名_姓@gss.com.tw
  (workingabid / workingdirid 可在通訊錄樹節點的 do_switchto(...) 參數取得)

用法:
  python3 m2kgroup.py expand --abid <ABID> --dirid <DIRID>
  python3 m2kgroup.py expand --abid <ABID> --dirid <DIRID> --as-attendees
  # 直接串進 book:
  python3 m2kcal.py book --title "部門會議" --start "2026-07-10 10:00" \
      $(python3 m2kgroup.py expand --abid <ABID> --dirid <DIRID> --as-attendees)
"""
import os
import sys
import re
import argparse
from html.parser import HTMLParser

try:
    import requests
except ImportError:
    sys.exit("需要 requests 套件，請先執行:  pip install requests")

BASE = os.environ.get("M2K_BASE", "https://mail.gss.com.tw")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def session():
    s = requests.Session()
    cookie = os.environ.get("M2K_COOKIE")
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def fetch_page(s, abid, dirid, page):
    """抓某一頁的成員 HTML。page 從 1 起算。"""
    params = {
        "command": "list",
        "workingabid": abid,
        "workingdirid": dirid,
        "tofield": "widget",
        "m": os.environ.get("M2K_M", ""),
        "ssnid": os.environ.get("M2K_SSNID", ""),
        "page": page,   # 若站台用不同分頁參數(如 row/pageno)，於此調整
    }
    r = s.get(f"{BASE}/cgi-bin/adb2main", params=params, timeout=20)
    r.raise_for_status()
    return r.text


class MemberParser(HTMLParser):
    """從通訊錄列表 HTML 逐列抽出 (姓名, email)。"""
    def __init__(self):
        super().__init__()
        self.rows = []
        self._cells = []
        self._buf = []
        self._in_td = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells = []
        elif tag == "td":
            self._in_td = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_td = False
            self._cells.append("".join(self._buf).strip())
        elif tag == "tr":
            text = " ".join(self._cells)
            em = EMAIL_RE.search(text)
            if em:
                email = em.group(0)
                # 取不含 @ 的最長欄位當姓名
                name = ""
                for c in self._cells:
                    if "@" not in c and c and len(c) > len(name):
                        name = c
                self.rows.append((name, email))

    def handle_data(self, data):
        if self._in_td:
            self._buf.append(data)


def expand(abid, dirid, max_pages=40):
    s = session()
    seen = {}
    for page in range(1, max_pages + 1):
        html = fetch_page(s, abid, dirid, page)
        p = MemberParser()
        p.feed(html)
        new = 0
        for name, email in p.rows:
            key = email.lower()
            if key not in seen:
                seen[key] = name
                new += 1
        # 沒有新成員就停(已到最後一頁)
        if new == 0:
            break
    return [(n, e) for e, n in seen.items()]


def main():
    ap = argparse.ArgumentParser(description="Mail2000 通訊錄群組展開")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("expand", help="展開群組/部門成員")
    pe.add_argument("--abid", required=True, help="workingabid（通訊錄 id）")
    pe.add_argument("--dirid", required=True, help="workingdirid（部門/群組 id）")
    pe.add_argument("--as-attendees", action="store_true",
                    help="輸出成 --attendee a@x --attendee b@x，可直接串進 m2kcal book")
    args = ap.parse_args()

    if not os.environ.get("M2K_M"):
        sys.stderr.write(
            "警告：未設定 M2K_M / M2K_SSNID / M2K_COOKIE。\n"
            "因 SAML SSO，需從已登入瀏覽器帶入 session；或改用瀏覽器驅動模式。\n")

    members = expand(args.abid, args.dirid)
    if args.as_attendees:
        out = []
        for _, email in members:
            out.append("--attendee")
            out.append(email)
        print(" ".join(out))
    else:
        for name, email in members:
            print(f"{email}\t{name}")
        sys.stderr.write(f"\n共 {len(members)} 位成員\n")


if __name__ == "__main__":
    main()
