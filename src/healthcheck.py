#!/usr/bin/env python3
"""容器健康檢查 — 確認 server 真的在回應請求，而不只是 port 開著。

為什麼不只檢查 TCP：`restart: unless-stopped` 只救進程死掉的情況；
「活著但壞掉」（事件循環卡住、worker 掛住）從外面看 port 仍是通的。
打一個真實 HTTP 請求才分得出來。

MCP 端點對裸 GET 會回 4xx（缺 session/accept 標頭）—— 那算健康：
server 活著且在處理請求。連不上、超時、或 5xx 才算壞。

用法（Dockerfile）：HEALTHCHECK CMD ["python", "src/healthcheck.py"]
埠號預設 8763，可用 M2K_HEALTH_PORT 覆蓋（部署改 --port 時要一起改）。
"""
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = float(os.environ.get("M2K_HEALTH_TIMEOUT", "4"))
PORT = os.environ.get("M2K_HEALTH_PORT", "8763")
PATH = os.environ.get("M2K_HEALTH_PATH", "/mcp")


def main() -> int:
    url = f"http://127.0.0.1:{PORT}{PATH}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return 0 if r.status < 500 else 1
    except urllib.error.HTTPError as err:
        return 0 if err.code < 500 else 1     # 4xx：server 有在處理，只是這個請求不合格式
    except Exception as err:                   # 連不上 / 超時 / DNS / 讀取中斷
        print(f"unhealthy: {type(err).__name__}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
