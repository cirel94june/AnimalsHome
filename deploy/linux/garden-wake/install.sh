#!/bin/bash
# 花园唤醒桥：服务器上装一次（root 跑）。不启动任何桥：启动由小猫在工具页点。
# 机器 token 就是每位 AI 接花园用的那把 Bearer（11-07 用官方 check 握手验证过），从 mcp_servers.json 取，
# 写进 root 600 的 /etc/garden-wake/<AI>.env，不打印。她以后在工具页改了花园钥匙，要再跑一次这个脚本。
set -euo pipefail
BRIDGE_COMMIT=f0cd9c2   # 官方桥固定版本，不跟最新
APP=/opt/aionshome/aion-chat
HERE="$(cd "$(dirname "$0")" && pwd)"

id garden-wake >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin garden-wake

if [ ! -d /opt/garden-wake ]; then
  git clone -q https://github.com/WenXiaoWendy/galatea-garden-wake-bridge.git /opt/garden-wake
fi
git -C /opt/garden-wake fetch -q origin
git -C /opt/garden-wake checkout -q "$BRIDGE_COMMIT"
(cd /opt/garden-wake && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
chown -R root:root /opt/garden-wake && chmod -R go-w /opt/garden-wake

install -m 644 "$HERE/garden-wake@.service" /etc/systemd/system/garden-wake@.service
install -m 644 "$HERE/50-aionshome-garden-wake.rules" /etc/polkit-1/rules.d/50-aionshome-garden-wake.rules
systemctl daemon-reload

# 本机密钥：injector 和家里的程序共用，只在这两个 600 的文件里
install -d -m 700 /etc/garden-wake
if ! grep -q '^GARDEN_WAKE_LOCAL_KEY=' /etc/aionshome.env; then
  echo "GARDEN_WAKE_LOCAL_KEY=$(openssl rand -hex 32)" >> /etc/aionshome.env
fi
KEY="$(grep '^GARDEN_WAKE_LOCAL_KEY=' /etc/aionshome.env | head -1 | cut -d= -f2-)"

KEY="$KEY" python3 - "$APP/data/mcp_servers.json" <<'PY'
import json, os, sys
from urllib.parse import urlsplit
servers = json.load(open(sys.argv[1], encoding="utf-8"))["servers"]
for actor in ("aion", "connor", "ai3"):
    found = [s for s in servers if (urlsplit(s.get("url", "")).hostname or "") == "galatea.abysslumina.com"
             and (s.get("actors") or []) == [actor]]
    auth = ((found[0].get("headers") or {}).get("Authorization", "") if found else "")
    if not auth.startswith("Bearer "):
        print(f"{actor}: 没有花园连接，跳过")
        continue
    path = f"/etc/garden-wake/{actor}.env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"GARDEN_MACHINE_TOKEN={auth.split(' ', 1)[1].strip()}\n"
                "GARDEN_INJECTOR_EXECUTABLE=/usr/bin/python3\n"
                f'GARDEN_INJECTOR_ARGS_JSON=["/opt/aionshome/aion-chat/tools/garden_wake_inject.py","{actor}"]\n'
                "GARDEN_INJECTOR_WORKING_DIRECTORY=/opt/garden-wake\n"
                f"GARDEN_WAKE_LOCAL_KEY={os.environ['KEY']}\n"
                "GARDEN_LOG_LEVEL=info\n")
    print(f"{actor}: 写好了")
PY
echo "装好了。家里的程序要重启一次才读到本机密钥。桥一个都没启动。"
