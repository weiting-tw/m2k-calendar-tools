# m2k-calendar MCP server（HTTP / OAuth 公用部署用；本機 stdio 模式不需要 Docker）
#
# 現成 image（多平台 amd64/arm64）：docker pull a26007565/m2k-calendar
# 發佈：bump src/_version.py 後 push main，GitHub Actions 自動 build+push
#   （.github/workflows/docker.yml）；手動備援：
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     -t a26007565/m2k-calendar:latest --push .
# 本機建置：docker build -t m2k-calendar .
# HTTP 模式（Basic pass-through）：
#   docker run -d -p 8763:8763 m2k-calendar
# OAuth 模式（claude.ai Connectors）——掛 /data volume 保留金鑰與 client 註冊：
#   docker run -d -p 8763:8763 -v m2k-data:/data m2k-calendar \
#     --oauth --issuer https://對外網址 --host 0.0.0.0 --port 8763
# 兩種模式都必須放在 HTTPS 反向代理後面。
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
# MCP App 行事曆 UI（show_calendar 的 ui:// resource 讀這個建置產物）
COPY apps/calendar/dist/ apps/calendar/dist/

# 非 root 執行；OAuth 執行期檔案（金鑰、client 註冊）collect 在 /data
RUN useradd -r -u 10001 m2k && mkdir /data && chown m2k /data
USER m2k
ENV M2K_BRIDGE_KEY_FILE=/data/bridge-key \
    M2K_OAUTH_CLIENTS=/data/oauth-clients.json \
    M2K_AUTH_LOG=/data/auth.log

EXPOSE 8763

# 只靠 restart policy 救不了「活著但壞掉」（事件循環卡住時 port 仍是通的），
# 所以打一個真實 HTTP 請求。4xx 也算健康：server 有在處理，只是請求不合 MCP 格式。
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
    CMD ["python", "src/healthcheck.py"]

ENTRYPOINT ["python", "src/m2k_mcp_server.py"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8763"]
