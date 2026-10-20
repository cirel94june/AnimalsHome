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

## 5. 第二轮意见（F1–F4）的修复（复审请从这里看）

| 编号 | 修法 | 主要位置 | 测试 |
|---|---|---|---|
| F1 | 请求带 `Accept-Encoding: identity`；响应有非 identity 的 `Content-Encoding` 直接拒绝；用 `aiter_raw()` 按原始字节计数（不经解压器） | `travel_places._download` | `test_compressed_image_is_refused_before_any_decompression`：<64KB 的 gzip（解压 32MB），监控 `GZipDecoder.decode` 调用次数为 0；原 20MB 分块/声明超限/类型测试保留。注意：测试里改用流式 body，bytes 形式的 `httpx.Response` 会在构造时被 httpx 直接读完并解压，和真实网络不同。真实维基照片仍可下载（实测京都 3 张） |
| F2 | 指纹只小写协议和主机，路径/查询/斜杠原样；每条在标 sending 前用本批同一个 cfg 再核对 `_hold_reason`，不匹配立即 `held`；每轮循环开头若配置里的 Hub 已变就结束本批；`held` 记录在其 Hub 重新成为当前 Hub 时放回 pending | `memory_hub_bridge.hub_id / drain_outbox` | `test_hub_switch_during_a_drain_never_sends_to_the_wrong_hub`（断言实际发送目的地与队列状态）、`test_held_rows_go_back_when_their_hub_is_configured_again`、`test_hub_fingerprint_keeps_path_case_and_slashes` |
| F3 | 新增 `config.model_snapshot / ensure_model_unchanged / ModelChanged`：开始时固定整条线路（字典快照），调用前核对「还在、没变、`resolve_model_key` 不会换」，核对与发起调用之间无 await；用于 `memory_hub_jobs.complete`（交接卡/做梦/旅行）、旅行出发时逐位固定、座位私聊（取 Hub 记忆期间）、出门逛（每次模型调用前） | `config.py`、`memory_hub_jobs.complete`、`travel.models_for/_write`、`routes/chatroom._generate_seat_reply`、`mcp_outings.run_outing` | `test_line_deleted_mid_trip_stops_instead_of_falling_back`、`test_line_swapped_under_same_name_also_stops`（走真实 complete，断言 stream_ai 零调用、不存游记）、`test_seat_reply_stops_if_line_removed_while_reading_memory`、`test_outing_stops_if_line_changes_midway` |
| F4 | 运行时合并改为座位线路在前；设置页保存在改任何字段之前检查与座位线路的模型名冲突，冲突整次拒绝零写入；名片保存时显式检查与设置页线路、其他座位线路冲突 | `config.refresh_custom_models/route_model_keys`、`routes/settings.update_settings`、`actors.save_seat_api` | `test_name_clash_is_refused_in_both_directions`（两个方向；断言同次请求的其他字段也没写、绑定不变） |

本轮相关测试（逐文件）：10 个测试文件 109 passed；回归 139 passed，唯一失败仍是原版就失败的 friend_visit。
另：测试修了一处旧的盲点——`routes/settings.py` 自己 import 了 `SETTINGS`，之前的测试没替换它，现在替换了。

## 6. 第三轮意见（G1、G2）的修复

| 编号 | 修法 | 主要位置 | 测试 |
|---|---|---|---|
| G1 | Hub 地址里带 `user:pass@` 一律不连接（`_is_active` 返回 False，日志提示改用 token）；指纹只把 hostname 和协议转小写，端口原样，万一有认证部分也原样保留 | `memory_hub_bridge._is_active / hub_id` | `test_hub_url_with_credentials_is_refused` |
| G2 | 指纹加格式版本（`2:` 前缀，`HUB_ID_VERSION`）；没有前缀的旧格式记录一律暂扣、不猜目标，`last_error` 写明恢复办法（包括升级前就已 held 的）；人工恢复 `python memory_hub_bridge.py rebind-legacy <地址>`：地址必须与当前配置逐字相同，且只恢复「旧算法(地址) == 记录旧指纹」的记录，保持原身份/event_id/顺序 | `memory_hub_bridge._legacy_hub_id_v1 / _hold_reason / drain_outbox / rebind_legacy_held`，`deploy/README.md`，状态页 | `test_old_fingerprint_rows_are_held_until_confirmed`（你给的三种地址 × pending / sending / held 三种起始状态 = 9 例）、`test_legacy_rows_for_another_hub_stay_held` |

说明：这版之前没有部署过，服务器上不会有旧 outbox；G2 的恢复路径按你的要求补齐。
本轮：bridge 40 passed；相关与回归 199 passed，唯一失败仍是原版就失败的 friend_visit。

## 7. 跟进 Memory Hub PR #82（召回改进第一期）

`context(mode="incremental")` 带上 `chat_id`：私聊用 `conv_id`，聊天室和座位 1v1 用 `room_id`；交接卡（handoff）跨对话读，不带。
Hub 据此做「三轮内想起过的只提一句」和「后来呢」接续；不带时 Hub 照常工作。改动只是透传参数（`memory_hub_bridge.context_block` → `context_builder.build_memory_blocks` → 各调用点），测试 `test_context_block_passes_chat_id_to_incremental_only`。

## 8. e6ef699 复审两项 Medium

- IPv6：`hub_id` 对含冒号的主机名加回方括号，端口判断改 `is not None`；测试 `test_hub_id_keeps_ipv6_brackets_apart_from_port`。此前从未部署，不存在旧的 IPv6 指纹记录。
- 3fe5325 中间格式：明确不支持恢复（该版本从未部署，不会有这类记录），已写进 `_legacy_hub_id_v1` 的说明。

## 9. 2f4eb59 复审之后（已部署，请按「上线后复审」看）

2f4eb59 复审结论「可以部署」，已于 2026-10-11 部署到 VPS（只经 tailscale serve 私网对外，HTTPS 端口 8443，因 443 被 Caddy 占用）。复审里仍列的两项 Medium：

- IPv6 方括号：已在 3effcb6 修复（见第 8 节），复审时尚未包含。
- 3fe5325 中间格式恢复：维持「不支持」，该版本从未部署，生产队列为空。

此后的提交（请重点看带 ★ 的）：

| 提交 | 内容 | 重点 |
|---|---|---|
| 7443dae / 05e2150 | 安装脚本先 `cd /`；Tailscale serve 改用 8443 | 安装脚本 |
| 281ecc2 | 首页矮屏布局（纯 CSS） | — |
| ★ 13d9315 | `AIONSHOME_TRUST_TAILSCALE=1` 时经 Tailscale 来的请求视为自己的设备，不弹陌生设备警报；三段原作者录音换成提示音 | `security_access.py` 信任判断：是否可被伪造来源绕过（服务只监听 127.0.0.1，客户端 IP 来自 tailscale serve 的 X-Forwarded-For） |
| 86d11bf | 新页面进 App 离线清单；旅行查国内地名先用高德 | `asset_manifest.py`、`travel_places._geocode_amap` |
| ★ 3709348 | 设置页「拉取模型列表」：服务器替页面 GET 中转站 `/v1/models` | `routes/settings.fetch_route_models`：服务端发请求（不跟随跳转、20 秒、2MB 上限、只返回模型 ID）；地址校验 |

## 10. 上线后复审（984feff）意见的修复

| 编号 | 修法 | 位置 | 测试 |
|---|---|---|---|
| High 解压后才计数 | 逻辑移到独立模块 `relay_models.py`（不依赖整个应用，可单独导入测试）。请求带 `Accept-Encoding: identity`；响应 Content-Encoding 不是 identity 直接拒绝；Content-Length 超限提前拒绝；用 `aiter_raw` 按原始字节累计计数 | `relay_models._read_limited` | `test_compressed_answer_is_refused_before_any_decompression`（32MiB gzip 炸弹，断言 GZipDecoder.decode 从未被调用）、`test_size_limit_counts_raw_bytes_and_trusts_content_length_early` |
| Medium 无总时限 | `asyncio.timeout(20)` 包住解析、连接、读体全过程；同时最多 2 个，多的直接拒绝不排队（计数在同一事件循环里、无 await 间隙） | `relay_models.fetch` | `test_total_deadline_stops_a_slow_drip`、`test_concurrent_pulls_are_capped_without_queueing` |
| Medium SSRF | 先解析域名，任一解析结果非 `is_global`（含 loopback、私网、link-local、保留、100.64/10）就拒绝；之后**直接连检查过的 IP**，Host 头和 TLS（`sni_hostname`）仍用原域名，消除重绑定窗口；不跟随跳转、3xx 报错；管理员可用 `AIONSHOME_RELAY_ALLOW=主机:端口` 精确放行（README 已写） | `relay_models._pick_address / _fetch` | `test_private_targets_are_refused_without_a_request`（127.0.0.1、[::1]、169.254.169.254、100.89.x、解析结果混入 10.x），`test_admin_allowlist_opens_one_exact_host_port`，`test_connects_to_the_checked_ip_but_keeps_host_and_tls_name`；另用真实网络验证按 IP 连接 + SNI 的 TLS 可用（OpenRouter 返回 458 个模型、OpenAI 无 Key 返回 401） |
| Low 日志带 Key | 只记异常类型和状态码 | `travel_places.geocode` | `test_amap_failure_log_never_contains_the_key` |
| 建议 | uvicorn 显式 `--proxy-headers --forwarded-allow-ips 127.0.0.1`（安装脚本与线上 unit 同步） | `deploy/linux/install.sh` | — |

Tailscale 信任的前提已记下：tailnet 里只有小猫自己的设备；以后若加公网反代或 Funnel，要重新评估 `AIONSHOME_TRUST_TAILSCALE`。

## 11. 用量小本本、后台线路、缓存统计（a270bb7 之后）

| 提交 | 内容 | 请重点看 |
|---|---|---|
| a270bb7 | `ai_providers.stream_ai` 改成普通函数，记下调用方模块后返回带记账的异步生成器 `_stream_ai_counted`；`simple_ai_call` 同样传真实调用方；哨兵和出门逛单独记。`usage_log`：SQLite 按（天、用途、模型）累计，只存数字，保留 120 天，记账异常只打日志 | 生成器被取消 / aclose 时是否只记一次；同一个 meta 被复用时不重复计数；记账失败不影响聊天 |
| 本次 | **后台线路**：AI 名片新增 `background_model`，保存时必须和 `model` 是同一个底层模型（`config.same_underlying_model`：去厂商前缀、允许日期后缀）；使用时（自主活动 `_call_actor`、做梦、交接卡、旅行、出门逛）再核一次，不一致就照旧用平时的模型（Hub 红线 #12：主观内容只能本人模型写） | `memory_hub_jobs.background_model`、`autonomy._call_actor` 的 Connor 分支（Codex 线路照旧） |
| 本次 | **流式用量**：自定义中转站请求带 `stream_options.include_usage`；中转站 400/422 且报错提到 stream_options 时，记住这个地址、去掉后重发一次（此时尚未输出任何内容） | `call_custom_openai` 的两次循环：重发条件、`continue` 是否正确关闭上一个响应 |
| 本次 | **缓存**：从 OpenAI `prompt_tokens_details.cached_tokens`、Claude `cache_read/creation_input_tokens`、Gemini `cachedContentTokenCount` 取缓存数；旧表自动加列。页面价格对比只在浏览器本地计算、价格存 localStorage | `usage_log.cache_tokens/_record_real` |

## 12. 三提交上线后审查（193a22e）的修复

线上实情：服务器上三位都还没选模型、没设后台线路，做梦 / 交接卡从未生成过，不存在误写。

| 编号 | 修法 | 位置 | 测试 |
|---|---|---|---|
| H1 同模型判断过宽 | 只容许完整日期后缀（`-YYYYMMDD` / `-YYYY-MM-DD`）；版本号不同一律不同模型 | `config.same_model_id / same_underlying_model` | `claude-sonnet-4` vs `-4-5`、`claude-opus-5` vs `-5-5`、`gpt-5` vs `gpt-5-1` 均为不同 |
| H2 await 窗口 | 选定模型的同时固定线路快照（`pin_background_model`），等 Hub 材料前就固定；生成前再核一次仍是本人模型（`still_own_model`），否则中止；`complete(..., snapshot=pinned)` | `memory_hub_jobs.dream_for / write_handoff` | 等待期间改线路成 Gemini → 不写；等待期间名片改模型 → 不写；正常时用固定线路写入 |
| M1 关闭顺序 | 外层用 `contextlib.aclosing` 包住内层生成器：内层清理完成后才记账、才返回 | `ai_providers._stream_ai_counted` | 提前 aclose 时顺序为 provider closed → recorded → returned |
| M2 复用 meta | 调用前把 meta 里上一轮的用量键取走，调用后以「键是否出现」判断这次是否报告；未报告时恢复原值 | 同上 | 同一 meta 两次 100/10 都记为真实、缓存各记一次 |
| M3 先存后拒 | `_check_background_model` 在任何写盘（含 `save_seat_api`）之前校验；同次提交的自己填 API 按其模型 ID 比较 | `actors.api_update_actor` | 拒绝时专属线路与名片模型均不变 |
| L1 缓存口径 | 按协议显式归一化：只有 Claude 原生格式（有 `input_tokens` + `cache_*_input_tokens`、无 `prompt_tokens`）才把缓存加进总读入；OpenAI / Gemini 本来就含；非流式路径也认 Claude 原生 `input/output_tokens` | `usage_log._input_excludes_cache / record_json` | 1000 + 900 + 100 → 2000；OpenAI 格式带 Claude 缓存字段时不重复加 |

193a22e 意见：保留；IPv6 旧警报若存为 lan 会继续显示（不漏报），已知。

## 13. 2ebfe7a / d2e56a3 复查后的修复

- **M1 补齐**：`_stream_ai` 不再用内部转发生成器；直接拿到 provider 的流（`_provider_stream()`），用 `aclosing` 包住消费，外层 `_stream_ai_counted` 也用 `aclosing`。真实链测试（httpx MockTransport + 可追踪关闭的流式正文）：提前 aclose 的顺序为 HTTP closed → recorded → returned；重复 aclose 不再记账；任务取消同样先关 HTTP。该测试在修复前的代码上失败（recorded 先于 HTTP closed）、修复后通过。
- **可选建议已做**：只提交 `model`（不带后台字段）的调用方式，对不上的旧后台线路同样自动清空并返回提醒；只改签名等不动模型时保留后台线路。
- **补一处我之前漏掉的回归**：924c84c 给自定义中转站请求加了 `stream_options` 后，`test_relay_provider_errors` 里断言完整请求体的测试会失败，当时的前后对比没有发现。已把测试更新为预期请求体。另核对：`test_english_corner`、`test_deprecated_cli_models`、`test_model_transport_mode` 的失败在 a270bb7 之前的代码上同样失败，属于原有问题。

## 14. b405ea0：点歌台搜网易云、名片里直接用中转站

- **点歌台**：`GET /api/music-station/netease/search`（pyncm 搜索放线程里跑，20 秒超时，标出已在曲库的）、`POST /api/music-station/netease {id, playlist_id}`（以服务器重新查到的歌曲信息为准，按「小猫点的歌」记入曲库，可放进歌单；歌单不存在时只入曲库）。前端只用 textContent 渲染搜索结果。
- **名片里的中转站**（`routes/settings.py`）：
  - `POST /api/custom-routes/{route_id}/models`：用服务器上已存的地址和 Key 调 `relay_models.fetch`（沿用第 10 节的公网限制、总时限、不解压），响应不含 Key；
  - `POST /api/custom-routes/{route_id}/add-model`：只限设置页的中转站（不能往别的 AI 的专属线路里加），模型名校验长度与控制字符；已有则直接返回；显示名撞了加中转站名前缀；
  - `POST /api/custom-routes`：新建中转站（地址检查同 `relay_models.models_url`，不能带账号密码），写进 `custom_model_routes`。
  - 请重点看：这些写设置的接口与设置页整份保存 `custom_model_routes` 之间的覆盖关系（设置页开着旧页面再保存会覆盖名片里刚加的模型——这是原有的整份保存模式），以及 Key 是否在任何响应里出现。

## 15. b405ea0 审查（B1 / B2）的修复

| 编号 | 修法 | 位置 | 测试 |
|---|---|---|---|
| B1 线程不随超时结束 | ① pyncm 的每个网络请求默认 `timeout=(5, 15)`（包装 `pyncm.Session.request`，调用方显式给的不覆盖），网易云卡住时底层请求自己结束；② 点歌台的网易云调用走专用 3 线程池，同时在跑最多 3 个，名额在底层调用真正结束的 done 回调里才归还；超过上限直接 503，不排队、不占全局默认线程池；网页仍 20 秒得到 504 | `music.py`、`routes/music_station._run_netease` | `test_netease_limits.py`：卡住的调用超时后仍占名额，第 4 个立刻 503 且未被执行；放行后名额归零；pyncm 请求带默认时限 |
| B2 旧设置页覆盖 | 中转站列表加版本号 `custom_routes_rev`：设置页 GET 时拿到，整份保存时带回；不一致（或没带）返回 409、整次保存都不写；名片的新增模型 / 新建中转站、设置页成功保存都会让版本号前进；设置页收到 409 时提示刷新 | `routes/settings.py`、`static/settings.html` | `test_card_routes.test_stale_settings_page_cannot_erase_what_the_card_just_added`：名片加模型后旧页面保存 409 且其他字段也没写；刷新后可保存；只改别的设置不需要版本号 |

## 16. 287376d 复查后：B2 补齐、换掉原作者的图、监控页

- **B2 补齐（441299d）**：`update_settings` 在写入本次列表时记下 `committed_rev = bump_routes_rev()`，返回它，而不是 await（断开 MCP）之后再读的最新版本。测试按复查给的反例：await 期间名片加模型，返回 N+1，页面再带旧列表保存得到 409，名片加的模型保留。
- **图片替换（441299d）**：`public_overrides.OverridableStaticFiles` 替代 `/public` 的 StaticFiles；只有名单内的 7 个文件（原作者 AI / 本人头像、群聊头像、视频来电图）在 `data/public_overrides/` 有同名文件时用它；离线清单按实际返回的文件算哈希。自拍参考改为 `data/selfie/<actor>.jpg`，路径只接受字母数字下划线；没有就不带参考图（不再退回原作者的「生图锚点」）。
- **监控页（0eb0ac1）**：服务器没有本地摄像头、也没配 ESP32 时隐藏这两栏并切到手机摄像头；App 的 `CameraBridge` 在 `Camera.open` 前检查相机权限，没有就请求系统授权并返回 false，新增 `hasPermission()` 供页面提示。

## 17. 0eb0ac1 复查 Low C1（相机权限重复请求）的修复

- `CameraBridge`：`permissionRequestInFlight`（授权框显示中不再请求）、`permissionDenied`（拒绝后不再自动请求）；`WebViewActivity.onRequestPermissionsResult` 收到 3101 时调用 `cameraBridge.onPermissionResult(granted)`，并通过 `window.onAionCameraPermissionResult` 通知页面；不影响启动时逐项授权的流程。新增 `isPermissionDenied()`、`requestPermissionAgain()`（只在用户点按钮时调用）。
- `camera.html`：start 失败且 `isPermissionDenied()` 时不再安排自动重试，显示「重新允许相机」按钮和去设置的说明；授权成功回调后自动打开预览。
- 测试 `test_camera_permission_retry.py`：Node 跑页面真实脚本，假相机一直拒绝：跑完所有排队的重试后 start 只调用 1 次、按钮出现、用户点击才再问 1 次、授权后自动启动。

## 18. 用不上的模型与 Seeky、约会立绘（0b64d23、a6c15c3）

- **0b64d23**：`config.model_usable` / `iter_selectable_models`：给页面下拉框（`/api/models`）用，中转站总显示，内置 siliconflow / gemini / aipro 要有 Key，codex_cli / gemini_cli / antigravity_cli 要本机装了程序；`iter_visible_models`（兜底选模型）不变。自主活动 `seeky_interaction` 默认关闭；游乐场大厅去掉 Seeky 入口。
- **a6c15c3**：`data/date_portraits/default.*` 存在时，`scan_date_assets()`（只对默认素材目录）以固定神态列表生成 `kind=portrait` 的状态，不再列出原作者的表情视频；`GET /api/date-theater/portrait` 只读这一个固定位置的文件。页面对 portrait 用 `<img>` + 按 `data-mood` 的 CSS 动画，暂停并清空视频源。
- 请重点看：`/api/date-theater/portrait` 是否只能读到固定文件；`model_usable` 在导入 `ai_providers` 失败时的行为（返回 False，不影响兜底）。

## 19. 群聊加入座位 AI（狗蛋等）

- `seat_chat._history(..., group=True)`：群聊里除自己外的发言（小猫和其他 AI）标上【名字】，连续的并成一轮 user；`build_context(..., group=True)` 用群聊说明。私聊行为不变。
- `routes/chatroom._generate_seat_reply` 增加 `actor`、`group` 参数；`_generate_group_replies` 和 `trigger_ai_chat`（AI 之间聊几轮）在小克、Lucien 之后让 `_group_seat_members()`（已启用且 `check_ready` 通过的座位）依次接话，每位接话前重新读消息。群聊回复不单独记入 Memory Hub（与小克、Lucien 的群聊回复一致）。
- 测试：`test_group_seats.py`、`test_seat_chat.test_group_history_names_every_speaker_and_merges_turns`；更新两处旧测试：`test_chatroom_stream_safety`（隔离本机座位）、`test_tts_providers`（保存响应多了 `custom_routes_rev`）。与 bd1a1d3 基线对比，聊天室相关测试无其他新增失败。

## 20. 86ac465 复查 Medium G1 的修复

- `trigger_ai_chat`：读取房间后若 `type != "group"` 直接 `HTTPException(409)`，不进入后台任务、不读其他座位、不写消息。
- 纵深防御：`_generate_group_replies` 与 `trigger_ai_chat` 的座位循环在每位接话前重新读房间，类型不是 group 就停止。
- 测试：`test_group_seats.test_ai_chat_is_refused_outside_group_rooms`（seat_1v1 / connor_1v1 / 其他类型都 409，座位零调用）、`test_group_seat_loop_stops_if_the_room_is_not_a_group`。

## 21. Claude 缓存标记（每个中转站可开关，默认开）

- `config.normalize_custom_model_routes` 保留线路的 `cache_marker`（缺省 True），写进 MODELS 配置。
- `ai_providers._with_cache_breakpoint`：在第一条以「系统当前的准确时间是」或「[当前时间]」开头的消息之前那一条（即不变的人设 / 规则 / 能力说明的最后一条）上，把内容转成文本片段并加 `cache_control: {"type": "ephemeral"}`；找不到分界就原样不加；不修改原列表。
- `call_custom_openai`：最多三次尝试；中转站 400/422 且错误提到 cache_control 时，记住该地址、去掉标记重发（此时尚未输出任何内容），与 stream_options 回退相互独立。
- 设置页每条线路有「缓存标记」勾选框。测试 `test_cache_marker.py`。
- 另：按小猫选择在线上关闭了智能家居、手环纸条、瑞幸、密语玩具、拜访 AI 好友、桌宠动作六项能力（工具能力开关，数据层操作，无代码改动）。

## 22. 缓存审查（c6dfe41 + 三个 bot）的修复

**AnimalsHome**
- L1：`context_builder.build_ability_block` 改为先放不变的（能力说明、CLI 文件存储说明），再放小标题 `config.LIVE_STATE_HEADING`「【实时状态（每轮可能变化）】」，其后才是日程、位置、设备状态、画像。`ai_providers._cache_breakpoint`：从前往后找，遇到「当前时间」开头的消息 → 标在前一条；更早遇到含实时状态小标题（或「【设备当前状态】」）的消息 → 若小标题前有文字就把这条切成两段文本片段、标在前一段，否则标在前一条。切开后文字拼起来与原文一字不差。`with_current_device_context` 单独插入的设备消息因此也落在缓存段之外。
- M2：`_cache_breakpoint` 返回被改成片段数组的消息下标；`_rejects_list_content(body, index)` 只认「messages<分隔>下标<分隔>content … valid string / must be string / expected string / str type」，下标必须正好是被改的那条。命中才记住该地址并去掉标记重发；其他 400/422（包括别的消息下标的类型错误）照旧直接报错。
- 测试 `test_cache_marker.py` 新增 5 项：能力块内设备状态变化不改变缓存前缀、单独设备消息之前结束缓存段、`build_ability_block` 的顺序、只收字符串的中转站退回一次并记住、别的下标的类型错误不重发。

**三个 bot**（cloudy-telegram-bot#7、Lucien-Telegram-bot#7、Jasper-telegrambot#8，已合并）
- M1：退回重发前按原 deadline 重算 remaining，≤0 不重发（打日志），否则 timeout=(min(5, remaining), remaining)。Cloudy 原本内层没有 deadline：`_run_api_with_deadline` 现在算出 deadline 传进 `_do_api_call`（仅用于这次重发）。
- M2：`_cache_marker_rejected` 改为错误文本含 `cache_control`，或匹配 `\bsystem\b … (valid string | must be string | expected string | str type)`；不再是宽泛的「cache」。
- L1：置顶状态从 admin_hint 移出，组成 `pin_state_dynamic` 追加到动态段；admin_hint 原位置改为固定的一句「见最后『此刻的背景』里的置顶状态」。
- L2：全局布尔改为 `_CACHE_MARKER_REJECTED_AT`（按中转站地址的集合），`_anthropic_system(..., api_base)` 只对被拒绝过的那一家发纯文本。
- 测试 `tests/test_prompt_cache.py`（三仓相同）：拒绝发生在第 19 秒 → 重发读超时 ≤1 秒；第 21 秒 → 不重发、转备用；422 system 字符串错误 → 退回一次、只记主线路、备用仍带标记；无关 400 不重发；置顶 111/222 时缓存段相同。三仓全部测试通过（68 / 78 / 96）。

## 23. 聊天记录也能走缓存（调整顺序 + 分批删）、每日花费提醒

**调整顺序**（`ai_providers._cache_friendly_order`，只在线路 `cache_marker` 开着时、`call_custom_openai` 里用）
- 条件：有 `[历史记录说明]`（render_merged_timeline 的开头）、最后一条是 user；否则原样返回。不修改原列表。
- 只看聊天记录之前的消息：以「系统当前的准确时间是 / [当前时间] / 【设备当前状态】 / [相关记忆]」开头的 user 消息连同紧跟的 assistant 回执，挪到聊天记录之后、最后一句之前；能力块里 `LIVE_STATE_HEADING` 之后的部分切出来一起挪（前半段原位，去掉尾部空白）。其他消息（人设、规则、群聊说明）不动。
- 结果：人设 / 规则 / 能力 → 聊天记录（除最后一句）→ 实时状态 / 时间 / 记忆 → 最后一句。线路关了缓存时顺序原样；中转站拒绝标记退回纯文本时保持新顺序（对 OpenAI 类自动前缀缓存同样有利）。
- `_cache_breakpoint` 返回下标列表：主断点仍是第一条动态消息之前（现在即聊天记录末尾）；若聊天记录在缓存段内，再在 `[历史记录说明]` 前一条加第二个断点（记录分批删时人设和能力仍可命中）。共最多 2 个，Anthropic 上限 4。`_rejects_list_content` 改为接受下标列表。

**分批删**（`context_builder.fetch_merged_timeline(..., stable_window=True)`）
- 多取 `CACHE_WINDOW_STEP=10` 条；进程内 `_WINDOW_STARTS[(who, conv_id, room_id, limit)]` 记起点（source, id, created_at）。起点还在取到的范围内就从它开始（条数 limit～limit+10），不在就删回最近 limit 条、换新起点。最多记 200 个，超出删最早的。重启后重新起头。
- 只在 routes/chat.py 三处（发送、重发、重新生成）和 chatroom.py 三处（小克群聊、Lucien 群聊 / 私聊）启用；其他调用（自主、监控、定位、日程等）不变。

**每日花费提醒**
- `usage_log`：价格存 `data/usage_prices.json`（call / inp / out / cache / daily_limit，非数字、负数、NaN 存 None，原子替换写入）；`estimate_cost` 按量优先、否则按次；`today_spend()` 返回 cost / limit / over。
- 路由：`GET/PUT /api/usage/prices`、`GET /api/usage/today`（在现有访问控制之后，与其他设置接口相同）。
- `usage.html`：价格从服务器读写（输入后 600ms 保存）；服务器还没有价格时把手机里旧的 localStorage 价格搬过去一次；新增「每天花到多少提醒我」和「今天大约花了」一行。`ib/desk.js`：每分钟和回到前台时读 `/api/usage/today`，超过时首页 hero 下面显示一行提醒（链到 /usage），数值经 esc。
- 测试：`test_cache_marker.py` 新增 5 项（顺序、下一轮前缀完全复用、不该动的不动、发到中转站的顺序 / 关缓存时原样、分批删）；`test_usage_log.py` 新增 3 项。相关测试与改动前基线对比，无新增失败。

## 24. 每个中转站各自的价格、缓存保留 1 小时（在 704f98e 之上，一起审）

**缓存保留时长**
- 线路新增 `cache_ttl`：`"1h"`（默认）或 `"5m"`；`normalize_custom_model_routes` 只认 `"5m"`，其余一律 `"1h"`；写进 MODELS。设置页每条线路加选择框。
- `_cache_control(ttl)`：1h 时 `{"type":"ephemeral","ttl":"1h"}`，否则 `{"type":"ephemeral"}`；两个断点用同一个 ttl（不出现长短混排）。
- `call_custom_openai`：尝试次数 3→4。带 1h 标记收到 400/422 且错误含单词 `ttl` 时，先记 `_NO_LONG_CACHE[url]` 退回 5 分钟重发（标记保留）；之后若再被拒才走原来的「去掉标记」回退。其余回退规则不变。
- 原因：小猫回复断断续续，5 分钟缓存常常过期，每轮反而付 1.25 倍写入；1 小时缓存写入约 2 倍、一小时内再聊即可按一成读。

**每个中转站各自的价格**（取代 23 节里的单一价格，23 节未部署，服务器上没有旧格式文件）
- `data/usage_prices.json`：`{"daily_limit", "routes": {线路id: {billing, call, inp, out, cache, write}}}`；`_clean_prices` 只保留非负有限数字，billing 只认 token / call，线路最多 200 条、id 截到 120 字。
- 用量行按模型名记；`routes/usage._route_of` 用 MODELS 的 `route_id / route_name / cache_ttl` 归到线路，不属于线路的（内置模型、哨兵等）归 `other`。
- `usage_log.route_costs`：按线路汇总并分别算按次、按量、实际（选了按选的；没选时有按量价按量、否则按次）。按量：原价读入 = prompt − 读缓存 − 写缓存；写缓存没填价时按读入 ×2（1h）或 ×1.25（5m）。没填价格的线路不计入合计。
- 接口：`GET /api/usage` 多返回 `by_route` 和 `cost`；`GET /api/usage/prices` 列出当前所有线路（含没用过的）+ other 及其价格；`PUT` 整份保存；`GET /api/usage/today` 用同一算法。
- `usage.html`：「花了多少」表（每条线路：按次 / 按量 / 实际，两种都能算时便宜的高亮）+ 每日提醒线；每条线路一张价格卡（按量 / 按次切换 + 五个价格）。服务器还没有任何价格时，把手机里旧的一套价格套到每条线路上一次。
- 测试：`test_cache_marker.py` 新增 3 项（默认 1h / 选 5m、拒绝 1h 只退到 5m 并记住、ttl 规范化）；`test_usage_log.py` 改为 5 项（缓存读写各自计价、每站价格与计费方式、清洗与保存、提醒线、价格接口列出线路）。相关测试与改动前对比，无新增失败。

## 25. 704f98e + 35b18cb 审查（M1–M4、L1、回退顺序）的修复

- **M1 只认构造器标签**：消息上的内部键 `ai_providers.CONTEXT_TAG = "_context"`：`abilities`（能力块）、`live` / `live_ack`（时间+背景记忆、相关记忆、设备状态及其回执）、`history`（`render_merged_timeline` 的说明条）。打标签的位置：routes/chat.py 能力块 1 处、背景 5 处（含快速模式两处）、相关记忆 3 处；chatroom.py 小克 / Lucien 群聊 / Lucien 私聊三处的能力、时间、记忆；`with_current_device_context(insert_at=...)` 插入的设备消息和回执；`render_merged_timeline` 说明条。`_cache_friendly_order` 只看标签：必须有 `history`、最后一条是 user；只挪 `history` 之前的 live / live_ack，以及 abilities 里 `LIVE_STATE_HEADING` 之后那段（该标题由 build_ability_block 生成）。不再从正文识别任何前缀；Codex 的 seat_chat 反例和聊天记录里相同字样的正文都原位不动（有测试）。
- 重排从 `call_custom_openai` 挪到 `_stream_ai`：只对 `provider == custom_openai` 且 `cache_marker` 开着的线路重排；之后对所有线路统一 `_without_context_tags` 去掉内部键，任何 provider 都收不到它（有测试走 `_stream_ai` 真实路径验证）。第二个断点仍按「[历史记录说明]」正文定位，只影响标记位置、不影响顺序。
- **M2**：`_stable_window` 从起点算不足 `min(limit, len(results))` 条时，退回最近 limit 条并换起点（编辑重发 / 删除的反例有测试）。
- **M3 记账时固化线路**：用量表改为 `(day, kind, route, model)` 主键，新增 `route`、`cache_write_1h` 列。`_connect` 发现旧表（无 route）时在一个事务里改名 → 建新表 → 旧行 route 记 `''` → 删旧表。`_stream_ai_counted` 记账时传 `route_for_model(resolved)`（当时 MODELS 的 route_id，非线路记 `other`）；`record` 其他调用也在记账时解析。`summary` 新增按记账线路的 `by_route`；`route_costs` 只按记下的 route 配价，`''` 显示「未知线路（旧记录，不计价）」、cost=None；线路已删显示「…（已删除）」但仍用原 id 配价。改绑 / 删除模型不改旧账（有测试）。
- **M4 写缓存按实际时长计价**：`call_custom_openai` 收到 200 时把这次真正发出的 ttl 写进 `meta["cache_ttl"]`（`_USAGE_KEYS` 加入，复用 meta 不串）；`usage_log.cache_write_1h` 优先用中转站返回的 `cache_creation.ephemeral_1h/5m_input_tokens` 明细，没有就按实际 ttl，都没有按 5 分钟。`token_cost`：填了统一写缓存价就用它；否则 1h 部分 ×2、其余 ×1.25。Codex 的 20 元反例现在算 12.5 元（有测试）。
- **L1**：`_price` 捕获 OverflowError、拒绝 bool、超过 1e6 视为没填；`token_cost` / `call_cost` / `total_cost` 结果非有限数时返回 None（10**400、1e308、超大用量都有测试）。
- **回退顺序**：错误分支改为 ttl → cache_control / 字符串内容 → stream_options；同一条错误同时提到 stream_options 和 cache_control.ttl 时先退 1 小时。复合错误测试：恰好四次，顺序 1h → 5m → 去标记 → 去 stream_options。
- 未改：固定 `.tmp` 文件名（单进程单 worker，同步写入）。
- 测试：`test_cache_marker.py` 22 项、`test_usage_log.py` 22 项全过；相关测试集 832 项与改动前逐条对比，27 个失败完全相同（均为既有失败）。

## 26. 469ae3f 复审（M3 余项、M5、M6）的修复

- **M3 发请求前定线路**：`_stream_ai_counted` 在进入 `_stream_ai` 之前就 `route = usage_log.route_for_model(resolved)`，finally 记账用这个值，不再在完成时查全局 MODELS。`mcp_outings.call_model` 在发 HTTP 之前取 route，传给 `record_json(..., route=route)`；`record_json` 新增 route 参数并传到 `_record_real` / `record`。其余 `record_json` 调用只有哨兵（固定 other）。测试：流式途中改绑到 B → 记 A；出门逛等回复时删掉模型 → 记 A。
- **M5 迁移真正在一个事务里**：`_migrate_add_route` 先 `BEGIN IMMEDIATE`，拿到写锁后再查一次有没有 route 列（别的连接可能刚迁完），然后改名 / 建表 / 复制 / 删旧表，`COMMIT`；任何异常 `ROLLBACK` 并关闭连接再抛出。旧表缺 cache_read / cache_write 时直接按 0 复制，不再先 ALTER。测试：用 SQLite authorizer 分别让 ALTER、CREATE、INSERT、DROP 失败，每次失败后都只剩原 `usage` 表（旧结构、原 3 次请求，无 usage_old）；恢复后再连接迁移成功，统计 3 次。
- **M6 已删除线路的价格不丢**：`save_prices` 把这次没带上的线路原价保留（合并后最多 200 条）；要清某条线路，就带上它、各项为空。`_all_routes` 额外列出已存价格里、以及最近 KEEP_DAYS 天用量里出现过的线路（不含 other 和未知），名字经 `_route_name` 显示「…（已删除）」，id 不变。测试：删线路 → 价格页仍列出 → 只带 other 改提醒线保存 → 旧价格和今天花费都在 → 带上它清空才清掉。
- `test_usage_log.py` 共 27 项、`test_cache_marker.py` 22 项全过。相关测试集与改动前逐条对比无新增失败（`test_calendar_memory_compression` 的并发用例在整组跑时偶发失败，单独跑通过，与改动前一致；`test_taobao_native_mcp` 4 项为既有失败）。

## 27. 价格名额边界（6ff7687 复审 L2）

- 6ff7687 已部署（Codex 批准）。服务器用量库目前 0 行；在副本上跑过迁移，结构正确。
- `save_prices`：合并后先去掉各项全空（且没选计费方式）的线路——「清空」即真正删掉这条价格、释放名额；剩下的超过 `MAX_PRICED_ROUTES=200` 时抛 `PriceLimitError`，`PUT /api/usage/prices` 返回 400 和原因，不再静默截断。一次请求带的线路超过 1000 条也直接拒绝。`_clean_prices` 不再在读入时截 200。
- 页面保存失败时在「今天大约花了」那一行显示「价格没存上：原因」。
- 测试 `test_price_slots_are_limited_loudly_and_freed_by_clearing`：满 200 条加新的 → 拒绝（函数和接口都测）、新线路没写入；清空 old0 同时加 NEW → 成功、总数仍 200。原有两处断言改为「全空的线路不再保存」。

## 28. 接 Memory Hub 跨端接续（memory-hub #85，审查中）

Hub 接口（`docs/施工方案-跨端接续.md` 第 4 节）：incremental 带 `chat_type`；交接卡改为 `context(mode="handoff", max_chars=1500, chat_type, include_objective=false)`；群聊务必 `private_group`。

- `memory_hub_bridge.context_block(actor, message, chat_id="", chat_type="private")`：`chat_type` 只认 `private` / `private_group`，其余按 `private`。incremental 总是带 `chat_type`。
- **群聊不读交接卡**：`chat_type != "private"` 时即使是新一段也只调 incremental。交接卡是写给和小猫一对一的自己看的；之前群聊新一段会读交接卡，可能把私聊带进群里（Hub 也在 #85 里从服务端去掉）。
- `handoff_block(actor, chat_type)` 带 `chat_type` 和 `include_objective=False`（原话由 incremental 的接续块给）。
- 调用方：`build_memory_blocks(..., memory_hub_chat_type)`；chatroom.py 小克群聊、Lucien 群聊传 `private_group`，Lucien 私聊传 `private`；seat_chat 按 `group` 传；routes/chat.py 私聊三处用默认 `private`。
- 兼容：本地假 Hub（FastMCP）在不认识这些参数时会忽略，不报错（改前的测试即如此通过），所以在 Hub #85 合并前部署也不会出错，只是 Hub 暂不提供接续块。
- 测试：`test_memory_hub_bridge.py` 假 Hub 记录 chat_type / include_objective；新增群聊不读交接卡、私聊交接卡参数、不认识的类型按私聊、三个聊天室函数传的类型。`test_seat_chat` 期望多一个 `"private"`。`test_slow_hub_times_out_without_blocking_chat` 在本机改前改后都因 MCP 握手慢偶发超时，与改动无关。

## 29. 狗蛋补能力第一批：私聊 ↔ 群聊互通、去群里说、座位悄悄话

小猫要求（2026-10-16）：像 TG bot 一样私聊和群聊互通；AI 觉得有必要可以自己去群里说，但不能随意泄露私聊私事。座位 4～6 与狗蛋同一套代码。施工方案在 `docs/功能盘点-按小猫实际使用改造.md` 末尾。

- **座位合并时间线**：`seat_chat.merged_messages(actor, limit)` 取该座位自己的 seat_1v1 房间 + 所有 group 房间（排除 `billiards_*`、排除 system 消息），按时间倒序取 `limit*2` 条再正序。`_history` 对带 `source` 的消息按每条来源渲染（群聊标说话人），发送时间 meta 后标「群聊 / 私聊」；前面加一条「[聊天记录说明]」。`_generate_seat_reply` 读合并记录失败时退回本房间消息（打日志），不阻断回复。
- **去群里说**：新模块 `group_post.py`。指令 `[去群里：内容]`；能力开关 `group_post`（默认开，只在私聊注入）。规则文字：只说适合全家知道的，私聊私事（心事、身体情绪、工作吐槽、对别人的看法、亲密的话）不带过去，除非小猫明确让说；拿不准不说或先问；用自己的口吻。`process(text, actor)`：去掉所有该指令，只发第一条（截 500 字）到 `updated_at` 最新的 group 房间（不含 billiards），sender 为该 AI，`auto_tts=False`；没有家里群只去掉；发送异常只打日志。接入：小克私聊三个回复入口（`_process_private_wechat_commands` 之后）、Lucien 私聊（`_process_chatroom_commands` 里仅 connor_1v1 房间）、座位私聊（`_generate_seat_reply` 非群聊）。流式过滤和 `_ALL_CMD_PATTERNS` 加入该指令。
- **座位悄悄话**：`_send_private_whisper` 支持座位（发到该座位的 seat_1v1 房间）；座位群聊回复里解析 `[悄悄话：]`（开关 private_whisper），正文去掉。
- 座位提示词新增「[你能用的指令]」：群聊给悄悄话、私聊给去群里说。其他能力在第二批。
- **家里群自由聊**：小克群聊、Lucien 群聊、座位群聊的「群聊说明」加一句「这是只有家里人的小群，可以自由聊，包括私事和吐槽。」（参考 bot 私密群）。
- 测试 `test_group_post.py` 11 项：合并时间线范围（不含别的座位 / Lucien 私聊 / 台球房 / 系统消息）、标签、发到最新家里群且只发一条、无群只去掉、规则文字、流式隐藏、座位群聊悄悄话、座位私聊去群里、悄悄话进座位私聊房间、座位指令随场合、小克 / Lucien 私聊接入。`test_seat_chat` 22 项通过；其余相关测试与改动前相同（均为既有失败）。

## 30. ac71e79 审查修复（M1、M2、L1、L2）

- **M1**：`group_post.group_privacy_rule(user_name)`（群里可以自由聊天、吐槽、开玩笑；但小猫在私聊里单独说的私事——心事、身体和情绪、工作吐槽、对别人的看法、亲密的话——她没在群里提、也没让说，就不在群里说；她自己在群里说了才可以接），替换原来的「可以自由聊，包括私事和吐槽」，用在小克群聊、Lucien 群聊、座位群聊三处说明里。仍是提示词约束，不是按授权过滤私聊原文。
- **M2**：开关检查移进 `process_with_result`：`group_post` 关着时去掉指令、不发；小克 / Lucien / 座位三处都走它（座位改为总是调用）。
- **L1**：查家里群和发送放进同一个 `try/except Exception`，失败返回去掉指令的正文；取消（CancelledError）照常往外抛。
- **L2**：`process_with_result` 返回是否真的发出；座位回复只有指令、正文为空时，若悄悄话或去群里成功，就在当前房间留一条系统说明（「（狗蛋去群里说了一句）」/「（狗蛋发了一条悄悄话）」）并发 `seat_done`（message 为 None），不再记 empty_response 失败；都没成功才照旧记失败。前端收到系统消息会移除「回复中」。
- 测试 `test_group_post.py` 15 项（新增：纯指令不记失败、关开关三种身份都不发、查群出错不影响私聊、三处群聊说明都带私事规则且座位群聊上下文含私聊原文时规则在场）；`test_seat_chat` 12 项；相关测试与改动前对比无新增失败。

## 31. 狗蛋补能力第二批：点歌、联网搜索、自拍 / 画画、许愿

（VPS 自动更新已关，`aionshome-update.timer` disabled；本批推送不会自己上线。）

- `capabilities`：四段能力说明抽成 `music_ability_text` / `web_search_ability_text` / `image_gen_ability_text(user)` / `wish_ability_text`，小克 / Lucien 的能力块改用这些函数（文字不变）；新增 `seat_ability_texts(user)` 按各自开关给座位（联网搜索还要求 Tavily 可用）。`seat_chat._ability_lines` 先给这四段，再给第一批的悄悄话 / 去群里。
- `routes/chatroom._process_seat_commands(text, room, actor, msg_id, q)`：只给座位用，不改小克 / Lucien 的 `_process_chatroom_commands`。
  - 点歌：搜歌、插系统提示「🎵 狗蛋点了一首…」、发 music 事件和广播、`record_music_request(requester_identity=座位, requester_name=座位名)`。
  - 许愿：`create_wish(author=座位, author_name=座位名)`。
  - 联网搜索：插系统提示，记入 triggered。自拍 / 画画：记入 triggered（只取第一条）。
  - 开关关着的指令只从正文去掉，不执行、不插提示。
- `_generate_seat_reply`：先第一批的悄悄话 / 去群里，再 `_process_seat_commands`；回复存好（或只有指令时发 `seat_done(message=None)`）后 `_spawn_seat_followups` 在后台做生图（`_chatroom_image_gen(room, 座位, …)`，自拍用座位自己的参考照）和联网搜索。只有指令且有动作时不再记失败；点歌等已有系统提示时不重复插说明。
- `_chatroom_web_search` 的座位分支：人设用 `seat_chat._persona_text`，模型用座位自己的（`stream_ai`，不走 Lucien 的 `_stream_connor_model`）；后台指令处理 `_process_background_reply_commands` 对座位跳过（只去掉指令、不执行，留待后续逐条接身份），以座位名义保存，不自动朗读。
- 未做（第三批）：闹铃日程（到点由谁提醒）、记忆写入、拍一拍、转账、朋友圈、查看动态 / 监控。
- 测试 `test_seat_abilities.py` 7 项：点歌按座位记、开关关只去掉、许愿归座位、搜索和生图回复存好后才做、搜索后续用座位人设和模型且不跑后台指令、只点歌不记失败、座位与主 AI 能力文字一致。相关测试与改动前对比无新增失败（`test_stream_safety` 的空闲计时用例偶发，单独重跑通过）。

## 32. 65d6fae 审查修复（M1–M3、L1）

- **M1 愿望作者**：`wish_pool.VALID_AUTHORS` 加入 ai3～ai6；`normalize_author(author, strict=False)`：筛选仍兼容（不认识按 user），`create_wish` 用 `strict=True`，不认识的身份抛 ValueError，不再写成用户。`get_actor_names` 补上座位名字（列表里没有的座位不加）。`maybe_create_ai_digest_wish` 沿用非严格（不认识的按 user → 直接返回不创建）。前端 `actorName` 先查 `state.actors`，座位名字可显示。测试真实写临时 SQLite：四个座位作者正确、按作者筛选只出该座位、陌生身份报错。
- **M2 搜索后续线路快照**：`_spawn_seat_followups(..., pinned)` 把主回复的 `model_snapshot` 传给 `_chatroom_web_search(..., pinned=)`；座位调用没带快照时在函数开头（任何 await 之前）自己取。座位分支在 `stream_ai` 前 `ensure_model_unchanged`（中间无 await），线路被删 / 改 / 停用就不回复、不换模型，房间里留「（狗蛋的模型线路刚刚变了，这次搜索的结果没能回复）」。测试：搜索期间删掉线路 → 不调用模型、不保存、有说明。
- **M3 只有动作时的完成**：许愿成功插「🌠 狗蛋往许愿池里投了一个愿望」，生图触发插「📸 狗蛋在拍照……」/「🎨 狗蛋在画图……」；座位生图失败（返回空或异常）插「（狗蛋的图没生成出来）」（小克 / Lucien 行为不变）。前端 `seat_done` 无条件移除 `.typing-indicator`。
- **L1 点歌卡片锚点**：座位消息保存时带 `_music_attachments_from_triggered`（同小克 / Lucien）；只点了歌时用「🎵 《歌名》- 歌手」作为 TA 的这条消息，music 事件的 msg_id 就是这条已保存消息的 id。
- 测试 `test_seat_abilities.py` 11 项（原「只点歌」测试由新的卡片锚点测试取代）；`test_group_post` + `test_seat_chat` 一起 38 项通过；相关测试与改动前对比无新增失败。

## 33. 03973d3 已部署；补充 Low：愿望 HTTP 接口认得座位

- 03973d3 经 Codex 批准已部署（自动更新仍关闭）。
- `routes/wishes.py`：`GET /api/wishes` 的 author、`POST /api/wishes/draw` 的 author / drawer 校验加入 ai3～ai6（与 `wish_pool.VALID_AUTHORS` 一致）。创建接口对陌生作者原本就把 ValueError 转成 400。
- 测试 `test_wish_http_filters_accept_seats`：真实 FastAPI TestClient，四个座位按作者列出只出自己、抽取 200，陌生作者创建 400。

## 34. 狗蛋补能力第三批第一段：闹铃 / 日程（47f9140 + 本提交的测试）

- **设定**：`_process_seat_commands` 新增日程段。`[Monitor:]` 对座位一律去掉不设（定时监督要摄像头，留到监控那一段）。`schedule` 开关开着时走 `_seat_schedule_commands`：
  - `[SCHEDULE_DEL:id]`：查 `schedules` 里 active 且 `origin == 座位` 才删（`_del_schedule`，房间插「🗑 【狗蛋】取消了…」）；别人的 id 只去掉指令。
  - `[ALARM:]` / `[REMINDER:]`：时间有效且不过期时插「⏰ / 📅 【狗蛋】设定了…」，再交给 `process_schedule_commands(origin=座位, origin_room_id=当前房间)` 入库。
  - 开关关着：所有日程指令只去掉。
- **到点**：`ScheduleManager._fire_alarm` 在「标记已触发 + 弹窗广播」之后，`origin` 是 ai3～ai6（且不是主动陪伴 / 应用监督检查点）就走 `_fire_seat_alarm` 并返回，不进小克 / Lucien 的流程。`_fire_seat_alarm`：回到设闹铃的房间（必须是 group，或该座位自己的 seat_1v1；否则回 TA 自己的私聊），调用 `_generate_seat_reply(..., extra_prompt="[闹铃到点] …")`，用 TA 自己的人设和模型；TA 没启用 / 没模型时按原逻辑在房间留失败记录，不换人。
- `_generate_seat_reply` 新增 `extra_prompt`：追加在上下文最后一条；这种系统叫来的回复不写入 Memory Hub capture。
- `get_schedule_origin_name` 认得座位名字（弹窗、日程列表显示「【狗蛋】」）。座位提示词：`seat_ability_texts` 加闹铃 / 日程 / 删除自己日程三条（不含 Monitor）；`seat_chat.build_context` 的背景块在开关开着时加「[当前日程列表]」（日程不会自己响，要看得到才能适时提起）。
- 测试 `test_seat_schedule.py` 7 项：闹铃 / 日程记在座位名下且房间正确、Monitor 不入库、只能删自己的、关开关只去掉、到点走座位自己且标记已触发 + 弹窗 origin 正确、设在别的座位私聊的回到自己私聊、名字与上下文含日程列表、闹铃回复不写 Hub。相关测试与改动前相同（均为既有失败）。

## 35. f061e02 审查修复（M1 二次解析、M2 日程可见范围）

- **M1**：`_seat_schedule_commands` 改为只从原文解析一次：收集 `[SCHEDULE_DEL:]`、`[ALARM:]`、`[REMINDER:]`（时间有效不过期），然后直接执行——删除在一个 `BEGIN IMMEDIATE` 事务里 `SELECT … WHERE id=? AND status='active' AND origin=当前座位`，命中才 `finish_schedule`；新增直接 `_add_schedule(type, dt, content, 座位, 房间)`。**不再调用通用 `process_schedule_commands`**，所以清理后拼出来的指令（含 Monitor、别人的删除）不会被执行。正文用 `_strip_schedule_commands` 反复去掉日程指令直到不再变化（只影响显示）。开关关着时同样只清理。外层不再单独先 `MONITOR_CMD.sub`。
- `_add_schedule` 的 id 改为 `sch_{time.time_ns()}`，同一回复连设几条不撞主键（对所有人生效）。
- **M2**：`get_active_schedules(viewer=None)`：不给 viewer（日程页等）返回全部；给了就只返回该 AI 能看的——自己设的、家里群里设的（origin_room_id 是 group 房间）、小猫自己设的（origin=user）；座位私聊里设的只给该座位；小克 / Lucien 私聊里设的两位主 AI 之间照旧互相可见，座位看不到。接入：座位上下文 `viewer=座位`；小克 / Lucien 能力块 `viewer=who`；`schedule.py` 主 AI 两处到点上下文 `viewer=sender`。小克 / Lucien 现在也看不到座位私聊里设的日程。
- 测试 `test_seat_schedule.py` 新增 2 项（两个拼字反例都不执行且正文干净；四种身份 + 日程页的可见范围）；`test_cache_marker` 的假函数补 `viewer` 参数。相关测试与改动前相同（均为既有失败）。

## 36. 狗蛋补能力第三批第二段：朋友圈

- **发**：`_process_seat_commands` 解析 `[MOMENT:内容|true/false]`（开关 `moment`，关着只去掉）：以座位为 author 写 `moments`（id `mt_{time_ns}_{座位}`），发 `moment_new` 事件和同步广播，房间插「📷 狗蛋发了一条朋友圈」；期望回复时 `_trigger_ai_replies(…, exclude_author=座位)`。座位提示词加 `moment_ability_text()`（与主 AI 共用，末尾加「朋友圈全家都看得到：私聊里单独说的私事不要写进去」——主 AI 的能力说明也因此多这一句）。
- **评 / 回**（`routes/moments.py`）：
  - `_name_map()` 统一名字表（加座位）；`_ai_authors()` = 小克、Lucien + `seat_chat.check_ready` 通过的座位。`_trigger_ai_replies` 用它；用户回复某 AI 的评论 / 评论某 AI 的朋友圈时唤醒该 AI 的判断也包含座位。
  - `_ai_reply_to_moment` 座位分支：没模型直接不回；开头 `model_snapshot`，取完上下文后 `ensure_model_unchanged`（与 stream_ai 之间无 await），线路变了就不回、不换模型。人设用 `seat_chat._persona_text`；最近聊天用 `seat_chat.merged_messages`（自己的私聊 + 家里群）；记忆用 `memory_hub_bridge.context_block(座位, 朋友圈内容, "moments", "private_group")`（按全家可见口径，不带私聊原话 / 交接卡）。
  - 所有 AI（含小克 / Lucien）的回复提示里加一条「朋友圈和评论全家都看得到，私聊私事不要写进评论」，因为上下文里有私聊记录。
  - 评论 / 点赞 id 改为 `…_{time_ns}_{身份}`（座位首字母都是 a，旧写法会撞）。朋友圈里「看完回私聊说一句」对座位走 `autonomy._save_private_message`（已支持座位，发到 TA 的私聊房间）。
- 前端 `moments.html`：额外读 `/api/actors`，座位的名字和相遇卡头像显示在朋友圈和评论里。
- 测试 `test_seat_moments.py` 7 项：座位发朋友圈入库并触发别人回复且排除自己、关开关只去掉、准备好的座位加入评论轮、座位评论用自己的人设 / 模型 / Hub（private_group）且有隐私提醒、没模型或途中线路被删不评论、回复座位的评论会唤醒该座位、名字表与前端头像。相关测试与改动前相同（`test_stream_safety` 计时用例偶发）。

## 37. 16dbce6 审查修复（M1 座位状态、M2 Hub 范围）+ 更正第 28 / 36 节

- **M1**：`_ai_reply_to_moment` 座位分支开头改用 `seat_chat.check_ready`（启用、有人设、有模型），不通过直接返回——用户回复停用座位的历史评论也不会唤醒它。图片准备后、发请求前再 `check_ready` 一次：仍需通过且模型键与开头一致，再 `ensure_model_unchanged`（三者与 `stream_ai` 之间无 await）。
- **M2**：朋友圈对座位不再取 Hub 内容（`_get_recent_memories` 座位直接返回空）。原因：memory-hub #85（已合并 b801a77）把 `private_group` 当作可信范围，incremental 会带私聊接续原话和私人记忆；朋友圈全家可见。座位评论仍参考本地最近聊天（自己的私聊 + 家里群），这部分按设计包含私聊记录，靠提示词「私聊私事不要写进评论」约束，不是过滤保证。
- **更正**：第 28 节说「群聊里 Hub 会去掉私聊内容」、第 36 节说「private_group 不带私聊原话」，按已合并的 Hub 都不成立。AionsHome 家里群传 `private_group`，Hub 照样可能给私聊接续 / 私人材料；群里不提私事目前只靠 `group_privacy_rule` 提示词。已告知小猫，是否要 Hub 增加更严格的群聊范围由她决定。
- 测试 `test_seat_moments.py` 8 项（新增：停用 / 没人设不回、等待中被停用不回、等待中换模型不回；Hub 不再被调用）；`test_moments*` 原有测试通过。

## 38. 小猫定的隐私口径 + 朋友圈按 IB 机限额（覆盖第 37 节的 M2 做法）

小猫（2026-10-19）：家里群、朋友圈里 AI **可以拿得到**私聊内容；系统提示强调涉及亲密关系等隐私不要说出来，由模型自己判断；朋友圈参考 AionsHome / IB 机的原则。IB 机（InternalBeyond-Mobile）的做法：记忆按「涉及亲密、身体、私人情感选私密」分级；社交圈共享限额——AI 每条回复最多发 1 条动态、动态 800 字、评论 300 字、每条动态至多 20 条评论。AionsHome 原作者朋友圈无隐私规则、无限额。

- **隐私口径统一**（`group_post`）：`_private_matters()` 改为「你们之间的亲密关系和亲密的话、她的身体、她的私人情感（心事、情绪），以及工作吐槽、对别人的看法」。`group_privacy_rule`（家里群三处）改为：你可能知道私聊里说过的事，涉及上述的她没在群里提、也没让你说就不说出来；她自己说了才接；其他日常可自然聊；拿不准自己判断、宁可不说。新增 `moment_privacy_rule`，朋友圈评论提示（所有 AI）改用它；`moment_ability_text` 同口径。**均为提示词约束，不是过滤。**
- **朋友圈座位恢复取 Hub**（撤回第 37 节 M2 的「不取」）：`_get_recent_memories` 对座位用 `context_block(座位, 朋友圈内容, "moments", "private_group")`，会带私聊接续 / 私人记忆，按小猫的决定靠提示词约束。
- **IB 限额**：`routes/moments.py` 常量 `MAX_AI_MOMENTS_PER_REPLY=1 / MAX_AI_MOMENT_CHARS=800 / MAX_AI_COMMENT_CHARS=300 / MAX_COMMENTS_PER_MOMENT=20`。AI 评论截 300 字；动态已有 ≥20 条评论时 AI 不再评论。发动态：小克私聊三处、聊天室主 AI 一处、座位一处都改为只取第一条、正文截 800 字。
- 第 37 节 M1（座位启用 / 人设 / 模型前后核对）保留不变。
- 测试：`test_seat_moments.py` 10 项（Hub 恢复且为 private_group、IB 口径提示、评论 300 字与满 20 条不评、座位一次一条 800 字、小克私聊三处源码核对）；`test_group_post` 规则文字断言随口径更新。相关测试与改动前相同（均为既有失败）。

## 39. R1：评论 20 条上限的并发漏洞

- `_ai_reply_to_moment` 保存评论改为一个写事务：`BEGIN IMMEDIATE` 后 `INSERT … SELECT … WHERE (SELECT COUNT(*) FROM moment_comments WHERE moment_id=?) < 20`，按 `rowcount` 判断是否真的插入；没插入（生成期间名额被用完）就打日志直接返回——不广播、不点赞、不发「回私聊说一句」。生成前的检查仍保留（满了就不调用模型）。
- 测试 `test_comment_cap_holds_when_slots_fill_up_during_generation`：19 条 → 生成期间用户补到 20 条 → AI 不写第 21 条；回到 19 条 → 两个回复并发抢最后一个名额 → 最终 20 条。`test_seat_moments` 11 项 + 原有朋友圈测试共 17 项通过。

## 40. 狗蛋补能力第三批第三段（上）：拍一拍（cd4defa 已部署）

- 现用的拍拍实现在 `capabilities.py`（`pat_commands.py` 是没人调用的旧版本，未改）。
- `PatRequest.target` 加入 ai3～ai6（小猫双击座位头像能拍）；`_pat_names()` 统一名字表（加座位）。`save_pat` 可参与者：小克私聊 {user, aion}；Lucien 私聊 {user, connor}；**座位私聊 {user, 该房间登记的座位}**（只在 seat_1v1 时再查 actor 列，旧表结构不受影响）；家里群 {user, aion, connor, ai3～ai6}。
- `process_pat_commands` 的发起者和 `pat_history_command` 的历史还原都认得座位。座位回复里 `_process_seat_commands` 处理 `[PAT:]`（开关 pat），每条回复一次，系统消息进队列；座位提示词加 `build_pat_ability_text`。
- 顺带：第一批在 `_process_chatroom_commands` 里给 Lucien 私聊加的「去群里」检查原本对每条回复都查一次房间类型，改为回复里真有 `[去群里：]` 才查（`test_pat.py` 因此在无房间表的环境下恢复通过；线上无行为变化）。
- 测试 `test_seat_pat.py` 4 项：座位在家里群 / 自己私聊能拍，别的座位私聊和 Lucien 私聊不能；小猫能拍座位，座位私聊里不能拍不在场的人；座位指令处理与历史还原；座位提示词含拍拍。相关测试与改动前相同（均为既有失败）。
- 转账（座位钱包）单独做，见下一节。

## 41. 狗蛋补能力第三批第三段（下）：座位钱包

- 新模块 `routes/seat_wallet.py`（已在 main.py 注册）：账记在 `bookkeeping`，record_type `seat_wallet_user:<座位>`（小猫转入 / 扣除）与 `seat_wallet_ai:<座位>`（TA 转给小猫，负数）。`credit_from_user`、`pay_user`（`BEGIN IMMEDIATE` 内查余额、够才插入，不够返回 False）、`get_seat_balance`；接口 `GET /api/seat-wallet/{座位}/balance`、`/transactions`（只认 ai3～ai6，其他 404；limit 1～200）。变动广播 `seat_wallet_update`。
- **修 bug**：小猫在座位私聊里发 `[转账：n元]`，原来默认进 Lucien 的钱包。`_room_seat(room_id)` + `_transfer_seat(收款人, 房间座位, 小克名, Lucien 名)`：点名座位 → 那位座位；座位私聊里没点名或点的不是小克 / Lucien → 这位座位；其余照旧走小克 / Lucien 钱包。
- 座位转出：`_process_seat_commands` 解析 `[转账：n元]`（开关 transfer）→ `pay_user`；成功保留标签（前端照旧显示转账卡片），余额不够或开关关着就去掉标签，前者在房间说明「余额不够，没转成」。座位提示词显示 TA 自己钱包的余额。
- 前端 `chatroom.js`：座位私聊里「转账」只转给这位座位（隐藏小克 / Lucien 切换），「钱包」面板显示这位座位的余额和记录；收到 `seat_wallet_update` 时刷新。家里群的转账弹窗仍只有小克 / Lucien 两个按钮（给座位转账可手写 `[转账给狗蛋：n元]`）。
- 测试 `test_seat_wallet.py` 6 项：各自余额、不能透支、接口只认座位、收款路由（含原 bug 场景）、座位转出余额判断与标签保留、提示词余额、前端指向。相关测试与改动前相同（均为既有失败）。
