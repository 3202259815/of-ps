#!/usr/bin/env bash
# 在服务器上发 GM 指令的快捷脚本（避免手打引号出错）
#
# 用法：
#   ./gm.sh give 1000001 402
#   ./gm.sh give all all
#   ./gm.sh tp 1000001 100
#   ./gm.sh players
#   ./gm.sh save
#
# 依赖 .env 里的 GM_HTTP_TOKEN（脚本会自动读取同目录的 .env）
set -euo pipefail

cd "$(dirname "$0")"

# 只读需要的两个键，**不能用 source .env**：
# .env 里 RES_URL 带分号（http://a;https://b），source 会被当成命令执行并报错退出。
env_get() {
  [ -f .env ] || return 0
  grep -E "^[[:space:]]*$1[[:space:]]*=" .env 2>/dev/null \
    | tail -n 1 \
    | cut -d= -f2- \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
          -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

PORT="$(env_get HTTP_PORT)"
PORT="${PORT:-25565}"
TOKEN="$(env_get GM_HTTP_TOKEN)"
TOKEN="${TOKEN:-${GM_HTTP_TOKEN:-}}"

if [ -z "${TOKEN}" ]; then
  echo "错误：.env 里没有设置 GM_HTTP_TOKEN" >&2
  echo "先执行： echo 'GM_HTTP_TOKEN=你的随机串' >> .env && docker compose up -d" >&2
  exit 1
fi

if [ "$#" -eq 0 ]; then
  echo "用法：./gm.sh <指令> [参数...]" >&2
  echo "例：  ./gm.sh give 1000001 402" >&2
  exit 1
fi

# --data-urlencode 会自动处理空格和特殊字符，不用担心引号
curl -sS -X POST "http://127.0.0.1:${PORT}/gm/cmd" \
  -H "X-GM-Token: ${TOKEN}" \
  --data-urlencode "cmd=$*"
echo
