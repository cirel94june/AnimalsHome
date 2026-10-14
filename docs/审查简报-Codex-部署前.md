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
