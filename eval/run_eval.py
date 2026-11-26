"""50 仓库基准评测。

用法：
    python eval/run_eval.py                # 全量
    python eval/run_eval.py --limit 10     # 只跑前 N 个
    python eval/run_eval.py --only fastapi # URL 包含关键字的
    python eval/run_eval.py --refresh      # 忽略缓存重新克隆

克隆缓存到 eval/cache/<owner>__<repo>，重跑直接复用。
结果写入 eval/results.json。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.git_ops import (  # noqa: E402
    RepoError, check_repo_size, clone_repo, get_commit_sha, parse_repo_url,
)
from app.scanners import scan_repo  # noqa: E402
from app.scoring import build_score_and_risks  # noqa: E402

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(EVAL_DIR, "cache")
RESULTS_PATH = os.path.join(EVAL_DIR, "results.json")
REPOS_PATH = os.path.join(EVAL_DIR, "repos.json")


def _cache_path(owner: str, repo: str) -> str:
    safe = f"{owner}__{repo}".replace("/", "_")
    return os.path.join(CACHE_DIR, safe)


def _ensure_repo(entry: dict, refresh: bool) -> tuple[str, str, float, str | None]:
    """返回 (cache_dir, commit_sha, 克隆耗时, 错误)。"""
    url = entry["url"]
    info = parse_repo_url(url)
    dest = _cache_path(info["owner"], info["repo"])
    started = time.perf_counter()
    if os.path.isdir(dest) and not refresh and os.path.exists(os.path.join(dest, ".git")):
        sha = get_commit_sha(dest)
        return dest, sha, 0.0, None
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        clone_repo(info["clone_url"], info["branch"], dest)
        check_repo_size(dest)
        sha = get_commit_sha(dest)
        return dest, sha, time.perf_counter() - started, None
    except RepoError as e:
        # 失败时清掉半成品目录
        import shutil
        shutil.rmtree(dest, ignore_errors=True)
        return "", "", time.perf_counter() - started, str(e)
    except Exception as e:  # noqa: BLE001
        return "", "", time.perf_counter() - started, f"{type(e).__name__}: {e}"


def analyze_one(entry: dict, refresh: bool) -> dict:
    started = time.perf_counter()
    record = {
        "url": entry["url"],
        "category": entry.get("category"),
        "expect": entry.get("expect") or {},
        "expect_windows": entry.get("expect_windows"),
        "ok": False,
        "error": None,
    }
    repo_dir, sha, clone_sec, err = _ensure_repo(entry, refresh)
    record["clone_sec"] = round(clone_sec, 1)
    if err:
        record["error"] = err
        record["elapsed_sec"] = round(time.perf_counter() - started, 1)
        return record
    try:
        scan = scan_repo(repo_dir)
        score = build_score_and_risks(scan)
        record.update({
            "ok": True,
            "commit_sha": sha,
            "type": score["archetype"]["type"],
            "type_confidence": score["archetype"]["confidence"],
            "score": score["score"],
            "grade": score["grade"],
            "summary": score["summary"],
            "components": {
                k: {"score": v["score"], "max": v["max"]} for k, v in score["components"].items()
            },
            "top_risks": score["risks"][:8],
        })
    except Exception as e:  # noqa: BLE001
        record["error"] = f"{type(e).__name__}: {e}"
    record["elapsed_sec"] = round(time.perf_counter() - started, 1)
    return record


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round((len(values) - 1) * p)))
    return values[idx]


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r["ok"]]
    failed = [r for r in records if not r["ok"]]
    labeled = [r for r in ok if r["expect"].get("type") not in (None, "any")]
    type_correct = [r for r in labeled if r["type"] == r["expect"]["type"]]
    win_labeled = [r for r in ok if r.get("expect_windows") is not None]
    win_correct = [
        r for r in win_labeled
        if bool((r.get("summary") or {}).get("windows_scripts", 0)) == bool(r["expect_windows"])
    ]
    by_cat: dict[str, list[float]] = {}
    for r in ok:
        if r["score"] is not None:
            by_cat.setdefault(r["category"], []).append(r["score"])
    scored = [r["score"] for r in ok if r["score"] is not None]
    return {
        "total": len(records),
        "ok": len(ok),
        "failed": len(failed),
        "failures": [{"url": r["url"], "error": r["error"]} for r in failed],
        "type_accuracy": {
            "labeled": len(labeled),
            "correct": len(type_correct),
            "accuracy": round(len(type_correct) / len(labeled), 3) if labeled else None,
            "mismatches": [
                {"url": r["url"], "expected": r["expect"]["type"], "got": r["type"]}
                for r in labeled if r["type"] != r["expect"]["type"]
            ],
        },
        "windows_accuracy": {
            "labeled": len(win_labeled),
            "correct": len(win_correct),
            "accuracy": round(len(win_correct) / len(win_labeled), 3) if win_labeled else None,
        },
        "score_distribution": {
            "count": len(scored),
            "min": min(scored) if scored else None,
            "median": statistics.median(scored) if scored else None,
            "p90": _pct(scored, 0.9) if scored else None,
            "max": max(scored) if scored else None,
            "by_category": {
                k: {
                    "count": len(v),
                    "min": min(v),
                    "median": statistics.median(v),
                    "max": max(v),
                }
                for k, v in sorted(by_cat.items())
            },
        },
        "findings": {
            "real_secrets_total": sum((r.get("summary") or {}).get("secrets", 0) for r in ok),
            "high_conf_secrets_total": sum((r.get("summary") or {}).get("secrets_high", 0) for r in ok),
            "hardcoded_paths_total": sum((r.get("summary") or {}).get("hardcoded_paths", 0) for r in ok),
            "doc_findings_total": sum((r.get("summary") or {}).get("doc_findings", 0) for r in ok),
            "repos_with_secrets": [
                r["url"] for r in ok if (r.get("summary") or {}).get("secrets", 0) > 0
            ],
        },
        "timing": {
            "median_sec": statistics.median([r["elapsed_sec"] for r in records]) if records else 0,
            "max_sec": max((r["elapsed_sec"] for r in records), default=0),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", default=RESULTS_PATH)
    args = ap.parse_args()

    with open(REPOS_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    if args.only:
        entries = [e for e in entries if args.only.lower() in e["url"].lower()]
    if args.limit:
        entries = entries[: args.limit]

    started = time.time()
    records: list[dict] = []
    print(f"评测 {len(entries)} 个仓库，并发 {args.workers} ...\n")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyze_one, e, args.refresh): e for e in entries}
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            records.append(rec)
            name = rec["url"].replace("https://github.com/", "")
            if rec["ok"]:
                s = rec["score"]
                s_txt = f"{s:5.1f}" if s is not None else "  n/a"
                print(f"[{i:2}/{len(entries)}] {name:<45} {rec['type']:<8} {s_txt} "
                      f"secrets={rec['summary']['secrets']:<3} "
                      f"paths={rec['summary']['hardcoded_paths']:<3} "
                      f"win={rec['summary']['windows_scripts']} "
                      f"{rec['elapsed_sec']}s")
            else:
                print(f"[{i:2}/{len(entries)}] {name:<45} ERROR: {rec['error']}")

    records.sort(key=lambda r: (r["category"], r["url"]))
    summary = summarize(records)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "commit": os.environ.get("EVAL_COMMIT", ""),
        "summary": summary,
        "records": records,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("\n=== 汇总 ===")
    print(f"成功 {summary['ok']}/{summary['total']}，失败 {summary['failed']}")
    ta = summary["type_accuracy"]
    print(f"类型识别：{ta['correct']}/{ta['labeled']} = {ta['accuracy']}")
    wa = summary["windows_accuracy"]
    print(f"Windows 脚本检出：{wa['correct']}/{wa['labeled']} = {wa['accuracy']}")
    sd = summary["score_distribution"]
    print(f"分数分布：min={sd['min']} median={sd['median']} p90={sd['p90']} max={sd['max']}")
    for cat, d in sd["by_category"].items():
        print(f"  {cat:<10} n={d['count']:<3} min={d['min']:<6} median={d['median']:<6} max={d['max']}")
    fd = summary["findings"]
    print(f"真实密钥 {fd['real_secrets_total']}（高危 {fd['high_conf_secrets_total']}），"
          f"硬编码路径 {fd['hardcoded_paths_total']}，文档示例 {fd['doc_findings_total']}")
    if summary["failures"]:
        print("\n失败项：")
        for f in summary["failures"]:
            print(f"  {f['url']} -> {f['error']}")
    print(f"\n总耗时 {time.time() - started:.1f}s，结果写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
