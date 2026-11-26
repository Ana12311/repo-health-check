"""Git 历史扫描：找出曾经提交过的密钥（即使后来删除）。

只读 diff 新增行（`+` 开头），不扫删除行。结果默认只展示、不计分——
历史密钥通常已轮换，且评分漂移会让结果不可复现。
"""
from __future__ import annotations

import os
import subprocess

from .scanners import _match_line

MAX_FINDINGS = 200
MAX_LINE_LEN = 2000


def scan_history(repo_dir: str, max_commits: int = 50) -> tuple[list[dict], str]:
    """返回 (findings, 错误说明)。找不到 git / 无历史时返回空。"""
    if max_commits <= 0:
        return [], ""
    cmd = [
        "git", "-C", repo_dir, "log", "-p", "-U0", "--no-merges",
        f"-n{max_commits}", "--pretty=format:@@C@@%H%x09%s", "--", ".",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"历史扫描失败: {e}"
    if proc.returncode != 0:
        return [], "历史扫描失败: 无法读取 git log"

    findings: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sha = ""
    subject = ""
    cur_file = None

    def add(line: str) -> None:
        nonlocal cur_file
        if not cur_file:
            return
        hit = _match_line(line, cur_file, os.path.basename(cur_file), False, [])
        if not hit:
            return
        match, kind, _conf, _reason = hit
        key = (cur_file, match[:40])
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "file": cur_file,
            "line": 0,
            "match": match[:80],
            "kind": f"{kind}（历史提交）",
            "confidence": "medium",
            "reason": f"commit {sha[:7]} 引入：{subject[:60]}",
            "engine": "history",
            "commit": sha,
        })

    for raw in proc.stdout.splitlines():
        if raw.startswith("@@C@@"):
            meta = raw[5:].split("\t", 1)
            sha = meta[0].strip()
            subject = meta[1] if len(meta) > 1 else ""
            cur_file = None
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                cur_file = None
            else:
                cur_file = path[2:] if path.startswith("b/") else path
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if len(raw) > MAX_LINE_LEN:
                continue
            add(raw[1:])
            if len(findings) >= MAX_FINDINGS:
                return findings, "已达历史上限，仅扫描部分提交"
    return findings, ""
