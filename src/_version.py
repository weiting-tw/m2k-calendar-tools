"""單一版本來源：image tag、登入頁 footer、README 都引用這裡。"""
import os

__version__ = "1.3.0"

# 原始碼連結（登入頁 footer 顯示）。可用 M2K_SOURCE_URL 覆寫；
# 預設先指向內部 GitLab，推上 GitHub 後改這個預設值即可。
SOURCE_URL = os.environ.get(
    "M2K_SOURCE_URL", "https://git.gss.com.tw/wilber_chen/m2k-calendar-tools")
