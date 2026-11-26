"""Git 仓库克隆与 URL 解析。带超时、大小上限、错误中文化。"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile

_GITHUB_RE = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_BRANCH_RE = re.compile(r"/(?:tree|blob|commits)/([A-Za-z0-9_.\-/]+)")

CLONE_TIMEOUT = 120
MAX_FILES = 100_000


class RepoError(Exception):
    """仓库解析 / 克隆失败。"""


def parse_repo_url(url: str) -> dict:
    """解析 GitHub URL，返回 {owner, repo, branch, clone_url}。"""
    url = (url or "").strip()
    if not url:
        raise RepoError("仓库链接为空")

    m = _GITHUB_RE.search(url)
    if not m:
        raise RepoError("无法解析：仅支持 GitHub 公开仓库链接")

    owner, repo = m.group(1), m.group(2)
    repo = repo.removesuffix(".git")

    branch = None
    bm = _BRANCH_RE.search(url)
    if bm:
        branch = bm.group(1).split("/")[0]

    clone_url = f"https://github.com/{owner}/{repo}.git"
    return {"owner": owner, "repo": repo, "branch": branch, "clone_url": clone_url}


def _friendly_git_error(stderr: str) -> str:
    low = stderr.lower()
    if "repository not found" in low or "not found" in low:
        return "仓库不存在，或为私有仓库（仅支持公开仓库）"
    if "could not resolve host" in low or "failed to connect" in low:
        return "网络无法访问 GitHub，请检查网络或代理"
    if "authentication failed" in low or "could not read username" in low or "permission denied" in low:
        return "私有仓库需要认证，本工具仅支持公开仓库"
    if "empty repository" in low:
        return "仓库为空（没有提交）"
    tail = stderr.strip().splitlines()
    return tail[-1] if tail else "克隆失败"


def clone_repo(clone_url: str, branch: str | None, dest: str, depth: int = 1) -> None:
    """浅克隆 + 部分克隆（跳过 >1MB 大文件）到 dest。失败抛 RepoError。

    depth > 1 时会拉取最近 depth 个 commit，供历史泄露扫描使用。
    """
    cmd = [
        "git", "clone", f"--depth={max(1, depth)}", "--single-branch", "--no-tags",
        "-c", "core.longpaths=true",
        "-c", "core.autocrlf=false",
    ]
    if depth <= 1:
        # 历史扫描需要真实 blob，只有纯 HEAD 扫描时才启用部分克隆
        cmd.append("--filter=blob:limit=1m")
    if branch:
        cmd += ["--branch", branch]
    cmd += [clone_url, dest]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLONE_TIMEOUT,
            cwd=os.path.dirname(dest) or ".",
        )
    except subprocess.TimeoutExpired:
        raise RepoError("克隆超时：仓库过大或网络缓慢，请换用更小的仓库")

    if proc.returncode != 0:
        raise RepoError(_friendly_git_error(proc.stderr or proc.stdout or ""))


def check_repo_size(repo_dir: str) -> None:
    """文件数过多时提前失败，避免扫描卡死。"""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "ls-files"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except subprocess.TimeoutExpired:
        return
    if proc.returncode != 0:
        return
    count = len(proc.stdout.splitlines())
    if count > MAX_FILES:
        raise RepoError(f"仓库文件过多（{count} 个），超出分析上限，请换用更小的仓库")


def get_commit_sha(repo_dir: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def make_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="repo-check-")


def _force_remove(func, path, exc_info):  # noqa: ANN001
    """Windows 只读文件清理兜底。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def cleanup_dir(path: str) -> None:
    if path and os.path.isdir(path):
        shutil.rmtree(path, onerror=_force_remove)
