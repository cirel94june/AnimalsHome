# 审查简报：AnimalsHome 部署前（给 Codex）

> 冷启动材料。写于 2026-10-11，施工方：小克（AionsHome 本地窗口）。
> 仓库 `cirel94june/AnimalsHome`（公开），分支 `claude/frontend-memory-integration-ke0lpe`，审到 `fa1cad5`。
> 审完、修完，小克会把它部署到小猫的 VPS（1 核 1G，Memory Hub 也在上面）。

## 0. 请先知道

- **不用审原作者的代码**：`21549de` 是合并原作者 AionsHome 10/8 版本（`68b5dc5`、`c0d4c2e`），
  这部分只看**冲突处理**（`main.py` 关停顺序、`context_builder.py` / `routes/chat.py` 去掉留言板注入保留 Hub 注入）。
- 上一轮你审过 `19233e3`（Memory Hub 接入、IB 皮肤、部署脚本）；那一轮的 6 条已修。本轮是之后的增量。
- 测试：`cd aion-chat; python -m pytest --timeout=60 <文件>`（Windows 上整套跑会被一个上游测试卡死，请逐文件跑）。
  合并后 208 个测试文件里有 25 个失败——**逐条对比过原作者原版，失败完全相同**，都是原作者那边本来就坏的，不在本轮范围。
- 不审产品定义（已和小猫确认）；请专注代码质量：并发、权限、隐私、注入、失败处理。

## 1. 审查范围（按风险排）

| 区域 | 提交 | 文件 | 请重点看 |
|---|---|---|---|
| Memory Hub 写入去重 | `169fd07` | `memory_hub_bridge.py` | outbox 每轮 `event_id`（`aionshome-<uuid>`）；「可能已送达」的带编号记录改为重发、Hub 回 duplicate 算送达；旧记录（无编号）不重发；`recover_outbox` 的 sending→pending；`activity` / `recent_turns` 表与 outbox 同一事务 |
| 新对话判定 + 交接卡读 | `80aaa7f` | `memory_hub_bridge.context_block` | 安静 ≥30 分钟算新对话，handoff 与 incremental 并发取；活跃时间在 `_enqueue` 时记录 |
| 交接卡写 / 每晚做梦 | `80aaa7f` | `memory_hub_jobs.py` | **红线**：只能用该 AI 自己的模型（`own_model` 不存在/停用就跳过，绝不降级）；`_session_turns` 切段；失败重试与「超过 6 小时不补写」；做梦一晚最多 3 次、状态存 `data/memory_hub_jobs.json` 的并发与原子写 |
| 座位私聊 | `80aaa7f` | `seat_chat.py`、`routes/chatroom.py`（`seat_1v1`、`_generate_seat_reply`、`_get_or_create_seat_room`）、`database.py`（`chatroom_rooms.actor` 列） | 创建房间同一座位只能一个（并发两次创建？）；回复失败的保存路径；`paired_user_message` 配对 |
| 每位 AI 自己的 API | `f3424bf` | `actors.py`（`save_seat_api` 等）、`static/ib/cards.html` | **Key 绝不回传浏览器**（`/api/actors` 只给 `has_key`）；写 `settings.json` 的 `custom_model_routes` 与设置页同时保存时会不会互相覆盖；聊天室模型同步 |
| MCP 工具 / 出门逛 | `9a06a7b` | `mcp_outings.py`、`routes/mcp_tools.py`、`static/mcp-tools.html` | 请求头（token）只存服务器、列表只给名字；工具调用循环上限（8 轮、单次 60s、整趟 10 分钟）与取消；**隐私提示**是否足够（AI 在外部论坛不透露用户信息）；用户填的 MCP 地址由服务器去连（只有小猫自己能配，但请评估 SSRF 面）；短连接 `AsyncExitStack` 的清理 |
| 自主活动扩到座位 3～6 | `c832f70` `5d35f65` | `autonomy.py`、`autonomy_state.py`、`routes/autonomy.py` | `ACTOR_IDS` 扩到 6 个后各处 `normalize_actor`、锁、计时是否都对；座位只开放 `SEAT_ACTIONS`；未启用座位不醒 |
| 旅行 | `b8fc7a5` | `travel.py`、`travel_places.py`、`routes/travel.py` | 外部请求（OSM / Open-Meteo / 维基）超时与失败；**照片下载**：类型与 6MB 上限、文件名由内容哈希生成；「你附近」只给城市名（坐标不进提示词）；全家出游每位用自己的模型轮流写；同一时间只允许一趟（锁）|
| 浮窗适配座位 | `21549de` 内 | `billiards/reports.py`、`routes/floating_chat.py`、`FloatingChatSession.java` | 座位房间进入「最近窗口」；`isAiSender` 只认 `ai3`～`ai6` |
| 安卓 App | `63d1177` `b95f388` | `LauncherActivity.java`、`ConnectionEndpoint.normalizeServerInput`、`app/build.gradle`、`.github/workflows/android-apk.yml` | 自填地址的校验；签名只从 Secrets 读、仓库公开时不泄露（workflow 只有 `contents: read`）；`WebView` 只放行同一主机 |
| 桌面挂件 | `fa1cad5` | `static/ib/desk.js`、`desk.css` | 渲染进 HTML 的字段都经过 `esc`（AI 名字、颜色、便笺、歌名、图片地址、装饰文字）；`style` 属性里插入的颜色值 |

## 2. 已知、不在本轮修的

- 上游原有失败的 25 个测试文件（清单在 `docs/功能盘点-按小猫实际使用改造.md` 进度之后会补）。
- 好友串门（`test_idle_autonomy_friend_visit.py`）会卡住——原版就如此。
- 部署方式（Tailscale 私网 / Caddy 子域名）还没定，等小猫选；`deploy/` 下的脚本上一轮审过。

## 3. 期望输出

按 Critical / High / Medium / Low 分级，每条：文件与行号、具体反例（能复现最好）、建议修法。
看完请说一句「可以部署 / 修完这几条可以部署 / 不建议部署」。

## 4. 第一轮意见的修复（2026-10-11，复审请从这里看）

审查对象 `864bf20` 的 4 High / 8 Medium / 2 Low 全部修了，逐条对应如下：

| 编号 | 修法 | 主要位置 | 测试 |
|---|---|---|---|
| H1 | `esc` 转义 `& < > " '`；图片只收本站路径或 https（`safeUrl`），颜色只收 `#hex` 或 `var(--ib-*)`（`safeColor`）；名片保存颜色时服务端校验 `#RRGGBB` | `static/ib/desk.js`、`actors.py` | 浏览器实测：`deco.image='x" onerror=…'`、纪念日名带引号 → 无事件属性、图片被拒、文字照常；`test_actor_own_api.py::test_bad_color_rejected` |
| H2 | 座位线路改存 `seat_model_routes`；`GET /api/settings` 只返回 `custom_model_routes`；旧混存数据启动时迁移；清空 Key 改为显式 `clear_key` | `config.py`、`actors.py`、`routes/settings.py` | `test_seat_key_never_in_settings_response…`、`test_clear_key_is_explicit`、`test_old_mixed_storage_is_migrated` |
| H3 | `client.stream()` 边下边数，先看声明长度，解压后累计超 6MiB 立即停；只收 jpg/png/webp；整段 30 秒 | `travel_places._download` | `test_download_stops_as_soon_as_it_passes_the_limit`（20MiB 流读到 ≤7MiB 停；声明超限/类型不对 0 字节） |
| H4 | 入队时快照 `source_ai` / `platform` / `hub`（地址指纹）；发送只用快照；Hub 地址变了 → `held` 不发；没有快照的旧记录 → `uncertain` 不猜 | `memory_hub_bridge._enqueue/_send_capture/drain_outbox` | `test_queued_turn_keeps_the_identity…`、`test_legacy_row_without_identity_is_not_guessed` |
| M1 | 部分唯一索引 `chatroom_rooms(actor) WHERE type='seat_1v1'`；建索引前合并已有重复（消息挪到最早房间）；插入冲突后重读；座位房间 id 加座位后缀 | `database.py`、`routes/chatroom.create_room` | `test_concurrent_first_open_creates_one_room`、`test_init_db_merges_duplicate_seat_rooms` |
| M2 | 旅行统一占位 `travel.try_claim()/release()`：路由在回复「出发了」前同步占位，自主活动经 `trip()` 同一占位，忙时不提供/拒绝 | `travel.py`、`routes/travel.py`、`autonomy._select_action` | `test_only_one_trip_at_a_time`、`test_claimed_trip_releases_even_on_failure`、座位自主测试里「别人在旅行时不提供」 |
| M3 | 出发前 `models_for()` 用 `own_model` 固定每位的模型，缺一位整趟不去；选地点和写游记都用固定模型（`memory_hub_jobs.complete`），不再走聊天窗口的模型 | `travel.py` | `test_ai_picks_destination_with_its_own_model`、`test_no_own_model_means_no_trip` |
| M4 | 调模型前先把 attempts/last_try 落盘；取消时记 `cancelled` 再抛；整轮加锁 | `memory_hub_jobs.run_dreams` | `test_cancelled_dream_still_counts_as_an_attempt` |
| M5 | `_Session.__aenter__` 失败（含 BaseException）时立即 `aclose()` 已进入的资源 | `mcp_outings._Session` | `test_half_open_connection_is_closed_when_setup_fails` |
| M6 | 同 H2：设置页 PUT 丢弃 `seat_` 开头的线路，座位线路不在它的覆盖范围 | `routes/settings.py` | `test_seat_key_never_in_settings_response_and_survives_stale_settings_page` |
| M7 | `_session_turns(…, until=session_ts)` 只取这段对话之前的轮次；完成时间只往前推（`MAX`） | `memory_hub_jobs` | `test_handoff_for_old_session_ignores_a_newer_one` |
| M8 | 同一身份只发队头（`MIN(id)` per `source_ai`），队头没到重试时间时后面的不插队；不同身份互不影响 | `memory_hub_bridge.drain_outbox` | `test_same_identity_is_sent_in_order` |
| L1 | 整趟一个截止时间：逛 ≤ 总时长 − 90 秒，回家总结用剩余时间 | `mcp_outings.run_outing` | `test_summary_is_inside_the_overall_deadline` |
| L2 | lifespan 收尾时先取消并 gather 两个 Memory Hub 任务，再停其他服务 | `main.py` | —（结构改动） |

相关测试（逐文件）：本轮新增/改动的 10 个测试文件 100 passed；自主活动、聊天室、浮窗等回归 139 passed，
唯一失败是原版就失败的 `test_idle_autonomy_actions.py::…friend_visit…`。
你上次本机缺 cv2 导致的导入失败与本仓库无关（requirements 里有 opencv）。
