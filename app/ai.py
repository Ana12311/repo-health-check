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

# 网页里填的 key（运行时生效，存内存，不落盘）。优先级最高。
_runtime_key: str | None = None


def set_runtime_key(key: str | None) -> None:
    global _runtime_key
    _runtime_key = (key or "").strip() or None


def has_api_key() -> bool:
    return bool(_runtime_key or os.environ.get("DEEPSEEK_API_KEY"))


def runtime_key_set() -> bool:
    """网页里是否填过 key（与 .env 区分）。"""
    return bool(_runtime_key)


def get_client():
    """全局唯一的 DeepSeek 客户端（OpenAI 兼容协议）。无 key 时返回 None。

    优先级：网页填的 key > 环境变量 / .env。
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None
    key = _runtime_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, timeout=60.0)
