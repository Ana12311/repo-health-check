"""请求/响应 schema。"""
from __future__ import annotations

from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    repo_url: str


class AnalyzeResponse(BaseModel):
    ok: bool
    repo_url: str
    owner: str | None = None
    repo: str | None = None
    commit_sha: str | None = None
    error: str | None = None
    scan: dict | None = None
    score: dict | None = None
    ai: dict | None = None
