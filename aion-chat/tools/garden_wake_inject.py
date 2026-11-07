#!/usr/bin/env python3
"""花园唤醒桥的 injector：桥每次叫醒时启动一次，从 stdin 读一行 JSON，交给家里本机的接口。

用法：garden_wake_inject.py <AI>（aion / connor / ai3）。只用标准库。
家里回「已接收」就退出 0；别的情况退出 1，stderr 写一句短的原因（桥会重试一次）。
不读、不打印机器 token（桥本来也不传进来）。
"""

import json
import os
import sys
import urllib.error
import urllib.request

ACTORS = ("aion", "connor", "ai3")
URL = os.environ.get("GARDEN_WAKE_HOME_URL", "http://127.0.0.1:8080")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ACTORS:
        print("usage: garden_wake_inject.py <aion|connor|ai3>", file=sys.stderr)
        return 1
    actor = sys.argv[1]
    key = os.environ.get("GARDEN_WAKE_LOCAL_KEY", "").strip()
    if not key:
        print("GARDEN_WAKE_LOCAL_KEY missing", file=sys.stderr)
        return 1
    try:
        envelope = json.loads(sys.stdin.buffer.readline().decode("utf-8"))  # 桥写的是 UTF-8，不看系统编码
    except ValueError:
        print("invalid envelope", file=sys.stderr)
        return 1
    if (not isinstance(envelope, dict) or envelope.get("version") != 1 or envelope.get("type") != "garden_wake"
            or not isinstance(envelope.get("reason"), str) or not isinstance(envelope.get("message"), str)
            or not envelope["message"].strip()):
        print("invalid envelope", file=sys.stderr)
        return 1
    body = json.dumps({"reason": envelope["reason"], "message": envelope["message"]}).encode("utf-8")
    request = urllib.request.Request(f"{URL}/api/garden-wake/{actor}/wake", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Garden-Wake-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        print(f"home rejected: {error.code}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"home unreachable: {type(error).__name__}", file=sys.stderr)
        return 1
    if not data.get("accepted"):
        print("home did not accept", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
