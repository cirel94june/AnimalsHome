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
