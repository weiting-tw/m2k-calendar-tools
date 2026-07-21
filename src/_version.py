"""單一版本來源：image tag、登入頁 footer、README 都引用這裡。"""
import os

__version__ = "1.6.3"

# 原始碼連結（登入頁 footer 顯示）。可用 M2K_SOURCE_URL 覆寫。
SOURCE_URL = os.environ.get(
    "M2K_SOURCE_URL", "https://github.com/weiting-tw/m2k-calendar-tools")
