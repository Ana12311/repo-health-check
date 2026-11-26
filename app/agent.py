"""Agent 复核层：把规则引擎里置信度不高（medium/low）的发现交给 LLM 复核。

设计原则：
- 高危（high）发现不送审、也不允许被 Agent 降级——格式明确，规则比模型可靠。
- 只有 medium/low 可能被判为误报并从评分中移除，且保留在报告里供人查看。
- 与「接入建议」共用同一个 DeepSeek 客户端，一次调用同时返回复核结果和建议。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .ai import get_client, DEEPSEEK_MODEL
from .scoring import _is_non_leak  # noqa: F401  (复用判定)

MAX_CANDIDATES = 20
SNIPPET_CHARS = 240


def _snippet(repo_dir: str, rel: str, line: int) -> str:
    if not line:
        return ""
    try:
        lines = Path(os.path.join(repo_dir, rel)).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lo = max(0, line - 2)
    hi = min(len(lines), line + 1)
    return "\n".join(l.strip() for l in lines[lo:hi])[:SNIPPET_CHARS]


def collect_candidates(repo_dir: str, scan: dict) -> list[dict]:
    """挑出需要 Agent 复核的发现：非文档、置信度 medium/low。"""
    out: list[dict] = []
    for idx, s in enumerate(scan.get("secrets") or []):
        if s.get("confidence") == "high" or s.get("sample"):
            continue
        if _is_non_leak(s):
            continue
        out.append({
            "id": f"s{idx}", "type": "secret", "file": s["file"], "line": s["line"],
            "kind": s["kind"], "match": s["match"], "confidence": s["confidence"],
            "snippet": _snippet(repo_dir, s["file"], s["line"]),
        })
    for idx, p in enumerate(scan.get("hardcoded_paths") or []):
        if p.get("severity") not in ("medium", "low"):
            continue
        if _is_non_leak(p):
            continue
        out.append({
            "id": f"p{idx}", "type": "path", "file": p["file"], "line": p["line"],
            "kind": p["kind"], "match": p["match"], "confidence": p["severity"],
            "snippet": _snippet(repo_dir, p["file"], p["line"]),
        })
    # medium 优先，其次 low
    order = {"medium": 0, "low": 1}
    out.sort(key=lambda c: order.get(c["confidence"], 2))
    return out[:MAX_CANDIDATES]


def _build_payload(repo_url: str, provisional: dict, candidates: list[dict]) -> dict:
    return {
        "repo": repo_url,
        "archetype": (provisional.get("archetype") or {}).get("type"),
        "rule_score": provisional.get("score"),
        "components": {
            k: {"score": v["score"], "max": v["max"]}
            for k, v in (provisional.get("components") or {}).items()
        },
        "candidates": candidates,
    }


SYSTEM_PROMPT = (
    "你是代码审查 Agent。任务分两部分：\n"
    "1) 复核给定的候选发现（candidates）。每条只能基于提供的 snippet 判断，"
    "evidence 不足以判断时必须标 uncertain，不得臆测。"
    "verdict 只能是 confirmed（真实风险）、false_positive（示例/测试数据/常量/非凭据）、"
    "uncertain（无法判断，交人工）。\n"
    "2) 基于仓库类型与规则评分，给出接入判断和 3-5 条按优先级排序的建议。"
    "只能引用输入里出现的事实，不得编造文件路径或依赖。\n"
    '只输出 JSON：{"review": [{"id": "...", "verdict": "...", "reason": "一句话"}], '
    '"verdict": "可直接接入|需整改后接入|不建议接入|无法评估", '
    '"reason": "一句话", "advice": ["..."]}。不要输出其他文字。'
)


def run_agent(repo_url: str, repo_dir: str, scan: dict, provisional: dict) -> dict | None:
    """一次调用完成复核 + 建议。无 key / 失败返回 None。"""
    client = get_client()
    if client is None:
        return None
    candidates = collect_candidates(repo_dir, scan)
    payload = _build_payload(repo_url, provisional, candidates)
    user = "仓库体检数据：\n" + json.dumps(payload, ensure_ascii=False, indent=2)

    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=1200,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
    except Exception:
        return None

    valid_ids = {c["id"] for c in candidates}
    verdicts: dict[str, dict] = {}
    for item in data.get("review") or []:
        cid = item.get("id")
        if cid in valid_ids and item.get("verdict") in ("confirmed", "false_positive", "uncertain"):
            verdicts[cid] = {
                "verdict": item["verdict"],
                "reason": (item.get("reason") or "")[:200],
            }

    return {
        "verdicts": verdicts,
        "verdict": data.get("verdict", ""),
        "reason": data.get("reason", ""),
        "advice": data.get("advice", []),
        "model": DEEPSEEK_MODEL,
        "reviewed": len(candidates),
    }
