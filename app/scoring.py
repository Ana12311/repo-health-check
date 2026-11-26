"""接入就绪度评分 + 风险清单。

按仓库类型选择 rubric：
- software：README / 依赖 / 启动 / Windows 脚本 / 硬编码路径 / 密钥
- docs：README 结构 / 导航 / 许可证 / 示例 / 敏感信息
- data：README / 数据说明 / 许可证 / 脚本 / 敏感信息
- empty / unknown：不评分，只给事实
"""
from __future__ import annotations

import re

from .classify import classify_repo

TYPE_LABELS = {
    "software": "软件项目",
    "docs": "文档仓库",
    "data": "数据/模型仓库",
    "empty": "空仓库",
    "unknown": "未分类",
}

_DOC_EXT = (".md", ".rst", ".txt", ".markdown", ".mdx", ".adoc")
_DOC_DIRS = {
    "docs", "doc", "documentation", "examples", "example", "references",
    "reference", "labs", "lab", "samples", "sample", "fixtures", "fixture",
    "tests", "test", "demos", "demo", "verification", "skills",
    "tutorial", "tutorials", "cookbook", "__tests__", "spec", "specs",
    "playground", "playgrounds", "e2e", "integration", "integration_tests",
    "testdata", "test-data", "test_data", "document", "documents",
}
_DOC_DIR_PREFIXES = (
    "docs_", "examples_", "samples_", "tutorial_", "test_", "tests_", "testdata_",
)
# TerminalApiTest.cpp / UtilsTests.cs / fooTests.js 等测试源文件
_TEST_FILE_RE = re.compile(
    r"(tests?|specs?|_test|_spec)\.(py|js|ts|tsx|jsx|mjs|cjs|java|kt|cs|cpp|cc|c|h|hpp|go|rs|rb|php|swift)$",
    re.IGNORECASE,
)


def _is_doc_or_test(rel: str) -> bool:
    low = rel.lower().replace("\\", "/")
    if low.endswith(_DOC_EXT):
        return True
    base = low.rsplit("/", 1)[-1]
    if (base.startswith(("test_", "test-"))
            or "_test." in base or "_spec." in base or ".test." in base
            or ".spec." in base
            or _TEST_FILE_RE.search(base)):
        return True
    for seg in low.split("/"):
        if seg in _DOC_DIRS or seg.startswith(_DOC_DIR_PREFIXES):
            return True
    return False


def _grade(score: float) -> str:
    if score >= 80:
        return "优"
    if score >= 60:
        return "良"
    if score >= 40:
        return "中"
    return "差"


def _comp(name: str, score: float, max_score: float, notes: list[str] | None = None) -> dict:
    return {
        "name": name,
        "score": round(max(0.0, min(max_score, score)), 1),
        "max": max_score,
        "applicable": True,
        "notes": notes or [],
    }


# --- 各维度打分 ---

def _readme_software(readme: dict) -> tuple[float, list[str]]:
    if not readme["exists"]:
        return 0.0, ["未提供 README 文档"]
    notes = list(readme.get("missing") or [])
    pts = 8.0
    if "install" in readme["sections"]:
        pts += 4
    if "usage" in readme["sections"]:
        pts += 4
    wc = readme["word_count"]
    pts += 2 if wc >= 200 else (1 if wc >= 80 else 0)
    if "license" in readme["sections"]:
        pts += 2
    return pts, notes


def _deps_software(deps: dict) -> tuple[float, list[str]]:
    n = deps["count"]
    if n == 0:
        return 0.0, ["未识别到依赖清单文件"]
    pts = 15.0 if n == 1 else 18.0
    if deps.get("lockfiles"):
        pts += 2
    return pts, []


def _startup_software(startup: dict) -> tuple[float, list[str]]:
    if startup["has_script_evidence"]:
        return 20.0, []
    if startup.get("has_ci_only"):
        return 10.0, ["仅有 CI 配置，未找到直接启动脚本"]
    return 0.0, ["未识别到启动脚本 / 入口"]


def _paths_software(real_paths: list[dict]) -> tuple[float, list[str]]:
    """按「受影响文件」扣分，单文件最多扣 4 分——避免测试数据把分数打穿。"""
    per_file: dict[str, float] = {}
    for p in real_paths:
        if p.get("severity") == "high":
            per_file[p["file"]] = min(4.0, per_file.get(p["file"], 0.0) + 4.0)
        elif p.get("severity") == "medium":
            per_file[p["file"]] = min(4.0, per_file.get(p["file"], 0.0) + 2.0)
    pts = max(0.0, 15.0 - sum(per_file.values()))
    notes = [f"发现 {len(real_paths)} 处硬编码绝对路径（{len(per_file)} 个文件）"] if real_paths else []
    return pts, notes


def _secrets_software(real_secrets: list[dict]) -> tuple[float, list[str]]:
    pts = 20.0
    high = sum(1 for s in real_secrets if s.get("confidence") == "high")
    medium = sum(1 for s in real_secrets if s.get("confidence") == "medium")
    pts -= 10 * high + 4 * medium
    notes = [f"发现 {len(real_secrets)} 处密钥 / 敏感信息风险"] if real_secrets else []
    return max(0.0, pts), notes


def _readme_docs(readme: dict) -> tuple[float, list[str]]:
    if not readme["exists"]:
        return 0.0, ["未提供 README"]
    pts = 10.0
    wc = readme["word_count"]
    pts += 10 if wc >= 1000 else (7 if wc >= 300 else (4 if wc >= 80 else 0))
    h = readme["headings"]
    pts += 10 if h >= 8 else (6 if h >= 4 else (3 if h >= 1 else 0))
    pts += min(10.0, 3.0 * len(readme["sections"]))
    return pts, []


def _nav_docs(readme: dict) -> tuple[float, list[str]]:
    links = readme.get("links", 0)
    if links >= 10:
        return 15.0, []
    if links >= 3:
        return 10.0, []
    if links >= 1:
        return 5.0, ["文档间链接较少，导航性一般"]
    return 0.0, ["README 无内部/外部链接，缺少导航"]


def _license_score(signals: dict, readme: dict) -> tuple[float, list[str]]:
    if signals.get("license_file"):
        return 1.0, []
    if readme.get("exists") and "license" in readme.get("sections", []):
        return 0.8, []
    return 0.0, ["未找到许可证文件或说明"]


def _examples_docs(readme: dict) -> tuple[float, list[str]]:
    cb = readme.get("code_blocks", 0)
    if cb >= 5:
        return 20.0, []
    if cb >= 2:
        return 14.0, ["示例代码块较少"]
    if cb >= 1:
        return 8.0, ["示例代码块较少"]
    return 0.0, ["文档中没有代码示例"]


def _secrets_generic(all_secrets: list[dict], max_score: float) -> tuple[float, list[str]]:
    high = sum(1 for s in all_secrets if s.get("confidence") == "high")
    medium = sum(1 for s in all_secrets if s.get("confidence") == "medium")
    pts = max_score - 10 * high - 3 * medium
    notes = []
    if high or medium:
        notes.append(f"发现 {high + medium} 处密钥 / 敏感信息风险")
    return max(0.0, pts), notes


# --- 风险清单 ---

def _risk_from_secret(s: dict, category: str = "密钥/敏感信息") -> dict:
    conf = s.get("confidence", "medium")
    sev = {"high": "high", "medium": "medium"}.get(conf, "low")
    if category != "密钥/敏感信息":
        sev = "low"  # 文档/示例/规则库里的命中只提示，不标高危
    return {
        "severity": sev,
        "confidence": conf,
        "category": category,
        "file": s["file"],
        "line": s["line"],
        "detail": f'{s["kind"]}: {s["match"]}',
        "reason": s.get("reason", ""),
        "agent": s.get("agent"),
    }


def _risk_from_path(p: dict, category: str = "硬编码路径") -> dict:
    sev = p.get("severity", "medium")
    if category != "硬编码路径":
        sev = "low"
    return {
        "severity": sev,
        "confidence": {"high": "high", "medium": "medium"}.get(p.get("severity"), "low"),
        "category": category,
        "file": p["file"],
        "line": p["line"],
        "detail": f'{p["kind"]}: {p["match"]}',
        "reason": p.get("reason", ""),
        "agent": p.get("agent"),
    }


def _note_risk(note: str, category: str, severity: str) -> dict:
    return {
        "severity": severity,
        "confidence": "high",
        "category": category,
        "file": "",
        "line": 0,
        "detail": note,
        "reason": "",
    }


def _sort_risks(risks: list[dict]) -> list[dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda r: order.get(r["severity"], 3))
    return risks


def _is_non_leak(finding: dict) -> bool:
    """文档/测试目录，或密钥扫描规则库里的样例数据。"""
    return finding.get("sample") is True or _is_doc_or_test(finding["file"])


def _split_findings(scan: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    real_secrets = [s for s in scan["secrets"] if not _is_non_leak(s)]
    doc_secrets = [s for s in scan["secrets"] if _is_non_leak(s)]
    real_paths = [p for p in scan["hardcoded_paths"] if not _is_doc_or_test(p["file"])]
    doc_paths = [p for p in scan["hardcoded_paths"] if _is_doc_or_test(p["file"])]
    return real_secrets, doc_secrets, real_paths, doc_paths


def _summary(scan: dict, real_secrets: list[dict], real_paths: list[dict], doc_count: int) -> dict:
    stats = scan.get("stats") or {}
    return {
        "secrets": len(real_secrets),
        "secrets_high": sum(1 for s in real_secrets if s.get("confidence") == "high"),
        "hardcoded_paths": len(real_paths),
        "windows_scripts": len(scan["windows_scripts"]),
        "dep_manifests": scan["deps"]["count"],
        "doc_findings": doc_count,
        "files_scanned": stats.get("scanned_files", scan.get("file_count", 0)),
        "files_total": stats.get("total_paths", 0),
        "skipped_large": stats.get("skipped_large", 0),
    }


def _finish(archetype: dict, components: dict, risks: list[dict], summary: dict) -> dict:
    applicable = [c for c in components.values() if c["applicable"]]
    total = round(sum(c["score"] for c in applicable), 1)
    max_total = sum(c["max"] for c in applicable)
    # 归一化到 100（防御：rubric 配置错误时不会超过 100）
    if max_total and max_total != 100:
        total = round(total * 100.0 / max_total, 1)
    total = max(0.0, min(100.0, total))
    return {
        "archetype": archetype,
        "score": total,
        "grade": _grade(total),
        "components": components,
        "risks": _sort_risks(risks),
        "summary": summary,
    }


# --- Agent 复核 ---

def _apply_agent(scan: dict, verdicts: dict | None) -> tuple[dict, list[dict], list[dict], int, int]:
    """把 Agent 判为误报的 medium/low 发现移出评分。

    返回 (用于评分的 scan, 被降级的密钥, 被降级的路径, 复核数, 判误报数)。
    high 置信度永不降级。
    """
    if not verdicts:
        return scan, [], [], 0, 0
    kept_secrets, fp_secrets = [], []
    for idx, s in enumerate(scan.get("secrets") or []):
        v = verdicts.get(f"s{idx}")
        if v:
            s = {**s, "agent": v}
            if v["verdict"] == "false_positive" and s.get("confidence") != "high":
                fp_secrets.append(s)
                continue
        kept_secrets.append(s)
    kept_paths, fp_paths = [], []
    for idx, p in enumerate(scan.get("hardcoded_paths") or []):
        v = verdicts.get(f"p{idx}")
        if v:
            p = {**p, "agent": v}
            if v["verdict"] == "false_positive" and p.get("severity") != "high":
                fp_paths.append(p)
                continue
        kept_paths.append(p)
    scan2 = {**scan, "secrets": kept_secrets, "hardcoded_paths": kept_paths}
    return scan2, fp_secrets, fp_paths, len(verdicts), len(fp_secrets) + len(fp_paths)


# --- 主入口 ---

def build_score_and_risks(scan: dict, agent_verdicts: dict | None = None) -> dict:
    scan, fp_secrets, fp_paths, reviewed, downgraded = _apply_agent(scan, agent_verdicts)
    signals = scan.get("signals") or {}
    archetype = classify_repo(signals)
    atype = archetype["type"]
    real_secrets, doc_secrets, real_paths, doc_paths = _split_findings(scan)
    summary = _summary(scan, real_secrets, real_paths, len(doc_secrets) + len(doc_paths))

    risks: list[dict] = []
    for s in real_secrets:
        risks.append(_risk_from_secret(s))
    for s in doc_secrets:
        risks.append(_risk_from_secret(s, "密钥/敏感信息(文档示例)"))
    for p in real_paths:
        risks.append(_risk_from_path(p))
    for p in doc_paths:
        risks.append(_risk_from_path(p, "硬编码路径(文档示例)"))

    for s in fp_secrets:
        risks.append(_risk_from_secret(s, "密钥/敏感信息(AI 判为误报)"))
    for p in fp_paths:
        risks.append(_risk_from_path(p, "硬编码路径(AI 判为误报)"))
    summary["agent_reviewed"] = reviewed
    summary["agent_downgraded"] = downgraded

    # 历史泄露：只展示、不计分（多为已轮换的旧密钥）
    history = scan.get("history") or []
    for h in history:
        risks.append({
            "severity": "low" if _is_non_leak(h) else "medium",
            "confidence": h.get("confidence", "medium"),
            "category": "历史泄露",
            "file": h["file"],
            "line": 0,
            "detail": f'{h["kind"]}: {h["match"]}',
            "reason": h.get("reason", ""),
            "agent": None,
        })
    summary["history"] = len(history)
    summary["ignored"] = scan.get("ignored", 0)

    if atype in ("empty", "unknown"):
        risks.append(_note_risk(
            "仓库内容过少或类型不明确，无法评估接入就绪度", "类型识别", "low"))
        return {
            "archetype": {**archetype, "label": TYPE_LABELS.get(atype, atype)},
            "score": None,
            "grade": "无法评估",
            "components": {},
            "risks": _sort_risks(risks),
            "summary": summary,
        }

    components: dict[str, dict] = {}
    readme = scan["readme"]
    deps = scan["deps"]
    startup = scan["startup"]
    windows = scan["windows_scripts"]

    if atype == "software":
        s_readme, n_readme = _readme_software(readme)
        components["readme"] = _comp("README 完整度", s_readme, 20, n_readme)
        s_deps, n_deps = _deps_software(deps)
        components["deps"] = _comp("依赖清单", s_deps, 20, n_deps)
        s_start, n_start = _startup_software(startup)
        components["startup"] = _comp("启动入口", s_start, 20, n_start)
        components["windows"] = _comp(
            "Windows 脚本", 5.0 if windows else 0.0, 5,
            [] if windows else ["无 Windows 专用脚本 (.bat/.cmd/.ps1/.vbs)"],
        )
        s_paths, n_paths = _paths_software(real_paths)
        components["paths"] = _comp("硬编码路径", s_paths, 15, n_paths)
        s_sec, n_sec = _secrets_software(real_secrets)
        components["secrets"] = _comp("密钥风险", s_sec, 20, n_sec)

        for note in n_readme:
            risks.append(_note_risk(note, "README", "low"))
        for note in n_deps:
            risks.append(_note_risk(note, "依赖清单", "medium"))
        for note in n_start:
            risks.append(_note_risk(note, "启动入口", "medium"))
        if not windows:
            risks.append(_note_risk("无 Windows 专用脚本 (.bat/.cmd/.ps1/.vbs)", "Windows 脚本", "low"))
        for note in n_sec:
            risks.append(_note_risk(note, "密钥/敏感信息", "high"))

    elif atype == "docs":
        s_readme, n_readme = _readme_docs(readme)
        components["readme"] = _comp("README 结构", s_readme, 40, n_readme)
        s_nav, n_nav = _nav_docs(readme)
        components["nav"] = _comp("文档导航", s_nav, 15, n_nav)
        s_lic, n_lic = _license_score(signals, readme)
        components["license"] = _comp("许可证", s_lic * 10, 10, n_lic)
        s_ex, n_ex = _examples_docs(readme)
        components["examples"] = _comp("使用示例", s_ex, 20, n_ex)
        s_sec, n_sec = _secrets_generic(
            [s for s in scan["secrets"] if not _is_non_leak(s)], 15)
        components["secrets"] = _comp("敏感信息", s_sec, 15, n_sec)
        for note in n_readme + n_nav + n_lic + n_ex:
            risks.append(_note_risk(note, "文档质量", "low"))

    elif atype == "data":
        if readme["exists"]:
            wc = readme["word_count"]
            pts = 15.0 + (8 if wc >= 200 else (4 if wc >= 80 else 0))
            pts += 7 if readme["headings"] >= 3 else 0
        else:
            pts = 0.0
        components["readme"] = _comp(
            "README / 数据卡", pts, 30, [] if readme["exists"] else ["未提供 README"])
        desc = 12.0 if (signals.get("notebooks", 0) or signals.get("data_files", 0)) else 0.0
        lower = ""
        if readme["exists"]:
            lower = " ".join(readme.get("sections", []))
        desc += 8 if any(k in lower for k in ("usage", "docs", "config")) else 0
        components["data_desc"] = _comp(
            "数据说明", desc, 20, [] if desc >= 12 else ["缺少数据说明"])
        s_lic, n_lic = _license_score(signals, readme)
        components["license"] = _comp("许可证", s_lic * 15, 15, n_lic)
        components["scripts"] = _comp(
            "处理脚本", 20.0 if signals.get("source_files", 0) else 0.0, 20,
            [] if signals.get("source_files", 0) else ["未找到数据下载/预处理脚本"])
        s_sec, n_sec = _secrets_generic(
            [s for s in scan["secrets"] if not _is_non_leak(s)], 15)
        components["secrets"] = _comp("敏感信息", s_sec, 15, n_sec)
        for note in n_lic:
            risks.append(_note_risk(note, "许可证", "low"))

    result = _finish(
        {**archetype, "label": TYPE_LABELS.get(atype, atype)},
        components, risks, summary,
    )
    # 文档/测试里的密钥仍列出来，但已按低危展示，不影响分数
    return result
