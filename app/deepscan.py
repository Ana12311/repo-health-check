"""深度扫描：压缩包内容 + Base64/URL 编码递归解码。

限制：单条目 256KB、总解压 20MB、最多 500 条目、解码深度 2。
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import re
import tarfile
import urllib.parse
import zipfile

from .scanners import _PROVIDER_PATTERNS, _looks_placeholder, _entropy, _scan_secrets_lines

ARCHIVE_EXTS = (".zip", ".jar", ".war", ".whl", ".tar", ".tar.gz", ".tgz", ".tar.bz2")
MAX_ENTRY_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 500

_B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _iter_archive_lines(path: str, limit: dict):
    """产出 (inner_name, text)；超过预算就停。"""
    low = path.lower()
    try:
        if low.endswith(".zip") or low.endswith((".jar", ".war", ".whl")):
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    if limit["entries"] >= MAX_ENTRIES or limit["bytes"] >= MAX_TOTAL_BYTES:
                        return
                    if info.file_size > MAX_ENTRY_BYTES:
                        continue
                    limit["entries"] += 1
                    data = z.read(info)
                    limit["bytes"] += len(data)
                    yield info.filename, data.decode("utf-8", errors="replace")
        else:
            with tarfile.open(path, "r:*") as t:
                for member in t:
                    if not member.isfile():
                        continue
                    if limit["entries"] >= MAX_ENTRIES or limit["bytes"] >= MAX_TOTAL_BYTES:
                        return
                    if member.size > MAX_ENTRY_BYTES:
                        continue
                    f = t.extractfile(member)
                    if not f:
                        continue
                    limit["entries"] += 1
                    data = f.read()
                    limit["bytes"] += len(data)
                    yield member.name, data.decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, tarfile.TarError, OSError, RuntimeError):
        return


def scan_archives(all_paths: list[str], root: str) -> list[dict]:
    findings: list[dict] = []
    limit = {"entries": 0, "bytes": 0}
    for rel in all_paths:
        if not rel.lower().endswith(ARCHIVE_EXTS):
            continue
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > 20 * 1024 * 1024:
                continue
        except OSError:
            continue
        for inner, text in _iter_archive_lines(full, limit):
            lines = text.splitlines()
            for f in _scan_secrets_lines(f"{rel}!{inner}"[:200], list(enumerate(lines, 1))):
                f["kind"] = f'{f["kind"]} (压缩包)'
                f["reason"] = f'{f.get("reason", "")} · 来自 {rel}!{inner}'.strip(" ·")
                f["confidence"] = "medium"
                findings.append(f)
            if len(findings) > 300:
                return findings
    return findings


def _printable(s: str) -> bool:
    if not s:
        return False
    printable = sum(1 for c in s if 32 <= ord(c) < 127 or c in "\t")
    return printable / len(s) > 0.8


def _detect_in_text(text: str, rel: str, line: int) -> list[dict]:
    out: list[dict] = []
    for kind, pat in _PROVIDER_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        val = m.group(0)
        if _entropy(val) < 2.8:
            continue
        out.append({
            "file": rel, "line": line, "match": val[:80],
            "kind": f"{kind} (编码后隐藏)", "confidence": "medium",
            "reason": "Base64/URL 编码中解出的 provider 密钥",
            "engine": "decode",
        })
        break
    return out


def scan_encoded(files: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    for rel, abspath in files:
        try:
            with open(abspath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if len(line) > 4000:
                continue
            # Base64：深度 2
            for token in _B64_RE.findall(line)[:3]:
                cur = token
                for _depth in range(2):
                    try:
                        raw = base64.b64decode(cur + "=" * (-len(cur) % 4), validate=False)
                        decoded = raw.decode("utf-8", errors="replace")
                    except (binascii.Error, ValueError):
                        break
                    if not _printable(decoded):
                        break
                    hits = _detect_in_text(decoded, rel, lineno)
                    findings.extend(hits)
                    if hits:
                        break
                    cur = decoded.strip()
                    if len(cur) < 40:
                        break
            # URL 编码
            if re.search(r"%[0-9A-Fa-f]{2}", line):
                decoded = urllib.parse.unquote(line)
                if decoded != line:
                    findings.extend(_detect_in_text(decoded, rel, lineno))
    # 去重
    seen, out = set(), []
    for f in findings:
        k = (f["file"], f["line"], f["match"])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out
