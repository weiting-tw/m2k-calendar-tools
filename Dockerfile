# m2k-calendar MCP server（HTTP / OAuth 公用部署用；本機 stdio 模式不需要 Docker）
#
# 建置：docker build -t m2k-calendar .
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

# 非 root 執行；OAuth 執行期檔案（金鑰、client 註冊）collect 在 /data
RUN useradd -r -u 10001 m2k && mkdir /data && chown m2k /data
USER m2k
ENV M2K_BRIDGE_KEY_FILE=/data/bridge-key \
    M2K_OAUTH_CLIENTS=/data/oauth-clients.json

EXPOSE 8763
ENTRYPOINT ["python", "src/m2k_mcp_server.py"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8763"]
