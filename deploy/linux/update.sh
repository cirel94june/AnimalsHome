#!/usr/bin/env bash
# 由 aionshome-update.timer 每 5 分钟调用，以 aionshome 用户运行（不是 root）：
# 仓库有新提交就备份数据、拉取、按需重建依赖、重启，并确认新版本真的跑起来了；否则自动回退。
#
# 安全边界：这个脚本和代码目录都归 aionshome 用户所有，所以它绝不能以 root 运行。
# 重启服务通过写一个请求文件完成，由 root 的 aionshome-restart.path 监听后只执行
# 「systemctl restart aionshome」这一条固定命令。
set -uo pipefail
APP_DIR="${APP_DIR:-/opt/aionshome}"
STATE_DIR="${STATE_DIR:-/var/lib/aionshome}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-8080}"
LOG="${UPDATE_LOG:-$STATE_DIR/logs/update.log}"
RUN_DIR="$STATE_DIR/run"
VENVS="$STATE_DIR/venvs"
BACKUPS="$STATE_DIR/backups"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"

note() { echo "$(date '+%F %T') $*" >> "$LOG"; }
[ "$(id -u)" != 0 ] || { echo "update.sh 不能以 root 运行"; exit 1; }
mkdir -p "$RUN_DIR" "$VENVS" "$BACKUPS"
git() { command git -C "$APP_DIR" "$@"; }

request_restart() { date +%s.%N > "$RUN_DIR/restart-request"; }

# 等到 /ops/healthz 报告的版本等于 $1
wait_healthy() {
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  sleep 5
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/ops/healthz" 2>/dev/null | grep -q "\"commit\":\"$1\""; then
      return 0
    fi
    sleep 5
  done
  return 1
}

current_venv() { readlink -f "$APP_DIR/.venv"; }
use_venv() { ln -sfn "$1" "$APP_DIR/.venv.tmp" && mv -Tf "$APP_DIR/.venv.tmp" "$APP_DIR/.venv"; }

backup_data() {
  # SQLite 用在线备份接口拷贝（运行中也一致），JSON 设置直接复制；保留最近 3 份
  local dest="$BACKUPS/$(date +%Y%m%d-%H%M%S)-${1:0:7}"
  mkdir -p "$dest"
  "$APP_DIR/.venv/bin/python" - "$APP_DIR/aion-chat/data" "$dest" <<'PY' || return 1
import shutil, sqlite3, sys
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
for f in src.glob("*.db"):
    with sqlite3.connect(f"file:{f}?mode=ro", uri=True) as a, sqlite3.connect(dest / f.name) as b:
        a.backup(b)
for f in src.glob("*.json"):
    shutil.copy2(f, dest / f.name)
PY
  ls -1dt "$BACKUPS"/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf
  echo "$dest"
}

git fetch -q origin "$BRANCH" || { note "fetch 失败"; exit 0; }
old="$(git rev-parse HEAD)"
new="$(git rev-parse "origin/$BRANCH")"
[ "$old" = "$new" ] && exit 0
if [ "$(cat "$RUN_DIR/bad-commit" 2>/dev/null)" = "$new" ]; then
  exit 0  # 这个版本已经失败过一次，等下一个提交
fi
if ! git merge-base --is-ancestor "$old" "$new"; then
  note "无法快进到 ${new:0:7}（分支被改写或服务器上有本地提交），本次跳过"
  exit 0
fi

backup="$(backup_data "$old")" || { note "数据备份失败，本次不更新"; exit 0; }
old_venv="$(current_venv)"
new_venv="$old_venv"

if ! git merge -q --ff-only "origin/$BRANCH"; then
  note "无法快进到 ${new:0:7}（服务器上有本地改动？），本次跳过"
  exit 0
fi
note "更新 ${old:0:7} → ${new:0:7}: $(git log -1 --format=%s)（数据已备份到 $backup）"

rollback() {
  note "回退到 ${old:0:7}：$1"
  echo "$new" > "$RUN_DIR/bad-commit"
  git reset -q --hard "$old"
  use_venv "$old_venv"
  [ "$new_venv" != "$old_venv" ] && rm -rf "$new_venv"
}

# 依赖有变化：装进一个新的虚拟环境，旧环境原样保留，失败时直接切回
if git diff --name-only "$old" "$new" | grep -qE '^(aion-chat/requirements\.txt|deploy/linux/(constraints-linux\.txt|sync_deps\.sh)|vendor/)'; then
  new_venv="$VENVS/${new:0:12}"
  rm -rf "$new_venv"
  if VENV="$new_venv" bash "$APP_DIR/deploy/linux/sync_deps.sh" >> "$LOG" 2>&1; then
    note "依赖已装到新环境 $new_venv"
  else
    rollback "依赖安装失败"
    exit 0
  fi
  use_venv "$new_venv"
fi

# 重启前先做语法检查，明显坏掉的代码不会替换正在运行的版本
if ! "$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/aion-chat" -x '(node_modules|/data/)' >> "$LOG" 2>&1; then
  rollback "语法检查失败"
  exit 0
fi

request_restart
if wait_healthy "$new"; then
  note "已重启，新版本 ${new:0:7} 运行正常"
  # 只保留当前和上一个虚拟环境
  ls -1dt "$VENVS"/*/ 2>/dev/null | while read -r d; do
    d="${d%/}"
    [ "$d" = "$(current_venv)" ] || [ "$d" = "$old_venv" ] || rm -rf "$d"
  done
  exit 0
fi

rollback "新版本 ${HEALTH_TIMEOUT}s 内没有通过健康检查"
request_restart
if wait_healthy "$old"; then
  note "已回退并恢复运行（旧版本 ${old:0:7}）。数据备份：$backup"
else
  note "回退后服务仍未恢复，请看 app.log。数据备份：$backup"
fi
