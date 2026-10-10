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
- 给服务加内存护栏（默认软上限 450M、硬上限 600M，可用 `MEMORY_HIGH` / `MEMORY_MAX` 调整）：
  超过硬上限只会重启 AionsHome，不会拖垮同机的 Memory Hub；
- 全站请求体上限 64MB（`AIONSHOME_MAX_BODY_MB` 可调，音乐站上传单独放宽），超大上传在进入接口前就被拒绝；
- 注册 `aionshome-update.timer`：每 5 分钟检查一次 GitHub，有新提交时：
  1. 先备份 `aion-chat/data/` 里的数据库和设置到 `/var/lib/aionshome/backups/`（保留最近 3 份）；
  2. 快进拉取代码；依赖有变化时装进**新的**虚拟环境，旧环境原样保留；
  3. 语法检查通过才重启，重启后等 `/ops/healthz` 报告新版本真的跑起来了；
  4. 任何一步失败都自动退回旧代码和旧环境并重启回旧版本，这个坏版本记下来不再反复尝试，等下一个提交。

安全边界：代码目录归 `aionshome` 用户所有，所以**自动更新也以 `aionshome` 用户运行，root 不执行代码目录里的任何东西**。
需要重启时，更新脚本只能写一个「重启请求」文件，由 root 的 `aionshome-restart.path` 监听，
只执行固定的 `systemctl restart aionshome` 这一条命令。

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

AI 不需要自己想起来去搜记忆。Memory Hub 连不上时只会跳过注入，不会影响聊天。

每轮对话先写进本地待补传队列（`aion-chat/data/memory_hub_outbox.db`），送达后才删除；
Hub 断线、重启或本服务重启后会自动补传。只有「肯定没送到」的才自动重发；
「可能已经送到但没收到回执」的标为「不确定」，**不自动重发**（Memory Hub 的 `capture` 目前没有幂等键，重发会重复记录），
状态页会显示待补传和不确定的条数。

聊天室里，只有 AI 正常回复用户的那一条会和触发它的用户发言配对录入；
主动消息、工具后续、失败提示、环境语音回复不会被配到旧的用户发言上。

默认角色对应：主 AI（aion）→ `claude`，第二位（connor）→ `lucien`。要改的话，
复制 `deploy/memory_hub.example.json` 到 `aion-chat/data/memory_hub.json` 修改 `actors`。

注意：访客会客室、外出拜访等面向他人的场景不会注入跨端记忆，避免私人记忆外泄。

## 常用命令（一般用不到）

```bash
systemctl status aionshome                # 服务状态
tail -n 100 /var/lib/aionshome/logs/app.log    # 日志
systemctl start aionshome-update          # 立刻检查更新
```
