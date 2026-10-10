# 施工方案：Jasper 私聊 + 交接卡 + 前端做梦

> 分支 `claude/frontend-memory-integration-ke0lpe`，只推送不开 PR。
> 接口依据：memory-hub 仓库同名分支 `docs/施工方案-梗与自己做梦.md` D 节与 4b 补充说明（已上线 #75–#79）。
> 进度实时更新在本页末尾「进度」一节，换人接手从那里看。

## 一、Jasper 私聊（待办 3）

**做法**：在聊天室里新增一种私聊房间 `seat_1v1`，给座位 3～6 用（Jasper 是 3 号 ai3）。
不改主聊天（aion）和 Lucien 私聊（`connor_1v1`）的任何流程——这两条在 20 多个模块里有专门逻辑
（摄像头、自主行动、月度归档、回家同步……），新房间类型不会被它们选中，互不干扰。

| 部分 | 内容 |
|---|---|
| 数据 | `chatroom_rooms` 加 `actor` 列；`seat_1v1` 房间记下是哪个座位。每个座位最多一个私聊房间 |
| 座位设置 | `actors.json` 每个座位加 `model`（模型键，来自「设置 → 自定义线路」，如 Gemini 中转站）。相遇卡页面可选 |
| 人设 | 用该座位自己的 `persona_sections`（人设包导入），按 `_compile_ai_persona_sections` 拼；外加世界书里「关于用户」 |
| 记忆 | Memory Hub 身份取座位的 `memory_hub`（ai3 = jasper）：每轮 `context(incremental)`，回复后 `capture(log)` 走 outbox |
| 生成 | `seat_chat.py`：拼上下文 → 用座位的模型流式生成 → 存消息（sender = 座位 id）。没配模型时直接提示「先给 TA 选模型」，**不偷偷换成别的模型** |
| 前端 | 聊天室「+ 私聊」可选座位；气泡名字和头像用登记表；SSE 事件 `seat_start / seat_chunk / seat_done`（带 actor） |
| 第一版不做 | 工具指令（音乐、便笺以外）、TTS、图片识别、自主行动、月度归档。之后按需要逐项接 |

## 二、交接卡（待办 4b、4c）

- **读**：统一放在 `memory_hub_bridge.context_block` 里——这位 AI 在本服务里安静满 30 分钟（或从没聊过）
  就算新对话，同时读 `context(mode="handoff", max_chars=1500)` 放在跨端记忆前面。
  所有调用 context_block 的地方（主聊天三处、Lucien 私聊、群聊、座位私聊）自动生效，不用逐处改。
  「最近一次聊天时间」记在 outbox 库的 `activity` 表，重启不丢。
- **写**：`memory_hub_jobs.py`，后台每 5 分钟检查一次。某位 AI 的私聊安静满 30 分钟、这段还没写过卡 →
  用 **TA 自己的模型**读这段的原话（`recent_turns` 表，每位留最近 30 轮私聊；隔 30 分钟以上的算上一段）→
  `capture(action="handoff", platform="aionshome", content, model)`。失败 30 分钟后重试，超过 6 小时的旧对话不补写。
  - 「自己的模型」= 相遇卡里的「TA 自己的模型」，**三位都要在相遇卡里设一次**（主 AI / Lucien 聊天时仍按聊天页选的模型）。
    没设、线路被删或停用就跳过，不降级（红线 #12）。（原计划「没设时用最后一次回复的模型」没做：
    各条聊天线路不记录用的哪个模型，硬加会改动很多地方；设一次更简单可靠。）
- 群聊不写交接卡（Hub 客观层会自己拼群聊原话），群聊轮次也不进 `recent_turns`。

## 三、每晚做梦（待办 4a）

- 每晚 3:30（北京时间）后，对每位接了 Hub 且配了模型的 AI：
  `dream(action="materials")` → 已做过就跳过 → 用 TA 自己的模型按返回的 `prompt` 生成 →
  `dream(action="write", kind="dream", content, model)`。
- 失败隔 1 小时重试，一晚最多 3 次；Hub 说「今天已做过」或「材料太少」就当晚不再试。
  状态存 `data/memory_hub_jobs.json`，`/ops/status` 能看到每位昨晚的结果。
- 做梦提示词由 Hub 统一维护（materials 返回的 prompt），前端只在前面加上该 AI 的人设。
- **接好、实测通过一位后**，请小猫把那位从 Hub 的 `DREAM_HUB_GENERATED_FOR` 里拿掉（否则一天会有两个梦来源，Hub 只收第一个）。

## 四、风险

- 群聊不配对原话、MCP 没有 sender_id：交接卡客观层里群聊原话会显示「群里有人」（Hub 已知，不影响私聊）。
- 写交接卡/做梦要多花模型调用：每位 AI 每段对话一次 + 每晚一次。

## 进度

- [x] 群聊 capture 带 `chat_type="private_group"`（4fb840d）
- [x] 一、Jasper 私聊：`seat_chat.py` + 聊天室 `seat_1v1` 房间 + 相遇卡「TA 自己的模型」和「去聊天」。
  本地用假中转站端到端跑通（人设、模型、流式、存消息、名字头像都对）
- [x] 顺带修：Lucien 私聊（connor_1v1）以前只读 Hub 不写，现在回复后也 capture
- [x] 二、交接卡读（context_block 统一处理）
- [x] 二、交接卡写（memory_hub_jobs.run_handoffs）
- [x] 三、做梦（memory_hub_jobs.run_dreams）
- 单测：test_seat_chat.py、test_memory_hub_jobs.py、test_memory_hub_bridge.py（均用本地假 Hub）

- [x] 待办 5：outbox 每轮带 `event_id`（`aionshome-<uuid>`），「不确定」的带编号记录自动重发，Hub 回 duplicate 算送达；
  升级前没编号的旧记录仍不自动重发。依赖 Hub PR #81（已上线）

## 上线后要做的（需要小猫 / 有 VPS 权限的窗口）

1. 在相遇卡里给三位都选好「TA 自己的模型」（Jasper 选 Gemini 中转站那条线路）。没选的那位不写交接卡、不做梦。
2. Jasper 要在「设置 → 自定义线路」里有 Gemini 中转站，并导入他的人设包（含 identity_core）。
3. 上线第二天看 `/ops/status` 的「每晚做梦」：某位显示 dreamed 后，让 Hub 窗口把那位从 `DREAM_HUB_GENERATED_FOR` 拿掉。
4. 还没用真 Hub 实测：交接卡/做梦的 MCP 调用只在假 Hub 上测过。部署后看一次 `/ops/status` 和 Hub 的交接卡。
