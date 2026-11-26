"""自定义规则与基线：读取仓库根目录 .repohealth.json。

格式：
{
  "rules":   [{"name": "内部密钥格式", "pattern": "MYCO-[A-Z0-9]{24}", "kind": "公司密钥", "confidence": "high"}],
  "ignore":  [{"file": "tests/fixtures/keys.py", "match": "AKIA", "reason": "测试夹具"}]
}

安全：模式长度上限、编译失败跳过、规则数上限，避免恶意配置拖垮扫描。
"""
from __future__ import annotations

import json
import os
import re

CONFIG_NAME = ".repohealth.json"
MAX_RULES = 50
MAX_PATTERN_LEN = 300


def load_config(root: str) -> dict:
    path = os.path.join(root, CONFIG_NAME)
    if not os.path.isfile(path):
        return {"rules": [], "ignore": [], "present": False}
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"rules": [], "ignore": [], "present": False, "error": "配置解析失败"}
    rules = []
    for r in (data.get("rules") or [])[:MAX_RULES]:
        pat = str(r.get("pattern") or "")
        if not pat or len(pat) > MAX_PATTERN_LEN:
            continue
        try:
            compiled = re.compile(pat)
        except re.error:
            continue
        rules.append({
            "name": str(r.get("name") or "custom"),
            "regex": compiled,
            "kind": str(r.get("kind") or "自定义规则命中"),
            "confidence": r.get("confidence") if r.get("confidence") in ("high", "medium", "low") else "medium",
        })
    ignores = []
    for i in (data.get("ignore") or [])[:200]:
        if isinstance(i, dict) and i.get("file"):
            ignores.append({
                "file": str(i["file"]).replace("\\", "/"),
                "match": str(i.get("match") or ""),
                "line": int(i.get("line") or 0),
                "reason": str(i.get("reason") or "基线忽略"),
            })
    return {"rules": rules, "ignore": ignores, "present": True}


def apply_rules(files: list[tuple[str, str]], rules: list[dict]) -> list[dict]:
    if not rules:
        return []
    out: list[dict] = []
    for rel, abspath in files:
        try:
            with open(abspath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if len(line) > 2000:
                continue
            for rule in rules:
                m = rule["regex"].search(line)
                if not m:
                    continue
                out.append({
                    "file": rel, "line": lineno, "match": m.group(0)[:80],
                    "kind": f'自定义规则: {rule["kind"]}',
                    "confidence": rule["confidence"],
                    "reason": f'命中规则 {rule["name"]}',
                    "engine": "custom",
                })
                break
    return out


def apply_ignores(findings: list[dict], ignores: list[dict]) -> tuple[list[dict], int]:
    """基线忽略：file 必须匹配；match / line 给了就一并匹配。"""
    if not ignores:
        return findings, 0
    kept, dropped = [], 0
    for f in findings:
        hit = False
        for ig in ignores:
            if f["file"].replace("\\", "/") != ig["file"]:
                continue
            if ig["line"] and f.get("line") != ig["line"]:
                continue
            if ig["match"] and ig["match"] not in (f.get("match") or ""):
                continue
            hit = True
            break
        if hit:
            dropped += 1
        else:
            kept.append(f)
    return kept, dropped
