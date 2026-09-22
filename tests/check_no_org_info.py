#!/usr/bin/env python3
"""擋下把公司內部資訊寫進版控的改動。

執行:  python3 tests/check_no_org_info.py     （CI 與本地都跑這支）
回傳:  0＝乾淨、1＝有命中（CI 會因此 fail）

**在 CI 上不印出命中的實際內容**：這個 repo 與它的 Actions log 都是公開的，
把抓到的信箱或部門代碼印在 log 裡等於換個地方洩漏一次，而且 log 不會隨著
commit 被修掉而消失。CI 只說「哪個檔案、第幾行、哪一類」，足以定位；
要看實際內容請在本機跑同一支。設 CHECK_ORG_SHOW=1 可強制顯示。

為什麼需要：這個 repo 是公開的，而工具本身是對公司 webmail 寫的，很容易在
註解、測試資料、範例裡不小心帶進真實信箱、部門代碼或組織樹路徑。靠人記得不可靠
——之前就發生過清乾淨後又在 docstring 寫回真實路徑。

**這支擋得住已知樣式，不是萬能**：部門代碼無法窮舉，新的代碼要自己加進
DEPT_CODES。它的價值在於把「最常見、最結構化」的洩漏擋在 commit 之外。

必須保留、不能誤擋的：
  mail.gss.com.tw  —— userscript 的 @match 與 CalDAV base URL，拿掉工具就廢了
  gss.m2k.*        —— userscript 的 @namespace，改了會被當成另一支腳本
  "gss.com.tw"     —— M2K_DOMAIN 的預設值（登入表單補網域用）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 先把合法用途遮掉，再套敏感樣式，才不會被 mail.gss.com.tw 之類誤觸
ALLOW = [
    r"mail\.gss\.com\.tw",
    r"gss\.m2k\.[A-Za-z0-9_-]+",
    r'"gss\.com\.tw"',
    r"M2K_DOMAIN",
]

# 已知的部門代碼。不完備——新增部門時要自己補，但常見的擋得住。
DEPT_CODES = [
    "CSBDBG", "CSBU", "CSMD", "CSIPD", "CSPASD", "BDBU", "BDPDD", "BDPSD",
    "BDTSD", "BDDSD", "CBDBU", "ISSDBU", "ODSMDBU", "SSDBU", "SDOBG", "PIBG",
    "EASG", "CPOD", "CEOO", "FABA", "OQMO", "BOD_BMD",
]

RULES = [
    ("公司信箱", re.compile(r"[A-Za-z0-9._%+-]+@gss\.com\.tw", re.I),
     "真實信箱不進版控；測試資料請用 @example.com"),
    ("通訊錄目錄", re.compile(r"\bGSS_(?:EMP|PT|ALL)\b", re.I),
     "組織樹目錄名；說明文字請寫成 /ROOT/BU/DEPT 這種泛稱"),
    ("通訊錄 ID", re.compile(r"\bPA\.\d{3}\b"),
     "通訊錄 abid；測試請用 BOOK1 之類的假值"),
    ("內部主機", re.compile(r"\b(?:git|yourls)\.gss\.com\.tw\b", re.I),
     "內部服務主機名"),
    ("部門代碼", re.compile(r"\b(?:" + "|".join(DEPT_CODES) + r")\b", re.I),
     "實際部門代碼；測試請用 UNIT1 / ENG_A 這種虛構名"),
    # 散文裡的公司縮寫（「GSS 的表單系統」「GSS 在台灣」這種）。結構化樣式抓不到，
    # 但它同樣是公司識別資訊；合法用途已在 ALLOW 先遮掉。
    ("公司縮寫", re.compile(r"\bGSS\b", re.I),
     "散文裡的公司名；描述請寫成「公司」「站台」或直接省略"),
]

SKIP_DIRS = {".git", "node_modules", ".venv", ".omc", "dist"}
SKIP_FILES = {os.path.relpath(os.path.abspath(__file__), ROOT)}   # 本檔自己就含樣式
TEXT_EXT = {".py", ".js", ".mjs", ".cjs", ".json", ".md", ".txt", ".yml", ".yaml",
            ".html", ".css", ".ts", ".sh", ".example", ".toml", ".cfg", ""}


def tracked_files():
    """只看版控中的檔案——本機的 .env 之類本來就不該被掃，也不該被讀。"""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    for path in out.decode("utf-8").split("\0"):
        if not path or path in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS for part in path.split("/")):
            continue
        if os.path.splitext(path)[1].lower() not in TEXT_EXT:
            continue
        yield path


def mask_allowed(text):
    """把合法用途換成等長佔位，位移不變，行號才不會跑掉。"""
    for pat in ALLOW:
        text = re.sub(pat, lambda m: "\0" * len(m.group(0)), text, flags=re.I)
    return text


def redacting():
    """CI 環境預設遮蔽。GitHub Actions 會設 CI=true。"""
    if os.environ.get("CHECK_ORG_SHOW") == "1":
        return False
    return os.environ.get("CI", "").lower() == "true"


def main():
    hits = []
    for path in tracked_files():
        full = os.path.join(ROOT, path)
        try:
            raw = open(full, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        masked = mask_allowed(raw)
        for lineno, line in enumerate(masked.splitlines(), 1):
            for label, rx, why in RULES:
                m = rx.search(line)
                if m:
                    hits.append((path, lineno, label, m.group(0), why))

    if not hits:
        print("✓ 版控檔案中未發現公司內部資訊")
        return 0

    hide = redacting()
    print(f"✗ 發現 {len(hits)} 處公司內部資訊，請改掉再提交：\n")
    for path, lineno, label, found, why in hits:
        shown = "（內容已遮蔽）" if hide else found
        print(f"  {path}:{lineno}  [{label}] {shown}")
        print(f"      {why}")
    if hide:
        print("\n本紀錄是公開的，命中內容不印出來。"
              "在本機跑 python3 tests/check_no_org_info.py 可看到實際內容。")
    print("\n（若確定是誤判，把合法用途加進本檔的 ALLOW）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
