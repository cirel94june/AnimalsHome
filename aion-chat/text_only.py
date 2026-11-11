"""这一次模型调用只许生成文字（狐狸 §219 H2）。

处理不受信任的材料（小故事、小作品）时设为 True，命令行模型就不带自动执行工具的权限。
用 contextvar：只对当前这次调用生效，不改全局开关，不影响同时进行的正常聊天。
"""

import contextvars

TEXT_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar("ai_text_only", default=False)
