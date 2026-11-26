"""第二引擎：可选调用 gitleaks，取其 provider 命中，丢弃其 generic 规则。

设计：召回取并集，精确靠上下文分类（gitleaks 命中同样走文档/测试降级）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

# gitleaks 的通用熵规则在真实代码上误报高（float32-slice / Asia-Singapore 等），不合并
DEFAULT_DROP_RULES = {"generic-api-key"}

_TOOL_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "tools", "gitleaks.exe"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "tools", "gitleaks"),
]


def find_gitleaks() -> str | None:
    env = os.environ.get("GITLEAKS_PATH")
    if env and os.path.isfile(env):
        return env
    for p in _TOOL_CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("gitleaks")


def run_gitleaks(repo_dir: str, timeout: int = 120) -> list[dict]:
    """返回 gitleaks 命中（已过滤 generic 规则），路径统一为仓库相对路径。"""
    exe = find_gitleaks()
    if not exe:
        return []
    drop = set(DEFAULT_DROP_RULES)
    env_rules = os.environ.get("GITLEAKS_DROP_RULES")
    if env_rules:
        drop |= {r.strip() for r in env_rules.split(",") if r.strip()}

    fd, report = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        subprocess.run(
            [exe, "dir", repo_dir, "-f", "json", "-r", report,
             "--exit-code", "0", "--no-banner", "-l", "error"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        with open(report, encoding="utf-8") as f:
            raw = json.load(f) or []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    finally:
        try:
            os.remove(report)
        except OSError:
            pass

    out: list[dict] = []
    for item in raw:
        rule = item.get("RuleID") or "gitleaks"
        if rule in drop:
            continue
        f = item.get("File") or ""
        try:
            rel = os.path.relpath(f, repo_dir).replace(os.sep, "/")
            if rel.startswith(".."):
                rel = f.replace(os.sep, "/")
        except ValueError:
            rel = f.replace(os.sep, "/")
        secret = item.get("Secret") or item.get("Match") or ""
        out.append({
            "file": rel,
            "line": int(item.get("StartLine") or 0),
            "match": secret[:80],
            "kind": f"gitleaks: {rule}",
            "confidence": "high",
            "reason": "gitleaks 规则命中",
            "engine": "gitleaks",
        })
    return out


def merge_gitleaks(ours: list[dict], gitleaks_hits: list[dict]) -> list[dict]:
    """按 (file, line) 去重，保留本项目的条目（带更详细 reason）。"""
    seen = {(s["file"], s["line"]) for s in ours}
    merged = list(ours)
    for g in gitleaks_hits:
        key = (g["file"], g["line"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(g)
    return merged
