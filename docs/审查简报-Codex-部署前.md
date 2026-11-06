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

## 42. 家里群转账弹窗加座位按钮

- `crOpenTransferDialog`（非座位私聊）：每次打开先删掉上次加的座位按钮（`data-seat`），再按 `crSeats` 里已启用的座位各加一个按钮（`data-target=座位`）；`crSwitchTransferTarget` 按 `data-target` 统一切换高亮，标题用 `crName`；确认时写成 `[转账给<座位名>：n元]`，服务端按第 41 节 `_transfer_seat` 点名路由进座位钱包。小克 / Lucien 两个按钮补 `data-target`。按钮容器 `flex-wrap: wrap`。
- 测试 `test_seat_wallet.py` 新增前端核对 1 项（共 7 项）。

## 43. 94ce633 / 061c4ee / 9b77cba 审查修复（M1–M3、L1）

- **M1 双击座位头像**：`chatroom.js` 的 `AionPat.bind` 上下文 `names` 加入 `crSeats` 里的所有座位（`crName`），`pat.js` 能找到座位名字、打开拍拍窗。
- **M2 AI 自由文本拍拍的范围**：`save_pat` 对 `PatTextRequest`：正文里「」中出现的名字，若是家里人（`_pat_names` 里有，含重名的多个身份），这些身份至少有一个必须在当前聊天的参与者里，否则 400；不认识的名字当物件放行。对小克 / Lucien 同样生效（例：Lucien 私聊里拍「小克」现被拒）。
- **M3 重名**：转账按钮写成 `[转账给<名字>#<身份>：n元]`（小克 / Lucien / 座位都带），转账卡片显示时去掉 `#身份`。服务端 `_transfer_recipient(收款人, 房间座位, 名字表)` 取代 `_transfer_seat`：带 `#身份` 就按身份；手写名字唯一对应一人才给 TA，**重名返回 ambiguous → 不入账**，房间里说明「有重名，没入账，请用转账按钮选人」；没点名或名字不认识：座位私聊给这位座位，其余照旧给 Lucien。发送处理按返回身份分到座位 / 小克 / Lucien 钱包。
- **L1**：`pay_user` 余额比较按分（`round(x*100)`）。
- 测试：`test_seat_wallet.py`（收款路由改为新函数、重名按身份 / 手写重名不转、0.30-0.10 还能转 0.20、卡片隐藏身份）、`test_seat_pat.py`（AI 拍不在场的人被拒、群里可拍、物件放行、双击上下文含座位）。相关测试与改动前相同（均为既有失败）。

## 44. 99ce1c7 已部署；复审 Low：钱包金额统一到分

- `seat_wallet._cents(amount) = round(amount*100)`。`pay_user`：不到一分（≤0 分）直接拒绝；余额与金额都按分比较；扣款写 `-cents/100`。`credit_from_user`：按分取整后记账，0 分不记。比较和扣款用同一个金额。
- 测试 `test_sub_cent_amounts_are_neither_paid_nor_credited`：0.004 元不转不记、1.005 入账按整分、0.999 按 1.00 扣、账上都是整分。`test_seat_wallet.py` 共 11 项通过。

## 45. 狗蛋补能力第三批第四段：主动陪伴

- `proactive_companionship`：座位各自一个开关 `proactive_companionship_<座位>_enabled`（默认关）；`_actor_id` 认得座位；`_message_actor` 把座位的 `chatroom_msg_created` 当作 AI 消息。座位回复里的 `[NEXT_CHAT:x]` 照原机制在广播时去掉并建计时器，**座位的计时器记下 `origin_room_id=说话的房间`**（小克 / Lucien 照旧留空）。小猫发消息清掉所有计时器、某位 AI 再说话清掉 TA 自己的，机制不变。
- `proactive_status_payload` 列出已启用的座位；`PUT /api/proactive-companionship/{actor}` 接受 ai3～ai6。工具能力页的「主动陪伴」卡片按 roles 渲染，座位自动出现开关。
- 座位提示词：开关开着时加 `proactive_ability_text`（每次回复给出 NEXT_CHAT 决定）。
- 到点：主动陪伴计时器实际由 `_fire_monitor` 处理——认领计时器后，origin 是座位就 `_fire_seat_alarm` 并返回；`_fire_alarm` 里的座位分支也不再排除主动陪伴（认领在前）。`_fire_seat_alarm` 对主动陪伴用「[主动陪伴触发] …没有新的对话消息，请结合最新上下文自然地主动继续聊」，回到计时器记下的房间（群或 TA 自己的私聊，其他情况回 TA 私聊）；回复不写 Hub（`extra_prompt`）。
- 测试 `test_seat_proactive.py` 5 项：座位 NEXT_CHAT 建计时器且记房间、关开关不建、状态列出座位且开关接口接受座位、到点由座位在原房间开口且重复触发不再开口、提示词随开关。原有主动陪伴测试通过；相关测试与改动前相同（`test_stream_safety` 计时用例偶发）。

## 46. 狗蛋补能力第三批第五段：看监控、看动态

- `_process_seat_commands`：`[CAM_CHECK]`（开关 cam_check）插「📷 狗蛋查看了监控」，记下系统消息 id；`[查看动态:n]`（开关 activity_check 且活动日志 AI 联动开着）n 夹到 1～12，插「📊 狗蛋查看了用户动态」。开关关着只去掉。`_spawn_seat_followups` 把两者交给 `_chatroom_cam_check` / `_chatroom_activity_check`，带主回复的线路快照。
- 两个后续函数新增关键字参数 `pinned`；座位调用没带时在任何等待前自取。座位分支：人设用 `seat_chat._persona_text`（插在最前），生成前 `ensure_model_unchanged`（与 `stream_ai` 之间无 await），线路变了就在房间说明并返回、不换模型；模型用座位自己的（不走 `_stream_connor_model`）；收尾不跑 `_process_background_reply_commands`，只去掉指令后以座位名义保存、不自动朗读。小克 / Lucien 分支不变。
- `capabilities`：两段能力说明抽成 `cam_check_ability_text` / `activity_check_ability_text`（主 AI 文字不变），`seat_ability_texts` 按开关给座位。
- 定时监督 `[Monitor:]` 对座位仍关闭（需要座位版的定时截图流程，未做）。
- 测试 `test_seat_cam_activity.py` 6 项：座位发起两种查看并插提示、关开关只去掉、后续带快照、看动态 / 看监控都用座位人设与模型且不跑后台指令、截图期间线路被删则不回复并说明、提示词按开关给。相关测试与改动前相同（均为既有失败）。

## 47. §44–46（d3d52eb / 87bec27 / 54d8e64）审查修复（M1、M2、L1）

- **M1 关掉主动陪伴后旧计时器还会来**（小克 / Lucien / 座位都修）：
  - `proactive_companionship.clear_proactive_timers(actor)`：删掉这位 AI 所有 `type='proactive' AND status='active'` 的计时器，别人的不动。`PUT /api/proactive-companionship/{actor}` 在 `enabled=False` 时调用。
  - `_fire_monitor` 和 `_fire_alarm` 的主动陪伴分支：认领计时器之后先 `is_proactive_enabled(origin)`，关着就直接返回（计时器已认领，不会再响）。
  - 座位：`_fire_seat_alarm` 对主动陪伴传 `proceed_check`；`_generate_seat_reply` 在取完记忆 / 上下文之后、`ensure_model_unchanged` 与 `stream_ai` 之前（中间无 await）再查一次，关了就发 `seat_done` 返回，不调模型、不保存。
  - 主 AI 的认领后检查之后到发请求之间仍有等待（取上下文），这一小段窗口未加二次检查；关开关已同步清掉计时器，正常操作下不会再进来。
- **M2 后续回复只核对线路、没核对座位本人**：新增 `_seat_still_ready(sender, model_key)`（`seat_chat.check_ready` 成功且当前所选模型 == 主回复的 `model_key`）。`_chatroom_cam_check` / `_chatroom_activity_check` / `_chatroom_web_search` 的座位路径：函数一开始查一次，不满足直接返回；发请求前（与 `ensure_model_unchanged`、`stream_ai` 之间无 await）再查一次，不满足走原来的「这次没能接着说」说明并返回，不换模型、不保存。
- **L1 后续回复里的转账标签**：新增 `_strip_unexecuted_transfers`（`TRANSFER_CMD_PATTERN`，含 `[转账给名字#身份：n元]`）。三个座位后续的保存路径在 `_visible_chatroom_text` 之前去掉转账标签，不再显示没执行过的转账卡片；后续本来就不执行转账。
- 测试：
  - `test_seat_proactive.py` +5：关开关只清这位的计时器；关着时到点（座位 / aion / connor）都不开口且计时器已认领；等待期间被关则座位不调模型不保存。原到点用例补上开关已开。
  - `test_seat_cam_activity.py` +4：等待期间座位停用 / 换模型则不调模型不保存；开始时就不满足则跳过；后续回复里两种转账标签被去掉且不调 `pay_user`。原用例补上座位就绪的替身。
  - `test_seat_abilities.py`：两条上网搜后续用例补上座位就绪替身；+1 后续回复去掉转账标签。
  - 三个座位文件共 33 项通过。相关 47 个测试文件：309 通过、10 失败，其中 8 个为既有失败（camera_sentinel_dispatch、chatroom 移动端 / 人设 / 输入框缓存、chatroom_wechat_system_display ×2（改动前同样失败）、wechat_mode_dispatcher ×2），另 2 个即上面补替身的上网搜用例，已修。

## 48. 狗蛋补能力第三批第六段：记住事情（写进 Memory Hub）

按 Hub 窗口 2026-10-20 的约定：用 MCP `remember`（不用 `safe_remember` / `capture`）。

- **outbox 多一种记录**（`memory_hub_bridge`）：
  - 表加列 `kind`（默认 `capture`）、`payload`；新表 `remembered(source_ai, digest, created_at)`。
  - `schedule_remember(actor, items, request_key)`：每条单独入队，`kind='remember'`，`payload` = content / room / speaker_name / subject_name / importance，`event_id = aionshome-mem-<回复 msg_id>-<序号>`。发送时把 `event_id` 作为 `client_request_id`，所以 outbox 重发时 Hub 能认出同一条。
  - 入队时同样固定 source_ai / platform / Hub 指纹。
  - 和对话记录走同一条队列，同一身份严格按顺序补传，断线、冷却、重启的处理都与 capture 相同。
  - 限制：
    - content 截到 200 字；
    - room 只接受白名单（preferences / people / bond / health / career / psychology / learning / social / animal_dorm），其他一律留空交给 Hub 自动分，**AI 不能写进 living_room**；
    - importance 夹在 0～0.8；
    - 一条回复最多 3 条；
    - 同一身份 24 小时内同一件事（去掉标点、空格、大小写后的指纹）只发一次，这次回复里的重复也一样。
  - `_send_capture` 按 kind 分派到 `_send_remember`。Hub 回 `status=error` 视为明确拒收：标为 `uncertain` 并记下原因，**不重发，也不挡同一身份后面的记录**；网络失败照旧按退避重发。
  - `can_remember(actor)`：Hub 开着、允许写入、这位 AI 有 Hub 身份。
- **座位指令**（`_process_seat_commands`）：
  - `[MEMORY:内容|分类|关于谁]`（`|` 或 `｜`），开关 `memory_write` 开着才写，关着只去掉。
  - speaker_name 固定为小猫的名字；没写「关于谁」时 subject_name = 小猫。
  - 小猫这一句里有「记住」时 importance 为 0.75，否则 0.5。
  - 真正排上队的才在房间里显示「💾 狗蛋记住了：…」。
  - `_generate_seat_reply` 传入配对的小猫原话（`paired_user_message`）；闹铃、主动陪伴这类系统触发的回复不传，按 0.5 处理。
- **提示词**：`seat_memory_ability_text`，只在开关开着且这位座位有 Hub 身份时给（`seat_ability_texts(user_name, actor)`）。按 Hub 的内容规则写明：
  - 一条一件事，第三人称，200 字内；
  - 只记小猫本人说的、关于自己的事，群里别人的话不写成小猫的事；
  - 梗和角色扮演不当事实（真要记就加 [梗]）；
  - 建议不是待办；
  - 说明这份记忆是三位 AI 共用的，并列出可选分类。
- 小克、Lucien 的 [MEMORY:] 仍然写本地记忆库，没有改动。
- 测试：
  - `test_memory_hub_bridge.py` +6，假 Hub 加了 remember 工具：参数和固定编号、不进客厅且截断长度和重要度、标点变体去重和每条回复的上限、断线后用同一编号补传且只记一次、拒收后停放且不挡后面的对话、没有 Hub 身份或写入关闭时不发。
  - 新文件 `test_seat_memory.py` 5 项：解析分类和关于谁并显示提示、默认值以及没排上队时不显示提示、关开关只去掉、提示词按 Hub 身份给、系统触发的回复不传小猫原话。
  - 相关测试：303 通过，8 个失败均为既有失败（同 §47）。

## 49. §47–48（d7ac282）复审修复（R1、R2、R3）

- **R1 主 AI 认领后等待期间关开关仍调模型**：`schedule._proactive_switched_off(item)`（主动陪伴计时器且这位 AI 的开关已关）。`_fire_alarm` / `_fire_monitor` 两条主 AI 路径（小克 / Lucien 各一个 `content_stream`，共 4 处）：
  - 所有截图、记忆、上下文等待之后、进入 `_consume_background_*` 之前查一次，关了直接返回（不建 TTS 输出、不保存）；
  - 每个 `content_stream` 生成器开头再查一次，紧贴 `stream_ai` / `_stream_connor_model` 调用，中间没有 await；关了就是空流，后面按原逻辑「没正文就返回」。
  - 关开关清计时器、认领后检查都保留。
- **R2 `[梗]` 截断 MEMORY**：提示词改成内容开头写「梗：」并说明内容里不要用方括号。座位解析前把 `[MEMORY:[梗]` / `【梗】` / `［梗］` 统一改成 `[MEMORY:梗：`（`_MEMORY_JOKE_PREFIX`，只在座位入口，没有改共享的 `MEMORY_CMD_PATTERN`，主 AI 入口不变）。
- **R3 只写记忆的回复被记成空回复失败**：只要解析出有效条目且开关开着，就设置 `triggered["memory"]`，无论新排上队还是因去重没排上，纯指令回复都走 `seat_done(message=None)` 正常结束。只有新排上队的才显示「💾 记住了」；去重时不显示，也不记成失败。
- 测试：
  - `test_seat_proactive.py` +5：小克 / Lucien × 在准备阶段关、在进模型前一刻关（换掉 consume，先关再迭代真实 `content_stream`），模型都没被调用；开关一直开着的对照组正常调用。拿掉修复后其中 4 项失败。
  - `test_seat_memory.py` +5：`[梗]`、`梗：`、`【梗】` 三种写法从真实指令一路到真实 SQLite outbox 的 payload，内容是「梗：小猫是猫王」、分类保留、正文干净；提示词不再出现 `[梗]`；只写记忆的回复（新排上队 / 被去重两种）不调 `_save_chatroom_reply_failure`，以 `seat_done(message=None)` 结束。
  - 相关测试：313 通过、9 失败，其中 8 个为既有失败（同 §47），`test_memory_hub_bridge::test_slow_hub_times_out_without_blocking_chat` 是本机计时问题，拿掉本轮改动后同样失败。

## 50. 4e2b598 已部署；复审 Low：记忆没记上时要提示

- 2026-10-21 03:31 部署 99ce1c7 → 4e2b598（服务正常、日志无报错）。
- `schedule_remember(..., outcome=None)`：返回值仍是「这次排上队的内容」；传入 dict 时填 `duplicate`（因记过而跳过的条数）和 `failed`：`""` / `unavailable`（Hub 没开或这位 AI 没有 Hub 身份）/ `full`（待补传队列满）/ `error`（落盘异常）。
- 座位入口：有有效条目且 `failed` 非空时，在房间里显示「（狗蛋想记的事这次没记上，晚点可以再跟 TA 说一次）」；正常去重（`failed` 为空）保持静默。`triggered["memory"]` 不变，纯指令回复仍以 `seat_done(message=None)` 正常结束，不记成空回复失败。
- 测试：
  - `test_memory_hub_bridge.py` +1：新排上队、去重、队列满（`OUTBOX_MAX=1`）、无 Hub 身份四种情况的 outcome。
  - `test_seat_memory.py` +3：full / error / unavailable 时显示「没记上」，去重时不提示；原有替身改为接受 `outcome`。
  - 相关测试 211 通过，6 个失败均为既有失败（chatroom 前端缓存 / 移动端 ×5，`test_slow_hub` 本机计时）。

## 51. 狗蛋补能力：定时监督 Monitor（座位版）

- **设定**：`_seat_schedule_commands` 在「看监控」开关（cam_check）开着时，多解析 `[Monitor:时间|内容]` → `_add_schedule("monitor", …, origin=座位, origin_room_id=房间)`，房间里显示「👀 【狗蛋】设定了监督」。开关关着只去掉，不设。仍然只从原文解析一次，去掉指令后拼出来的新指令不执行。座位删除自己的监督显示「定时监督」。
- **能力说明**：`seat_ability_texts` 在 schedule 和 cam_check 都开着时给出 `[Monitor:…]`。
- **到点**：`_fire_monitor` 非主动陪伴分支：`finish_schedule(…"triggered")` 认领成功、广播之后，如果 origin 是座位就调用 `_fire_seat_monitor` 并返回，不走小克 / Lucien 流程。重复触发因认领失败不再开口。
  - `_fire_seat_monitor`：cam_check 开着时调用 `acquire_prompt_monitor_context("scheduled_monitor", alert_extra={origin: 座位})` 截图；开关关着或截图异常时不截图。提示词「[定时监控触发] … 监控目的 …」：有图时说明是实时画面，没图时说明这次没拿到画面，并要求「不要假装看到了画面」。
  - 交给 `_fire_seat_alarm(item, prompt=…, attachments=[截图])`，房间选择与闹铃相同（设的房间：群或 TA 自己的私聊，其他情况回 TA 私聊）。
  - `_generate_seat_reply` 新增 `extra_attachments`，附在系统触发的那条 user 消息上（和主 AI 定时监督的 `trigger_msg["attachments"]` 写法一致）。回复不写 Hub（extra_prompt 规则不变），模型线路快照、`check_ready` 与之前相同。
- 没有加活动日志、心率摘要（主 AI 的监督有）；如果需要以后再加。
- 测试 `test_seat_schedule.py`：
  - 改：原「座位不能设监督」改为「开关开着能设」，上下文里有 `[Monitor:`。
  - 新增 6 项：关开关不设；到点由座位带截图在原房间开口、主 AI 流程没走、已认领、重复不开口；关开关到点不截图且提示没画面；截图附在触发消息上；提示词随开关。
  - 相关 160 项通过。

## 52. 监控 / 定位叫谁：智能模式不固定分工，可以叫座位，出门回家也智能挑人

方案见 `docs/施工方案-监控定位叫谁.md`。小猫 2026-10-21 的要求：智能模式不要按事情类型固定分工；出门回家要「随机，智能一点」；狗蛋等座位都能被叫来。

- **候选名单** `camera.wake_roster()`：小克、Lucien，加上 `seat_chat.check_ready` 通过的座位。每人带 `key`（给哨兵看的编号：小克 main_ai、Lucien second_ai，座位用自己的 id，内部名不交给哨兵）和 `last_woken`（最近 3 天监控日志里 `final_wake_targets` 出现的最晚时间）。
- **随机建议人选** `suggest_wake_target`：权重 = 1 + 距上次被叫来的小时数（上限 72；从没被叫过的按 72 算），用 `random.choices` 抽一个。
- **派单规则** `build_sentinel_dispatch_policy(user_name, roster, suggested)`（替换原来的固定分工）：列出名单和上次被叫来的时间；不按事情类型分工；点名优先；保持上下文连续；都不明显时看互动情况、避免总叫同一位；拿不准就用建议人选；只有明显危险、健康异常、情绪崩溃、长时间失联才多叫，用逗号分隔。
- **解析**：
  - `parse_wake_target_ids(value, allowed)`：支持逗号、顿号、斜杠、空格分隔多人；兼容 main_ai / second_ai / both / aion / connor；all 表示名单里所有人；不在名单里的丢掉；结果按名单顺序。
  - `resolve_sentinel_wake_targets(..., roster_ids, suggested)`：固定模式返回设置的人；智能模式解析不出人时回退到建议人选（不再固定回退小克，没有名单时仍回退 aion，旧测试保持）。
  - `parse_sentinel_dispatch` 同样传入名单和建议人选。`sentinel_wake_target` 解析得出人时记原值，否则记 main_ai（旧行为）。
- **摄像头**：
  - `CAM_WAKE_MODES` 加入 ai3～ai6。
  - `_analyze_and_log` 用名单、建议人选生成规则并解析。
  - `_handle_monitor_no_image` 取图失败时，智能模式也用建议人选。
  - `_wake_core_targets` 按 aion、connor、ai3～ai6 的顺序处理座位：`_resolve_actor_target` 返回座位私聊（`_get_or_create_seat_room`），`_call_seat_core` 拼唤醒提示（多久没说话、叫醒原因、整体状况、最新和最近的监控记录、设备动态、位置），附截图后调用 `_generate_seat_reply(group=False)`。座位没准备好时，房间里留「没能回复」记录，不换人。
- **定位**：
  - 摄像头配置新增 `location_wake_mode`，默认 smart，可选值同上。
  - 智能模式下，`_notify_sentinel` 的提示词附上派单规则，JSON 多出 wake_target、wake_reason、wake_confidence；固定模式提示词不变。
  - 解析失败时照旧默认要叫，叫谁按设置，智能模式用建议人选。
  - 日志记录带上 dispatch 和 `final_wake_targets`，用于下次计算上次被叫来的时间。
  - 叫 aion 照旧走 `_call_core_location`，行为不变；Lucien 和座位走 `cam._wake_core_targets`，位置事件作为监控记录，不带截图，派单依据写进原因。
- **设置页** `static/camera.html`：
  - 「需要联系时唤醒谁」改成动态选项（小克、Lucien、已启用座位、智能），名字用 textContent 填写。选过、后来被停用的座位显示「（未启用）」。
  - 新增「出门 / 回家时叫谁」。
  - 智能说明改成新规则。
  - `/api/cam/status` 多返回 `location_wake_mode` 和 `wake_seats`，`PUT /api/cam/config` 接受 `location_wake_mode`。
- 测试：
  - `test_camera_sentinel_dispatch.py`：原来的固定分工规则用例改写为新规则（名单、编号、不固定分工、建议人选、不出现内部名）。其余用例不变，全部通过。
  - 新文件 `test_wake_roster.py` 11 项：解析座位和多人、回退到建议人选、座位可作为固定设置、建议权重、名单只含准备好的座位且带上次被叫来的时间、叫座位时在私聊里带截图开口；定位方面有智能挑座位（提示词带规则、日志记录目标）、挑小克走原路径、固定模式不带规则、哨兵失败用建议人选、不叫时谁都不叫。
  - 相关 35 个测试文件：274 通过，2 个失败均为既有的 `test_sentinel_routes`。设置页内联脚本 `node --check` 通过。

## 53. §50–52（c2dc985）审查修复（M1、M2）

- **M1 加入座位后，小克 / Lucien 同群不再串行**：`_wake_core_targets` 的同群判断去掉 `len(ordered_actors) == 2`，只比较 aion 和 connor 的目标（都是同一个群）。同群时把这两位按 aion→connor 的顺序串行，作为一个任务，和各座位的私聊任务 `asyncio.gather` 并行；不同群时照旧全部并行。座位仍然只到自己的私聊。
- **M2 共享生成器在等上下文之后没有重查座位**：`_generate_seat_reply` 在 `proceed_check` 之后、`ensure_model_unchanged` 和 `stream_ai` 之前（中间没有 await），调用 `_seat_still_ready(actor_id, model_key)`，要求仍然启用、有人设、当前所选模型等于开始时的 model_key。不满足就抛出「座位设置刚刚改动了…没有换人」，交给已有的失败处理（`_save_chatroom_reply_failure` 在房间里留「没能回复」记录），不调模型、不保存、不换人。所有调用方（聊天、闹铃、主动陪伴、定时监督、监控 / 定位唤醒）都会经过这次检查。
- 测试：
  - `test_wake_roster.py` +4：三种叫人顺序（含 ai4），都满足 aion 结束后 connor 才开始、座位不排在两位后面；对照组是两位在不同群时并行。
  - `test_seat_proactive.py` +2：等上下文期间停用座位或改选模型，模型没被调用、没保存、留下失败记录。
  - 拿掉两个修复后其中 5 项失败。
  - 座位与叫人相关的 7 个文件共 95 项通过。相关 54 个文件中，失败的都是既有失败（chatroom 前端 ×5、sentinel_routes ×2）。`test_seat_chat::…line_removed…` 在改用统一失败记录后通过。

## 54. 座位被叫来也去她最近说话的地方（小猫：「几个 AI 权限都一样」）

- `_resolve_actor_target(座位)`：查她最近一条消息（`chatroom_messages.sender='user'`）所在的房间，范围和座位的合并时间线一致：群（不含 `billiards_*`）或这位座位自己的私聊。最近在群里就返回该群（room_type=group），否则返回 TA 的私聊（`_get_or_create_seat_room`）。别的座位的私聊、Lucien 的私聊不算。
- `_call_seat_core`：`group` 和场景描述跟着目标房间走（群里或私聊里），群里走座位的群聊上下文（含群聊隐私规则）。
- `_wake_core_targets`：§53 只把 aion 和 connor 编成同群串行组，现在改成**按房间分组**。被叫到同一个群的人（aion、connor、ai3～ai6，按这个顺序）一个接一个说；私聊和其他房间各自成组，组与组之间 `gather` 并行。§53 的用例照样通过。
- 测试 `test_wake_roster.py`：
  - 去哪说话 4 种：没说过话去私聊；最近在私聊；最近在群里；别的座位私聊和台球房不算。
  - 叫座位在私聊或群里开口各 1 种（`group` 参数和场景描述）。
  - 三人同群严格按顺序，另一位在私聊的座位并行。
  - 相关测试中失败的只有既有的 `sentinel_routes` ×2。

## 55. ④ 狗蛋补能力：日记、微信

清单见 `docs/座位能力对齐清单.md`（小猫：几个 AI 权限都一样）。

- **日记** `seat_diary.py`（新）：
  - 小克 / Lucien 的日记是在本地记忆总结时顺带写的。座位的记忆在 Hub、没有本地总结，所以单独做一个循环，在 `main.lifespan` 里启动，关闭时和 Hub 的任务一起取消。
  - **触发**：每 10 分钟检查一次每位座位。范围和座位的合并时间线一致：TA 的私聊 + 所有群，不含 `billiards_*` 和 system。锚点存在 `chatroom_digest_anchors`，key 为 `seat_diary:<座位>`。锚点之后至少 20 条新消息、其中有她说的话，并且她最后一句已过 30 分钟，才写。
  - **生成**：用座位的人设 + 最近 80 条（`【说话人】内容`）+ 和 Lucien 相同的 JSON 格式（日记 / 朋友圈 / 礼物），另加一句「亲密、身体、私人情感只写进日记，不发朋友圈」。
    - 一开始 `check_ready` 并做模型快照；发请求前再 `check_ready`（模型键必须相同）并 `ensure_model_unchanged`，中间没有 await。
    - 超时 300 秒。
  - **结果**：
    - 解析成功：`save_diary_entry(author=座位, source_type="seat_digest")`，推进锚点。
    - 朋友圈：moment 开关开着才发，截到 `MAX_AI_MOMENT_CHARS`（800），作者是座位。
    - 礼物：`send_gift_from_decision(sender=座位)`。
    - 解析失败不推进锚点；同一段连续失败 3 次后推进锚点并跳过，避免反复花钱。
  - **日记本页面**：从 `/api/actors` 读取座位，增加标签（textContent），`#seatTabs { display: contents }` 与其他标签平分一行。
  - **朗读**：座位日记的朗读接口返回 400「还没有给狗蛋配置声音」（座位声音是清单第 4 项）。
- **微信**：
  - `_process_seat_commands`：wechat_message 开关开着时，和小克 / Lucien 一样调用 `process_wechat_outbound_commands(source_type="chatroom", source_id=房间, sender=座位)`。房间里留下记录，记下回复路线（她从微信回话会回到这个房间，入站已支持座位私聊房间），通过 `dispatch_wechat_message` 发出。开关关着时只去掉指令。
  - `wechat_bridge.resolve_wechat_sender_label`：座位用 `actors.display_name`（「狗蛋：内容」）。
  - `wechat_mode_dispatcher`：微信模式转发的发言人加上 ai3～ai6，名字用 display_name。
  - 能力说明：抽出 `wechat_message_ability_text`（主 AI 文字不变），`seat_ability_texts` 按开关给出。
- 测试：
  - `test_seat_diary.py` 9 项：写日记（人设、上下文范围、别的座位私聊不算、作者、锚点、朋友圈截断、礼物发送者、写过不再写）；4 种不满足条件；未就绪或改选模型；朋友圈开关关着；解析失败重试后跳过；座位朗读提示没配声音。
  - `test_seat_wechat.py` 4 项：以自己的名字发送并记下路线、关开关只去掉、提示词、微信模式转发和名字。
  - 相关测试 220 通过，5 个失败均为既有失败（wechat_mode ×3、chatroom 显示 ×2）。

## 56. ⑤ 狗蛋补能力：人格成长（persona evolution）

做法与小克 / Lucien 相同：读她写了理由的点赞 / 点踩，以及当天的「记忆」，由 TA 自己的模型决定是否改写自己的人设分区。核心身份和种子分区锁定，其余分区可以慢慢生长。

- `persona_evolution.py`：
  - `EVOLUTION_ACTORS` 加入 ai3～ai6。
  - **点评** `fetch_seat_feedback`：这位座位在群里、在自己私聊里（`seat_1v1` 且 `r.actor=座位`）有评分且写了理由的发言。别的座位私聊不算。上下文 `_timeline_user_neighbor` 只取 TA 的私聊 + 群里她说的话。
  - **当天记忆** `fetch_seat_daily_summaries`：座位没有本地记忆总结，改用这段时间 TA 写的日记（`diary_entries.author=座位`，§55）。
  - **模型**：`_actor_model` 调用 `check_ready`，没准备好就报错，这次复盘失败、不换模型。`_call_actor_model` 调用前再查一次，所选模型变了就报错，中间没有 await。调用走 `simple_ai_call`（和小克的路径一样）。
  - **人设**：`_current_actor_sections` 和 `_build_prompt` 读 `actors.persona_sections(座位)`。原本 `_build_prompt` 的 else 分支会把小克的世界书人设交给模型，已经修正；测试确认提示词里没有小克的人设。`_apply_section_updates` 通过 `actors.save_persona_sections` 写回座位分区，不碰世界书。
  - **锁**：座位不读世界书里小克的 `persona_section_locks`，只锁 `identity_core` + `seed_locked_sections(座位)`（人设包的种子分区，默认是核心身份、关系锚点、边界与禁令）。`_effective_section_locks` 也同样处理。
  - **开关**：`actors.persona_evolution_enabled` 和 `set_…`，存在 actors.json 里这位座位下，默认关。`seat_persona_evolution_loop` 每天北京时间 05:00 依次处理开着开关的座位，在 lifespan 里启动和取消。
- **接口** `routes/persona_evolution.py`：`/seat/{actor}/run-test | config (GET/PUT) | runs (GET) | runs/{id} (DELETE)`。座位以外的 actor 返回 404。
- **评分**：`update_message_feedback` 允许座位在群里的发言，以及 TA 自己私聊里的发言（查询多取 `r.actor AS room_actor`，要求发言人等于房间主人）。前端 `crCanRateAiMsg` 做了同样的判断。
- **AI 名片页**（`static/ib/cards.html`）：座位的编辑框里新增「人格成长」，包括开关（立即保存，失败时回滚）、「现在复盘一次」、最近一次结果（textContent）。
- 测试 `test_seat_persona_evolution.py` 5 项：
  - 点评范围（群 + 自己私聊，不含别的座位私聊、没评分的、小克的）；
  - 端到端复盘：只改座位分区，核心身份不变，小克锁住的分区不影响座位，提示词里没有小克的人设，世界书没写过；拿掉 `_build_prompt` 修正后此项失败；
  - 模型变了或没准备好时报错；
  - 开关和接口（非座位返回 404）；
  - 座位消息可以评分，她自己的消息不能评分。
  - 相关测试 205 通过，5 个失败均为既有失败（chatroom 前端）。

## 57. §55（054d17f）座位日记审查修复（D1–D4）

- **D1 空日记被当成成功**：先用 `diary.build_diary_entry` 组装日记，正文去掉空白后为空就返回 None。`{"diary": null}`、`{}`、只有空白、字符串只有空白，都和解析失败一样处理：计一次失败、锚点不动；连续 3 次后才跳过这一段。没有另外设计「模型主动不写」的状态（提示词要求一定写一篇）。
- **D2 日记和锚点不在同一次提交**：`diary.py` 把 `save_diary_entry` 拆成 `build_diary_entry` / `insert_diary_entry(db, entry)`（只执行，不提交）/ `broadcast_diary_entry`。`save_diary_entry` 由这三步组成，小克 / Lucien 的行为不变。座位日记在同一个连接里 INSERT 日记并 upsert 锚点，然后一次 commit，提交之后才广播、发朋友圈、送礼。提交后在任何等待点停机，重启时这一段已经算写过，不会再写第二篇。
- **D3 模型快照建立得太晚**：第一次 `check_ready` 之后马上 `model_snapshot`，中间没有 await，然后才读消息。生成前再 `check_ready`（模型键相同）+ `ensure_model_unchanged`，中间没有 await。这一步出任何问题都返回 `skip_not_ready`，不计失败次数（设置变了不算「写不出来」）。
- **D4 积压记录全部读进内存**：
  - `_pending_stretch` 只用聚合查询取锚点之后这一段的概况：条数、MIN/MAX 时间、她最后一句的时间，不读正文。
  - `_recent_messages(anchor, last_ts)` 在 SQL 里 `created_at <= last_ts ORDER BY created_at DESC, rowid DESC LIMIT 80`，再反转为正序。
  - 这一段的语义：从锚点之后到快照时的 last_ts，整段都算处理过（日记的 source 起止 = 整段的 MIN/MAX）；正文只用最近 80 条；成功后锚点推进到 last_ts。生成期间新来的消息时间晚于 last_ts，留给下一段。
- 测试 `test_seat_diary.py`：
  - 原用例改成真实写入 `diary_entries`，只替换广播。
  - 新增 D1 四种空日记；D2 在广播时取消任务，结果库里 1 篇、锚点已推进，再跑一次时这一段不再写；D3 读消息期间改了线路的模型，结果是跳过、不调模型、不计失败；D4 300 条只读最近 80 条，提示词里没有更早的，日记起止按整段。
  - 共 16 项通过；日记、总结、座位相关的 17 个文件共 157 项通过。

## 58. §56（d029f6b）人格成长审查修复（E1–E3）

注：上一轮审到 d029f6b，§55 日记的 D1–D4 当时还没修；它们已在 **e9c2dee**（§57）修好，请一并复审。

- **E1 线路同键改绑**：`run_persona_evolution` 中，`_actor_model(座位)` 返回后（座位分支内部没有 await）立即 `model_snapshot`。`_call_actor_model(..., pinned=)` 在座位分支里依次 `check_ready`、比较所选键、`ensure_model_unchanged(model_key, pinned)`，之后直接调用模型，中间没有 await。线路被改绑会抛错，这次复盘记为 failed，不调模型、不改人设。
- **E2 等模型期间导入的人设被覆盖**：调用模型前，在构建提示词的同一时刻读取 `prompt_sections`，`_collect_section_updates` 也基于它解析。模型返回后再读一次当前分区；凡是本次要更新、但从 prompt_sections 到现在已经被改过的分区，都去掉不应用（逐个分区做 compare-and-swap），并在记录里写明「复盘期间这些分区被改过，没有应用：…」。没有冲突的分区照常更新。小克 / Lucien 也走同一逻辑（只有等待期间人设被改时才生效）。
- **E3 取消顺序**：`seat_persona_evolution_task` 移到 lifespan finally 的最前面，和 Hub、日记任务一起 cancel + gather，排在 `post_sentinel.stop` 等调用之前。
- 测试 `test_seat_persona_evolution.py` +3（共 8 项）：同键改绑时不调模型、人设不变、状态 failed；等待期间导入的分区不被覆盖、其他分区照常更新；finally 中取消在 `post_sentinel.stop()` 之前。用 HEAD 版本的源码跑这 3 项全部失败。

## 59. 狗蛋补能力：内心旁白

- 内心旁白不是后台指令：模型写 `[心里嘀咕：…]`，保留在回复正文里，前端 `crMessageContentItems` / `crRenderInnerMonologues` 对所有非用户消息都特殊显示（不看发言人）；TTS、微信模式会去掉它，这些已经适用于所有人。
- 改动：`seat_ability_texts` 在 inner_monologue 开关开着时，加入和主 AI 相同的 `INNER_MONOLOGUE_ABILITY_TEXT`。
- 测试 `test_seat_inner_monologue.py` 2 项：提示词随开关出现；座位回复（分两段流式）原样保存这个标记，流式显示时也不被吞掉。

## 60. 狗蛋补能力：桌面便笺

- 座位回复本来就会经过 `process_note_commands(raw_text, actor_id)`（以座位身份写便笺，主 AI 也一样不按开关过滤处理）。桌面页 `desk.js` 用 actors 列表显示署名和头像，座位已包含在内。缺的只是能力说明。
- 改动：抽出 `desk_note_ability_text(user_name)`（主 AI 文字不变）；`seat_ability_texts` 在 desk_note 开关开着时加入。
- 测试 `test_seat_desk_note.py` 2 项：提示词随开关出现；座位回复中的 `[NOTE:…]` 被去掉，以 ai3 署名写入便笺。原 `test_actors_desk_notes.py` 照样通过。

## 61. 狗蛋补能力：位置和周边

- **位置上下文**：`seat_chat.location_line()` 在 location_context 开关开着、位置追踪开启时，把 `【位置信息】format_location_for_prompt()` 加进座位的背景（和日程列表同一段），条件与主 AI 的 `build_ability_block` 相同。
- **周边搜索**：
  - `routes/chat.py::perform_poi_check` 中「取最新坐标 → 逆地理 → 按类型搜索 → 写缓存 → 格式化」整段原样移到 `location.search_nearby_pois_text(categories)`，小克私聊改为调用它，行为不变。
  - 座位：`_process_seat_commands` 解析 `[POI_SEARCH:类型]`。和主 AI 一样，poi_search 开关开着且 `_is_outside_location()` 为真时，房间里显示「📍 狗蛋搜了搜附近的…」，最多 3 类，交给 `_spawn_seat_followups` → `_chatroom_poi_search`（带主回复的线路快照）。条件不满足时只去掉指令。
  - `_chatroom_poi_search`（只给座位用）：开始时 `_seat_still_ready`；搜索；没结果时说明「没搜到」；发请求前再做 `_seat_still_ready` + `ensure_model_unchanged`，中间没有 await，不满足就说明后返回。用座位的人设和模型，提示词「从结果里挑几个自然推荐，不编造」。保存前去掉 POI 指令和未执行的转账标签，`auto_tts=False`，不跑后台指令。
  - 能力说明抽成 `poi_search_ability_text`（主 AI 文字不变），座位只在户外时给出。
- 测试 `test_seat_location.py` 8 项：
  - 位置这一行随开关和位置追踪变化；座位上下文里带位置；
  - 只在户外才搜；提示词只在户外给出；
  - 用座位的人设、模型推荐，并去掉转账标签；搜索期间改选模型则不回复；没结果时说明；
  - 共用搜索函数的格式化、缓存，没有 Key 时返回空。
  - 相关测试 268 通过，9 个失败均为既有失败（chatroom 前端、wechat_mode、bubble parity）。

## 62. 狗蛋补能力：逛淘宝

逛淘宝 = AI 按自己的兴趣去淘宝真实搜索、挑选后存进独立收藏篮、写小感想（不加购、不下单），可以由自主活动发起，也可以在淘宝页手动点。

- `seat_chat.seat_target_room(actor)`（新）：把 §54「去她最近说话的地方」抽出来共用（最近在群就去那个群，否则去 TA 私聊；别的座位私聊、Lucien 私聊、台球房不算）。`camera._resolve_actor_target` 改为调用它，行为不变。
- `taobao_shopping.actor_id`：接受 ai3～ai6，座位要先通过 `check_ready`（停用、没人设、没选模型时报错，不去逛）。选搜索词和挑商品都走 `autonomy._call_actor` / `_actor_context`，座位分支已有（用 TA 自己的模型和人设）。
- `taobao_notifications.notify_shopping_trip`：座位在事务之前用 `seat_target_room` 确定房间（避免在 `BEGIN IMMEDIATE` 里再开一个写连接），事务里再确认这个房间是群或 TA 自己的私聊；回执卡片、去重、同步事件的逻辑和主 AI 相同，名字用 display_name。
- `routes/taobao.py`：`ActorBody.actor` 接受 ai3～ai6；`/state` 的 names / avatars 加上启用的座位。`taobao.js` 的同伴卡片改为按 names 生成（选择框、筛选按钮原本就按 names 生成）。
- `autonomy.SEAT_ACTIONS` 加入 `taobao_roam`（自主活动开关、自主入口总开关、购物锁的过滤仍然有效）。
- 测试：
  - `test_seat_taobao.py` 新增 7 项：回执去哪 4 种情况（没说过话去私聊；最近在群里去群；最近在私聊去私聊；别的座位私聊不算），同一次只发一张；没准备好时不能逛；自主活动可选；页面列出启用的座位。
  - `test_autonomy_seats.py` 中座位可选活动的期望加上 taobao_roam。`test_wake_roster.py` 的数据库替身同时替换 `database.get_db`（共用函数用它）。
  - 淘宝和座位相关共 54 项通过，淘宝前端 4 个 node 测试通过。

## 63. 狗蛋补能力：自主活动（和家人聊聊、看家庭动态、翻旧记忆、小红书）

小猫 2026-10-21：相册、AI 好友 / 留言板、台球先不做；Seeky 不要。所以 SEAT_ACTIONS 只加 `role_chat`、`home_dynamics`、`memory_browse`、`xhs_roam`。

- **和家人聊聊**：
  - 选动作的 JSON 多一个可选 `target`；提示词列出能找的家人（`_partner_hint`）。
  - `_family_partners(actor)`：小克、Lucien、`check_ready` 通过的座位，不含自己。
  - `_pick_partner(actor, wanted)`：点名的人（id 或名字）在名单里就找 TA；否则小克↔Lucien 照旧互找，座位随机找一位。小克 / Lucien 也可以点名找座位。
  - `_reply_in_group(target, …)`：aion → `_reply_aion`，connor → `_reply_connor`，座位 → `_generate_seat_reply(group=True)`（就绪检查都在里面，没准备好会留「没能回复」，不换人）。
- **看家庭动态**：`_run_home_group_tease` 的调侃对象改成「发这条动态的家人」（如果在名单里），否则用 `_pick_partner`；回话同样走 `_reply_in_group`。小克 / Lucien 的行为因此有一点变化：以前固定调侃对方，现在调侃发这条动态的人（可能是座位）。回复朋友圈、私聊、写日记的分支原本就支持座位。
- **翻旧记忆**：座位的记忆在 Hub，本地没有记忆表。`_memory_rows(座位)` 读 TA 自己写的日记（`diary_entries.author=座位`，id 加前缀 `diary:`），按天抽取、读后可以发朋友圈 / 写随笔 / 私聊，流程与主 AI 相同。
- **私聊留言**：`_save_private_message(座位)` 不带 `force_private` 时，和小克一样去她最近说话的地方（`seat_target_room`），带 `force_private` 时仍去 TA 的私聊。自主私聊、读后私聊等都会经过这里。
- **小红书**：
  - `xhs_lite`：`known_actors()` 包含 ai3～ai6；`actor_label` 用 display_name；`public_config.actor_labels` 只列准备好的座位。
  - `is_ready_for_auto` 和 `run_actor_roam` 对座位要求 `check_ready`（不满足就不自动去，手动点会报错）；`save_config` 和接口接受座位的启用开关（座位默认启用，和主 AI 默认一致）。决定评论走 `autonomy._ask_actor_json`，座位分支已有。
  - 页面：座位的启用卡片和「跑一次」按钮动态生成（textContent），保存时一起提交。
- 测试：
  - `test_seat_autonomy_more.py` 8 项：可选活动（含暂缓项不出现）；找谁聊天；回话走对方自己的路径（小克 / 座位）；座位的旧记忆是自己的日记；私聊去她最近说话的地方、force_private 时去私聊；调侃发动态的人；小红书的名字、列表、开关、就绪检查。
  - `test_autonomy_seats.py` 中座位可选活动的期望更新。
  - 相关 34 个文件：330 通过，5 个失败是既有失败（taobao_native_mcp ×4；`test_idle_autonomy_actions…friend_visit_action_is_available…` 改动前同样失败）。`test_idle_autonomy_friend_visit.py` 在本机改动前后都卡住超时（AI 好友小猫暂缓）。

## 64. §59–63（903b21f）复审修复（M1–M3、L1）

- **M1 座位自主活动没有固定身份和线路**（`autonomy.py`）：
  - 新增 `_SEAT_PINS` ContextVar 和 `pin_seat(actor)`：同步执行 `check_ready`，固定所选模型、`background_model(actor, model)` 后台线路和 `model_snapshot(route)`，没有 await。
  - `verify_seat_pin(actor)`：再做一次 `check_ready`，要求所选模型和后台线路都与固定值相同，再 `ensure_model_unchanged(route, snapshot)`；不满足就抛错。
  - `_call_actor(座位)` 改为 `verify_seat_pin` 后直接用固定的线路 `stream_ai`，中间没有 await，不再临时读当前模型。
  - 固定点：`_run_actor_once` 在选动作之前；淘宝 `_roam` 开头（选方向、挑商品两次调用共用同一份）；小红书 `_run_actor_roam` 开头（在解析账号等任何等待之前）。`_seat_actor_context` 第一次等待之前如果还没有固定也会固定。每个入口都重新固定，上一轮的旧值不会一直卡住。
  - 主 AI 路径不变。
- **M2 小红书发出前没有再核对**：发送延迟结束后、对外写入之前（中间没有别的等待）调用 `_still_allowed_to_write(actor)`：重新读配置，检查总开关、写评论开关、这一位的开关；座位还要 `verify_seat_pin`。不满足就记为 drafted（保留草稿），不发送。不检查 `auto_enabled`，手动运行不受自动开关影响。
- **M3 重复巡游**：
  - 服务端：`run_actor_roam` 对每一位加 `asyncio.Lock`，手动接口和自动巡游都经过这里；同一位正在巡游时直接报「正在巡游，没有重复发起」。
  - 前端：新增 `xhsRunning` 状态。`renderSeatActors` 重建按钮时按这个状态禁用，`runOnce` 运行中不能再次进入，结束后恢复。
- **L1 点名找错人**：`_pick_partner` 先按精确编号匹配；不是编号时只有恰好一位叫这个名字才选 TA，重名不猜、按默认安排。提示词要求写编号。
- 测试：
  - `test_seat_autonomy_guard.py` 13 项：准备上下文期间同键改绑、停用、改选模型、换后台线路，都不调模型；没有变化时调用自己的线路；每次入口重新固定；小红书等待发出期间停用座位、关这一位的开关、关写评论开关、关总开关，都不发；没有变化时正常发；同一位并发第二次直接报忙、只发一条；编号优先、重名不猜。
  - `test_xhs_seat_run_ui.js`：运行中保存配置重建的座位按钮仍然禁用、`runOnce` 不能再次进入、名字是纯文本。
  - 用 HEAD 版本的 autonomy / xhs_lite 跑新测试，全部失败或报错。
  - 相关测试：343 通过，5 个失败都是既有失败（同 §63）；淘宝和小红书前端 5 个 node 测试通过。

## 65. 哨兵走 Codex 登录态时模型可以自己选

背景：2026-10-22 服务器上装了官方 Codex CLI 并用小猫的 ChatGPT 账号登录（不是代码改动；AionsHome 原有的 codex_cli / app-server 路径已实测可用）。原来哨兵的 codex_luna 线路写死为 `gpt-5.6-luna`，小猫希望以后模型下架时能自己换。

- `config.normalize_sentinel_codex_model`：模型名只允许 `[A-Za-z0-9][A-Za-z0-9._-]{0,79}`，不合法或为空时回退 `gpt-5.6-luna`（`DEFAULT_SENTINEL_CODEX_MODEL`，没选过的照旧）。`get_sentinel_config()` 的 codex_luna 分支改为读取 `SETTINGS["sentinel_codex_model"]`。
- `routes/settings.py`：`SettingsUpdate.sentinel_codex_model` 写入时同样规范化，GET 时一并返回。新增 `GET /api/settings/codex-models`：读取 Codex 自己缓存的 `models_cache.json`（`_CODEX_HOME` 和 chat home 里较新的那一份；不另外请求、不调模型），列出 visibility 不是 hide 的模型，带 `vision`（input_modalities 是否含 image）。读不到时返回空列表。
- 设置页：线路名改为「Codex 登录态（模型可选）」，只有选了这条线路时才显示「Codex 模型」下拉框。选项用 textContent 填；当前的模型如果已经不在账号的模型列表里，会照实显示并提示换一个；不能看图的模型会标出来。
- 测试 `test_sentinel_codex_model.py` 4 项：名字校验和默认值；哨兵配置使用所选模型；模型目录（隐藏的不列、能否看图、没缓存或坏缓存）；设置的保存和读取。相关 sentinel / settings / camera / location 测试 126 通过，2 个失败是既有的 `test_sentinel_routes`。设置页内联脚本 `node --check` 通过。

## 66. 线路管理：默认模型自己选、Codex 模型跟着账号走

小猫 2026-10-23：找不到怎么换 Codex 模型；内置模型默认还是 Gemini；希望存好线路后能直接拉取、选择。

- **默认模型**（`config`）：
  - 原来 `DEFAULT_MODEL = "Gemini-3.5-flash"` 早就不在 MODELS 里，服务器上也没有 Gemini Key。`resolve_model_key` 的回退顺序是「这个不存在的默认 → 第一个可见模型」，可见不等于可用，可能落到没 Key 的模型上。
  - 新增 `default_model_key()`：优先用 `SETTINGS["default_model"]`（必须是共用线路、没停用、在这台服务器上可用）；否则取 `iter_selectable_models()` 里第一个共用线路的模型；最后才是旧常量。
  - `is_shared_model`：`route_id` 以 `seat_` 开头的座位专属线路不能当默认，也不出现在默认模型下拉框里。
  - `resolve_model_key(key)`：key 在 MODELS 里且没停用就原样返回；空的、不存在的、已停用的才换成 `default_model_key()`。在、只是暂时用不了的不偷偷换（座位「必须用自己的模型」靠 check_ready）。所有经过 `resolve_model_key` 的地方都会生效，包括新建对话、stream_ai、各处 `resolve_model_key(conv["model"] or DEFAULT_MODEL)`。
- **Codex 模型目录**（新模块 `codex_models.py`）：
  - `list_codex_models()` 读 Codex 自己的 `models_cache.json`（从 routes/settings 挪过来）。
  - `codex_model_key("gpt-6.1-sol") = "Codex-6.1-Sol"`，和原来写死的名字一致，以前选过的照样认得。
  - `sync_models()` 把账号里有、MODELS 里没有的补进来（provider codex_cli、safe_live、vision 按目录），并去掉以前补进来、现在账号里没有的；写死的条目不动。
  - `model_usable(codex_cli)`：装了 Codex，并且（目录未知 / 没写模型名 / 模型在账号目录里）才算可用。下架的模型自动从下拉框里消失；选过它的地方 key 还在，不会被偷偷换掉。
  - `refresh()`：用 `_build_codex_chat_environment()` 启动一次 `codex app-server`，只发 initialize + `model/list`（不开 thread、不调模型），让 Codex 更新缓存，再 sync。40 秒超时，结束后 kill。
  - `refresh_custom_models()` 会 `MODELS.clear()` 重建列表，所以末尾补一次 `sync_models()`（只在 ai_providers 已加载时，避免循环导入）；`main.lifespan` 启动时也 sync 一次，选了补进来的模型的 AI 重启后照样认得。
- **接口**：
  - `GET /api/models` 先 sync，每行加 `is_default`、`shared`。
  - 设置项 `default_model`：GET 返回当前生效的默认；PUT 时必须是可选的共用模型，否则 400，空串表示恢复「第一个能用的」。
  - `POST /api/settings/codex-models/refresh`。
- **设置页**：「🧭 模型线路」卡片里依次是「默认模型」（只列共用线路）、「Codex 登录态」（列出账号里的模型，带能否看图，「⟳ 刷新 Codex 模型」按钮）、中转站（原有功能：存线路、拉取模型列表、勾选）。名字都用 textContent。
- 测试：
  - `test_model_defaults.py` 6 项：旧默认值换成她选的；在但用不了的不换；没选时取第一个能用的共用模型、不取专属线路；Codex 目录增减和下架；重建列表时保留补进来的 Codex 模型；默认模型的保存校验（专属线路、用不了的、不存在的都返回 400）；没写模型名的 Codex 也算可用。
  - `test_deprecated_cli_models::…falls_back…` 改为固定默认模型。这条用例原来依赖「第一个可见模型」，现在的回退目标是默认模型。
  - 相关 71 个测试文件：其余失败都是既有失败（chatroom 前端、wechat_mode、deprecated_cli ×3、model_transport_mode ×3、model_audio_input、sentinel_routes ×2、bubble parity、lounge_friends_routes 的 main 注册用例，改动前都失败）。

## 67. §65–66（9d99317）审查修复（M1–M4、L1）

- **M1 同名覆盖**：`codex_models._OWNED` 记「名字 → 补进去的那个 dict 对象」，同步时只覆盖 / 删除 `MODELS.get(key) is _OWNED[key]` 的条目，同名的内置条目或小猫自己存的线路一律不碰。`update_settings` 保存中转站时，模型名不能和 `owned_keys()` 重名（返回 400，在改任何设置之前）。
- **M2 三态目录**：`_read_cache()` 读到返回列表（可能为空），没有或损坏返回 None。`current_catalog()` 读到时记为可信目录，并原子写一份副本到 `data/codex_models_catalog.json`；读不到时用内存里的或副本里的可信目录。`available_slugs()` 目录未知返回 None，明确为空返回空集合。明确的空目录会去掉补进来的条目；损坏的缓存不会。
- **M3 清理**：Linux 下用 `start_new_session=True` 启动（独立进程组）。`_stop()`：先关 stdin 等 3 秒，不退出就 `os.killpg(pid, SIGKILL)`（只杀这一组；Windows 用 `terminate_process_tree`），再等 3 秒，每一步都有时限。整个刷新过程最多约 timeout + 9 秒。
- **M4 先校验**：`default_model` 的校验移到 `update_settings` 最开头，不通过时内存里的设置也不会被改动。
- **L1**：刷新 Codex 目录后，同时以当前选择重新加载哨兵下拉框。
- 测试：`test_model_defaults.py` 新增：缓存损坏时保留可信目录、明确空目录时去掉；同名自定义线路不被覆盖也不被删除。fixture 改为隔离 `_OWNED`、`_TRUSTED` 和副本路径。`test_sentinel_codex_model.py` 中「坏缓存返回空」改为「坏缓存用上一份可信目录」。

## 68. §67（fb28318）复审修复（R1、R2）

- **R1 结构坏的目录被当成空目录**：`_parse` 改为先校验、再过滤。顶层必须是带 `models` 列表的对象；每一项必须是 dict，带非空字符串 slug（或副本格式的 id）；display_name / name、visibility 必须是字符串，input_modalities 必须是字符串列表，vision 必须是布尔值。任何一项不合格，整份目录都返回 None（当作读不到），继续用上一份可信目录，内存和磁盘副本都不更新。只有结构完全正确的空列表才算明确为空。隐藏模型仍然合法，只是不列出。
- **R2 刷新准备阻塞主循环**：`_build_codex_chat_environment(prepare_catalog=False)` 跳过陪聊模型目录的准备步骤（那一步会同步跑 `debug models --bundled` 子进程）。只问 `model/list` 不需要它，剩下的同步工作只是复制登录文件。`refresh` 从一开始就定好 deadline，准备、启动、问答共用这一个时限；清理沿用 §67 自己的上限。
- 测试：`test_model_defaults.py` 新增：6 种结构坏的缓存（`[None]`、`[{}]`、空 slug、`input_modalities: 42`、models 不是列表、顶层是列表）都保留动态模型，模拟重启后能从副本恢复；刷新时调用环境构造带 `prepare_catalog=False`。模型、Codex 相关 5 个测试文件共 27 项通过。

## 69. 公网访问：App 地址换成 home.animalshome.top + Cloudflare Access 登录凭证校验（第二道锁）

背景：小猫不想再用 Tailscale（和 VPN 互斥、很慢），已改为 Cloudflare Tunnel + Access（只允许她的邮箱一次性 PIN 登录）。隧道 `aionshome` 只转发到 `http://127.0.0.1:8080`，未登录访问 `/`、`/api/*`、`/ws`、`/uploads/*.apk`、`/static/*` 等一律 302 到 `claudexiaoke.cloudflareaccess.com`（已实测）。服务器原有的另一个隧道 `xiaoke-memory`（服务名 `cloudflared`）没动。

- **cafdd44 App**：`ConnectionEndpoint.CLOUDFLARE_HOST` 从占位的 `chat.example.com` 改成 `home.animalshome.top`，后台推送 / 截屏上传等走这个域名时会带上 `CF_Authorization` Cookie（原作者已有的逻辑）。4 个单测里的域名同步更新。CI 构建 40071854565 通过。
- **新 `aion-chat/cf_access.py`（纯 ASGI，main.py 里加在最外层）**：
  - 配置：`AIONSHOME_CF_ACCESS_TEAM`、`AIONSHOME_CF_ACCESS_AUD`、`AIONSHOME_PUBLIC_HOST` 三个都设了才生效，否则完全不做事（本地开发）。
  - 适用范围：Host 等于公开域名，**或者**带任何 Cloudflare 请求头（cf-ray / cf-connecting-ip / cf-access-jwt-assertion / cdn-loop）。Tailscale / 本机请求不受影响。
  - 凭证：`Cf-Access-Jwt-Assertion` 请求头优先，其次 `CF_Authorization` Cookie。
  - 校验：手写 RS256（用已有依赖 `cryptography`，不改 requirements.txt，避免 1G 服务器重建 venv）。只接受 `alg=RS256` 且带 kid；公钥从 `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` 取，缓存 1 小时；遇到陌生 kid 最多 60 秒重取一次（防被刷）；取失败保留旧公钥、验不过就拒绝。核对 `aud` 包含 AUD、`iss` 等于团队域名、`exp` / `nbf`（各留 60 秒时钟误差）。
  - 拒绝：http 回 403 JSON；WebSocket 发 `websocket.close` 1008。
- 测试 `test_cf_access.py` 17 项：有效凭证（请求头 / Cookie）放行且公钥只取一次；空、格式错、别的钥匙签的、别的 aud、别的 iss、过期、未生效、alg=none、陌生 kid、签名被改全部 403；Host 不同但带 cf-ray 也要验；WebSocket 无凭证关闭、有 Cookie 放行；Tailscale 请求不受影响也不去取公钥；未配置时不做事；钥匙轮换后重取一次能验过，刚取过时陌生 kid 不再重取；取公钥失败拒绝。security_access / request_limits / model_defaults 相关 56 项通过。
- 部署后要在 `/etc/aionshome.env` 加上三个环境变量（AUD 来自 Access 应用）。

## 70. 小克私聊落到 Codex（GPT 顶着小克人设说话）+ 私聊页看不出是谁

2026-10-24 小猫在 home.animalshome.top 打开「聊天」（小克的私聊页），标题还是 Aion Chat、看不出是谁；她喊「狐狸」，回话的是 GPT 顶着小克的人设。原因：服务器上只有 Codex 线路，新对话取下拉框第一项 / 默认模型「第一个能用的共用模型」都落到了 Codex（存下的会话模型是 `Codex-5.6-Sol`）。小猫定过：Codex 是 GPT，只给 Lucien。

- `config.is_shared_model`：`codex_cli` 不算共用线路 → 不当默认模型（设置页也不能选它当默认）。`default_model_key()` 一条都没有时返回空串（不再回到不存在的 `Gemini-3.5-flash`）。
- 新 `config.aion_model_key(key)`：先 `resolve_model_key`，结果是 codex_cli 就换成默认模型（可能为空）。用在：私聊 `_conversation_dict`、新建 / 改会话、三处发送 / 重新生成（替换 `resolve_model_key(conv["model"] or DEFAULT_MODEL)`）；群聊 5 个接口的 `model_key = body.model` 和环境语音的 aion_model；自主活动 `_aion_model()`。Lucien 的线路走 `_resolve_connor_model`，不受影响。
- `_stream_ai`：模型键为空时提示「还没有能用的线路：请到 设置 → 🧭 模型线路 接一条中转站（Codex 只给 Lucien 用）」，不拿别的模型顶替。
- 前端：私聊页下拉框不列 codex_cli，默认模型排第一（新对话就用它），没线路时显示提示项；用 `new Option` 代替拼 HTML。顶部加名字徽章（世界书 ai_name），标签页标题「小克 · 私聊」。群聊设置里小克的模型下拉框同样不列 Codex，Lucien 的照旧。
- 测试：`test_model_defaults.py` 改 1 项、新增 2 项（Codex 不当默认、存过的 Codex 换掉、没有别的线路时返回空不顶替；空模型给明确提示）。聊天 / 群聊 / 自主活动 / 座位相关 27 个测试文件改动前后结果一致（6 个既有失败）。
- **补充（同属 §70）主页入口改名**：小猫反馈「点进聊天不知道在跟谁聊，群聊在哪里」。主页图标「聊天」→「私聊」，载入世界书后显示成「<AI 名字>私聊」（如「小克私聊」）；「聊天室」→「群聊」（里面有「群聊」「私聊」两个标签，Lucien 和座位 3～6 的私聊也在里面）。图标文字和 alt 改为 `escapeAttr` 转义（AI 名字来自世界书）。home.html 相关 27 项测试通过。

## 71. 统一聊天入口：想跟谁聊就点谁，每位 AI 都能开多个窗口；换窗口能搜到别的窗口（方案：docs/施工方案-统一聊天入口.md）

小猫：「聊天不应该是能切换我想跟谁聊就跟谁，自由切换，还能自由选每个角色下面的窗吗？」原作者的结构是小克一个页面（多窗口）、Lucien 在聊天室里（前端只给一个）、座位每人只能一个房间（唯一索引）。她选：一次做完；每个窗口各聊各的，但换窗口能搜到别的窗口说过的。

**后台**
- `database.init_db`：去掉「同一座位多个房间就合并（消息挪到最早的房间、删掉其余）」的启动迁移和唯一索引 `idx_chatroom_seat_room`（`DROP INDEX IF EXISTS`），改为普通索引 `(type, actor, updated_at DESC)`。否则她开的窗口每次重启都会被并掉。
- `create_room`：`RoomCreate.new_window`。不带时座位私聊照旧返回 TA 最近用的房间（按 updated_at，原来是任意一个），带了就新建。去掉依赖唯一索引的 IntegrityError 分支。房间 ID 改成 `cr_{time_ns}{4 位随机十六进制}`：Windows 下连续建两个窗口时 time_ns 相同会撞主键，测试抓到了。`_get_or_create_seat_room`、`seat_target_room` 等「去 TA 的私聊」照旧取最近的那个。
- 每个窗口各聊各的（和小克用 conv_id 只取当前窗口一样）：
  - `context_builder` 的时间线加 `private_room_id`（只影响 connor 的私聊来源；stable_window 的 key 也带上它）。
  - `build_connor_1v1_context(room_id)` 传入它。
  - `seat_chat.merged_messages(actor, limit, room_id="")`：私聊窗口里只取这个房间，在群里照旧取 TA 所有私聊和群。
  - 后台用的（自主活动、摘要等）不传，照旧看全部。
  - `get_connor_1v1_recent` 改为取所有 Lucien 私聊窗口（原来只取最早建的那个房间）。
- 换窗口能搜到：小克（私聊所有窗口 + 群聊）和 Lucien（所有私聊窗口 + 群聊）本来就能用 `[MEMORY_SEARCH:…|history]`。这次补座位 3～6（权限对等）：
  - `active_memory_search._history_sources`：座位搜「自己名下所有 seat_1v1 + 群聊，不含 billiards_*」。actor 先核对是座位编号，才拼进 SQL。
  - `search_actor_memories`：接受座位。座位没有本地记忆库（长期记忆在 Hub、每轮已带），非 open= 的请求一律当 history 搜。
  - 来源行里座位显示名字。
  - `routes/chatroom`：
    - `_memory_actor_for_speaker` 认座位；
    - `_process_seat_commands` 开头认 `[MEMORY_SEARCH:…]`（开关 memory_search，同小克 / Lucien：有指令就先搜，系统提示「X正在翻找记忆……」）；
    - `_spawn_seat_followups` 起 `_chatroom_memory_search`。
  - `_chatroom_memory_search` 的座位分支：
    - 座位人设前缀，不给能力块；
    - 生成前 `_seat_still_ready` + `ensure_model_unchanged(model_key, pinned)`，线路快照从主回复带过来；
    - 存消息时同座位联网搜索的追加回复，只去掉指令、不执行。
  - `capabilities.seat_ability_texts` 加一段座位用的搜索说明。小克 / Lucien 的说明补一句「包括你别的私聊窗口」。

**前端**
- 新页 `/contacts`（`contacts.html/css/js`）：
  - 「私聊」列出每位 AI（`/api/actors` 里 builtin 或 enabled 的；头像、名字、窗口数、最近窗口），点开是 TA 名下所有窗口（按最近排序，可改名）和「＋ 和 TA 开新窗口」。
  - 「群聊」列出群（不含 billiards_*）和「＋ 新群聊」。
  - 所有文字用 textContent / DOM 构建。
- 打开方式：
  - 小克的窗口调外壳的新函数 `window.openAionConversation(convId|null)`：关子页面后 selectConv，或 newConversation。
  - 其他的走 `openSubPage('/chatroom?room=…')`（已有逻辑：带参数会重新导航常驻的聊天室子页面）。
- 主页「聊天」图标改指向 `/contacts`。撤掉上一提交里「小克私聊」改名那段；「群聊」图标保留。
- 小克私聊页和聊天室顶部加「👥」（换个人 / 换窗口）和名字徽章。聊天室徽章按房间类型显示 TA 的名字，群聊显示「群聊」，名字读到后会刷新；标签页标题同步。
- 本机实测（手机宽度）：
  - 联系人页无横向溢出；
  - 开 Jasper 新窗口 → 进聊天室显示「Jasper」徽章；
  - 在应用外壳里点小克的窗口 → 关子页面并切到该对话；
  - 点 Jasper 旧窗口 → 聊天室打开对应房间；
  - 控制台无错误。

**测试**
- 新 `test_multi_window_chat.py` 6 项：
  - Lucien、座位的窗口只看自己这个窗口的私聊加群聊，后台调用看全部；
  - 座位搜得到自己别的窗口，搜不到、也打不开别的座位的私聊；
  - Lucien 的搜索覆盖所有窗口；
  - 未知 actor 仍然拒绝；
  - 座位回复里的搜索指令会触发搜索，开关关着时不触发。
- `test_seat_chat.py` 两项改写：开新窗口 / 回到最近窗口；重启不合并多个窗口，旧唯一索引会被去掉。
- 相关约 480 项：失败的 10 项全是既有的（chatroom 前端 5 项、link_preview 2 项、health_ring 1 项，改动前就失败）。

## 72. 1c2a113..7829e17 审查修复（M1–M3、L1–L3）

- **M1 Codex 只给 Lucien，按三层补齐**
  - **规则**：`config.codex_forbidden(actor, key)` = actor 不是 connor 且模型是 codex_cli。
  - **第一层，存的时候就挡**：`actors.api_update_actor` 的 model / background_model（小克和座位，返回 400「Codex 是 GPT，只给 Lucien 用」）；聊天室设置 `_apply_config_update` 的 aion_model（400）。Lucien 的不受影响。
  - **第二层，启动清旧数据**：`config.scrub_codex_choices()` 在 lifespan 里、`codex_models.sync_models()` 之后运行。私聊会话里存的 Codex 换成默认模型（可能为空）；聊天室 aion_model、名片里小克和座位的 model / background_model 清空；connor 不动。日志只记位置，不记内容。
  - **第三层，以小克身份生成时再核**，全部走 `aion_model_key()`：
    - 闹铃、定时监控（schedule ×2，origin≠connor）；
    - 监控唤醒（camera）、定位唤醒（location）；
    - 朋友圈评论（moments）；
    - 基金分析、礼物、英文角小克那一份、记忆整理用的「最近会话模型」（memory._get_active_model_and_conv）；
    - 维修室小克、悬浮聊天的发送选项；
    - 约会剧场候选里的聊天室小克模型改用 codex_forbidden 过滤。
  - **人格成长**：`_main_ai_model()` 走 aion_model_key，没有合法线路就报错、这次不做；`_call_actor_model` 对非 connor 在调用前再核一次（紧贴调用、中间无 await）。
  - **复审反例二**：`memory_hub_jobs.own_model` 对小克 / 座位忽略名片里的 Codex（当作没设）。`background_model` 的 base 是 Codex 时视为没有合法线路，返回空，调用方会提示去接线路，不会从名片取回 Codex；后台线路选了 Codex 也不用。
  - 小剧场（theater_conversations 自带角色）和 Seeky 不是小克身份，没有改。
- **M2 公钥冷启动积压**：`_Certs` 改为记录 `attempted_at`（任何一次尝试，None = 还没取过）和 `fetched_at`（上次成功）。锁内先看是否已有新鲜的 kid，再看冷却：成功、失败、取到空列表都算一次尝试，60 秒内不再取，排队的这一批也一样，拿到同一个结果。
- **M3 主动消息去错窗口**：`seat_target_room` 查到她最后说话的地方（群，或这位座位的某个私聊窗口）就直接用；只有她还没说过话才取最近 / 新建。Lucien 的 `_get_or_create_connor_private_room` 同样改为先找她最后说话的 Lucien 窗口，没有再按 updated_at。
- **L1**：`refresh` 中子进程启动也计入同一个 deadline（`wait_for(shield(spawn))`）。超时或被取消时由 `_reap_late` 后台等待：启动迟到的进程照样用有时限的 `_stop` 收掉（任务引用保存在模块集合里）。
- **L2**：header、payload 必须是对象；kid 必须是非空字符串；exp 必须是有限数值（排除 bool、NaN、Infinity）；nbf 如果存在也必须是有限数值。类型不对直接返回 False，不靠扩大异常捕获。
- **L3**：`chat.js` 初始化识别 `?new=1`，优先于恢复旧窗口。等模型列表就绪后新建；启动等待期间她已经自己选了窗口就不覆盖；用 replaceState 去掉参数。
- **测试**
  - 新 `test_codex_only_lucien.py` 16 项：
    - 名片和后台线路的 Codex 对小克 / 座位无效，Lucien 照旧，覆盖空 usual 的反例；
    - 小克 / 座位保存 Codex 返回 400，聊天室小克模型返回 400，Lucien 可以；
    - 人格成长的两层挡；
    - 启动清理：会话、聊天室、名片三处被清，Lucien 不动；
    - 12 个小克身份入口都调用 aion_model_key（防以后新加入口漏掉）。闹铃、监控、定位的整条流程依赖太多，没做端到端测试，用这条静态回归和共用解析函数的行为测试覆盖。
  - `test_cf_access.py` 新增 15 项：空缓存 + 6 个并发 + 取失败 / 取到空，只取一次；6 种坏 header 稳定拒绝；7 种坏时间字段拒绝。钥匙轮换用例改为推冷却时间。
  - `test_multi_window_chat.py` 新增 3 项：座位、Lucien 都去她最后说话的窗口；入口页 new=1 分支在恢复旧窗口之前。
  - `test_model_defaults.py` 新增 1 项：启动慢于时限时，0.02 秒的时限在 0.15 秒内返回，迟到的进程被收掉一次。
  - `test_english_corner` / `test_floating_chat` 各有 1 项用了不在模型表里的假模型名，现在会被身份解析换成默认，所以在测试里登记这两个名字（以前它们在真正调用模型时也会被换掉，最终结果不变）。
  - 约 790 项相关测试里，失败的都是既有失败（chatroom 前端 5、link_preview 2、health_ring 1、english_corner 2 和另外 3 个文件级，与改动前一致）。

## 73. 多窗口改成「折中」：窗口只帮她整理，每只 AI 看得到所有私聊窗口最近的聊天

小猫问原作者是不是为了记忆才没给角色分窗口。我说明了：小克原来就有多个窗口，并且按窗口隔离上下文；分窗口多了，短期连贯感会变差。她选了折中：窗口只用来整理，AI 不会因为换窗口而断片。

- 撤回 §71 里的按窗口隔离：去掉 `context_builder` 的 `private_room_id` 参数（Lucien 回到「所有私聊窗口 + 群聊」）；`seat_chat.merged_messages` 去掉 `room_id` 参数（座位回到「自己所有私聊窗口 + 群聊」）。
- 小克私聊页的发送、重新生成、重发三处，以及闹铃、定时监控（schedule ×2）、监控唤醒（camera ×2）、定位唤醒（location），取上下文时不再传 `conv_id`，都是「小克所有私聊窗口 + 群聊」。这和他在群聊里本来的取法（chatroom.py 里不传 conv_id）一致。消息仍然存到当前窗口。
- 保留不变的：多窗口本身、`new_window`、启动不合并、联系人页、座位翻聊天记录（更早的内容靠它）、主动消息去她最后说话的窗口（§72 M3）。
- 测试：`test_multi_window_chat.py` 的两条「只看本窗口」改成「看得到 TA 所有窗口、看不到别的座位」；新增静态检查，确认上面四个文件里小克取上下文时不再按 conv_id 过滤。相关约 790 项测试的失败与改动前的既有失败一致。

## 74. 8a5e851 复审修复（R1–R3）

- **R1 兼容监控接口**：`camera.perform_cam_check` 第一步就用 `aion_model_key(model_key)`。不管 `/api/cam-check-trigger` 或其他调用方传进来什么，都在生成边界核一次，不依赖客户端，也不依赖启动清理。
- **R2 座位运行时限制**：
  - `seat_chat.seat_model` 每次都用 `codex_forbidden` 核对：启动清理时目录里还没有、刷新后才出现的 Codex 键，同样返回空。
  - `check_ready` 遇到这种情况抛 `SeatNotReady`，提示「X 选的是 Codex（Codex 是 GPT，只给 Lucien 用），请在相遇卡里换一个模型」。明确停下，不换别的模型。
  - 座位的主回复、追加回复、自主活动、人格成长都经过 `check_ready` / `seat_model`；后台线路之前已由 `own_model` / `background_model` 挡住。
- **R3 工具追加回复跨窗口**：
  - **小克私聊页**：`_private_memory_recent_messages`（记忆搜索追加轮）和联网搜索、周边搜索、查看动态三处「最近 6 条」去掉 `conv_id` 过滤，取小克所有私聊窗口最近的消息。回复仍存在当前窗口。
  - **聊天室**：新增 `_load_followup_messages(room_id)`：
    - Lucien 的私聊窗口取他所有私聊窗口；
    - 座位的私聊窗口取这位座位自己的所有私聊窗口（不含别的座位）；
    - 群聊只取这个群。
    - 替换了看监控、查看动态、周边检查、联网搜索、记忆搜索、周边搜索这 6 个追加回复里的 `_load_room_and_messages(room_id, limit=10)`。追加回复仍存回 room_id。
- **测试**
  - `test_codex_only_lucien.py` +2：
    - 监控入口第一步就用传入的 Codex 键过身份核对；
    - 座位名片存了 Codex 时 `seat_model` 返回空，`check_ready` 明确报错、提到 Lucien。
  - `test_multi_window_chat.py` +2，真实 SQLite：
    - 聊天室追加读取：座位、Lucien 跨窗口，不串到别的座位，群只读本群；
    - 小克私聊追加读取跨窗口；静态检查确认「当前窗口最近 6 条」的旧查询已全部去掉。
  - 相关约 790 项测试中，失败的都与此前的既有失败一致。

## 75. 排查「只认小克和 Lucien」的地方：AI 认人、相册、许愿、点名说话、人设编辑等（docs/排查-其他AI漏掉的地方.md）

小猫发现记忆库看不到其他 AI，要求全面排查，并且给座位 4～6 在页面上留好位置（空着、灰色）。

**AI 认人（影响模型输入）**
- A1 `context_builder.render_merged_timeline`：座位说的话用 `_seat_display_name`（actors.display_name），不再写成编号「ai3」。
- A2 / A3 `routes/chatroom.py`：新增 `_is_ai_sender`，座位也算 AI。替换了群聊 `recent_for_digest` 和三处记忆来源原文里的 `"assistant" if sender in ("aion","connor") else "user"`；name_map 补上座位名字（`_seat_name_map`）。

**后台**
- 相册 `album.py`：
  - `ACTOR_IDS` = 六个位置，`ALBUM_IDS` = family + 六个。
  - 生成照片默认进这位 AI 自己的相册（原来非 connor 的一律进 aion）。
  - 启动迁移：`source='generated' AND album_id='aion' AND actor IN (ai3..ai6)` 的照片挪回 actor 自己的相册。
  - 浏览 / 已看记录也接受座位。
  - `routes/album.py`：分类按 `actors.list_actors()` 生成，没入住的座位带 `placeholder: true`；三处 album_id 正则加上 ai3–ai6；actor_name / viewed_by 用登记表名字。
- 许愿池 `routes/wishes.py`：announce 的 target 可以是座位（先过 `seat_chat.check_ready`，没入住或没准备好返回 400），发到这位座位她最后说话的私聊窗口（`_seat_private_window`，没有就最近的 / 新建）。
- 群聊手动轮流 `reply_once`：speaker 可以是座位（先 check_ready），走 `_generate_seat_reply(group=True)`。编辑 AI 回复 `update_message` 用 `_is_ai_sender`。
- 人设编辑 `actors.py`：
  - 新增 `GET / PUT /api/actors/{id}/persona`，只处理 connor 和 ai3–ai6，aion 返回 404（仍在世界书改）。
  - PUT 用已有的 `_clean_sections` 校验（必须有 identity_core、分区必须认识、内容为字符串、不能全空），写错不会清空已有人设。
  - connor 同时写 `connor_persona_sections` 和编译后的 `connor_persona`；座位写 `persona_sections`。

**前端**
- 新增 `static/seats.js`（`AionSeats.load()`）：六个位置，还没入住的标 placeholder。
- 淘宝：伙伴卡片改成一行可横向滑动的小卡片（头像、名字、状态，空位灰着），原来左右两张叠在逛街面板上的 absolute 布局作废；去掉原作者的 app 图标。
- 日记：座位空位标签灰着、不能点，标签行可横向滑动。
- 许愿池：「去实现」按入住的人动态生成。作者选择那段代码页面上没有对应的框（死代码），一并改成 DOM 构建。
- 相册：空位标签灰着；上传、移动的下拉只列入住的。
- 礼物页、主页礼物弹窗：送礼人按名字显示。
- 监控日志：叫醒对象写座位名字。
- 聊天室：
  - 座位私聊借第二道光环显示 TA 的主动陪伴；
  - 手动轮流出现「狗蛋 说」等按钮（只列入住的）；
  - 座位回复菜单里有「编辑」。
- 壁纸：座位说话也冒泡。
- 家庭动态：座位先找 `<座位>-door.webp`，没有就借 Lucien 的门。
- 世界书「AI 人设」：顶部一排人（小克 / Lucien / 狗蛋 / 空位）。选小克是原来的编辑区；选其他人用同一套分区，单独「保存 X 的人设」。底部「保存」在看别人时也会先存 TA 的。

**测试**
- 新 `test_seat_pages.py` 6 项：
  - 座位名字进时间线；座位算 AI；
  - 相册每人一本、旧照片迁回、空位标记；
  - 人设接口：读、写、四种坏输入拒绝、aion 拒绝、Lucien 写入编译文本；
  - 手动轮流、编辑接受座位；
  - 许愿通知座位：未入住和未知目标返回 400。
- 本机实测（手机宽度）：淘宝卡片不再重叠，页面无横向溢出；日记、世界书人员切换和读取人设正常。
- 约 870 项相关测试中，失败的都是既有失败（新增出现的 taobao_native_mcp ×4 早就在既有失败名单里，需要本机淘宝工具）。

**还没做**：记忆库（座位的记忆在 Hub，等 Hub 窗口回复）、维修室、声音设置。

## 76. 记忆库的「Hub 记忆」页 + 维修室让座位一起出主意

**Hub 记忆**：狗蛋等座位的记忆只在 Memory Hub 里。按 Hub 窗口的约定，用 Hub 的管理接口（REST + HUB_SECRET），不用 MCP。
- 新 `routes/hub_memory.py`：
  - 地址取 `MEMORY_HUB_URL` 去掉 `/mcp`；认证复用 `memory_hub_bridge._headers`，即 `MEMORY_HUB_TOKEN`，只在服务器上用，返回给浏览器的内容里没有。
  - `GET /api/hub-memory/list?actor=`：actor 必须是六个位置之一，Hub 身份取 `actors.memory_hub_id`（没入住或没接 Hub 的返回 400，未知的返回 404，都不会请求 Hub）。返回字段白名单，去掉向量、历史等。
  - `PUT /{id}`：只能改 content、importance 1–10、category，带上 `changed_by="ceci"`。
  - `POST /{id}/archive`。
  - `DELETE /{id}?confirm=delete`：页面两次确认后才带 confirm。
  - id 用正则 `^[A-Za-z0-9_-]{1,80}$` 校验并 quote，防路径注入。
  - Hub 不可用时返回 503；Hub 返回 404 时原样传 404；其他错误返回 502，不透传 Hub 的错误正文。
- 新页面 `/hub-memory`（hub-memory.html / js / css）：
  - 选人（六个位置，空位灰着）；
  - 「正在用 / 已归档」两栏，翻页；
  - 正在用的可以改、归档；已归档的可以彻底删除（两次确认）；
  - 所有文字用 textContent 渲染。
- 记忆库页面加入口。
- 只给小猫的页面用，不进任何 AI 的能力。页面在 Cloudflare Access 和第二道锁后面。
- 已实测服务器上 Hub 的管理接口认这个 token：只取了条数和字段名，没有读内容。jasper 199 条、claude 514 条、lucien 315 条；id 格式符合校验。
- Hub 没有「取消归档」接口，已归档的只能看和彻底删除，待问 Hub 窗口。

**维修室**：小猫想要「小克或 Lucien 干活、狗蛋出主意」。动手只能是 Lucien，因为执行靠 Codex CLI；小克要动手得在那台机器上装好并登录 Claude Code，目前没有。
- `repair_context.repair_advisors()`：小克加上已经入住、check_ready 通过的座位。`repair_names()` 把这些座位的名字补进 names。
- `build_context`：座位用自己的人设；协作者列表和「分工」写出所有出主意的人，执行者只有 Lucien。
- `repair_worker`：`reply_aion(store, job, actor='aion')` 通用化（保留原名，旧测试按原名 patch）。座位用自己的模型：先 `check_ready` 固定快照，生成前再核一次 `check_ready` 和 `ensure_model_unchanged`，变了就不回复、不换模型。`discuss` 的 both 顺序是：所有出主意的人，然后执行者。
- `repair_routes`：`recipient` 用正则 `both|aion|connor|ai3–6`，并且必须是 both、connor 或当前出主意的人，否则返回 400。bootstrap 返回 advisors。
- `repair_store.can_steer`：只有发给执行者或大家时才能中途补充（原来是「不是 aion 就行」，否则发给座位也会被当成能补充给执行者）。
- `repair.js`：参与者显示所有出主意的人；收件人下拉加上入住的座位。
- 测试：
  - 新 `test_hub_memory.py` 9 项（假 Hub）：走管理接口并带密钥；字段白名单；密钥不出现在返回里；没入住或未知的座位不发请求；改、归档、删除（删除必须带确认）；5 种坏 id 不发到 Hub；Hub 未接返回 503。
  - `test_seat_pages.py` +2：出主意的人和 can_steer；both 的顺序是 小克 → 狗蛋 → Lucien。
  - `test_repair_room.py` 中「大家」用例固定只有小克出主意（这条测试写于有座位之前）。
  - 约 890 项相关测试中，失败的与之前的既有失败一致。

## 77. Hub 记忆：「找回」+ 预留主人钥匙

Hub 窗口新上线了 `POST /api/memory/{id}/unarchive`，把已归档或自动淡出的记忆恢复成 active；找不到时返回 `{"status": "not_found"}`。
- `routes/hub_memory.py`：
  - 新增 `POST /api/hub-memory/{id}/unarchive`，id 校验同其他接口，`not_found` 转成 404。
  - Hub 的「主人钥匙」上线后（Hub PR #98），在 `/etc/aionshome.env` 设 `MEMORY_HUB_OWNER_SECRET`，这一页就改用它：用它改的记忆，Hub 会记成「小猫亲手确认」。没设时照旧用 `MEMORY_HUB_TOKEN`。替换时同时去掉 cfg.headers 里自带的 Authorization，避免两把钥匙混用。只在服务器上用。
  - 不再传 edited_by_owner：Hub 按钥匙判断。
- 页面：已归档的记忆多一个「找回」按钮。
- 测试 `test_hub_memory.py` +2：找回走 unarchive 接口；设了主人钥匙后改用它；not_found 返回 404。

## 78. eebfef2..d131505 审查修复（M1–M3、L1–L4）

- **M1 世界书人设读取前可保存**：
  - 每次读取有递增序号 `wbPersonaLoad`，回填时同时核对人和序号，`ai3→ai4→ai3` 迟到的结果不会回填。
  - 读到之前文本框只读、保存按钮禁用（`setOtherPersonaEditable`）；`saveOtherPersona` 自己也检查 `wbPersonaLoaded`。
  - 读取失败时保持只读，提示刷新，不会把空表单当成旧人设覆盖上去。
- **L1**：`saveOtherPersona` 返回是否成功。底部「保存」在 Lucien / 座位那一份没存上时停下，提示「世界书也先没保存」，不再显示世界书保存成功。
- **M2 相册迁移每次启动都执行**：新表 `album_migrations`。`seat_albums_v1` 迁移和「做过了」的记号在 `BEGIN IMMEDIATE` 的同一个事务里，只做一次；失败回滚后下次可以重试。之后她手动挪到哪本相册，不会再被改回去。
- **M3 重要度尺度**：Hub 用 0–1 浮点。
  - 后端 `MemoryEdit.importance` 改为 `float` 0–1。
  - 页面显示成 1–10（`round(x*10)`）。编辑框默认「不改（现在 N）」：只改正文时不提交 importance；选了才提交 N/10。正文没变就不提交 content，什么都没改直接关闭。
- **L3 Hub 回应校验**：`_call` 只接受 JSON 对象。非 JSON（如登录页）或非对象返回 502「这次没有确认成功」；带 error 的返回 400，用固定文案，不传 Hub 原文。列表要求 items 是列表。改动类操作（改、归档、找回、删除）只返回 `{ok, id, status}`。
- **L2 群聊摘要漏座位**：`recent_for_digest` 改为收 user 或 `_is_ai_sender`；`group_participants` 补上座位名字。发言顺序策略不变。
- **L4 维修室座位背景缺自己的私聊**：`context_builder._merged_timeline_sources` 新增座位分支，取 `seat_1v1 AND actor=?`（这位的所有私聊窗口，带时间窗），不含别的座位，不回退到 connor。目前只有 `repair_context` 用座位身份调用这个函数（英语角只传 aion / connor）。
- **测试**：
  - `test_hub_memory.py`：重要度改用 0–1；新增 1–10 整数被拒、3 种看不懂的回应不算成功且不带 Hub 原文、改动类只返回 id 和 status。
  - `test_seat_pages.py`：相册迁移只做一次（手动挪回小克相册后重开两次仍是小克）；旧用例改成模拟升级前没有迁移记号的库；维修室座位背景带自己的私聊、不带别的座位；摘要和世界书守卫的源码检查。
  - 约 900 项相关测试中，失败的与此前的既有失败一致。

## 79. a8bed46 复审 L3 收尾：Hub 改动类操作要看到明确的成功状态

- 按 Hub 实际协议（memory-hub `memory_ops`）：改 → `updated`，归档 → `archived`，找回 → `unarchived` / `already_active`，删除 → `deleted`；找不到 → `not_found`。
- `_done(memory_id, data, success)`：
  - `not_found` 一律转 404（四个接口统一）；
  - `ok is False` 或 status 不在这次操作的成功集合里，就不算成功：带非空 status（如 guardrail_blocked、denied）返回 400 固定文案，空对象或无 status 返回 502；
  - 成功只返回 `{ok, id, status}`。
- 发现 Hub 那边的小问题（不在 AionsHome）：Hub 的 `archive_memory` 对不存在的 id 也返回 `archived`，这条要告诉 Hub 窗口。
- 测试 `test_hub_memory.py`：假 Hub 改为按真实协议回 status；新增归档必须见到 archived（空对象、ok=false、denied、guardrail_blocked、别的操作的成功状态都不算），四个接口的 not_found 都是 404。共 25 项通过。

## 80. Hub 归档的新回应 + 主人钥匙上线

- Hub 窗口修了归档：找不到 → `not_found`，已经归档过 → `already_archived`，正常 → `archived`，后台自动操作遇到锚定或保留的 → `refused`（带 reason）；DELETE 找不到也返回 `not_found`。
- `routes/hub_memory.py`：归档的成功集合改为 `archived` + `already_archived`。`refused` 走已有的「Hub 明确拒绝」分支，返回 400 固定文案，不带 reason 原文。
- 测试 +2：already_archived 算成功；refused 返回 400，且 detail 里不含 reason。
- 运维（2026-10-25，已做）：
  - Hub 主人钥匙已写进服务器 `/etc/aionshome.env` 的 `MEMORY_HUB_OWNER_SECRET`。文件权限 600 root，用 stdin 写入，没进命令行；原文件备份为 `.bak-20261025`（600）。重启后，Hub 记忆页列表用主人钥匙正常（小克 516、狗蛋 206），日志里没有钥匙。
  - AI 和 bot 的记忆桥接继续用 `MEMORY_HUB_TOKEN`，§77 的代码只在 `_hub()` 里换钥匙。
  - Codex 登录目录本来就是 700，auth.json 600。`/var/lib/aionshome/AppData`、`Local`、`AionsHome` 三层去掉组可写（775 → 755）。

## 81. 小猫反馈：小克没用自己的模型、摄像头一片黑、时间不对、群聊入口落到私聊

- **小克没用自己的模型**（她给小克的名片选了 `[kiro量高缓]claude-opus-4-6-thinking`，私聊却走了另一条中转站线路的第一个模型）：私聊会话里存的模型是空的（§70 启动清理会把 Codex 换成默认值，而当时默认为空），`aion_model_key` 回退到全局默认 `default_model_key()`，也就是第一个能用的共用线路。
  - 新增 `config.aion_own_model()`：先看小克名片的 model，再看聊天室的 aion_model。要求还在、没停用、不是 Codex、能用。
  - `aion_model_key`：选了、还在、不是 Codex 的就用；否则先用 `aion_own_model()`，再退到 `default_model_key()`。
  - `/api/models` 增加 `aion_own` 标记；私聊下拉框把小克自己的模型排第一，新窗口就用它。
  - 「默认模型不能选 Codex」是 §70 的既定规则（Codex 只给 Lucien），没有改。
- **时间不对**：VPS 是 Etc/UTC，AionsHome 大多数地方用不带时区的 `datetime.now()`。在 `/etc/aionshome.env` 加了 `TZ=Asia/Shanghai`（只影响 AionsHome，Hub 不受影响），改之前确认没有待触发的日程；原文件备份为 `.bak-20261027`。运维改动，不在 git 差异里。
- **摄像头一片黑**：监控源是 `phone`，手机 App 没连上或没给相机权限，所以每次取图都失败，代码本身没问题。但发现每次失败都会叫醒一位 AI：10-26 一天巡逻 96 次、失败 96 次、叫醒 96 次，白白耗模型额度。
  - `camera._handle_monitor_no_image`：照常记日志；最多每 `NO_IMAGE_WAKE_GAP`（6 小时）叫一次人。其余几次日志里 `call_core=False`、`final_wake_targets=[]`，不叫人。
- **群聊入口有时进的是私聊**：主页「群聊」原来打开 `/chatroom`，会回到上次待过的房间（可能是私聊）。
  - 改成 `/chatroom?view=group`，初始化时 `crOpenLatestGroup()` 打开最近的群（不含 billiards_*）。
  - 聊天室是常驻子页面：新增 `window.onAionSubPageVisibilityChanged`，从主页「群聊」再次打开时如果停在私聊上就切回群聊（只在 URL 是 view=group 时）。
- **测试**：
  - `test_model_defaults.py` +1：没选模型时用小克自己的；选了别的照她选的；Codex 换成他自己的；名片是 Codex 时不算，退到默认。
  - `test_phone_camera_trigger_wiring.py` +1：连续失败时第一次叫人，10 分钟后只记日志，6 小时后再叫一次。
  - 相关 282 项测试中，失败的都是既有失败。

## 82. 免费语音试听没声音：edge-tts 升级到 7.3.1；巡检记录

- 小猫说设置页里的「免费语音试听」试听不了。在服务器上直接请求 `/api/tts`（voice `edge:zh-CN-YunxiNeural`）返回 500：微软的 `speech.platform.bing.com` 握手返回 403，是旧版 edge-tts 不带新的验证参数。
- 在服务器的临时 venv 里对比：7.3.1 正常生成音频（14KB mp3），7.2.8 仍然 403。`requirements.txt` 改为 `edge-tts==7.3.1`（全仓只有这一处版本固定）。部署时 update.sh 发现 requirements 变了会同步依赖。
- 运维（2026-10-27，已做，不在 git 差异里）：
  - 哨兵线路改为 `codex_luna`。这是小猫之前定的，模型 `gpt-5.6-luna` 早就选好了，只是巡逻线路一直停在 original。
  - 监控巡逻暂停（`/api/cam/monitor/stop`，`monitor_enabled=False`）。手机相机从没接上（`armed=false`），每次巡逻都失败并叫醒 AI，群聊里堆满了「失败回执」。等手机 App 连上后由小猫重新打开；§81 的限频上线后，就算忘了关也最多 6 小时提醒一次。
- 巡检：通过服务器内部端口（ssh 转发，不经过 Cloudflare）按手机尺寸逐页打开主页列出的 35 个页面。没有横向溢出、坏图或脚本报错。主页「群聊」仍落到 Lucien 私聊，等 §81 部署后修复。设置页是一长页 15 个分区，打开先看到语音，「模型线路」在第 6 个、「哨兵」在第 11 个，交互问题见《施工方案-设置和聊天设置重排》（待写）。

## 83. 巡检私聊发现的三个问题 + 验收清单

小猫希望每次审查也查功能和视觉：「每次它颤颤巍巍检查出一堆错，结果我还是用出 bug 了」。新增 `docs/验收清单.md`（私聊 / 群聊 / 开关 / App / 视觉，每项写清怎么做、对的样子）和 `tools/page_sweep.js`（浏览器控制台运行的全页巡检）。**以后审查请按验收清单实际点一遍**，不只看代码。

本机最新代码 + 测试数据，按手机尺寸巡检小克私聊，发现并修复：
- **小克的模型下拉里出现「Jasper专属」线路**：别人的专属线路（座位自己填的 API，route_id `seat_ai3` 等）会让小克穿错衣服。
  - 新增 `config.others_private_route(actor, key)`；`aion_model_key`、`aion_own_model` 都排除别人的专属线路（`seat_aion` 是小克自己的，可以用）。以前存下的选择也会被换掉。
  - `chat.js` 的私聊下拉同样过滤。
- **打开 /chat 停在「选择或创建一个对话开始聊天」**：有对话、但 localStorage 里没记住上次开的是哪个时，原来什么都不选。现在打开最近的那个（`/api/conversations` 按 updated_at 倒序）。
- **没打开对话时发送没反应**：`send()` 原来在没有当前对话时直接 return，字留在输入框里、没有任何提示。现在先 `newConversation()` 再发；开不了就提示。
- 记下待修：回复失败时页面写「回复连接中断」，连不上线路时也这么写，原因说得不准（验收清单 A6）。
- 测试：`test_model_defaults.py` +1，别人的专属线路不给小克、小克自己的可以、以前存过的被换掉。相关 38 项通过。JS 语法检查通过；本机实测：自动打开最近对话、下拉里没有 Jasper 专属、没开对话也能发出去。

## 84. 巡检群聊：没有群时「群聊」入口是空白页；群人数写死

- **没有群时点主页「群聊」**：页面停在「选择或创建一个聊天室开始吧」，建群的按钮藏在侧栏里。`crOpenLatestGroup()` 在没有群（不含 billiards_*）时自动建一个「家」（`POST /rooms {type:'group'}`），刷新列表后打开；建失败就提示。本机实测：进入后标题「群聊 · 家」，徽章「群聊」，「让他们聊」按钮出现。
- **空群提示写死「三人群聊」**：现在家里有小猫、小克、Lucien、狗蛋。新增 `crGroupSize()`：3 加上已入住的座位数，用中文数字显示。
- JS 语法检查通过。

## 85. e0b15d5 审查修复（M1–M3、L1）+ 视觉

- **M1 小克的后台仍能用别人的专属线路**：
  - `memory_hub_jobs.own_model` 遇到 `others_private_route`（以及原来的 Codex）就当作没设。
  - `background_model` 的 base 和 chosen 都检查 `others_private_route(actor, …)`：base 是别人的 → 返回空（调用方提示，不顶替）；chosen 是别人的 → 回到本人的 base。`pin_background_model` 随之安全。
  - `actors.api_update_actor` 保存 model / background_model 时，别人的专属线路返回 400。
  - 这套规则对所有位置通用（座位也不能用别的座位的专属线路），Lucien 的 Codex 规则不变。
- **M2 主页「群聊」可能重复建「家」**：
  - `crOpenLatestGroup()` 改为同一时间只跑一次（`_crOpeningGroup`），初始化和「从主页再次打开」共用。
  - 先 `await loadRooms()` 真正读到列表，读不到就提示并停下，不当成「没有群」。
  - 服务端：`RoomCreate.ensure_group`。创建群时在 `BEGIN IMMEDIATE` 事务里再查一次（不含 billiards_*），已有就返回最近的那个，真没有才 INSERT；两个页面同时进来只会建一个。没有给群名加唯一约束。
- **M3 没开对话就发送时可重入**：
  - `send()` 在等待开窗之前就把 `sending` 设为 true，开窗结束后释放。
  - `newConversation()` 改为共享进行中的创建（`_creatingConversation`），初始化自动开窗、发送开窗、连点共用这一次。
  - 开窗异常在 try 里接住，统一提示「没能打开对话」，并可靠释放。
- **L1 无图限频不是原子认领**：在第一个 await 之前就认领（`_last_no_image_wake = now`）。叫人失败也不放回，避免连环叫。
- **视觉**：
  - 群聊顶部在手机宽度下按钮换到第二行（`.chat-header` 换行、`.header-actions` 换行靠右，标题最少保留 96px）。本机实测手动轮流时三位「X 说」、「让他们聊」、⚙ 都在 375px 内，群名可见。
  - 联系人页补上座位 4～6 的灰色空位（不可点）。
- **验收清单 A2 口径**：应用启动先显示主页是设计如此。A2 改为「进入小克私聊时自动打开最近的对话」。
- **测试**：
  - `test_codex_only_lucien.py` +1：名片模型 / 后台线路是别人的专属线路时，own / background / pin 都不用它，保存时 400。
  - `test_phone_camera_trigger_wiring.py` +1：两次失败同时进来只叫一次。
  - `test_multi_window_chat.py` +2：真实 SQLite 并发两次 `ensure_group` 只建一个；前端单飞和发送占位的源码检查。
  - 相关 74 项通过。
- **本机实测**：连点发送只新建 1 个窗口；联系人显示 3 位 + 3 个灰色空位；群聊空态显示「四人群聊」。

## 86. cfa48d5 复审 V1：两行群聊顶栏盖住第一条消息

- 原因：`.chat-header` 是绝对定位、浮在消息上面的，消息区靠写死的上边距（72px / 76px / 手机 62px+安全区）避开它。§85 让顶栏在手机上换成两行后，顶栏底部约 98px，第一条消息的头像、名字、菜单被盖住约 20～25px。
- 修法（按你的建议，不再写死一个更大的数）：`chatroom.js` 新增 `crKeepMessagesBelowHeader`。量顶栏的实际底边（`getBoundingClientRect`，含安全区、换行、按钮多少、字号），给 `#messages` 设 `padding-top = 顶栏底边 − 消息区顶边 + 8px`。只在比样式表原本的上边距大时才设，顶栏一行时清掉内联值、回到原样。
  - 触发：`ResizeObserver(顶栏)` + 窗口 resize。另外在 `updateHeaderActions()`（按钮增减）和 `crShowRoomWho()`（换房间、名字读到）里直接再量一次（`window.crSyncHeaderSpace`），不只依赖 ResizeObserver（页面在后台不绘制时它不触发）。
- 本机实测 375×812，群里已有一条狗蛋的消息，手动轮流三个按钮：顶栏底边 98，第一条消息顶边 112，没有遮挡（修之前是 78，被盖 20）。切到狗蛋私聊（顶栏一行，底边 63）后上边距回到 72px。
- 顺带查了缓存：`/static` 的回应本来就带 `Cache-Control: public, max-age=0, must-revalidate`，浏览器每次都会先确认有没有新版本，所以 `?v=` 版本号没跟着改不会让手机用到旧脚本，这次不需要改版本号。

## 87. 小猫手机真用：入口页滑不动、窗口删不掉、看不到思考过程

小猫 2026-10-28 在手机上点：「主页的聊天那里，点开小克的窗口，下面的位置划不下去，然后还有个问题是无法删除窗口。另外，私聊里好像无法看到 ai 的原生思维链？」

- **滑不动**：`common.css` 给所有页面设了 `html, body { height:100% }`、`body { overflow:hidden }`（聊天页需要这样）。`/contacts` 和 `/hub-memory` 是我新做的长页面，没改回来，小克展开 14 个窗口后下面滑不到。
  - 修法：这两页 `body` 改为 `height:auto; min-height:100%; overflow-y:auto`。只写 `overflow-y:auto` 不够：body 还是一屏高，吸顶栏会跟着 body 一起滑走。
  - 吸顶栏原来是半透明的 `--surface`，滑动时下面的字透上来叠在一起。改成和其他页一样加 `backdrop-filter: blur`。没有用实底，因为皮肤主题里 `--bg` 本身也是半透明。
  - `tools/page_sweep.js` 新增 `stuckBelow` 列：内容底边超出屏幕，但文档和页面里任何滚动容器都滑不动时，报超出的像素。之前只查横向溢出，所以漏了这次。
- **删不掉窗口**：入口页每个窗口在 ✎ 旁边加 🗑。
  - 先 `confirm`（「删掉「X」？里面的 N 条聊天会一起删掉，删了找不回来。（已经记进记忆 / Hub 的不受影响）」），确认后再调已有的接口：小克 `DELETE /api/conversations/{id}`，其他人和群聊 `DELETE /api/chatroom/rooms/{id}`。删完刷新列表，给出提示。
  - 服务端没改。
- **看不到思考过程**：
  - 服务器上小克 78 条回复里有 77 条存了 `reasoning_content`，💭 按钮也一直都有，问题出在显示上：它在 `.msg-feedback-actions` 里面。这组按钮在手机上默认 `display:none`，要先点一下那条消息才出现，而且只有 18px、透明度 .28。
  - 修法：小克私聊和聊天室（Lucien、座位、群聊）都把它拿出来，做成一直显示的「💭 思考」小按钮（最小高 28px），紧跟在名字后面。放在名字后面，是因为点消息时时间和 ⋯ 会插进来，如果放在后面会被挤开、点空，本机实测确实点空过一次。
  - 思考弹窗原来只往消息上方弹，高度上限是 min(28vh, 260px, 上方剩余空间)，消息靠近屏幕顶部时只剩一条缝。改为上方放不下时弹到按钮下方，上限 min(60vh, 480px)。`chat.js` 和 `chatroom.js` 两处一样。
- **本机实测（375×812）**：
  - 入口页展开 14 个窗口，能滑到底，顶栏一直在，不透字。
  - 删除「巡检窗口 7」：先问，问的文字正确；删完剩 13 个，并提示「已删掉」。
  - 小克私聊：「💭 思考」一直可见，点一下往下弹，长思考可以滚动看完。
  - 狗蛋私聊：点一下就开。
  - 全页巡检 36 页里，`stuckBelow` 报了 `/moments` 20px、`/app-supervision` 228px。逐个看过，都是收起的面板或藏着的评论菜单，不是滑不到。
- **测试**：新增 `test_phone_usability.py` 4 项（两页能滚、先问再删、💭 不在隐藏按钮组且紧跟名字、弹窗能往下弹）；相关 75 项通过。

## 88. AI 自己开 / 停哨兵巡逻；拿不到画面一律不叫人

小猫 2026-10-28：「有没有办法 ai 让哨兵停下来就能停，想看就让哨兵去看？」她的决定：**AI 能自己关、也能自己开**（开关都会提示她）；**停多久由 AI 自己决定**：写了时长就到点恢复，不写就一直停到有人打开。所有 AI 权限一样。

背景：10-27 那晚手机相机没接上，巡逻每 10～20 分钟取图失败就叫醒小克，他停不下来（§84 已经限成 6 小时一次，监控也关了）。「想看就看」原本就有：小克、Lucien、座位都有 `[CAM_CHECK]`，这次没改。

- **新模块 `patrol_control.py`**：
  - 指令写法：`[巡逻:停]`、`[巡逻:停|3小时]`、`[巡逻:开]`，也认【】、全角冒号、竖线和几个同义词。看不懂的写法会从文字里去掉，但不执行。一条回复里写了好几条，只按最后一条做。
  - 时长：支持「3小时」「90分钟」「半小时」「1天」「1.5h」，最短 10 分钟，最长 72 小时；读不懂时按「一直停」处理。
  - 停：`cam.stop_monitoring(resume_at=…)`；开：`cam.start_monitoring()`。同时记录 `patrol_changed_by`。
  - 每次开 / 停都在当前对话或房间里留一条系统提示，例如「🛡 小克把巡逻停了 3 小时，21:30 自动恢复」「🛡 狗蛋把巡逻停了，等有人再打开」。提示用的是带 `system_model_context` 的系统消息，之后的模型也看得到。
  - 开关失败时只发一句「想停巡逻，但没成功」，这条回复本身照常保存。
- **挂在哪里**（`patrol_control.process(...)`）：
  - 小克私聊：发送、编辑重发、重新生成（`routes/chat.py` 3 处，在小组件指令前面）。
  - 聊天室：`_process_chatroom_commands`，覆盖小克在群里和 Lucien。
  - 座位：`_process_seat_commands`。
  - 聊天室和座位这两处都放在函数最前面，因为后面「翻记录」那一步会提前返回。
  - 后台回复：`schedule._process_background_reply_commands`，覆盖巡逻叫醒、闹铃、定位、自主活动。被叫醒时最可能说「先别巡逻了」，所以这一处最重要。
- **能力说明**：小克 / Lucien 的说明（`build_capability_prompt_items`）和座位的说明（`seat_ability_texts`）里，在 `[CAM_CHECK]` 后面加一段 `patrol_control.ability_text`，带当前状态（开着 / 停着 / 谁停的 / 几点恢复）。和 `cam_check` 用同一个开关。
- **到点恢复**：`main.py` lifespan 里启动 `patrol_control.resume_loop()`，每 30 秒检查一次 `patrol_resume_at`。到点后在线程池里调 `start_monitoring`，写一条巡逻日志并广播。关停时 cancel 并 gather。
- **小猫自己在设置里开 / 关**：`/api/cam/monitor/start|stop` 会清掉 `patrol_resume_at` 和 `patrol_changed_by`。她关掉的巡逻不会被 AI 之前定的「到点恢复」重新打开；之后 AI 仍可以用 `[巡逻:开]` 打开，这是她选的。`/api/cam/status` 多返回这两个字段。
- **停了马上再开会有两个巡逻线程**（这次顺手修的旧隐患）：
  - 原因：`start_monitoring` 原来只看 `self.monitoring`。旧线程可能还在 0.5 秒的小睡里，醒来看到 `monitoring` 又是 True，就会和新线程一起跑。
  - 修法：加 `_monitor_gen` 代数，每次开巡逻加一；`_monitor_loop(gen)` 发现代数变了就退出。不传 gen 时行为不变。
- **拿不到画面一律不叫人**（她选的方案第 2 条）：
  - `_handle_monitor_no_image` 只记日志并广播（`call_core: False`、`final_wake_targets: []`），不再调 `_wake_core_targets`。
  - 删掉了 `NO_IMAGE_WAKE_GAP` 和「算叫谁」。
  - 原来的 3 个测试（失败叫一次 / 6 小时一次 / 并发只叫一次）合并成 1 个：3 次同时失败，叫人 0 次、日志 3 条。
- **测试**：
  - 新增 `test_patrol_control.py` 9 项，覆盖：各种写法和时长；停 3 小时后到点自动恢复；不写时长就一直停；最后一条为准；开关失败不丢回复；她手动开关会清掉 AI 定的恢复时间；停了马上再开只有一个线程（真实线程）；能力说明带当前状态；每条回复路径都挂上了。
  - `test_phone_camera_trigger_wiring.py` 改为上面那 1 项。

## 89. 4a0915b 复审 R1–R4、L1

- **R1 排着队的到点恢复会盖掉小猫后来的手动关闭**：
  - `CameraMonitor` 新增 `_patrol_lock`（RLock）和 `_patrol_version`。`start_monitoring(changed_by=…)` 和 `stop_monitoring(resume_at=…, changed_by=…)` 都在锁里改状态、存配置，并把版本加一。小猫手动开关（`routes/cam.py`，`changed_by=""`）、AI 指令（`patrol_control.apply`，`changed_by=actor`）、启动和换画面源用的是同一对入口。原来在锁外补写 `patrol_changed_by` 的地方都去掉了。
  - 到点恢复改成两步：`patrol_snapshot()` 先在锁里取 (版本, resume_at)；真正动手的 `resume_if_due(version, resume_at, now)` 在线程池里执行，并在锁里重新核对四件事：版本没变、仍然停着、resume_at 没变、确实已经到点。四项都满足才调用 `start_monitoring`。所以无论任务在线程池里排队多久，只要期间有人开关过或改过时间，这次恢复就作废。
  - 回归测试：
    - 你给的反例原样复现：单 worker 线程池先被占住，恢复任务排进去以后调用真实的 `cam_monitor_stop`，再放行。结果是巡逻保持关闭，返回空提示。
    - AI 到点前延长暂停；手动开了又关。
    - 两次到期检查同时进来：只开一次，只有一条提示。
    - 「取消 `resume_loop` 但线程池里的恢复还没跑」靠同一个版本核对兜住。没有单独写测试，因为取消只会让 `await` 拿不到结果，线程池里那一步照样会核对版本。
- **R2 旧代数等完截图仍会交去分析**：
  - 新增 `patrol_active(gen)`，判断条件是「开着，并且代数等于当前代数」。用到它的地方：循环头、两处采集的 `should_continue`、本地分支等手机截图以后的 break，以及采集结束、提交分析之前再核对一次（不算数就 break）。
  - `_analyze_and_log(..., patrol_gen=gen)`：一开头核对；调用哨兵模型之前再核对；分析完、写日志和叫人之前又核对一次，不算数时把 `call_core` 置为 False，这次不叫人。
  - `patrol_gen=None` 的老调用方式行为不变。
  - 回归测试：
    - 用真实线程跑真实的 `_monitor_loop`。旧一代卡在假的手机取图里，这时先停再开；放行以后，旧线程退出，`_analyze_and_log` 没有被提交（记录 `run_coroutine_threadsafe` 的调用）。
    - 过期代数的分析直接返回；源码检查确认在调用哨兵之前核对了代数。
  - 变异检查：去掉 `resume_if_due` 里的核对，或者去掉提交前的核对，对应的测试都会失败。
- **R3 座位只回巡逻指令时记成空回复失败**：
  - `_process_seat_commands` 有巡逻提示（成功或失败的回执都算）时设置 `triggered["patrol"] = True`，于是走已有的 `seat_done(message=None)` 分支，不再记 `empty_response`。
  - `_process_chatroom_commands` 同样设置这个标记，三处「只有指令、没有文字」的判断（Lucien、群里小克、Lucien 群聊）都改为 `memory_search or patrol`，结束时不存空消息。
- **R4 小克私聊翻记忆提前返回，跳过了巡逻**：三个入口都把巡逻处理挪到 `_start_private_memory_search_if_requested` 前面，原来放在小组件指令前的位置删掉，保证每条输出只处理一次。源码测试检查三处顺序都是「巡逻 → 翻记忆」，而且两者紧挨着。
- **L1**：没有有效指令时也返回去掉巡逻写法的文字。
- 测试：`test_patrol_control.py` 共 17 项，加上 `test_phone_camera_trigger_wiring.py` 和 `test_phone_usability.py`，合计 28 项通过。
- 删除确认窗的真机观察：小猫的手机部署以后会实际点一次「取消」和「确定」，到时补记在这里。
- 整套测试和 acb919c 基线对照：
  - 新出现的只有一条真问题：`test_camera_phone_screen_freshness` 按字符串 `def _monitor_loop(self):` 找函数，4a0915b 改了函数签名以后就找不到了。上一轮漏跑了它，这次把定位串改成 `def _monitor_loop(self`，已通过。
  - 还有几条只在整套一起跑时偶尔失败，单独跑能过（memory_hub_bridge 两条）。
  - antigravity 那一条在 acb919c 上单独跑也失败，和本轮无关。
  - english_corner 两条是已知的老失败。

## 90. 5a7f850 复审余项：换画面源也走版本协议；写日志广播之后、叫人之前再核对

- **R1 余项 换源会覆盖较新的手动关闭**：
  - `switch_source` 一进来就在巡逻锁里做三件事：读 `was_monitoring`、版本加一并记为 `switch_version`、`monitoring=False`（旧一代收手；配置里的开关不动，因为换完源要恢复）。
  - `_stop_current_source` 和开新设备这类慢操作放在锁外。
  - 最后在锁里核对 `ok and was_monitoring and 版本 == switch_version`，满足才 `start_monitoring()`。所以换源期间只要有人手动关、AI 停、AI 延长暂停、或者手动开了又关，版本都会变，这次换源就不再重开，也不改 `patrol_changed_by` 和 `resume_at`。
  - 末尾的 `start_monitoring` 只有在版本仍等于自己认领的那个时才会执行，过期的换源拿不到新版本。
  - **其他直接改状态的地方（已审计）**：`close_esp32` 改为在锁里执行，并且只在原来确实开着时才把版本加一、置为 False；这样换源流程里自己调用它时不会让自己的认领失效。`self.monitoring` 剩下的直接赋值只有 `__init__`，以及 `start_monitoring` / `stop_monitoring` 内部（都在锁里）。
  - 回归测试：
    - 换源卡在关旧设备时分别做三种操作：真实的 `cam_monitor_stop`；AI `[巡逻:停|3小时]`（暂停和恢复时间都保留）；手动开了又关。三种情况最后都保持关闭。
    - 换源期间没人打扰：照常恢复巡逻。
    - `close_esp32` 计为一次开关。
  - 变异检查：去掉末尾的版本核对，三条竞态测试都失败。
- **R2 余项 写日志广播 await 之后还会叫人**：
  - `_analyze_and_log` 在 `await manager.broadcast(monitor_log)` 之后、`_wake_core_targets` 之前再核对一次代数，不算数就直接返回。
  - `_wake_core_targets(..., proceed=…)`：巡逻叫人时传入 `lambda: self.patrol_active(gen)`，`wake_one` 每叫一个人之前都问一次。同一个群里按顺序叫的人，排在后面的不会再被叫。已经开始回复的那一位会把这一轮说完，和已经开始的哨兵调用一样处理。定位等其他入口不传 `proceed`，行为不变。
  - 回归测试：
    - 真跑 `_analyze_and_log`：用假哨兵返回 `call_core=True`，写日志广播被卡住时停掉再开，进入新一代；放行后旧一代没有叫人。测试里断言了确实走到了 `call_core=True` 的广播（第一版测试因为哨兵配置没就绪，在更早的地方就返回了，是空跑；变异检查发现后补上了「哨兵就绪」和这条断言）。
    - 群里依次叫三个人，第一位开口时巡逻被停，后两位不叫；不传 `proceed` 时照旧叫。
  - 变异检查：去掉广播后的核对，这条测试失败。
- 测试：`test_patrol_control.py` 共 24 项，加上相关文件合计 38 项通过。所有引用 camera / 换源 / 叫人的 16 个测试文件共 197 项通过，唯一失败的 friend_visit 在 acb919c 基线上就失败。

## 91. e2954bb 复审 N1：重叠换源丢失「巡逻原本开着」

- 修法（采用你的第一种建议）：新增 `_switch_lock`（普通 Lock），`switch_source` 整次在这把锁里执行，实际逻辑移到 `_switch_source_locked`。
  - 慢的设备操作会持有换源锁，但不持有 `_patrol_lock`，所以换源慢的时候，手动停和 AI 停照样能马上生效。
  - 后来的换源要等前一个换完才进来，看到的是真实状态，不会把前一个的「临时停」当成「本来就关着」。
  - 末尾的版本核对保留不变。
- **顺带：换源失败也会丢失意图**（改动前就有的老问题，同属「配置和实际状态不一致」）：
  - 原来巡逻开着时换源失败（比如设备打不开），会出现 `monitoring=False` 但 `monitor_enabled=True`，下一次换源成功也不会恢复。
  - 现在换源失败、并且版本没被别人动过时，记下 `_resume_after_switch=True`；下一次换源把它当作「原本开着」，换成功后恢复。
  - 任何真正的开或关（`start_monitoring` / `stop_monitoring`，包括小猫手动、AI 指令、到点恢复）都会清掉这个标记。
- 回归测试：
  - 两个、三个重叠换源，包括同源重复、不同源、esp32 加 phone：巡逻继续开着，`monitor_enabled` 也是 True。
  - 三个重叠换源期间，有人手动关，或 AI 停 3 小时：最后保持关闭，配置也是关，AI 的暂停记录和恢复时间都保留。
  - 换源连续失败两次后，第三次成功：巡逻恢复。
  - 换源失败后小猫关掉巡逻，下一次换源成功：保持关闭。
- 变异检查：去掉换源锁，3 个重叠测试失败；去掉「失败后记住」，失败后恢复的那条测试失败。
- 测试：`test_patrol_control.py` 共 31 项。所有引用 camera 的测试文件加上 `test_phone_usability.py`，共 208 项通过；唯一失败的 friend_visit 是基线就有的。

## 92. 36b44cf 之后：巡逻状态看得见、狗蛋和 Lucien 的 💭 思考

小猫 10-29 真用反馈：小克停了巡逻，但看不出停多久；Lucien 的 💭 很短或者没有，狗蛋的一直是空的。

- **监控页显示巡逻状态**：
  - `patrol_control.page_note()` 生成一句话，`/api/cam/status` 用 `patrol_note` 字段返回。可能是「巡逻开着」「巡逻停着（小克停的，15:30 自动恢复）」「巡逻停着（小克停的，没定时间，等有人再打开）」，小猫手动关时只显示「巡逻停着」。
  - camera.html 在哨兵巡逻标题下显示这一行，每秒刷新状态时一起刷新；开关也跟着服务器走（AI 开或停之后不再显示旧状态）。正在点的那一下不会被覆盖（`document.activeElement` 判断）。
- **狗蛋（Gemini 走 OpenAI 兼容中转站）的思考**：
  - 服务器上对 4Router 做了实测，只发极短的算术题，不进聊天：
    - 不带开关或只带 `reasoning_effort` 时，不返回思考，但用量里有 `reasoning_tokens`。
    - 带 `extra_body.google.thinking_config.include_thoughts=true`、一次性返回时，有 `reasoning_content`。
    - 同样带开关、边写边传时，思考被丢掉了，增量里只有 content 和 role。
  - `call_custom_openai`：模型名里有 gemini 才加这个开关，其他模型（DeepSeek、Kimi、Grok、Claude 中转）请求体不变。
    - 中转站对开关报 400 或 422 时，记住这个地址（`_NO_GEMINI_THOUGHTS`），去掉开关重发。重试上限从 4 次改为 5 次。
    - 流式结束时，如果有正文、没有思考、但用量里 `reasoning_tokens>0`，就记住这个（地址, 模型）组合（`_STREAM_DROPS_THOUGHTS`）。之后对这个组合改用一次性返回 `_custom_openai_whole`：先发「正在思考...」状态，再一次性给出正文，把 `reasoning_content` 和用量写进 meta。出错时和流式一样报 `provider_error`。
    - 两个记忆都只存在进程里，重启后会再丢一条思考，然后重新记住。
  - `[DONE]` 由 return 改为 break，这样才能走到结束时的检测。行为不变。
- **Lucien 的思考摘要**：Codex 的 `reasoning_summary` 默认值从 auto 改为 detailed（auto 常常只给 30 来字的标题，甚至不给）。哨兵那条调用仍然是 none。设置里显式写了的值照旧生效。
- 空座位 ai4 到 ai6 和小克、Lucien 走同一套 `usage_meta["reasoning_content"]` 保存和 💭 显示。以后接 DeepSeek、Kimi、Grok，只要中转站返回 `reasoning_content`、`reasoning` 或 `<think>`，就能看到思考，不需要另外改。
- 测试：
  - 新增 `test_gemini_thoughts.py` 6 项：
    - 带开关，流式思考能存下来
    - 其他模型不带开关
    - 流式丢思考后改为一次性返回
    - `reasoning_tokens=0` 时不误判
    - 中转站拒绝开关后去掉
    - 一次性返回出错能报出来
  - `test_patrol_control.py` 新增页面状态 1 项，共 32 项。
  - 变异检查：去掉 gemini 开关时 3 项失败；去掉流式丢思考的记忆时 1 项失败；去掉拒绝记忆时 1 项失败。

## 93. 归巢让每位 AI 都能进（227238f 服务器、a19fe24 手机）

小猫 10-29：归巢想让每位 AI 都能进，包括以后在座位上接的 DeepSeek、Grok、Kimi。施工方案在 `docs/施工方案-归巢全员.md`。

- **约定**：保留原来的 main 和 second，新增座位 owner `ai3` 到 `ai6`，私聊时间线叫 `seat_private:<座位>`。服务器 `homecoming/contracts.py` 和手机 `HomecomingOwners.java` 各有一份，规则相同。快照的 schema 不变（仍然是 2），只新增字段：旧手机不认识就忽略，新手机拿到旧快照时也只显示原来两位。
- **服务器**
  - `snapshot.build_snapshot_sections` 新增 `seats` 和 `group_seats` 参数，默认用 `_load_seats()`，判断标准和聊天室一样用 `seat_chat.check_ready`。
    - 只有准备好的座位才会带上，内容包括名字、人设（`_compile_ai_persona_sections`）和平时用的模型 key。
    - `identity.companions` 加上座位，另外新增 `identity.roster`（每位的 owner、时间线、名字）和 `identity.group_members`。
    - 每个座位一条时间线 `seat_private:aiN`，包含这位座位所有 seat_1v1 房间的消息（90 天、500 条），containers 是这些房间。
    - 很旧的库里 `chatroom_rooms` 没有 actor 列时，不带座位。
  - `routes/homecoming`
    - 打包前用 `_ensure_seat_rooms()` 给准备好的座位建好私聊房间（复用 `_get_or_create_seat_room`），这样第一次在归巢里跟 TA 说话，回来也有地方合并。失败只打日志，不影响快照。
    - `_timeline_mapping` 按 `is_known_timeline` 收录座位时间线。
  - 回传
    - `return_planner._timeline_mapping` 只认已知时间线；`seat_private:ai9` 这类伪造的时间线会被拒绝，消息进隔离。
    - 座位私聊里的消息，发送者只能是 user 或这位座位本人，否则隔离（`seat_sender_mismatch`）。
    - importer 原样写入 `sender=ai3`，这部分不用改。
    - 座位的记忆和日程走原来的规则：`_memory_target` 和 `_schedule_plan` 只认 main 和 second，座位的会被跳过或隔离，第一版不合并。
- **手机**（CI 跑单测加打包，run 40829002187 通过）
  - `HomecomingOwners`：统一判断 owner 和时间线。`ChatRepository` 和 `Runtime.validateTimeline` 都改用它。
  - `IdentityRepository`
    - `identityForTimeline` 认座位时间线，群聊回复者也可以是座位。
    - `roster()` 返回原来两位，加上名单里带人设、名字不为空的座位。
    - `groupReplyOrder` 按 reply_order 排好原来两位，座位排在后面，顺序按 `group_members`。
  - `GroupTurnCoordinator`：人数从「正好 2 位」放宽为「至少 1 位」。`Runtime` 取消群聊时，按本轮名单逐个停止。
  - `Runtime`
    - `send` 私聊时，回复者固定为时间线的主人，不用页面传来的 owner。
    - `sendGroup` 和 `setRoutePreference` 只接受已知 owner。
    - bootstrap 返回 roster，以及每位的线路偏好。
    - 座位不朗读，避免借用小克的声音。
  - `ContextBuilder`：座位不查本地记忆（手机上只有两位有本地记忆）。
  - 界面
    - 会话列表按 roster 生成，最后是「群聊」；点击用事件委托，所以动态生成的按钮也能点。
    - 切到某位的私聊时，设置里自动换成这位的线路和模型。没选过时，优先选包含 TA 平时模型的线路。
    - 记忆和日程的下拉框仍然只有两位。
- **测试**
  - `test_homecoming_seats.py` 8 项。变异检查：去掉发送者核对 1 项失败；planner 不过滤时间线 1 项失败。归巢全套 61 项通过。
  - 安卓 `HomecomingSeatOwnersTest` 8 项，原有测试全部通过。
  - 本机用假的手机桥打开归巢页：会话列表是「小克 / Lucien / 狗蛋 / 群聊」；切到狗蛋时线路自动变成 4RouterGemini + gemini-3.8-flash，发送的 owner 是 ai3；切回小克，线路变回空悲切。
- **请重点看**
  1. 快照里多了座位人设，加密前的 sections 和以前一样是明文 gzip，靠 Cloudflare Access 加设备注册保护，和小克、Lucien 的人设是同一个级别。
  2. `_ensure_seat_rooms` 在快照接口里写库，可能和聊天室同时建房间。
  3. 群聊里座位的回复传回服务器时，sender 是 `ai3` 这样的座位 id，main 和 second 仍然映射为 aion 和 connor。

## 94. 1acbac4 复审：M1、M2、L1、L2

- **M1 一次性返回绕过「不认思考开关」的回退**
  - 一次性请求挪进 `call_custom_openai` 原来的重试循环（总次数仍是 5 次），不再单独走一个函数。
  - 收到 400 或 422，并且明确提到 extra_body、thinking_config 或 include_thoughts 时，记进 `_NO_GEMINI_THOUGHTS`，`continue`。下一轮不再带开关，于是回到流式，照常聊天。
  - 其他错误和成功的响应都不重发。「正在思考…」状态在一轮请求里只发一次。
  - 测试 `test_whole_reply_that_rejects_the_switch_falls_back_like_streaming`：先发现流式丢思考，接着一次性请求被拒，再去掉开关走流式，依次是 `[(stream, 带开关), (一次性, 带开关), (stream, 不带)]`。再聊一次只发 `(stream, 不带)`。变异检查：去掉这一分支，测试失败。
- **L2 一次性返回的解析和结构错误**：`_custom_openai_whole_reply` 先校验顶层是对象、`choices[0].message` 是对象、usage 是对象、思考和正文是字符串。200 但不是 JSON，或者结构不对，都会记 `provider_error` 并结束，不重发。取消请求的异常照旧往外传，因为这里不捕获它。参数化测试覆盖了 4 种坏响应：HTML、顶层是数组、choices 是字符串、message 是字符串，每种都只发 1 次请求。
- **M2 座位第一个私聊窗口可能被重复创建**
  - `create_room` 在 `actor` 存在且 `new_window=false` 的分支里，先 `BEGIN IMMEDIATE`，再查询；已经有窗口就 rollback 并返回那个，没有就在同一个事务里 INSERT。这和 `ensure_group` 的写法一样。
  - `new_window=true` 时照旧可以开好几个窗口，没有加唯一索引。`_get_or_create_seat_room` 外面那次查询只是快速路径，最终以事务里的结果为准。
  - 测试：真实临时 SQLite，每轮 20 个 `_get_or_create_seat_room` 加 4 个 `_ensure_seat_rooms` 同时进来，连跑 5 轮，每轮都只有 1 个窗口，并且大家拿到的是同一个 id。已经有窗口时返回最近那个；两个 `new_window` 同时来，会建出 2 个新的。变异检查：去掉 `BEGIN IMMEDIATE`，连跑 3 次都失败。
- **L1 用焦点判断导致开关长期不同步**
  - 删掉 `activeElement` 判断，改成 `_monitorTogglePending`（请求进行中）加 `_monitorToggleGen`（轮询出发时的代数）。开或关的请求在路上时不覆盖；请求发出前就已经出发的旧轮询，回来后作废。
  - 请求结束时在 finally 里释放，并重新拉一次状态（成功失败都拉）。`syncCamModalState` 和每秒轮询都改走 `syncMonitorToggle`。
  - 测试用 Node 真跑页面里这几个函数：一开始开关是 true、服务器说停着，开关变成 false；旧轮询在点击之后才回来，不覆盖；请求进行中的轮询也不覆盖；请求结束后按真实状态；之后 AI 停了，开关跟着变。变异检查：去掉这个判断，测试失败。
- 相关测试 189 项通过，1 项跳过（Gemini、巡逻、多窗口、归巢全套、Codex、中转站、手机可用性）。

## 95. 338e314 复审：N1、L2 余项

- **N1 状态变量晚于 init 声明，监控页首次打开就失败**
  - `_monitorTogglePending` 和 `_monitorToggleGen` 挪到页面顶部其他状态变量旁边，放在 init IIFE 之前，并加注释说明为什么必须在前面。计数和代数的保护逻辑不变。
  - 新测试 `test_camera_page_first_load_reads_status_in_real_order`：截取页面整段内联脚本原样执行，init 立刻运行；common.js 的全局函数和 DOM 用万能替身，没定义的全局函数自动补替身；api 是内存假接口。
    - 断言：首次打开发出了 `GET /api/cam/status`，状态文字是「✅ 监控中…」，开关是 true。
    - 用 338e314 的 camera.html 跑，测试失败：只请求了 crop，状态文字是「获取状态失败」。
  - 上一条开关测试改为只截两行声明，不再依赖声明在页面里的位置。
  - 本机真浏览器打开 /camera：状态文字「未连接」（本机没接摄像头），巡逻提示「巡逻停着」，间隔已经填成 10，控制台没有报错。说明 init 正常走完了。
- **L2 余项 字段存在但类型不对**
  - `usage` 必须是对象，`message.content` 和 `message.reasoning_content`（或 `reasoning`）必须是字符串。缺字段或者是 null 都算正常；字段存在但类型不对，就记 `provider_error`，返回「返回格式不对」，不重发。
  - 坏响应的参数化用例从 4 种加到 7 种，补上你给的 3 个反例；另加「可选字段是 null 也正常」1 项。把 ai_providers.py 换回 338e314 的版本，这 3 个新用例都失败。
- 测试：`test_gemini_thoughts.py` 15 项；`test_patrol_control.py` 和摄像头新鲜度合计 37 项；相关文件合计约 200 项通过。

## 96. 10-30 真用反馈：归巢拿不到快照、通知名字、记忆详情透字（3603af3）

- **归巢一直拿不到快照**：手机归巢页的数字全是 0，提示「尚无完整快照或可用云端线路」。
  - 服务器日志显示，最后一次 `/api/homecoming/v1/snapshot/build` 是 10-22，走的是 Tailscale 地址。10-24 App 换到 Cloudflare Access（cafdd44）之后，再没有一次归巢请求进到服务器。
  - 原因：后台推送、无障碍、悬浮聊天都用 `WebViewCookieJar` 带着网页的 Access 登录状态，而 `HomecomingBackupClient` 和 `HomecomingReturnClient` 的 OkHttp 没带，会被 Access 拦到登录页。
  - 修法：这两个连自家服务器的 client 加上 `.cookieJar(new com.aion.chat.WebViewCookieJar())`。`HomecomingModelGateway`（直连模型商）和 `HomecomingTtsNetworkSynthesizer`（直连语音）不加，避免把 Access 凭证发给第三方。
  - `HomecomingAccessCookieTest` 用源码契约锁住：这两个带，那两个不带。
  - 顺带量了线上真实快照大小（只读）：gzip 后 52KB，离手机 2MB 的上限很远，不是大小的问题。
- **通知标题是 Aion / Connor / Ai3**
  - `AionPushService` 原来把 sender 代号首字母大写当标题。新增 `ActorNames`：WebSocket 连上时、名单超过 10 分钟时，在 `deviceContextExecutor` 后台请求 `/api/actors`（同一个带 Access cookie 的 client），拿 id→名字。
  - 拿不到或解析失败时，退回原来的写法；坏的响应（比如登录页 HTML）不会覆盖已有的好名单。
  - 主聊天的 `msg_created` 没有 sender 时，当作 aion。
  - `ActorNamesTest` 3 项。
- **记忆详情透出底下的列表**：IB 皮肤下 `--bg` 只有 0.35 透明度。手机上记忆库的 `.detail` 底部浮层用 `var(--bg)`，又没有毛玻璃，底下列表的字会透上来叠在一起（小猫截图）。
  - 修法：手机 `.detail` 改用 `var(--surface)` 加 `backdrop-filter: blur(26px)`；记忆库的 `dialog` 也加同样的模糊。
  - 本机 IB 皮肤、375 宽实测：修之前透字，修之后底下被糊住、字清楚。`test_phone_usability.py` 加 1 项。
- 安卓 CI：run 40995083746，单测加打包通过。

## 97. 6fa588d 复审：M1、L1，以及 App 改名（86c2ac7）

- **M1 名单刷新占住共享线程**
  - 新增独立的单线程 `actorNamesExecutor`，名单请求不再放进 `deviceContextExecutor`。
  - 从 WebSocket client 用 `newBuilder()` 派生一个 HTTP client：`readTimeout` 10 秒、`callTimeout` 15 秒（覆盖读响应体）。WebSocket 原来的无限读不变。
  - 并发和冷却：
    - 入队前用 `ActorNames.claimRefresh` 原子认领，保证同时只跑一个刷新；名单还新鲜或者在失败冷却里，就不认领。
    - 任务执行前用 `stillNeeded` 再判断一次，不需要了就直接释放，不算失败。
    - finally 里 `finishRefresh` 释放认领。失败或坏名单都冷却 60 秒。线程池拒绝任务时（服务正在关）也会释放。
  - `onDestroy` 会取消正在进行的 Call，并 `shutdownNow` 这条线程。
- **L1 坏名单覆盖好名字**：`parse` 整份校验，任何一条出问题都整份不收，旧名单和 loadedAt 保留。
  - 要求：actors 是数组，每条是对象；id 是字符串、在 aion/connor/ai3–ai6 里、不重复；name 是字符串或 null。
  - name 是空字符串的空座位跳过，不算坏名单。
  - 测试覆盖：数字或对象做名字、数字 id、不认识的 id、混入非对象、好坏条目混在一起、重复 id、空数组、actors 是对象、HTML。
- **改名**：小猫要求，`app_name` 从「Aion Oloth」改为「Animals Home」，桌面图标名和常驻通知标题都会变。
- `ActorNamesTest` 6 项（含单飞、冷却，以及源码契约：不用 deviceContextExecutor、有 callTimeout、关服务时 shutdownNow）。安卓 CI run 40997711033 通过。

## §98 Hub 记忆页：同一件事叠在一起（2026-10-30）

来源：Memory Hub 窗口的《给AionsHome的接口说明》优先级 1-2。Hub 会把「同一件事的补充说法」挂在原来那条名下（补充那条的 `fact_ref` = 原来那条的 id，链最多 4 层）。

- `routes/hub_memory.py`：`_FIELDS` 加上 `fact_ref`，其他字段仍然只挑白名单。
- `static/hub-memory.js` 新增 `stacked(items)`：
  - 顺着 `fact_ref` 往上找，最多 4 步，只在本页里找；找到的最上面那条做组头，补充收在它下面，显示「同一件事还记着：…」。
  - 指向的那条不在本页或已归档：单独显示，前面加「补充」小标签。
  - 互相指（环）：各自单独显示，不会丢条目也不会死循环。
  - 收在下面的每条仍然有自己的「改 / 归档」（已归档页是「找回 / 彻底删除」），按单条操作。
- `hub-memory.css`：收在下面的条目不画成独立卡片，左边一条竖线。
- 测试：`test_hub_memory.py` 加 2 项，共 29 项通过。其中 Node 跑真实的 `stacked`，覆盖两层链、指向页外、环、普通条目。
- 本机浏览器：用假数据打开 /hub-memory?ai=ai3 看过，结构是 3 个组头、2 条收在下面、1 个「补充」标签，截图正常。

## §99 归巢快照被 Cloudflare 半路解压，手机一直装不上（2026-10-30）

**现象**：小猫用新 App（已带登录状态），归巢页一直写「尚无可用灾备快照」。服务器日志里 `devices/register` 和 `snapshot/build` 都是 200，而且连着两次都是 200。如果第一次装上了，第二次应该带着旧 id 拿到 304，所以说明手机把第一份扔了。

**根因（推断，线上没法从手机取日志）**：
- 服务器回快照时，正文是 gzip，同时标了 `Content-Encoding: gzip`。
- 手机请求时写的是 `Accept-Encoding: identity`，要的是原样字节，再自己解压、按原始字节核对各段哈希。
- 10-24 起服务器在 Cloudflare 后面。Cloudflare 看到「客户端只要 identity」而响应标着 gzip，就会半路解压，手机收到的是 JSON 正文。
- 手机的 `GZIPInputStream` 解压失败，抛 IOException（DOWNLOAD_INTERRUPTED / parse）。
- 10-24 前走 Tailscale 直连，没有中间这一层，所以不出事。

**服务器（`routes/homecoming.py`）**：
- 快照当成一个 gzip 文件发：`media_type="application/gzip"`，不再标 `Content-Encoding`。
- `Cache-Control` 加 `no-transform`。
- 手机本来就只读原始字节、不看类型，所以**现有 App 不用重装**就能好。
- `test_homecoming_routes.py` 改为断言：没有 Content-Encoding、类型是 application/gzip、开头是 1f8b、no-transform、解压后没有明文密钥。归巢 61 项通过。

**手机（兜底）**：
- `HomecomingBackupClient.asGzip`：收到的已经是 JSON 正文（以 `{` 开头）时，在本地重新压成 gzip 再走原流程。各段哈希按原始字节算，解压不改字节，所以照样对得上。既不是 gzip 也不是 JSON 时拒收。
- 读取上限改为解压后的 8MB；压回 gzip 后仍按 2MB 检查。

**手机（看得见原因）**：
- 归巢页原来只写「尚无可用灾备快照」。失败代码和原因其实存在 `last_error_code` / `last_error_message` 里，但从没显示过。
- 现在由 `HomecomingActivity.notReadyReason` 显示「上次准备失败：代码（原因）」。快照在、但没有线路时，写「快照里没有能用的云端线路」。
- 定时任务那条失败路径以前只存代码，现在也存原因。

**版本号**：versionCode 23→24、versionName 1.22→1.23。以前每次打包都没涨，手机上分不出新旧。

**测试**：`HomecomingBackupImportTest` 加 2 项：正文快照照样装上并激活；HTML 登录页被拒收，旧快照保留。

## §100 狗蛋在记录里被写成「ai3」（2026-10-30）

**现象**：小猫说很多记录里狗蛋还是 AI3。

**根因**：服务器有不少地方把聊天记录拼成文字（多数是给模型看的），名字表只有 `{"user", "aion", "connor"}`，最后用 `.get(sender, sender)` 兜底。座位发的话就原样写成 `ai3`，AI 读到后又照抄进日记和记忆。库里没有存成大写「AI3」的文字（case-sensitive 扫过全部表），所以不需要回填。Hub 里有没有，交给 Hub 窗口查。

**改法**：
- `actors.seat_names()`：返回座位 3～6 的名字（不含前两位），缓存 5 秒，因为拼记录时常在循环里逐条调用。出错时返回 `{}`，不抛异常。
- 补到以下名字表里：
  - `chatroom.py`：`display_name_for_sender`、证据块 `name_map`、记忆整理、愿望上下文，共 3 处拼记录。
  - `memory.py`：召回证据的 `chat_name_map`，以及 3 处拼记录。
  - `routes/memories.py`：`_chatroom_sender_name`。
  - `routes/seeky.py`：拼记录。
  - `routes/search.py`：`name_map` 和返回给页面的 `names`。
  - `activity.py`：`_user_dynamic_names`（活动日志）。
  - `context_builder.py`：旅行卡片的名字。
- 已经认座位的地方没动：`active_memory_search`、`autonomy_state`、`routes/moments`、`routes/chatroom` 的 `_name_for_identity` / `_seat_name_map`。
- 不涉及座位的没动：斗地主、拍一拍只有两位；`wechat_bridge`。
- 各文件顶部 `from actors import seat_names`。actors 顶层只依赖 config 和 request_limits，没有循环导入，`import main` 正常。

**测试**：新增 `test_seat_names_everywhere.py`，共 12 项：
- `seat_names` 跳过前两位、有缓存、出错不抛；
- `display_name_for_sender`、记忆页、活动日志都写「狗蛋」；
- 源码扫描：上述 7 个文件里不再有只认 connor 的名字表。

全量测试：除 friend_visit 外 2157 通过、40 个失败。这 40 个失败在不带本次改动时同样失败，是原有失败，没有新增。


## 101. 群聊里的「人设」页在手机上透字（10-31 小猫截图）

- 原因：`chatroom.css` 末尾的手机规则（`max-width:760px` + `pointer:coarse`）为了打字不卡，去掉了人设页的毛玻璃，底色用 `var(--bg)`；IB 皮肤下 `--bg` 只有约 35% 不透明，底下群聊的字透上来。
- 改法：只在这条手机规则里把底色换成 `var(--ib-base, var(--bg))`：IB 皮肤下是实色 `#dfe9f6`，不开皮肤时退回原来的 `--bg`（本来就是实色）。毛玻璃仍然关着，不影响打字性能。
- 世界书里的人设页（小克 / Lucien / Jasper / 空位）是独立整页，底下没有内容，不受影响。
- 本机实测：375 宽、模拟触屏、IB 皮肤，计算出的底色是 rgb(223,233,246)，截图看不到底下的字。`test_phone_usability.py` 加 1 项，6 项都通过。


## 102. IB 皮肤下弹窗/抽屉改实底色 + 先隐藏密语时刻入口（10-31 小猫）

- **透字**：私聊侧栏「系统日志」弹窗在手机上透出底下的聊天。IB 皮肤给 `.modal/.sidebar/...` 的底色是 `rgba(255,255,255,var(--ib-card-a))`（约 0.5），全靠毛玻璃糊住；手机开「减少动效」时 `data-ib-reduce` 会把所有 backdrop-filter 关掉，WebView 上毛玻璃也不一定生效。
  - 改法：`ib/ib-skin.css` 在原玻璃规则后面加一条，只针对盖在聊天上面的层（`.modal .sidebar .settings-panel .memory-panel .plus-menu .config-popup .wallet-panel .persona-history-dialog .toy-edit-sheet`）设实底色 `color-mix(in srgb, var(--ib-base) 55%, #fff)`，前面先写一个 `#f0f5fc` 兜底。卡片类（列表项、日记卡等）不变，仍是玻璃感。
  - 本机实测：375 宽、模拟触屏、IB 皮肤、打开「减少动效」，系统日志弹窗是实底，看不到底下的字；侧栏、加号菜单、聊天设置浮层、钱包的计算底色都是同一个实色。
- **密语时刻入口**：小猫暂时用不上。私聊侧栏、群聊侧栏、群聊加号菜单里的入口删掉；功能代码、/toys 页面不动，以后恢复入口就能用。`test_toy_navigation.js` 改成直接检查原来的打开方式（不再从侧栏 HTML 里抠按钮）。
- 顺带：`test_chatroom_persona_mobile_performance.py` 里写死了 §101 改之前的底色 `var(--bg)`，跟着改成 `var(--ib-base, var(--bg))`。这个文件里剩下 1 项（缓存版本号字符串）是以前就失败的。
- 测试：`test_phone_usability.py` 8 项通过（新加 2 项），`test_toy_navigation.js` 通过。改到的 HTML/CSS 相关测试文件里其他失败，在不带本次改动时也一样失败。


## 103. 归巢带上 Hub 记忆（Hub #123 export-lite）+ 归巢数据不进云备份（10-31）

小猫 10-31 同意：共享记忆 + 每位 AI 自己的私人记忆都放进手机（Hub 方案约定私人记忆上手机前要问她）。

- **服务器拉包**（`homecoming/hub_pack.py`）：`POST /homecoming/snapshot/build` 在**开数据库之前**并发拉每位 AI 的 `GET /api/memory/export-lite?ai_id=…&max_bytes=60000&max_items=400&recent_days=30&include_dreams=false`。
  - 用**共用钥匙**（`memory_hub_bridge._headers(cfg)`），不用主人钥匙。
  - Hub 没接、某一位返回非 200、连不上：这一位不带 Hub 记忆，归巢照常打包，只打一行日志（不含内容）。
  - 对应关系：main→aion→claude、second→connor→lucien、ai3–ai6→各自座位的 `memory_hub`。快照里只给已准备好的座位放。
- **快照格式**（`snapshot.py`）：`memories.main` / `memories.second` 是「本地记忆 + Hub 条目」，座位是 `memories.<ai3…>`（旧手机本来就按 key 逐个导入）。
  - Hub 条目 id 是 `hub:<owner>:<hub id>`。手机库 `memory_snapshot.id` 是全局主键，同一条共享记忆进两位 AI 的包，不能撞。
  - `tags` 拼成 `keywords`（手机按关键词挑记忆），另带 `fact_ref`（同样加前缀）、`source:"hub"`、`read_only:true`。
- **只读**：
  - 服务器：`return_planner._manual_memory_plan` 遇到 `hub:` 开头的 entity_id 直接 `skip / hub_memory_is_read_only`，不写本地表，也不回写 Hub。
  - 手机：`HomecomingMemoryRepository.update/delete` 对 `hub:` 开头的 id 抛 IllegalArgumentException；归巢页记忆列表对这类条目不显示编辑/删除，显示「Hub · 只读」。
- **不进云备份**：`AndroidManifest` 加 `fullBackupContent`（≤ Android 11）和 `dataExtractionRules`（≥ 12，云备份 + 换机传输都排除）。排除的是 `files/homecoming/`、`files/homecoming-returns/` 和 `homecoming.db`（含 -journal/-wal/-shm）。
- **APK**：1.24（versionCode 25）。
- **测试**：
  - 新增 `test_homecoming_hub_pack.py`：打包/前缀/关键词、共用钥匙不是主人钥匙、单个失败不拖垮、Hub 没接不发请求、快照里只给准备好的座位、回传 update/delete 都 skip。它继承了座位快照和回传规划两组旧测试，所以条数里带着旧测试。
  - `test_homecoming_*.py` 共 91 项全过；不含新文件时 61 项，和改动前一样。
  - Java：`HomecomingMemoryRepositoryTest` 加 1 项（能被想起、不能改不能删、不产生操作记录），由 CI 跑。
- **已知限制（下一步）**：手机挑记忆的规则较粗，主要按关键词/整句包含打分；打分为 0 时按时间新旧排。Hub「钉住」的记忆不一定每轮都带上。以后可在 APK 里按 Hub 给的顺序兜底。


## 104. 修 §103 审查的 M1–M4

- **M1 总时限**：`hub_pack._fetch_one` 用 `asyncio.wait_for(…, TOTAL_SECONDS=15)` 包住「发请求 + 流式读完正文 + 解析」。
  - 超时时任务被取消，该位返回空，快照继续；三位仍并发，所以最坏约 15 秒。
  - 另加 `MAX_RESPONSE_BYTES=1_000_000`：边读边计，超过就放弃该位。正常一份约 94KB，不拿请求参数 max_bytes 当回应大小。
  - httpx 的 `timeout=12` 保留，管连接和单块等待。
- **M2 结构错误**：`portable_items` 先校验顶层是对象、items 是列表，否则抛 ValueError；最多取 400 条。
  - 转换和校验都在每位自己的 `except Exception` 边界里。CancelledError 是 BaseException，不会被吞。
- **M3 只读前缀**：
  - `contracts.is_hub_memory_id` 先 strip 再 lower 再判断。
  - 回传：新增 `return_planner._reserved_memory_row`，`memory` 和 `memory_auto` 两条分支都先过它。entity_id 或 payload.id 任一带 hub: 前缀 → `skip / hub_memory_is_read_only`；两者不一致 → `quarantine / memory_id_mismatch`。
  - 落库出口 `return_importer._upsert_memory/_delete_memory` 再挡一次 hub: 前缀，直接返回，不写库、不发事件。
  - 手机：`HomecomingMemoryRepository.update/delete` 先由 `required` 去掉空白；检查挪到统一的 `save()` 出口（`requireWritable(memory.id)`），`isReadOnly` 也先 trim 再 lower。
- **M4 座位用不上**：把「能想起」和「能写」的 owner 分开。
  - `recall` 改用 `validateReadableOwner`（两位 + 座位）；create/update/delete 仍只认 main/second。
  - `ContextBuilder` 对所有合法 owner 都召回，每位只召回自己的 owner，不串包。`HomecomingOwners.hasLocalMemory` 只改了注释。
- **测试**：
  - `test_homecoming_hub_pack.py` 新加：慢速滴流触发总时限且只丢那一位；items=1 / 顶层是列表 / 回应过大只丢那一位；payload.id 偷带前缀、大小写加空白、id 不一致、memory_auto 带前缀；importer 出口拒绝。
  - 归巢 Python 全套 95 项通过。
  - Java：`HomecomingMemoryRepositoryTest` 加空白/制表符/大写前缀拒绝，以及座位能召回自己的包、不能写、空座位为空、未知 owner 报错；`HomecomingSeatOwnersTest` 改成座位只召回自己的包、不串到小克。Java 由 CI 跑。


## 105. 归巢页改版（照草图 ④–⑦）+ 头像带进手机（11-01 小猫：原来的太丑、不顺手）

草图：https://claude.ai/artifact/4RciFYF4MvMtqbrk88wNwD 下排四张，小猫看过说「好」。

- **样式**：`assets/homecoming/` 三个文件重写，改成和家里一样的 IB 蓝色毛玻璃；文案全换成大白话。
- **功能和桥接调用一个没少**：
  - 发消息、停止、图片、拍照、朗读、记忆的增删改和整理、提醒、监督状态、线路/模型偏好、进入、回家五个动作，用的都是原来的 `HomecomingNative.*`。
  - 资产合同测试里写死的旧文字和旧元素换成新的；安全检查一条没删：仍然没有 innerHTML / fetch / http(s)，全部用 textContent。
- **交互变化**：
  - 换人：不再藏在标题下拉里，顶上直接一排头像 + 群聊（按快照名单画，只在聊天页显示）。
  - 输入框：图片 / 拍照收进「＋」；「停下」只在等回复时出现在「正在想…」旁边；朗读关着时不显示「再读一遍」。
  - 记忆页：按人切换（座位也能看，但只有两位能新记），带搜索；分「这次在手机上记的」和「从家里带来的（只能看）」两块。
  - 提醒页：「在哪里提醒」原来有两个一样的「私聊里」，现在只剩一个，跟着「谁来提醒」走。
  - 回家：五步改四步（收拾好 → 送到家门口 → 核对 → 放进家里）；三个选择加底部红字「全部丢掉」，丢掉要再确认。
  - 顺手修了个老 bug：`classList.toggle("hidden", mode !== "active" || data.inFlight)` 在 inFlight 是 undefined 时会变成来回切换，「再待一会儿」会被误藏。
- **头像**：
  - 服务器：`snapshot.avatar_data_uri` 只认 `/skin-assets/<名>.<后缀>`（data/skin）和 `/public/<名>.<后缀>`（先找替换图再找原图）。拒绝 `..`、子目录和外部链接；只收 png / jpg / webp / gif；单张不超过 300KB。转成 data URI 放进 `identity.user.avatar` 和 `identity.companions.<owner>.avatar`，带不上就空串。
  - 线上真实头像大约 45KB / 张，你的头像（替换图）176KB。
  - 手机：`HomecomingIdentityRepository.safeAvatar` 只收 `data:image/(png|jpeg|webp|gif);base64,…`（整串正则），且不超过 60 万字符；外部链接、svg、夹带引号的都丢掉。roster 每位带 avatar，另有 `userAvatar()`。
  - `readinessJson` 增加 `members`（名字、头像、记得几件事），读不出来就给空数组，不影响能不能进。
  - JS 里又过一遍同样的正则，才拿去当 `background-image`。
- **验证**：
  - 本机用假桥在 375 宽下打开新页：进门 / 群聊 / 记忆 / 更多 / 回家都看过截图，并用脚本点过换人、换记忆主人、搜索、发送后「正在想」、提醒下拉、回家面板的「再待一会儿」。控制台无错误。
  - 归巢 Python 全套 102 项通过（新加：头像只认自己家的图、过大不带、快照里带上）。
  - Java 新加 `avatarsComeOnlyFromSnapshotImageData`，旧的资产合同测试已按新页面更新；由 CI 跑。
- **APK**：1.25（versionCode 26）。


## 106. 修 §105 审查的 M1、M2 + 符号链接加固

- **M1 搜索漏掉第 100 条之后**：`loadMemories` 把搜索框里的词传给 `getMemoriesJson(owner, query)`，交给手机去找。手机按匹配度排，包含这个词的排在前面。页面再按「包含」筛一遍，输入后停 250ms 才去查。
  - 本机假桥：160 条记忆、目标在第 121 条、手机一次只给 100 条，搜「桔梗」能找到。
- **M2 头像合起来撑爆快照**：
  - 缩图：用 Pillow 缩成最长边 192 像素的 webp（quality 82）。真实素材缩完是 5～15KB/张，原来最大 238KB。缩完单张超过 8 万字符就不带。原图读取上限是 5MB。
  - 总预算：所有头像加起来 40 万字符，按「小克 → Lucien → 座位 → 小猫自己」的顺序装，装不下的就不带。
  - 兜底：`fit_phone_budget` 在算 snapshot_id 之前，先把 sections 压一遍。超过 1,900,000 字节（手机上限 2MB，留出线路密文和外壳）就把所有头像拿掉，记忆和聊天照带，打一行日志（不含内容）。Pillow 线上已装（13.0.0）。
- **符号链接**：`_avatar_file` 先 resolve，解析后还得在原来那个根目录里（data/skin、public_overrides 或 public），链到外面的不认。
- **测试**：
  - 新加三项：总预算按顺序装，小猫自己的排最后；7 张随机大图时快照降到预算内，头像清空、记忆原样、原对象没被改；链到外面的被拒（用 mock 模拟 resolve，本机建不了链接）。
  - 旧测试改为期待缩完的 webp。Java 资产测试加一条：搜索词要传给 `getMemoriesJson`。
  - 归巢 Python 全套 105 项通过。


## 107. 修 §106 审查的 M3：缩图前先看像素

- `_shrink_avatar` 在 `Image.open` 之后、任何解码 / 缩图之前，先读 `image.size`。
  - 像素超过 8,000,000（约 2800×2800），或任一边超过 8000，直接不带这张头像。
  - 之后才 `draft`（JPEG 直接按小尺寸解码）再 `thumbnail`。
- **测试**：
  - 新加 `AvatarPixelLimitTests`：现场造一张 13000×13000、1 位灰度的 PNG（文件只有几十 KB，文件大小那一关拦不住），断言返回空、`thumbnail` 和 `load` 都没被调用。
  - 在不带本次修改的代码上，这一项会失败：旧代码真的去解码了，跑了 41 秒。
  - 正常 1200×1200 头像仍缩成 webp。
  - 归巢 Python 全套 107 项通过。
- 只改服务器，APK 不变（仍是 ad426f3 那一版的 1.25）。


## 108. 旅行：中文写的外国地名查不到时，换英文写法再查（11-01 狗蛋没去成威基基海滩）

- **现象**：线上 app.log 有一条 `[travel] 这趟没成：没找到「美国 檀香山 威基基海滩」这个地方`。服务器上实测：
  - OSM 和 Open-Meteo 对「美国 檀香山 威基基海滩」「美国檀香山威基基海滩」都返回空。
  - 单查「威基基海滩」，OSM 返回的是弗吉尼亚州的 Virginia Beach（错的）。
  - 英文写法 `Waikiki Beach, Honolulu, Hawaii, USA` 能准确命中（21.274），清水寺、黄山的英文写法也对。
- **改法**：`travel._lookup_place`。
  - 先按原样 `travel_places.lookup`。
  - 抛 ValueError（查不到）时，用这趟已经固定的那位的模型调一次 `_map_query`：只发一句「改写成 OSM 能查到的英文：地点, 城市, 州或省, 国家」，不带人设和聊天。
  - 拿英文写法再查一次；地图只给英文名时，名字用小猫写的中文最后一段。
  - 还查不到就报原来的中文名。
  - 只在查不到时多花一次模型调用。
- **测试**：`test_travel.py` 加 2 项：中文查不到 → 英文查到、名字回填中文；两次都查不到时报原名。旅行两组共 25 项通过。


## 109. 主页改版第 1 步：图标收进分组文件夹；点歌台每首歌上直接能删（11-01 小猫）

草图：https://claude.ai/artifact/4RciFYF4MvMtqbrk88wNwD ①（小猫说「还可以」，再按她的意见调过）。AI 动态卡片、开关总览在后面两步。

- **Dock（常用）**：聊天、群聊、**日记本**（新加，`/diary`，原来只能从私聊侧栏进）、记忆库。朋友圈移出 Dock。记忆库换成原版的「记忆库」图标（原来误用了日记本的图）。
- **文件夹**（`home.html` 的 `FOLDERS`）：
  - 一起玩：小剧场、点歌台、娱乐室、学习角、陪伴阅读、旅行
  - 家里的事：朋友圈、相册、爱的印记、许愿池、好友串门、逛淘宝、小红书、家庭动态
  - 我的生活：日程监督、健康、定位、奥罗斯财团、监管
  - 家里的开关：设置、世界书、工具与能力、监控摄像头、监控日志、活动日志、用量、MCP 工具、维修室
  - 先收起来：密语时刻、去约会、抱抱
  - 以后新加、没列进任何分组的应用，照旧单独放在桌面上。
- **桌面实现**：
  - 文件夹作为一个格子参加原来的长按拖动排位置。id 是 `folder-*`，服务器 `/api/home/layout` 本来就收任意字符串 id，不用改。
  - 格子里显示前 4 个小图标。点开是居中的毛玻璃弹层，里面是原来的应用按钮。点空白处或按 Esc 关掉；点了应用会先关弹层再打开。
- **旧排法**：本地和服务器上存的旧排法里没有任何 `folder-*`，一律不用，按新的默认排一次，并把新排法存回服务器，换手机也一样。
- **朋友圈红点**：挂在「家里的事」文件夹上；文件夹里那份不再用同一个 id。
- **IB 皮肤**：
  - 新画 `ib/icons/diary.svg`，同样是白线条加蓝影。
  - `ib-skin.css` 给每个已有的 IB 线条图标都补了一条 `.mini-icon[data-app-id=…] img`，让文件夹里的小图标也是线条图。
  - 文件夹底色和弹层用 IB 玻璃色。
- **点歌台**：
  - 不在「管理」状态时，每首歌右边有个「删除」，点了先确认再删。按钮阻止冒泡，点它不会顺带播放。
  - 删除和批量删除共用 `deleteTracks(ids)`；单删时不去关没打开的对话框。
  - 背景：小猫说网易云加的歌删不掉。线上 app.log 里一次 `DELETE /api/music-station/tracks` 都没有，说明是入口太隐蔽，不是接口坏了。
- **验证**：
  - 本机 375 宽、IB 皮肤看过主页、「家里的开关」文件夹和点歌台，并用脚本点过：开 / 关文件夹、点里面的应用（closeFolder 后调 openApp）、新排法存到服务器、每首歌的删除（确认后删掉、提示「歌曲已删除」）。控制台无错误。
  - 新测试 `test_home_folders_ui.js`：Dock 四个；每个应用正好进一个文件夹；桌面上只剩文件夹；旧排法不认、新排法认；IB 线条图标；点歌台删除。
  - `test_music_station_ui.js` 和 `test_home_lounge_friends_ui.js` 在本次改动前后都失败在同一处旧断言（分别是上传按钮的写法、好友串门那一行的写法），和本次无关。相关 Python 测试 35 项通过。


## 110. 修 §108 审查的 M1：地名回填不再按空格截

- **问题**：`place.name = place_name.split()[-1]` 会把多词地名截坏，比如「New York Central Park」变成「Park」，「日本 东京 浅草 寺」变成「寺」。
- **改法**：
  - 只有「地图给的是英文名」而且「小猫原来写的是中文」时，才换名字，换成原来那一整串（只去掉首尾空白）。
  - 原来就是英文写的，用地图查到的名字。
  - 不再按空格去猜哪一段是地点。
- **测试**：改了原来那一项，期望得到整串「美国 檀香山 威基基海滩」。新加一项，覆盖 Codex 的三个反例：英文输入保留地图名，中文和混合输入保留整串。旅行两组 26 项通过。


## 111. 主页改版第 2 步：「AI 动态」页 + 主页小卡片；时间线里狗蛋不再显示成 ai3

草图 ②。小猫说过活动日志、家庭动态、日记藏得深、难翻。

- **时间线接口**（`routes/activity.py` 的 `/api/timeline`）：
  - 每条多一个 `actor_id`（aion / connor / ai3… / user）。朋友圈、日记、礼物、自主活动取原表的作者字段；小猫自己的事件和位置变化标成 user。
  - `_timeline_names` 加 `**seat_names()`。原来名字表没有座位，狗蛋在家庭动态、活动日志里显示成「ai3」，§100 漏了这一处。本机实测现在显示座位名。
  - 其他字段不变，家庭动态、活动日志照旧能用。
- **新页面 `/ai-feed`**（`static/ai-feed.html`，`main.py` 加路由）：
  - 顶上一排人：全部 + 每位已启用的 AI（头像只认 /public、/skin-assets、/static、/uploads 开头的本站地址，否则显示名字首字）；没住进来的显示「空位 ×N」，点不了。
  - 「下次醒来」：读 `/api/idle-autonomy` 的 `remaining_minutes`；没开自己醒来的写「没开自己醒来」。
  - 时间线：`/api/timeline?hours=72&limit=500`，只放 actor_id 是 AI 的条目，按今天 / 昨天 / 日期分组；点开按类型跳到日记本、朋友圈、爱的印记、家庭动态。「再往前看」把时间范围放大，最多 30 天。
  - 全部用 textContent；附图只认 `/uploads/` 开头。
- **主页**：
  - 图标区上面加一张「AI 动态」小卡片，显示最近一条 AI 动态的时间和标题，点开进 `/ai-feed`；每 2 分钟刷新一次，用 textContent 写。
  - 「家里的事」文件夹第一个放「AI 动态」入口。IB 皮肤下沿用家庭动态的线条图标。
  - IB 小组件区在矮屏上本来就能自己滚，卡片只占约 50 像素，不挡内容。
- **验证**：
  - 本机临时插了 3 条假动态（测完已删）：主页卡片显示「10:25 Jasper 发布了日记」。动态页分组正确；只看 Jasper 时只剩他的那条，「下次醒来」也只剩他；点开日记跳到 /diary。控制台无错误。
  - 新测试 `test_ai_feed.py` 3 项：临时库里时间线带 actor_id、座位显示名字、页面不用 innerHTML、主页有卡片和入口。
  - 相关旧测试 `test_autonomy_journey` / `test_family_user_events` / `test_seat_names_everywhere` 共 24 项通过；`test_home_folders_ui.js`、`test_family_dynamics_ui.js` 通过。
- **还没并进来**：哨兵 / 监控日志不在这条时间线里，以后再并。


## 112. 修 §109 审查的 M1：主页布局迁移的迟到保存会盖掉新排法

- **手机页面**（`home.html`）：
  - 「旧排法 → 新默认」那次保存带上 `migration: true`，并记下「现在排着的是迁移保存」。
  - `syncLayoutFromServer` 一旦读到服务器上已有文件夹排法，先 `cancelPendingMigrationSave()`，把还没发出的迁移保存作废，再按原逻辑应用。
  - 已经排着普通保存（刚拖过图标）时，不再另排迁移保存。
  - 拖图标的普通保存不带 migration，照常覆盖。
- **服务器**（`routes/settings.py`）：`HomeLayoutUpdate` 加 `migration`。迁移写入时，如果服务器上已经有任何 `folder-*`，就不写，回 `skipped: already_migrated` 和现有排法。
- **验证**：
  - 本机页面里用真实接口重放三种时序：
    - A：排好迁移 → 别处存新排法 → 本页同步。迁移作废，新排法保留。
    - B：排好迁移 → 别处存新排法 → 本页没同步，迁移照发。服务器拒收，新排法保留。
    - C：真旧排法。照常迁移。
  - 新测试 `test_home_layout_migration.py` 4 项：迟到的迁移不覆盖、旧排法会被迁移、普通保存照常覆盖、页面里取消迁移在应用新排法之前。
  - `test_ai_feed.py`、`test_home_folders_ui.js` 通过。


## 113. 主页改版第 3 步：「开关总览」页 + 自己醒来静默时间 + 通知总开关 / 不让 AI 看的 App

草图 ③。小猫 10-31 要求：自己醒来能选 2/4/8 小时、能设静默时间；哨兵也能设静默时间；不想让 AI 看的 App 能挑出来。

- **自己醒来频率**：每位本来就有 `min/max_interval_minutes`（默认 5～1440 随机，所以显得没规律）。页面选「N 小时」就存成 0.8N～1.2N 小时之间随机（如 4 小时 → 192～288 分钟）；「自定」可填 0.5～20 小时。走原有的 `PUT /api/idle-autonomy/{actor}/config`。
- **自己醒来静默时间（新）**：
  - `quiet_hours.py`：HH:MM 到 HH:MM，可以跨午夜，按服务进程本地时间算（线上 TZ=Asia/Shanghai，和摄像头静默同一算法）；格式不对或首尾相同都当没开。
  - `SETTINGS.idle_autonomy_quiet_hours`，接口 `GET/PUT /api/idle-autonomy/quiet-hours`（时间格式不对回 422）。
  - `run_actor_once` 非手动时，若正处在静默时段：用新函数 `postpone_due_wake` 只把「已经到点」的那次挪到静默结束后 0～30 分钟内，不跑，回 `skipped: quiet hours`。手动「现在醒一次」不受影响。
- **哨兵**：直接用现有的 `/api/cam/monitor/start|stop` 和 `/api/cam/config` 的 quiet_hours_*，后端没改。
- **通知（新）**：
  - `device_context.notification_settings()` / `save_notification_settings()`：`SETTINGS.notification_context = {enabled, blocked_apps:[{package,name}]}`，按包名去重，最多 100 个。
  - 过滤分两层。`_fresh_notifications` 把总开关关着时的通知、被挑出来的 App 的通知归进已过滤，写明原因，不进提示词。`activity.record_notification` 遇到这两种直接不存，也不进活动日志。
  - `recent_apps()` 只给包名、名字和时间，不给通知内容，供页面挑选。
  - 接口：`GET/PUT /api/device-context/notification-settings`。
- **页面 `/switches`**（`static/switches.html`，`main.py` 加路由；主页「家里的开关」文件夹第一个，IB 用设置的线条图标）：
  - 自己醒来：每位一个开关 + 2/4/8/自定；空位提示；静默开关和起止时间。
  - 哨兵：巡逻开关和巡逻说明；静默开关和起止时间。
  - 通知：总开关；不让看的 App 显示成小标签，点 × 恢复，「+ 添加」从最近发过通知的 App 里挑。
  - 更多设置的链接。
  - 全部 textContent；头像只认本站地址。
- **「你不在线时改成留言」暂不放**：这个功能还没做，不放按了没用的开关。
- **验证**：
  - 本机 375 宽实测：打开 Jasper 自己醒来、选 4 小时（存成 192～288）；设静默 23:00～08:00；塞两条假通知（微信、淘宝）后挑微信不让看，微信进已过滤并写明原因，淘宝照常。时间写错时回 422。测完已把本机设置还原。
  - 新测试 `test_switches.py` 7 项：跨午夜判断、静默结束时间、静默时到点只推迟不运行、坏时间拒绝、被挑出的 App 过滤且不记录、总开关关掉全过滤、页面接口。
  - 自主活动和设备上下文相关旧测试 86 项改动前后都通过；`test_ai_feed.py`、`test_home_layout_migration.py`、`test_home_folders_ui.js` 通过。


## 114. 修 §111 审查的 M1、L1、L2：AI 动态被挤掉、图片地址穿越、记忆整理缺 actor_id

- **M1 先截全家再筛 AI 会漏**：
  - `/api/timeline` 加 `who` 参数：空是全家（老页面不变）；`ai` 是除了小猫和系统的；其他值就只要这一位。
  - 在 SQL 里先按作者筛：朋友圈和日记按 author，礼物按 COALESCE(sender,'aion')，自主活动按 actor，值走参数。筛完再截断。
  - 带 who 时不放小猫自己的事件和位置变化。
  - 加 `before` 游标（上一页最后一条的时间，含这一刻）。每类多取一条，用来判断后面还有没有，返回 `next_before`。
  - 动态页按人筛时重新从第一页读，「再往前看」用 next_before 接着往前翻，按 kind+source_id+timestamp 去重（同一秒的那条会再来一次）。
  - 主页卡片改成 `who=ai&limit=5`。
  - 本机实测：90 条较新的小猫动态 + 1 小时前的狗蛋日记 + 45 篇小克日记。卡片显示狗蛋的日记；动态页第一页 40 条，「再往前看」补齐到 46 条，没有重复，翻到底按钮收起；只看狗蛋时只剩他的。
  - 测完已删掉本机假数据。中途发现每类只取 limit 条时判断不出下一页，已改成多取一条，测试覆盖。
- **L1 图片地址**：动态页和开关总览的头像 / 附图改用 `safeLocalUrl`。要求以 `/` 开头、不是 `//`；不能带反斜杠、空白、引号、括号、尖括号；不能带 `%2e` `%2f` `%5c`；路径段里不能有 `.` 或 `..`；再要求前缀在白名单里。新测试 `test_ai_feed_urls.js` 覆盖 Codex 的 `/uploads/../../cdn-cgi/access/logout` 和各种编码变体。
- **L2 记忆整理**：`list_compression_events` 的事件按 author 补上 actor_id（aion / connor），动态页能显示。
- **测试**：`test_ai_feed.py` 新加两项：挤掉场景、单人 + 游标翻页（含「正好多一篇」的判断），在不带本次修改的 activity.py 上会失败。相关 Python 28 项通过；`test_home_folders_ui.js`、`test_ai_feed_urls.js`、`test_family_dynamics_ui.js` 通过。


## 115. 修 §114 审查的 R1–R4、L1

- R1：时间线改用复合游标（时间、类别、编号），SQL 里按 created_at DESC, CAST(id AS TEXT) DESC 排序并按游标接着取；返回 next_cursor。
- R2：记忆整理按 who 映射 target（aion→main、connor→chatroom，座位不查），带同样的游标、多取一条，时间线读取时按文本比编号。
- R3：动态页加请求代数，换人后晚到的旧回应直接丢掉，游标只在代数和人物都对得上时更新。
- R4：认领醒来之后再看一次静默；是就用 restore_claimed_wake 只在 next_wake_at 仍为空时放回静默结束后，不覆盖期间新起的计时。
- L1：.sw-picker[hidden] { display: none; }。
- **测试**：新文件 `test_ai_feed_paging.py`，5 项。
  - R1：42 条同一时刻，每页 40 条，两页读完、不重复、不打转。
  - R2：who=connor 时只查 chatroom、多取一条、游标原样传下去；座位不查记忆整理。`_read_completed_batches` 在临时库里不会被小克的 5 条挤掉，同一时刻里只取编号更小的。
  - R4：认领后进静默会放回、不运行；放回时不覆盖期间新起的计时。
  - 把 activity.py、memory_compression.py、autonomy.py、autonomy_state.py 换回 a96a3cd 的版本时，这 5 项全部失败；换回来后全部通过。
- **本机页面实测**：
  - R3：人为让「小克」那次请求晚 1.5 秒回来，先点小克再点狗蛋。最后选中的是狗蛋，列表只有狗蛋的日记。
  - L1：选完微信后，App 列表的 display 是 none，名单里出现微信。
  - 测完已删假数据、还原通知设置。
- 相关旧测试（`test_ai_feed` / `test_switches` / `test_autonomy_journey` / `test_autonomy_state`）41 项通过，`test_ai_feed_urls.js` 通过。


## 116. 修 §115 审查的三个余项（R1 / R3 / R4）

- **R1 扫满预算被当成到底**：
  - 自主活动 10 批都满、还没凑够可显示的条目时，记下扫到的最后一行，叫 frontier。
  - 这一页只给比 frontier 新的条目（这些已经确定），游标给到 frontier。所以可能返回空页带游标，不再判成读完。
  - 其他来源比 frontier 旧的条目，下一页按游标重新读到。
  - 动态页遇到「空页带游标」时，自动接着读，最多 5 次，每次都核对请求代数和人物。
  - 测试：900 条内部记录后面还有一篇日记，limit=40。第一页是空页带游标，接着读能找到那篇，最后游标为空。
- **R3 旧整页刷新失败盖掉新页面**：`load()` 开头记下这次的请求代数（loadTimeline 一开头会加到这个数），catch 里代数对不上就直接返回，不画错误提示。
  - 本机实测：人为让「全部」的整页刷新 1.2 秒后失败，同时点狗蛋。最终选中狗蛋，列表是狗蛋的日记，没有错误提示。
- **R4 第二次检查静默之后还有读库等待**：改成认领 → 读配置核对 enabled（这里有等待）→ 最后检查静默 → 紧接着开始采集，静默检查之后不再有等待。
  - 测试：在第二次读配置时打开静默，采集和活动都没开始。
- **测试**：`test_ai_feed_paging.py` 共 7 项（新加 2 项，在 67c5705 的 activity.py、autonomy.py 上会失败）；相关 Python 共 52 项通过；`test_ai_feed_urls.js`、`test_home_folders_ui.js`、`test_family_dynamics_ui.js` 通过。测完已删本机假数据。


## 117. 定位：查地址失败后不再一直用空结果；日志写清高德给的原因（11-01 小猫填了高德钥匙仍没有天气 / 地点）

- **线上只读排查**（没看钥匙本身，只看长度和格式）：
  - 定位已启用，钥匙 32 位，手机心跳正常。
  - app.log 只有一行「逆地理编码失败: 」，后面是空的。
  - 用这把钥匙查一个公开坐标（天安门）：regeo 和 weather 都回 `USERKEY_PLAT_NOMATCH (10009)`，说明钥匙的服务平台不是「Web 服务」。已请小猫重新申请一把「Web 服务」类型的钥匙。
- **代码问题**：
  - 查地址失败那次，`last_api_lng/lat` 照样写了。之后没走够 movement_threshold，就一直走「轻量处理」，用空地址和空天气，换了钥匙也不会重查。
  - 天气要靠 adcode，所以天气也一直是空的。
- **改法**（`location.py`）：
  - `missing_geo = amap_key 不为空 且 旧状态里没有 adcode`。成立时也走全量刷新，每次心跳都重查，直到查到为止。
  - 钥匙错着的时候大约 10 分钟一次，一天约 144 次，远低于高德免费额度。
  - `amap_regeo` 在 status 不是 1 时，把高德的 info 和 infocode 打进日志（不含钥匙）。
- **测试**：`test_location_api_usage.py` 加 1 项：有旧坐标、没有 adcode、没移动时也会重查，并存下地址和天气。在旧 location.py 上会失败。定位相关 15 项通过。


## 118. 小猫不在线时不去找手机，改成留言；手机上线不再刷出一串「命令已过期」

小猫 10-30 定的规矩：她常断 VPN 去忙自己的；App 不在线时，「要找手机的事」不排队，AI 干自己的事或给她留言。

- **在不在线**（`ws.py`）：手机推送服务连上时用 `android-push:<设备号>` 报到。`manager.phone_online()` 看有没有这样的连接；断开后 90 秒内仍算在线，当作网络抖动。网页、桌宠的连接不算。
- **AI 看到的说明**（`app_supervision_ai.build_app_supervision_ability_text`）：手机不在线时，只给一句 `PHONE_OFFLINE_TEXT`（手机不在线，找手机的指令先别用，想她就直接留言），不给指令格式，也省了这段长说明的字数。拿不到在线状态时照旧给完整说明。
- **不排队**（`queue_app_supervision_reply_command`）：AI 回复里如果还是带了指令，而手机不在线，就不写进 `app_supervision_commands`。返回一个带 `skipped: phone_offline` 的命令对象（字段和排队的一样，commandId 为空）；`broadcast_app_supervision_command` 见到它，不再发给手机，而是在原聊天里发一条「…未能唤回小猫：小猫的手机不在线，这次没去找手机」，用的还是原来的 `_publish_command_result`。
- **过期合并**（`GET /api/app-supervision/commands/pending`）：一次查出多条过期命令时，只在最近那条的位置发一条「手机离线时的 N 条命令都过期了」；只有一条时照旧写「命令已过期」。原来 10-27 手机上线时一下刷出 26 条。
- **测试**：
  - 新文件 `test_phone_offline_rule.py` 4 项：android-push 才算在线、断开 90 秒宽限；不在线时 AI 只拿到一句话、没有指令格式；不在线时不排队、只发一条提示、不广播给手机；26 条过期只发一条（最近那条、写明 26 条）。不带本次修改时这 4 项都失败。
  - `test_app_supervision_ai.py` 里测完整说明的那组，在 setUp 里设成手机在线。
  - 应用监管相关共 56 项通过。


## 119. 旅行：每位用不同的照片，游记里能看到「这个地方」（11-01 小猫：三位回的都是同一张图，只有个封面，不知道那地方是什么样）

- **照片数**：`travel_places.MAX_PHOTOS` 3 → 6。
- **全家出游**：原来只有第一位的群聊消息带全部照片，三张纪念卡的封面都是第一张。现在第 i 位的消息和封面都用 `photos[i % n]`，每位一张。独自 / 两人同游照旧带全部照片。
- **游记存「这个地方」**：
  - `_keepsake` 的 sources 改成 `_place_source(place)`：place、intro、weather、local_time，以及全部照片的 path 和 credit。
  - `autonomy_niches._sources` 原来只收带 url 的条目、只留 title 和 url，会把这些都过滤掉。现在带 place 的条目也收，字段各自截断；照片只认 `/uploads/travel/[\w.-]+`，不能带 `..`，最多 8 张。只有 url 的条目照旧。
- **旅行页**：点开一篇游记，下面出现「这个地方」：一排可以横滑的照片、简介、地点和当时天气、当地时间。页面再检查一遍照片地址，只认旅行照片目录；文字照旧经 esc。旧游记没有这些资料就不显示。
- **验证**：本机放一篇假游记，混进 `..` 和外站地址各一张，页面只显示 3 张正常照片、简介和天气时间；测完已删。`test_travel.py` 改一项（全家每位配不同照片、卡片里存全部照片和天气），新加一项（只认旅行照片、截断、只有网址的照旧）。旅行和游记卡片相关 41 项通过。


## 120. 聊天未读小红点（11-01 小猫：去旅行页再回来，不知道哪里有新回复）

原来只有朋友圈有未读，聊天完全没有。

- **服务器**（新 `routes/unread.py`）：
  - `GET /api/unread`：看最近 30 天里 AI 发的消息。私聊取 messages.role='assistant'；群和座位取 chatroom_messages 里 sender 不是 user / system / 空的。按窗口取最新时间，和「看到哪里」比较。
  - 窗口编号：小克私聊是 `conv:<id>`，Lucien、座位、群是 `room:<id>`。返回 unread 列表（container、kind 是 private 或 group、last_at、actor）和 private / group 两个总开关。房间不存在的跳过。
  - `POST /api/unread/read {container}`：记到现在，只往后挪（MAX）。container 格式不对回 400。
  - 新表 `unread_read_marks(container PRIMARY KEY, read_at)`，手机和电脑共用。
  - 第一次调用时把「现在」记成 `SETTINGS.unread_baseline_at`，以前的消息不算未读，免得一上线满屏红点。
- **私聊页**（`chat.js`，聊天页是整个 App 的外壳）：
  - 选中窗口、回到聊天页、页面回到前台时，把当前窗口记成已读。只在页面在前台、上面没盖着子页时才记。
  - 推送里每来一条 AI 消息（私聊或群），就刷新未读；顶栏 👥 有别的窗口未读时亮红点。
  - 停在别的子页面时，冒一条「某某回复了 · 点这里去看」，8 秒自动消失：是当前私聊就回到聊天，否则打开换人页。当前开着的就是那个群或窗口、或者正在换人页时，不提醒。
- **群聊和单独窗口页**（`chatroom.js`）：
  - 选中窗口、收到当前窗口的新消息、子页面重新显示时，记成已读。
  - 用聊天页给子页面标的 `aionSubPageVisible` 判断在不在眼前（和 common.js、桌面弹窗同一个标记），藏在后台的页面收到消息不记成已读。
- **换人页**（`contacts.js`）：人名后面、窗口标题前面亮红点；子页面重新显示时刷新。
- **首页**：
  - Dock 上「聊天」有私聊未读、「群聊」有群未读时亮红点，30 秒刷新一次。页面回到前台、聊天页把首页翻出来时，立刻刷新。
  - `chatUnread` 用 var：首页一开始画图标时就会用到，用 let 会报错，把后面的脚本全卡住。实测时发现过这个问题，当时 AI 动态卡片也因此没出来，已修。
- **验证**（本机，测完已删假消息）：
  - 往狗蛋的窗口和群里各塞一条 AI 消息后：👥、首页聊天、首页群聊三处红点都亮；换人页上狗蛋和「家」都亮。
  - 打开狗蛋窗口后，只剩群未读。
  - 停在旅行页时来一条群消息，冒出「AI回复了 · 点这里去看」。
  - 窗口页关到后台后，`crPageInFront()` 为 false。
- **测试**：新文件 `test_unread.py` 4 项：只算起点之后、AI 发的；私聊和群分开；已读只往后挪；第一次用设起点；坏编号拒绝；页面接线。读取聊天页代码的 JS 测试里，改动前后失败的是同一批，原因完全相同（缺函数、缺 playwright 等老问题）。


## 121. 修 §118 审查的两处 + 群聊页系统日志透字

- **M1 发送失败后仍算在线**（`ws.py`）：
  - broadcast 和 send_to_client 发送失败时都走 `disconnect()`，连登记一起清掉。
  - `phone_online` 要求 android-push 连接同时还在 active 里。
- **宽限期补执行**：`phone_online(grace=...)`。
  - `grace=True`（默认）：给 AI 看的那句说明用，刚断开 90 秒内仍算在线，免得网络一抖就来回切。
  - `grace=False`：真要排队找手机（`queue_app_supervision_reply_command`）、取手机摄像头时用，只认现在确实连着。断网后不再有命令排进队、等重连补执行。
- **M2 手机摄像头漏了离线规则**：
  - `camera.acquire_monitor_image` 和 `acquire_monitor_image_sync` 一开头，手机不在线（严格判断）就直接返回 `phone_offline`。这时的 no_image_context 写明手机不在线、不要描述画面、想她就留言。拿不到在线状态时照旧去取。
  - `phone_camera.request_capture` / `request_capture_sync`：发送明确返回 False 时立刻结束（unavailable / phone_offline），不再等 15 秒重发、总共 45 秒。
- **群聊页系统日志透字**（小猫截图）：群聊页的弹窗叫 `cr-syslog-modal`、`cr-wallet-panel`、`cr-transfer-dialog`，私聊转账叫 `transfer-dialog`，§102 只盖到私聊页的 `.modal`。已加进 IB 实底列表。本机开「减少动效」实测是实底。
- **测试**：
  - `test_phone_offline_rule.py` 共 9 项。新加：发送失败清登记（定向和广播）、宽限只用于说明、摄像头发送失败立刻返回且不重发（异步和同步）、手机不在线时取图直接返回不找手机。新加的 5 项在上一版代码上都失败。
  - `test_phone_camera_trigger_wiring.py` 测手机在线时的取图，在 setUp 里设成在线。
  - `test_phone_usability.py` 加群聊弹窗的检查。
  - 摄像头、应用监管、哨兵相关共 209 项，只有 `test_sentinel_routes.py` 的 2 项失败，改动前就失败。


## 122. 家里聊的 TG bot 不记得：Hub 取记忆常超时，一次超时全员停 60 秒

- **小猫反映**：在前端聊的，去问 TG bot 都不记得。这次只修 AionsHome 这一侧；Hub 是否把群聊捕获变成 bot 能想起来的记忆，另外问 Hub 窗口。
- **只读排查**（只看日志、计数，不看聊天内容）：
  - 写入正常：群聊回合进了 Hub outbox，今天也有发出去的，outbox 里没积压。
  - 读取不正常：app.log 里约 99 条「[MemoryHub] read 调用失败，60.0s 内暂停：」，冒号后面是空的（超时异常的文字是空的）。
  - 用生产参数对 Hub 计时，一次 context 要 2.3～4 秒。上限是 6 秒，群里几位同时取会更慢。
  - 原来的逻辑是一次失败就设一个全局暂停，所有人 60 秒内都不去取。群里一位超时，接下来一分钟里谁都拿不到 Hub 记忆。
  - 系统日志里「召回 N 条 / 无相关记忆」只看本机旧记忆库，Hub 取没取到完全看不出来。所以看上去像「小克不召回」。
- **改动**（`memory_hub_bridge.py`）：
  - `context_timeout_seconds` 默认从 6 改成 12，交接卡读取也一样。
  - `_trip` 改成连续失败 2 次才暂停，暂停时长默认从 60 秒改成 15 秒；读成功一次（incremental 或交接卡）就清零。写入是另一个计数，互不影响。
  - 日志写出异常类型（如 `TimeoutError`），区分是超时还是连不上。
  - 新增 `read_status(actor, chat_id)`：按「AI + 对话」记最近一次取 Hub 记忆的状态（ok / empty / timeout / error / cooldown / off）、字数、耗时，只有这些，不存记忆内容。超过 500 条时整体清空。
- **系统日志**：
  - 私聊（`routes/chat.py` 三处 debug）和群聊 / 座位（`context_builder.py` → `routes/chatroom.py`）的 debug 里加 `memory_hub`。
  - `chat.js` / `chatroom.js` 在「召回 N 条」旁边加一项，例如「🗂 Hub 记忆 812 字 · 2.6秒」、「Hub 超时没取到」、「Hub 刚连续失败，暂停中」。没接 Hub 的 AI 不显示这一项。
- **测试**（`test_memory_hub_bridge.py` 共 54 项全过）：
  - 新加：一次失败不暂停、成功清零、连续两次才停且不超过 15 秒；默认等待时间够一次真实调用；系统日志状态按窗口分开记、不含内容、超时也会记下。
  - 群聊、座位、上下文相关测试中的失败（6 项），改动前就同样失败（`test_chatroom_*` 的移动端样式 / 缓存号检查、`test_seat_chat` 的事务问题）。


## 123. 修 §122 审查（67f31e7）的 M1、M2、L1、L2

- **M1 消息没加载成功也记成已读**：
  - 私聊（`chat.js`）：
    - 新加 `currentMessagesConvId`，表示屏幕上的消息是从服务器成功取到的哪个窗口。`selectConv` 一开始把它清空，消息显示出来以后才设上、才记已读。去掉了原来请求前的 `setTimeout(markCurrentConvRead, 0)`。
    - `refreshCurrentConversationFromServer` 成功后也设上。
    - 从别的页面回到聊天页时，等重新取消息成功（返回 true）再记已读。
  - 群聊和座位（`chatroom.js`）：
    - 新加 `crRoomServerLoadedId`，只在 `crFetchRoomMessages` 真正取成功并显示后设上，并在那里记已读。
    - `selectRoom` 不再在 `loadMessages()` 之后无条件记已读。只显示了本地旧快照时不算已读。
    - 收到这个窗口的新消息时，等它显示出来（setTimeout 0）再记。
  - 只记到实际显示的消息：两边都把屏幕上最后一条消息的 `created_at` 作为 `at` 传给 `POST /api/unread/read`（临时 temp_ 消息不算）。服务器记 `min(at, 现在)`，仍然只往后挪。显示之后才到的回复仍算未读。
- **M2 查完在线后断线，命令仍可能重连后补执行**：
  - `ws.py` 新增 `send_to_phones()`：只发给 `android-push:` 的连接，至少一台确实发出去才返回 True，网页收到不算。只有手机 App 处理这个事件。
  - `broadcast_app_supervision_command` 改用 `send_to_phones`。没送到（发送前已断开，或发送失败）时，用 `withdraw_app_supervision_command` 把这条从 pending 改成 `undelivered`，手机重连时取不到。确实撤下了，才在原聊天留言「小猫的手机刚断开，这次没送到」。已经不是 pending 的（被手机处理过的）不动。
  - 你给的两个反例都由这一处兜住：检查后、写库前断线；入队后、发送前连接失效。
- **L1 坏窗口编号**（`routes/unread.py`）：编号只认 `conv:` / `room:` 加 1～120 位字母、数字、`_.-`，并核对 `conversations` / `chatroom_rooms` 里确实有这个窗口。不存在返回 404，失败时都不落库。线上 3 个私聊、4 个房间的编号都符合（只读核对）。
- **L2 系统日志漏算交接卡**（`memory_hub_bridge.py`）：新对话两路读取合并后，有交接卡就记下 `handoff_chars`。incremental 为空时状态改成 ok，字数按合并后的总长。incremental 超时、交接卡取到时，日志写「Hub 超时没取到（交接卡取到了）」。
- **测试**：
  - 新加 7 项，在上一版代码上全部失败：
    - 只记到显示的那条、不超过现在、不倒退。
    - 空白、多余冒号、不存在的窗口都拒绝，而且不落库。
    - 页面接线。
    - 没送到就撤下并留言；送到了留着等手机确认。
    - 只算手机连接。
    - 交接卡计入日志。
  - 相关 149 项全过。群聊、座位、哨兵等 261 项里有 8 项失败，都是改动前就有的那批。
  - 用桩跑了真实的 `markCurrentConvRead` / `crMarkRoomRead`：没加载成功、盖着别的页、只有旧快照时都不发已读；加载成功时只记到最后一条消息的时间。


## 124. 修 §123 复审的 R1：发送任务被取消时漏撤单

- **问题**：`send_text()` 等待期间发送任务被取消，`CancelledError` 越过 `except Exception`，撤单没执行，手机重连仍能取到这条。
- **改动**（`app_supervision_ai.broadcast_app_supervision_command`）：
  - 单独接住 `asyncio.CancelledError`：把撤单（`_withdraw_undelivered`）放进独立任务，用 `asyncio.shield` 加 `wait_for` 最多等 5 秒（`WITHDRAW_TIMEOUT_SECONDS`），然后再把原来的取消往外传。
  - 外层再次取消、或者 5 秒内没做完时，撤单任务仍在后台做完。任务引用放在 `_withdraw_tasks`，免得被回收。
  - 仍然只改 status='pending' 的，已确认的命令不动。
- **文案**：按建议把「这次没送到」改成「小猫的手机刚断开，没能确认送到」（可能其实已经执行了）。
- **测试**：新加 `test_cancelled_send_still_withdraws_before_cancelling`，走真实的 ConnectionManager 发送链路和临时 SQLite：发送卡住时取消任务，外面收到 CancelledError 之前 pending 已清空，也只留言一次。这一项在 797399b 上失败。应用监管和离线相关 59 项全过。


## 125. 修 §124 复审：发送明确失败后的撤单也要防取消

- **问题**：发送明确失败后，普通路径直接 `await _withdraw_undelivered(command)`。正在等数据库时任务被取消，撤单跟着中断，命令还在 pending。
- **改动**：抽出 `_withdraw_protected(command)`，发送被取消和发送失败两条路都用它。
  - 撤单单独跑成任务，引用放在 `_withdraw_tasks`，做完自动移除。
  - 用 `shield` 加 `wait_for` 最多等 5 秒。等的时候被取消，就把取消往外传，撤单在后台做完；超时或撤单本身出错只记日志。
  - 仍然只改 pending 的。
  - 现在两条路都不再直接等裸的撤单协程。
- **测试**：新加 `test_cancel_while_withdrawing_after_a_failed_send`，用真实 ConnectionManager 让发送失败、撤单卡在等数据库时取消任务。验证三件事：外面收到 CancelledError；放开数据库后，后台撤单做完、引用释放；pending 清空，只留言一次。这一项在 ddafd87 上失败。应用监管和离线相关 60 项全过。


## 126. 首页红点：文件夹亮了，点开里面那个应用也亮

- **小猫反映**：文件夹上出现红点，但不知道是哪个应用。是「家里的事」文件夹，红点来自朋友圈未读。原来 `momentsBadge` 只挂在文件夹图标上，文件夹里的朋友圈图标特意不挂（`!options.inFolder`），点开以后看不到是谁。
- **改动**（`home.html`）：
  - 红点不再靠唯一 id，改成 `data-badge="<key>"`。现在有三个：`APP_BADGE_KEYS = { moments, chat → chat-private, chatroom → chat-group }`。同一个红点可以同时出现在 Dock、主页格子、文件夹里，原来 `!options.grid` 那些避免 id 重复的条件都去掉了。
  - 文件夹图标的红点改成 `data-folder-badges="<文件夹里各应用的 key>"`，任意一个亮就亮。
  - 状态统一放在 `var badgeState`，`applyUnreadBadges()` 一次刷新所有红点。`renderApps`、`openFolder`、朋友圈和聊天未读的轮询都调它。
- **验证**：本机浏览器把朋友圈设成未读：「家里的事」文件夹亮，点开后朋友圈图标亮，其他不亮；Dock 聊天红点照常；控制台无报错（截图见对话）。`test_unread.py` 和 `test_home*.py` 共 116 项全过。


## 127. 给 AI 的时间写清星期几、上午下午、今天上不上班

- **小猫反映**：小克下午写的日记以为是凌晨，还以为今天是工作日（11-01 是星期日）。
- **排查**（只看元数据，没看日记内容）：
  - 服务进程时区是 Asia/Shanghai，没问题。小克那篇在北京时间 17:33 写，来源消息是 15:04～16:49。
  - 原来各处给 AI 的时间格式是「2026年11月01日 17:33:05」：没有星期几，要模型自己推算；没有上午下午。日记那几处用 `%A`，给的是英文「Sunday」；也都没说今天上不上班。
  - **座位日记（狗蛋等，`seat_diary.py`）的提示里完全没有当前时间**，上下文每条消息也没有时间。
- **改动**：
  - 新模块 `clock_text.now_text()`，输出例如「2026年11月1日 星期日 下午 17:33:05（北京时间，今天是休息日）」。
    - 时段分凌晨、早上、上午、中午、下午、晚上、深夜。
    - 工作日或休息日用 `chinese_calendar.is_workday` 判断（fund.py 已在用，包含调休和法定假日）。日历库不认识的年份退回按周末算。
  - 替换这些地方的时间：私聊 5 处 bg_block、`context_builder`（群聊、Lucien）、`seat_chat`、`schedule` 5 处、私聊日记（`memory.py`）、群聊日记（`chatroom.py`）、送礼（`gift.py`）、基金、定位 2 处、摄像头。
  - `seat_diary`：提示开头加「当前时间」，上下文每条加 `[月-日 时:分]`。
  - 「系统当前的准确时间是」这几个前缀字没变，`ai_providers._DYNAMIC_START_MARKERS` 的缓存切分不受影响。
- **测试**：
  - 新文件 `test_clock_text.py` 4 项：星期日下午的完整输出、时段和工作日、日历库不可用时的退路、各提示都改用它（座位日记有当前时间）。
  - 相关 16 个测试文件共 132 项，4 项失败，都是改动前就有的：`test_role_device_context_injection` 3 项是 fake_provider 不收 `request_timeout`，`test_seat_chat` 1 项是事务问题。


## 128. 取 Hub 记忆每次记一行日志（排查 Lucien 私聊系统日志里看不到 Hub 项）

- **小猫反映**：部署 2f6117e 后，在 Lucien 私聊里从侧边栏打开系统日志，仍然看不到「🗂 Hub」。
- **已排除**：
  - 手机用的是新页面：服务器日志里有她的 `/api/unread/read` 请求，这是新加的接口；脚本带 `must-revalidate`。
  - 服务器上的 `chatroom.js`、`routes/chatroom.py`、`context_builder.py` 都是新版。
  - Lucien 私聊走 `build_connor_1v1_context` → `build_memory_blocks(memory_hub_actor="connor")` → `_chatroom_debug_payload` 的 `memory_hub`。本机直接调 `build_memory_blocks` 能拿到状态；本机群聊页喂一条带 `memory_hub` 的 debug，能显示「🗂 Hub 记忆 900 字（含交接卡） · 7.5秒」。
  - 线上那次回复的 `memory_context` 用了 7.6 秒，说明确实去 Hub 取了。
  - 服务器进程里 Hub 是开着的，connor 对应 lucien。
- **改动**：`_note_read` 每次通过 `uvicorn.error` logger 写一行 INFO：「[MemoryHub] 取记忆 actor/chat_id：状态 N字 Ns」。本模块自己的 logger 只输出 warning 以上，所以和 reply_timing 走同一个 logger。只有状态、字数、耗时，没有内容。部署后请小猫和 Lucien 说一句，对照日志和她看到的系统日志。
- **测试**：`test_memory_hub_bridge.py` 55 项全过。


## 129. 群聊里小猫说的那一轮让 Hub 认得出是她（接 Hub #131）

- **背景**：Hub 窗口反馈，AionsHome 群聊的回合没写「是谁说的」，Hub 把小猫当成「群里有人（不确定是谁）」，10-29 以来 71 条一条都没记下。所以 TG bot 不记得前端聊的内容。Hub #131 已上线，接口见 Hub 仓库 `docs/给AionsHome的接口说明.md` 优先级 1 第 4 项。
- **改动**（`memory_hub_bridge.py`）：
  - outbox 加两列：`chat_id`（哪个窗口）和 `sender_type`（`owner` 表示用户那半句是小猫本人说的）。入队时固定下来，和身份快照一样。
  - `schedule_chatroom_capture(..., room_id)` 统一带 `chat_id=room_id`，并标 `from_owner`。用户那半句来自 `paired_user_message`，它只认 `sender == "user"` 的消息。服务端只有 `/rooms/{id}/send` 会存 sender=user，那只能是小猫自己发的；环境语音（`ambient_context`）的回合本来就不记。
  - `_send_capture`：同时满足五条才走 REST——群聊、`sender_type=owner`、平台是 aionshome / aionshome-group、有 event_id、有 chat_id。
    - REST 调 `POST {Hub}/api/capture/log`，带 `MEMORY_HUB_OWNER_SECRET`，body 为 `{user_message, ai_response, ai_id, platform, chat_type: private_group, chat_id, event_id, sender_type: "owner"}`，不带 sender_id。
    - Hub 返回：2xx 算送达；5xx 或网络错按不确定重发（有 event_id，Hub 去重）；其他 4xx 算拒收。
    - 401 / 403（主人钥匙不对）或没配主人钥匙：这条改走原来的 MCP 加共用钥匙，照常记下，只是 Hub 认不出是她。没配钥匙时只警告一次。
    - 主人钥匙只在服务器这一个函数里读环境变量，不写进 outbox、不写日志、不进任何 AI 能调的工具。
  - 私聊和其余走 MCP 的回合：有 chat_id 就带上（Hub 的 capture 新增参数）。私聊 Hub 本来就默认是小猫，不用主人钥匙。
  - 测试里只替换 `_http_client`，不动全局 httpx（MCP 客户端也用它）。
- **调用点**（`routes/chatroom.py`）：小克、Lucien 在群里的回复带上 room_id；Lucien 私聊、座位私聊改用 `schedule_chatroom_capture(..., "private", room_id)`。座位在群里说的话照旧不记（原来就是 `if not group`）。
- **旧的 71 条**：AionsHome 送达后就删 outbox 记录，本地没留 event_id，列不出逐条清单。但按上面的代码，AionsHome 发出的每一条 aionshome-group 回合，用户那半句都是小猫本人发的消息（只读核对：10-29 以来群里 sender=user 37 条，小克、Lucien 群消息各 43 条，和 71 条回合对得上）。建议 Hub 按「平台 aionshome-group、event_id 以 `aionshome-` 开头」整批认定。等小猫批准。
- **测试**：
  - `test_memory_hub_bridge.py` 新加 3 项：群里小猫那一轮走 REST（地址、主人钥匙、owner、chat_id、event_id、无 sender_id、不再走 MCP）；没主人钥匙和私聊仍走 MCP 并带 chat_id；主人钥匙被拒时退回 MCP 照常记。
  - fake hub 的 capture 收 chat_id。`test_seat_chat` 的调用断言改成带 chat_type / chat_id / from_owner。
  - 相关 113 项里只有 `test_seat_chat::test_new_window_opens_another_room_for_the_same_seat` 失败，改动前就有。


## 130. 修 §126–§128 审查的 L1、L2，并补上 Lucien 漏系统日志的入口

- **L1 日志在交接卡合并前就记**：`_note_read` 不再写日志。`context_block` 改成一层外壳：调内部的 `_context_block` 取完（交接卡已经合进来），再由 `_log_read` 记一行汇总，状态和字数与系统日志一致；有交接卡时写「含交接卡 N 字」。只有状态、字数、耗时。
- **L2 旧 Node 测试**：`test_home_folders_ui.js` 原来断言「文件夹里不显示朋友圈红点」，改成检查 `APP_BADGE_KEYS`、`data-badge`、`data-folder-badges` 和统一刷新，并且不再有 `!options.inFolder`。
- **漏系统日志的入口**（你找到的）：回复只有搜记忆或巡逻指令、没有文字时提前 return，跳过了 debug。Lucien 私聊、小克群聊、Lucien 群聊三处同样的提前返回，都在 return 前补发 `_chatroom_debug_payload`。
- **测试**：
  - 新加：交接卡合并后只记一行日志，状态 ok、字数是合并后的、不含内容；三处提前返回都发 debug（源码检查）。
  - `node test_home_folders_ui.js` 通过。
  - bridge、chatroom、seat_chat 共 108 项里 6 项失败，都是改动前就有的。


## 131. 修 §129 审查的 M1–M3、L1

- **M1 跳转算成送达**：只有 2xx 算送达。3xx 返回 `not_sent`，记录保留，稍后按退避重发，状态页写「Hub 地址跳转了（3xx），请检查 Hub 地址」。httpx 默认不跟随跳转，带着主人钥匙也不会跳去别的地址。
- **M2 限流当成永久拒收**：408、429、5xx 都返回 `uncertain`，记录保留，用同一个 event_id 按现有的有界退避重发（30 秒起，最长 30 分钟）。还没读 `Retry-After`，退避本身已经有上限。其余 4xx 仍算拒收。
- **M3 没有总时限**：建连接、发请求、读完回复整体放进一个 `asyncio.wait_for`，时限用 `capture_timeout_seconds`，和 MCP 路径一致。超时按不确定处理，记录保留、同一个 event_id 重发。
- **L1 小克私聊不带 chat_id**：`routes/chat.py` 三处 `schedule_capture` 都加上 `chat_id=conv_id`。
- **§129 身份前提的说法更正**：`paired_user_message` 只认 sender=user，但剧场同步、归巢导入也会写 sender=user 的消息。所以准确的说法是：「sender=user 的消息都是小猫这边的内容（她本人发的，或者从她的剧场、归巢里同步进来的）」，而不是「一定是她刚刚直接打的原话」。目前没有哪条路径能让陌生人写进 sender=user。71 条旧记录按小猫的同意整批补身份，结论不变。
- **测试**：新加 6 项：302、307 不删记录；408、429、503 留着重发；Hub 慢慢回时整体超时（0.3 秒）后记录保留。其中 5 项在 0903395 上失败（503 原来就会重发）。`test_memory_hub_bridge.py` 66 项全过。`schedule_capture` 相关的座位测试里只有改动前就有的那 1 项失败。


## 132. 小猫 11-03 报的几处：自主醒来兜底、失败标题、静默时段、红点、朋友圈互回、换模型不生效

只读排查结论（只看元数据、计数、系统写的失败原因，没看聊天内容）：
- 小克两次「觉得暂时没什么想做」都是兜底：11-02 是「模型未返回有效的行动数据」，11-03 是「本次行动选择调用失败」（同一时间他的线路报 503）。Lucien 两次都是自己选的。
- 狗蛋 11-03 选的是逛淘宝，标题却写成「旅行没有完成」。原因是 `taobao_native_mcp.native_command` 写死了原作者 Windows 上的路径（`C:/Program Files/nodejs/node.exe`、`H:/taobao/bin/cli-rpc.js`），服务器上没有；A2A 退路返回 401。狗蛋 11-02 旅行失败没留下原因：`str(exc)` 是空的，所以没写「中断原因」。
- 静默时段存的是 `idle_autonomy_quiet_hours = 11:00→08:00`，哨兵是 `11:00→09:00`，应该是手机 12 小时制把晚上 11 点选成了上午。所以一天只有 8～11 点能醒，他们都扎堆在 8 点多醒。最近 3 天 0～8 点没有任何 AI 消息；凌晨 5 点那两条是每天固定的记忆整理。
- 红点：服务器已读标记都对、`/api/unread` 为空，是子页面自己隔 30～60 秒才查一次，后台时还会被暂停。
- 朋友圈：只有小猫评论时才叫被评论的 AI 回；AI 评论 AI 的朋友圈，原作者不回。朋友圈完全不进 Hub。
- 换模型：她 11:16:23 在名片里换了模型（actors.json、chatroom_config.json 同时更新），11:16:53 群聊里小克还在用 kiro 渠道。群聊页把打开时的 `chatroomModel` 放在请求 body 里，服务器以 body 为准。私聊每个窗口自己存 `conversations.model`，名片换了也不改它。

改动：
- **自主醒来**（`autonomy.py`）：
  - 选行动时调用失败、或回的内容读不懂，再问一次。还不行就返回 `rest` 并带 `wake_failed`，理由写「这次没醒成：模型调用失败（类型: 说明）」或「模型回的内容读不懂」，并记 warning。时间线标题改成「X这次没醒成」，不再写「觉得暂时没什么想做」。
  - 行动失败的标题按实际动作写，「X这次没做成：逛淘宝」等（`ACTION_SHORT_NAMES`），不再一律写「旅行没有完成」。
  - 失败原因用 `_error_text`：异常说明为空时写类型名（如 TimeoutError），并记 warning。
- **静默时段**（`switches.html`）：两处时间下面用大白话写「从上午 11:00 到第二天上午 8:00，一共 21 小时」。超过 12 小时时标红，提示确认上午下午有没有选反。`clock_text` 和页面上的时段分界一致：12 点前算上午。设置本身由小猫自己改。
- **红点立刻刷新**：
  - `chat.js` 的 `refreshUnread` 每次拿到结果后，让各子页面马上重查：首页 `checkChatUnread` / `checkMomentsUnread`，换人页只在显示时 `onAionUnreadChanged`。
  - 群聊页记已读、朋友圈页 mark-read 成功后调 `window.top.refreshUnread()`。
- **朋友圈 AI 互回**（`routes/moments.py`）：AI 在另一位 AI 的朋友圈下发了顶层评论，原作者回这一条一次。作者的回复带 `target_comment_id`，不会再触发，所以不会来回没完。评论 20 条上限照旧。
- **小克的模型只认一个设置**：
  - 群聊各发送入口改用 `_room_models(body)`，以 `chatroom_config` 里保存的为准，页面带来的只在没保存时用。
  - 私聊发送以 `aion_own_model()`（名片上的）为准，窗口里存的只在名片没选时用；会话列表显示的也是它。
  - 私聊页换模型、群聊设置换小克或 Lucien 的模型时，用 `actors.set_own_model_from_elsewhere` 同步到名片（私聊页同时同步群聊设置）。
- **没做的**：淘宝（小猫找到了别的思路，等她发来）；朋友圈进 Hub（她选了全部都送，要先问 Hub 要什么格式）。

测试：
- 新加 `test_autonomy_wake_honesty.py` 4 项、`test_1103_fixes.py` 6 项，其中静默时段的文字用 node 跑真实函数。
- `test_independent_autonomy` 原来断言读不懂只问 1 次，改成读不懂问 2 次。
- 本机浏览器在开关总览页验证：11:00→08:00 显示 21 小时并标红；23:00→08:00 显示 9 小时。
- 相关 48 个文件里 6 项失败，都是改动前就有的：model_audio / model_transport 4 项、seat_chat 1 项、bridge 的计时用例 1 项（你上次也提过）。friend_visit 测试文件改动前就会卡住。


## 133. 修 §132 审查的 M1–M3、L1：小克的模型真正只认一个设置

- **M3 其他生成入口还在用窗口里的旧模型**：
  - 不再一个个改调用方，直接改统一入口 `config.aion_model_key`：名片上「TA 自己的模型」（`aion_own_model()`，要求还在、能用、不是 Codex、不是别人专属）能用就用它，调用方传来的值只在名片没选或用不了时才看。
  - 这样自主醒来、朋友圈评论、基金、闹铃、提醒、定位、摄像头、礼物、英语角、记忆整理、人设演化、维修室、浮窗，凡是走 `aion_model_key` 的，都跟着名片走。
  - 私聊恢复原来的 `private_model_key(conv["model"])` 写法（统一入口已经先看名片）。
  - `test_model_defaults` 里「选了别的就照她选的」改成「只认名片」：在别处换模型会同步到名片，不会出现选了却不生效。
  - 读 `conversations.model` 但没经过 `aion_model_key` 的：`theater.py` 是剧场自己选的模型，`monthly_chat_archive.py` 是归档时记录用的，这两处没动。
- **M1 群聊实际用的 PATCH 房间设置漏了同步**：抽出 `_sync_model_cards(before, cfg)`，`PUT /config` 和 `PATCH /rooms/{id}/settings` 都在保存时调用它。
- **M2 旧弹窗把旧模型写回去**：
  - 服务器：`_sync_model_cards` 只在这次保存后的模型和保存前不一样时才同步名片。
  - 前端：`crPopulateSettings` 把打开时的模型记在 `dataset.opened`；`saveSettings` 只有选择框和打开时不同才提交 `aion_model` / `connor_model`，保存后更新 `opened`。只改上下文条数时不带模型，旧值不会覆盖群聊设置，也不会进名片。
- **L1 起止相同**：显示「开始和结束是同一个时间，等于没有设置静默时段」。
- **测试**：
  - `test_1103_fixes` 新加：名片是 New 时，`aion_model_key("Old")` 返回 New；`_sync_model_cards` 只在真的换了模型时同步；两个保存入口都调用它；前端只提交改过的模型；起止相同时的说明。
  - 相关 410 项只有 2 项失败（`idle_autonomy` 的 friend_visit、`seat_chat` 的事务），改动前就有。
  - 浮窗、闹铃、定位、基金、英语角、人设演化、礼物、维修室、剧场、摄像头等 27 个文件里有 3 项失败（英语角 2 项、剧场 1 项），单独在改动前的代码上也同样失败。


## 134. 修 §133 复审的 R1、R2

- **R1 私聊换模型失效**：
  - 把「运行时选模型」和「保存小猫的选择」分开。新增 `config.aion_selectable(key)`：还在、没停用、不是 Codex（只给 Lucien）、不是别人专属线路，就返回它，否则返回空。
  - `aion_model_key` 改成 `aion_own_model() or aion_selectable(key) or default_model_key()`，行为和 §133 一样。
  - `PUT /api/conversations/{id}` 保存模型时用 `aion_selectable(body.model)`：合规就存下并同步名片、群聊设置；不合规返回 400，什么都不改。`body.model` 为空时当作没换模型。
- **R2 PATCH 房间设置写回旧快照**：
  - 开头只用 `_apply_config_update` 做校验（不合规就 400，什么都不改）。
  - 真正的「读最新配置 → 只合并这次提交的字段 → `_sync_model_cards` → `save_chatroom_config`」挪到所有数据库读写之后、`append_sync_event` 之前，中间没有 await。名片（`actors` 同步写）、`PUT /config`（同步的读改写）都不会被它的旧快照覆盖。
  - 取舍：配置文件仍在数据库提交前写好；如果之后登记同步事件失败，房间改动会回滚，但这次的配置已经存下。
- **测试**（`test_1103_fixes` 新加 3 项）：
  - 私聊提交 New 后，名片和群聊都同步成 New；提交 Codex 返回 400。
  - `aion_selectable` 保留各项限制。
  - PATCH 里从读最新配置到写回之间没有 await，而且发生在房间读写之后。
  - 相关 328 项里有 3 项失败（聊天气泡样式、friend_visit、seat_chat 事务），改动前就同样失败。
  - `test_model_defaults` 的计时用例在机器忙时偶尔超时，单独重跑两次都通过。


## 135. §134 的 Low：没换模型时不广播空模型

- 2026-11-03 13:49（北京时间）已部署 87e6a4d（§132–§134，你审过）。
- `PUT /api/conversations/{id}` 提交 `model=""` 时，存储的模型原来就不动，但广播里还带着空模型，页面会据此把缓存的模型清空。现在广播前先去掉 payload 里的 model，只有真的换了模型才带上。
- 测试：`test_1103_fixes` 新加 1 项（源码检查）。


## 136. 狗蛋（座位）在群里的回复也记进 Hub

- **Hub 窗口反馈**：一条狗蛋（jasper）的 aionshome-group 回合都没收到，只有 claude 和 lucien 的。
- **只读核对**：狗蛋在群里，10-29 以来群消息 41 条；服务器上 `source_ai_for("ai3")` 是 `jasper`，映射正常。原因在 `_generate_seat_reply` 里写着 `if not group and not extra_prompt`：座位的群聊回复特意不记，注释说「和小克、Lucien 的群聊回复一致」（§ 第三批）。后来小克、Lucien 的群聊回复都通过 `schedule_chatroom_capture` 记进了 Hub，这里没跟上，三位不对等。
- **改动**：条件改成只看 `extra_prompt`（闹铃、主动陪伴这类系统叫来的话照旧不记）。群聊回复用 `schedule_chatroom_capture(actor_id, paired_user_message(...), clean_text, "group" if group else "private", room_id)`，和小克、Lucien 走同一条路：群聊带 chat_id、from_owner，小猫那一轮走主人钥匙 REST。
- **测试**：`test_seat_chat` 新加：座位群聊回复以 `chat_type="group"`、`chat_id=房间`、`from_owner=True` 入队。座位、群、bridge 共 285 项里有 2 项失败（seat_chat 事务、bridge 的计时用例），改动前就有。
- 同批还有 §135（718b255）：没换模型时不广播空模型。


## 137. 时间选择改成 24 小时制的下拉框

- **小猫反映**：静默时段的表盘（手机 WebView 自带的 `<input type="time">` 选择器）只有 12 小时，怎么选都是上午；想要 24 小时只能手动输入。
- **改动**：新增 `static/time24.js`，在开关总览、摄像头、定位、健康四个页面引入。
  - 把页面上的 `input[type=time]` 换成「几点（00～23，后面标凌晨/上午/下午/晚上）」和「几分（5 分钟一格；以前存的不是 5 的倍数就补上那一项）」两个下拉框。
  - 原输入框藏起来照旧使用：在这个元素上重定义 `value`，页面代码赋值时下拉框跟着变；选下拉框时写回原输入框，并派发 `input` 和 `change`（含 `onchange` 属性），各页面原来的保存逻辑一行不改。
- **验证**：
  - 本机浏览器开关总览：选「23 点」后，`/api/idle-autonomy/quiet-hours` 存成 23:00，下面写「一共 9 小时」（截图见对话）。
  - 摄像头、定位、健康页各有 2 组下拉框，读到已有值，控制台无报错。
  - `test_1103_fixes` 新加 1 项。


## 138. 修 §136 的 R1：AI 之间自主接话不按「小猫的回合」记；AI 动态页人名一排被压扁

- **R1**：`autonomy._reply_in_group`（AI 自主找家人在群里聊）叫小克、Lucien、座位接话时，`paired_user_message` 会往回找到小猫旧的那句，配成新的 owner 回合；并发时还会记两次。小克、Lucien 本来也有同样的问题，§136 之后座位也有了。
  - `_generate_seat_reply`、`_reply_aion`、`_reply_connor` 加参数 `capture_user_turn: bool = True`，为 False 时不调 `schedule_chatroom_capture`。
  - `_reply_in_group` 三处都传 `capture_user_turn=False`。
  - 小猫发起的群聊、`trigger_ai_chat`（多轮里 `paired_user_message` 在 AI 说过话后返回空串）不变。系统叫来的（带 `extra_prompt` 或 `ambient_context`）本来就不记。
- **AI 动态页**（小猫截图：顶部人名一排只剩一条缝）：`.feed-chips` 是能横向滚动的元素，在纵向 flex 的滚动容器里最小高度按 0 算，内容一多就被压扁。加 `flex-shrink: 0`。本机手机宽度塞 40 张卡片验证：不加时 2px，加了 38px（截图见对话）。
- **测试**：`test_seat_chat` 新加 2 项：座位 `capture_user_turn=False` 时不入队；`_reply_in_group` 三处都关掉捕获。座位、自主、群相关 58 项里只有 seat_chat 事务那 1 项失败，改动前就有。


## 139. 逛淘宝改用淘宝联盟官方接口（服务器上直接搜商品）

- **背景**：原来的 `taobao_native_mcp` 写死了原作者 Windows 上的 Node 和淘宝脚本路径，服务器上用不了，A2A 退路也返回 401。小猫申请了淘宝联盟的应用（他方平台 / 社交平台 / 爱分享），「物料搜索」（16516，`taobao.tbk.dg.material.optional`）、「物料信息查询」（16189）、「物料精选」（16518）已批，新版「商品物料获取」（27939，`taobao.tbk.dg.material.optional.upgrade`）还在审核。
- **新模块 `taobao_union.py`**（只读，只搜商品）：
  - 调淘宝开放平台网关 `https://eco.taobao.com/router/rest`，MD5 签名（secret + 按参数名排序的 key+value + secret，大写十六进制），时间戳用北京时间。
  - 先调新版 upgrade；返回权限错误（code 11 或说明含 permission）才退回旧版物料搜索；其他错误（限流等）直接报，不重试。
  - `normalize` 兼容新旧两版返回：商品 ID 允许加密 ID（`[0-9A-Za-z_-]{5,80}`），必须有标题。链接优先用能核对 id 的 `item_url`，否则用联盟推广链接（click_url / url / coupon_share_url），只认 https 的 taobao.com / tmall.com 及其子域；图片只认 https 的 alicdn。不合格的计入 skipped。
  - AppSecret 单独存 `data/taobao_union_secret.json`：先以 0600 新建临时文件再替换，git 忽略。不进设置 JSON，不回给页面，不进请求参数（只用于算签名），也不进报错。
- **接入 `taobao_shopping`**：
  - 设置多一种连接方式 `union`，`union_app_key`、`union_adzone_id` 存在设置里；`settings()` 只回 `union_secret_set` 布尔值。
  - `set_settings`：校验 AppKey 和推广位 ID 格式；选 union 时三样必须齐（AppSecret 可沿用已存的）；AppSecret 留空表示不换。
  - `search_and_record`：union 走 `taobao_union.search`，候选标 `transport="union"`，skipped 透传。
  - `check_connection`：union 时试搜「水杯」3 件，并说明用的是哪个接口。
- **页面**（`taobao.html` / `taobao.js`）：连接方式第一项是「淘宝联盟官方接口（推荐）」，下面三栏 AppKey、AppSecret（密码框，打开时永远是空的，旁边提示「已存过（不显示）」）、推广位 ID；保存后清空密码框。商品链接放行 taobao.com / tmall.com 及其子域（联盟推广链接是 s.click.taobao.com）。
- **测试**：新文件 `test_taobao_union.py` 6 项：
  - 签名算法；
  - 新旧两版返回的解析和拒收；
  - 新版没权限时退回旧版，请求里只有签名不含 AppSecret；
  - 限流等错误不重试；
  - AppSecret 不回给页面、留空沿用、格式校验；
  - 联盟搜索结果能记录、能收藏。
  - 和原来的 `test_taobao_shopping` 一起 22 项全过。本机浏览器看过设置对话框（截图见对话），没有在本机真的保存钥匙。
- **还没用真钥匙验证过**：上线后由小猫在页面里填，再点「检查已保存的连接」。


## 140. §139 的 L1：淘宝链接解析边界

- 2026-11-03 16:49（北京时间）已部署 9f98c17（718b255～9f98c17，你审过）。
- `taobao_union._clean_url`：带反斜杠、空白、控制字符的，解析出错的（如 `https://[bad`、`:bad` 端口），端口不是 443 的，一律不要。`_taobao_link` 和 `_image` 都先过这一关。`search` 逐件 `normalize`，单件抛异常只计入 skipped，不让整次搜索失败。
- `taobao.js` 的 `productLink`：`new URL()` 包 try，解析不了的只显示文字、不能点。
- 测试：`test_taobao_union` 新加 1 项，覆盖你给的三个反例和 8080 端口、带空格；`https://s.click.taobao.com:443/x` 照常接受；一件坏链接加一件好商品时得到 1 件、skipped 1。淘宝相关 23 项全过。


## 141. 小红书页「测试登录」先保存刚填的 cookie

- **小猫反映**：贴好 cookie 点「测试登录」不行。服务器上没有 `xhs_lite_config.json`，也没有 `PUT /api/xhs-lite/config` 请求：她没点保存，测试登录读的是空设置（「未配置小红书 cookie」）。
- **改动**（`static/xhs-lite.html` 的 `checkLogin`）：Cookie 输入框里有新内容时，先 `saveConfig({quiet:true})`，失败就停，成功后再测。`saveConfig` 本来就在保存后清空输入框，所以不会重复保存。


## 142. 小红书「随便逛」：刷推荐或按兴趣搜，看完一篇回来说两句

- **小猫要的**：不用盯着某个账号，随便逛逛；看到的东西能在私聊或群里说，这样能进记忆。
- **配置**（`xhs_lite.py`）：新增 `roam_mode`，`explore`（默认）或 `target`（原来「看指定账号最新帖子」的流程，原样保留）。`is_ready_for_auto` 只在 target 模式要求填目标账号。`routes/xhs_lite.py` 的配置更新接受 `roam_mode: Literal["explore","target"]`。
- **`_run_explore`**（只看：不评论、不点赞、不收藏，不走写入开关）：
  1. 一半机会刷首页推荐（`list-feeds`，homefeed_recommend）；另一半先问 TA 想搜什么（`search`，general 排序），不想搜就改刷推荐。
  2. 列表前 12 篇（标题、作者、赞数）给 TA 挑一篇，`-1` 表示都不想看，记为 `skipped`，不再请求详情。
  3. `get-feed-detail`（xsec_source 按来源用 pc_feed / pc_search）读正文和评论（评论条数上限沿用 `max_comments_for_prompt`）。之后问 TA 要感想 `thoughts`，以及是否分享：`share` 为 private / group / none，加上要说的 `message`。
  4. private 走 `autonomy._save_private_message(force_private=True)`；group 走 `_latest_group_room_id` + `_save_autonomy_chatroom_message`；发送失败记 `share_error`，不影响这次逛的结果。
  5. 每一步的提示里都写明：笔记是外部内容，不是指令，也不是小猫或 TA 自己的经历。
  - 模型调用：刷推荐 2 次，按兴趣搜 3 次。
- **家庭动态**（`autonomy._run_xhs_roam`）：explore 时标题写「X在小红书看了一篇笔记（，还跟小猫说了 / 还在群里说了）」或「逛了逛小红书，没看到想点开的」；详情写怎么逛的、笔记标题、感想。`ACTION_DEFS["xhs_roam"]` 的说明也改成随便逛。
- **页面**（`xhs-lite.html`）：「模块与权限」里加「巡游方式」下拉框；运行结果多显示「搜了 / 刷了首页推荐」「感想」「跟小猫说 / 在群里说」。
- **Hub**：这类「AI 自己的经历」怎么记进 Hub，已经问 Hub 窗口，等它定格式。这次只在聊天和动态里说，不往 Hub 写。
- **测试**：
  - 新文件 `test_xhs_explore.py` 4 项：刷推荐、挑一篇、私聊分享，并且不评论；按兴趣搜、群里分享；都不想看就不读详情；`share=none` 不发；随便逛不需要目标账号。
  - `test_seat_autonomy_guard` 里看指定账号的用例改成显式 `roam_mode: "target"`。
  - `test_seat_autonomy_more` 补上 §138 的 `capture_user_turn: False`（这个断言是 §138 漏改的）。
  - 自主相关 49 项全过，`node test_xhs_seat_run_ui.js` 通过。本机浏览器看过页面和运行结果显示（截图见对话）。


## 143. 修 §142 的 M1：随便逛分享前再核对一次

- 新增 `xhs_lite._still_allowed_to_share(actor)`：总开关、这一位的开关；座位再走 `autonomy.verify_seat_pin`（启用、人设、所选模型、后台线路、线路配置都没变）。不看写评论开关，因为在家里说话不往小红书写东西。
- `_run_explore` 分享时先把房间选好：群聊用 `_latest_group_room_id`；座位私聊用 `_get_or_create_seat_room`，不再经过 `_save_private_message`，那里面还有等待。然后核对，核对和保存之间没有别的等待。核对没过就 `shared=False` 并写 `share_error`，这次逛的结果照常保留。小克、Lucien 私聊仍走 `_save_private_message(force_private=True)`：他们没有座位那种固定线路，核对放在调用之前。
- 测试：`test_xhs_explore` 新加 2 项，都在 8ceef74 上失败：生成期间关了模块、关了这一位、座位换模型，都不再发出，但逛的结果保留；座位私聊发到自己的房间，并且在核对之后发。小红书和自主相关 27 项全过。


## 144. 修 §143 余项：写库前最后一刻再核对（guard 传进保存函数）

- `routes/chatroom.py`：新增 `MessageBlocked`。`_save_msg` 加可选参数 `guard`：链接预览、手环附件、查重占位这些等待都做完以后，紧挨着 INSERT 调一次，中间没有等待。返回非空就抛 `MessageBlocked`；事务没提交，查重占位跟着回滚，什么都不写。
- `autonomy.py`：
  - `_save_autonomy_chatroom_message`：`guard` 随 kwargs 交给 `_save_msg`；内容里有拍一拍时，先在 `process_pat_commands` 之前核对一次，免得没发消息却先拍出去了。
  - `_save_aion_private_message` 加 `guard`：拍一拍之前核对一次；打开写库连接后、INSERT 之前再核对一次，中间没有等待。
  - `_save_private_message` 加 `guard`，传到所有分支：小克在群、小克私聊、座位、Lucien。
- `xhs_lite._run_explore`：原来的「选好房间后核对」保留；再把 `guard=lambda: _still_allowed_to_share(actor)` 传给保存函数。被拦下时按原逻辑 `shared=False` 并写 `share_error`。
- 测试：
  - 新文件 `test_save_guard.py` 4 项，用真实的保存函数和临时 SQLite：
    - 群聊 `_save_msg`：链接预览期间关了开关，消息不写；
    - 小克私聊：查窗口、链接预览之后关了开关，消息不写；
    - 没关开关时照常写入；
    - `guard` 传到 `_save_private_message` 的每个分支。
  - `test_xhs_explore` 的两个断言改成接受 `guard` 参数。
  - 小红书、自主、聊天室相关 87 项里有 5 项失败，都是改动前就有的 chatroom 样式和缓存号用例。


## 145. §144 的 Low：新建私聊窗口和写消息放进同一个事务

- 2026-11-03 18:14（北京时间）已部署 e39fd3b（85cb1e8～e39fd3b，你审过）。
- `_save_aion_private_message`：没有私聊窗口时，原来先建窗口并提交，再核对写消息；拦下后会留下一扇空窗口。现在先只定好新窗口的编号，建窗口和写消息放在同一个连接、同一次提交里，并且都在 `_check()` 之后。核对没过就都不写。
- 测试：`test_save_guard` 新加 1 项：没有窗口、写入前被拦，窗口和消息都是 0。相关 51 项里只有 friend_visit 那 1 项失败，改动前就有。


## 146. 主动关心改规矩：只在回她时约下次、有理由才约（最短 10 分钟）、静默时段不来

- **小猫反映**：开了主动关心以后，三位风吹草动就来找她。只读统计（只数条数和时间）：11-04 到 11-05，她没说话时 AI 又来的大约 70 次（小克 32、Lucien 28、狗蛋 11），凌晨 0～8 点 5 次。
- **原规矩**：每条回复都要输出 `[NEXT_CHAT:x]`（1～60 分钟）或 NONE，提示词写的是「高优先级要求……尽量主动安排」。到点 AI 来找她；那条消息又会约下一次，她不回就一直接力。静默时段不管，三位各算各的。
- **改动**（小猫选了 1、2、3）：
  1. `proactive_companionship._is_replying_to_her`：同一窗口（私聊按 conv_id，聊天室按 room_id）往前找「小猫和这位」最近的一句，是小猫的才算在回她，才能约下次。主动关心到点、自己醒来说的话都不是在回她，约不了，所以不会一直追。指令照样从正文里去掉。
  2. 提示词改成「只在真有理由时才安排……大多数时候不需要……不要为了显得关心而安排」，范围写 10～60；`extract_next_chat_decision` 把 1～9 提到 10（`MIN_NEXT_CHAT_MINUTES`）。
  3. `schedule` 里两处到点处理（`_fire_alarm`、`_fire_monitor` 的主动关心分支）：认领计时器、确认开关之后，若在「自己醒来」的静默时段（`autonomy.get_wake_quiet_hours`）里就不开口。计时器已认领，所以不会出了静默再补一次。
- 第 4 条（群里一次只来一个）她没选，没做。
- 小猫想以后把「什么时候主动来找」和待办里的「心潮·念」接起来，先记着。
- **测试**：
  - `test_proactive_companionship`：在回她时照常约，按 12 分钟；新加「她没回、自己找上门的那条不约」；最短 10 分钟；提示词检查。
  - `test_seat_proactive`：座位同样两种情况；新加静默时段到点不开口，计时器也用掉。
  - 主动关心和日程相关 49 项全过。


## 147. 修 §146 的 M1、M2

- **M1 同一秒后写进来的消息被当成「前一句」**：`_is_replying_to_her` 先从库里取这条消息自己的 `created_at` 和 `rowid`，库里没有就不约；然后只看严格排在它前面的（`created_at < t`，或者 `created_at = t 且 rowid 更小`）。私聊、聊天室用同一套写法。
- **M2 准备期间进了静默仍调模型**：新增 `schedule._proactive_quiet_now()`。`_proactive_switched_off` 改成「开关关了，或者现在是静默时段」都算。这个函数原来就在两条到点路径、四处生成器的模型调用前复查，所以都覆盖到了。座位的 `proceed_check` 也加上 `and not _proactive_quiet_now()`。两处认领后的静默检查改用同一个函数。计时器已认领，仍然不补发。
- **测试**：
  - 新加 4 项，其中 3 项在 e0cac12 上失败：
    - 自己找上门那条之后她同一秒回了，仍不约；
    - 回她的那条之后自己同一秒补了一条，仍照常约；
    - 库里没有这条消息时不约（新加，旧代码也通过）；
    - 准备期间进入静默时 `_proactive_switched_off` 为真，闹铃不受影响，座位的 `proceed_check` 也检查静默。
  - 主动关心和日程相关 53 项全过。


## 148. 小猫画像：旧报错不再一直挂着；整理时只改状态的条目沿用原正文

- **小猫反映**：画像页「自动维护」显示 Codex 上限；「当前状态」写着在戳狗蛋，不知道哪来的；长期画像是空的。
- **只读排查**（只看状态、计数、报错，没看画像正文）：
  - 11-02、11-03 有 20 次 `CodexAppServerError：usage limit`（画像用的是 sentinel 的 codex_luna 线路），后面 176 次都成功了。但 `snapshot()` 取的是「最近一次失败」的报错，不管之后有没有成功，所以一直挂着。
  - `consolidation_error` 是 4 次「画像：每条画像请填写 1–600 字」。整理时模型只想改状态（标成已结束），没给正文，`_entry` 要求必须有正文，这几条就被拒了；`needs_consolidation` 一直为真，24 小时后再试也一样。当前 16 条 recent、1 条 profile。
  - 「当前状态」的两个来源是她 11-04 22:56、22:58 在群里自己发的两条消息（sender=user，65、97 字），不是凭空来的，几小时后会过期，不算 bug。
- **改动**（`post_sentinel.py`）：
  - `snapshot()` 只显示比最近一次成功（status='done'）更新的失败报错。
  - `_apply_analysis`：upsert 已有条目时，如果没给正文，就沿用原正文（只改状态等字段）。新条目照旧必须有 1～600 字正文。
- **测试**：`test_post_sentinel` 新加 2 项：只改状态的条目保留原文且不报错，新条目没正文仍报错；旧失败在后来有成功后不再显示。共 29 项全过。
- **没改的**：「近期待办需要 review」是原设计：待办不能由 AI 自动收尾，要小猫确认完成或取消。


## 149. 世界书：每位 AI 的复盘、自动进化和名字都在这里

- **小猫反映**：世界书里只找到小克的复盘和自动进化开关，其他 AI 的没有；名字也只有小克的。
- **现状**：后台三位都支持（`/api/persona-evolution/main-ai`、`/connor`、`/seat/{actor}` 的 config / runs / run-test）。页面上本来就能切到别的 AI 改人设（`wbPeople` + `/api/actors/{id}/persona`），但复盘面板放在 `#aionPersona` 里，只对小克生效；Lucien 的开关藏在群聊页人设设置里，座位的页面上没有。
- **改动**（`static/worldbook.html`）：
  - 复盘面板挪到人员切换下面，所有人共用。`evolutionEndpoint(id)` 按当前选中的人返回 main-ai / connor / `seat/{id}`；`EVOLUTION_ACTOR_UI.ai.endpoint` 改成 getter，原有的开关、立即复盘、记录等函数不用改。
  - `refreshEvolutionForPerson()`：切人时读这位的复盘开关（GET config）、最近复盘和记录，标题用这位的名字。`persistPersonaEvolutionEnabled` 里旧的 404 兼容只在小克时走。
  - 其他人的人设上方加「名字」栏：Lucien 保存到群聊设置的 `connor_name`，座位保存到名片（`PUT /api/actors/{id}`）。和人设一起点保存，存完刷新人员按钮和标题。
  - 说明文字写清复盘做什么：北京时间 05:00，看前一天她对这位消息的点评，改人设里标着「可进化」的几段；没有点评就跳过。
- **验证**：本机浏览器切到第二位和 Jasper 时，标题、名字、接口（connor / seat/ai3）、复盘记录都对得上，控制台无报错（截图因为窗口不在前台没截成）。`test_1103_fixes` 新加 1 项源码检查；世界书、座位复盘相关 24 项全过。
- **另记**：小猫说她很少用点评，所以按现在的规矩基本不会进化。要不要改成没有点评时按当天记忆小步调整，等她决定。


## 150. 自动进化：没有点评时按当天记忆「小步调整」

- **小猫选的**：她很少点评，原来没有点评就跳过，三位基本从没进化过。她选「没点评时也可以小步调整」。
- **改动**（`persona_evolution.py`）：
  - `run_persona_evolution`：没有点评但当天有记忆摘要时，`small_step=True`。点评和记忆都没有时照旧跳过。小步时不做点评指纹去重；定时任务本来就按窗口去重。
  - `_build_prompt(..., small_step=True)`：单独一套说明。这次不是反馈复盘，只看当天记忆摘要，大多数时候不用改；最多改一段，只能是 `SMALL_STEP_SECTIONS`（表达与互动方式、关系锚点、人格与判断）里的一段；只能小改，并且返回完整新内容；不要把偶然事件写成永久性格；改了就用第一人称告诉她。
  - 程序端强制：只保留允许的那几段里第一段，并且要通过 `small_step_ok`（原文至少保留 75%，新增不超过 200 字，不能清空）。不合规的不应用，写进 error 说明。原有的锁定分区、等待期间分区被改、座位线路固定等检查都不变。
  - 返回值多一个 `small_step`；提示文字分「小步调整」和「反馈复盘」。
- **页面**：世界书复盘说明改成「没有点评时，按当天的记忆最多小改一段」。
- **测试**（`test_seat_persona_evolution` 新加 4 项）：
  - `small_step_ok` 的边界：原文很短时加一句算小改，大段重写、清空、没变都不算；
  - 没点评、有记忆时，用小步说明，只应用第一段允许的小改，第二段和核心身份不动；
  - 大段重写不应用，状态是 reviewed；
  - 点评和记忆都没有时仍跳过，不调模型。
  - 人设和世界书相关 33 项里只有改动前就有的 chatroom 缓存号用例失败。


## 151. 修 §149 的 M1–M3：世界书切人太快时不串人

- 新增 `wbPersonGen`（每次 `selectPerson` 加一）和 `samePerson(id, gen)`。所有异步结果回来时都核对「人 + 序号」，对不上就丢掉。
- **M1**：
  - `refreshEvolutionForPerson` 读开关后、`loadPersonaEvolutionRuns` 读记录后，都先核对再写界面；切人时先清空旧记录。
  - `loadPersonaEvolutionRuns` 的接口按发出时的人固定（`evolutionEndpoint(id)`）。
  - `runPersonaEvolutionTest`：接口按发出时的人固定；结果和报错只在仍是这个人时才显示，提示里带上名字。
- **M2**：`saveOtherPersona` 在第一个等待之前就固定人、序号、人设内容、名字和原名字。返回后的界面更新只在仍是这个人时才做。改名仍走原来的接口。
- **M3**：`persistPersonaEvolutionEnabled(enabled, actor, person)` 按发出时固定的人选接口，404 兼容回退只在发出时是小克才走。`savePersonaEvolutionToggle` 失败回滚开关、解除禁用，也只在仍是这个人时才做。
- **验证**：
  - 本机浏览器用真实页面函数，加上人为让请求变慢的假接口，重放三种情况：
    - Lucien 的记录和开关晚回来，切到 Jasper 后显示的仍是 Jasper 的；
    - Lucien 的开关 404 晚回来时已切到小克，不写小克的世界书；
    - 保存 Lucien 期间切到 Jasper 并改了 Jasper 的名字栏，名字仍只写给 Lucien（connor_name）。
  - `test_1103_fixes` 新加 1 项源码检查，14 项全过。


## 152. 修 §151 余项：复盘开关按人记保存状态，读取失败保持禁用

- 全局的 `personaEvolutionConfigSaving` 换成按人记的 `evolutionSavingFor`（Set），`isEvolutionConfigSaving` / `setEvolutionConfigSaving` 都带上人。
- `refreshEvolutionForPerson`：只有这位的设置读成功、并且这位没在保存时才放开开关；读取失败就显示「复盘开关读取失败，先不能改（刷新页面再试）」，开关保持禁用，不读记录。成功读出的人记在 `toggle.dataset.loadedFor`；初始化时记为小克。
- `savePersonaEvolutionToggle`：如果这位还在保存，或者开关不是这位读出来的，就把这次点击还原并提示，不再悄悄什么都不做。保存状态在发出时按人加上，`finally` 里按人去掉；只有仍是这个人、并且是这个人读出来的开关，才重新放开。
- 验证：本机浏览器用真实页面函数重放。Lucien 的开关保存还没完成就切到 Jasper，Jasper 的开关可以点，也真的发出了 `seat/ai3/config` 的保存；Jasper 读取失败时开关禁用并显示提示。`test_1103_fixes` 新加 1 项，15 项全过。


## 153. 修 §152 余项：保存中途切走再切回来，存完后重读开关

- `savePersonaEvolutionToggle` 的 `finally`：先按人去掉保存状态；如果此时选中的还是这个人（中途切走又切回来也算），就调 `refreshEvolutionForPerson()`，按当前序号重读服务器上的值，再决定开关值、能不能点。保存成功、失败都走这一步。
- `refreshEvolutionForPerson` 加 `wbEvolutionLoad`：每次读取加一，只认最后一次（同时还核对人和序号）。切回来时那次「保存中读到的旧值」晚回来，也盖不掉保存后的重读。
- 验证：本机浏览器用真实页面函数重放。Lucien 的开关保存（延迟 500ms）期间切到 Jasper 再切回：保存中开关禁用；保存成功后恢复可点，显示新值（开）；保存失败后恢复可点，显示服务器上的值（关）。`test_1103_fixes` 新加 1 项，16 项全过。


## 154. 朋友圈进 Hub（接 Hub #136）

- **小猫要的**：朋友圈「全部都送」。Hub #136 已上线，格式按 Hub 仓库 docs/给AionsHome的接口说明.md 优先级 1 第 5 项。
- **做法**：家里有 8 处会写朋友圈和评论（朋友圈页、日记、座位日记、自主醒来、私聊、群聊等）。没有在每处都插一段，而是新增 `moments_hub_sync.py`，每分钟扫一次新增的朋友圈和评论，排进 `memory_hub_bridge` 原有的待发队列（outbox）。以后新加的发朋友圈入口也不会漏。
  - 第一次运行只记下起点，**不补送旧朋友圈**。之后每次往回多看 10 分钟，防止同一时刻写进库、时间稍早的那条被漏掉。已排过的记在 `moment_hub_synced` 表里，不会重复排。
  - 每条一轮 `POST /api/capture/log`：`platform=aionshome-moments`、`chat_type=private_group`、`chat_id=moment:<编号>`、`event_id=aionshome-moments-<朋友圈编号>-<评论编号或 post>`。
  - **小猫写的**：主人钥匙 + `sender_type=owner`，内容放 `user_message`；`ai_id` 填这条朋友圈作者的 AI，朋友圈本身是她发的就填 claude（取 `source_ai_for("aion")`）。
  - **AI 写的**：共用钥匙，内容放 `ai_response`，`ai_id` 填作者本人。没接 Hub 的空位座位跳过。
  - `reply_to_id`：回复某条评论填那条的 `event_id`；直接评论朋友圈的填朋友圈本身的 `event_id`；朋友圈本身不填。
  - 只有图没有字的填「[图片]」。不填 `sender_id`。
- **发送**（`memory_hub_bridge.py`）：
  - outbox 新增 `kind='moment'`，`payload` 存完整请求体。重发时原样重发，`ai_id`、`chat_id`、`event_id` 都不变。
  - `_send_moment`：没配主人钥匙或主人钥匙被拒（401/403）时，小猫那轮改用共用钥匙、去掉 `sender_type` 照常送（Hub 认不出是她，但不丢）。
  - 原来群聊主人钥匙那段发 REST 的代码抽成 `_post_capture_rest`，两边共用；结果判定不变：2xx 算送到，3xx 不重发留着，408/429/5xx 用同一个 `event_id` 退避重发。
  - 新增 `_rest_base`。
- **开关**：`data/memory_hub.json` 里 `moments_enabled`（默认开）；Hub 没启用或 `capture_enabled` 关着时也不送。
- **没做的**：编辑 / 删除不同步（接口说明这一版也不要求）；没调可选的 MCP flush（不调最晚当晚处理）。
- **测试**：新增 `test_moments_hub_sync.py` 7 项：
  - 第一次运行不补送；
  - 小猫发的朋友圈、Lucien 的评论、小猫回 Lucien，三轮的每个字段和钥匙都核对；
  - 小猫评论狗蛋的朋友圈时 `ai_id=jasper`，空位跳过；
  - Hub 500 后原样重发；
  - 没主人钥匙改用共用钥匙；
  - 开关关掉不送；
  - 回看窗口不漏。
  - Hub 相关 226 项全过；本机真启动一次，`/ops/healthz` 正常。


## 155. 群聊页：Lucien 的回复刚写完时同一条显示两遍

- **小猫反映**（11-01）：Lucien 回复刚写完，页面上同一条显示两遍；重新打开就只剩一条（数据库里只有一条）。
- **原因**：保存回复时，`_save_msg` 先通过 WebSocket 广播 `chatroom_msg_created`，然后才在 SSE 里发 `connor_done`。两条线互不等待。Lucien（CLI 线路、safe_live）常常是整段一次到，SSE 里的 `connor_chunk` 这时可能还排在后面：
  1. 广播先到：页面上没有 `streaming-<id>` 行，也没有 `data-msg-id`，于是 `appendMessage` 显示一条；
  2. chunk 后到：`startStreamingBubble` 又新建一个流式行；
  3. `connor_done` 把流式行换成正式消息。

  结果就是两条。重新打开只从数据库读，所以只剩一条。
- **改动**（`static/chatroom.js`）：`startStreamingBubble` 开头先看这条 id 是不是已经显示了。显示了就不建流式气泡，并清掉「正在输入」；之后的 chunk 因为没有气泡直接忽略，`*_done` 也不会再追加。小克、Lucien、座位三条路都经过这个函数。
- **验证**：本机真实页面重放（调页面自己的 `handleSSE` 和 `crWs.onmessage`）：
  - 广播先到、chunk 后到：修之前 2 条，修之后 1 条；
  - 正常顺序（chunk 先到、广播后到）仍是 1 条，内容是最终文字；
  - 「正在输入」已清掉，控制台没有报错。
- `test_1105_fixes` 加了 1 项源码检查。


## 156. 回复失败的提示按原因说清楚（验收清单 A6）

- **原来**：线路出错一律写「回复连接中断，已自动停止生成。」（私聊、群聊的安全提示），或者把原始报错原样贴出来。
- **改动**：
  - `stream_safety.py` 新增 `describe_failure`：把线路错误归成几类，认不出的返回空串、不乱猜。按顺序判断：
    1. 没有可用的线路（没选模型 / 线路已停用 / 不存在）；
    2. 线路拒绝了请求（401、403、钥匙无效、余额不足）；
    3. 限流或额度用完（429、usage limit）；
    4. 模型拒绝回答（safety、content filter、refusal）；
    5. 线路那边出错了（5xx）；
    6. 太久没有回应（超时）；
    7. 连不上线路（connection、refused、DNS 等）。
  - 「拒绝回答」只认 refusal 和 refused to answer 这类说法，所以 `Connection refused` 不会被当成模型拒绝（有测试）。
  - `failure_detail_snippet`：附一段原始报错，最多 80 字，遮住 `sk-…` 和 `Bearer …` 后面的钥匙。
  - `consume_safe_stream` 在 transport 中断时，提示改成「回复失败：<原因>，已自动停止生成。」；认不出原因时仍写「连接中断」。
  - 群聊 `_chatroom_reply_failure_text`：transport / quality / length 这几个固定原因照旧。认得出的写「<名字> 本次回复失败：<原因>（<原始报错摘录>）」；认不出的只写摘录。超时那句的原文也走摘录，遮住钥匙。
- **测试**：
  - 新增 `test_reply_failure_reasons.py` 5 项：13 种真实报错的归类、认不出不乱猜、遮钥匙、群聊提示文字、流式中断提示。
  - `test_chat_regenerate`、`test_chatroom_stream_safety` 各更新 1 处期望文字，原来断言的正是这句笼统提示。
  - 相关 34 项全过。另有 6 个文件失败，改动前就失败（英语角、线路模式、触发消息、微信等，§24 之前已记）。


## 157. 监控页「开始外出监控」改成「让大家能看到：开 / 关」，并写清有没有接通

- **原来**：按钮叫「开始外出监控 / 停止外出监控」，容易误会；点完不知道服务器那边接上没有。按下时只是手机这边先算开着，下一次读服务器状态时会悄悄变回关。
- **改动**（`static/camera.html`）：
  - 按钮写当前状态：「👀 让大家能看到：开 / 关」。开着用强调色，不再用红色（红色看着像出错）。
  - 按钮下面加一行 `#phoneLinkStatus`：
    - 已关：大家看不到你这边；
    - 正在接通服务器…：点开后 10 秒内，服务器还没确认时显示，期间每 3 秒再查一次；
    - ✅ 已接通：大家想看你这边时，能拍一张看看；
    - ⚠️ 没接通服务器：确认网络 / VPN 连上后，先关再开一次。
  - 读服务器状态时（`fromServer`），服务器说开着就以它为准。服务器说没开、但小猫点过开时，按钮保持「开」、提示没接通，不再悄悄变回「关」。
  - 提示文字也改了：「请在手机 App 里打开这一页再开」「已关：大家看不到你这边了」「已打开，正在接通服务器…」。
- **验证**：本机真实页面调 `updatePhoneArmState`，重放 7 种状态，文字和状态都对，没有报错：
  - 初始；
  - 点开；
  - 服务器还没确认；
  - 10 秒后仍没接通；
  - 接通；
  - 点关；
  - 服务器也是关。
- 接口和手机 App 都没改。`test_1105_fixes` 加了 1 项源码检查；摄像头相关测试全过。


## 158. 修 §154（2717144）的 M1–M3

- **M1：首次回看会补送开启前的朋友圈**
  - 原因：开启时只记了 watermark，第二次扫描用 `watermark − 600` 当下界，起点被往前挪了 10 分钟。
  - 修法（`moments_hub_sync.py`）：
    - 另存一个永不后退的 `start`（开启那一刻）；查询下界取 `max(start, watermark − 600)`。
    - 逐条再判 `created_at <= start` 跳过，所以和开启同一时刻的那条也算开启前。
    - 已经有 watermark、没有 start 的旧状态：start 取原来的 watermark（目前还没部署过，只防万一）。
- **M2：关掉同步后，已入队的朋友圈仍会发送**
  - 修法（`memory_hub_bridge.py`）：
    - 新增 `moments_paused()`。
    - `drain_outbox` 每取一条都重新读开关。关着时，`kind='moment'` 的记录原地暂停：不发、不改状态，也不算同一身份的队头，同一位 AI 的聊天记录照常发。
    - 重新打开后，按原顺序接着发。
- **M3：入队和登记去重不在同一事务**
  - 修法：
    - `enqueue_moment` 改成 `insert_moment(conn, …)`，由调用方传连接。
    - `sync_new` 在同一个连接、同一个事务里写 outbox 和 `moment_hub_synced`，要么都成、要么都不成。同一件事的身份只定一次。
    - 去重登记从 `INSERT OR IGNORE` 改成 `INSERT`：真重复时报错，整个事务回滚。
- **测试**（`test_moments_hub_sync.py` 新增 3 项，按狐狸的重放写）：
  - **M1**：1000 时开启，900 和 1000 那两条都不送，1001 的送一次；再扫 700 秒也不补送。
  - **M2**：排队后关开关，同一位 AI 的群聊记录照常发出，朋友圈仍是 pending；重新打开后发出。
  - **M3**：用 SQLite 触发器让登记去重失败，outbox 同时回滚为 0 条；改身份映射后再扫两次，只有一条，身份是新的。
  - 朋友圈 10 项、Hub 相关 229 项全过。


## 159. 修 §157 的 R1：关掉后旧状态把「想开着」又写回来

- **狐狸的反例**：小猫点关 → 关之前发出的查询晚回来，返回 `armed=true`，把 `_phoneWantOn` 改回 true → 新查询返回 false，按钮仍显示开 → 改镜头设置时调了 `bridge.arm()`，又开了。
- **改动**（`static/camera.html`）：
  - 把两件事分开记：
    - `_phoneIntent`：小猫在本页点过的开 / 关。`null` 表示本页还没点过，跟着服务器。只有她点按钮才会改。
    - `_phoneServerArmed`：服务器最近一次确认的状态。
  - `_phoneOpGen`：每点一次加一。`syncCamModalState` 发查询前先记下 `phoneGen`，结果回来交给 `applyPhoneServerState(status, phoneGen)`。编号对不上（点击之前发出的查询）直接丢掉。
  - 按钮显示：她点过就按她点的，没点过就按服务器。点击时把 `_phoneServerArmed` 清空，点之前的确认不算数。
  - 她点了关、服务器（新的回应）还说开着：按钮保持关，下面一行写「正在断开…」。
  - `phoneCameraSettingChanged` 只在「显示为开」且她没点过关时才重新 `arm`。
  - 原来的 `updatePhoneArmState` 拆成 `setPhoneArmIntent`（点击）、`applyPhoneServerState`（服务器回应）、`renderPhoneArmState`（画按钮）。
- **验证**：本机真实页面加桥接桩重放 12 种情况，全对，没有报错：
  - 服务器开着时打开页面，显示开、已接通；
  - 狐狸的反例：点关 → 旧的 `armed=true` 被丢掉 → 新的 false 仍是关 → 改镜头设置**没有**调 `arm`；
  - 点关后，新的回应说开着：按钮仍是关，写「正在断开…」；
  - 点开后旧的 false 被丢掉；新的 false 在 10 秒内是「正在接通」，过了 10 秒是「没接通」，true 是「已接通」；
  - 开着时改镜头设置，照常重新 `arm`。
- `test_1105_fixes` 新增 1 项源码检查，守住编号核对和关闭意愿；摄像头相关 78 项全过。

另：施工方案 docs/施工方案-bot出门.md 按狐狸的三条建议补齐，还没写代码：
- 服务钥匙只开 `/api/outing/*`，后端再拦一次；
- 借用有总超时、每只和全家的并发上限，额度先认领，日志带 `request_id`、错误脱敏；
- `/outreach` 有持久的幂等编号，收件人只限小猫私聊和已批准的朋友群，额度认领持久化。


## 160. 修 §159 的 R2：摄像头页首次加载读不到状态

- **原因**：§159 把 `_phoneOpGen` 等手机状态变量写在脚本中段（原第 533 行）。`init()` 先调 `syncCamModalState()`，它一开头就读 `_phoneOpGen`，这时变量还没初始化，抛出 TDZ 异常；异常被捕获后显示「获取状态失败」，第一次 `/api/cam/status` 根本没发出。
- **改动**（`static/camera.html`）：`_phoneIntent`、`_phoneServerArmed`、`_phoneOpGen`、`_phoneArmedAt`、`PHONE_LINK_GRACE_MS` 挪到 `let _phoneArmed = false;` 后面，和其他页面状态变量放在一起，在 `init()` 之前。逻辑一行没改。
- **验证**：
  - `test_patrol_control.py::test_camera_page_first_load_reads_status_in_real_order` 通过，`test_patrol_control` 加 `test_1105_fixes` 共 37 项全过；
  - 本机真实浏览器打开 /camera：页面一加载就发出 `/api/cam/status`，没有「获取状态失败」；
  - §159 的 12 种重放仍全对。
- **教训**：上一轮只在页面加载完以后手动调函数重放，没看加载过程本身。以后改页面脚本都先打开一次页面，看首次加载有没有报错。

另：施工方案 `/outreach` 按狐狸复审补充：bot **发送前**先持久认领编号，认领成功才发；认领后没发出的，宁可漏一条也不重复。


## 161. bot 出门第 1 步（家里这边）：回家借工具的入口 + 门锁只给服务钥匙开这一扇门

方案：docs/施工方案-bot出门.md（狐狸已认可方向）。11-05 小猫定了：大群里她开口才借；三只一起做；出门记录页显示朋友群。

- **新增 `outing_tools.py`**：`POST /api/outing/tool`，body 为 `{tool, args, request_id, chat_type, owner_asked}`。
  - **钥匙**：每个座位一把，放在 `/etc/aionshome.env` 的 `AIONSHOME_OUTING_KEY_<座位>`（如 `_AION` / `_CONNOR` / `_AI3`），少于 24 位当没配。逐个用 sha256 + `hmac.compare_digest` 比对，不提前退出。
  - **工具**：`search`（Tavily 搜索）、`read`（Tavily 读网页，只认 http / https）、`taobao`（淘宝联盟，取 5 件：标题｜价格｜店｜链接）。结果最多 3000 字。
  - **检查顺序**：
    1. 钥匙；
    2. 格式（`request_id` 8～120 位、`chat_type` 三选一）；
    3. 总开关、这位的开关、这位能借的工具；
    4. 大群（`public_group`）要 `owner_asked=true`；
    5. 并发名额（每只 2、全家 4，满了直接 429，不排队）；
    6. 认领额度：SQLite 的 `BEGIN IMMEDIATE` 里查重 `request_id`（重复回 409）、数这一小时次数（超了回 429）、登记为 running；
    7. 干活（总超时 30 秒）；
    8. 收尾：记成 ok / timeout / failed / refused。
  - **错误**：只回分类，用 §156 的 `describe_failure`；工具没配好时回「家里这个工具还没配好」。日志和数据库只记座位、工具、`chat_type`、时间、状态、分类，不记搜索词和结果。
  - **小猫用的接口**：`GET` / `PUT /api/outing-settings`（总开关、每只的开关、工具、每小时次数，外加最近 30 次借用记录）。页面还没做，放到第 2 步和开关总览一起做。
- **`cf_access.py` 门锁**（两个方向都拦）：
  - 新增 `verify_claims`，返回凭证内容；`verify` 改成包它。修掉了改写时漏下的一处 `return False`：那里应返回 None，否则会把格式不对的凭证当成通过。补了专门的测试。
  - `/api/outing/*` 只认借工具应用（`AIONSHOME_CF_ACCESS_OUTING_AUD`）签发的服务钥匙凭证：有 `common_name`、没有 `email`。小猫的登录也进不来；没配这个 AUD 时一律拒绝。
  - 其他路径只认整站应用的凭证，服务钥匙一律不认（哪怕是整站应用签的）。
  - 路径里有 `..` 段的，经 Cloudflare 来时一律拒绝。
  - `/api/outing-settings` 不算借工具的门，只给小猫的登录。
- **测试**：
  - `test_cf_access.py` 新增 11 项：9 种「哪把钥匙进哪扇门」、没配 AUD 时关门、payload 不是对象时拒绝。原有 32 项照过。
  - 新增 `test_outing_tools.py` 16 项：钥匙对错、太短的钥匙、同一编号只办一次、额度先认领、大群要她开口、开关和工具名单、失败只回分类且不漏钥匙、超时释放名额、两种并发上限、坏请求、三样工具的结果格式（含拒绝 `file://`）。
  - 本机真启动：钥匙不对 401；钥匙对但没配 Tavily 时回「家里这个工具还没配好」；设置接口正常。
- **还没做**：
  - bot 那边（三只一起写，见下一节）；
  - 设置页面；
  - Cloudflare 上建借工具应用和服务钥匙（要小猫点）。


## 162. bot 出门第 1 步（三只 bot）：回家借工具

三个 bot 仓库各推了一个分支 `claude/frontend-memory-integration-ke0lpe`，**没有动 main**。Render / Fly 从 main 自动部署，所以正在跑的 bot 不受影响。

| 仓库 | 提交 |
|---|---|
| cirel94june/cloudy-telegram-bot | 84eb896 |
| cirel94june/Lucien-Telegram-bot | 1b64efd |
| cirel94june/Jasper-telegrambot | 7a41121 |

三只改动完全相同：
- **新增 `home_tools.py`**（三份一字不差）：
  - 识别 `[回家:搜索 / 读网页 / 淘宝:…]`，最多 2 个，去重；
  - `borrow` 带 bot 钥匙和 Cloudflare 服务钥匙（`CF-Access-Client-Id` / `CF-Access-Client-Secret`）请求 `POST {AIONSHOME_URL}/api/outing/tool`；超时 40 秒，`allow_redirects=False`，每次一个新的 `request_id`；
  - 任何失败都变成一句「（没借成：原因）」，不抛异常，不带家里的内部报错；
  - 四个环境变量都填了才开。没开时提示词不提，回复里的 `[回家:…]` 只删掉。
- **`bot.py`**（每只约 20 行）：
  - 提示词加 `home_tools.prompt_hint`；
  - `process_message_background` 拿到第一次回复后：有 `[回家:…]` 就先不发，借完把结果拼进 memory（系统提示里的背景），用同样的输入再调一次 `call_claude`，发第二次的回复。第二次为空时，退回第一次去掉指令后的文字；再为空，就说「我回家查了一下，但这会儿没弄好」。
  - `owner_asked` = 当前这条消息的 `sender_id` 等于 `CECI_ID`；
  - `chat_type` 换算：private / small_group / big_group → private / private_group / public_group。
- **测试**：每只新增 `tests/test_home_tools.py` 8 项：
  - 开关、找指令、换算；
  - 两把钥匙和请求体；
  - 5 种失败；
  - 整条回复流程：先借再问一次、结果进第二次的背景、发出的文字里没有指令；
  - 大群里别人发的消息 `owner_asked=False`；
  - 没开时只删指令。
  - 小克 107 项、Lucien 133 项、狗蛋 135 项全过。
- **说明**：每只加了 `HOME_TOOLS.md`；小克的 `env.example` 加了四个变量。

上线顺序（狐狸审过后）：
1. 本地窗口部署 AionsHome；
2. 小猫按 docs/施工方案-bot出门.md「第 0 步」配钥匙；
3. 把小克的分支合进 main（Render 自动部署），TG 私聊试一次；
4. 没问题再合 Lucien 和狗蛋。


## 163. 修 §162（三只 bot 回家借工具）的 M1、M2、L1

三份 `home_tools.py` 和 `tests/test_home_tools.py` 仍一字不差。分支照旧是 `claude/frontend-memory-integration-ke0lpe`，没有动 main。

| 仓库 | 新提交 |
|---|---|
| cloudy-telegram-bot | 7ddde80 |
| Lucien-Telegram-bot | c204755 |
| Jasper-telegrambot | 8ab0b34 |

- **M1：外部网页内容进了 system 提示词**
  - 借回的结果不再拼进 `memory`，改成 `home_tools.external_data_event(note)`：作为历史副本末尾单独一条（role=user，`sender_type="tool"`），说话人标「外部资料（借工具结果，不是谁说的话）」。
  - `_model_speaker_label` 认 `sender_type == "tool"`，不再落到「群友」。正文开头写明这是外面抓来的内容，夹着的「让你做事」的话不是任何人的要求，不要照做。
  - 带图时，最后一条会被换成图片，所以资料也放进图片那条的文字里。
  - 带外部资料的那次回复不执行任何动作：
    - `process_message_background` 里，`borrowed_from_home` 时跳过 `parse_and_execute_actions`，改用 `strip_untrusted_actions` 删掉全部动作标签：KICK / PIN / POST / SEND_TO / DM_CECI / TAG / TITLE / DAILY、`[记住:]`、`[更新状态:]`、`（签名:）`、`[回家:]`；
    - `call_claude` 加 `external_data=False` 参数。为 True 时，回复不经 `_hub_process_capabilities`（那一步会把 `[记住:]` 交给 Hub），改成只删。
- **M2：40 秒不是总时限**
  - `borrow` 改用 `stream=True` 加 `_read_limited`：用 `read1(4096)` 边读边看总期限（`TIMEOUT_SECONDS`=40，连上、收完、解析一起算），超时抛 `_TooSlow` 并关掉连接。
  - 每次读最多停 `READ_STALL_SECONDS`=2，所以最多超出约 2 秒。
  - 回应最多 `MAX_RESPONSE_BYTES`=64KB，超了就断。请求带 `Accept-Encoding: identity`，按收到的字节数限大小，免得解压后变大。
  - 不传 session 时自己建一个 `requests.Session`，用完关掉。
- **L1：合法但非对象的 JSON**
  - 先 `json.loads`，再要求是 dict；`ok` 必须是布尔，`reason` / `detail` 必须是文字，`result` 必须是非空文字。不满足的一律走「没借成：家里回的东西看不懂」或「家里查了，但没有结果」。
- **测试**（`test_home_tools.py` 从 8 项变成 13 项）：
  - 本机滴水服务器（每 10ms 一个字节）：期限设 0.3 秒时 1 秒内返回「太久没回」；
  - 超大回应被截断；
  - null、[]、42、`{"ok":"yes"}`、非 JSON、403 加数组、reason 不是文字；
  - 外部资料是历史末尾那一条、memory 没变、第二次调用带 `external_data=True`、`parse_and_execute_actions` 没被调用、发出去的文字没有 KICK 和 [记住:]；
  - `build_model_messages` 里标成外部资料、不是群友；
  - `call_claude(external_data=True)` 不调 `_hub_process_capabilities`，返回里 [记住:] 已删；不带这个参数时照常调。
  - 带 `-W error::ResourceWarning` 跑，没有连接泄漏。
  - 全套：小克 112 项、Lucien 138 项、狗蛋 140 项全过。


## 164. 三只 bot 借工具：修 §163 复审的三处超时问题（M2 余项和两处新问题）

三份 `home_tools.py`、`tests/test_home_tools.py` 仍然一字不差。分支 `claude/frontend-memory-integration-ke0lpe`：cloudy **893ea9e**、Lucien **3da81bb**、Jasper **2e5da3a**。main 没动。

- **M2 余项：总期限没覆盖接收响应头**。借工具这一次请求不再用 requests，改成自己发（`_post`）：
  - `socket.create_connection` 连接超时取 `min(5 秒, 剩余总期限)`；https 用 `ssl` 加 certifi 证书库（和 requests 同一份，没有就用系统的）；握手、发送前都按剩余总期限设超时。
  - 读响应走 `_DeadlineReader`：每次 `recv_into` 之前都先看总期限，过了就抛 `_TooSlow`。响应头一行一行慢慢滴，也会在总期限到点时断开。`http.client.HTTPResponse` 用它解析状态行、响应头和正文（Content-Length 和 chunked 都支持）。
  - 不跟随跳转；`Connection: close`；函数结束时一定关掉 socket。正文超过 `MAX_RESPONSE_BYTES` 抛 `_TooLarge`。
- **新问题：等响应头超过 2 秒就失败**：`_DeadlineReader.stall` 在收完响应头之后才设成 `READ_STALL_SECONDS`。等响应头时用剩余的全部总期限（家里要等工具办完才回）；读正文时，每次等待取 `min(停顿上限, 剩余总期限)`。
- **新问题：底层读取异常绕过兜底**：
  - `_post` 里除了 `_TooSlow` / `_TooLarge`，其余异常（连不上、TLS 出错、`http.client` 的各种异常、对方中途断开）一律转成 `_Unreachable`。
  - 声明了长度却没收齐就断开时，`response.read(amt)` 不会报错，所以读完后看 `response.length` 是否还有剩余，有就算 `_Unreachable`。
  - `borrow` 最外层再兜一层 `except Exception` →「家里没连上」，不会跳出借工具流程。
- **接口**：`borrow` / `borrow_all` 的测试注入参数从 `session` 改成 `transport`（签名为 `(url, payload, headers, deadline) -> (status, body)`）。bot.py 本来没传它，不用改。bot 的依赖不变（certifi 随 requests 装）。
- **测试**：
  - 原来用假会话的两项改成假的 transport。新加 3 项，在本机回环 HTTP 上真实发请求：
    - 响应头每 0.02 秒滴一行，总期限 0.08 秒，0.3 秒内退出，结果是「太久没回」；
    - 家里 0.2 秒后才正常回，总期限 1 秒、停顿上限 0.05 秒，照常拿到结果；
    - 正文给了一个字节就停住，结果是「太久没回」，0.8 秒内退出；声明 100 字节只给 5 个就断开，结果是「没连上」。
  - 原来的滴数据、超大回应用例照旧通过。小克 115、Lucien 141、狗蛋 143 项全过。
  - https 在服务器上实测：google.com 正常读到 405；github.com 首页太大，被截断（`_TooLarge`）；家里 `/api/outing/tool` 不带钥匙时回 302（Cloudflare 要登录），bot 会说「家里这次没办成」。本机连外网的 TLS 握手本身会超时，所以 https 只在服务器上测了。

## 165. 朋友圈点开大图黑屏

- 小猫 11-05 报：朋友圈发了图，点开想看大图就黑屏。
- 原因：`static/moments.html` 的 `openMomentImage` 是 `window.open(url, '_blank')`。手机 App 的 WebView 里会开一个空白新窗口，没有返回键，看起来就是黑屏。
- 改法（只改这一页）：
  - `openMomentImage` 改成在本页盖一层看图（`.moment-viewer`，fixed、z-index 10000）：图片按屏幕缩放，右上角 ×、底部「保存图片」「关闭」；点空白处或图片本身、按 Esc 都能关。
  - 用 DOM 赋值建元素（`img.src = url`、`textContent`），不把地址拼进 innerHTML。
  - 「保存图片」照聊天页的做法：有 `AionImageSaver`（本页、parent、top）就转 base64 交给 App 存相册，否则浏览器下载；失败只弹「图片保存失败」，不再退回开新窗口。
- 测试 `test_moments_image_viewer.py` 3 项：看图和保存都不开新窗口、不用 innerHTML、能用 Esc 关。本机手机尺寸预览实测：盖层出现、图片显示、「关闭」和 Esc 都能关。

## 166. 三只 bot 借工具：连接阶段也算进总期限（§164 复审 Medium）

- 狐狸复审：`socket.create_connection` 先同步 `getaddrinfo`，不受 timeout 限制；多个地址时每个都给满一份超时，不扣已用时间。
- 改法（三份 `home_tools.py` 完全一致）：
  - `_resolve`：查地址放到后台线程，最多等到总期限，超了算「太久没回」。同时挂着的查询最多 `MAX_PENDING_LOOKUPS = 4` 个（非阻塞 BoundedSemaphore，线程结束时归还）；满了新请求直接算「没连上」，不再开线程。
  - `_connect`：逐个地址连，每个之前重新算剩余时间，超时取 `min(CONNECT_TIMEOUT_SECONDS, 剩余)`；用完预算算「太久没回」，全部被拒且没超时算「没连上」。
  - `_post` 改用 `_connect`，其余不变。
- 新测试 4 项（三只都加）：查地址卡 0.35 秒、总期限 0.08 秒，0.25 秒内退出；三个地址各失败 0.06 秒，最多试两个，后面的只拿到剩下的时间，0.2 秒内退出；卡住的查询满 4 个后第 5 个直接「没连上」且不再开线程；预算内全部被拒是「没连上」。
- 小克 119、Lucien 145、狗蛋 147 项全过。服务器上实测 https：google.com 照常回 405；不存在的域名 0.05 秒返回「没连上」。
- 提交：cloudy `977d4d2`、Lucien `cae72c4`、Jasper `10697c2`。

## 167. 修 §165 复审 Low：保存图片先确认真拿到了图片

- 狐狸：`saveMomentImage` 直接把 fetch 结果转 blob，404 text/plain 也会转 base64 交给 `AionImageSaver.save`，或浏览器下载成坏图。
- 改法：先查 `res.ok`，再查 `content-type`（没有就看 blob.type）以 `image/` 开头且非空；不满足就抛错，落到「图片保存失败」提示，原生保存和浏览器下载都不走。
- 测试 +1（共 4 项）。本机预览实测（假的 AionImageSaver）：真图片存了 1 次；404 地址、HTML 页面各提示一次「图片保存失败」，没有调用保存。
- 狐狸说 bot 那边看的还是旧提交：§166 的三个新提交 cloudy `977d4d2`、Lucien `cae72c4`、Jasper `10697c2` 已在 GitHub 分支上（`git ls-remote` 确认）。

## 168. 淘宝联盟：词太细搜不到时不再算失败，自动放宽

- 小猫 11-05：27939 批下来后，Lucien（connor）逛淘宝失败，记录里的错误是 `淘宝联盟接口报错 15：Remote service error；无结果`，当时的搜索词是「仓鼠亚克力观景台 磁吸 可拆洗」。新版接口用报错来表示「这个词没结果」。
- 改法：
  - `taobao_union.call`：`UnionError` 加 `no_result`（错误文字里有「无结果」，或 sub_code 含 no-result / no_result）。
  - `search`：新旧接口任一处报 `no_result` 都返回空列表；其他错误照旧抛出（code 15 + 别的 sub_msg 仍算失败）。
  - 新增 `search_broadening`：没结果、也没有被跳过的商品、词数 ≥ 2 时去掉最后一个词再搜，最多放宽 2 次（一次逛最多 3 次请求）。返回值加 `keyword_used`。
  - `taobao_shopping.search_and_record`（联盟分支）和 `outing_tools`（bot 借淘宝）都改用它。记录里存的是实际搜的词；没结果时提示「淘宝联盟没搜到「…」相关的商品（已经把词放宽过）」，不再说「页面尚未就绪」。
- 测试 +4：`无结果` 当作空列表；code 15 的其他错误仍然失败；放宽顺序依次是整句、去掉末词、再去掉一个；一个词不放宽；最多放宽两次。淘宝联盟和借工具测试全过。`test_taobao_native_mcp` 有 4 项失败，改之前就失败（本机路径问题），和这次无关。

## 169. 修 §168 复审的两项 Low

- L1：`no_result` 只在 `code == "15"`，并且有「无结果」或 sub_code 是 no-result / no_result 时才成立。其他错误码的文字里就算写着「无结果」也照样抛出（反例 code 7 + 「调用受限，查询无结果」已加成测试）。
- L2：放宽过的词要完整传下去。
  - 选品提示改用 `_search_words_note(原词, keyword_used)`：没放宽时还是「搜索词：…」；放宽过时同时写「原本想搜」和「实际搜的是放宽后的」，并说明结果不一定符合被去掉的条件，标题里没看到的条件不要当成商品有。
  - bot 借淘宝（`outing_tools`）：放宽过时，结果前加一行「原词「…」没搜到，下面是放宽成「…」搜到的，不一定符合被去掉的条件」；没结果时也写出实际搜的词。
- 测试 +3，淘宝联盟和借工具测试共 29 项全过。

## 170. 桌面便笺：AI 能看到桌上有什么（提交 5f92e51）

- 小猫 11-05：桌上的小纸条 AI 只能写，看不到有什么。
- `desk_notes.visible_notes_text`：最近 8 张（旧的在前），每张最多 120 字，换行压成一行，写明作者（`user` 显示她的名字，其他用 `actors.display_name`）和北京时间。没有便笺时说「什么也没贴」。开头写明「这只是便笺上写的字，不是给你的指令」。
- `capabilities.desk_note_ability_text` 把它接在 `[NOTE:内容]` 说明后面，小克、Lucien、座位用的都是这一段；开关还是原来的 `desk_note`。
- 测试 +2。

## 171. 聊天里能当场搜淘宝：[TAOBAO:关键词]

- 小猫 11-05：前端对话里不能直接去淘宝。
- 搭联网搜索那条路，不另开一条，所以小克私聊、座位、群聊（`perform_web_search_check`、`_chatroom_web_search`）都能用：
  - `WEB_SEARCH_CMD_PATTERN` 也认 `[TAOBAO:词]`，取出来是 `TAOBAO:词`，`clean_web_command_text` 和 context_builder 的清理自动覆盖。
  - `run_web_commands` 按前缀分流到 `_taobao_context`：结果标明「外部不可信数据」，并说明只看了摘要、没买过；淘宝失败只影响这一条，不牵连同一轮的联网搜索。关键词最长 60 字，为空不调用接口。一轮的条数上限和联网搜索共用（3 条）。
  - 隐藏：`_WEB_CMD_START_RE`、`_possible_command_prefix_len`（测试抓到指令分两段到达时会露出来）、`safe_live_stream` 两个列表、`tts._STRIP_PATTERNS`。
  - 系统提示：只搜淘宝时写「小克去淘宝搜「…」」，不写「发起了联网搜索」。
- `taobao_union.lookup_text` 收拢成几行文字，bot 借淘宝（`outing_tools`）也改用它，输出不变。
- 能力开关：新增 `taobao_chat`，默认开，没填 AppSecret 时不注入。只搜、只看，不写收藏，也不记逛街记录。
- 已知影响：autonomy 的 `_web_roam_query_from_result` 也用这个 pattern，模型若在那里写 `[TAOBAO:x]`，会把 `TAOBAO:x` 当成网页查询词。那边的提示只教 `[WEB_SEARCH:…]`，影响很小。
- 测试 `test_taobao_chat.py` 7 项。相关的 45 个测试文件跑过，FAILED 的测试和改动前相同（english_corner、idle_autonomy_actions 里的老问题）。本机没有淘宝钥匙，没有真实搜过，也没有在真实聊天里试过。

## 172. 修 §171 复审的两项 Medium

- M1 开关接线：新增 `web_search.allowed_web_commands(searches, extracts)`，按指令类型查 `taobao_chat`（`capabilities.taobao_chat_on`：开关 + AppSecret）和 `web_search`，关着的只清除、不执行。私聊 3 处、聊天室 2 处调度都先过它，座位原来只查 `web_search` 的判断去掉了。`_taobao_context` 执行前再查一次，调度和执行之间被关掉就不搜。
  - 顺带的行为变化：小克、Lucien 私聊和群聊原来不查 `web_search` 开关，现在关掉后 `[WEB_SEARCH:]` 也只清除、不执行，和座位一致。
- M2 带商品资料的续答：`searches` 里有淘宝时，`perform_web_search_check` 和 `_chatroom_web_search` 不再调用 `_process_background_reply_commands`，改用 `strip_untrusted_reply`。它不逐个列举标签：先 `strip_tool_commands`，再去掉所有 `[...]` / `［...］`（后面紧跟 `(` 的 markdown 链接保留）和 `【小组件…】` `【横幅…】`。没进执行入口的标签就算漏清，也只是显示出来，不会执行。座位路径同样先清一遍。
- 测试 `test_taobao_chat.py` +9（共 16 项）：四种开关组合；没钥匙；调度后被关掉；调度点数量；20 种动作标签只清除；markdown 链接和普通文字保留；聊天室 `[巡逻:停]` 反例不进执行入口；普通联网搜索仍进执行入口；私聊的守卫在执行入口之前（源码断言）。
- `test_seat_abilities._enable` 同时替换 `capabilities.is_capability_enabled`，因为开关改到那里查了；断言没改。
- 相关测试文件跑过，FAILED 和改动前相同（english_corner、idle_autonomy_actions 的老问题）。全量测试没跑完。没有真实调用过淘宝、模型或设备。
- 没有改、要提醒的：普通联网搜索、读网页（§171 之前就有）的续答同样读外部内容，也同样进执行入口，和 M2 是同一类问题，网页比商品标题更不可控。这次按 M2 的范围只改了淘宝，已记进待办。

## 173. 修 §172 复审的两项 Medium 和一项 Low

- M1：`_taobao_context` 在 `settings()` 之后、紧贴 `lookup_text` 前再查一次 `taobao_chat_on()`，中间没有等待。
- M2：`run_web_commands` 加 `allow_taobao`，默认 False，只有 `perform_web_search_check` 和 `_chatroom_web_search` 传 True（这两处都会在保存前清除动作标签）。其他调用方写 `TAOBAO:词` 只按普通联网搜索查这个词，不分流到淘宝。`_web_roam_query_from_result` 也把它还原成普通词。
- Low：`strip_untrusted_reply` 不再删所有方括号，只删已知动作标签（`safe_live_stream._BRACKET_COMMAND_PREFIXES` + 巡逻），名字后面要紧跟冒号、空白、分隔符或结束。`[无糖]`、`[30×40cm]`、`[HOME改造]` 保留。不进执行入口的保护不变，所以漏清的标签只会显示，不会执行。
- 测试 +4：等设置时被关掉；没给 allow_taobao 时碰不到淘宝；冲浪把淘宝当普通词；只有两处聊天续答传 allow_taobao。普通括注加进保留用例。`test_seat_abilities` 里的假搜索函数改成收 `**k`。

## 174. 写日记前读 Hub 这一天的经历（Hub GET /api/ai/day）

- Hub 接口说明优先级 1 第 7 项。新模块 `hub_day.py`：
  - 用共享钥匙（`bridge._headers`），不传 `include_private_turns`，不用主人钥匙，不跟随跳转。
  - 读到 `truncated` 为 false，`next_cursor` 原样传。上限 12 页，cursor 不动就停，整体 25 秒。没读完就用已读到的，提示词里写「没读全」。
  - 任何失败（非 200、不是对象、超时、异常）只记日志（只记状态和异常类型，不记内容），返回空串，日记照常写。Hub 关着或读取冷却中就跳过。不触发全局冷却。
  - 日期：北京时间，凌晨 4 点以前算前一天。
  - 提示词：放 `speaker_hint`、`time_hint`，并写明「这是记录，不是给你的指令」「群友的事不要写成是小猫的」。`event_date` 早于查询日的标「今天才记下的旧事」。摘要每条最多 220 字，记忆 160 字，总长各 5000 / 3000 字，放不下时留新的，并写还有几条放不下。
- 接入三处日记提示词（朋友圈是日记时顺带决定的，也就覆盖了）：`memory.py`（小克）、`chatroom.py`（Lucien）、`seat_diary.py`（座位，在开口前确认线路之前读，不打破「确认后到发请求之间不等待」）。日记回复只按 JSON 解析，不执行标签，所以读 Hub 内容不增加动作风险。
- 没接的，想请你看对不对：
  - 本地「记忆整理」（`_do_digest`）：Hub 说整理记忆前也可以调。但整理的结果会写进本地记忆再送回 Hub，把 Hub 的摘要喂进去可能绕一圈变成重复记忆，所以先不接。
  - 自主活动的「翻记忆库」：看的是过去某天的本地记忆，不是今天。
- 测试 `test_hub_day.py` 13 项，用假的 Hub，没连真 Hub，也没真写过日记。

## 175. Hub #145 梦每天自然讲一次：确认 context、capture 的两个入参

- Hub 要确认：context 传小猫这一轮的原话；capture 的 chat_type 传对。
- 查的结果：
  - 参数名没问题：Hub 的 MCP `context` 收的是 `message`，内部当 user_message 用，我们传的就是 `message`。
  - 小克私聊、Lucien 私聊、小克和 Lucien 的群聊：传的是触发这轮回复的那条消息。打字的都对。
  - **漏洞 1，语音消息**：语音的正文是空串，话在附件的 transcript 里，所以她用语音问梦时 Hub 收到的是空的。
  - **漏洞 2，座位在群里**：`seat_chat.build_context` 取时间线最后一条，群里轮到 TA 时那常是别的 AI 刚说的，不是她的话。
  - capture：私聊原来不传 chat_type，Hub 默认按 private 算，所以「讲过梦」本来就算得上，不是 bug。群聊传 private_group。bot 看起来也都传了（只看了 cloudy 的 `bot.py`，没改）。
- 改法：
  - `memory_hub_bridge.spoken_text(content, attachments)`：把语音、视频通话的 transcript 和正文合起来。私聊 2 处、聊天室 5 处 `query_text` 都用它（这个 query_text 也用于本地记忆召回，语音消息现在也能召回了，算顺带改善）。
  - 座位取时间线里最后一条 `sender == "user"` 的消息；没有才用最后一条。这个 `query_text` 只给 Hub 用。
  - capture 总是写明 chat_type（不是 bug，Hub 要求传对，写明更稳）。`test_private_capture_does_not_send_chat_type` 改成断言 private。
- 没改、已告诉 Hub 窗口要确认的：朋友圈读记忆时用 `context_block(who, query, "moments", "private_group")`，朋友圈评论也用 private_group capture。按 Hub 现在的规则，这些也算「自家小群」，可能在朋友圈里被提醒讲梦、讲了也算今天讲过。
- 测试 `test_hub_dream_inputs.py` 5 项。没连真 Hub。

## 176. 修 §174：真 Hub 的群类型名

- `60f0d03` 部署后在服务器上连真 Hub 试（只看条数）：三位都读通了，一页读完，提示词约 2000 字。
- 但真 Hub 的 `chats[].chat_type` 是 `big_group` / `small_group`，不是接口说明推断的 private_group / public_group，所以提示词里都显示「别处」，大群小群分不出来。`speaker_hint` 和「大群半公开」那句提示还在，影响是模型分不清这条是大群的。
- 改 `CHAT_LABELS`，两种写法都认。测试 +1。

## 177. 修 §175 复审的两项 Medium

- M1 系统唤醒误判问梦：`seat_chat.build_context` 不再从合并时间线猜，改成显式收 `query_text`。`seat_chat.hub_query_for(actor, 房间这一轮的 msgs, system_wake=, her_turn=)` 决定传什么：
  - 系统叫醒（`extra_prompt` 非空：闹铃、监控、主动陪伴）传空串；
  - 她说话：`memory_hub_bridge.pending_user_turn` 取这位 AI 还没回应的她那句（语音带转写），已回应过就是空串；
  - AI 之间自主接话（`capture_user_turn=False`）：用房间最后一句，和 §175 之前一样，用来想相关的事。这句如果带「梦」字 Hub 也会当作在问，但她没在问，影响是多带一次真梦，没有动。
  - `paired_user_message` 改为基于 `pending_user_turn`，行为不变。
  - Hub 的 `context` 允许空 message，系统叫醒仍能拿到记忆块（相关度可能差一点）。
- M2 小克重新生成：`_process_voice_attachments_in_history` 之前用 `spoken_text` 固定 `hub_query_text`，`context_block` 改用它。
- 没改、要提醒的：同一入口的 `schedule_capture("aion", lounge_request_text, ...)` 也用转换前的文字，语音消息重新生成时是空串，这一轮不记入 Hub。发送入口不受影响。这是老问题，不在这次范围。
- 测试 `test_hub_dream_inputs.py` 改写：群里听她的、语音、系统叫醒不算问梦、已回应不算、AI 接话、`build_context` 不猜、入口传参和重新生成顺序（后两项是源码断言）。

- §175–§177 狐狸通过，11-06 已部署 fc5aa41。

## 178. Hub：background=true；斗地主单独 chat_id（**等 Hub 上线才能部署**）

- Hub 定了：
  - 斗地主改 `chat_id="doudizhu"`，Hub 已按朋友圈的方式排除；
  - 闹铃、主动关心、监控叫醒、自主活动群里接话、环境音发言，`context` 传 `background=true`（不带梦、不占提醒次数，以后审批提醒也用它）；
  - 闹铃这类不带梦，不用补写回。真实聊天不传。
- **部署顺序**：Hub 这个参数还没上线，现在传可能报错。报错不只是这一轮：`_incremental_block` 失败会触发全局读取冷却，真实聊天也读不到。**小猫告诉我 Hub 已上线前不部署。** 斗地主的 chat_id 可以先上，但和这批一起。
- 改法：
  - `context_block(..., background=False)`，只在 True 时才带这个参数；`_incremental_block` 和 `handoff_block` 都带（两个都是 context 调用）。
  - `build_memory_blocks`、`build_aion_group_context`、`build_connor_group_context`、`build_connor_1v1_context` 加 `memory_hub_background`；两个群聊的加 `memory_hub_chat_id` 覆盖房间 id。
  - 标 background：`schedule.py` 4 处、`camera.py` 2 处（Lucien）；`_reply_aion`、`_reply_connor` 在 `ambient_context` 或 `capture_user_turn=False` 时；座位在 `extra_prompt` 或 `capture_user_turn=False` 时。
  - 斗地主两处传 `memory_hub_chat_id="doudizhu"`，不标 background（Hub 靠 chat_id 排除）。
- 没动的：Hub 说系统唤醒传空查询。座位已经是空的（§177）。Lucien 的闹铃、监控仍传提醒内容，这样能想起相关的事；有 background 就不会带梦，也不会误判问梦。已告诉 Hub。
- 01d21fd 的两条 Medium 已在 §177 修好、已部署 fc5aa41，不用再做。
- 测试 `test_hub_background.py`。没连真 Hub。

## 179. 高德 POI 一直没效果

- 小猫 11-06：「高德的 poi 一直没有发挥效果」。
- 查的结果（服务器上，只看条数）：key 正常，四个大类都能搜到结果；今天 POI 调用 0 次，`nearby_pois` 是空的。所以问题在我们怎么用：
  1. 只有 `state == outside` 才告诉 AI 有这个能力，在家问「附近有什么吃的」时 AI 没有工具，只能凭印象说。
  2. 只认四个大类。写「奶茶」会被直接丢掉，`search_nearby_pois_text` 返回空，调用方静默 return：AI 说了「我查查」，然后没下文。
  3. 不认全角冒号：`[POI_SEARCH：奶茶]` 不触发，还会露在聊天里。
  4. `amap_poi_search` 被高德拒绝时什么都不记，异常里还可能带 key。
  5. `_chatroom_poi_check` 单独抄了一份搜索，同样的毛病。
- 改法：
  - 新增 `capabilities._is_location_known`（位置开启且有坐标），`poi_search` 的判断都换成它。
  - 不在四大类里的词用高德 `keywords` 搜，一次最多 3 个。缓存只存四大类。
  - 小克私聊和 Lucien 没搜到时不再静默，用 `POI_FAILED_TEXT` 接着说「没查到，不要编」。座位原来就有提示。
  - 两处 pattern 认全角冒号，忽略大小写。
  - 被拒记录 info 和 infocode，异常只记类型。
  - `_chatroom_poi_check` 改用共用的 `search_nearby_pois_text`。
  - 能力说明改成：她问附近的东西时先查，不要凭印象；可以写大类或具体的词。
- 行为变化：`test_seat_location` 三项钉的是旧规则（只有户外、丢掉不认识的词），已按新规则改。
- 新增 POI 能力在家也会注入，多占一点提示词。
- 没改、要提醒的：POI 结果里的店名来自商家，续答（`perform_poi_check`、`_chatroom_poi_check`）仍进 `_process_background_reply_commands`。和 §171 M2、联网搜索是同一类老问题，这次没动。
- 测试 `test_poi_search.py` 10 项。相关 105 个文件 1062 过、8 败；这 8 项放到改动前的版本上同样失败（聊天室系统提示样式 2、手环 2、friend_visit 1、米环健康 1、triggered reasoning 2）。全量没跑。没有真的让 AI 搜过。
- **部署**：这个提交在 §178 之后，要等 Hub 的 background 上线才能一起部署。

## 180. 修 §178 复审的两项 Medium

- M1 AI 互聊后续轮次：`trigger_ai_chat` 第一轮是她按的，不标；`round_num > 0` 时给 `_reply_aion`、`_reply_connor`、`_generate_seat_reply` 传新参数 `hub_background=True`。只影响告诉 Hub 的标记，capture 规则不动。
- M2 旧 Hub 拒绝参数连累真实聊天：
  - `_call_tool` 工具报错改抛 `HubToolError`，对方说明放在 `detail`。这段可能带她的话，不进日志，只用于判断；`str(error)` 和原来一样。
  - `_background_rejected`：带 background 且明确是「不认这个参数」（含 ExceptionGroup 内层、McpError 文字）才算。网络和超时不算，照原来的熔断。
  - 被拒：不计入普通读取失败，后台读取单独暂停 10 分钟（`_note_read` 记 background_paused / background_rejected），返回空块。不去掉标记重试。真实聊天照常读。
- 部署验收（狐狸提醒旧签名可能静默忽略额外参数，不能只看调用成功）：Hub 上线后先用 `list_tools` 确认 `context` 的参数里有 `background`，再部署。「不带梦、不占次数」的语义由 Hub 侧确认。
- Hub 已回话接受：交接卡读取带 background（交接卡模式下不起作用）；Lucien 系统叫醒仍传提醒内容（有 background 就不带梦，提醒里的「梦」字也不算她在问）。
- 测试 `test_hub_background.py` +6。

## 181. 修 §180、§179 复审的 Medium 和 Low

- §180 M：`_background_rejected` 原来只看文字里是否同时有 background 和任意关键词。`ConnectionError("background stream: unexpected EOF")` 被误判，`{"code":"unrecognized_keys","keys":["background"]}` 又漏判。改成：只看 `HubToolError` 和 `McpError`，连接、超时、其他异常不管文字写什么都不算；文字只认明确的格式（unexpected keyword argument、extra inputs not permitted、unrecognized_keys、additional properties、unknown argument，且紧贴 background）。
- §179 M：`amap_regeo` 和 `amap_weather` 的异常也只记类型。全文件查过，高德请求的 print 都改了（哨兵那处不是高德请求，没动）。
- §179 L：`tts._STRIP_PATTERNS` 的 POI 认全角冒号和大小写。

## 182. 修 §181 复审的 Low 1：background 判断

- 参数名完整匹配（`background_job` 不算）；文字格式限定在同一条说明内，不跨 `;` 到请求回显里找；结构化错误看 `"keys":[…"background"…]`。狐狸的两条反例加进测试。

## 183. MCP 第 1 步：执行器（方案 v2.6 第二节）

- 新模块 `mcp_policy.py`：`classify`（看、写、玩、禁、没分类）、`known_secrets` / `redact`、`outcome`、`fingerprint`、`TripGuard`。
- `mcp_outings.run_outing` 接上执行器：
  - 工具清单先过 `visible_to_model`；每次调用先 `guard.before`，再 `guard.after` / `after_exception`；工具结果先遮后截。
  - 上限：40 轮、每轮 3 个调用（超出的回一句，不执行）、20 分钟。连续错 8 次回家。剩 5 轮或 3 分钟时提醒。
  - `_compact`：近 12 条保留完整结果，更早的截 200 字，每次带账本。原始记录不动。
  - 见闻：先遮挡，再 `strip_untrusted_reply`。
- `routes/mcp_tools.py`：`go_out` 带 `source`，同一位 AI 的锁（`_out_now`）放在这里，页面和自主活动共用（聊天入口第 3 步接）。报错先遮后截。
- `mcp_trips.db`：只记次数和状态，不记内容；`unclassified` 只记工具名和子命令名。
- 工具页两个新开关：「能发帖回帖」默认开，「没见过的操作也放行」默认关。
- 日志：`httpx`、`httpcore`、`mcp` 等的记录过 `_RedactLogs`。
- **有改变现有行为的地方**：
  - 上线后没分类的工具不能用。三个已知站按表走。花园和 4399 的表来自工具说明；AIsay 没登录只读到 `help`、`chat.*`、`account.*`。
  - 4399 `play` 除了带公开发言字样的都算玩，游戏太多，没有逐个列子动作（方案说未列出的默认拒绝，这里是例外，想请你判断）。AIsay `play.*` 同理。
- 没做：第 2 步 Codex 线路、第 3 步聊天入口。AIsay 账号怎么连要先问小猫（方案第一节）。
- 测试：`test_mcp_policy.py` 新增，`test_mcp_outings.py` 的假论坛开了放行；相关 20 个文件 402 项全过。本机页面看过开关（手机尺寸）。没有连真论坛走过一趟。

## 184. 相遇卡不让填别的座位在用的 Hub 身份

- Hub 窗口 11-06 要求：`Deepseek` 没别名、大小写敏感，能看公共记忆，看不到另外三位的私人记忆。相遇卡要防止填别人的身份（包括别名 cloudy=claude、gpt=lucien、gemini=jasper，不分大小写）。
- `actors.api_update_actor`：`memory_hub` 保存前查 `_hub_identity_taken_by`，别的座位（含停用的）在用就 400，提示谁在用。空值可以，自己的身份和别名可以。页面已有显示 `detail` 的逻辑。
- 只防相遇卡这个入口；已有数据没动（现在四位没有撞的）。`Deepseek` 不改小写，等小猫定。
- 测试 +2。

## 185. 修 §183 复审的 High 和三项 Medium（`a4fd66d`）

- High 遮挡出口：
  - 工具清单 `redact_deep`；工具名带钥匙的不展示。`_compact` 整段 `redact_deep`（真实调用用模型给的参数，不走这份）。账本、`counts`、`actions` 都遮。
  - 日志改成 `setLogRecordFactory`，记录创建时遮，覆盖子 logger（`httpcore.http11`）和异常堆栈；原来 logger 上的过滤器去掉。这是全局的，每条日志多一次字符串替换，钥匙按配置文件修改时间缓存。
  - `known_secrets` 无条件登记完整连接地址。
- M1 分类绕过：`_sub_actions` 取 `action`、`operation`、`command`（含 `args.*`、`params.*`、dict 的 `id`），先统一查禁止项。AIsay `play.*` 只放行说明里见过的两组（`play.linkgame`、`play.horror`），其他算没分类。4399 `play` 不再是「除了发言都算玩」：只放行列出的单人游戏和 `dt_*`，`duel`、`turtle_soup`、`shared_craft` 等算没分类，游戏名来自 `list_games`，**各游戏说明我没逐个读**。
- M2 不确定的玩：解除要同一局（`_scope_of`：4399 看 `game`，花园看 `nostos`，AIsay 看 `play.xxx`）的状态查询，查询成功且结果变了。`get_guide`、`list_games` 不算状态。认不出局的不解除。4399 没有独立的状态工具，用 `play` 带 `status` 这类子动作，**这些子动作名我没逐个核对**，核对前最坏是一直解除不了，不会误解除。
- M3 结果判断：写和玩要结构化回复且没说失败才算成功；写的纯文字一律不确定；玩的纯文字不像报错算成功（很多游戏回一段场景）。明确拒绝只认结构化字段，且没提到已保存等。**小猫要的「被打回能改了再发」依赖真实错误格式，要第一次真机对照；对不上时会算不确定，这趟就不再发。** 花园的两步确认第一步不发帖，改稿在这一步不受影响。
  - 例外：小猫开了放行的新站（`classify` 是 NEW），工具不分读写，很多只回纯文字。不像报错的纯文字算成功，否则读一次就判成「写入不确定」，这趟就废了；也不记成「已发出」。已知站点的写不变。这条放宽了 M3，因为是她明确放行的站，想请你看。
- 测试见 `test_mcp_policy.py`。没有连真论坛。

## 186. 4399 整站能玩（小猫的决定）

- 小猫 11-06：海龟汤肯定要玩，还想让 AI 拿到群里全家一起猜。§185 的名单是我按「多人的先不放」划的，撤掉：`play` 带了 `game` 就算玩，子动作带发言字样算写，账号类（`register` 等）仍禁，不知道哪个游戏算没分类。
- 这偏离了「不能以除了发言都算玩作为通用边界」的建议，只对 4399 这一个站。是她的决定，不用再提。

## 187. AIsay 的 token 可能在地址里

- 公开页面写着注册后推荐「替换地址为 auto_login_url」，token 在 AI 的工具配置里。所以 `_PATH_KEY_SITES` 加 `aisay.top`，路径段除 `chatroom`、`mcp` 外当钥匙遮。实际地址形式要等小猫找到那条地址才能确认（她不用发给我，我只看形式）。

## 188. 修 §185 复审的 High 和三项 Medium（`527e7ca`）

- High：日志防重入改成 `threading.local`；重入时用已缓存的钥匙遮，不读配置。加并发测试。
- M 字典键：`redact_deep` 也遮键；`names_contain_secret` 查工具名和任何一层的键名，带钥匙的整个工具不展示（遮了就调不了）。
- M 解锁：解除「不确定的玩」要 `is_trusted_state`：能解析的 JSON 对象，没说失败，且顶层或第二层有回合、版本、余额这类字段。错误页、纯文字、看不懂的都不算。没有这些字段的游戏就一直解除不了，但只拦这一个操作，其他操作不受影响。
- M 成功证据：
  - 写要肯定证据：`ok` / `success` 为真、正面 status，或有 `id`、`thread_id`、`reply_id`、`post_id`、`message_id`、`floor_number`、`write_confirmation_code`。`pending` 类、`denied` 类都不算成功。
  - 看和纯文字：像报错的页面（500、502、限流、权限）不算成功。
  - 新状态 `UNVERIFIED`：小猫放行的新站，收到回应但不知道成没成。不确认、不清零、不算错；同一个操作结果一样时第三次拦。
  - **玩没有要求肯定证据**：结构化或纯文字没说失败、不是 pending 就算走了这一步。这是我的取舍：游戏回执各不相同，要求证据会把正常玩拦住。后果只影响「重复同一步的拦截」和错误计数，想请你看。
- **真实站点的成功回执字段我没见过（还没真发过帖）**，要第一次真机对照 `_WRITE_EVIDENCE_KEYS`。对不上时算不确定，这趟不再发，不会重复发。
