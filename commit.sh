#!/usr/bin/env bash
# 在你的 Mac 上、於 outputs 資料夾執行： bash commit.sh
# （沙箱對同步資料夾的 git 有權限限制，需在你本機跑；push 也需要你的 git 憑證）
set -e
cd "$(dirname "$0")"

REMOTE="https://git.gss.com.tw/wilber_chen/m2k-calendar-tools.git"

# 清掉沙箱殘留的 lock（若有）
rm -f .git/*.lock 2>/dev/null || true
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init -q
git config user.email "wilber_chen@gss.com.tw"
git config user.name  "Wilber Chen"
git branch -M main 2>/dev/null || true

commit () { git add "$@" 2>/dev/null && git commit -q -m "$MSG" 2>/dev/null && echo "✓ $MSG" || echo "略過：$MSG"; }

MSG="chore: gitignore and env example";            commit .gitignore .env.example
MSG="feat(cli): m2kcal CalDAV calendar CLI + tests"; commit m2kcal.py test_m2k.py
MSG="feat(cli): m2kgroup address-book group expansion"; commit m2kgroup.py
MSG="feat(userscript): webmail group meeting helper";   commit m2k-group-book.user.js
MSG="feat(userscript): multi-calendar board (others' calendars)"; commit m2k-multi-calendar-board.user.js
MSG="feat(mcp): m2k-calendar MCP server (query + book)"; commit m2k_mcp_server.py
MSG="feat(skill): m2k-calendar skill";                  commit skill/SKILL.md
MSG="docs: feasibility report, usage guide, progress, commit script"; commit "README-使用說明.md" "m2k-calendar-cli-feasibility.md" PROGRESS.md commit.sh

# 設定 remote 並推送
git remote get-url origin >/dev/null 2>&1 && git remote set-url origin "$REMOTE" || git remote add origin "$REMOTE"
echo "推送到 $REMOTE …"
git push -u origin main

echo "=== git log ==="
git log --oneline
