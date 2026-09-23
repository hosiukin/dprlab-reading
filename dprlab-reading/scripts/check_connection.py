#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dprlab-reading 连接验证：确认 lab-connect 隧道与 spark 临床模型服务可用。

用法：
  python check_connection.py                       # 默认探测 http://127.0.0.1:18000/v1
  python check_connection.py --base-url http://127.0.0.1:18000/v1 --model clinical-qwen36-35b

通过标准：/v1/models 列出模型（含 clinical-qwen36-35b 或别名 clinical-qwen3-4b），
且一次关闭思考的对话返回非空内容。纯标准库，强制直连（绕过本机代理）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:18000/v1"
DEFAULT_MODEL = "clinical-qwen36-35b"
ALIAS = "clinical-qwen3-4b"
DEFAULT_KEY = "local-vllm"

# 直连 opener：忽略 http_proxy/https_proxy 环境变量，防止本机代理劫持 127.0.0.1
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url: str, api_key: str, payload: dict | None, timeout: float) -> tuple[int, dict | str]:
    if payload is None:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        return e.code, body
    except Exception as e:  # noqa: BLE001
        raise ConnectionError(f"{type(e).__name__}: {e}") from e


def advise(err: str) -> str:
    s = str(err)
    if "ConnectionRefused" in s or "10061" in s or "积极拒绝" in s or "Connection refused" in s:
        return "本地端口没有监听 → lab-connect 转发未启动：打开 lab-connect UI，Start all forwards（本地 18000 → spark 127.0.0.1:8000）"
    if "timed out" in s or "Timeout" in s:
        return "网络不通 → 检查 EasyConnect 是否已连接登录；检查 lab-connect 隧道是否存活"
    if "gaierror" in s or "getaddrinfo" in s:
        return "主机名解析失败 → base_url 应为 http://127.0.0.1:18000/v1（走本地转发，不写服务器 IP）"
    return "见上方错误信息；排障手册见 references/connection.md"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("RP_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--model", default=os.environ.get("RP_MODEL", DEFAULT_MODEL))
    ap.add_argument("--api-key", default=os.environ.get("RP_API_KEY", DEFAULT_KEY))
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    if not base.endswith("/v1"):
        print(f"[warn] base_url 似乎缺少 /v1 后缀：{base}")
    print(f"探测端点: {base}   模型: {args.model}\n")

    # --- 1) /models ---
    try:
        code, body = http_json(f"{base}/models", args.api_key, None, args.timeout)
    except ConnectionError as e:
        print(f"[FAIL] 无法连接: {e}\n      → {advise(e)}")
        return 1
    if code == 401:
        print("[FAIL] HTTP 401：API key 不对（spark 服务 key 为 local-vllm）")
        return 1
    if code == 404:
        print("[FAIL] HTTP 404：路径不对，base_url 必须以 /v1 结尾")
        return 1
    if code != 200:
        print(f"[FAIL] HTTP {code}: {body}")
        return 1
    ids = [m.get("id", "") for m in body.get("data", [])]
    print(f"[OK] /models 返回 {len(ids)} 个模型: {', '.join(ids) or '(空)'}")
    known = {args.model, ALIAS}
    hit = [m for m in ids if m in known]
    if not hit:
        print(f"[FAIL] 模型列表中没有 {args.model} 或别名 {ALIAS} —— spark 侧服务可能异常，请联系管理员")
        return 1
    model = hit[0]
    print(f"[OK] 目标模型可用: {model}\n")

    # --- 2) 1-token 对话（关思考、温度 0）---
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "请只回复两个字：正常"}],
        "temperature": 0,
        "max_tokens": 16,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        code, body = http_json(f"{base}/chat/completions", args.api_key, payload, args.timeout * 4)
    except ConnectionError as e:
        print(f"[FAIL] 对话请求失败: {e}\n      → {advise(e)}")
        return 1
    if code != 200:
        print(f"[FAIL] 对话返回 HTTP {code}: {body}")
        return 1
    content = ""
    try:
        content = (body["choices"][0]["message"]["content"] or "").strip()
    except Exception:  # noqa: BLE001
        pass
    if not content:
        print("[FAIL] 返回内容为空 —— 常见原因：未带 enable_thinking=false，思考耗尽了 max_tokens")
        return 1
    print(f"[OK] 对话正常，回复: {content[:50]}")
    print("\n全部通过：隧道与服务均正常，可以开始病历读取（见 references/pipeline_example.md）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
