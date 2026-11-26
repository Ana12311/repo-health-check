"""检测器 + 类型识别 + 评分 单元测试。全部离线，用手造仓库。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.classify import classify_repo  # noqa: E402
from app.scanners import scan_repo  # noqa: E402
from app.scoring import build_score_and_risks  # noqa: E402



# 假 token 拆开拼接：避免源码里出现完整密钥字面量（GitHub push protection 会拦截）
_T_OPENAI = "sk-proj-" + "Xk9mQ2vLp7Rt4ZwKd8Nf3Hs6Jb1Gy5"
_T_GH = "ghp_" + "Z8kR2mQ9vLp7Rt4Zw8Nf6Hs1Jb5Gy0Cd2Ef4"
_T_AWS = "AKIA" + "Q7Z2M3XR4V5W6Y7Z"
_T_TENCENT = "AKID" + "Z8kR2mQ9vLp7Rt4Zw8N"
_T_GITLAB = "glpat-" + "Xk9mQ2vLp7Rt4Zw8Nf6Hs"
_T_SLACK = "xoxb-" + "9823746510-Q2vLpR8tZw4Xm9"
_PEM_BEGIN = "-----BEGIN RSA " + "PRIVATE KEY-----"
_PEM_END = "-----END RSA " + "PRIVATE KEY-----"

def _write(root, rel, content):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


READMES = {
    "clean_app": "README.md",
}


def _clean_app(tmp_path):
    _write(tmp_path, "README.md", (
        "# Demo App\n\nA demo application.\n\n"
        "## Installation\n\n```bash\nnpm install\n```\n\n"
        "## Usage\n\n```bash\nnpm start\n```\n\n"
        "## License\n\nMIT\n\n" + "More details about the project. " * 40
    ))
    _write(tmp_path, "package.json", json.dumps({
        "name": "demo", "scripts": {"start": "node index.js", "test": "jest"},
        "dependencies": {"express": "^4", "lodash": "^4"},
        "devDependencies": {"jest": "^29"},
    }))
    _write(tmp_path, "package-lock.json", "{}")
    _write(tmp_path, "index.js", "console.log('hi')\n")
    _write(tmp_path, "src/util.js", "module.exports = 1\n")
    _write(tmp_path, "src/other.js", "module.exports = 2\n")
    _write(tmp_path, "Dockerfile", "FROM node:20\nCMD ['npm','start']\n")
    _write(tmp_path, "build.bat", "@echo off\nnpm run build\n")


def _leaky_app(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"name": "leaky", "dependencies": {"x": "1"}}))
    _write(tmp_path, "main.py", 'PATH = "C:\\\\Users\\\\bob\\\\data"\nprint(1)\n')
    _write(tmp_path, "config.py",
           f'API_KEY = "{_T_OPENAI}"\n'
           'DB_PASSWORD = "Xk9#mQ2vLp7Rt4Zw"\n')
    _write(tmp_path, "utils.py", "def f():\n    return 1\n")
    _write(tmp_path, ".env", "SECRET_KEY=Xk9mQ2vLp7Rt4ZwAbCdEf12\n")


def _docs_repo(tmp_path):
    _write(tmp_path, "README.md", (
        "# Awesome List\n\n" + "\n".join(f"- item {i}" for i in range(40)) + "\n\n"
        "## Contents\n\n- [A](docs/a.md)\n- [B](docs/b.md)\n- [C](docs/c.md)\n\n"
        "```bash\nexample\n```\n\n" + "Some explanatory text. " * 100
    ))
    for name in ["a", "b", "c", "d", "e"]:
        _write(tmp_path, f"docs/{name}.md", f"# {name}\n\ncontent\n")
    _write(tmp_path, "LICENSE", "MIT\n")


def _data_repo(tmp_path):
    _write(tmp_path, "README.md", "# Dataset\n\n## Usage\n\nLoad the csv.\n" + "words " * 100)
    for name in ("analysis", "clean", "explore"):
        _write(tmp_path, f"{name}.ipynb", json.dumps({"cells": [], "metadata": {}}))
    for name in ("train", "test", "val", "sample", "extra"):
        _write(tmp_path, f"data/{name}.csv", "a,b\n1,2\n")


def _windows_repo(tmp_path):
    _write(tmp_path, "README.md", "# Win Tool\n\n## Install\n\ndownload\n\n## Usage\n\nrun\n")
    _write(tmp_path, "tool.ps1", "Write-Host hi\n")
    _write(tmp_path, "run.bat", "@echo off\necho hi\n")
    _write(tmp_path, "src/a.cs", "class A {}\n")
    _write(tmp_path, "src/b.cs", "class B {}\n")
    _write(tmp_path, "src/c.cs", "class C {}\n")
    _write(tmp_path, "App.csproj", "<Project />\n")


# --- README ---

def test_readme_detection(tmp_path):
    _write(tmp_path, "README.md", "# p\n\n## Install\nx\n\n## Usage\nx\n")
    scan = scan_repo(str(tmp_path))
    assert scan["readme"]["exists"] is True
    assert "install" in scan["readme"]["sections"]
    assert "usage" in scan["readme"]["sections"]


def test_readme_extensionless(tmp_path):
    _write(tmp_path, "README", "# p\n\n## 安装\nx\n\n## 使用\nx\n")
    scan = scan_repo(str(tmp_path))
    assert scan["readme"]["exists"] is True
    assert scan["readme"]["path"] == "README"


def test_readme_sections_use_headings_not_prose(tmp_path):
    # 正文里出现 install 字样，但标题没有 → 不应算作安装章节
    _write(tmp_path, "README.md", "# p\n\nYou can install it somehow.\n\n## Features\n\nstuff\n")
    scan = scan_repo(str(tmp_path))
    assert "install" not in scan["readme"]["sections"]


# --- 依赖 ---

def test_deps_multiple_manifests(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"dependencies": {"a": "1", "b": "2"}}))
    _write(tmp_path, "requirements.txt", "# c\nflask\nrequests\n")
    _write(tmp_path, "yarn.lock", "")
    scan = scan_repo(str(tmp_path))
    assert scan["deps"]["count"] == 2
    assert {m["type"] for m in scan["deps"]["manifests"]} == {"npm", "pip"}
    assert "yarn.lock" in scan["deps"]["lockfiles"]


def test_deps_count_various(tmp_path):
    _write(tmp_path, "Cargo.toml", '[dependencies]\nserde = "1"\nregex = "1"\n\n[dev-dependencies]\n')
    _write(tmp_path, "Gemfile", 'source "x"\ngem "rails"\ngem "puma"\n')
    _write(tmp_path, "pom.xml", "<dependency>a</dependency><dependency>b</dependency>")
    scan = scan_repo(str(tmp_path))
    by_base = {m["path"]: m["count"] for m in scan["deps"]["manifests"]}
    assert by_base["Cargo.toml"] == 2
    assert by_base["Gemfile"] == 2
    assert by_base["pom.xml"] == 2


# --- 启动 ---

def test_startup_scripts_full_credit(tmp_path):
    _write(tmp_path, "Makefile", "all:\n\techo hi\n")
    _write(tmp_path, "run.sh", "echo hi\n")
    scan = scan_repo(str(tmp_path))
    assert scan["startup"]["has_script_evidence"] is True
    assert scan["startup"]["has_ci_only"] is False


def test_startup_ci_only_partial(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", "on: push\njobs: {}\n")
    scan = scan_repo(str(tmp_path))
    assert scan["startup"]["has_script_evidence"] is False
    assert scan["startup"]["has_ci_only"] is True


def test_entry_candidates(tmp_path):
    _write(tmp_path, "main.py", "print(1)\n")
    _write(tmp_path, "cmd/server/main.go", "package main\n")
    scan = scan_repo(str(tmp_path))
    assert "main.py" in scan["startup"]["entries"]
    assert "cmd/server/main.go" in scan["startup"]["entries"]


# --- Windows ---

def test_windows_scripts(tmp_path):
    _write(tmp_path, "build.bat", "@echo off\n")
    _write(tmp_path, "deploy.ps1", "Write-Host hi\n")
    _write(tmp_path, "run.cmd", "echo hi\n")
    _write(tmp_path, "auto.vbs", "MsgBox 1\n")
    scan = scan_repo(str(tmp_path))
    assert set(scan["windows_scripts"]) == {"auto.vbs", "build.bat", "deploy.ps1", "run.cmd"}


# --- 硬编码路径 ---

def test_hardcoded_paths(tmp_path):
    _write(tmp_path, "config.py",
           'PATH = "C:\\Program Files\\app"\nHOME = "/Users/bob/data"\nROOT = "/usr/local/bin"\n')
    scan = scan_repo(str(tmp_path))
    kinds = {f["kind"] for f in scan["hardcoded_paths"]}
    assert "Windows 绝对路径" in kinds
    assert "Unix 用户目录绝对路径" in kinds
    assert len(scan["hardcoded_paths"]) >= 3


def test_hardcoded_paths_ignore_urls_and_volumes(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           'image: http://ai-service:8000\n  base: https://api.deepseek.com\n  vol: mysql_data:/var/lib/mysql\n')
    _write(tmp_path, "config.py", 'BASE = "https://example.com/path"\n')
    scan = scan_repo(str(tmp_path))
    assert scan["hardcoded_paths"] == []


def test_hardcoded_paths_ignore_container_and_placeholders(tmp_path):
    _write(tmp_path, "app.py",
           'A = "/app/config.yml"\nB = "/workspace/x"\nC = "C:\\\\Users\\\\<name>\\\\x"\n'
           'D = "/home/user/project"\n')
    scan = scan_repo(str(tmp_path))
    assert scan["hardcoded_paths"] == []


def test_hardcoded_paths_severity(tmp_path):
    _write(tmp_path, "run.bat", "cd C:\\tools\\bin\n")
    _write(tmp_path, "app.py", "# see C:\\tools\\bin\nPATH = 'D:\\data\\x'\n")
    scan = scan_repo(str(tmp_path))
    bat = [f for f in scan["hardcoded_paths"] if f["file"] == "run.bat"]
    py_comment = [f for f in scan["hardcoded_paths"] if f["file"] == "app.py" and f["line"] == 1]
    py_code = [f for f in scan["hardcoded_paths"] if f["file"] == "app.py" and f["line"] == 2]
    assert bat and bat[0]["severity"] == "high"
    assert py_comment and py_comment[0]["severity"] == "low"
    assert py_code and py_code[0]["severity"] == "medium"


# --- 密钥 ---

def test_secrets_provider_patterns(tmp_path):
    _write(tmp_path, "keys.py", (
        f'A = "{_T_GH}"\n'
        f'B = "{_T_TENCENT}"\n'
        f'C = "{_T_AWS}"\n'
        f'D = "{_T_GITLAB}"\n'
        f'E = "{_T_SLACK}"\n'
    ))
    scan = scan_repo(str(tmp_path))
    kinds = " | ".join(f["kind"] for f in scan["secrets"])
    assert "GitHub Token" in kinds
    assert "Tencent Cloud Key" in kinds
    assert "AWS Access Key" in kinds
    assert "GitLab PAT" in kinds
    assert "Slack Token" in kinds
    assert all(f["confidence"] == "high" for f in scan["secrets"])


def test_secrets_pem_requires_end_and_env(tmp_path):
    _write(tmp_path, ".env", "SECRET_KEY=Xk9mQ2vLp7Rt4ZwAbCdEf12\n")
    _write(tmp_path, "id_rsa",
           f"{_PEM_BEGIN}\nMIIabc\n{_PEM_END}\n")
    _write(tmp_path, "scanner.py", f'PATTERN = "{_PEM_BEGIN}"\n')
    scan = scan_repo(str(tmp_path))
    files = {f["file"] for f in scan["secrets"]}
    assert ".env" in files
    assert "id_rsa" in files
    assert "scanner.py" not in files  # 裸 PEM 头不算


def test_secrets_skip_placeholders(tmp_path):
    _write(tmp_path, "README.md",
           'JWT_SECRET="至少32字节的随机串"\nPASSWORD="123456"\nAPI_KEY="<your-key-here>"\n'
           'TOKEN_TYPE = "Bearer"\nREFRESH_TOKEN = "auth:refresh:"\n')
    scan = scan_repo(str(tmp_path))
    assert scan["secrets"] == []


def test_secrets_skip_known_fake(tmp_path):
    _write(tmp_path, "notes.md", f"AKIA{'IOSFODNN7EXAMPLE'}\nwJalrXUtnFEMI\n")
    scan = scan_repo(str(tmp_path))
    assert scan["secrets"] == []


def test_secrets_detect_prefixed_credential(tmp_path):
    _write(tmp_path, "config.py",
           'SECRET_KEY = "Xk9mQ2vLp7Rt4Zw"\nDB_PASSWORD = "pX9mQ2vLp7Rt4Zw"\n')
    scan = scan_repo(str(tmp_path))
    lines = {f["line"] for f in scan["secrets"]}
    assert lines == {1, 2}


def test_secrets_skip_example_files(tmp_path):
    _write(tmp_path, ".env.example", "API_KEY=aaaaaaaaaaaaaaaaaaaa\n")
    _write(tmp_path, "config.py", 'API_KEY = "aX9mQ2vLp7Rt4ZwKd8N"\n')
    scan = scan_repo(str(tmp_path))
    assert all(f["file"] != ".env.example" for f in scan["secrets"])


# --- 类型识别 ---

def test_classify_software(tmp_path):
    _clean_app(tmp_path)
    scan = scan_repo(str(tmp_path))
    assert classify_repo(scan["signals"])["type"] == "software"


def test_classify_docs(tmp_path):
    _docs_repo(tmp_path)
    scan = scan_repo(str(tmp_path))
    assert classify_repo(scan["signals"])["type"] == "docs"


def test_classify_data(tmp_path):
    _data_repo(tmp_path)
    scan = scan_repo(str(tmp_path))
    assert classify_repo(scan["signals"])["type"] == "data"


def test_classify_empty(tmp_path):
    _write(tmp_path, "x.txt", "hi\n")
    scan = scan_repo(str(tmp_path))
    assert classify_repo(scan["signals"])["type"] == "empty"


# --- 评分 ---

def test_score_clean_app_high(tmp_path):
    _clean_app(tmp_path)
    result = build_score_and_risks(scan_repo(str(tmp_path)))
    assert result["archetype"]["type"] == "software"
    assert result["score"] >= 90, result
    assert result["grade"] == "优"


def test_score_leaky_app_low(tmp_path):
    _leaky_app(tmp_path)
    result = build_score_and_risks(scan_repo(str(tmp_path)))
    assert result["score"] <= 50, result
    assert result["summary"]["secrets_high"] >= 2


def test_score_docs_repo_uses_docs_rubric(tmp_path):
    _docs_repo(tmp_path)
    result = build_score_and_risks(scan_repo(str(tmp_path)))
    assert result["archetype"]["type"] == "docs"
    keys = set(result["components"])
    assert keys == {"readme", "nav", "license", "examples", "secrets"}
    assert result["score"] >= 60, result  # 文档仓库不应因缺依赖/启动脚本被判差


def test_score_empty_repo_null(tmp_path):
    _write(tmp_path, "x.txt", "hi\n")
    result = build_score_and_risks(scan_repo(str(tmp_path)))
    assert result["score"] is None
    assert result["grade"] == "无法评估"


def test_windows_weight_is_5(tmp_path):
    _clean_app(tmp_path)
    result = build_score_and_risks(scan_repo(str(tmp_path)))
    assert result["components"]["windows"]["max"] == 5


def test_doc_findings_do_not_penalize(tmp_path):
    _write(tmp_path, "README.md", "# p\n## Install\nx\n## Usage\nx\n")
    _write(tmp_path, "docs/guide.md", f'KEY = "{_T_OPENAI}"\n')
    _write(tmp_path, "app.py", "print(1)\n")
    _write(tmp_path, "b.py", "print(2)\n")
    _write(tmp_path, "package.json", '{"dependencies":{"a":"1"}}')
    result = build_score_and_risks(scan_repo(str(tmp_path)))
    assert result["summary"]["secrets"] == 0
    assert result["summary"]["doc_findings"] == 1


def test_deterministic(tmp_path):
    _clean_app(tmp_path)
    a = json.dumps(build_score_and_risks(scan_repo(str(tmp_path))), sort_keys=True, ensure_ascii=False)
    b = json.dumps(build_score_and_risks(scan_repo(str(tmp_path))), sort_keys=True, ensure_ascii=False)
    assert a == b


def test_agent_downgrades_medium_but_not_high(tmp_path):
    # medium 明文凭据可被 Agent 判为误报；high 的 AWS key 永不降级
    _write(tmp_path, "config.py",
           'DB_PASSWORD = "Xk9mQ2vLp7Rt4Zw"\n'
           f'AWS = "{_T_AWS}"\n')
    _write(tmp_path, "main.py", "print(1)\n")
    _write(tmp_path, "utils.py", "print(2)\n")
    _write(tmp_path, "README.md", "# p\n")
    scan = scan_repo(str(tmp_path))
    base = build_score_and_risks(scan)
    assert base["summary"]["secrets"] == 2

    verdicts = {}
    for idx, s in enumerate(scan["secrets"]):
        if s["confidence"] == "high":
            verdicts[f"s{idx}"] = {"verdict": "false_positive", "reason": "test"}
        else:
            verdicts[f"s{idx}"] = {"verdict": "false_positive", "reason": "test"}
    adjusted = build_score_and_risks(scan, verdicts)
    assert adjusted["summary"]["secrets"] == 1, "high 发现不应被 Agent 降级"
    assert adjusted["summary"]["agent_downgraded"] == 1
    cats = [r["category"] for r in adjusted["risks"]]
    assert any("AI 判为误报" in c for c in cats)
    assert adjusted["score"] >= base["score"]


def test_agent_confirmed_keeps_penalty(tmp_path):
    _write(tmp_path, "config.py", 'DB_PASSWORD = "Xk9mQ2vLp7Rt4Zw"\n')
    scan = scan_repo(str(tmp_path))
    idx = 0
    verdicts = {f"s{idx}": {"verdict": "confirmed", "reason": "真实密码"}}
    result = build_score_and_risks(scan, verdicts)
    assert result["summary"]["secrets"] == 1
    assert result["summary"]["agent_downgraded"] == 0
    assert result["risks"][0]["agent"]["verdict"] == "confirmed"


def test_extensionless_pem_file_detected(tmp_path):
    # 无扩展名的私钥文件（deploy/keys/id_deploy）也要被扫到
    _write(tmp_path, "deploy/keys/id_deploy",
           f"{_PEM_BEGIN}\nMIIabc\n{_PEM_END}\n")
    scan = scan_repo(str(tmp_path))
    assert any(f["file"] == "deploy/keys/id_deploy" and "私钥" in f["kind"]
               for f in scan["secrets"])


# --- 深度扫描 / 自定义规则 ---

def test_custom_rules_and_baseline_ignore(tmp_path):
    _write(tmp_path, ".repohealth.json", json.dumps({
        "rules": [{"name": "corp", "pattern": "CORP-[A-Z0-9]{8}",
                   "kind": "公司密钥", "confidence": "high"}],
        "ignore": [{"file": "fixtures/keys.py", "match": "CORP-", "reason": "测试夹具"}],
    }))
    _write(tmp_path, "app.py", 'TOKEN = "CORP-AB12CD34"\n')
    _write(tmp_path, "fixtures/keys.py", 'TOKEN = "CORP-ZZ99YY88"\n')
    scan = scan_repo(str(tmp_path))
    files = {s["file"] for s in scan["secrets"]}
    assert "app.py" in files
    assert "fixtures/keys.py" not in files
    assert scan["ignored"] == 1


def test_archive_content_scanned(tmp_path):
    import zipfile
    token = _T_GH
    with zipfile.ZipFile(tmp_path / "bundle.zip", "w") as z:
        z.writestr("inner/keys.py", f'GH = "{token}"\n')
    scan = scan_repo(str(tmp_path))
    hits = [s for s in scan["secrets"] if "压缩包" in s["kind"]]
    assert hits and hits[0]["file"].startswith("bundle.zip!")


def test_base64_encoded_secret_detected(tmp_path):
    import base64
    token = _T_OPENAI
    payload = base64.b64encode(f"OPENAI_API_KEY={token}".encode()).decode()
    _write(tmp_path, "app.py", f'BLOB = "{payload}"\n')
    scan = scan_repo(str(tmp_path))
    assert any("编码后隐藏" in s["kind"] for s in scan["secrets"])


def test_history_scan_finds_deleted_secret(tmp_path):
    import subprocess
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=a@b.c",
                        "-c", "user.name=t", *args], check=True, capture_output=True)
    git("init", "-q")
    _write(tmp_path, "config.py", f'AWS = "{_T_AWS}"\n')
    git("add", "-A")
    git("commit", "-qm", "add key")
    os.remove(tmp_path / "config.py")
    git("add", "-A")
    git("commit", "-qm", "remove key")
    scan = scan_repo(str(tmp_path), {"history": 10})
    assert scan["history"], "应发现历史提交中的密钥"
    assert any("历史提交" in h["kind"] for h in scan["history"])


def test_gitleaks_merge_dedupes():
    from app.engines import merge_gitleaks
    ours = [{"file": "a.py", "line": 3, "match": "x", "kind": "k",
             "confidence": "high", "reason": "r"}]
    gl = [{"file": "a.py", "line": 3, "match": "x", "kind": "gitleaks: x",
           "confidence": "high", "reason": "g"},
          {"file": "b.py", "line": 7, "match": "y", "kind": "gitleaks: y",
           "confidence": "high", "reason": "g"}]
    merged = merge_gitleaks(ours, gl)
    assert len(merged) == 2
    assert merged[0]["kind"] == "k"
