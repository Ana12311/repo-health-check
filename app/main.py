"""FastAPI 入口：GET / 返回页面，POST /api/analyze 执行体检。"""
from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent import run_agent
from .ai import has_api_key, set_runtime_key
from .git_ops import (
    RepoError, check_repo_size, cleanup_dir, clone_repo, get_commit_sha,
    make_temp_dir, parse_repo_url,
)
from .models import AnalyzeRequest
from .scanners import scan_repo
from .scoring import build_score_and_risks

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="Git 仓库接入体检", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 限制并发分析数量，避免多请求同时克隆/扫描把机器打满
_ANALYZE_SEM = threading.Semaphore(3)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/status")
def status():
    return {"key_configured": has_api_key()}


@app.post("/api/set-key")
def set_key(req: dict):
    key = (req or {}).get("api_key", "")
    set_runtime_key(key)
    return {"key_configured": has_api_key()}


def _scan_options() -> dict:
    """从环境变量读取深度扫描开关（默认全开，历史 50 个 commit）。"""
    def flag(name: str, default: bool = True) -> bool:
        v = os.environ.get(name)
        if v is None:
            return default
        return v.strip().lower() not in ("0", "false", "no", "off")

    try:
        history = int(os.environ.get("REPO_HEALTH_HISTORY_DEPTH", "50"))
    except ValueError:
        history = 50
    return {
        "archives": flag("REPO_HEALTH_ARCHIVES"),
        "decode": flag("REPO_HEALTH_DECODE"),
        "gitleaks": flag("REPO_HEALTH_GITLEAKS"),
        "custom_rules": flag("REPO_HEALTH_CUSTOM_RULES"),
        "history": max(0, history),
    }


def _analyze(repo_url: str) -> dict:
    info = parse_repo_url(repo_url)
    opts = _scan_options()
    tmp = None
    try:
        tmp = make_temp_dir()
        repo_dir = os.path.join(tmp, "repo")
        clone_repo(info["clone_url"], info["branch"], repo_dir,
                   depth=opts["history"] if opts["history"] > 0 else 1)
        check_repo_size(repo_dir)
        commit_sha = get_commit_sha(repo_dir)
        scan = scan_repo(repo_dir, opts)
        # 第一层：确定性规则评分
        provisional = build_score_and_risks(scan)
        # 第二层：Agent 复核中低置信发现 + 输出接入建议（无 key 时为 None，保持纯规则结果）
        ai = run_agent(repo_url, repo_dir, scan, provisional)
        if ai and ai.get("verdicts"):
            score = build_score_and_risks(scan, ai["verdicts"])
            score["summary"]["agent_reviewed"] = ai.get("reviewed", 0)
        else:
            score = provisional
        return {
            "ok": True,
            "repo_url": repo_url,
            "owner": info["owner"],
            "repo": info["repo"],
            "commit_sha": commit_sha,
            "scan": scan,
            "score": score,
            "ai": ai,
        }
    finally:
        if tmp:
            cleanup_dir(tmp)


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    started = time.perf_counter()
    try:
        parse_repo_url(req.repo_url)
    except RepoError as e:
        return JSONResponse(status_code=400, content={
            "ok": False, "repo_url": req.repo_url, "error": str(e)})

    acquired = _ANALYZE_SEM.acquire(timeout=180)
    if not acquired:
        return JSONResponse(status_code=429, content={
            "ok": False, "repo_url": req.repo_url, "error": "服务器繁忙，请稍后重试"})
    try:
        result = _analyze(req.repo_url)
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return result
    except RepoError as e:
        return JSONResponse(status_code=400, content={
            "ok": False, "repo_url": req.repo_url, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "repo_url": req.repo_url, "error": f"分析失败: {e}"})
    finally:
        _ANALYZE_SEM.release()
