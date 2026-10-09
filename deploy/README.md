# 部署到 Linux VPS

AionsHome 原本跑在 Windows 电脑上。这里的脚本让它在一台小 VPS（1G 内存也可以）上 24 小时运行，
并且以后不需要 SSH：代码推到 GitHub 后，VPS 每 5 分钟自动拉取、自动重启。

## 一次性安装

1. 打开 VPS 服务商后台的「网页控制台 / VNC」，登录 root（普通用户先执行 `sudo -i`）。
2. 粘贴下面一行回车（合并到 main 之前先用功能分支）：

   ```bash
   curl -fsSL https://raw.githubusercontent.com/cirel94june/AnimalsHome/claude/frontend-memory-integration-ke0lpe/deploy/linux/install.sh | BRANCH=claude/frontend-memory-integration-ke0lpe bash
   ```

   仓库设为私有后，下载脚本也需要 token：`curl -fsSL -H "Authorization: token 只读token" 同一个地址 | BRANCH=... GIT_TOKEN=只读token bash`。
3. 中途会打印一个 Tailscale 登录链接，用手机打开、登录你的 Tailscale 账号。
   如果之后提示要开启 HTTPS 证书，也点开它给的链接确认一次。
4. 结束时会打印两个地址：
   - `https://<你的VPS>.<tailnet>.ts.net/`：AionsHome 本体（手机也装 Tailscale 并登录同一账号才能打开）；
   - `https://<你的VPS>.<tailnet>.ts.net/ops/status`：运行状态页，看服务是否正常、内存、最近更新和日志。

脚本做了什么：

- 内存小于 2G 且没有 swap 时，创建 2G swap；
- 用 uv 安装 Python 3.12（上游代码要求 3.12+），按上游 `requirements.txt` 安装依赖，
  并自动去掉 Windows 专属的 `pywin32`、改用无界面版 OpenCV、把 `mcp` 锁在 1.x（2.x 会导致启动崩溃）；
  已从 PyPI 下架的 `pyncm` 从仓库自带的 `vendor/` 安装；
- 注册 `aionshome` 服务（开机自启、崩溃自动重启），只监听 `127.0.0.1`，通过 `tailscale serve` 只对你的设备开放；
- 注册 `aionshome-update.timer`：每 5 分钟检查一次 GitHub，有新提交就快进更新并重启；
  依赖装不上时自动退回上一版本，不会把服务弄坏。`aion-chat/data/` 里的聊天记录和设置不会被动到。

重复运行安装命令是安全的，可以用来修复环境。

## 接上 Memory Hub（跨端记忆）

编辑 `/etc/aionshome.env`，去掉这三行开头的 `#` 并填好，然后执行 `systemctl restart aionshome`：

```bash
MEMORY_HUB_ENABLED=true
MEMORY_HUB_URL=http://127.0.0.1:端口/mcp
MEMORY_HUB_TOKEN=如果你的 Memory Hub 需要的话
```

Memory Hub 和 AionsHome 在同一台 VPS 上时，地址一般是 `http://127.0.0.1:端口/mcp`。
不记得端口可以执行 `ss -ltnp` 看本机正在监听的端口。

接好后，每轮对话：

- 生成回复前，自动调用 Memory Hub 的 `context`，把「各端最近发生的事 + 和这句话相关的记忆」注入给 AI；
- 回复之后，自动调用 `capture` 把这一轮记进 Memory Hub（平台标记为 `aionshome`）。

AI 不需要自己想起来去搜记忆。Memory Hub 连不上时只会跳过这一步，不会影响聊天；状态页会显示当前状态。

默认角色对应：主 AI（aion）→ `claude`，第二位（connor）→ `lucien`。要改的话，
复制 `deploy/memory_hub.example.json` 到 `aion-chat/data/memory_hub.json` 修改 `actors`。

注意：访客会客室、外出拜访等面向他人的场景不会注入跨端记忆，避免私人记忆外泄。

## 常用命令（一般用不到）

```bash
systemctl status aionshome                # 服务状态
tail -n 100 /var/lib/aionshome/logs/app.log    # 日志
systemctl start aionshome-update          # 立刻检查更新
```
