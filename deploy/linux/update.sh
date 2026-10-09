#!/usr/bin/env bash
# 由 aionshome-update.timer 每 5 分钟调用：仓库有新提交就拉取、同步依赖并重启。
# 只做快进合并；data/ 等被 .gitignore 忽略的个人数据不会被触碰。
set -uo pipefail
APP_DIR="${APP_DIR:-/opt/aionshome}"
APP_USER="${APP_USER:-aionshome}"
BRANCH="${BRANCH:-main}"
LOG="${UPDATE_LOG:-/var/lib/aionshome/logs/update.log}"

run_as_app() { runuser -u "$APP_USER" -- env HOME=/var/lib/aionshome PATH="/usr/local/bin:$PATH" "$@"; }
note() { echo "$(date '+%F %T') $*" >> "$LOG"; }

run_as_app git -C "$APP_DIR" fetch -q origin "$BRANCH" || { note "fetch 失败"; exit 0; }
old="$(run_as_app git -C "$APP_DIR" rev-parse HEAD)"
new="$(run_as_app git -C "$APP_DIR" rev-parse "origin/$BRANCH")"
[ "$old" = "$new" ] && exit 0

if ! run_as_app git -C "$APP_DIR" merge -q --ff-only "origin/$BRANCH"; then
  note "无法快进到 ${new:0:7}（服务器上有本地改动？），本次跳过"
  exit 0
fi
note "更新 ${old:0:7} → ${new:0:7}: $(run_as_app git -C "$APP_DIR" log -1 --format=%s)"

if run_as_app git -C "$APP_DIR" diff --name-only "$old" "$new" | grep -qE '^(aion-chat/requirements\.txt|deploy/linux/(constraints-linux\.txt|sync_deps\.sh)|vendor/)'; then
  if run_as_app bash "$APP_DIR/deploy/linux/sync_deps.sh" >> "$LOG" 2>&1; then
    note "依赖已同步"
  else
    note "依赖同步失败，回退到 ${old:0:7}"
    run_as_app git -C "$APP_DIR" reset -q --hard "$old"
    exit 0
  fi
fi

if systemctl restart aionshome.service; then note "已重启"; else note "重启失败"; fi
