"""注入缺陷测召回率。

往真实仓库的副本里注入已知缺陷（密钥 / 路径 / Windows 脚本），统计规则层检出率，
以及 Agent 复核后是否仍保留（不被误判为误报）。

用法：
    python eval/recall_test.py            # 规则层
    python eval/recall_test.py --agent    # 同时测 Agent 复核后的存活率（消耗 API）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import string
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.scanners import scan_repo  # noqa: E402
from app.scoring import build_score_and_risks, _is_non_leak  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
BASES = ["pallets__flask", "Ana12311__TaskFlow", "elementalsouls__Claude-BugHunter"]
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall_results.json")

rnd = random.Random(42)
ALNUM = string.ascii_letters + string.digits


def tok(n: int, alphabet: str = ALNUM) -> str:
    return "".join(rnd.choice(alphabet) for _ in range(n))


def has_digit_and_alpha(s: str) -> bool:
    return any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


def make_defects() -> list[dict]:
    """每条缺陷：id / type / file / content / needle（用于判断命中）。"""
    d: list[dict] = []

    def add(did, typ, rel, content, needle):
        d.append({"id": did, "type": typ, "file": rel, "content": content, "needle": needle})

    openai = "sk-proj-" + tok(28, ALNUM + "-_")
    add("secret_openai", "secret", "src/service/keys.py",
        f'OPENAI_API_KEY = "{openai}"\n', openai)

    gh = "ghp_" + tok(36, string.ascii_letters + string.digits)
    add("secret_github", "secret", "src/service/keys.py",
        f'GITHUB_TOKEN = "{gh}"\n', gh)

    aws = "AKIA" + tok(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    add("secret_aws", "secret", "src/service/keys.py",
        f'AWS_ACCESS_KEY_ID = "{aws}"\n', aws)

    akid = "AKID" + tok(10, string.ascii_letters + string.digits)
    add("secret_tencent", "secret", "src/service/keys.py",
        f'TENCENT_SECRET_ID = "{akid}"\n', akid)

    slack = "xoxb-" + tok(10, string.digits) + "-" + tok(12, ALNUM)
    add("secret_slack", "secret", "src/service/keys.py",
        f'SLACK_BOT_TOKEN = "{slack}"\n', slack)

    glpat = "glpat-" + tok(20, ALNUM + "-_")
    add("secret_gitlab", "secret", "src/service/keys.py",
        f'GITLAB_TOKEN = "{glpat}"\n', glpat)

    aiza = "AIza" + tok(35, ALNUM + "-_")
    add("secret_google", "secret", "src/service/keys.py",
        f'GOOGLE_API_KEY = "{aiza}"\n', aiza)

    hf = "hf_" + tok(34, ALNUM)
    add("secret_hf", "secret", "src/service/keys.py",
        f'HUGGINGFACE_TOKEN = "{hf}"\n', hf)

    npm = "npm_" + tok(36, ALNUM)
    add("secret_npm", "secret", "src/service/keys.py",
        f'NPM_TOKEN = "{npm}"\n', npm)

    ant = "sk-ant-" + tok(24, ALNUM + "-_")
    add("secret_anthropic", "secret", "src/service/keys.py",
        f'ANTHROPIC_API_KEY = "{ant}"\n', ant)

    pem_body = "\n".join(tok(64) for _ in range(3))
    add("secret_pem", "secret", "deploy/keys/id_deploy",
        f"-----BEGIN RSA PRIVATE KEY-----\n{pem_body}\n-----END RSA PRIVATE KEY-----\n",
        "-----BEGIN RSA PRIVATE KEY-----")

    generic = "Xk9mQ2vLp7Rt4Zw8Nf3"
    add("secret_generic", "secret", "src/service/db.py",
        f'DB_PASSWORD = "{generic}"\n', generic)

    add("secret_env", "secret", ".env",
        "APP_ENV=production\nDB_HOST=10.0.0.5\n", ".env")

    cwin = "C:\\Users\\alice\\work\\data\\input.csv"
    add("path_win_users", "path", "src/util/paths.py",
        f'INPUT = r"{cwin}"\n', cwin)

    unixhome = "/home/alice/project/dataset"
    add("path_unix_home", "path", "src/util/paths.py",
        f'DATA_DIR = "{unixhome}"\n', unixhome)

    dpath = "D:\\build\\output\\app.exe"
    add("path_script_high", "path", "scripts/release.ps1",
        f"$out = '{dpath}'\nWrite-Host $out\n", dpath)

    opt = "/opt/appdata/config/runtime.yml"
    add("path_opt", "path", "config/runtime.yml",
        f"path: {opt}\n", opt)

    add("win_bat", "windows", "scripts/run.bat", "@echo off\necho run\n", "scripts/run.bat")
    add("win_ps1", "windows", "scripts/setup.ps1", "Write-Host setup\n", "scripts/setup.ps1")

    # 文档 / 测试目录中的"缺陷"：应检出但不计入评分
    doc_key = "sk-proj-" + tok(28, ALNUM + "-_")
    add("doc_secret", "doc_decoy", "docs/guide.md",
        f"示例：OPENAI_API_KEY={doc_key}\n", doc_key)
    test_key = "AKIA" + tok(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    add("test_secret", "doc_decoy", "tests/test_config.py",
        f'FAKE_AWS_KEY = "{test_key}"\n', test_key)
    return d


def prepare_copy(base: str, defects: list[dict]) -> str:
    src = os.path.join(CACHE, base)
    tmp = tempfile.mkdtemp(prefix="recall-")
    dst = os.path.join(tmp, "repo")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"))
    for d in defects:
        p = os.path.join(dst, d["file"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "a" if os.path.exists(p) else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(("\n" if mode == "a" else "") + d["content"])
    return tmp


def evaluate(base: str, defects: list[dict], agent: bool) -> dict:
    tmp = prepare_copy(base, defects)
    try:
        scan = scan_repo(os.path.join(tmp, "repo"))
        score = build_score_and_risks(scan)

        secret_text = [(s["file"], s["line"], s["match"], s["kind"], s["confidence"])
                       for s in scan["secrets"]]
        path_text = [(p["file"], p["line"], p["match"], p["severity"])
                     for p in scan["hardcoded_paths"]]
        wins = set(scan["windows_scripts"])

        rows = []
        for d in defects:
            needle = d["needle"]
            if d["type"] == "secret":
                # needle 全局唯一，直接全文匹配
                hit = any(needle in m for _, _, m, _, _ in secret_text)
                conf = next((c for f, _, m, _, c in secret_text if needle in m), None)
            elif d["type"] == "path":
                hit = any(needle in m for _, _, m, _ in path_text)
                conf = next((s for f, _, m, s in path_text if needle in m), None)
            elif d["type"] == "windows":
                hit = d["file"] in wins
                conf = "present" if hit else None
            else:  # doc_decoy
                allf = secret_text + path_text
                hit = any(needle in row[2] for row in allf)
                conf = "doc" if hit else None
            rows.append({"id": d["id"], "type": d["type"], "file": d["file"],
                         "detected": bool(hit), "confidence": conf})

        agent_result = None
        if agent:
            from app.agent import run_agent
            ai = run_agent("local-recall", os.path.join(tmp, "repo"), scan, score)
            if ai and ai.get("verdicts"):
                final = build_score_and_risks(scan, ai["verdicts"])
                # 检查：被判误报的候选是否正好是注入缺陷
                cand = {}
                from app.agent import collect_candidates
                for c in collect_candidates(os.path.join(tmp, "repo"), scan):
                    cand[c["id"]] = c
                fp_cands = [cand[k] for k, v in ai["verdicts"].items()
                            if v["verdict"] == "false_positive" and k in cand]
                fp_needles = [c["match"] for c in fp_cands]
                for r in rows:
                    d = next(x for x in defects if x["id"] == r["id"])
                    if r["detected"] and any(d["needle"] in m for m in fp_needles):
                        r["downgraded_by_agent"] = True
                    else:
                        r["downgraded_by_agent"] = False
                agent_result = {"reviewed": ai.get("reviewed"), "verdict": ai.get("verdict")}

        return {"base": base, "rows": rows, "score": score["score"],
                "agent": agent_result}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--out", default=RESULT_PATH)
    args = ap.parse_args()

    defects = make_defects()
    results = [evaluate(b, defects, args.agent) for b in BASES]

    # 汇总
    by_id: dict[str, list] = {}
    for res in results:
        for r in res["rows"]:
            by_id.setdefault(r["id"], []).append(r)
    total = len(by_id) * len(results)
    detected = sum(1 for rs in by_id.values() for r in rs if r["detected"])
    scored_defects = [i for i, rs in by_id.items() if rs[0]["type"] != "doc_decoy"]
    scored_detected = sum(1 for i in scored_defects for r in by_id[i] if r["detected"])
    scored_total = len(scored_defects) * len(results)
    doc_rows = [r for rs in by_id.values() for r in rs if r["type"] == "doc_decoy"]

    summary = {
        "repos": len(results),
        "defect_kinds": len(by_id),
        "injections": total,
        "detected": detected,
        "recall_all": round(detected / total, 3) if total else None,
        "recall_scored_defects": round(scored_detected / scored_total, 3) if scored_total else None,
        "misses": [{"base": res["base"], **r} for res in results for r in res["rows"]
                   if not r["detected"]],
    }
    if args.agent:
        downgraded = [{"base": res["base"], **r} for res in results
                      for r in res["rows"] if r.get("downgraded_by_agent")]
        summary["agent_downgraded_injected"] = downgraded

    payload = {"summary": summary, "results": results}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"注入 {summary['injections']} 次（{summary['defect_kinds']} 类 × {len(results)} 仓库）")
    print(f"总检出率: {summary['recall_all']}")
    print(f"计分缺陷检出率: {summary['recall_scored_defects']}")
    if summary["misses"]:
        print("漏报：")
        for m in summary["misses"]:
            print(f"  {m['base']} {m['id']} {m['file']}")
    if args.agent:
        dg = summary.get("agent_downgraded_injected") or []
        print(f"Agent 误降级注入缺陷: {len(dg)}")
        for x in dg:
            print(f"  {x['base']} {x['id']} {x['file']}")
    print(f"结果写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
