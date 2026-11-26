"""与业界工具 gitleaks 对比：注入召回率 + 真实仓库噪音量。

用法：
    python eval/compare_gitleaks.py

依赖 eval/tools/gitleaks.exe（脚本不含下载逻辑）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.scanners import scan_repo  # noqa: E402
from app.scoring import build_score_and_risks, _is_non_leak  # noqa: E402
from recall_test import BASES, make_defects, prepare_copy  # noqa: E402

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
GITLEAKS = os.path.join(EVAL_DIR, "tools", "gitleaks.exe")
CACHE = os.path.join(EVAL_DIR, "cache")
OUT = os.path.join(EVAL_DIR, "compare_results.json")

NOISE_REPOS = [
    "pallets__flask", "tiangolo__fastapi", "gin-gonic__gin", "BurntSushi__ripgrep",
    "axios__axios", "django__django", "gitleaks__gitleaks", "trufflesecurity__trufflehog",
]


def run_gitleaks(path: str) -> tuple[list[dict], float]:
    report = os.path.join(EVAL_DIR, "_gl_tmp.json")
    started = time.perf_counter()
    subprocess.run(
        [GITLEAKS, "dir", path, "-f", "json", "-r", report,
         "--exit-code", "0", "--no-banner", "-l", "error"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    elapsed = time.perf_counter() - started
    try:
        with open(report, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = []
    finally:
        try:
            os.remove(report)
        except OSError:
            pass
    return data or [], elapsed


def rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def recall_part() -> dict:
    all_defects = make_defects()  # 只生成一次，注入与判定用同一批 token
    defects = [d for d in all_defects if d["type"] == "secret" and d["id"] != "secret_env"]
    rows = []
    for base in BASES:
        tmp = prepare_copy(base, all_defects)
        try:
            repo = os.path.join(tmp, "repo")
            gl, gl_sec = run_gitleaks(repo)
            scan = scan_repo(repo)
            ours = [(rel(os.path.join(repo, s["file"]), repo), s["line"], s["match"])
                    for s in scan["secrets"]]
            gl_norm = [(rel(x.get("File", ""), repo), x.get("StartLine", 0),
                        x.get("Secret") or x.get("Match") or "")
                       for x in gl]
            for d in defects:
                needle = d["needle"]
                ours_hit = any(needle in m for _, _, m in ours)
                gl_hit = any(needle in m for _, _, m in gl_norm)
                rows.append({"base": base, "id": d["id"], "ours": ours_hit,
                             "gitleaks": gl_hit})
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    n = len(rows)
    return {
        "injections": n,
        "ours_detected": sum(1 for r in rows if r["ours"]),
        "gitleaks_detected": sum(1 for r in rows if r["gitleaks"]),
        "rows": rows,
    }


def noise_part() -> dict:
    out = []
    for name in NOISE_REPOS:
        repo = os.path.join(CACHE, name)
        if not os.path.isdir(repo):
            continue
        gl, gl_sec = run_gitleaks(repo)
        t0 = time.perf_counter()
        scan = scan_repo(repo)
        sc = build_score_and_risks(scan)
        ours_sec = time.perf_counter() - t0

        ours_all = {}
        for idx, s in enumerate(scan["secrets"]):
            key = (s["file"], s["line"])
            ours_all.setdefault(key, []).append(s)
        ours_real = [s for s in scan["secrets"] if not _is_non_leak(s)]
        gl_keys = {(rel(x.get("File", ""), repo), x.get("StartLine", 0)) for x in gl}
        overlap = sum(1 for k in gl_keys if k in ours_all)
        out.append({
            "repo": name,
            "gitleaks_findings": len(gl),
            "gitleaks_seconds": round(gl_sec, 1),
            "ours_findings_total": len(scan["secrets"]),
            "ours_real": len(ours_real),
            "ours_doc_or_sample": len(scan["secrets"]) - len(ours_real),
            "overlap_file_line": overlap,
            "ours_seconds": round(ours_sec, 1),
            "gitleaks_sample": [
                {"rule": x.get("RuleID"), "file": x.get("File"), "line": x.get("StartLine"),
                 "secret": (x.get("Secret") or "")[:40]}
                for x in gl[:5]
            ],
        })
    return {
        "repos": out,
        "total_gitleaks": sum(r["gitleaks_findings"] for r in out),
        "total_ours_real": sum(r["ours_real"] for r in out),
        "total_ours_doc": sum(r["ours_doc_or_sample"] for r in out),
    }


def main() -> int:
    if not os.path.isfile(GITLEAKS):
        print(f"缺少 {GITLEAKS}")
        return 1
    print("== 注入召回对比 ==")
    rec = recall_part()
    print(f"注入 {rec['injections']} 次：ours {rec['ours_detected']} / "
          f"gitleaks {rec['gitleaks_detected']}")
    for r in rec["rows"]:
        if not r["gitleaks"]:
            print(f"  gitleaks 漏检: {r['base']} {r['id']}")
        if not r["ours"]:
            print(f"  ours 漏检: {r['base']} {r['id']}")

    print("\n== 真实仓库噪音量 ==")
    noise = noise_part()
    for r in noise["repos"]:
        print(f"{r['repo']:<32} gitleaks={r['gitleaks_findings']:<4} "
              f"ours_real={r['ours_real']:<3} ours_doc={r['ours_doc_or_sample']:<4} "
              f"overlap={r['overlap_file_line']:<3} "
              f"time gl={r['gitleaks_seconds']}s ours={r['ours_seconds']}s")
    print(f"合计: gitleaks {noise['total_gitleaks']} 条 / "
          f"ours 真实 {noise['total_ours_real']} 条（文档示例 {noise['total_ours_doc']} 条）")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"recall": rec, "noise": noise}, f, ensure_ascii=False, indent=2)
    print(f"\n结果写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
