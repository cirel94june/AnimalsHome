#!/usr/bin/env bash
# 按上游 aion-chat/requirements.txt 同步 Linux 依赖。
# 上游面向 Windows，这里自动做三处转换，以后上游增删依赖也能直接跟上：
#   - 去掉 pywin32（Windows 专属，Linux 上相关功能会自动停用）
#   - opencv-python → opencv-python-headless（服务器不需要图形界面库，更省内存和依赖）
#   - 用 constraints-linux.txt 锁住会破坏启动的版本（如 mcp 2.x）
# pyncm 已从 PyPI 下架，从仓库自带的 vendor/ 目录安装。
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
# VENV 可指定安装到别的目录（自动更新时先装到新目录，验证通过再切换）
venv="${VENV:-$root/.venv}"

if [ ! -x "$venv/bin/python" ]; then
  uv python install 3.12
  uv venv --python 3.12 "$venv"
fi

req="$(mktemp)"
trap 'rm -f "$req"' EXIT
grep -viE '^\s*pywin32\b' "$root/aion-chat/requirements.txt" \
  | sed -E 's/^\s*opencv-python\s*$/opencv-python-headless/' > "$req"

VIRTUAL_ENV="$venv" uv pip install --quiet \
  --find-links "$root/vendor" \
  -r "$req" -c "$here/constraints-linux.txt"
