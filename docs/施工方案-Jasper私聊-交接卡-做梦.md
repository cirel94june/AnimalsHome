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

- **读**：一段新对话开头（距离这个房间/会话上一条消息超过 30 分钟，或第一条消息）注入一次
  `context(mode="handoff", source_ai, max_chars=1500)`。三条线都接：主聊天（小克）、Lucien 私聊、座位私聊。
- **写**：后台每 5 分钟检查一次。某位 AI 的私聊安静满 30 分钟、且这段对话还没写过卡 →
  用 **TA 自己的模型**读最后若干轮原话，按 Hub 建议的内容写交接卡 →
  `capture(action="handoff", source_ai, platform="aionshome", content, model)`。
  - 「自己的模型」= 座位设置里的 `model`；主 AI / Lucien 没设时用这段对话最后一次回复实际用的模型。
    都没有就跳过，不降级（红线 #12）。
  - 记录在 `data/memory_hub_jobs.json`：每位 AI 最后写卡对应的消息时间，避免重复写。
- 群聊第一版不写交接卡（Hub 客观层会自己拼群聊原话）。

## 三、每晚做梦（待办 4a）

- 每晚 3:30（北京时间）后，对每位接了 Hub 且配了模型的 AI：
  `dream(action="materials")` → 已做过就跳过 → 用 TA 自己的模型按返回的 `prompt` 生成 →
  `dream(action="write", kind="dream", content, model)`。
- 失败只记日志、第二天再试，不重试轰炸；状态在 `/ops/status` 可看。
- **接好、实测通过一位后**，请小猫把那位从 Hub 的 `DREAM_HUB_GENERATED_FOR` 里拿掉（否则一天会有两个梦来源，Hub 只收第一个）。

## 四、风险

- 群聊不配对原话、MCP 没有 sender_id：交接卡客观层里群聊原话会显示「群里有人」（Hub 已知，不影响私聊）。
- 写交接卡/做梦要多花模型调用：每位 AI 每段对话一次 + 每晚一次。

## 进度

- [x] 群聊 capture 带 `chat_type="private_group"`（4fb840d）
- [ ] 一、Jasper 私聊
- [ ] 二、交接卡读
- [ ] 二、交接卡写
- [ ] 三、做梦
