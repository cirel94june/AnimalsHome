#!/usr/bin/env bash
# AionsHome · Linux VPS 一键安装（Debian / Ubuntu）
#
# 在 VPS 服务商的「网页控制台 / VNC」里以 root 粘贴运行一次即可，之后不需要 SSH：
#   curl -fsSL https://raw.githubusercontent.com/cirel94june/AnimalsHome/main/deploy/linux/install.sh | bash
# 可选环境变量（写在 bash 前面，例如 BRANCH=xxx bash install.sh）：
#   REPO_URL     仓库地址（默认 https://github.com/cirel94june/AnimalsHome.git）
#   BRANCH       跟随的分支（默认 main）
#   GIT_TOKEN    仓库设为私有时填一个只读 token
#   PORT         本机监听端口（默认 8080，只监听 127.0.0.1）
#   SWAP_MB      内存小于 2G 且没有 swap 时创建的 swap 大小（默认 2048）
#   NO_TAILSCALE=1  跳过 Tailscale 安装
#   MEMORY_HIGH / MEMORY_MAX  本服务的内存软/硬上限（默认 450M / 600M）。超过硬上限只会重启本服务，
#                不会拖垮同机的 Memory Hub；装好后按状态页里的实际占用调整
#
# 重复运行是安全的：已有的数据、配置和 swap 都会保留。
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/cirel94june/AnimalsHome.git}"
BRANCH="${BRANCH:-main}"
GIT_TOKEN="${GIT_TOKEN:-}"
PORT="${PORT:-8080}"
SWAP_MB="${SWAP_MB:-2048}"
MEMORY_HIGH="${MEMORY_HIGH:-450M}"
MEMORY_MAX="${MEMORY_MAX:-600M}"
APP_USER="aionshome"
APP_DIR="/opt/aionshome"
STATE_DIR="/var/lib/aionshome"
ENV_FILE="/etc/aionshome.env"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "请用 root 运行（网页控制台里先执行 sudo -i）"; exit 1; }
command -v apt-get >/dev/null || { echo "目前只支持 Debian / Ubuntu"; exit 1; }

say "1/7 检查内存与 swap"
mem_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
swap_mb=$(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo)
echo "内存 ${mem_mb}MB，swap ${swap_mb}MB"
if [ "$mem_mb" -lt 2000 ] && [ "$swap_mb" -lt 512 ]; then
  if [ ! -f /swapfile ]; then
    echo "内存不足 2G，创建 ${SWAP_MB}MB swap"
    fallocate -l "${SWAP_MB}M" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_MB" status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=20 && (grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=20' >> /etc/sysctl.conf)
fi

say "2/7 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git curl ca-certificates libportaudio2 ffmpeg logrotate >/dev/null

say "3/7 准备用户与代码"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" "$STATE_DIR" "$STATE_DIR/logs" "$STATE_DIR/run" "$STATE_DIR/venvs" "$STATE_DIR/backups"
# 代码目录归应用用户所有；root 不在里面执行任何东西（git、脚本都以应用用户身份运行）
as_app() { runuser -u "$APP_USER" -- env HOME="$STATE_DIR" PATH="/usr/local/bin:$PATH" "$@"; }
clone_url="$REPO_URL"
if [ -n "$GIT_TOKEN" ]; then
  clone_url="$(echo "$REPO_URL" | sed "s#https://#https://x-access-token:${GIT_TOKEN}@#")"
fi
if [ ! -d "$APP_DIR/.git" ]; then
  install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
  as_app git clone --branch "$BRANCH" "$clone_url" "$APP_DIR"
else
  as_app git -C "$APP_DIR" remote set-url origin "$clone_url"
  as_app git -C "$APP_DIR" fetch -q origin "$BRANCH"
  as_app git -C "$APP_DIR" checkout -q "$BRANCH"
  as_app git -C "$APP_DIR" merge -q --ff-only "origin/$BRANCH" || warn "本地有改动，未自动更新代码"
fi

say "4/7 安装 Python 3.12 与依赖（首次约需几分钟）"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
fi
# 虚拟环境放在 $STATE_DIR/venvs/ 下，$APP_DIR/.venv 是指向当前环境的链接。
# 自动更新换依赖时装进新环境再切换链接，失败可以切回。
if [ -d "$APP_DIR/.venv" ] && [ ! -L "$APP_DIR/.venv" ]; then
  as_app mv "$APP_DIR/.venv" "$STATE_DIR/venvs/legacy"
  as_app ln -s "$STATE_DIR/venvs/legacy" "$APP_DIR/.venv"
fi
if [ ! -L "$APP_DIR/.venv" ]; then
  as_app ln -s "$STATE_DIR/venvs/initial" "$APP_DIR/.venv"
fi
as_app env VENV="$(readlink -f "$APP_DIR/.venv")" bash "$APP_DIR/deploy/linux/sync_deps.sh"

say "5/7 写入配置"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# AionsHome 运行配置（改完执行：systemctl restart aionshome）
PORT=$PORT
BRANCH=$BRANCH
# Memory Hub 跨端记忆：去掉下面三行开头的 # 并填好（同机部署时地址一般是 http://127.0.0.1:端口/mcp）
#MEMORY_HUB_ENABLED=true
#MEMORY_HUB_URL=http://127.0.0.1:8000/mcp
#MEMORY_HUB_TOKEN=
EOF
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
fi
as_app mkdir -p "$APP_DIR/aion-chat/data"

say "6/7 注册开机自启与自动更新"
cat > /etc/systemd/system/aionshome.service <<EOF
[Unit]
Description=AionsHome
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR/aion-chat
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port \${PORT}
Restart=always
RestartSec=5
# 内存护栏：超过软上限先回收，超过硬上限只杀本服务（随后自动重启），保护同机的 Memory Hub
MemoryHigh=$MEMORY_HIGH
MemoryMax=$MEMORY_MAX
NoNewPrivileges=true
PrivateTmp=true
StandardOutput=append:$STATE_DIR/logs/app.log
StandardError=append:$STATE_DIR/logs/app.log

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/aionshome-update.service <<EOF
[Unit]
Description=AionsHome auto update

[Service]
Type=oneshot
# 以应用用户运行：更新脚本在应用可写的目录里，绝不能交给 root 执行
User=$APP_USER
Group=$APP_USER
EnvironmentFile=$ENV_FILE
Environment=HOME=$STATE_DIR APP_DIR=$APP_DIR STATE_DIR=$STATE_DIR PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash $APP_DIR/deploy/linux/update.sh
Nice=10
MemoryMax=500M
NoNewPrivileges=true
EOF
# 应用用户只能「请求重启」：写这个文件后，root 只执行一条固定的 systemctl restart
cat > /etc/systemd/system/aionshome-restart.path <<EOF
[Unit]
Description=AionsHome restart request

[Path]
PathModified=$STATE_DIR/run/restart-request

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/aionshome-restart.service <<EOF
[Unit]
Description=Restart AionsHome on request

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart aionshome.service
EOF
cat > /etc/systemd/system/aionshome-update.timer <<EOF
[Unit]
Description=AionsHome auto update every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
cat > /etc/logrotate.d/aionshome <<EOF
$STATE_DIR/logs/*.log {
  su $APP_USER $APP_USER
  weekly
  rotate 4
  maxsize 20M
  missingok
  notifempty
  compress
  copytruncate
}
EOF
systemctl daemon-reload
systemctl enable --now aionshome.service aionshome-update.timer aionshome-restart.path
systemctl restart aionshome.service

say "7/7 Tailscale（只让你自己的设备访问）"
if [ "${NO_TAILSCALE:-0}" != 1 ]; then
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  if ! tailscale status >/dev/null 2>&1; then
    echo "下面会打印一个登录链接，用手机打开并登录你的 Tailscale 账号："
    tailscale up
  fi
  tailscale serve --bg "$PORT" >/dev/null 2>&1 || warn "tailscale serve 未成功，可稍后手动执行：tailscale serve --bg $PORT"
fi

for _ in $(seq 1 30); do
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break
  sleep 2
done
echo
if curl -fs -o /dev/null "http://127.0.0.1:$PORT/"; then
  say "安装完成 ✓"
else
  warn "服务还没起来，看日志：tail -n 80 $STATE_DIR/logs/app.log"
fi
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  host=$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
  [ -n "$host" ] && echo "手机装好 Tailscale 并登录同一账号后打开：https://$host/"
  [ -n "$host" ] && echo "运行状态页：https://$host/ops/status"
fi
echo "Memory Hub 配置：编辑 $ENV_FILE 后执行 systemctl restart aionshome"
