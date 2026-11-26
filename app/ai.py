"""DeepSeek 客户端（唯一入口）。

Agent 复核（app/agent.py）与接入建议共用这一个客户端；无 DEEPSEEK_API_KEY 时返回 None，
上层自动降级为纯规则结果，不阻塞主流程。
"""
from __future__ import annotations

import os

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _load_dotenv() -> None:
    """读取项目根目录 .env（KEY=VALUE），不覆盖已有环境变量。"""
    if not os.path.isfile(_ENV_FILE):
        return
    try:
        with open(_ENV_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_dotenv()


def get_client():
    """全局唯一的 DeepSeek 客户端（OpenAI 兼容协议）。无 key 时返回 None。"""
    try:
        from openai import OpenAI
    except ImportError:
        return None
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, timeout=60.0)
